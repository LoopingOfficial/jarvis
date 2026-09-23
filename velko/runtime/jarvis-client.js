/** Bridge between the VELKO stage and the real engine.
 *  Every action shown on the monitors comes from an engine event; nothing here
 *  invents progress, and completion is only ever delivered by the engine reply. */
import {engineFeed} from './engine-feed.js';
import {VelkoVisualActivityManager, VisualActionScheduler, VISUAL_STATES} from './visual-activity.js';
const TOOL_KINDS=[
 [/^n8n\./i,'terminal',1],            // n8n : exécution/logs au centre
 [/discord/i,'discord',2],
 [/browser|navigat|web|search|http|fetch|site|scrape|url/i,'browser',2],
 [/google\.|sheet|brainrot\.brainrots\./i,'sheet',2],
 [/brainrot\.analytics|analytics|registrations|activity|email_status|users_/i,'analytics',1],
 [/brainrot\.blog\.|blog\./i,'blog',2],
 [/brainrot\.email\.|email\./i,'email',2],
 [/^db\./i,'database',1],
 [/shell|command|terminal|bash|process|exec|run\b|install|test/i,'terminal',1],
 [/file|code|write|edit|patch|read_file|document/i,'code',0],
];
const ACTIONS={code:'type',terminal:'type',browser:'click',discord:'click',sheet:'read',analytics:'read',blog:'type',email:'type',database:'read',read:'read'};
/** Geste par défaut pour une action. Le geste suit toujours un fait réel. */
const GESTURES={type:'TypingNormal',click:'MouseClick',scroll:'MouseScroll',read:'ReadScreen'};
export function classifyTool(name=''){for(const [re,kind,screen] of TOOL_KINDS)if(re.test(name))return {kind,screen};return {kind:'read',screen:1};}

