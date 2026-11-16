import React,{useEffect,useRef,useState} from 'react';
import {Plus,RefreshCw,CheckCircle2,Settings2} from 'lucide-react';
import {agentRead,agentWrite} from './agent-client';
import {createRequestGate} from './presentation';
import {providerDraft,providerPayload,providerTestPayload,providerModelIds} from './model-providers';
import './model-settings.css';

const endpoint='/api/agent/settings';

export default function ModelSettings(){
 const [settings,setSettings]=useState(null),[draft,setDraft]=useState(()=>providerDraft()),[version,setVersion]=useState(0),[makeDefault,setMakeDefault]=useState(false),[loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[conflict,setConflict]=useState(false),[notice,setNotice]=useState(''),[tested,setTested]=useState(null);
 const reads=useRef(createRequestGate()),actions=useRef(createRequestGate()),initialized=useRef(false);
 const providers=Array.isArray(settings?.providers)?settings.providers:[],selected=providers.find(provider=>provider.id===draft.id),legacy=selected?.legacy===true;
 const detected=providerModelIds(tested?.models),models=providerModelIds([...providerModelIds(draft.models_text),...detected],draft.default_model);

 function choose(provider,data=settings){
  actions.current.cancel();setBusy('');setDraft(providerDraft(provider));setVersion(data?.version??0);setMakeDefault(provider?.id===data?.default_provider_id);setError('');setConflict(false);setNotice('');setTested(null);
 }
 async function load(replace=false){
  const current=reads.current.begin();setLoading(true);
  try{
   await agentRead('/api/prepared/session',{signal:current.signal});
   const result=await agentRead(endpoint,{signal:current.signal});
   if(!current.isCurrent())return;
   setSettings(result);
   if(!initialized.current||replace){initialized.current=true;choose(result.providers?.find(provider=>provider.id===(draft.id||result.default_provider_id))||result.providers?.[0],result)}
  }catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setLoading(false)}
 }
 useEffect(()=>{load();return()=>{reads.current.cancel();actions.current.cancel()}},[]);
 function edit(field,value){
  actions.current.cancel();setBusy('');setError('');setConflict(false);setNotice('');setTested(null);setDraft(previous=>({...previous,[field]:value}));
  if(field==='enabled'&&!value)setMakeDefault(false);
 }
 async function save(defaultOnly=false){
  if(busy||!settings)return;
  const current=actions.current.begin();setBusy('save');setError('');setConflict(false);setNotice('');
  const payload=defaultOnly?{version,default_provider_id:draft.id}:providerPayload(draft,version,makeDefault);
  try{
   const result=await agentWrite(endpoint,payload,{signal:current.signal});
   if(current.isCurrent()){
    const saved=result.providers?.find(provider=>provider.id===draft.id)||result.providers?.find(provider=>!providers.some(previous=>previous.id===provider.id))||result.providers?.find(provider=>provider.id===result.default_provider_id);
    setSettings(result);choose(saved,result);setNotice('模型接入已保存，正在执行的任务继续使用原配置。');window.dispatchEvent(new Event('printlab:models-changed'));
   }
  }catch(problem){if(current.isCurrent()){setError(problem.message);setConflict(problem.status===409)}}finally{if(current.isCurrent())setBusy('')}
 }
 async function test(tools=false){
  if(busy||legacy)return;
  if(tools&&!draft.default_model){setError('请先选择或填写默认模型，再测试工具调用。');return}
  const current=actions.current.begin();setBusy(tools?'test':'models');setError('');setNotice('');setTested(null);
  try{
   const result=await agentWrite(`${endpoint}/test`,providerTestPayload(draft,tools),{signal:current.signal});
   if(current.isCurrent()){setTested(result);if(!result.ok)setError(result.message||'连接测试未通过，请检查配置。')}
  }catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setBusy('')}
 }
 const readonly=loading||busy==='save'||legacy;
 return <section className="model-settings settings-section" aria-labelledby="model-settings-heading">
  <header className="settings-section-heading"><div><h2 id="model-settings-heading"><Settings2 size={18}/>模型服务</h2><p>配置自己的 API 服务，在项目对话中选择。服务需要支持 OpenAI Responses 与工具调用。</p></div><button type="button" className="button secondary" onClick={()=>load()} disabled={loading||!!busy}><RefreshCw size={16}/>{loading?'正在读取…':'刷新列表'}</button></header>
  <div className="model-settings-toolbar"><label className="model-settings-service"><span>模型服务</span><select aria-label="编辑的模型服务" value={draft.id||''} disabled={loading||busy==='save'} onChange={event=>choose(providers.find(provider=>provider.id===event.target.value))}><option value="">新服务</option>{providers.map(provider=><option key={provider.id} value={provider.id}>{provider.name}{provider.id===settings?.default_provider_id?' · 默认':''}{provider.enabled===false?' · 已停用':''}{provider.legacy?' · 原有配置':''}</option>)}</select></label><button type="button" className="button secondary" onClick={()=>choose(null)} disabled={loading||busy==='save'}><Plus size={16}/>添加服务</button></div>
  {legacy&&<p className="model-settings-note">此服务沿用服务器原有 Codex 配置，只能查看或设为默认。新接入请使用“添加服务”。</p>}
  <form className="model-settings-form" onSubmit={event=>{event.preventDefault();save(legacy)}} aria-busy={!!busy}>
   <fieldset disabled={readonly}>
    <div className="model-settings-fields">
     <label className="field">服务名称<input required autoComplete="off" value={draft.name} maxLength={80} onChange={event=>edit('name',event.target.value)} placeholder="例如：我的 API 服务"/></label>
     <label className="field">Base URL<input required type="url" autoComplete="off" value={draft.base_url} maxLength={2048} onChange={event=>edit('base_url',event.target.value)} placeholder="https://example.com/v1"/></label>
     {!legacy&&<label className="field model-settings-key">API Key<input type="password" autoComplete="new-password" spellCheck={false} value={draft.api_key} onChange={event=>edit('api_key',event.target.value)} placeholder={selected?.key_configured?'留空保留已有密钥':'填写此服务的 API Key'}/><small>{selected?.key_configured?'已保存密钥；不会回显。':'密钥仅保存在服务端私有配置中。'}</small></label>}
     <label className="field model-settings-models">模型 ID<textarea value={draft.models_text} onChange={event=>edit('models_text',event.target.value)} rows={4} spellCheck={false} placeholder={'每行一个模型 ID\n例如：my-model'}/><small>使用服务实际提供的模型 ID；图片输入能力取决于模型。</small></label>
     <div className="model-settings-options"><label className="field">默认模型<select aria-label="默认模型" value={draft.default_model} onChange={event=>edit('default_model',event.target.value)}><option value="">选择默认模型</option>{models.map(model=><option key={model} value={model}>{model}</option>)}</select></label><label className="model-settings-check"><input type="checkbox" checked={draft.enabled} onChange={event=>edit('enabled',event.target.checked)}/>启用此服务</label><label className="model-settings-check"><input type="checkbox" checked={makeDefault} disabled={!draft.enabled} onChange={event=>setMakeDefault(event.target.checked)}/>保存后设为默认</label></div>
    </div>
   </fieldset>
   {!legacy&&<div className="model-settings-test-actions"><button type="button" className="button secondary" disabled={!!busy||loading||!draft.base_url.trim()} onClick={()=>test(false)}>{busy==='models'?'正在获取…':'获取模型'}</button><button type="button" className="button secondary" disabled={!!busy||loading||!draft.base_url.trim()||!draft.default_model} onClick={()=>test(true)}>{busy==='test'?'正在测试…':'测试工具调用'}</button>{detected.length>0&&<button type="button" className="text-link" onClick={()=>{const modelsText=detected.join('\n');edit('models_text',modelsText)}}>填入 {detected.length} 个模型</button>}</div>}
   {tested?.ok&&<p className="model-settings-result" role="status"><CheckCircle2 size={16}/>{tested.message||`已获取 ${detected.length} 个模型。`}{tested.tool_supported===null&&' 模型列表可读取；尚未测试工具调用。'}</p>}
   {error&&<div className="model-settings-error" role="alert"><p>{error}</p>{conflict?<button type="button" className="text-link" onClick={()=>load(true)}>重新载入设置（放弃未保存更改）</button>:<p>未保存的内容已保留，可以修改后重试。</p>}</div>}
   {notice&&<p className="model-settings-result" role="status">{notice}</p>}
   <div className="model-settings-save"><span><Settings2 size={15}/>只支持 Responses 协议；接入切换不会清除项目文件。</span><button type="submit" className="button primary" disabled={loading||!!busy||!settings||(legacy&&draft.id===settings.default_provider_id)}>{busy==='save'?'正在保存…':legacy?'设为默认服务':'保存设置'}</button></div>
  </form>
 </section>;
}
