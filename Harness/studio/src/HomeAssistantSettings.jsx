import React,{useEffect,useRef,useState} from 'react';
import {CheckCircle2,Plug,RefreshCw,Unplug} from 'lucide-react';
import {agentRead,agentWrite} from './agent-client';
import {createRequestGate} from './presentation';
import {homeAssistantPayload,homeAssistantStatus} from './homeassistant-settings';

const endpoint='/api/settings/home-assistant';

export default function HomeAssistantSettings({onChanged}){
 const [connection,setConnection]=useState(null),[baseUrl,setBaseUrl]=useState(''),[token,setToken]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[tested,setTested]=useState(null);
 const reads=useRef(createRequestGate()),actions=useRef(createRequestGate()),initialized=useRef(false);
 const status=homeAssistantStatus(connection),devices=Array.isArray(connection?.devices)?connection.devices:[];
 async function load(){
  const current=reads.current.begin();setLoading(true);setError('');
  try{
   await agentRead('/api/prepared/session',{signal:current.signal});
   const result=await agentRead(endpoint,{signal:current.signal});
   if(current.isCurrent()){
    setConnection(result);
    if(!initialized.current){initialized.current=true;setBaseUrl(result.base_url||'')}
   }
  }catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setLoading(false)}
 }
 useEffect(()=>{load();return()=>{reads.current.cancel();actions.current.cancel()}},[]);
 function edit(setter,value){setter(value);setError('');setNotice('');setTested(null)}
 async function apply(action){
  if(busy||loading)return;
  const current=actions.current.begin();setBusy(action);setError('');setNotice('');setTested(null);
  try{
   const path=action==='save'?endpoint:`${endpoint}/${action}`;
   const result=await agentWrite(path,action==='disconnect'?{}:homeAssistantPayload(baseUrl,token),{signal:current.signal});
   if(!current.isCurrent())return;
   if(action!=='disconnect'&&result.connected!==true){setError(result.message||'尚未连接到 Home Assistant，请检查地址和访问令牌。');return}
   if(action==='test'){setTested(result);return}
   setConnection(result);setBaseUrl(result.base_url||'');setToken('');
   setNotice(action==='disconnect'?'已断开 Home Assistant，仍可继续建模。':'Home Assistant 已保存，设备列表正在刷新。');
   onChanged?.();
  }catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setBusy('')}
 }
 return <section className="model-settings homeassistant-settings settings-section" aria-labelledby="homeassistant-heading">
  <header className="settings-section-heading"><div><h2 id="homeassistant-heading"><Plug size={18}/>Home Assistant</h2><p>可选连接。通过 ha-bambulab 集成发现你的打印机，显示摄像头与打印参数。</p></div><button type="button" className="button secondary" onClick={load} disabled={loading||!!busy}><RefreshCw size={16}/>{loading?'正在读取…':'刷新状态'}</button></header>
  <div className="homeassistant-status"><span className={`status ${status.connected?'positive':connection?.configured?'attention':''}`}><span/>{status.label}</span>{connection?.message&&<p>{connection.message}</p>}</div>
  <form className="model-settings-form" onSubmit={event=>{event.preventDefault();apply('save')}} aria-busy={!!busy}>
   <fieldset disabled={loading||!!busy}>
    <div className="model-settings-fields">
     <label className="field">Home Assistant 地址<input type="url" required aria-label="Home Assistant 地址" aria-describedby="homeassistant-url-note" autoComplete="off" maxLength={2048} value={baseUrl} onChange={event=>edit(setBaseUrl,event.target.value)} placeholder="http://homeassistant.local:8123"/><small id="homeassistant-url-note">填写 PrintLab 容器可以访问的地址。</small></label>
     <label className="field">长期访问令牌<input type="password" aria-label="长期访问令牌" aria-describedby="homeassistant-token-note" autoComplete="new-password" spellCheck={false} value={token} onChange={event=>edit(setToken,event.target.value)} placeholder={connection?.configured?'同一地址留空可保留已有凭据':'填写长期访问令牌'}/><small id="homeassistant-token-note">在 Home Assistant 的个人资料 → 安全中创建。令牌仅存于服务端，不会回显。</small></label>
    </div>
   </fieldset>
   <p className="model-settings-note">先在 Home Assistant 安装并配置 ha-bambulab。未发现打印机时，也可以保存连接并稍后刷新。</p>
   {devices.length>0&&<ul className="homeassistant-devices" aria-label="已发现的打印机">{devices.map(device=><li key={device.id}><strong>{device.name||device.model||'打印机'}</strong>{device.model&&device.model!==device.name&&<span>{device.model}</span>}</li>)}</ul>}
   {tested?.connected&&<p className="model-settings-result" role="status"><CheckCircle2 size={16}/>{`连接测试通过，发现 ${Array.isArray(tested.devices)?tested.devices.length:0} 台打印机。尚未保存。`}</p>}
   {error&&<div className="model-settings-error" role="alert"><p>{error}</p><p>已保存的连接和未提交的输入已保留，可以修改后重试。</p></div>}
   {notice&&<p className="model-settings-result" role="status">{notice}</p>}
   <div className="model-settings-save homeassistant-actions"><button type="button" className="text-link homeassistant-disconnect" disabled={loading||!!busy||!connection?.configured} onClick={()=>apply('disconnect')}><Unplug size={15}/>{busy==='disconnect'?'正在断开…':'断开并移除凭据'}</button><div><button type="button" className="button secondary" disabled={loading||!!busy||!baseUrl.trim()} onClick={()=>apply('test')}>{busy==='test'?'正在测试…':'测试连接'}</button><button type="submit" className="button primary" disabled={loading||!!busy||!baseUrl.trim()}>{busy==='save'?'正在保存…':'保存连接'}</button></div></div>
  </form>
 </section>;
}
