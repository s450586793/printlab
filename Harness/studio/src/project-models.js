const formats=['glb','stl','3mf'];
const revisionOf=path=>path.match(/^(?:Exports|Design|Preview|Print)\/(v\d+)(?:\/|$)/i)?.[1]||null;
const extension=path=>path.split(/[?#]/)[0].split('.').at(-1).toLowerCase();

export function projectDownloadFiles(project){
 return (Array.isArray(project?.files)?project.files:[]).filter(file=>
  typeof file?.path==='string'&&typeof file.url==='string'&&file.url&&
  !/(?:^|\/)(Sessions|Review)(?:\/|$)/.test(file.path)&&!file.path.endsWith('.lock'));
}

export function projectModelChoices(project){
 if(!project)return [];
 const files=projectDownloadFiles(project),current=project.current_revision||null;
 const models=files.filter(file=>formats.includes(extension(file.path)));
 if(project.model_url&&!models.some(file=>file.url===project.model_url)){
  models.push({url:project.model_url,path:project.model_url,name:project.model_url.split('/').at(-1)});
 }
 const seen=new Set();
 return models.filter(file=>{if(seen.has(file.url))return false;seen.add(file.url);return true}).map(file=>{
  const isCurrent=file.url===project.model_url,revision=isCurrent?current:revisionOf(file.path);
  const cover=revision&&revision===current?project.cover_url:files.find(candidate=>
   revisionOf(candidate.path)===revision&&revision&&/(?:^|\/)Preview\//.test(candidate.path)&&/\/cover\.(png|jpe?g|webp)$/i.test(candidate.path))?.url;
  return {...file,name:file.name||file.path.split('/').at(-1),revision,cover,format:extension(file.path).toUpperCase(),
   origin:revision?`${revision===current?'当前版本':'历史版本'} ${revision}`:'项目文件'};
 }).sort((a,b)=>{
  const priority=model=>model.url===project.model_url?0:model.revision===current&&current?1:model.revision?2:3;
  const overall=model=>/(?:^|[_\- .])(model|assembly|station|overall)(?:[_\- .]|$)/i.test(model.name)?0:1;
  return priority(a)-priority(b)||(Number(b.revision?.slice(1))||0)-(Number(a.revision?.slice(1))||0)||
   formats.indexOf(a.format.toLowerCase())-formats.indexOf(b.format.toLowerCase())||overall(a)-overall(b)||a.path.localeCompare(b.path,undefined,{numeric:true});
 });
}
