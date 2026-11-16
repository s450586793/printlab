import test from 'node:test';
import assert from 'node:assert/strict';
import {homeAssistantPayload,homeAssistantStatus,clearPrinterSelection} from '../studio/src/homeassistant-settings.js';

test('HA connection payload omits blank credentials and trims intentional input',()=>{
 assert.deepEqual(homeAssistantPayload(' http://ha.local:8123/ ','  '),{base_url:'http://ha.local:8123/'});
 assert.deepEqual(homeAssistantPayload('https://ha.example','  test-token  '),{base_url:'https://ha.example',token:'test-token'});
});

test('a connected HA with no discovered printers is still connected',()=>{
 assert.deepEqual(homeAssistantStatus({configured:true,connected:true,devices:[]}),{label:'已连接 · 0 台打印机',connected:true});
 assert.equal(homeAssistantStatus({configured:true,connected:true,devices:[{id:'one'},{id:'two'}]}).label,'已连接 · 2 台打印机');
 assert.equal(homeAssistantStatus({configured:true,connected:false}).label,'连接待检查');
 assert.equal(homeAssistantStatus({configured:false}).label,'未连接');
 assert.equal(homeAssistantStatus(null).connected,false);
});

test('clearing a prior printer does not affect any other browser preference',()=>{
 const removed=[];clearPrinterSelection({removeItem:key=>removed.push(key)});
 assert.deepEqual(removed,['printlab-selected-printer']);
 assert.doesNotThrow(()=>clearPrinterSelection({removeItem(){throw Error('storage unavailable')}}));
});
