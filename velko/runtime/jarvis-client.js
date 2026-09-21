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
   return this.apply({kind:'code',screen:0,action:type==='code.file.modified'||type==='code.file.saving'?'type':'read',path,label:path});
  }
  if(type==='tool.started'||type==='tool.called'){
   const name=String(data.tool||data.name||data.tool_id||'');const {kind,screen}=classifyTool(name);
   return this.apply({kind,screen,action:ACTIONS[kind],actionId:String(data.call_id||data.id||name),label:name,path:data.path||''});
  }
  if(type==='tool.completed'||type==='tool.failed'||type==='tool.denied')return this.apply(null);
  if(type==='agent.progress'||type==='task.progress'){
   const value=Number(data.progress??data.percent);
   if(Number.isFinite(value))this.bus.emit('mission.progress',{progress:Math.max(0,Math.min(100,value))});
   return;
  }
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
 settle(result){
  this.conversationId=result.conversation_id||this.conversationId;
  const text=String(result.response||'').trim();
  if(result.needs_confirmation){
   this.confirmation=result.needs_confirmation;
   this.director.receive({taskId:this.id,seq:++this.seq,type:'task.blocked',result:text||'Confirmation requise.'});
   this.bus.emit('mission.snapshot',{id:this.id,status:'blocked',result:text});
   this.bus.emit('mission.confirmation',{...this.confirmation,message:text});
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
