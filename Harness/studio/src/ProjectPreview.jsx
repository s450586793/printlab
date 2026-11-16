import React,{lazy,Suspense,useState} from 'react';
import {Box,Download,FileText} from 'lucide-react';
import {projectDownloadFiles,projectModelChoices} from './project-models';

const ModelViewer=lazy(()=>import('./ModelViewer'));
const sizeText=n=>!n?'—':n>1048576?`${(n/1048576).toFixed(1)} MB`:`${Math.max(1,Math.round(n/1024))} KB`;

export default function ProjectPreview({project,children}){
 const [selected,setSelected]=useState('');
 const choices=projectModelChoices(project),files=projectDownloadFiles(project);
 const model=choices.find(choice=>choice.url===selected)||choices[0];
 const current=model?.revision&&model.revision===project.current_revision;
 const dimensions=current?project.dimensions_mm:null;
 const values=Array.isArray(dimensions)?dimensions:dimensions?[dimensions.x??dimensions.width,dimensions.y??dimensions.depth,dimensions.z??dimensions.height]:[];
 return <aside className="project-preview" aria-label="项目模型与资料">
  <section className="project-model" aria-labelledby="project-model-title">
   <div className="project-panel-heading"><h2 id="project-model-title"><Box size={16}/>模型预览</h2><span>{model?.format||'待生成'}</span></div>
   {choices.length>0&&<label className="project-model-select"><select aria-label="预览模型" value={selected&&choices.some(choice=>choice.url===selected)?selected:''} onChange={event=>setSelected(event.target.value)}><option value="">自动 · {choices[0].name}</option>{choices.map(choice=><option key={choice.url} value={choice.url}>{choice.origin} · {choice.name}</option>)}</select></label>}
   <Suspense fallback={<div className="model-viewer model-loading">载入三维查看器…</div>}><ModelViewer url={model?.url} cover={model?.cover||(!model?project.cover_url:undefined)} name={model?.name||project.name} format={model?.format}/></Suspense>
   <div className="project-model-caption"><div><strong>{model?.origin||'尚无模型文件'}</strong><span>{model?.name||'在左侧描述需求，生成的模型会显示在这里。'}</span></div>{model&&<a className="text-link" href={model.url} download><Download size={15}/>下载 {model.format}</a>}</div>
   {values.length>0&&<p className="project-model-dimensions">当前版本尺寸 · {values.map(value=>typeof value==='number'&&Number.isFinite(value)?Number(value.toFixed(1)):'—').join(' × ')} mm</p>}
  </section>
  <details className="project-disclosure"><summary>全部项目文件 <span>{files.length}</span></summary>{files.length?<div className="file-list">{files.map(file=><a key={file.path} href={file.url} download className="file-row"><FileText size={16}/><div><strong>{file.name||file.path.split('/').at(-1)}</strong><span>{file.path}</span></div><small>{sizeText(file.size)}</small><Download size={15}/></a>)}</div>:<p className="muted">模型、源文件和附件会保存在这里。</p>}</details>
  {children}
 </aside>;
}
