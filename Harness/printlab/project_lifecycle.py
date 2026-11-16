"""从工作台移除项目，保留文件、对话和稳定编号。"""
import re

from . import agent_bridge, core

INACTIVE_PRINT_STATES = {'awaiting_configuration', 'awaiting_approval', 'approved',
                         'failed', 'cancelled', 'completed', 'awaiting_physical_review',
                         'slice_failed', 'superseded', 'dispatch_rejected'}
FINISHED_PRINT_STATES = {'failed', 'cancelled', 'completed', 'awaiting_physical_review',
                         'slice_failed', 'superseded', 'dispatch_rejected'}
ACTIVE_PRINT_STATES = {'slicing', 'dispatching', 'submitted', 'printing', 'running',
                       'preparing', 'paused', 'dispatch_unknown', 'staged_requires_reconciliation'}


class DeleteError(core.WorkflowError):
    pass


def _check_prints(path, pid):
    if core.read_json(path / 'project.json', {}).get('status') in ACTIVE_PRINT_STATES:
        raise DeleteError('此项目有切片或打印任务正在处理，或打印结果待核对，请稍后再删除。')
    for file in (core.ROOT / 'Runtime/workbench/operations').glob('*.json'):
        operation = core.read_json(file, {})
        if operation.get('project_id') == pid and operation.get('state') not in {'blocked', 'submitted', 'observed'}:
            raise DeleteError('此项目有打印操作正在处理或结果待核对，请先核对后再删除。')
        if operation.get('project_id') == pid and operation.get('state') in {'submitted', 'observed'}:
            jid = operation.get('job_id', '')
            if not isinstance(jid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', jid):
                raise DeleteError('此项目的打印任务记录待核对，请先核对后再删除。')
            job = core.read_json(path / 'Print' / jid / 'job.json', {})
            if job.get('state') not in FINISHED_PRINT_STATES:
                raise DeleteError('此项目的打印任务尚未结束，请先核对后再删除。')
    for file in (path / 'Print').glob('*/job.json'):
        job = core.read_json(file, {})
        if job.get('state') not in INACTIVE_PRINT_STATES:
            raise DeleteError('此项目有切片或打印任务正在处理，或打印结果待核对，请稍后再删除。')
        receipt = core.read_json(file.parent / 'dispatch.json', {})
        if receipt and job.get('state') not in FINISHED_PRINT_STATES:
            raise DeleteError('此项目已有打印发送记录，请先核对结果后再删除。')
        resolution = core.read_json(file.parent / 'dispatch-resolution.json', {})
        settled = (job.get('state') == 'dispatch_rejected' and job.get('reconciliation', {}).get('state') == 'settled'
                   and resolution.get('kind') == 'signature_failure_settled_after_plate_clear'
                   and resolution.get('original_dispatch') == receipt and bool(resolution.get('next_job_id')))
        if job.get('state') == 'dispatch_rejected' and not settled:
            raise DeleteError('此项目的打印发送结果待核对，请先核对后再删除。')
        if not settled and receipt.get('state') in {'unknown', 'dispatch_unknown', 'staged_requires_reconciliation'}:
            observation = core.read_json(file.parent / 'observation.json', {})
            native = core.read_json(file.parent / 'native-observation.json', {})
            queue = observation.get('queue', {})
            queue_finished = (receipt.get('queue_id') is not None and queue.get('id') == receipt['queue_id']
                              and queue.get('status') in {'failed', 'cancelled', 'completed'})
            native_finished = (native.get('matching_task') is True and native.get('fresh') is True
                               and native.get('device', {}).get('state') in {'FAILED', 'FINISH', 'CANCELLED'})
            if not queue_finished and not native_finished:
                raise DeleteError('此项目的打印发送结果待核对，请先核对后再删除。')


def delete_project(pid):
    if not isinstance(pid, str) or not re.fullmatch(r'P\d{3,}', pid):
        raise DeleteError('项目编号无效。')
    # 与新任务、打印启动和项目写入使用相同的锁，并遵循既有锁顺序。
    with core.lock('agent-schedule-' + pid):
        path = core.project_path(pid, include_deleted=True)
        project = core.read_json(path / 'project.json')
        if not project.get('deleted_at'):
            if any(run.get('status') not in agent_bridge.TERMINAL for run in agent_bridge._runs(pid)):
                raise DeleteError('Codex 仍在处理此项目，请等待任务结束后再删除。')
            with core.lock('workbench-start'), core.lock('printer'), core.lock(pid):
                path = core.project_path(pid)
                project = core.read_json(path / 'project.json')
                _check_prints(path, pid)
                project.update(deleted_at=core.now(), updated_at=core.now())
                core.write_json(path / 'project.json', project)
        return {'id': pid, 'deleted_at': project['deleted_at']}