export class VelkoJarvisClient {
 constructor(bus,director,screens){
  Object.assign(this,{bus,director,screens});
  this.screenRouter=screens?.router||null;
  this.id=null;this.seq=0;this.conversationId='';this.pendingAction=null;this.source=null;this.confirmation=null;
  // Couche d'activité visuelle : regroupe les rafales techniques en activité humaine.
  this.visual=new VelkoVisualActivityManager();
  this.scheduler=new VisualActionScheduler();
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
   const name=String(data.tool||data.tool_id||data.name||'');const {kind,screen}=classifyTool(name);
   // L'activité visuelle est dictée par le véritable outil appelé : une requête
   // DB approfondie, une comparaison de Sheet… Aucune étiquette générique.
   const visual=this.visual.consume(type,{...data,tool:name});
   const activity=this.scheduler.push(visual||this.visual.current());
   if(this.screenRouter)this.screenRouter.setActivity({activityFamily:kind,tool:name,resource:data.path||''});
   if(this.screenRouter)this.screenRouter.ingest(type,data);
   if(!activity)return;   // rafale identique absorbée : aucun geste répété
   // L'écran que le routeur a réellement affiché fait autorité pour le regard :
   // l'avatar regarde le moniteur qui montre le contenu, pas une recopie d'outil.
   const routed=this.screenRouter?.screenFor(kind) ?? screen;
   return this.apply({kind:activity.kind||kind,screen:routed>=0?routed:activity.screen,action:
     (kind==='sheet'||kind==='analytics'||kind==='database')?'read':ACTIONS[kind],
    gesture:activity.gesture||GESTURES[ACTIONS[kind]]||'ReadScreen',
    actionId:String(data.call_id||data.id||name),label:activity.label,path:data.path||''});
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
  // Google Sheets / famille métier : la comparaison se lit, l'écriture se tape.
  if(type.startsWith('google.')||type.startsWith('sheet.')) {
   const visual=this.visual.consume(type,data);
   const activity=this.resolvedActivity(visual,'sheet');
   if(this.screenRouter)this.screenRouter.setActivity({activityFamily:'sheet',tool:'google.sheets',resource:data.spreadsheet_id||''});
   return this.apply({kind:'sheet',screen:2,action:'read',gesture:activity.gesture||'ReadScreen',
    label:activity.label||'Google Sheets',path:String(data.spreadsheet_id||data.mode||'')});
  }
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
   if(label){this.bus.emit('engine.notice',{text:label,phase:data.phase});
    if(this.screenRouter)this.screenRouter.ingest(type,data);}
   return;
  }
  if(type==='tool.completed'||type==='tool.failed'||type==='tool.denied'){
   // Le contenu réel (preview, erreur) alimente le panneau d'activité : la fin
   // d'un outil n'est pas une raison de « claquer » un geste, mais une raison
   // de montrer le vrai résultat sur l'écran actif.
   const name=String(data.tool||data.tool_id||data.name||'');
   const preview=String(data.preview||data.error||'');
   if(preview&&this.screenRouter)this.screenRouter.setActivity({tool:name});
   this.bus.emit('tool.real',{type,name,preview,ok:type!=='tool.failed'&&type!=='tool.denied',path:data.path||''});
   return this.apply(null);
  }
  if(type==='jarvis.activity'||type==='activity.trace'||type==='system.warning')
   this.bus.emit('engine.notice',{text:String(data.detail||data.message||data.text||'')});
 }
 /** Étiquette humaine issue de l'activité visuelle, sinon de l'outil. */
 resolvedActivity(visual,kind){
  if(visual&&VISUAL_STATES[visual.state])return {...VISUAL_STATES[visual.state],label:visual.label,gesture:visual.gesture};
  return {kind,screen:[...new Set([2,1,1,1,2,2,2,1,0])][['discord','browser','sheet','analytics','blog','email','database','terminal','code'].indexOf(kind)]||1,
   label:(VISUAL_STATES.ANALYSING_DATA&&kind==='analytics'?'Analyse des données':kind),gesture:'ReadScreen'};
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
 async start(task,{url='/api/command',body=null}={}){
  // Une mission bloquée ne fige jamais VELKO : elle est mise de côté (reprenable).
  if(this.director.active&&['blocked','failed'].includes(this.director.task?.status))this.director.park();
  if(this.director.active)throw Error('Une mission est déjà en cours. Attendez le retour de VELKO.');
  this.id='m'+Date.now().toString(16);this.seq=0;this.pendingAction=null;this.confirmation=null;
  this.screens.routeTask(task);this.director.start(task,{id:this.id});
  if(this.screenRouter)this.screenRouter.setActivity({activityFamily:'',tool:'',resource:''});
  this.bus.emit('mission.created',{id:this.id,task,status:'running'});
  this.bus.emit('mission.snapshot',{id:this.id,status:'running'});
  this.dispatch(body||{text:task,conversation_id:this.conversationId,source:'text'},url);
  return {id:this.id};
 }
 /** REPRENDRE LA MISSION : relance la tâche bloquée d'origine, sans la retaper. */
 resume(taskId,label){return this.start(label||'Reprise de la mission',{url:`/api/tasks/${encodeURIComponent(taskId)}/resume`,body:{}});}
 /** Fire and forget : the reply closes the mission, the SSE feed animates it. */
 dispatch(payload,url='/api/command'){
  const id=this.id;
  this.request(url,payload)
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
   this.bus.emit('mission.snapshot',{id:this.id,status:'blocked',result:text,
    recovery:result.recovery||null,taskId:result.task_id||'',task:this.director.task?.text||'',
    confirmation:!!this.confirmation});
   if(this.confirmation)this.bus.emit('mission.confirmation',{...this.confirmation,message:text});
   return;
  }
  this.director.receive({taskId:this.id,seq:++this.seq,type:'task.completed',status:'completed',
   result:text||'Travail terminé.'});
  if(result.n8n)this.bus.emit('n8n.result',result.n8n);
  this.bus.emit('mission.snapshot',{id:this.id,status:'completed',result:text});
 }
 fail(reason){
  this.director.receive({taskId:this.id,seq:++this.seq,type:'task.failed',reason});
  this.bus.emit('mission.snapshot',{id:this.id,status:'failed',result:reason,
   recovery:{category:'NETWORK_ERROR',cause:reason,state:'Moteur injoignable ou réponse invalide',
    solution:'Vérifiez que le moteur VELKO tourne, puis réessayez.',actions:[{id:'retry',label:'Réessayer'},{id:'cancel',label:'Annuler la mission'}]},
   task:this.director.task?.text||''});
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