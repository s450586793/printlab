import test from 'node:test';
import assert from 'node:assert/strict';
import {agentUploadFile} from '../studio/src/agent-client.js';
import {attachmentKind,attachmentSize,attachmentProjectName,defaultAttachmentMessage,validateProjectAttachments,safeProjectFileUrl,uploadProjectAttachments,MAX_PROJECT_FILE_BYTES} from '../studio/src/file-attachments.js';
import {MAX_REFERENCE_IMAGE_BYTES} from '../studio/src/image-attachments.js';

const file=(overrides={})=>({name:'part.step',type:'application/octet-stream',size:100,lastModified:1,...overrides});
const metadata=(id='a'.repeat(32),projectId='P001',collection='files')=>({id,name:'part.step',size:100,url:`/api/projects/${encodeURIComponent(projectId)}/${collection}/${id}`});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});

test('generic attachments accept model, document, source and archive files without a count cap while keeping image limits',()=>{
 const sources=['part.stl','part.step','design.3mf','plan.pdf','notes.docx','source.py','bundle.zip','extensionless'].map(name=>file({name}));
 assert.deepEqual(validateProjectAttachments([],sources),sources);
 assert.equal(validateProjectAttachments([],[file({size:MAX_PROJECT_FILE_BYTES})]).length,1);
 for(const size of [0,NaN,MAX_PROJECT_FILE_BYTES+1])assert.throws(()=>validateProjectAttachments([],[file({size})]));
 const image=file({name:'reference.jpg',type:'image/jpeg'});
 assert.equal(attachmentKind(image),'image');
 assert.equal(attachmentKind(file()),'file');
 assert.throws(()=>validateProjectAttachments([],[{...image,size:MAX_REFERENCE_IMAGE_BYTES+1}]),/12 MB/);
 assert.throws(()=>validateProjectAttachments([],[file()],{imagesOnly:true}),/PNG/);
 assert.deepEqual(validateProjectAttachments([{file:sources[0]}],[sources[0],sources[1],sources[1]]),[sources[1]]);
 assert.deepEqual(validateProjectAttachments([],null),[]);
});

test('file URLs stay in the current project and file-only inputs produce a meaningful project and message',()=>{
 assert.equal(safeProjectFileUrl(metadata().url,'P001'),metadata().url);
 for(const value of [metadata().url.replace('/files/','/images/'),metadata().url+'?download=1','https://other.test/file','/api/projects/P001/files/../../secret',null])assert.equal(safeProjectFileUrl(value,'P001'),null);
 assert.equal(safeProjectFileUrl(metadata().url,'P002'),null);
 assert.equal(safeProjectFileUrl(metadata('a'.repeat(32),'part/two').url,'part/two'),metadata('a'.repeat(32),'part/two').url);
 assert.equal(attachmentProjectName([{file:file({name:'支架.v2.step'})}]),'支架.v2');
 assert.equal(attachmentProjectName([]),'附件项目');
 assert.equal(attachmentProjectName([{file:file({name:'a'.repeat(150)+'.stl'})}]).length,120);
 assert.match(defaultAttachmentMessage([{file:file()}]),/附件/);
 assert.match(defaultAttachmentMessage([{file:file({type:'image/png'})}]),/参考图片/);
 assert.equal(defaultAttachmentMessage([]),'');
 assert.deepEqual([attachmentSize(100),attachmentSize(1024),attachmentSize(1024*1024),attachmentSize(NaN)],['100 B','1 KB','1.0 MB','大小未知']);
});

test('mixed uploads use distinct endpoints and retain selected order and per-file progress',async()=>{
 const items=[{localId:'step',file:file()},{localId:'image',file:file({name:'ref.png',type:'image/png'})},{localId:'pdf',file:file({name:'brief.pdf',type:'application/pdf'})}];
 const calls=[],progress=[];
 const result=await uploadProjectAttachments('P001',items,{onProgress:value=>progress.push(value),uploadFile:async(pid,source)=>{calls.push(`file:${source.name}`);return metadata(calls.length.toString().padStart(32,'0'),pid)},uploadImage:async(pid,source)=>{calls.push(`image:${source.name}`);return metadata(calls.length.toString().padStart(32,'0'),pid,'images')}});
 assert.deepEqual(calls,['file:part.step','image:ref.png','file:brief.pdf']);
 assert.equal(result.images.length,1);assert.equal(result.files.length,2);
 assert.deepEqual(progress.filter(item=>item.status==='uploading').map(({localId,index,total})=>({localId,index,total})),[{localId:'step',index:1,total:3},{localId:'image',index:2,total:3},{localId:'pdf',index:3,total:3}]);
});

