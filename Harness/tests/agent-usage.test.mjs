import test from 'node:test';
import assert from 'node:assert/strict';
import {formatTokenCount,formatUsageCost,usageSummary,usageTokenRows,pricingDetails,runUsageLabel} from '../studio/src/agent-usage.js';

const tokens={input_tokens:12000,cached_input_tokens:2000,cache_write_input_tokens:0,output_tokens:3400,reasoning_output_tokens:400,total_tokens:15400};
const usage={tokens,recorded_runs:2,unrecorded_runs:0,partial:false,cost:{amount:'0.0482',currency:'USD',basis:'official_reference',partial:false},models:[]};

test('token formatting preserves zero and rejects missing or invalid counts',()=>{
 assert.equal(formatTokenCount(0),'0');
 assert.equal(formatTokenCount(15400),'15,400');
 assert.equal(formatTokenCount(15400,{compact:true}),'15.4K');
 for(const value of [null,undefined,-1,NaN,Infinity,'1200',1.5,Number.MAX_SAFE_INTEGER+1])assert.equal(formatTokenCount(value),'未记录');
});

test('missing statistics and old unrecorded tasks never become zero consumption',()=>{
 assert.match(usageSummary(null).text,/Token 未记录/);
 assert.doesNotMatch(usageSummary(null).text,/0 Token|0\.00/);
 const old=usageSummary({...usage,tokens:null,recorded_runs:0,unrecorded_runs:3,partial:true,cost:{amount:null,currency:null}});
 assert.equal(old.tokens,'Token 未记录');
 assert.match(old.coverage,/3 个任务未记录/);
 assert.equal(old.cost,'费用暂不可估算');
});

test('a project with no tasks is distinct from tasks with missing usage',()=>{
 const empty=usageSummary({tokens:null,recorded_runs:0,unrecorded_runs:0,partial:false,cost:{amount:null}});
 assert.equal(empty.tokens,'尚无用量');
 assert.equal(empty.coverage,'尚无 Codex 任务');
 assert.equal(empty.cost,'尚无费用估算');
 assert.equal(usageSummary({tokens:null,recorded_runs:0,unrecorded_runs:0,partial:true}).tokens,'Token 未记录');
 assert.equal(usageSummary({tokens:null,recorded_runs:null,unrecorded_runs:null,partial:false}).tokens,'Token 未记录');
});

test('partial usage states its coverage in the compact project summary',()=>{
 const result=usageSummary({...usage,unrecorded_runs:2,partial:true,cost:{...usage.cost,partial:true}});
 assert.equal(result.tokens,'已记录 15.4K Token');
 assert.match(result.text,/部分任务未记录/);
 assert.match(result.coverage,/2 个任务未记录/);
 assert.match(result.cost,/部分估算 USD/);
});

test('cost labels carry currency and estimate wording without assuming a currency',()=>{
 assert.equal(formatUsageCost(usage.cost,true),'估算 USD 0.0482');
 assert.equal(formatUsageCost({amount:'12.50',currency:'CNY',basis:'configured'},true),'估算 CNY 12.50');
 assert.equal(formatUsageCost({amount:'0',currency:'USD'},true),'估算 USD 0.00');
 assert.equal(formatUsageCost({amount:null,currency:null},true),'价格待配置');
 assert.equal(formatUsageCost({amount:'1.2',currency:null},true),'价格待配置');
 assert.equal(formatUsageCost({amount:null,reason:'mixed_currency'},true),'币种待统一');
 assert.equal(formatUsageCost({amount:null,reason:'cache_write_pricing_unknown'},true),'缓存写入价格待配置');
});

test('small positive amounts are never rounded into zero costs',()=>{
 assert.equal(formatUsageCost({amount:'0.00000001',currency:'USD'},true),'估算 USD < 0.000001');
 for(const amount of ['-1','NaN','Infinity','',null,{},'1e3'])assert.equal(formatUsageCost({amount,currency:'USD'},true),'价格待配置');
});

test('breakdown uses the supplied total and labels cached and reasoning tokens as subsets',()=>{
 const rows=usageTokenRows(tokens);
 assert.deepEqual(rows,[['输入','12,000'],['其中缓存命中','2,000'],['其中缓存写入','0'],['输出','3,400'],['其中推理','400'],['合计','15,400']]);
 assert.equal(usageTokenRows(null).at(-1)[1],'未记录');
 assert.equal(usageSummary(usage).tokens,'15.4K Token');
});

test('reference pricing exposes its basis, rates and a safe source link',()=>{
 const result=pricingDetails({currency:'USD',input_per_million:'2',cached_input_per_million:'0.1',output_per_million:'10',source:'official_reference',source_url:'https://openai.com/api/pricing/',as_of:'2026-11-15'});
 assert.equal(result.label,'官方公开参考价');
 assert.equal(result.url,'https://openai.com/api/pricing/');
 assert.equal(result.asOf,'2026-11-15');
 assert.deepEqual(result.rates,[['输入','USD 2 / 百万 Token'],['缓存命中','USD 0.1 / 百万 Token'],['输出','USD 10 / 百万 Token']]);
});

test('configured, missing and unsafe pricing remain explicit',()=>{
 assert.equal(pricingDetails(null).label,'价格待配置');
 assert.equal(pricingDetails({source:'configured'}).label,'自定义单价');
 assert.equal(pricingDetails({source:'official_reference',source_url:'javascript:alert(1)'}).url,null);
 assert.equal(pricingDetails({source_url:'https://secret@example.com/pricing'}).url,null);
 assert.equal(pricingDetails({currency:'USD',input_per_million:-1}).rates[0][1],'未配置');
});

test('a run without a recorded response cannot turn missing usage into zero tokens',()=>{
 for(const value of [null,{total_tokens:0},{response_count:0,total_tokens:0},{response_count:0,missing_responses:1,total_tokens:0}])assert.equal(runUsageLabel(value),'Token 未记录');
 assert.equal(runUsageLabel({response_count:1,missing_responses:0,total_tokens:0}),'0 Token');
 assert.equal(runUsageLabel({response_count:1,missing_responses:0,total_tokens:1234}),'1,234 Token');
 assert.equal(runUsageLabel({response_count:1,missing_responses:1,total_tokens:1234}),'已记录 1,234 Token · 部分用量未记录');
});
