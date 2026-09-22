export class VelkoEventBus extends EventTarget {
 emit(type,detail={}) {this.dispatchEvent(new CustomEvent(type,{detail}));this.dispatchEvent(new CustomEvent('*',{detail:{...detail,type}}));}
 on(type,fn){const listener=e=>fn(e.detail);this.addEventListener(type,listener);return ()=>this.removeEventListener(type,listener);}
}
export const STATES=['IDLE_CONVERSATION','IDLE','LISTENING','THINKING','SPEAKING','MOVING_TO_WORKSTATION','SITTING','WORKING_CODE','WORKING_TERMINAL','WORKING_DISCORD','WORKING_BROWSER','READING_OUTPUT','ERROR_STATE','SUCCESS_STATE','RETURN_TO_USER','WORKING','SUCCESS','ERROR','RETURNING'];
// Seule une réussite confirmée autorise le retour.
export const CLOSED_STATUSES=new Set(['completed']);
const RETURN_STATES=new Set(['RETURN_TO_USER','RETURNING','IDLE_CONVERSATION','IDLE']);
export class VelkoStateMachine {
 constructor(bus){this.bus=bus;this.state='IDLE_CONVERSATION';this.history=[];this.taskStatus=null;this.atWorkstation=false;}
 setTaskStatus(status){this.taskStatus=status;}
 set(state){if(!STATES.includes(state))throw Error('Unknown state '+state);
  if(this.atWorkstation&&!CLOSED_STATUSES.has(this.taskStatus)&&(RETURN_STATES.has(state)||state==='MOVING_TO_WORKSTATION')){this.bus.emit('transition.rejected',{from:this.state,to:state,reason:'task_not_completed'});return false;}
  if(state===this.state)return true;this.state=state;this.history.push({state,time:performance.now()});this.bus.emit('state.changed',{state});return true;
 }
}
export class VelkoCameraDirector {
 constructor(T,camera){this.T=T;this.camera=camera;this.focus=new T.Vector3(0,1.03,2.3);this.position=new T.Vector3(0,1.37,4.15);camera.position.copy(this.position);this.mode='conversation';this.workLocked=false;}
 set(mode){if(this.workLocked&&mode==='conversation')return false;this.mode=mode;const views={conversation:[[0,1.38,4.15],[0,1.10,2.3]],work:[[3.35,2.2,.2],[1.32,.99,-2.22]],hands:[[2.75,1.72,-.65],[1.42,.87,-2.16]],room:[[6.1,3.5,6.8],[0,.95,-.6]],transition:[[3.8,2.4,4.8],[.65,.95,.25]]};const v=views[mode]||views.conversation;this.position.set(...v[0]);this.target=new this.T.Vector3(...v[1]);return true;}
 update(dt){this.camera.position.lerp(this.position,1-Math.exp(-dt*1.9));if(this.target)this.focus.lerp(this.target,1-Math.exp(-dt*2));this.camera.lookAt(this.focus);}
}
export class VelkoSceneDirector {
 constructor(bus,machine,avatar,camera,speak){Object.assign(this,{bus,machine,avatar,camera,speak});this.active=false;this.time=0;this.next=0;this.marks=[];this.context={seated:true,typing:false,mouse:false,action:'read'};avatar.root.position.set(0,0,2.3);this.task=null;this.ready=false;this.returnStart=null;this.currentAction=null;this.lastSequence=0;}
 schedule(t,fn){this.marks.push({t,fn});}
 finished(){return CLOSED_STATUSES.has(this.task?.status);}
 /** Le moteur a répondu avant l'arrivée au poste : on annule le trajet plutôt
  *  que d'annoncer un travail qui n'aura pas lieu. */
 cancelStaging(){if(this.machine.atWorkstation)return false;this.marks=[];this.next=0;
  this.context.walking=false;this.context.seated=true;this.avatar.root.position.set(0,0,2.3);
  this.avatar.root.rotation.y=0;this.camera.workLocked=false;this.camera.set('conversation');return true;}
 /** Confirmation accordée : la mission repart, VELKO reste au poste. */
 resume(){if(!this.active||!['blocked','failed'].includes(this.task?.status))return false;this.task.status='running';this.machine.setTaskStatus('running');this.completionTime=undefined;this.machine.set('READING_OUTPUT');return true;}
 start(task,options={}){if(this.active)return false;this.active=true;this.time=0;this.next=0;this.marks=[];this.ready=false;this.returnStart=null;this.lastSequence=0;this.currentAction=null;this.task={id:options.id||'local',text:task,status:'queued'};this.machine.atWorkstation=false;this.machine.setTaskStatus('queued');this.camera.workLocked=false;this.context={seated:true,typing:false,mouse:false,action:'read',look:0,speaking:false};this.bus.emit('TASK_STARTED',{task,taskId:this.task.id});this.machine.set('LISTENING');this.camera.set('conversation');
 this.schedule(.8,()=>this.machine.set('THINKING'));
 this.schedule(1.8,()=>{this.machine.set('SPEAKING');this.context.speaking=true;this.speak('Bien reçu. Je rejoins mon poste. Vous pourrez suivre les opérations réelles sur les écrans.');});
 this.schedule(3.5,()=>{this.context.look=1;this.context.speaking=false;});
 this.schedule(4.2,()=>{this.machine.set('MOVING_TO_WORKSTATION');this.context.seated=false;this.camera.set('transition');this.moveStart=this.time;});
 this.schedule(11.2,()=>{this.machine.set('SITTING');this.context.walking=false;this.context.seated=true;this.avatar.root.position.set(1.4,0,-1.6);this.avatar.root.rotation.y=Math.PI;this.machine.atWorkstation=true;this.camera.workLocked=true;this.camera.set('work');});
 this.schedule(12.4,()=>{this.ready=true;this.machine.set(['blocked','failed'].includes(this.task?.status)?'ERROR_STATE':'READING_OUTPUT');this.bus.emit('workstation.ready',{taskId:this.task.id});});
 return true;}
 restore(snapshot){
 if(this.active||!snapshot.id||snapshot.status==='completed'||snapshot.status==='idle')return false;
 this.start(snapshot.task,{id:snapshot.id});this.marks=[];this.next=0;this.ready=true;
 this.task.status=snapshot.status;this.machine.setTaskStatus(snapshot.status);this.machine.atWorkstation=true;
 this.camera.workLocked=true;this.camera.set('work');this.avatar.root.position.set(1.4,0,-1.6);this.avatar.root.rotation.y=Math.PI;
 this.context={seated:true,walking:false,typing:false,mouse:false,action:'read',look:0,speaking:false};
 this.machine.set(['blocked','failed'].includes(snapshot.status)?'ERROR_STATE':'READING_OUTPUT');this.sync(snapshot);return true;
 }
 receive(event){if(!this.active||event.taskId!==this.task.id)return false;if(event.seq&&event.seq<=this.lastSequence)return false;if(event.seq)this.lastSequence=event.seq;
 if(event.type==='task.completed'){
  // Completion can only be delivered by the actual worker, never by a timer.
  if(event.status!=='completed')return false;
  this.task.status='completed';this.machine.setTaskStatus('completed');this.currentAction=null;this.setAction(null);this.cancelStaging();this.machine.set('SUCCESS_STATE');this.camera.workLocked=false;
  this.speak(event.result||'Le travail est terminé. Les fichiers et les résultats sont disponibles.');this.context.speaking=true;this.completionTime=this.time;this.bus.emit('TASK_COMPLETED',{taskId:this.task.id,result:event.result,progress:100});return true;
 }
 if(event.type==='task.blocked'||event.type==='task.failed'){
  this.task.status=event.type==='task.failed'?'failed':'blocked';this.machine.setTaskStatus(this.task.status);this.currentAction=null;this.setAction(null);if(this.ready)this.machine.set('ERROR_STATE');this.completionTime=undefined;this.bus.emit('mission.blocked',{...event,reason:event.reason||event.message||event.result||'Intervention requise'});return true;
 }
 return true;
 }
 setAction(action){this.currentAction=action;this.context.typing=false;this.context.mouse=false;this.context.action='read';this.context.look=0;this.context.gesture='ReadScreen';
  if(!this.ready||this.finished())return;
  if(!action){if(this.task.status!=='blocked'&&this.task.status!=='failed')this.machine.set('READING_OUTPUT');return;}
  const states={code:'WORKING_CODE',terminal:'WORKING_TERMINAL',discord:'WORKING_DISCORD',browser:'WORKING_BROWSER',read:'READING_OUTPUT'};
  this.context.look=[-1,0,1][action.screen??1];this.context.action=action.action||'read';
  // Reading output or waiting for a process does not cause decorative keystrokes.
  this.context.typing=action.action==='type';this.context.mouse=['click','scroll','move','drag'].includes(action.action);
  // Le geste vient du fait réel traduit par le pont ; à défaut, il découle
  // de l'action. Jamais de frappe quand un processus travaille seul.
  this.context.gesture=action.gesture||{type:'TypingNormal',click:'MouseClick',scroll:'MouseScroll',drag:'MouseDrag'}[action.action]||'ReadScreen';
  this.machine.set(states[action.kind]||'READING_OUTPUT');this.bus.emit('action.focus',{...action});
 }
 sync(snapshot){if(!this.active||snapshot.id!==this.task.id)return;this.task.status=snapshot.status;this.machine.setTaskStatus(snapshot.status);if(this.ready&&!['completed','blocked','failed'].includes(snapshot.status))this.setAction(snapshot.currentAction||null);}
 update(dt){if(this.active){this.time+=dt;while(this.next<this.marks.length&&this.time>=this.marks[this.next].t)this.marks[this.next++].fn();}
 const a=this.avatar.root,s=this.machine.state;
 if(this.active&&this.finished()&&!this.machine.atWorkstation&&['SUCCESS_STATE','ERROR_STATE'].includes(s)&&this.time-this.completionTime>3){
  this.context.speaking=false;this.active=false;this.machine.set('IDLE_CONVERSATION');
  this.bus.emit('mission.finished',{taskId:this.task.id,status:this.task.status});
 }
 if(this.finished()&&['SUCCESS_STATE','ERROR_STATE'].includes(s)&&this.time-this.completionTime>4){this.context.speaking=false;if(this.machine.set('RETURN_TO_USER')){this.returnStart=this.time;this.context.seated=false;this.camera.set('transition');}}
 if(s==='MOVING_TO_WORKSTATION'||s==='RETURN_TO_USER'||s==='RETURNING'){
  const returning=s!=='MOVING_TO_WORKSTATION';if(returning&&!this.finished())throw Error('Return guard: mission still open');
  const u=Math.min(1,(this.time-(returning?this.returnStart:this.moveStart))/7),walk=Math.max(0,Math.min(1,(u-.2)/.68));this.context.walking=walk>0&&walk<1;
  const p=returning?1-walk:walk,bend=.8;let dx,dz;if(p<bend){a.position.set(.62*p/bend,0,2.3-3.9*p/bend);dx=.62;dz=-3.9;}else{a.position.set(.62+.78*(p-bend)/(1-bend),0,-1.6);dx=.78;dz=0;}
  const desired=Math.atan2(returning?-dx:dx,returning?-dz:dz),target=walk>=1?(returning?0:Math.PI):desired;let diff=((target-a.rotation.y+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;a.rotation.y+=diff*(1-Math.exp(-dt*5));
  if(returning&&u>=1){this.context.walking=false;this.context.seated=true;this.context.look=0;this.machine.atWorkstation=false;this.machine.set('IDLE_CONVERSATION');this.camera.set('conversation');this.active=false;this.bus.emit('mission.finished',{taskId:this.task.id,status:this.task.status});}
 }
 if(this.machine.atWorkstation&&!this.finished()){a.position.set(1.4,0,-1.6);a.rotation.y=Math.PI;this.context.seated=true;this.context.walking=false;}
 this.avatar.update(dt,performance.now()/1000,this.machine.state.toLowerCase(),this.context);
 }
}
