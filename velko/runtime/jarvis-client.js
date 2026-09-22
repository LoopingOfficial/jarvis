/** Bridge between the VELKO stage and the real engine.
 *  Every action shown on the monitors comes from an engine event; nothing here
 *  invents progress, and completion is only ever delivered by the engine reply. */
import {engineFeed} from './engine-feed.js';
const TOOL_KINDS=[
 [/discord/i,'discord',2],
 [/browser|navigat|web|search|http|fetch|site|scrape|url/i,'browser',2],
 [/shell|command|terminal|bash|process|exec|run\b|install|test/i,'terminal',1],
 [/file|code|write|edit|patch|read_file|document/i,'code',0],
];
const ACTIONS={code:'type',terminal:'type',browser:'click',discord:'click',read:'read'};
/** Geste par défaut pour une action. Le geste suit toujours un fait réel. */
const GESTURES={type:'TypingNormal',click:'MouseClick',scroll:'MouseScroll',read:'ReadScreen'};
export function classifyTool(name=''){for(const [re,kind,screen] of TOOL_KINDS)if(re.test(name))return {kind,screen};return {kind:'read',screen:1};}

export class VelkoJarvisClient {
 constructor(bus,director,screens){
  Object.assign(this,{bus,director,screens});
  this.id=null;this.seq=0;this.conversationId='';this.pendingAction=null;this.source=null;this.confirmation=null;
  bus.on('workstation.ready',({taskId})=>{if(taskId===this.id&&this.pendingAction!==null){this.director.setAction(this.pendingAction);this.pendingAction=null;}});
 }
 connect(){
  if(this.source)return this.source;
  this.source=engineFeed();
  this.source.subscribe(frame=>{
   if(frame.type==='feed.state')
    return this.bus.emit(frame.data.online?'engine.online':'engine.offline',
     {reason:'Flux du moteur interrompu. Reconnexion automatique.'});
   this.ingest(frame);
  });
  return this.source;
 }
 /** Engine event -> stage event. Unknown types are ignored, never guessed. */
 ingest(frame){
  const type=frame?.type||'',data=frame?.data||{};
  if(!type)return;
  this.bus.emit('engine.event',{type,data});
  if(!this.id)return;
  if(['llm.started','memory.search.started','knowledge.search.started','agent.started','task.started','task.created','mission.started','sheet.progress'].includes(type))
   return this.apply({kind:'read',screen:1,action:'read',label:String(data.title||data.detail||data.agent||'Analyse')});
  if(type==='jarvis.state'&&['IDLE','STANDBY','WAITING'].includes(String(data.state||'').toUpperCase()))return this.apply(null);
  if(type.startsWith('code.file.')){
   const path=data.absolute_path||data.filename||data.path||'';
   if(type==='code.file.saved'||type==='code.file.error'||type==='code.file.closed')return this.apply(null);
   const writing=type==='code.file.modified'||type==='code.file.saving';
   return this.apply({kind:'code',screen:0,action:writing?'type':'read',
    gesture:writing?'TypingNormal':'ReadScreen',path,label:path});
  }
  // Fichiers réels : une OUVERTURE se lit, une ÉCRITURE se tape. On ne tape
  // jamais « pour faire joli » : le geste suit l'opération réellement faite.
  if(type==='file.opened'||type==='code.file.active'){
   const path=String(data.path||data.absolute_path||'');
   return this.apply({kind:'code',screen:0,action:'read',gesture:'ReadScreen',path,label:path||'Fichier'});
  }
  if(type==='file.changed'||type==='file.created'||type==='code.patch.applied'){
   const path=String(data.path||data.absolute_path||'');
   return this.apply({kind:'code',screen:0,action:'type',gesture:'TypingFast',path,label:path||'Fichier'});
  }
  if(type==='file.deleted')
   return this.apply({kind:'code',screen:0,action:'read',gesture:'ReadScreen',
    path:String(data.path||''),label:String(data.path||'')});
  if(type==='tool.started'||type==='tool.called'){
   const name=String(data.tool||data.name||data.tool_id||'');const {kind,screen}=classifyTool(name);
   return this.apply({kind,screen,action:ACTIONS[kind],gesture:GESTURES[ACTIONS[kind]]||'ReadScreen',
    actionId:String(data.call_id||data.id||name),label:name,path:data.path||''});
  }
  // Terminal : la commande se tape puis se valide ; dès que le processus
  // produit sa sortie, il travaille SEUL — VELKO retire les mains et lit.
  if(type==='terminal.command')
   return this.apply({kind:'terminal',screen:1,action:'type',gesture:'PressEnter',
    label:String(data.command||'Terminal'),path:String(data.cwd||'')});
  if(type==='terminal.output')
   return this.apply({kind:'terminal',screen:1,action:'read',gesture:'ReadScreen',
    label:'Sortie du processus'});
  if(type==='terminal.failed'||type==='terminal.completed')
   return this.apply({kind:'terminal',screen:1,action:'read',gesture:'ReadScreen',
    label:type==='terminal.completed'?'Fin du processus':'Processus en échec'});
  if(type.startsWith('process.'))
   return this.apply({kind:'terminal',screen:1,action:'read',gesture:'ReadScreen',
    label:'Processus '+type.split('.')[1]});
  // Navigateur réel : la main droite va sur la souris, et revient au clavier
  // seulement quand VELKO saisit réellement du texte.
  if(type.startsWith('browser.')){
   if(type==='browser.frame')return;                       // flux d'image : aucun geste
   const what=type.split('.').slice(1).join('.');
   // La main atteint la souris sur le clic réel, puis appuie sur l'événement
   // d'action correspondant : deux faits successifs, deux gestes.
   if(type==='browser.action'){
    const kind=String(data.kind||'').toUpperCase();
    const g={CLICK:'MouseClick',SCROLL:'MouseScroll',OPEN:'ReadScreen',
             BACK:'MouseClick',FORWARD:'MouseClick',TYPE:'TypingNormal'}[kind];
    if(!g)return;
    return this.apply({kind:'browser',screen:2,
     action:g.startsWith('Typing')?'type':g==='MouseScroll'?'scroll':g==='ReadScreen'?'read':'click',
     gesture:g,label:'Navigateur · '+kind.toLowerCase(),path:String(data.target||'')});
   }
   const MAP={
    started:['read','ReadScreen'], navigate:['click','MouseReach'],
    loaded:['read','ReadScreen'],  click:['click','MouseReach'],
    scroll:['scroll','MouseScroll'], type:['type','TypingNormal'],
    read:['read','ReadScreen'],    wait:['read','ReadScreen'],
    gate:['read','ReadScreen'],    error:['read','ReadScreen'],
    closed:['read','ReadScreen'],  download:['read','ReadScreen'],
   };
   const [action,gesture]=MAP[what]||['read','ReadScreen'];
   return this.apply({kind:'browser',screen:2,action,gesture,
    label:'Navigateur · '+what,path:String(data.url||data.target||'')});
  }
  if(type.startsWith('ssh.')){
   const what=type.split('.')[1];
   return this.apply({kind:'terminal',screen:1,action:what==='run'?'type':'read',
    gesture:what==='run'?'PressEnter':'ReadScreen',label:'SSH · '+what,
    path:String(data.host||'')});
  }
  if(type.startsWith('git.'))
   return this.apply({kind:'terminal',screen:1,action:'read',gesture:'ReadScreen',
    label:'Git · '+type.split('.')[1]});
  // Progression : relayée UNIQUEMENT quand le moteur en fournit une réelle.
  // Aucun pourcentage n'est calculé ni interpolé ici.
  if(type==='task.progress'||type==='agent.progress'||type==='mission.progress'){
   const raw=data.progress;
   if(typeof raw==='number'&&isFinite(raw))
    this.bus.emit('mission.progress',{progress:raw<=1?raw*100:raw});
   if(type!=='task.progress')return;
  }
  if(type==='velko.task.phase'){
   const label=String(data.label||data.phase||'');
   if(label)this.bus.emit('engine.notice',{text:label,phase:data.phase});
   return;
  }
  if(type==='tool.completed'||type==='tool.failed'||type==='tool.denied')return this.apply(null);
  if(type==='jarvis.activity'||type==='activity.trace'||type==='system.warning')
   this.bus.emit('engine.notice',{text:String(data.detail||data.message||data.text||'')});
 }
 apply(action){
  if(!this.director.active)return;
  if(!this.director.ready){this.pendingAction=action;return;}
  this.director.setAction(action);
 }
 async request(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json().catch(()=>({}));
  if(!r.ok||data.ok===false)throw Error(data.error||data.response||'Le moteur a refusé la demande ('+r.status+').');
  return data;
 }
 async start(task){
  if(this.director.active)throw Error('Une mission est déjà en cours. Attendez le retour de VELKO.');
  this.id='m'+Date.now().toString(16);this.seq=0;this.pendingAction=null;this.confirmation=null;
  this.screens.routeTask(task);this.director.start(task,{id:this.id});
  this.bus.emit('mission.created',{id:this.id,task,status:'running'});
  this.bus.emit('mission.snapshot',{id:this.id,status:'running'});
  this.dispatch({text:task,conversation_id:this.conversationId,source:'text'});
  return {id:this.id};
 }
 /** Fire and forget : the reply closes the mission, the SSE feed animates it. */
 dispatch(payload){
  const id=this.id;
  this.request('/api/command',payload)
   .then(result=>{if(this.id===id)this.settle(result);})
   .catch(error=>{if(this.id===id)this.fail(error.message);});
 }
 async settle(result){
  // Read the persisted task: an HTTP reply alone is never completion.
  if(result.task_id){
   try {
    const response=await fetch(`/api/tasks/${encodeURIComponent(result.task_id)}`);
    const detail=await response.json();
    if(!response.ok||!detail.task)throw Error("Statut de la tâche indisponible");
    result={...result,status:detail.task.status};
   } catch(error){return this.fail(error.message);}
  }
  this.conversationId=result.conversation_id||this.conversationId;
  const text=String(result.response||'').trim();
  if(result.blocked||result.needs_confirmation||result.status!=='completed'){
   // ACTION BLOQUÉE : seul le backend décide du blocage (connecteur absent,
   // action non exécutable, validation utilisateur). VELKO reste au poste.
   this.confirmation=result.needs_confirmation||null;
   this.director.receive({taskId:this.id,seq:++this.seq,type:'task.blocked',
    result:text||result.error||'Action bloquée — achèvement non confirmé par le moteur.'});
    this.bus.emit('mission.snapshot',{id:this.id,status:'blocked',result:text});
   if(this.confirmation)this.bus.emit('mission.confirmation',{...this.confirmation,message:text});
   return;
  }
  this.director.receive({taskId:this.id,seq:++this.seq,type:'task.completed',status:'completed',
   result:text||'Travail terminé.'});
  this.bus.emit('mission.snapshot',{id:this.id,status:'completed',result:text});
 }
 fail(reason){
  this.director.receive({taskId:this.id,seq:++this.seq,type:'task.failed',reason});
  this.bus.emit('mission.snapshot',{id:this.id,status:'failed',result:reason});
 }
 async answer(approved){
  if(!this.confirmation)return;
  const confirmation_id=this.confirmation.id;this.confirmation=null;
  this.director.resume();
  this.bus.emit('mission.snapshot',{id:this.id,status:'running'});
  try{this.settle(await this.request('/api/confirm',{confirmation_id,approved}));}
  catch(error){this.fail(error.message);}
 }
}
