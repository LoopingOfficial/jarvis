import {VelkoLiveSurfaces} from './live-surfaces.js';
import {VelkoScreenRouter, PANEL_LABELS} from './screen-router.js';
/** Les trois moniteurs de VELKO.
 *
 *  Aucune API de capture d'écran, aucun sélecteur de fenêtre : chaque moniteur
 *  projette une surface réelle appartenant à VELKO (`workbench.html`), et c'est
 *  le routeur — nourri par les événements réels du moteur — qui décide de son
 *  rôle. L'utilisateur ne choisit jamais ce qui s'affiche. */
export {VelkoScreenRouter};
const PANES=['editor','terminal','application'];
export class VelkoScreenManager {
 constructor(T,meshes,bus,{camera}={}){
  Object.assign(this,{T,meshes,bus});
  this.router=new VelkoScreenRouter();
  this.native=new VelkoLiveSurfaces(T,meshes,camera);
  this.preview=document.querySelector('#screen-copies');this.preview.replaceChildren();
  meshes.forEach((mesh,i)=>{
   mesh.material=new T.MeshBasicMaterial({color:0x080e16});
   const section=document.createElement('section');section.className='live-monitor';
   const title=document.createElement('h3');title.id='monitor-role-'+i;
   const frame=document.createElement('iframe');frame.className='native-preview';
   frame.title=['Code réel','Processus réels','Outil actif réel'][i];
   frame.src='workbench.html?pane='+PANES[i]+'&embedded=1';
   section.append(title,frame);this.preview.append(section);
   this.native.connect(i,PANES[i]);
  });
  this.applyRoles();
  // Le rôle du troisième écran suit les faits du moteur, pas un choix humain.
  bus.on('engine.event',({type,data})=>{const before=this.router.panels[2];
   this.router.ingest(type,data);if(this.router.panels[2]!==before)this.applyRoles();});
  bus.on('action.focus',a=>{this.focus=a.screen??1;
   this.preview.querySelectorAll('.live-monitor').forEach((node,i)=>node.classList.toggle('focused',i===this.focus));});
 }
 applyRoles(){
  this.roles=this.router.state().labels;
  this.roles.forEach((name,i)=>{const node=document.querySelector('#monitor-role-'+i);if(node)node.textContent=name;});
  this.bus.emit('screens.routed',this.router.state());
 }
 /** Conservé pour la compatibilité : le routage ne dépend plus du texte de la
  *  demande mais des événements réels. */
 routeTask(){this.applyRoles();}
 status(){return (this.roles||[]).map((role,screen)=>({screen,role,connected:true,native:true}));}
 update(){this.native.update();}
 dispose(){this.meshes.forEach((_,i)=>this.native.disconnect(i));this.native.layer.remove();}
}