test('later upload errors preserve successful IDs for retry and identify the failed file',async()=>{
 const items=['first.stl','second.pdf','third.zip'].map((name,index)=>({localId:String(index),file:file({name})}));
 const calls=[],onUploaded=(id,uploaded)=>{items.find(item=>item.localId===id).uploaded=uploaded};
 await assert.rejects(uploadProjectAttachments('P001',items,{onUploaded,uploadFile:async(pid,source)=>{calls.push(source.name);if(source.name==='second.pdf'){const error=Error('Too large');error.status=413;throw error}return metadata()}}),error=>error.fileName==='second.pdf'&&error.status===413&&/second.pdf/.test(error.message));
 await uploadProjectAttachments('P001',items,{onUploaded,uploadFile:async(pid,source)=>{calls.push(source.name);return metadata(calls.length.toString().padStart(32,'0'))}});
 assert.deepEqual(calls,['first.stl','second.pdf','second.pdf','third.zip']);
 assert.equal(items[0].uploaded.id,'a'.repeat(32));
 for(const bad of [{id:'bad',url:metadata().url},metadata('a'.repeat(32),'P002'),metadata('a'.repeat(32),'P001','images')])await assert.rejects(uploadProjectAttachments('P001',[{file:file()}],{uploadFile:async()=>bad}),/结果不完整/);
});

test('cancelling an in-flight generic attachment keeps completed uploads and never sends later files',async()=>{
 const items=['one.step','two.pdf','three.zip'].map((name,index)=>({localId:String(index),file:file({name})})),controller=new AbortController(),calls=[];
 let started;const uploading=new Promise(resolve=>{started=resolve});
 const pending=uploadProjectAttachments('P001',items,{signal:controller.signal,onUploaded:(id,uploaded)=>{items.find(item=>item.localId===id).uploaded=uploaded},uploadFile:async(pid,source,{signal})=>{calls.push(source.name);if(source.name==='one.step')return metadata();started();return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')),{once:true}))}});
 await uploading;controller.abort();await assert.rejects(pending,error=>error.name==='AbortError');
 assert.deepEqual(calls,['one.step','two.pdf']);assert.ok(items[0].uploaded);assert.equal(items[1].uploaded,undefined);
 let count=0;await assert.rejects(uploadProjectAttachments('P001',items,{signal:controller.signal,uploadFile:async()=>{count++;return metadata()}}),error=>error.name==='AbortError');assert.equal(count,0);
});

test('raw file requests preserve original bytes, filename, session, CSRF and cancellation',async()=>{
 const calls=[],source=file({name:'模型 #?.step'}),signal=new AbortController().signal;
 await agentUploadFile('part/two',source,{signal,fetcher:async(url,options)=>{calls.push({url,options});return response(calls.length===1?{csrf_token:'csrf'}:metadata())}});
 assert.equal(calls[1].url,'/api/projects/part%2Ftwo/files?filename=%E6%A8%A1%E5%9E%8B%20%23%3F.step');
 assert.equal(calls[1].options.body,source);assert.equal(calls[1].options.signal,signal);assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.headers['X-PrintLab-CSRF'],'csrf');
 for(const error of [Error('network'),new DOMException('cancelled','AbortError')]){
  let count=0;await assert.rejects(agentUploadFile('P001',source,{fetcher:async()=>{if(++count===1)return response({csrf_token:'csrf'});throw error}}),problem=>error.name==='AbortError'?problem===error:/附件上传/.test(problem.message));
 }
 let count=0;await assert.rejects(agentUploadFile('P001',source,{fetcher:async()=>{count++;return response({})}}),/会话/);assert.equal(count,1);
 count=0;await assert.rejects(agentUploadFile('P001',source,{fetcher:async()=>response(++count===1?{csrf_token:'csrf'}:{detail:'附件过大'},count===1?200:413)}),error=>error.status===413&&/附件过大/.test(error.message));
});
