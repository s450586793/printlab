import React from 'react';
import {formatUsageCost,usageSummary,usageTokenRows,pricingDetails,runUsageLabel} from './agent-usage';
import './agent-usage.css';

export function UsageLabel({usage}){
 const summary=usageSummary(usage);
 return <span className="agent-usage-label" title={summary.text}>{summary.text}</span>;
}

function TokenRows({tokens}){
 return <dl className="agent-usage-numbers">{usageTokenRows(tokens).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export default function AgentUsage({usage,run}){
 const summary=usageSummary(usage),models=Array.isArray(usage?.models)?usage.models:[];
 return <details className="agent-usage" aria-label="项目 Token 与费用明细">
  <summary><UsageLabel usage={usage}/></summary>
  <div className="agent-usage-popover">
   <div className="agent-usage-title"><strong>项目 Token 与费用</strong><span>{summary.coverage}</span></div>
   <TokenRows tokens={usage?.tokens}/>
   <p className="agent-usage-cost">{summary.cost}</p>
   {run&&<p className="agent-usage-current">本轮 {runUsageLabel(run.usage)}{run.provider_name?` · ${run.provider_name}`:''}{run.effective_model?` · ${run.effective_model}`:''}</p>}
   <p className="agent-usage-note">缓存命中和缓存写入包含在输入中，推理包含在输出中。未记录的用量不计作 0。</p>
   {models.map((model,index)=>{
    const pricing=pricingDetails(model.pricing);
    return <section className="agent-usage-model" key={`${model.model}-${model.service_tier||''}-${index}`}>
     <div className="agent-usage-model-heading"><strong>{model.provider_name?`${model.provider_name} · `:''}{model.model||'模型待识别'}{model.service_tier?` · ${model.service_tier}`:''}</strong><span>{formatUsageCost(model.cost,Number.isSafeInteger(model.tokens?.total_tokens))}</span></div>
     <TokenRows tokens={model.tokens}/>
     <div className="agent-usage-pricing"><span>{pricing.label}{pricing.asOf?` · ${pricing.asOf}`:''}</span>{pricing.url&&<a href={pricing.url} target="_blank" rel="noreferrer">价格来源</a>}</div>
     {model.pricing&&<dl className="agent-usage-rates">{pricing.rates.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
    </section>;
   })}
   <p className="agent-usage-note">金额按已配置单价或适用的公开参考价估算，实际计费以对应服务记录为准。</p>
  </div>
 </details>;
}
