const validCount=value=>Number.isSafeInteger(value)&&value>=0;
const validCurrency=value=>typeof value==='string'&&/^[A-Z]{3}$/.test(value);
const decimal=value=>typeof value==='string'&&/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)?Number(value):null;

export function formatTokenCount(value,{compact=false}={}){
 if(!validCount(value))return '未记录';
 return new Intl.NumberFormat(compact?'en-US':'zh-CN',compact?{notation:'compact',maximumFractionDigits:1}:{}).format(value);
}

export function formatUsageCost(cost,hasTokens=false){
 const amount=decimal(cost?.amount),currency=cost?.currency;
 if(amount===null||!Number.isFinite(amount)||!validCurrency(currency)){
  if(cost?.reason==='mixed_currency'||cost?.reason==='mixed_currencies')return '币种待统一';
  if(cost?.reason==='cache_write_pricing_unknown')return '缓存写入价格待配置';
  return hasTokens?'价格待配置':'费用暂不可估算';
 }
 const positive=/[1-9]/.test(cost.amount);
 const value=positive&&amount<0.000001?'< 0.000001':new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:6}).format(amount);
 return `${cost.partial?'部分估算':'估算'} ${currency} ${value}`;
}

export function usageSummary(usage){
 const recorded=usage?.recorded_runs,unrecorded=usage?.unrecorded_runs;
 const empty=recorded===0&&unrecorded===0&&!usage?.partial;
 const known=validCount(usage?.tokens?.total_tokens);
 const partial=usage?.partial===true||(validCount(unrecorded)&&unrecorded>0);
 const tokens=empty?'尚无用量':known?`${partial?'已记录 ':''}${formatTokenCount(usage.tokens.total_tokens,{compact:true})} Token`:'Token 未记录';
 const cost=empty?'尚无费用估算':formatUsageCost(usage?.cost,known);
 const coverage=empty?'尚无 Codex 任务':validCount(unrecorded)&&unrecorded>0?`${unrecorded} 个任务未记录用量`:partial?'部分用量未记录':validCount(recorded)?`已记录 ${recorded} 个任务`:'用量记录暂不可用';
 const warning=validCount(unrecorded)&&unrecorded>0?'部分任务未记录':partial?'部分用量未记录':'';
 return {tokens,cost,coverage,empty,text:[tokens,warning,cost].filter(Boolean).join(' · ')};
}

export function usageTokenRows(tokens){
 return [['输入','input_tokens'],['其中缓存命中','cached_input_tokens'],['其中缓存写入','cache_write_input_tokens'],['输出','output_tokens'],['其中推理','reasoning_output_tokens'],['合计','total_tokens']].map(([label,key])=>[label,formatTokenCount(tokens?.[key])]);
}

export function runUsageLabel(usage){
 if(!validCount(usage?.response_count)||usage.response_count===0||!validCount(usage?.total_tokens))return 'Token 未记录';
 const partial=validCount(usage.missing_responses)&&usage.missing_responses>0;
 return `${partial?'已记录 ':''}${formatTokenCount(usage.total_tokens)} Token${partial?' · 部分用量未记录':''}`;
}

export function pricingDetails(pricing){
 let url=null;
 try{const parsed=new URL(pricing?.source_url);if(parsed.protocol==='https:'&&!parsed.username&&!parsed.password)url=parsed.href}catch{}
 const rate=value=>{
  const number=typeof value==='number'?value:decimal(value);
  return number!==null&&Number.isFinite(number)&&number>=0&&validCurrency(pricing?.currency)?`${pricing.currency} ${value} / 百万 Token`:'未配置';
 };
 return {label:!pricing?'价格待配置':pricing.source==='official_reference'?'官方公开参考价':pricing.source==='configured'?'自定义单价':'价格来源待核对',url,
  asOf:typeof pricing?.as_of==='string'?pricing.as_of:'',
  rates:[['输入',rate(pricing?.input_per_million)],['缓存命中',rate(pricing?.cached_input_per_million)],['输出',rate(pricing?.output_per_million)]]};
}
