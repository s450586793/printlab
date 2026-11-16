import test from 'node:test';
import assert from 'node:assert/strict';
import {agentPath,isActiveRun,mergeAgentEvents,mergeAgentRun,assistantPreview,createAgentRequestId,agentRead,agentWrite} from '../studio/src/agent-client.js';

const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});

test('agent routes keep projects and runs in separate encoded paths', () => {
  assert.equal(agentPath('P001'), '/api/projects/P001/agent');
  assert.equal(agentPath('project/two', 'run?#'), '/api/projects/project%2Ftwo/agent/runs/run%3F%23');
  assert.notEqual(agentPath('P001', 'R1'), agentPath('P002', 'R1'));
});

test('only terminal runs allow a new independent submission', () => {
  assert.equal(isActiveRun(null), false);
  for (const status of ['queued','starting','running','awaiting_input','awaiting_approval','new_unknown_status']) assert.equal(isActiveRun({id: 'R1', status}), true);
  for (const status of ['succeeded','failed','interrupted','cancelled']) assert.equal(isActiveRun({id: 'R1', status}), false);
});

test('replayed or out of order events do not duplicate streamed text', () => {
  const events = mergeAgentEvents([{seq: 2, type: 'assistant_delta', text: 'lo'}], [{seq: 1, type: 'assistant_delta', text: 'Hel'}, {seq: 2, type: 'assistant_delta', text: 'lo'}, {seq: '3', text: 'bad'}, null]);
  assert.deepEqual(events.map(event => event.seq), [1, 2]);
  assert.equal(assistantPreview(events), 'Hello');
  const completed = mergeAgentEvents(events, [{seq: 3, type: 'assistant_message', text: 'Hello'}]);
  assert.equal(assistantPreview(completed), 'Hello');
  assert.equal(assistantPreview(completed, 'Final answer'), 'Final answer');
});

test('assistant items preserve their own full messages instead of appending them twice', () => {
  assert.equal(assistantPreview([{seq: 1, item_id: 'one', type: 'assistant_delta', text: 'Draft'}, {seq: 2, item_id: 'one', type: 'assistant_message', text: 'Done'}, {seq: 3, item_id: 'two', type: 'assistant_message', text: 'Next'}, {seq: 4, type: 'tool', text: 'Not assistant text'}]), 'Done\n\nNext');
  assert.equal(assistantPreview([{type: 'assistant_delta', text: null}], ''), '');
});

test('old polling results cannot replace a newer cancellation or another project run', () => {
  const current = {id: 'R1', project_id: 'P1', status: 'cancelled', last_seq: 5};
  assert.equal(mergeAgentRun(current, {id: 'R1', project_id: 'P1', status: 'running', last_seq: 4}), current);
  assert.equal(mergeAgentRun(current, {id: 'R2', project_id: 'P2', last_seq: 10}), current);
  assert.equal(mergeAgentRun(current, {id: 'R1', project_id: 'P2', last_seq: 10}), current);
  assert.equal(mergeAgentRun(current, null), current);
  assert.deepEqual(mergeAgentRun(null, current), current);
  assert.equal(mergeAgentRun(current, {...current, last_seq: 6, result: 'Stopped'}).result, 'Stopped');
});

test('submission identifiers support browsers without randomUUID but never fall back to weak random values', () => {
  assert.equal(createAgentRequestId({randomUUID: () => 'uuid'}), 'uuid');
  assert.equal(createAgentRequestId({getRandomValues: bytes => bytes}), '00000000-0000-4000-8000-000000000000');
  assert.throws(() => createAgentRequestId({}), /浏览器/);
});

test('writes first obtain same-origin session and carry CSRF on the project operation', async () => {
  const calls = [], signal = new AbortController().signal;
  const fetcher = async (url, options) => { calls.push({url, options}); return response(calls.length === 1 ? {csrf_token: 'test-csrf'} : {id: 'R1'}); };
  assert.equal((await agentWrite(agentPath('P2') + '/runs', {message: 'Build this', request_id: 'request'}, {fetcher, signal})).id, 'R1');
  assert.equal(calls[0].url, '/api/prepared/session');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.headers['X-PrintLab-CSRF'], 'test-csrf');
  assert.equal(calls[1].options.signal, signal);
  assert.deepEqual(JSON.parse(calls[1].options.body), {message: 'Build this', request_id: 'request'});
});

test('missing CSRF, failed session and invalid responses never start an agent operation', async () => {
  let count = 0;
  await assert.rejects(agentWrite('/run', {}, {fetcher: async () => { count++; return response({}); }}), /会话/);
  assert.equal(count, 1);
  await assert.rejects(agentWrite('/run', {}, {fetcher: async () => response({detail: 'Session expired'}, 403)}), /Session expired/);
  await assert.rejects(agentRead('/read', {fetcher: async () => ({ok: false, status: 502, json: async () => { throw Error('HTML'); }})}), /502/);
});

test('project deletion uses the same session and CSRF protection and preserves conflict reasons', async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({url, options});
    return response(calls.length % 2 ? {csrf_token: 'delete-csrf'} : {detail: 'Codex 仍在处理此项目'}, calls.length % 2 ? 200 : 409);
  };
  await assert.rejects(agentWrite('/api/projects/P003', {project_id: 'P003'}, {method: 'DELETE', fetcher}), error => error.status === 409 && /Codex/.test(error.message));
  assert.equal(calls[1].options.method, 'DELETE');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.headers['X-PrintLab-CSRF'], 'delete-csrf');
  assert.deepEqual(JSON.parse(calls[1].options.body), {project_id: 'P003'});
  await assert.rejects(agentWrite('/api/projects/P003', {}, {method: 'GET', fetcher}), /不支持/);
  assert.equal(calls.length, 2);
});

test('read and write network errors stay distinct from intentional cancellation', async () => {
  await assert.rejects(agentRead('/read', {fetcher: async () => { throw Error('network'); }}), /无法连接/);
  let count = 0;
  await assert.rejects(agentWrite('/write', {}, {fetcher: async () => { if (++count === 1) return response({csrf_token: 'test'}); throw Error('network'); }}), /结果待核对/);
  const abort = new DOMException('aborted', 'AbortError');
  await assert.rejects(agentRead('/read', {fetcher: async () => { throw abort; }}), error => error === abort);
  count = 0;
  await assert.rejects(agentWrite('/write', {}, {fetcher: async () => { if (++count === 1) return response({csrf_token: 'test'}); throw abort; }}), error => error === abort);
});
