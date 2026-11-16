import test from 'node:test';
import assert from 'node:assert/strict';
import {providerDraft,providerPayload,providerTestPayload,providerModelIds,resolveAgentSelection,selectionModels,agentSelectionFields,agentSubmissionFingerprint} from '../studio/src/model-providers.js';
import {createRequestGate} from '../studio/src/presentation.js';

const settings={default_provider_id:'gateway',providers:[
 {id:'gateway',name:'Gateway',enabled:true,models:['alpha','beta'],default_model:'alpha'},
 {id:'local',name:'Local',enabled:true,models:['alpha','local/model'],default_model:'local/model'},
 {id:'disabled',enabled:false,models:['old'],default_model:'old'},
]};

test('editing provider metadata starts with an empty key and preserves the server object',()=>{
 const provider={...settings.providers[0],base_url:'https://example.invalid/v1',api_key:'must-not-copy',key_configured:true};
 const draft=providerDraft(provider);
 assert.equal(draft.api_key,'');assert.equal(draft.models_text,'alpha\nbeta');
 draft.name='Changed';assert.equal(provider.name,'Gateway');
 assert.deepEqual(providerDraft(),{name:'',base_url:'',api_key:'',models_text:'',default_model:'',enabled:true});
});

test('model lists use nonempty unique IDs and include an explicit default',()=>{
 assert.deepEqual(providerModelIds(' alpha \n\nbeta\nalpha\n local/model '),['alpha','beta','local/model']);
 assert.deepEqual(providerModelIds(['alpha','alpha','beta'], 'other'),['alpha','beta','other']);
 assert.deepEqual(providerModelIds(null),[]);
});

test('saving omits a blank key, retains a supplied key only in the write payload and never mutates drafts',()=>{
 const draft={...providerDraft(settings.providers[0]),api_key:'',base_url:' https://example.invalid/v1 ',default_model:'extra'};
 const payload=providerPayload(draft,4,true);
 assert.equal(payload.version,4);assert.equal(payload.make_default,true);assert.equal(payload.provider.base_url,'https://example.invalid/v1');
 assert.deepEqual(payload.provider.models,['alpha','beta','extra']);assert.equal('api_key' in payload.provider,false);
 assert.equal('api_key' in providerPayload({...draft,api_key:'   '},4).provider,false);
 assert.equal(providerPayload({...draft,api_key:'draft-only-key'},4).provider.api_key,'draft-only-key');
 assert.equal(draft.models_text,'alpha\nbeta');
});

test('listing models omits the selected model while tool checks include it',()=>{
 const draft={...providerDraft(settings.providers[0]),base_url:'https://example.invalid/v1',api_key:'temporary-key'};
 const listing=providerTestPayload(draft,false),tools=providerTestPayload(draft,true);
 assert.equal('default_model' in listing,false);assert.equal(tools.default_model,'alpha');
 assert.equal(listing.api_key,'temporary-key');assert.equal('enabled' in listing,false);assert.equal('version' in listing,false);
});

test('project selections restore an enabled provider and keep identical model names isolated',()=>{
 assert.deepEqual(resolveAgentSelection(settings,{provider_id:'local',model:'alpha'}),{provider_id:'local',model:'alpha'});
 assert.deepEqual(resolveAgentSelection(settings,{provider_id:'disabled',model:'old'}),{provider_id:'gateway',model:'alpha'});
 assert.deepEqual(resolveAgentSelection(settings,{provider_id:'missing',model:'old'}),{provider_id:'gateway',model:'alpha'});
 assert.deepEqual(resolveAgentSelection({...settings,default_provider_id:'missing'},{}),{provider_id:'gateway',model:'alpha'});
 assert.deepEqual(resolveAgentSelection({providers:[]},{}),{provider_id:'',model:''});
});

test('runtime model options preserve configured fallback and reject stale selections',()=>{
 const provider=settings.providers[0];
 assert.deepEqual(selectionModels(provider,null),[{id:'alpha',name:'alpha',default:true},{id:'beta',name:'beta',default:false}]);
 assert.deepEqual(selectionModels(provider,[{id:'beta',name:'Beta',default:true},{id:'beta'},{id:''},null]),[{id:'beta',name:'Beta',default:true}]);
 assert.deepEqual(agentSelectionFields({provider_id:'local',model:'alpha'}),{provider_id:'local',model:'alpha'});
 assert.deepEqual(agentSelectionFields({provider_id:'legacy',model:''}),{provider_id:'legacy'});
 assert.deepEqual(agentSelectionFields({provider_id:'local',model:'alpha'},true),{});
});

test('retry fingerprints include provider and model while resumes ignore the current selector',()=>{
 const input={message:'Build',attachment_ids:['image'],file_ids:['file']};
 const first=agentSubmissionFingerprint(input,{provider_id:'gateway',model:'alpha'});
 assert.notEqual(first,agentSubmissionFingerprint(input,{provider_id:'local',model:'alpha'}));
 assert.notEqual(first,agentSubmissionFingerprint(input,{provider_id:'gateway',model:'beta'}));
 assert.equal(agentSubmissionFingerprint(input,{provider_id:'gateway',model:'alpha'},true),agentSubmissionFingerprint(input,{provider_id:'local',model:'beta'},true));
 assert.equal(agentSubmissionFingerprint(input,{provider_id:'gateway',model:'alpha',api_key:'secret'}).includes('secret'),false);
});

test('superseded reads and cancelled drafts cannot apply a late settings response',()=>{
 const gate=createRequestGate(),first=gate.begin(),second=gate.begin();
 assert.equal(first.signal.aborted,true);assert.equal(first.isCurrent(),false);assert.equal(second.isCurrent(),true);
 gate.cancel();assert.equal(second.isCurrent(),false);
});
