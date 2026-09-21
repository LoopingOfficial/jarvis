import fs from 'node:fs';
import * as THREE from '../vendor/three.module.js';
import {VelkoAvatarController} from '../runtime/avatar.js';
import {VelkoEnvironmentController} from '../runtime/environment.js';
const context=new Proxy({createLinearGradient:()=>({addColorStop(){}})}, {get:(t,p)=>p in t?t[p]:()=>{}});
globalThis.document={createElement:()=>({getContext:()=>context})};
THREE.TextureLoader.prototype.load=function(path,cb){const t=new THREE.Texture();t.userData.path=path;cb?.(t);return t};
const avatar=new VelkoAvatarController(THREE), scene=new THREE.Scene(), office=new VelkoEnvironmentController(THREE,scene);
function manifest(root){
 const objects=[], names=new Map();let n=0;
 root.traverse(o=>names.set(o,(o.name||o.type)+'_'+n++));
 function record(o,matrix,name,parent){
  const d={name,parent,type:o.isMesh?'mesh':'empty',matrix:matrix.toArray()};
  if(o.isMesh){const g=o.geometry,m=Array.isArray(o.material)?o.material[0]:o.material;d.positions=Array.from(g.attributes.position.array);d.indices=g.index?Array.from(g.index.array):null;if(g.attributes.uv)d.uv=Array.from(g.attributes.uv.array);d.material={color:m.color?.toArray()||[.3,.3,.3],roughness:m.roughness??.6,metalness:m.metalness??0,emissive:m.emissive?.toArray()||[0,0,0],emissiveIntensity:m.emissiveIntensity??1,opacity:m.opacity??1,texture:m.map?.userData?.path};}
  objects.push(d);
 }
 root.updateMatrixWorld(true);
 root.traverse(o=>{o.updateMatrix(); if(o.isInstancedMesh){const mat=new THREE.Matrix4();for(let i=0;i<o.count;i++){o.getMatrixAt(i,mat);record(o,new THREE.Matrix4().multiplyMatrices(o.matrix,mat),names.get(o)+'_instance_'+i,names.get(o.parent)||null);}}else record(o,o.matrix,names.get(o),names.get(o.parent)||null)});
 return {objects};
}
const result={avatar:manifest(avatar.root),office:manifest(office.group)};
fs.writeFileSync(new URL('../exports/scene.json',import.meta.url),JSON.stringify(result));
console.log('Scene manifest:',result.avatar.objects.length,'avatar objects,',result.office.objects.length,'office objects');
