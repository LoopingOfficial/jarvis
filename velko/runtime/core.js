export class VelkoEventBus extends EventTarget {
 emit(type,detail={}) {this.dispatchEvent(new CustomEvent(type,{detail}));this.dispatchEvent(new CustomEvent('*',{detail:{...detail,type}}));}
 on(type,fn){const listener=e=>fn(e.detail);this.addEventListener(type,listener);return ()=>this.removeEventListener(type,listener);}
}
export const STATES=['IDLE_CONVERSATION','IDLE','LISTENING','THINKING','SPEAKING','MOVING_TO_WORKSTATION','SITTING','WORKING_CODE','WORKING_TERMINAL','WORKING_DISCORD','WORKING_BROWSER','READING_OUTPUT','ERROR_STATE','SUCCESS_STATE','RETURN_TO_USER','WORKING','SUCCESS','ERROR','RETURNING','AWAITING_USER_DECISION'];
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
 constructor(T,camera){this.T=T;this.camera=camera;this.focus=new T.Vector3(0,1.03,2.3);this.position=new T.Vector3(0,1.37,4.15);camera.position.copy(this.position);this.mode='conversation';this.workLocked=false;
  // Mémoïsation du cadrage : la caméra se déplace en DÉPLACEMENTS COURTS et
  // lents (600-1200 ms), jamais en coups nerveux. Une nouvelle cible pendant
  // un plan est mise en file, appliquée à la fin du plan courant.
  this.permutation=0;this.pending=null;this.shotDuration=800;this.shotStartAt=0;this.pace='auto';}
 set(mode){if(this.workLocked&&mode==='conversation')return false;this.mode=mode;const views={
  conversation:[[0,1.38,4.15],[0,1.10,2.3]],
  work:[[3.35,2.2,.2],[1.32,.99,-2.22]],
  // Plan travail élargi : VELKO et ses trois écrans visibles, lecture des rôles.
  work_wide:[[4.2,2.5,.6],[1.4,1.0,-2.3]],                 // 1200 ms : cadrage large
  // Plans rapprochés : l'écran actif est VISIBLE ET LISIBLE, jamais lointain.
  work_left_screen:[[2.7,1.8,-1.2],[.56,1.24,-2.77]],     // 700 ms : moniteur gauche
  work_center_screen:[[2.4,1.8,-1.0],[1.4,1.24,-2.77]],   // 700 ms : moniteur central
  work_right_screen:[[1.0,1.8,-1.2],[2.24,1.24,-2.77]],   // 700 ms : moniteur droit
  work_hands:[[2.75,1.72,-.65],[1.42,.87,-2.16]],         // 600 ms : clavier/souris
  // Plan « par-dessus l'épaule » : VELKO reste visible, jamais relégué loin.
  work_over_shoulder:[[2.6,1.95,0],[1.4,.9,-2.2]],        // 800 ms
  // Présentation d'un rapport : moniteur central en grand, VELKO au poste.
  report_screen:[[2.3,1.62,-.95],[1.5,1.2,-2.77]],
  room:[[6.1,3.5,6.8],[0,.95,-.6]],
  transition:[[3.8,2.4,4.8],[.65,.95,.25]]};
  const v=views[mode]||views.conversation;this.position.set(...v[0]);this.target=new this.T.Vector3(...v[1]);this.shotDuration={work_wide:1200,work_left_screen:700,work_center_screen:700,work_right_screen:700,work_hands:600,work_over_shoulder:800}[mode]??800;this.shotStartAt=performance.now();this.permutation=0;return true;}
 /** Auto-focus : le moniteur QUE VELKO utilise réellement devient le plan.
  *  `screen` = 0/1/2 (écran du routeur). `action` = read/type/click/scroll moyenné. */
 focusMonitor(screen=1,action='read'){
  if(!this.workLocked||this.pace!=='auto')return false;
  const now=performance.now();
  // Un plan dure au minimum 2.5 s : les micro-changements de frappe/lecture ne
  // produisent pas de coupe nerveuse, la cible suivante est mise en file.
  if(now-this.shotStartAt<2500){this.pending={screen,action};return false;}
  this.pending=null;
  const mode=action==='type'||action==='scroll'?'work_hands'
   :action==='click'||action==='drag'?'work_over_shoulder'
   :(['work_left_screen','work_center_screen','work_right_screen'])[screen]||'work_center_screen';
  return this.set(mode);}
 /** Retour au plan de travail global (clic ou voix). */
 wide(pace='manual'){this.pace=pace;return this.set('work_wide');}
 resumeAuto(){this.pace='auto';return this.set(this.mode.startsWith('work_left')?'work_left_screen':this.mode.startsWith('work_right')?'work_right_screen':(this.mode==='work_hands'||this.mode==='work_over_shoulder'?'work_center_screen':this.mode));}
 update(dt){
  // Fin d'un plan : la cible mise en file est appliquée si elle est restée stable.
  if(this.pending&&performance.now()-this.shotStartAt>=2500){const p=this.pending;this.pending=null;this.focusMonitor(p.screen,p.action);}
  const tau=this.shotDuration/1000;                       // 600-1200 ms par plan
  const rate=1-Math.exp(-dt/tau);
  this.camera.position.lerp(this.position,rate);
  if(this.target)this.focus.lerp(this.target,1-Math.exp(-dt*2));
  this.camera.lookAt(this.focus);}
}
export class VelkoSceneDirector {
 constructor(bus,machine,avatar,camera,speak,environment){Object.assign(this,{bus,machine,avatar,camera,speak,environment});this.active=false;this.time=0;this.next=0;this.marks=[];this.context={seated:true,typing:false,mouse:false,action:'read',inputMode:'READING'};avatar.root.position.set(0,0,2.3);this.task=null;this.ready=false;this.returnStart=null;this.currentAction=null;this.lastSequence=0;this.desk={keyboard:null,mouse:null};}
 schedule(t,fn){this.marks.push({t,fn});}
 finished(){return CLOSED_STATUSES.has(this.task?.status);}
 /** Le moteur a répondu avant l'arrivée au poste : on annule le trajet plutôt
  *  que d'annoncer un travail qui n'aura pas lieu. */
 cancelStaging(){if(this.machine.atWorkstation)return false;this.marks=[];this.next=0;
  this.context.walking=false;this.context.seated=true;this.avatar.root.position.set(0,0,2.3);
  this.avatar.root.rotation.y=0;this.camera.workLocked=false;this.camera.set('conversation');return true;}
 /** Confirmation accordée : la mission repart, VELKO reste au poste. */
 // Une mission bloquée est MISE DE CÔTÉ (reprenable) : VELKO redevient libre
 // pour une nouvelle demande au lieu de rester figé au poste.
 park(){if(!this.active||!['blocked','failed'].includes(this.task?.status))return false;this.active=false;this.marks=[];this.next=0;this.ready=false;this.currentAction=null;this.machine.atWorkstation=false;this.machine.set('IDLE_CONVERSATION');this.camera.workLocked=false;this.camera.set('conversation');this.avatar.root.position.set(0,0,2.3);this.avatar.root.rotation.y=0;this.context.seated=true;this.context.walking=false;this.bus.emit('mission.parked',{taskId:this.task.id,status:this.task.status});return true;}
 /** Présentation d'un rapport : hors mission, VELKO s'assied au poste et le
  *  rapport complet s'affiche sur le moniteur central. */
 presentReport(){if(this.active)return false;this.presenting=true;const a=this.avatar.root;a.position.set(1.4,0,-1.6);a.rotation.y=Math.PI;
  this.context={...this.context,seated:true,walking:false,typing:false,mouse:false,look:0,speaking:false,action:'read',inputMode:'READING'};
  this.machine.set('READING_OUTPUT');this.camera.set('report_screen');return true;}
 endPresentation(){if(!this.presenting)return false;this.presenting=false;if(this.active)return true;const a=this.avatar.root;a.position.set(0,0,2.3);a.rotation.y=0;
  this.context.seated=true;this.context.look=0;this.machine.set('IDLE_CONVERSATION');this.camera.set('conversation');return true;}
 resume(){if(!this.active||!['blocked','failed'].includes(this.task?.status))return false;this.task.status='running';this.machine.setTaskStatus('running');this.completionTime=undefined;this.machine.set('READING_OUTPUT');return true;}
 start(task,options={}){if(this.active)return false;if(this.presenting){this.presenting=false;this.avatar.root.position.set(0,0,2.3);this.avatar.root.rotation.y=0;this.bus.emit('report.dismissed');}this.active=true;this.time=0;this.next=0;this.marks=[];this.ready=false;this.returnStart=null;this.lastSequence=0;this.currentAction=null;this.task={id:options.id||'local',text:task,status:'queued'};this.machine.atWorkstation=false;this.machine.setTaskStatus('queued');this.camera.workLocked=false;this.context={seated:true,typing:false,mouse:false,action:'read',look:0,speaking:false};this.bus.emit('TASK_STARTED',{task,taskId:this.task.id});this.machine.set('LISTENING');this.camera.set('conversation');
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
  // Pendant les phases intermédiaires le moteur annonce simplement la phase :
  // VELKO reste au poste, il ne rentre que sur un achèvement confirmé.
  if(['task.testing','task.retrying','task.waiting_tool'].includes(event.type)){
   this.task.status='running';this.machine.setTaskStatus('running');
   if(this.ready&&this.task.status!=='completed'){this.machine.set('READING_OUTPUT');this.context.inputMode='READING';}
   return true;
  }
  // L'utilisateur est attendu : VELKO reste assis, se tourne vers lui et pose
  // les mains — il ne simule aucune activité pendant que l'on décide.
  if(event.type==='task.waiting_user'||event.type==='task.waiting_confirmation'){
   this.task.status='waiting_user';this.machine.setTaskStatus('waiting_user');
   if(this.ready){this.context.look=0;this.context.typing=false;this.context.mouse=false;this.context.inputMode='NONE';this.context.gesture='ReadScreen';this.machine.set('READING_OUTPUT');}
   return true;
  }
  if(event.type==='task.completed'){
   // Completion can only be delivered by the actual worker, never by a timer.
   if(event.status!=='completed')return false;
   this.task.status='completed';this.machine.setTaskStatus('completed');this.currentAction=null;this.setAction(null);this.cancelStaging();this.machine.set('SUCCESS_STATE');this.camera.workLocked=false;
   this.context.speaking=true;this.completionTime=this.time;this.bus.emit('TASK_COMPLETED',{taskId:this.task.id,result:event.result,progress:100});return true;
  }
  if(event.type==='task.blocked'||event.type==='task.failed'){
   this.task.status=event.type==='task.failed'?'failed':'blocked';this.machine.setTaskStatus(this.task.status);this.currentAction=null;this.setAction(null);if(this.ready)this.machine.set('ERROR_STATE');this.completionTime=undefined;this.bus.emit('mission.blocked',{...event,reason:event.reason||event.message||event.result||'Intervention requise'});return true;
  }
  return true;
 }
setAction(action){this.currentAction=action;this.context.typing=false;this.context.mouse=false;this.context.action='read';this.context.look=0;this.context.gesture='ReadScreen';this.context.inputMode='READING';
   if(!this.ready||this.finished())return;
   // Auto-focus : VELKO ne reste jamais au loin quand il travaille sur un
   // écran réel — la caméra suit le moniteur qu'il utilise.
   if(action){this.context.action=action.action||'read';this.camera.focusMonitor(action.screen??1,this.context.action);}
   if(!action){if(this.task.status!=='blocked'&&this.task.status!=='failed')this.machine.set('READING_OUTPUT');return;}
   const states={code:'WORKING_CODE',terminal:'WORKING_TERMINAL',discord:'WORKING_DISCORD',browser:'WORKING_BROWSER',sheet:'WORKING_TERMINAL',analytics:'WORKING_TERMINAL',blog:'WORKING_CODE',email:'WORKING_CODE',database:'WORKING_TERMINAL',read:'READING_OUTPUT'};
  // Le regard suit l'écran que le routeur a activé : gauche (0), centre (1),
  // droite (2). Une analyse de données occupe l'écran central, un Sheet la droite.
  const monitor=action.screen??1;
  this.context.look=[-1,0,1][monitor]??0;this.context.action=action.action||'read';
  // Reading output or waiting for a process does not cause decorative keystrokes.
  this.context.typing=action.action==='type';this.context.mouse=['click','scroll','move','drag'].includes(action.action);
  // InputMode : guide l'avatar — mains au clavier quand VELKO tape réellement,
  // main à la souris quand il clique, lecture penchée le reste du temps.
  this.context.inputMode=action.action==='type'?'KEYBOARD':this.context.mouse?'MOUSE':(action.action==='read'?'READING':'NONE');
  // Le geste vient du fait réel traduit par le pont ; à défaut, il découle
  // de l'action. Jamais de frappe quand un processus travaille seul.
  this.context.gesture=action.gesture||{type:'TypingNormal',click:'MouseClick',scroll:'MouseScroll',drag:'MouseDrag'}[action.action]||'ReadScreen';
  this.machine.set(states[action.kind]||'READING_OUTPUT');this.bus.emit('action.focus',{...action});}
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
 // Cibles réelles du bureau : mains posées sur le clavier et la souris réels.
 if(this.environment&&this.environment.targets){
  const T=this.environment.THREE,v=new T.Vector3();
  const kb=this.environment.targets.KeyboardTarget,m=this.environment.targets.MouseTarget;
  if(kb){kb.getWorldPosition(v);this.desk.keyboard=v.clone();}if(m){m.getWorldPosition(v);this.desk.mouse=v.clone();}
 }
 this.context.desk=this.desk;
 this.avatar.update(dt,performance.now()/1000,this.machine.state.toLowerCase(),this.context);
 }
}
