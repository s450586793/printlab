import test from 'node:test';
import assert from 'node:assert/strict';
import {validateReferenceFiles,uniqueReferenceFiles,safeAttachmentUrl,uploadReferenceImages,MAX_REFERENCE_IMAGE_BYTES} from '../studio/src/image-attachments.js';
import {agentUploadImage} from '../studio/src/agent-client.js';

const file = (overrides = {}) => ({name:'reference.png',type:'image/png',size:100,lastModified:1,...overrides});
const meta = (id = 'a'.repeat(32), pid = 'P001') => ({id,name:'Reference',url:`/api/projects/${pid}/images/${id}`});
const response = (data, status = 200) => ({ok:status>=200&&status<300,status,json:async()=>data});

test('reference images enforce supported formats and each file size before requests', () => {
  assert.equal(validateReferenceFiles([file()]).length,1);
  assert.equal(validateReferenceFiles([file({size:MAX_REFERENCE_IMAGE_BYTES})]).length,1);
  for (const invalid of [file({type:'image/svg+xml'}),file({type:'text/plain'}),file({size:0}),file({size:NaN}),file({size:MAX_REFERENCE_IMAGE_BYTES+1})]) assert.throws(()=>validateReferenceFiles([invalid]));
  assert.deepEqual(validateReferenceFiles(null),[]);
});

test('reference images accept seven files at once and another file after six are already selected', () => {
  const files=Array.from({length:7},(_,index)=>file({name:`reference-${index}.png`}));
  assert.equal(validateReferenceFiles(files).length,7);
  const existing=files.slice(0,6).map(source=>({file:source}));
  const incoming=validateReferenceFiles(uniqueReferenceFiles(existing,[files[6]]));
  assert.equal(incoming.length,1);
  assert.equal(existing.length+incoming.length,7);
});

test('repeated paste or drop does not duplicate an already selected file', () => {
  const selected=file();
  assert.deepEqual(uniqueReferenceFiles([{file:selected}],[selected,file({name:'another.png'}),selected]),[file({name:'another.png'})]);
  assert.equal(uniqueReferenceFiles([],[selected,selected]).length,1);
  assert.deepEqual(uniqueReferenceFiles([],null),[]);
});

test('history thumbnails accept only a server image belonging to the current project', () => {
  assert.equal(safeAttachmentUrl(meta().url,'P001'),meta().url);
  for(const value of [meta().url,'https://external.test/image.png','//external.test/image.png','javascript:alert(1)','/api/projects/P001/images/../../secrets',null]) assert.equal(safeAttachmentUrl(value,'P002'),null);
});

test('successful image uploads are reused after a later image fails without duplicating the first file', async () => {
  const first={localId:'one',file:file()},second={localId:'two',file:file({name:'second.png'})};
  let uploads=0;
  const onUploaded=(id,image)=>{[first,second].find(item=>item.localId===id).uploaded=image};
  await assert.rejects(uploadReferenceImages('P001',[first,second],{onUploaded,upload:async()=>{if(++uploads===2)throw Error('Temporary failure');return meta()}}),/Temporary/);
  assert.equal(first.uploaded.id,'a'.repeat(32));
  const images=await uploadReferenceImages('P001',[first,second],{onUploaded,upload:async()=>{uploads++;return meta('b'.repeat(32))}});
  assert.equal(uploads,3);
  assert.deepEqual(images.map(image=>image.id),['a'.repeat(32),'b'.repeat(32)]);
});

test('more than six selected images upload in their original order', async () => {
  const items=Array.from({length:7},(_,index)=>({localId:String(index),file:file({name:`reference-${index}.png`})}));
  const order=[];
  const uploaded=await uploadReferenceImages('P001',items,{upload:async(pid,source)=>{order.push(source.name);return meta(String(order.length).padStart(32,'0'),pid)}});
  assert.deepEqual(order,items.map(item=>item.file.name));
  assert.equal(uploaded.length,7);
});

test('uploads reject an incomplete or cross-project response and stop on cancellation', async () => {
  for(const image of [{id:'bad',url:meta().url},meta('a'.repeat(32),'P002')]) await assert.rejects(uploadReferenceImages('P001',[{file:file()}],{upload:async()=>image}),/结果不完整/);
  const controller=new AbortController();controller.abort();let calls=0;
  await assert.rejects(uploadReferenceImages('P001',[{file:file()}],{signal:controller.signal,upload:async()=>{calls++;return meta()}}),error=>error.name==='AbortError');
  assert.equal(calls,0);
});

