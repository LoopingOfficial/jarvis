import fs from 'node:fs';
import * as T from '../vendor/three.module.js';
import {VelkoAvatarController} from '../runtime/avatar.js';
const avatar=new VelkoAvatarController(T),names=new Map();let n=0;avatar.root.traverse(o=>names.set(o,(o.name||o.type)+'_'+n++));
const clips=[];for(const [name,context] of Object.entries({Idle:{},Listening:{},Thinking:{},Speaking:{speaking:true},Walk:{walking:true},Sit:{seated:true},Typing:{seated:true,typing:true},Mouse:{seated:true,mouse:true},Success:{speaking:true}})){
 const tracks=[];for(let f=0;f<=24;f++){avatar.update(1/12,f/12,name.toLowerCase(),context);for(const o of Object.values(avatar.joints))tracks.push({name:names.get(o),frame:f+1,position:o.position.toArray(),rotation:o.rotation.toArray().slice(0,3),scale:o.scale.toArray()});}clips.push({name,fps:12,tracks});}
fs.writeFileSync(new URL('../animations/clips.json',import.meta.url),JSON.stringify(clips));console.log('Baked',clips.length,'procedural animation clips');
