import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import ReactMarkdown from 'react-markdown';
import {Send,Square,RefreshCw,MessageSquare,CheckCircle2,AlertCircle,ChevronRight,Maximize2,Minimize2,ChevronsUpDown} from 'lucide-react';
import {createRequestGate} from './presentation';
import {agentPath,isActiveRun,mergeAgentEvents,mergeAgentRun,assistantPreview,createAgentRequestId,agentRead,agentWrite} from './agent-client';
import ImageAttachments,{AttachmentGallery,useProjectAttachments} from './ImageAttachments';
import AgentUsage from './AgentUsage';
import {defaultAttachmentMessage} from './file-attachments';
import {resolveAgentSelection,selectionModels,agentSelectionFields,agentSubmissionFingerprint} from './model-providers';
import './agent-chat.css';
import './model-settings.css';

const statuses={queued:'任务已排队',starting:'正在准备项目',running:'Codex 正在处理',awaiting_input:'等你补充信息',awaiting_approval:'等你确认下一步',succeeded:'本轮已完成',failed:'本轮未完成',interrupted:'任务已中断',cancelled:'任务已取消'};
const when=value=>{if(!value)return '';const date=new Date(value);return Number.isNaN(+date)?'':date.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})};
const readable=value=>typeof value==='string'?value:value?.message||'';

function PendingRequest({request,busy,onReply}){
 const [answers,setAnswers]=useState({}),questions=Array.isArray(request.questions)?request.questions:[],approval=request.kind==='approval';
 const ready=questions.length>0&&questions.every((question,index)=>answers[question.id||String(index)]?.trim());
 function submit(event){event.preventDefault();if(!ready||busy)return;onReply({answers:Object.fromEntries(questions.map((question,index)=>[question.id||String(index),{answers:[answers[question.id||String(index)].trim()]}]))})}
 return <section className="agent-question" aria-label={approval?'确认 Codex 操作':'回答 Codex 的问题'}><h3>{approval?'确认下一步操作':'需要你补充的信息'}</h3>
 {approval?<><p>{request.description||'Codex 请求执行下一步操作。请确认后继续。'}</p><div className="agent-question-actions"><button type="button" className="button primary" disabled={busy} onClick={()=>onReply({decision:'accept'})}>允许本次操作</button><button type="button" className="button secondary" disabled={busy} onClick={()=>onReply({decision:'decline'})}>拒绝本次操作</button><button type="button" className="text-link" disabled={busy} onClick={()=>onReply({decision:'cancel'})}>取消任务</button></div></>:questions.length?<form onSubmit={submit}>
 {questions.map((question,index)=>{const id=question.id||String(index),options=Array.isArray(question.options)?question.options:[];return <fieldset key={id} disabled={busy}><legend>{question.question||question.header||`问题 ${index+1}`}</legend>{options.length>0&&<div className="agent-answer-options">{options.map((option,optionIndex)=>{const label=typeof option==='string'?option:option.label;return <label key={`${id}-${optionIndex}`}><input type="radio" name={`agent-answer-${request.id}-${id}`} checked={answers[id]===label} onChange={()=>setAnswers(previous=>({...previous,[id]:label}))}/><span><strong>{label}</strong>{option.description&&<small>{option.description}</small>}</span></label>})}</div>}<label className="agent-free-answer"><span>{options.length?'补充或直接输入回答':'你的回答'}</span><textarea rows={2} value={answers[id]||''} maxLength={4000} onChange={event=>setAnswers(previous=>({...previous,[id]:event.target.value}))}/></label></fieldset>})}
 <button type="submit" className="button primary" disabled={!ready||busy}>{busy?'正在提交…':'提交回答并继续'}</button></form>:<p>当前问题暂时无法显示，请刷新任务状态。</p>}
 </section>;
}

