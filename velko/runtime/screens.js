import {VelkoLiveSurfaces} from './live-surfaces.js';
/** Automatic routing of VELKO-owned applications. No desktop capture API. */
export class VelkoScreenRouter {
 route(task='') {return {kind:/discord/i.test(task)?'discord':'local',panes:['editor','terminal','application'],roles:['Code · fichiers réels','Terminal · processus réels',/discord/i.test(task)?'Discord · session VELKO':'Applications · session VELKO']};}
}
export class VelkoScreenManager {
 constructor(T,meshes,bus,{camera}={}){
  Object.assign(this,{T,meshes,bus});this.router=new VelkoScreenRouter();this.native=new VelkoLiveSurfaces(T,meshes,camera);this.preview=document.querySelector('#screen-copies');this.preview.replaceChildren();
  meshes.forEach((mesh,i)=>{mesh.material=new T.MeshBasicMaterial({color:0x080e16});const section=document.createElement('section');section.className='live-monitor';const title=document.createElement('h3');title.id='monitor-role-'+i;const frame=document.createElement('iframe');frame.className='native-preview';frame.title=['Code réel','Processus réels','Session applicative'][i];frame.src='workbench.html?pane='+['editor','terminal','application'][i]+'&embedded=1';section.append(title,frame);this.preview.append(section);this.native.connect(i,['editor','terminal','application'][i]);});
  this.routeTask('');bus.on('action.focus',a=>{this.focus=a.screen??1;this.preview.querySelectorAll('.live-monitor').forEach((node,i)=>node.classList.toggle('focused',i===this.focus));});
 }
 routeTask(task){const route=this.router.route(task);this.route=route.kind;this.roles=route.roles;this.roles.forEach((name,i)=>document.querySelector('#monitor-role-'+i).textContent=name);this.bus.emit('screens.routed',route);}
 status(){return this.roles.map((role,screen)=>({screen,role,connected:true,native:true}));}
 update(){this.native.update();}
 dispose(){this.meshes.forEach((_,i)=>this.native.disconnect(i));this.native.layer.remove();}
}
