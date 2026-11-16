import React,{useEffect,useRef,useState} from 'react';
import {ImagePlus,Paperclip,File,FileText,FileArchive,FileCode,Box,Download,X} from 'lucide-react';
import {createAgentRequestId} from './agent-client';
import {safeAttachmentUrl} from './image-attachments';
import {attachmentKind,attachmentSize,safeProjectFileUrl,validateProjectAttachments,uploadProjectAttachments} from './file-attachments';
import './image-attachments.css';

export function useProjectAttachments(){
 const [items,setItems]=useState([]),[error,setError]=useState(''),[progress,setProgress]=useState(null),current=useRef([]),mounted=useRef(true);
 function replace(next){current.current=next;if(mounted.current)setItems(next)}
 function add(files,options){
  try{const incoming=validateProjectAttachments(current.current,files,options);const added=incoming.map(file=>({localId:createAgentRequestId(),file,preview:URL.createObjectURL(file),uploaded:null}));replace([...current.current,...added]);setError('');setProgress(null)}
  catch(problem){setError(problem.message)}
 }
 function remove(id){const found=current.current.find(item=>item.localId===id);if(found)URL.revokeObjectURL(found.preview);replace(current.current.filter(item=>item.localId!==id));setError('');setProgress(null)}
 function clear(){for(const item of current.current)URL.revokeObjectURL(item.preview);replace([]);setError('');setProgress(null)}
 function addImages(files){add(files,{imagesOnly:true})}
 function onPaste(event){const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();event.stopPropagation();addImages(files)}}
 function onDrop(event){if(!event.dataTransfer?.files?.length)return;event.preventDefault();event.stopPropagation();add(event.dataTransfer.files)}
 function onDragOver(event){if(Array.from(event.dataTransfer?.types||[]).includes('Files'))event.preventDefault()}
 async function upload(projectId,{signal}={}){
  const selected=current.current;
  try{const result=await uploadProjectAttachments(projectId,selected,{signal,onProgress:value=>{if(mounted.current)setProgress(value)},onUploaded:(id,uploaded)=>{replace(current.current.map(item=>item.localId===id?{...item,uploaded}:item))}});if(mounted.current&&selected.length)setProgress({status:'complete',total:selected.length});return result}
  catch(problem){if(mounted.current&&problem.name==='AbortError')setProgress(previous=>({...previous,status:'cancelled'}));throw problem}
 }
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;for(const item of current.current)URL.revokeObjectURL(item.preview);current.current=[]}},[]);
 return {items,error,progress,add,addImages,remove,clear,onPaste,onDrop,onDragOver,upload};
}

function AttachmentIcon({name}){
 const extension=(name||'').split('.').at(-1).toLowerCase(),Icon=['stl','step','stp','3mf','obj','blend','glb','gltf','scad','igs','iges'].includes(extension)?Box:['zip','7z','rar','gz','tar'].includes(extension)?FileArchive:['py','js','jsx','ts','tsx','json','yaml','yml','html','css','xml','sh'].includes(extension)?FileCode:['pdf','doc','docx','xls','xlsx','ppt','pptx','txt','md','csv'].includes(extension)?FileText:File;
 return <Icon size={18} aria-hidden="true"/>;
}

export function AttachmentGallery({images=[],files=[],projectId}){
 const visible=(Array.isArray(images)?images:[]).filter(image=>image&&safeAttachmentUrl(image.url,projectId));
 const documents=(Array.isArray(files)?files:[]).filter(file=>file&&safeProjectFileUrl(file.url,projectId));
 if(!visible.length&&!documents.length)return null;
 return <>{visible.length>0&&<div className="message-images">{visible.map(image=><a key={image.id} href={image.url} target="_blank" rel="noreferrer"><img src={image.url} alt={image.name||'项目参考图'} loading="lazy"/><span>{image.name||'参考图片'}</span></a>)}</div>}{documents.length>0&&<div className="message-files">{documents.map(file=><a className="message-file" key={file.id} href={file.url} download={file.name} aria-label={`下载附件 ${file.name}`}><AttachmentIcon name={file.name}/><span><strong title={file.name}>{file.name||'附件'}</strong><small>{attachmentSize(file.size)}</small></span><Download size={15} aria-hidden="true"/></a>)}</div>}</>;
}

