import React,{useEffect,useRef,useState} from 'react';
import {Printer,RefreshCw,CheckCircle2,AlertCircle} from 'lucide-react';
import {agentRead,agentWrite} from './agent-client';
import {createRequestGate} from './presentation';
import {canAllocatePrinter,allocationNeedsReview} from './printer-allocation';
import './project-printer.css';

const fitNames={fits:'尺寸可容纳',too_large:'超出打印空间',unknown:'设备能力待核对'};
const dimensions=value=>Array.isArray(value)?value.map(number=>Number(number).toLocaleString('zh-CN',{maximumFractionDigits:1})).join(' × ')+' mm':'尚未核实';

export default function ProjectPrinter({project,onSaved}){
 const [data,setData]=useState(null),[loading,setLoading]=useState(false),[saving,setSaving]=useState(''),[message,setMessage]=useState(''),[error,setError]=useState(''),loadGate=useRef(createRequestGate()),saveGate=useRef(createRequestGate());
 const endpoint=`/api/projects/${encodeURIComponent(project.id)}`;
 async function load(){const current=loadGate.current.begin();setLoading(true);setError('');try{const result=await agentRead(`${endpoint}/printer-options`,{signal:current.signal});if(current.isCurrent())setData(result)}catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setLoading(false)}}
 useEffect(()=>{setData(null);setSaving('');setMessage('');load();return()=>{loadGate.current.cancel();saveGate.current.cancel()}},[project.id,project.current_revision,project.updated_at]);
 async function save(option){
  if(saving||!canAllocatePrinter(data,option,project.current_revision))return;
  const current=saveGate.current.begin();setSaving(String(option.printer_id));setError('');setMessage('');
  try{const result=await agentWrite(`${endpoint}/printer`,{printer_id:option.printer_id},{signal:current.signal});if(!result.target_printer)throw Error('打印机分配结果待核对，请刷新。');if(current.isCurrent()){onSaved(result.target_printer);setData(previous=>({...previous,selected_printer_id:result.target_printer.id,target_printer:result.target_printer,assignment_current:true}));setMessage(`已分配给 ${result.target_printer.name||option.name}，接下来核对切片设置。`);window.dispatchEvent(new Event('printlab:refresh'))}}
  catch(problem){if(current.isCurrent())setError(problem.message)}finally{if(current.isCurrent())setSaving('')}
 }
 const options=Array.isArray(data?.options)?data.options:[],selected=data?.selected_printer_id??project.target_printer?.id;
 return <section className="model-printer" aria-label="模型适配与打印机分配">
 <div className="section-heading"><div><span className="section-eyebrow"><Printer size={16}/>模型适配</span><h2>为这个模型安排打印机</h2></div><button type="button" className="button secondary" disabled={loading||!!saving} onClick={load}><RefreshCw size={16}/>重新核对</button></div>
 {loading&&!data?<p className="muted" role="status">正在读取模型与设备能力…</p>:data?.ready&&data.revision===project.current_revision?<><p className="printer-model-dimensions">当前模型 {data.revision} · {dimensions(data.dimensions_mm)}</p><p className="printer-fit-note">先按当前模型尺寸筛选。材料、喷嘴、摆放和支撑仍需在切片时核对；分配不会开始打印。</p><div className="printer-options">{options.map(option=>{const chosen=String(selected)===String(option.printer_id),allowed=canAllocatePrinter(data,option,project.current_revision),confirmed=chosen&&allowed&&data.assignment_current!==false;return <article key={option.printer_id} className={`printer-option printer-fit-${option.fit} ${confirmed?'is-selected':chosen?'needs-review':''}`}><div className="printer-option-heading"><div><h3>{option.name||option.model}</h3><span>{option.model}{option.online===false?' · 当前离线':option.online===true?' · 当前在线':''}</span></div><span className="printer-fit-label">{option.fit==='fits'?<CheckCircle2 size={15}/>:<AlertCircle size={15}/>} {fitNames[option.fit]||fitNames.unknown}</span></div><p>标称打印空间 · {dimensions(option.build_volume_mm)}</p>{Array.isArray(option.reasons)&&option.reasons.length>0&&<ul>{option.reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul>}<button type="button" className={`button ${confirmed?'secondary':'primary'}`} disabled={!allowed||!!saving||confirmed} onClick={()=>save(option)}>{saving===String(option.printer_id)?'正在分配…':confirmed?'已分配':chosen&&allowed?'确认本版本分配':allowed?'分配给这台打印机':chosen?'原分配需要重新核对':'暂不可分配'}</button></article>})}</div>{!options.length&&<p className="muted">还没有可核对的打印机，请先在设备页检查连接。</p>}</>:<div className="printer-model-pending"><Printer size={24}/><div><h3>先完成模型，再选择打印机</h3><p>{data?.reason||'模型和尺寸检查完成后，这里会列出适配的设备与待核对事项。'}</p></div></div>}
 {allocationNeedsReview(data,project.current_revision)&&<p className="printer-fit-note" role="status">原分配设备需要重新核对，请根据当前模型重新选择；已有切片任务的设备记录保留。</p>}
 {message&&<p className="printer-save-message" role="status">{message}</p>}{error&&<p className="inline-error" role="alert">{error}</p>}
 </section>;
}
