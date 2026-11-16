export function providerModelIds(value,defaultModel=''){
 const rows=Array.isArray(value)?value:typeof value==='string'?value.split(/\r?\n/):[];
 return [...new Set([...rows,defaultModel].filter(item=>typeof item==='string').map(item=>item.trim()).filter(Boolean))];
}

export function providerDraft(provider){
 return {...(provider?.id?{id:provider.id}:{}),name:provider?.name||'',base_url:provider?.base_url||'',
  api_key:'',models_text:providerModelIds(provider?.models).join('\n'),default_model:provider?.default_model||'',enabled:provider?.enabled!==false};
}

export function providerPayload(draft,version,makeDefault=false){
 const provider={...(draft.id?{id:draft.id}:{}),name:draft.name.trim(),base_url:draft.base_url.trim(),
  models:providerModelIds(draft.models_text,draft.default_model),default_model:draft.default_model,enabled:draft.enabled};
 if(draft.api_key?.trim())provider.api_key=draft.api_key;
 return {version,provider,make_default:makeDefault};
}

export function providerTestPayload(draft,tools=false){
 const {enabled,default_model,...provider}=providerPayload(draft,0).provider;
 return {...provider,...(tools&&default_model?{default_model}:{})};
}

export function resolveAgentSelection(settings,selection={}){
 const providers=Array.isArray(settings?.providers)?settings.providers.filter(provider=>provider.enabled!==false):[];
 const provider=providers.find(item=>item.id===selection?.provider_id)||providers.find(item=>item.id===settings?.default_provider_id)||providers[0];
 if(!provider)return {provider_id:'',model:''};
 const model=provider.id===selection?.provider_id&&typeof selection?.model==='string'?selection.model:provider.default_model||'';
 return {provider_id:provider.id,model};
}

export function selectionModels(provider,rows){
 const values=Array.isArray(rows)?rows:providerModelIds(provider?.models,provider?.default_model).map(id=>({id,name:id,default:id===provider?.default_model}));
 const ids=new Set();
 return values.filter(row=>row&&typeof row.id==='string'&&row.id&&!ids.has(row.id)&&ids.add(row.id)).map(row=>({id:row.id,name:row.name||row.id,default:row.default===true}));
}

export function agentSelectionFields(selection,resume=false){
 if(resume)return {};
 return {...(selection?.provider_id?{provider_id:selection.provider_id}:{}),...(selection?.model?{model:selection.model}:{})};
}

export function agentSubmissionFingerprint(input,selection,resume=false){
 return JSON.stringify({message:input.message,attachment_ids:input.attachment_ids,file_ids:input.file_ids,...agentSelectionFields(selection,resume)});
}