export default function ImageAttachments({attachments,disabled=false,label='添加参考图片',compact=false}){
 const input=useRef(null),fileInput=useRef(null),images=attachments.items.filter(item=>attachmentKind(item.file)==='image'),files=attachments.items.filter(item=>attachmentKind(item.file)==='file');
 const itemStatus=item=>item.uploaded?'已上传':attachments.progress?.localId===item.localId&&attachments.progress.status==='uploading'?'正在上传':attachments.progress?.localId===item.localId&&attachments.progress.status==='failed'?'上传失败':'待上传';
 return <div className="image-attachments" onDragOver={disabled?undefined:attachments.onDragOver} onDrop={disabled?undefined:attachments.onDrop} onPaste={disabled?undefined:attachments.onPaste}>
 <div className="image-attachments-toolbar"><button type="button" className="button secondary" aria-label={label} title={compact?`${label}，每张 12 MB 以内`:undefined} disabled={disabled} onClick={()=>input.current?.click()}><ImagePlus size={17}/>{compact?'图片':label}</button><button type="button" className="button secondary" aria-label="添加附件" title={compact?'添加附件，每个 100 MB 以内':undefined} disabled={disabled} onClick={()=>fileInput.current?.click()}><Paperclip size={17}/>{compact?'附件':'添加附件'}</button><span>可拖入文件或粘贴图片 · 图片 12 MB / 附件 100 MB 以内</span></div>
 <input ref={input} className="image-file-input" type="file" accept="image/png,image/jpeg,image/webp" multiple aria-label={label} disabled={disabled} onChange={event=>{attachments.addImages(event.target.files);event.target.value=''}}/>
 <input ref={fileInput} className="image-file-input" type="file" multiple aria-label="添加附件" disabled={disabled} onChange={event=>{attachments.add(event.target.files);event.target.value=''}}/>
 {images.length>0&&<div className="draft-images">{images.map(item=>{const failed=attachments.progress?.localId===item.localId&&attachments.progress.status==='failed';return <div className={`draft-image ${failed?'draft-image-failed':''}`} key={item.localId}><img src={item.preview} alt={item.file.name||'待发送参考图'}/><button type="button" className="icon-button" aria-label={`移除图片 ${item.file.name}`} disabled={disabled} onClick={()=>attachments.remove(item.localId)}><X size={15}/></button><span title={item.file.name}>{item.file.name||'参考图片'}</span><small className="draft-image-status">{itemStatus(item)}</small></div>})}</div>}
 {files.length>0&&<ul className="draft-files" aria-label="待发送附件">{files.map(item=><li className={`draft-file ${itemStatus(item)==='上传失败'?'draft-file-failed':''}`} key={item.localId}><a href={item.preview} download={item.file.name} aria-label={`下载草稿附件 ${item.file.name}`}><AttachmentIcon name={item.file.name}/><span><strong title={item.file.name}>{item.file.name}</strong><small>{attachmentSize(item.file.size)} · {itemStatus(item)}</small></span><Download size={14} aria-hidden="true"/></a><button type="button" className="icon-button" aria-label={`移除附件 ${item.file.name}`} disabled={disabled} onClick={()=>attachments.remove(item.localId)}><X size={15}/></button></li>)}</ul>}
 {attachments.progress&&<p className="image-upload-progress" role="status" aria-live="polite">{attachments.progress.status==='uploading'?`正在上传第 ${attachments.progress.index} / ${attachments.progress.total} 个文件：${attachments.progress.name}`:attachments.progress.status==='complete'?`${attachments.progress.total} 个文件已上传。`:attachments.progress.status==='cancelled'?'已取消上传，草稿和已上传的文件已保留。':attachments.progress.status==='failed'?`第 ${attachments.progress.index} / ${attachments.progress.total} 个文件上传未完成，其余文件已保留。`:`已上传 ${attachments.progress.index} / ${attachments.progress.total} 个文件`}</p>}
 {attachments.error&&<p className="image-upload-error" role="alert">{attachments.error}</p>}
 </div>;
}
