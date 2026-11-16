import {estimateDuration} from './presentation.js';

const states={RUNNING:'打印中',PRINTING:'打印中',PREPARE:'准备中',PREPARING:'准备中',PAUSE:'已暂停',PAUSED:'已暂停',FINISH:'上次打印完成',COMPLETED:'上次打印完成',IDLE:'空闲',FAILED:'打印失败',ERROR:'设备异常',CANCELLED:'已取消',OFFLINE:'设备离线'};
const number=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
const nonnegative=value=>number(value)!==null&&value>=0?value:null;
const display=value=>value===null?'—':String(Number(value.toFixed(1)));

export function printerTelemetry(device) {
 const printer=device?.printer||{},state=String(printer.state||'').toUpperCase();
 const progress=nonnegative(printer.progress),validProgress=progress!==null&&progress<=100?progress:null;
 const nozzle=number(printer.nozzle_temp??printer.temperatures?.nozzle??printer.temperatures?.nozzle_temp);
 const bed=number(printer.bed_temp??printer.temperatures?.bed??printer.temperatures?.bed_temp);
 const remaining=nonnegative(printer.remaining_minutes??printer.remaining_time);
 const layer=nonnegative(printer.layer_num),total=nonnegative(printer.total_layers);
 return {
  status:device?.state==='offline'?'设备离线':device?.state!=='online'?'等待设备数据':states[state]||'状态未知',
  active:['RUNNING','PRINTING','PREPARE','PREPARING','PAUSE','PAUSED'].includes(state),
  attention:device?.state==='offline'||printer.hms_errors===true||['FAILED','ERROR'].includes(state),
  progress:validProgress,
  progressLabel:validProgress===null?'—':`${display(validProgress)}%`,
  metrics:[['喷嘴温度',nozzle===null?'—':`${display(nozzle)} °C`],['热床温度',bed===null?'—':`${display(bed)} °C`],['剩余时间',remaining===null?'—':estimateDuration(remaining*60)],['打印层数',layer===null&&total===null?'—':`${display(layer)} / ${display(total)}`]],
 };
}