export default function AgentChat({projectId,projectName,focused=false,onToggleFocus,statusTarget,initialUsage}){
 const [conversation,setConversation]=useState({messages:[]}),[loaded,setLoaded]=useState(false),[run,setRun]=useState(null),[events,setEvents]=useState([]),[draft,setDraft]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState(''),[loadError,setLoadError]=useState(''),[pollError,setPollError]=useState(''),[submissionPhase,setSubmissionPhase]=useState('');
 const mounted=useRef(false),loadGate=useRef(createRequestGate()),action=useRef(null),actionLock=useRef(false),pollNow=useRef(null),requestMemo=useRef(null),scrollArea=useRef(null),followOutput=useRef(true),actionError=useRef(null),uploading=useRef(false);
 const attachments=useProjectAttachments();
 const input=useRef(null),[expanded,setExpanded]=useState(false);
 const [usage,setUsage]=useState(initialUsage),usageReads=useRef({next:0,applied:0}),latestRun=useRef(null);
 const [modelSettings,setModelSettings]=useState(null),[settingsLoading,setSettingsLoading]=useState(true),[settingsError,setSettingsError]=useState(''),[modelSelection,setModelSelection]=useState({}),[listedModels,setListedModels]=useState({}),[modelsError,setModelsError]=useState('');
 const settingsGate=useRef(createRequestGate()),modelsGate=useRef(createRequestGate()),selectionTouched=useRef(false);
 latestRun.current=run;
 const active=isActiveRun(run),base=agentPath(projectId),messages=Array.isArray(conversation.messages)?conversation.messages:[],pending=['awaiting_input','awaiting_approval'].includes(run?.status)?run?.pending_request:null;
 const preview=assistantPreview(events,run?.result),savedAnswer=!active&&messages.some(message=>message.run_id===run?.id&&message.role==='assistant'&&readable(message.content)===preview);
 const progress=events.filter(event=>!['assistant_delta','assistant_message','completed'].includes(event.type)&&event.text).slice(-80);
 const providers=Array.isArray(modelSettings?.providers)?modelSettings.providers.filter(provider=>provider.enabled!==false):[],selected=resolveAgentSelection(modelSettings,modelSelection),selectedProvider=providers.find(provider=>provider.id===selected.provider_id);
 const models=selectionModels(selectedProvider,listedModels.provider_id===selected.provider_id?listedModels.rows:null),selectedModel=models.some(model=>model.id===selected.model)?selected.model:models.find(model=>model.default)?.id||models[0]?.id||selectedProvider?.default_model||'';
 const setupMissing=modelSettings&&!providers.length,modelSetupReady=!settingsLoading&&!settingsError&&!!modelSettings&&providers.length>0,selectedFields={provider_id:selected.provider_id,model:selectedModel};

 function acceptUsage(value,ticket){
  if(value&&typeof value==='object'&&!Array.isArray(value)&&ticket>=usageReads.current.applied){usageReads.current.applied=ticket;setUsage(value)}
 }

 async function loadConversation(syncRun=false){
  const current=loadGate.current.begin();
  try{
   await agentRead('/api/prepared/session',{signal:current.signal});
   const usageTicket=++usageReads.current.next;
   const data=await agentRead(base,{signal:current.signal});
   if(!current.isCurrent()||!mounted.current)return null;
   acceptUsage(data.agent_usage,usageTicket);
   if(!selectionTouched.current&&data.selection)setModelSelection(data.selection);
   setConversation(data);setLoaded(true);setLoadError('');
   if(syncRun){const latest=data.active_run||data.latest_run;const previousMessage=[...(data.messages||[])].reverse().find(message=>message.run_id);setRun(latest||(previousMessage?{id:previousMessage.run_id,project_id:projectId,status:'starting'}:null))}
   return data;
  }catch(problem){if(current.isCurrent()&&mounted.current){setLoadError(problem.message);setLoaded(true)}return null}
 }

 useEffect(()=>{
  mounted.current=true;setConversation({messages:[]});setRun(null);setEvents([]);setLoaded(false);setError('');setLoadError('');setPollError('');loadConversation(true);
  return()=>{mounted.current=false;loadGate.current.cancel();action.current?.abort();pollNow.current=null};
 },[projectId]);

 async function loadModelSettings(){
  const current=settingsGate.current.begin();setSettingsLoading(true);setSettingsError('');
  try{await agentRead('/api/prepared/session',{signal:current.signal});const data=await agentRead('/api/agent/settings',{signal:current.signal});if(current.isCurrent())setModelSettings(data)}
  catch(problem){if(current.isCurrent())setSettingsError(problem.message)}finally{if(current.isCurrent())setSettingsLoading(false)}
 }
 useEffect(()=>{
  selectionTouched.current=false;setModelSelection({});setModelSettings(null);setListedModels({});setModelsError('');
  loadModelSettings();window.addEventListener('printlab:models-changed',loadModelSettings);
  return()=>{settingsGate.current.cancel();modelsGate.current.cancel();window.removeEventListener('printlab:models-changed',loadModelSettings)};
 },[projectId]);
 useEffect(()=>{
  if(!selectedProvider?.id)return;
  const current=modelsGate.current.begin(),providerId=selectedProvider.id;setListedModels({provider_id:providerId,rows:null});
  agentRead(`/api/agent/models?provider_id=${encodeURIComponent(providerId)}`,{signal:current.signal}).then(data=>{if(current.isCurrent()){setListedModels({provider_id:providerId,rows:Array.isArray(data.models)?data.models:null});setModelsError('')}}).catch(problem=>{if(current.isCurrent())setModelsError(problem.message)});
  return()=>modelsGate.current.cancel();
 },[selectedProvider?.id,selectedProvider?.revision]);

 useEffect(()=>{
  if(!run?.id)return;
  const gate=createRequestGate(),runId=run.id;let timer,cursor=0,notified=false;setEvents([]);setPollError('');
  const poll=async()=>{
   clearTimeout(timer);const current=gate.begin(),usageTicket=++usageReads.current.next;
   try{
    const data=await agentRead(`${agentPath(projectId,runId)}?after=${cursor}`,{signal:current.signal});
    if(!current.isCurrent()||!mounted.current)return;
    if(data.id!==runId||(data.project_id&&data.project_id!==projectId))throw Error('收到的任务与当前项目不一致，请刷新重试。');
    if(mergeAgentRun(latestRun.current,data)!==latestRun.current)acceptUsage(data.agent_usage,usageTicket);
    const incoming=Array.isArray(data.events)?data.events:[];
    cursor=Math.max(cursor,...incoming.filter(event=>Number.isInteger(event.seq)).map(event=>event.seq));
    setEvents(previous=>mergeAgentEvents(previous,incoming));setRun(previous=>mergeAgentRun(previous,data));setPollError('');
    if(isActiveRun(data))timer=setTimeout(poll,1500);
    else if(!notified){notified=true;await loadConversation(false);window.dispatchEvent(new Event('printlab:refresh'))}
   }catch(problem){if(current.isCurrent()&&mounted.current){setPollError(problem.message);timer=setTimeout(poll,4000)}}
  };
  pollNow.current=poll;poll();return()=>{gate.cancel();clearTimeout(timer);if(pollNow.current===poll)pollNow.current=null};
 },[projectId,run?.id]);

 useEffect(()=>{if(followOutput.current&&scrollArea.current)scrollArea.current.scrollTop=scrollArea.current.scrollHeight},[messages.length,preview,progress.length,pending?.id]);
 useEffect(()=>{
  const node=scrollArea.current;if(!node)return;
  const follow=()=>{if(followOutput.current)node.scrollTop=node.scrollHeight};
  const observer=new ResizeObserver(follow);observer.observe(node);node.addEventListener('load',follow,true);
  return()=>{observer.disconnect();node.removeEventListener('load',follow,true)};
 },[]);
 useEffect(()=>{if(error&&actionError.current){actionError.current.focus({preventScroll:true});actionError.current.scrollIntoView({block:'nearest'})}},[error]);
 useLayoutEffect(()=>{const node=input.current;if(node){node.style.height='auto';node.style.height=`${node.scrollHeight}px`}},[draft,expanded]);
 useEffect(()=>{
  const viewport=window.visualViewport;
  const resize=()=>{document.documentElement.style.setProperty('--workspace-viewport',`${viewport?.height||window.innerHeight}px`);const node=input.current;if(node){node.style.height='auto';node.style.height=`${node.scrollHeight}px`}};
  resize();viewport?.addEventListener('resize',resize);window.addEventListener('resize',resize);
  return()=>{viewport?.removeEventListener('resize',resize);window.removeEventListener('resize',resize);document.documentElement.style.removeProperty('--workspace-viewport')};
 },[]);

 function requestId(kind,message){const key=`${kind}:${message}`;if(requestMemo.current?.key!==key)requestMemo.current={key,id:createAgentRequestId()};return requestMemo.current.id}
 async function begin(resume=false){
  const message=draft.trim()||defaultAttachmentMessage(attachments.items)||(resume?'接着上次任务继续。先核对项目文件与最近交接，说明当前进度，再处理未完成事项。':'');
  if(!loaded||!message||active||actionLock.current||(!resume&&!modelSetupReady))return;
  const selection=agentSelectionFields(selectedFields,resume);
  actionLock.current=true;action.current=new AbortController();uploading.current=attachments.items.length>0;setSubmissionPhase(uploading.current?'uploading':'submitting');setBusy(resume?'resume':'submit');setError('');followOutput.current=true;
  try{
   const {images,files}=await attachments.upload(projectId,{signal:action.current.signal}),attachment_ids=images.map(image=>image.id),file_ids=files.map(file=>file.id);
   if(action.current.signal.aborted)throw new DOMException('上传已取消','AbortError');
   uploading.current=false;setSubmissionPhase('submitting');
   const path=resume&&run?.id?`${agentPath(projectId,run.id)}/resume`:`${base}/runs`;
   const data=await agentWrite(path,{message,attachment_ids,file_ids,...selection,request_id:requestId(resume?'resume':'submit',agentSubmissionFingerprint({message,attachment_ids,file_ids},selection,resume))},{signal:action.current.signal});
   if(mounted.current){setRun(data.run||data);setEvents([]);setDraft('');attachments.clear();requestMemo.current=null;await loadConversation(false)}
  }catch(problem){if(mounted.current&&problem.name!=='AbortError'){setError(problem.message);if(!uploading.current)await loadConversation(true)}}
  finally{uploading.current=false;actionLock.current=false;if(mounted.current){setBusy('');setSubmissionPhase('')}}
 }
 function cancelUpload(){if(uploading.current)action.current?.abort()}
 async function cancel(){
  if(!run?.id||!active||actionLock.current)return;actionLock.current=true;action.current=new AbortController();setBusy('cancel');setError('');
  try{const data=await agentWrite(`${agentPath(projectId,run.id)}/cancel`,{},{signal:action.current.signal});if(mounted.current){setRun(previous=>mergeAgentRun(previous,data.run||data));pollNow.current?.()}}
  catch(problem){if(mounted.current&&problem.name!=='AbortError')setError(problem.message)}finally{actionLock.current=false;if(mounted.current)setBusy('')}
 }
 async function reply(answer){
  if(!run?.id||!pending||actionLock.current)return;actionLock.current=true;action.current=new AbortController();setBusy('reply');setError('');
  try{const data=await agentWrite(`${agentPath(projectId,run.id)}/reply`,answer,{signal:action.current.signal});if(mounted.current){if(data.run||data.id)setRun(previous=>mergeAgentRun(previous,data.run||data));pollNow.current?.()}}
  catch(problem){if(mounted.current&&problem.name!=='AbortError')setError(problem.message)}finally{actionLock.current=false;if(mounted.current)setBusy('')}
 }
 async function reconcile(){await loadConversation(true);pollNow.current?.()}

 const runStatus=<section className={`agent-run-status agent-status-${run?.status||'idle'}`} aria-label="Codex 任务状态">{run&&<div className="agent-run-heading"><span role="status">{run.status==='succeeded'?<CheckCircle2 size={16}/>:['failed','interrupted'].includes(run.status)?<AlertCircle size={16}/>:<span className={`agent-state-dot ${active?'active':''}`}/>}<strong>{statuses[run.status]||'正在核对任务状态'}</strong></span><div className="agent-run-actions">{progress.length>0&&<details key={run.id} className="agent-progress"><summary>执行记录 · {progress.length}</summary><ol>{progress.map(event=><li key={event.seq} className={`agent-event-${event.type}`}><time>{when(event.at)}</time><pre>{event.text}</pre></li>)}</ol></details>}{pending&&<button type="button" className="text-link" onClick={()=>{followOutput.current=true;scrollArea.current.scrollTop=scrollArea.current.scrollHeight}}>查看问题</button>}{active?<button type="button" className="text-link" disabled={!!busy} onClick={cancel}><Square size={14}/>{busy==='cancel'?'正在取消…':'取消任务'}</button>:<button type="button" className="text-link" disabled={!!busy} onClick={()=>begin(true)}><ChevronRight size={16}/>{busy==='resume'?'正在接续…':'继续上次任务'}</button>}</div></div>}
 <AgentUsage usage={usage} run={run}/>
 {readable(run?.error)&&<p className="agent-error-text">{readable(run.error)}</p>}{pollError&&<p className="agent-error-text" role="alert">{pollError} 后台任务可能仍在继续，正在重新读取。</p>}
 </section>;

 return <section className="agent-chat" aria-label={`${projectName||projectId} 项目对话`}>
 {statusTarget&&!focused?createPortal(runStatus,statusTarget):runStatus}
 {loadError&&<div className="agent-error" role="alert"><AlertCircle size={18}/><span>{loadError}</span></div>}
 <div className="agent-transcript" ref={scrollArea} onScroll={event=>{const node=event.currentTarget;followOutput.current=node.scrollHeight-node.scrollTop-node.clientHeight<90}} role="log" aria-label="项目对话记录" aria-live="polite" aria-relevant="additions">
 {!loaded?<p className="muted" role="status">正在读取项目对话…</p>:!messages.length&&!run?<div className="agent-empty"><MessageSquare size={28}/><h3>从这个作品的下一步开始</h3><p>可以描述用途、补充尺寸，或请 Codex 根据项目资料继续建模。</p></div>:null}
 {messages.filter(message=>['user','assistant'].includes(message.role)).map((message,index)=><article className={`agent-message agent-message-${message.role}`} key={message.id||`${message.role}-${index}`}><div className="agent-message-meta"><strong>{message.role==='user'?'你':'Codex'}</strong><time>{when(message.created_at)}</time></div><AttachmentGallery images={message.attachments} files={message.files} projectId={projectId}/><div className="markdown"><ReactMarkdown>{readable(message.content)}</ReactMarkdown></div></article>)}
 {preview&&!savedAnswer&&<article className="agent-message agent-message-assistant agent-live-answer" aria-live="off"><div className="agent-message-meta"><strong>Codex</strong><span>{active?'正在回复':'本轮回复'}</span></div><div className="markdown"><ReactMarkdown>{preview}</ReactMarkdown></div></article>}
 {pending&&<PendingRequest key={`${run.id}:${pending.id}`} request={pending} busy={!!busy} onReply={reply}/>}
 </div>
 <form className={`agent-composer ${expanded?'agent-composer-expanded':''}`} aria-busy={!!busy} onPaste={busy?undefined:attachments.onPaste} onDrop={busy?undefined:attachments.onDrop} onDragOver={attachments.onDragOver} onSubmit={event=>{event.preventDefault();begin(false)}}>
 <div className="agent-composer-heading"><label htmlFor={`agent-input-${projectId}`}>{focused?`${projectId} · ${projectName||'项目对话'}`:active?'下一条需求':'接下来想做什么？'}</label><div><button type="button" className="icon-button" aria-label={expanded?'收起输入框':'展开输入框'} aria-expanded={expanded} title={expanded?'收起输入框':'展开输入框'} onClick={()=>setExpanded(value=>!value)}><ChevronsUpDown size={16}/></button><button type="button" className="icon-button" aria-label="刷新对话" title="刷新对话" disabled={!!busy} onClick={reconcile}><RefreshCw size={16}/></button>{onToggleFocus&&<button type="button" className="text-link agent-focus-toggle" aria-pressed={focused} onClick={onToggleFocus} title={focused?'退出专注（Esc）':'专注对话'}>{focused?<Minimize2 size={15}/>:<Maximize2 size={15}/>}<span>{focused?'退出专注':'专注对话'}</span></button>}</div></div>
 <textarea ref={input} disabled={!!busy} id={`agent-input-${projectId}`} value={draft} onChange={event=>setDraft(event.target.value)} maxLength={16000} rows={3} placeholder="描述需求，或添加图片、模型、文档。Ctrl / ⌘ + Enter 发送。" onKeyDown={event=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'&&!event.nativeEvent.isComposing){event.preventDefault();event.currentTarget.form.requestSubmit()}}}/>
 <ImageAttachments attachments={attachments} disabled={!!busy} compact/>
 {error&&<div ref={actionError} tabIndex={-1} className="agent-error agent-submit-error" role="alert"><AlertCircle size={18}/><div><strong>本次发送未完成</strong><p>{error}</p><p className="agent-error-retry">草稿和附件已保留，处理后可以再次发送。</p></div></div>}
 <div className="agent-composer-actions">{submissionPhase==='uploading'&&<button type="button" className="button secondary" onClick={cancelUpload}>取消上传</button>}<button type="submit" className="button primary" aria-label="发送给 Codex" disabled={!loaded||(!draft.trim()&&!attachments.items.length)||active||!!busy||!modelSetupReady}><Send size={16}/>{submissionPhase==='uploading'?'正在上传…':busy==='submit'?'正在提交…':'发送'}</button></div>
 <div className="agent-model-selection"><label><span>接入</span><select aria-label="项目模型服务" value={selected.provider_id} disabled={active||!!busy||!modelSetupReady} onChange={event=>{selectionTouched.current=true;const provider=providers.find(item=>item.id===event.target.value);setModelSelection({provider_id:provider?.id||'',model:provider?.default_model||''})}}>{!providers.length&&<option value="">{settingsLoading?'读取服务中':settingsError?'读取失败':'尚未配置'}</option>}{providers.map(provider=><option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label><label><span>模型</span><select aria-label="项目对话模型" value={selectedModel} disabled={active||!!busy||!modelSetupReady||!selected.provider_id} onChange={event=>{selectionTouched.current=true;setModelSelection({provider_id:selected.provider_id,model:event.target.value})}}>{!models.length&&<option value={selectedModel}>{selectedModel||'服务默认模型'}</option>}{models.map(model=><option key={model.id} value={model.id}>{model.name}</option>)}</select></label><a className="text-link" href="#/settings">设置</a></div>
 {settingsLoading?<p className="agent-model-note">正在读取模型接入…</p>:settingsError?<p className="agent-model-note" role="alert">无法读取模型接入，草稿已保留。<button type="button" className="text-link" onClick={loadModelSettings}>重试读取接入</button><a href="#/settings">检查设置</a></p>:setupMissing?<p className="agent-model-note">先在<a href="#/settings">模型接入</a>中添加服务，再开始协作。</p>:modelsError?<p className="agent-model-note" role="alert">模型列表暂不可用，保留当前选择。<a href="#/settings">检查接入</a></p>:null}
 {active&&<p className="agent-composer-note">{run.provider_name&&`本轮使用 ${run.provider_name}${run.selected_model?` · ${run.selected_model}`:''}。`}{pending?'请先回答问题，或取消本轮任务。':'可以先写下一条需求，本轮结束后发送。'}</p>}
 </form>
 </section>;
}
