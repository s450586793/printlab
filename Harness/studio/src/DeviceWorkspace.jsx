import React,{useEffect,useRef,useState} from 'react';
import {Printer,RefreshCw,WifiOff,ArrowUpRight,FolderOpen,Package} from 'lucide-react';
import {AMSSlots,MaterialInventory} from './Materials';
import {createRequestGate,printerApiPath,materialDisplayColor} from './presentation';
import {printerTelemetry} from './device-presentation';
import './device-workspace.css';

const stamp=value=>{const d=new Date(value);return value&&!Number.isNaN(+d)?d.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'尚未读取'};
const slotStates={empty:'空槽',unknown:'状态未知',unidentified:'材料未识别',identifying:'识别中'};

function Camera({device,name,loading,snapshot}){
 const url=device?.camera_image_url,src=url?`${url}${url.includes('?')?'&':'?'}snapshot=${encodeURIComponent(`${device.updated_at||'current'}:${snapshot}`)}`:null;
 const [failed,setFailed]=useState(false);
 useEffect(()=>setFailed(false),[src]);
 return <div className="printer-monitor-camera">{src&&!failed?<img src={src} alt={`${name} 摄像头快照`} onError={()=>setFailed(true)}/>:<div className="printer-camera-placeholder"><WifiOff size={26}/><p>{loading&&!device?'正在读取摄像头…':failed?'暂时无法读取画面':'暂无可用摄像头'}</p><span>其他打印参数仍可单独查看</span></div>}<span className="printer-camera-caption">摄像头快照 · 每 30 秒刷新</span></div>;
}

function LoadedMaterials({device}){
 const modules=device?.material_slots?.modules||[];
 return <section className="printer-loaded-materials" aria-label="当前装料"><h3><Package size={15}/>当前装料</h3>{modules.length?modules.map((module,i)=><div className="printer-loaded-module" key={`${module.name}-${i}`}><span className="printer-module-name">{module.name}</span><div className="printer-loaded-slots">{(module.slots||[]).map(slot=><div className="printer-loaded-slot" key={slot.slot_number}><span className="printer-slot-swatch" style={{background:slot.state==='loaded'?materialDisplayColor(slot).hex:'var(--faint)'}}/><span className="printer-slot-number">{slot.slot_number}</span><strong>{slot.state==='loaded'?(slot.english_label||slot.material||'材料未识别'):(slotStates[slot.state]||'状态未知')}</strong></div>)}</div></div>):<p className="muted">尚未读到装料信息</p>}<details className="printer-material-details"><summary>查看装料详情与余量依据</summary><AMSSlots device={device}/></details></section>;
}

function PrinterMonitor({printer,initialDevice,request,managementUrl}){
 const [device,setDevice]=useState(initialDevice||null),[loading,setLoading]=useState(true),[error,setError]=useState(false),[snapshot,setSnapshot]=useState(0),gate=useRef(createRequestGate());
 useEffect(()=>{
  const load=async()=>{const current=gate.current.begin();setLoading(true);try{const next=await request(printerApiPath('/api/device',printer.id),{signal:current.signal});if(next.printer?.id!==printer.id)throw Error('打印机数据不匹配');if(current.isCurrent()){setDevice(next);setError(false)}}catch(problem){if(current.isCurrent())setError(true)}finally{if(current.isCurrent()){setLoading(false);setSnapshot(value=>value+1)}}};
  load();addEventListener('printlab:refresh',load);return()=>{gate.current.cancel();removeEventListener('printlab:refresh',load)};
 },[printer.id,request]);
 const name=printer.name||printer.model||'打印机',telemetry=printerTelemetry(device),stale=error||device?.stale||device?.state==='offline';
 return <article className="printer-monitor" aria-label={`${name} 打印机`} aria-busy={loading}>
 <header className="printer-monitor-heading"><div><Printer size={18}/><h2>{name}</h2>{printer.model&&printer.model!==name&&<span>{printer.model}</span>}</div><span className={`status ${telemetry.attention||error?'attention':device?.state==='online'?'positive':''}`}><span/>{error?'读取失败':device?.state==='online'?'已连接':loading?'正在读取':'未连接'}</span></header>
 <Camera device={device} name={name} loading={loading} snapshot={snapshot}/>
 <section className="printer-monitor-parameters" aria-label={`${name} 打印参数`}>
 <div className="printer-print-state"><strong>{stale&&device?'上次状态：':''}{telemetry.status}</strong><span className="updated-note">{stamp(device?.updated_at)}</span></div>
 <div className="printer-current-task"><span>{telemetry.active?'当前任务':'设备上次任务'}</span><p>{device?.printer?.current_print||'暂无任务信息'}</p></div>
 <div className="printer-progress-label"><span>打印进度</span><strong>{telemetry.progressLabel}</strong></div><div className={`printer-progress ${telemetry.progress===null?'unknown':''}`} role={telemetry.progress===null?undefined:'progressbar'} aria-label={`${name} 打印进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={telemetry.progress??undefined}><span style={{width:`${telemetry.progress??0}%`}}/></div>
 <dl className="printer-parameter-grid">{telemetry.metrics.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
 {device?.printer?.hms_errors&&<p className="inline-error" role="alert">设备报告故障，请在 Home Assistant 中查看详情。</p>}
 {(stale||!device)&&<p className="printer-telemetry-note" role={error?'alert':undefined}>{error?(device?'刷新失败，保留上次读取的数据。':'暂时无法读取此打印机，请重试。'):device?'设备离线或数据已过期，当前显示上次读取值。':'等待打印机数据。'}</p>}
 <LoadedMaterials device={device}/>
 <a className="printer-ha-link text-link" href={managementUrl(`/config/devices/device/${encodeURIComponent(printer.id)}`)} target="_blank" rel="noreferrer">在 Home Assistant 中查看<ArrowUpRight size={15}/></a>
 </section></article>;
}

export default function DeviceWorkspace({data,request,managementUrl}){
 const printers=data.devices?.length?data.devices:data.device?.printer?.id?[data.device.printer]:[];
 return <div className="device-workspace"><header className="page-heading"><div><h1>设备与材料</h1><p>{printers.length?`${printers.length} 台设备的画面与打印参数，每 30 秒自动刷新。`:'连接 Home Assistant 后，在这里查看打印机画面与参数。'}</p></div><button className="button secondary" onClick={()=>dispatchEvent(new Event('printlab:refresh'))}><RefreshCw size={17}/>刷新状态</button></header>
 <div className="printer-monitor-grid">{printers.map(printer=><PrinterMonitor key={printer.id} printer={printer} initialDevice={data.device?.printer?.id===printer.id?data.device:null} request={request} managementUrl={managementUrl}/>)}</div>
 {!printers.length&&<div className="material-data-empty"><WifiOff size={24}/><p>尚未发现打印机，请在设置中检查 Home Assistant 与 ha-bambulab 集成。</p><a className="text-link" href="#/settings">打开设置</a></div>}
 <section className="section-block"><div className="section-heading"><h2>料卷库存</h2><a className="text-link" href="#/history"><FolderOpen size={16}/>模型缓存</a></div><MaterialInventory inventory={data.inventory} provider="homeassistant"/></section>
 </div>;
}