test('image upload sends original bytes to the encoded project with its browser session and CSRF', async () => {
  const calls=[],source=file({name:'参考 #?.png'}),signal=new AbortController().signal;
  const fetcher=async(url,options)=>{calls.push({url,options});return response(calls.length===1?{csrf_token:'csrf'}:meta())};
  await agentUploadImage('project/two',source,{fetcher,signal});
  assert.equal(calls[0].url,'/api/prepared/session');
  assert.equal(calls[1].url,'/api/projects/project%2Ftwo/images?filename=%E5%8F%82%E8%80%83%20%23%3F.png');
  assert.equal(calls[1].options.body,source);
  assert.equal(calls[1].options.signal,signal);
  assert.equal(calls[1].options.headers['X-PrintLab-CSRF'],'csrf');
  assert.equal(calls[1].options.headers['Content-Type'],'image/png');
  assert.equal(calls[1].options.credentials,'same-origin');
});

test('image upload distinguishes failed sessions, server rejections and a cancelled or interrupted transfer', async () => {
  let calls=0;
  await assert.rejects(agentUploadImage('P001',file(),{fetcher:async()=>{calls++;return response({})}}),/会话/);
  assert.equal(calls,1);
  calls=0;
  await assert.rejects(agentUploadImage('P001',file(),{fetcher:async()=>response(++calls===1?{csrf_token:'csrf'}:{detail:'Invalid image'},calls===1?200:400)}),/Invalid image/);
  for(const error of [new Error('network'),new DOMException('cancelled','AbortError')]){
    calls=0;
    await assert.rejects(agentUploadImage('P001',file(),{fetcher:async()=>{if(++calls===1)return response({csrf_token:'csrf'});throw error}}),problem=>error.name==='AbortError'?problem===error:/图片上传/.test(problem.message));
  }
});

test('upload failures identify the failed file and preserve server status for 400, 413 and network errors', async () => {
  for (const status of [400,413,'network']) {
    let calls=0;
    const source=file({name:'手机照片.jpeg'});
    const upload=(pid,uploadFile,options)=>agentUploadImage(pid,uploadFile,{...options,fetcher:async()=>{
      if(++calls===1)return response({csrf_token:'csrf'});
      if(status==='network')throw Error('disconnected');
      return response({detail:status===400?'无法读取图片。':'图片超过大小限制。'},status);
    }});
    await assert.rejects(uploadReferenceImages('P001',[{localId:'photo',file:source}],{upload}),error=>{
      assert.match(error.message,/手机照片\.jpeg.*上传失败/);
      assert.equal(error.fileName,'手机照片.jpeg');
      assert.equal(error.status,status==='network'?undefined:status);
      assert.match(error.message,status===400?/无法读取/:status===413?/大小限制/:/图片上传未完成/);
      return true;
    });
  }
});

test('progress locates the current and failed image while a retry reuses successful uploads', async () => {
  const items=[{localId:'first',file:file({name:'first.png'})},{localId:'second',file:file({name:'second.jpeg'})}];
  const progress=[],calls=[];
  const onUploaded=(id,image)=>{items.find(item=>item.localId===id).uploaded=image};
  const onProgress=value=>progress.push(value);
  await assert.rejects(uploadReferenceImages('P001',items,{onUploaded,onProgress,upload:async(pid,source)=>{
    calls.push(source.name);if(source.name==='second.jpeg')throw Error('无法读取图片。');return meta();
  }}),/second\.jpeg/);
  assert.deepEqual(progress.map(({localId,index,total,status})=>({localId,index,total,status})),[
    {localId:'first',index:1,total:2,status:'uploading'},
    {localId:'first',index:1,total:2,status:'uploaded'},
    {localId:'second',index:2,total:2,status:'uploading'},
    {localId:'second',index:2,total:2,status:'failed'},
  ]);
  await uploadReferenceImages('P001',items,{onUploaded,onProgress,upload:async(pid,source)=>{calls.push(source.name);return meta('b'.repeat(32))}});
  assert.deepEqual(calls,['first.png','second.jpeg','second.jpeg']);
  assert.equal(progress.at(-1).status,'uploaded');
  assert.equal(progress.at(-1).index,2);
});

test('cancelling an in-flight image keeps earlier uploads and never continues to later images', async () => {
  const controller=new AbortController(),items=['first.png','second.png','third.png'].map((name,index)=>({localId:String(index),file:file({name})}));
  const calls=[];let uploading;
  const started=new Promise(resolve=>{uploading=resolve});
  const pending=uploadReferenceImages('P001',items,{signal:controller.signal,onUploaded:(id,image)=>{items.find(item=>item.localId===id).uploaded=image},upload:async(pid,source,{signal})=>{
    calls.push(source.name);
    if(source.name==='first.png')return meta();
    uploading();
    return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')),{once:true}));
  }});
  await started;controller.abort();
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.deepEqual(calls,['first.png','second.png']);
  assert.equal(items[0].uploaded.id,'a'.repeat(32));
  assert.equal(items[1].uploaded,undefined);
});
