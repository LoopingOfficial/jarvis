/** VelkoBlockedActionPanel — une action bloquée n'immobilise jamais VELKO.
 *  Panneau NON MODAL : déplaçable (barre de titre), réductible, toujours
 *  fermable. Fermer ≠ abandonner : la mission reste en attente et se retrouve
 *  via l'indicateur « 1 mission en attente ». */
const CAT_LABEL={AUTH_REQUIRED:'Authentification requise',CONNECTOR_MISSING:'Connecteur manquant',
 NETWORK_ERROR:'Service injoignable',PERMISSION_DENIED:'Permission refusée',
 USER_CONFIRMATION_REQUIRED:'Décision requise',TOOL_UNAVAILABLE:'Outil indisponible',
 INVALID_CONFIGURATION:'Configuration invalide',EXTERNAL_SERVICE_ERROR:'Erreur du service',UNKNOWN:'Cause inconnue'};
const POS_KEY='velko.blockedPanel.pos';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};

export class VelkoBlockedActionPanel {
 constructor(bus,{onConfigure,onRetry,onResume,onCancel,onChangeMethod}){
  Object.assign(this,{bus,onConfigure,onRetry,onResume,onCancel,onChangeMethod});
  this.pending=new Map();          // taskId -> blocage
  this.current=null;
  this.node=el('section','vb-panel hidden');this.node.setAttribute('role','dialog');
  this.node.setAttribute('aria-modal','false');this.node.setAttribute('aria-label','Action bloquée');
  this.node.innerHTML=`<div class="vb-bar" title="Glisser pour déplacer"><span class="vb-dot"></span><b>ACTION BLOQUÉE</b>
   <div class="vb-ctl"><button data-a="details" title="Détails">↗</button><button data-a="min" title="Réduire">–</button><button data-a="close" title="Fermer (la mission reste en attente)">✕</button></div></div>
   <div class="vb-body"></div><div class="vb-actions"></div>`;
  document.body.append(this.node);
  this.indicator=el('button','vb-indicator hidden');this.indicator.type='button';
  this.indicator.title='Rouvrir la mission en attente';
  this.indicator.onclick=()=>this.reopen();
  document.querySelector('.top-actions')?.prepend(this.indicator);
  this.node.querySelector('[data-a="close"]').onclick=()=>this.close();
  this.node.querySelector('[data-a="min"]').onclick=()=>this.node.classList.toggle('minimized');
  this.node.querySelector('[data-a="details"]').onclick=()=>this.node.classList.toggle('expanded');
  this.drag();
  addEventListener('resize',()=>this.clamp());
 }
 show(block){
  const key=block.taskId||block.missionId||'local';
  this.pending.set(key,{...block,key});this.current=this.pending.get(key);
  this.render();this.node.classList.remove('hidden','minimized');this.restore();this.updateIndicator();
 }
 render(){
  const b=this.current,r=b.recovery||{};const body=this.node.querySelector('.vb-body');body.replaceChildren();
  body.append(el('p','vb-title',r.cause||b.result||'Intervention requise.'));
  const grid=el('dl','vb-grid');
  const row=(k,v)=>{if(!v)return;grid.append(el('dt',null,k),el('dd',null,v));};
  row('CAUSE',CAT_LABEL[r.category]||r.category);row('OUTIL REQUIS',r.connector_label||r.connector);
  row('ÉTAT',r.state);row('VELKO A BESOIN DE',r.needs);row('SOLUTION',r.solution);
  body.append(grid);
  const det=el('div','vb-details');
  if(b.task)det.append(el('p',null,'Mission : '+b.task));
  if(r.detail)det.append(el('p',null,'Détail : '+r.detail));
  if(r.http_status)det.append(el('p',null,'HTTP '+r.http_status));
  if(b.taskId)det.append(el('p',null,'Tâche : '+b.taskId));
  body.append(det);
  const acts=this.node.querySelector('.vb-actions');acts.replaceChildren();
  const actions=(r.actions&&r.actions.length?r.actions:[{id:'retry',label:'Réessayer'},{id:'cancel',label:'Annuler la mission'}]);
  const add=(label,fn,primary)=>{const btn=el('button',primary?'vb-primary':'',label);btn.type='button';btn.onclick=fn;acts.append(btn);};
  for(const a of actions){
   if(a.id==='configure')add(a.label||'Configurer',()=>this.onConfigure?.(a.connector||r.connector,b),true);
   else if(a.id==='retry')add(b.taskId?'Reprendre la mission':'Réessayer',()=>this.act(()=>b.taskId?this.onResume?.(b):this.onRetry?.(b)));
   else if(a.id==='change_method')add(a.label,()=>this.onChangeMethod?.(b));
   else if(a.id==='cancel')add(a.label||'Annuler',()=>this.act(()=>this.onCancel?.(b)));
  }
  if(r.connector&&!actions.some(a=>a.id==='configure'))add('Ouvrir les paramètres',()=>this.onConfigure?.(r.connector,b));
 }
 act(fn){const b=this.current;if(b)this.pending.delete(b.key);this.current=null;this.node.classList.add('hidden');this.updateIndicator();fn();}
 /** Fermer ne signifie PAS abandonner : la mission reste listée en attente. */
 close(){this.node.classList.add('hidden');this.updateIndicator();}
 resolve(taskId){this.pending.delete(taskId);if(this.current?.key===taskId){this.current=null;this.node.classList.add('hidden');}this.updateIndicator();}
 reopen(){const last=[...this.pending.values()].pop();if(!last)return;this.current=last;this.render();this.node.classList.remove('hidden','minimized');this.clamp();}
 isOpen(){return !this.node.classList.contains('hidden');}
 /** Après configuration réussie d'un connecteur : propose la reprise. */
 connectorReady(type){for(const b of this.pending.values())if(b.recovery?.connector===type){this.current=b;this.render();
  const body=this.node.querySelector('.vb-body');const ok=el('p','vb-ok',`${b.recovery.connector_label||type} est maintenant connecté. Vous pouvez reprendre la mission.`);body.prepend(ok);
  this.node.classList.remove('hidden','minimized');this.clamp();return true;}return false;}
 updateIndicator(){const n=this.pending.size;this.indicator.classList.toggle('hidden',!n||this.isOpen());
  this.indicator.textContent=n?`● ${n} mission${n>1?'s':''} en attente`:'';}
 drag(){
  const bar=this.node.querySelector('.vb-bar');let sx=0,sy=0,ox=0,oy=0,moving=false;
  bar.addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;moving=true;bar.setPointerCapture(e.pointerId);
   const r=this.node.getBoundingClientRect();sx=e.clientX;sy=e.clientY;ox=r.left;oy=r.top;this.node.classList.add('dragging');});
  bar.addEventListener('pointermove',e=>{if(!moving)return;this.place(ox+e.clientX-sx,oy+e.clientY-sy);});
  const end=()=>{if(!moving)return;moving=false;this.node.classList.remove('dragging');
   const r=this.node.getBoundingClientRect();try{sessionStorage.setItem(POS_KEY,JSON.stringify({x:r.left,y:r.top}));}catch{}};
  bar.addEventListener('pointerup',end);bar.addEventListener('pointercancel',end);
 }
 place(x,y){const r=this.node.getBoundingClientRect(),m=48;   // au moins 48 px restent visibles
  x=Math.min(Math.max(x,m-r.width),innerWidth-m);y=Math.min(Math.max(y,0),innerHeight-m);
  Object.assign(this.node.style,{left:x+'px',top:y+'px',right:'auto',bottom:'auto'});}
 restore(){let p=null;try{p=JSON.parse(sessionStorage.getItem(POS_KEY)||'null');}catch{}
  if(p)this.place(p.x,p.y);}
 clamp(){if(this.node.style.left){const r=this.node.getBoundingClientRect();this.place(r.left,r.top);}}
}
