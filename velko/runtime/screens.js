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
const REPORT_CSS=`#velko-report{position:fixed;inset:0;z-index:9999;background:#07111b;color:#d6e4f1;font:24px/1.45 Inter,'Segoe UI',sans-serif;padding:48px 64px;box-sizing:border-box;overflow:hidden;display:flex;flex-direction:column}
#velko-report[hidden]{display:none}
#velko-report header{display:flex;align-items:baseline;gap:32px;border-bottom:2px solid #1f3a55;padding-bottom:22px;margin-bottom:26px}
#velko-report header span{font-size:18px;letter-spacing:5px;color:#5fb4f0}#velko-report header b{font-size:40px;font-weight:600;color:#f2f7fc}
#velko-report .vr-body{flex:1;overflow:hidden}#velko-report .rp-summary{font-size:26px;color:#9fb6ca;margin:0 0 22px}
#velko-report .vr-pair{display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:start}
#velko-report table{width:100%;border-collapse:collapse;font-size:22px;table-layout:fixed}
#velko-report th{text-align:left;font-size:15px;letter-spacing:2px;text-transform:uppercase;color:#6f96b6;font-weight:500;padding:8px 10px;border-bottom:2px solid #24405c}
#velko-report td{padding:7px 10px;border-bottom:1px solid #ffffff12;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#velko-report th:nth-child(1){width:46%}#velko-report th:nth-child(3){width:13%}#velko-report th:nth-child(4){width:14%}
#velko-report td:nth-child(3),#velko-report td:nth-child(4),#velko-report th:nth-child(3),#velko-report th:nth-child(4){text-align:right;color:#8fa6ba}
#velko-report .rp-n8n-name{color:#f0f6fc;font-weight:500}#velko-report .rp-n8n-name i{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:14px;vertical-align:middle}
#velko-report i.on{background:#78dfbb;box-shadow:0 0 10px #60d6b0}#velko-report i.off{background:#5c6b78}
#velko-report .rp-n8n-foot{font-size:18px;color:#6f889c;margin-top:18px}
#velko-report h2,#velko-report h3{color:#f0f6ff;font-size:30px;margin:18px 0 10px}#velko-report p{margin:8px 0}
#velko-report ul{list-style:none;padding:0;columns:2;column-gap:56px}#velko-report li{padding:6px 0;border-bottom:1px solid #ffffff10;break-inside:avoid}
#velko-report code,#velko-report pre{font-family:Consolas,monospace;color:#8fd4c8}`;
export class VelkoScreenManager {
 constructor(T,meshes,bus,{camera}={}){
  Object.assign(this,{T,meshes,bus});
  this.router=new VelkoScreenRouter();
  this.native=new VelkoLiveSurfaces(T,meshes,camera);
  this.preview=document.querySelector('#screen-copies');this.preview.replaceChildren();
  meshes.forEach((mesh,i)=>{
   // Trou dans le canevas : l'écran écrit alpha 0 AVEC profondeur. L'iframe,
   // placée sous le canevas, n'apparaît que là où rien de plus proche (le
   // visage de VELKO, par exemple) ne la recouvre.
   mesh.material=new T.MeshBasicMaterial({color:0x000000,opacity:0,blending:T.NoBlending,toneMapped:false});
   const section=document.createElement('section');section.className='live-monitor';
   const title=document.createElement('h3');title.id='monitor-role-'+i;
   const frame=document.createElement('iframe');frame.className='native-preview';
   frame.title=['Code réel','Processus réels','Outil actif réel'][i];
   frame.src='workbench.html?pane='+PANES[i]+'&embedded=1';
   section.append(title,frame);this.preview.append(section);
   this.native.connect(i,PANES[i]);
  });
  this.applyRoles();
  // Le rôle des écrans suit les faits du moteur, pas un choix humain.
  bus.on('engine.event',({type,data})=>{
   const before=this.router.state().labels;
   this.router.ingest(type,data);
   const after=this.router.state().labels;
   if(before.join('|')!==after.join('|'))this.applyRoles();
  });
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
 /** Affiche un rapport sur le moniteur central (surface 1920x1080 réelle).
  *  Les longs tableaux sont répartis sur deux colonnes pour rester lisibles. */
 showReport(html,title=''){
  const doc=this.native.entries[1]?.frame?.contentDocument;if(!doc?.body)return false;
  let host=doc.getElementById('velko-report');
  if(!host){
   const style=doc.createElement('style');style.id='velko-report-style';style.textContent=REPORT_CSS;doc.head.append(style);
   host=doc.createElement('section');host.id='velko-report';doc.body.append(host);
  }
  host.innerHTML=`<header><span>RAPPORT COMPLET</span><b></b></header><div class="vr-body">${html}</div>`;
  host.querySelector('header b').textContent=title;
  host.querySelector('.vr-body > .rp-title')?.remove();   // déjà dans l'en-tête
  for(const table of host.querySelectorAll('table')){
   const rows=[...table.tBodies[0]?.rows||[]];if(rows.length<=14)continue;
   const half=Math.ceil(rows.length/2),twin=table.cloneNode(false);
   twin.append(table.tHead.cloneNode(true),doc.createElement('tbody'));
   rows.slice(half).forEach(r=>twin.tBodies[0].append(r));
   const pair=doc.createElement('div');pair.className='vr-pair';table.replaceWith(pair);pair.append(table,twin);
  }
  host.hidden=false;return true;
 }
 clearReport(){const host=this.native.entries[1]?.frame?.contentDocument?.getElementById('velko-report');if(host)host.hidden=true;}
 status(){return (this.roles||[]).map((role,screen)=>({screen,role,connected:true,native:true}));}
 update(){this.native.update();}
 dispose(){this.meshes.forEach((_,i)=>this.native.disconnect(i));this.native.layer.remove();}
}
