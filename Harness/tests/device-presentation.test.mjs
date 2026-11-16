import test from 'node:test';
import assert from 'node:assert/strict';
import {printerTelemetry} from '../studio/src/device-presentation.js';

test('completed reports stay completed at 100 percent and preserve each device readings',()=>{
 const a=printerTelemetry({state:'online',printer:{state:'FINISH',progress:100,temperatures:{nozzle:17,bed:18},remaining_time:0,layer_num:78,total_layers:78}});
 const b=printerTelemetry({state:'online',printer:{state:'RUNNING',progress:45.5,temperatures:{nozzle:230,bed:70},remaining_time:61,layer_num:90,total_layers:198}});
 assert.equal(a.status,'上次打印完成');assert.equal(a.active,false);assert.equal(a.progressLabel,'100%');
 assert.deepEqual(a.metrics,[['喷嘴温度','17 °C'],['热床温度','18 °C'],['剩余时间','0 分钟'],['打印层数','78 / 78']]);
 assert.equal(b.active,true);assert.equal(b.progressLabel,'45.5%');assert.equal(b.metrics[0][1],'230 °C');assert.equal(b.metrics[2][1],'1 小时 1 分钟');
});

test('missing, invalid and partial telemetry is displayed as unknown while valid zero survives',()=>{
 assert.equal(printerTelemetry(null).status,'等待设备数据');
 assert.ok(printerTelemetry({}).metrics.every(([,value])=>value==='—'));
 const invalid=printerTelemetry({state:'online',printer:{state:'unknown',progress:101,nozzle_temp:NaN,bed_temp:'20',remaining_minutes:-2,layer_num:-1,total_layers:Infinity}});
 assert.equal(invalid.status,'状态未知');assert.equal(invalid.progress,null);assert.equal(invalid.progressLabel,'—');assert.ok(invalid.metrics.every(([,value])=>value==='—'));
 const zero=printerTelemetry({printer:{progress:0,nozzle_temp:0,bed_temp:0,remaining_minutes:0,layer_num:0,total_layers:100}});
 assert.equal(zero.progress,0);assert.equal(zero.metrics[0][1],'0 °C');assert.equal(zero.metrics[3][1],'0 / 100');
 assert.equal(printerTelemetry({printer:{total_layers:10}}).metrics[3][1],'— / 10');
});

test('offline, paused and fault states do not get inferred from progress',()=>{
 assert.equal(printerTelemetry({state:'offline',printer:{state:'RUNNING'}}).status,'设备离线');
 assert.equal(printerTelemetry({state:'online',printer:{state:'PAUSE'}}).status,'已暂停');
 assert.equal(printerTelemetry({state:'online',printer:{state:'IDLE',progress:100}}).status,'空闲');
 assert.equal(printerTelemetry({state:'online',printer:{state:'FAILED'}}).attention,true);
 assert.equal(printerTelemetry({state:'online',printer:{hms_errors:true}}).attention,true);
});
