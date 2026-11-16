import test from 'node:test';
import assert from 'node:assert/strict';
import {projectDownloadFiles,projectModelChoices} from '../studio/src/project-models.js';

const file=path=>({path,name:path.split('/').at(-1),url:'/api/files/P003/'+path,size:123});

test('unregistered attachment models are visible without being promoted to a formal version',()=>{
 const project={files:['Sources/attachments/id/simplified_cutaway.glb','Sources/attachments/id/simplified_station.glb','Sources/attachments/id/frame.stl','Sources/attachments/id/station.3mf','Sources/attachments/id/v99/part.glb','Sources/reference-images/photo.png'].map(file)};
 const choices=projectModelChoices(project);
 assert.equal(choices.length,5);
 assert.equal(choices[0].name,'simplified_station.glb');
 assert.ok(choices.every(choice=>choice.revision===null&&choice.origin==='项目文件'));
 assert.equal(choices.find(choice=>choice.name==='frame.stl').url,'/api/files/P003/Sources/attachments/id/frame.stl');
 assert.equal(project.current_revision,undefined);
});

test('current model wins, historical versions sort numerically, and covers stay with their version',()=>{
 const current=file('Exports/v12/model.glb');
 const project={current_revision:'v12',model_url:current.url,cover_url:'/current.png',files:[file('Exports/v2/model.glb'),file('Preview/v2/cover.png'),file('Exports/v10/model.glb'),file('Preview/v10/cover.png'),current]};
 const choices=projectModelChoices(project);
 assert.deepEqual(choices.map(choice=>choice.revision),['v12','v10','v2']);
 assert.equal(choices[0].cover,'/current.png');
 assert.equal(choices[1].cover,'/api/files/P003/Preview/v10/cover.png');
 assert.equal(choices[2].origin,'历史版本 v2');
 assert.equal(choices[2].cover,'/api/files/P003/Preview/v2/cover.png');
});

test('partial records, missing current file and uppercase formats degrade without losing downloads',()=>{
 assert.deepEqual(projectModelChoices(null),[]);assert.deepEqual(projectDownloadFiles(null),[]);
 const project={current_revision:'v1',model_url:'/current.glb',files:[null,{},file('Sources/模型.STL'),file('Sources/design.step'),file('Sources/project.zip'),file('Sources/notes.txt'),file('Sessions/handoff.md'),file('Review/report.md'),file('project.lock')]};
 assert.equal(projectModelChoices(project)[0].url,'/current.glb');
 assert.equal(projectModelChoices(project)[1].format,'STL');
 assert.deepEqual(projectDownloadFiles(project).map(f=>f.name),['模型.STL','design.step','project.zip','notes.txt']);
});
