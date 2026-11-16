import React, {useEffect, useRef, useState} from 'react';
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {STLLoader} from 'three/addons/loaders/STLLoader.js';
import {ThreeMFLoader} from 'three/addons/loaders/3MFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RotateCcw, Move, Box, LoaderCircle} from 'lucide-react';
import {modelFrameDistance} from './model-framing';
import {agentRead} from './agent-client';

export default function ModelViewer({url,cover,name,format,interactive=true}){
 const host=useRef(null),reset=useRef(null);const [state,setState]=useState('loading');
 useEffect(()=>{
  if(!url){setState('empty');return;}
  const el=host.current;if(!el)return;let renderer,scene,camera,controls,frame,disposed=false,modelRadius=0,modelScale=0,fittedDistance=0;const objects=[];setState('loading');
  try{
   renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.setClearColor(0,0);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
   renderer.domElement.setAttribute('aria-label',`${name} 三维模型，可拖动或用方向键旋转，用滚轮或加减键缩放`);renderer.domElement.setAttribute('role','img');renderer.domElement.tabIndex=interactive?0:-1;el.appendChild(renderer.domElement);
   scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(35,1,0.001,1000);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.08;controls.enablePan=false;controls.enabled=interactive;controls.minPolarAngle=.1;controls.maxPolarAngle=Math.PI*.88;const renderHidden=()=>{if(document.hidden)renderer.render(scene,camera)};controls.addEventListener('change',renderHidden);
   scene.add(new THREE.HemisphereLight(0xffffff,0x8b9188,2.8));const key=new THREE.DirectionalLight(0xffffff,4);key.position.set(3,5,4);scene.add(key);const fill=new THREE.DirectionalLight(0xe7eddf,2);fill.position.set(-3,1,-2);scene.add(fill);
   const keyboard=e=>{if(!interactive||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','-','='].includes(e.key))return;e.preventDefault();const offset=camera.position.clone().sub(controls.target),spherical=new THREE.Spherical().setFromVector3(offset);if(e.key==='ArrowLeft')spherical.theta-=.12;if(e.key==='ArrowRight')spherical.theta+=.12;if(e.key==='ArrowUp')spherical.phi=Math.max(.1,spherical.phi-.12);if(e.key==='ArrowDown')spherical.phi=Math.min(Math.PI*.88,spherical.phi+.12);if(e.key==='+'||e.key==='=')spherical.radius=Math.max(controls.minDistance,spherical.radius*.9);if(e.key==='-')spherical.radius=Math.min(controls.maxDistance,spherical.radius*1.1);camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical));controls.update()};renderer.domElement.addEventListener('keydown',keyboard);
   const fit=(resetView=false)=>{if(!modelRadius)return;const distance=modelFrameDistance(modelRadius,camera.fov,camera.aspect),offset=camera.position.clone().sub(controls.target),zoom=!resetView&&fittedDistance?offset.length()/fittedDistance:1,direction=resetView||offset.lengthSq()===0?new THREE.Vector3(1.5,1.1,1.8).normalize():offset.normalize();fittedDistance=distance;camera.near=modelScale/100;camera.far=Math.max(modelScale*100,distance*10);controls.minDistance=modelScale*.8;controls.maxDistance=Math.max(modelScale*8,distance*4);camera.position.copy(controls.target).add(direction.multiplyScalar(distance*zoom));camera.updateProjectionMatrix();controls.update()};
   const resize=()=>{if(disposed)return;const w=el.clientWidth,h=el.clientHeight;if(!w||!h)return;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();fit()};const observer=new ResizeObserver(resize);observer.observe(el);resize();
   const type=(format||url.split(/[?#]/)[0].split('.').at(-1)).toUpperCase();
   const loader=type==='STL'?new STLLoader():type==='3MF'?new ThreeMFLoader():new GLTFLoader();
   const load=()=>loader.load(url,(asset)=>{
    const model=type==='STL'?new THREE.Mesh(asset,new THREE.MeshStandardMaterial({color:0xa1b5a0,roughness:.65,metalness:.05,side:THREE.DoubleSide})):type==='3MF'?asset:asset.scene;
    if(type==='STL'||type==='3MF')model.rotation.x=-Math.PI/2;
    if(disposed){model.traverse(o=>{o.geometry?.dispose();if(o.material)(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>{Object.values(m).forEach(v=>v?.isTexture&&v.dispose());m.dispose()})});return;}
    objects.push(model);const box=new THREE.Box3().setFromObject(model);if(box.isEmpty()){setState('error');return;}const size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());modelScale=Math.max(size.x,size.y,size.z,.001);modelRadius=Math.max(box.getBoundingSphere(new THREE.Sphere()).radius,modelScale/2);if(!Number.isFinite(modelScale)||!Number.isFinite(modelRadius)){setState('error');return;}model.position.sub(center);scene.add(model);
    reset.current=()=>{controls.target.set(0,0,0);fit(true)};reset.current();renderer.render(scene,camera);setState('ready');
   },undefined,()=>{if(!disposed)setState('error')});
   // 附件下载依赖工作台会话，首次进入时需等 Cookie 就绪。
   if(url.startsWith('/api/files/')||url.startsWith('/api/projects/')){
    agentRead('/api/prepared/session').then(()=>{if(!disposed)load()}).catch(()=>{if(!disposed)setState('error')});
   }else load();
   const tick=()=>{if(disposed)return;controls.update();renderer.render(scene,camera);frame=requestAnimationFrame(tick)};tick();
   return()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();renderer.domElement.removeEventListener('keydown',keyboard);controls.removeEventListener('change',renderHidden);controls.dispose();objects.forEach(obj=>obj.traverse(o=>{o.geometry?.dispose();if(o.material)(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>{Object.values(m).forEach(v=>v?.isTexture&&v.dispose());m.dispose()})}));renderer.dispose();renderer.domElement.remove();reset.current=null;};
  }catch{setState('error');renderer?.dispose();}
 },[url,interactive,name,format]);
 return <div className={`model-viewer model-${state}`}>
   <div ref={host} className="model-canvas"/>
   {(state==='empty'||state==='error')&&(cover?<img className="model-fallback" src={cover} alt={`${name} 模型预览`}/>:<div className="model-empty"><Box size={28} strokeWidth={1}/><span>{state==='error'?'三维预览暂不可用':'模型预览待生成'}</span></div>)}
   {state==='loading'&&<div className="model-loading"><LoaderCircle className="spin" size={22}/><span>载入模型</span></div>}
   {state==='ready'&&interactive&&<><div className="model-hint"><Move size={14}/> 拖动旋转 · 滚动缩放</div><button className="icon-button model-reset" onClick={()=>reset.current?.()} aria-label="重置模型视角" title="重置视角"><RotateCcw size={17}/></button></>}
   {state==='error'&&cover&&<span className="model-hint">静态预览 · 三维模型加载失败</span>}
 </div>
}
