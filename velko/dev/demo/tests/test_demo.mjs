import assert from 'node:assert/strict';
import * as THREE from '../vendor/three.module.js';
import {VelkoEventBus,VelkoStateMachine,VelkoSceneDirector,VelkoCameraDirector} from '../runtime/core.js';
function setup(){const bus=new VelkoEventBus(),machine=new VelkoStateMachine(bus);const avatar={root:new THREE.Group(),update(dt,t,state,context){this.last={state,...context};}};const camera=new VelkoCameraDirector(THREE,new THREE.PerspectiveCamera());const director=new VelkoSceneDirector(bus,machine,avatar,camera,()=>{});let ready=0;bus.on('workstation.ready',()=>ready++);return {bus,machine,avatar,camera,director,get ready(){return ready;}};}
function step(d,seconds){for(let i=0;i<seconds*60;i++)d.update(1/60);}
const a=setup();assert(a.director.start('Bot Discord',{id:'actual-task'}));assert.equal(a.director.start('Concurrent'),false);
let p=a.avatar.root.position.clone(),maxDelta=0;for(let i=0;i<14*60;i++){a.director.update(1/60);maxDelta=Math.max(maxDelta,a.avatar.root.position.distanceTo(p));p.copy(a.avatar.root.position);}
assert.equal(a.ready,1);assert(maxDelta<.03);assert(a.machine.atWorkstation);assert.equal(a.machine.state,'READING_OUTPUT');
step(a.director,180);assert(a.director.active);assert.equal(a.machine.state,'READING_OUTPUT');assert(a.avatar.root.position.distanceTo(new THREE.Vector3(1.4,0,-1.6))<1e-9);
assert.equal(a.machine.set('RETURN_TO_USER'),false);assert.equal(a.machine.set('IDLE_CONVERSATION'),false);assert.equal(a.camera.set('conversation'),false);
a.director.sync({id:'actual-task',status:'running',currentAction:{kind:'code',action:'type',screen:0}});assert.equal(a.machine.state,'WORKING_CODE');assert(a.director.context.typing);assert.equal(a.director.context.look,-1);
a.director.sync({id:'actual-task',status:'running',currentAction:{kind:'terminal',action:'read',screen:1}});assert(!a.director.context.typing);assert(!a.director.context.mouse);
a.director.sync({id:'actual-task',status:'running',currentAction:{kind:'discord',action:'scroll',screen:2}});assert.equal(a.machine.state,'WORKING_DISCORD');assert(a.director.context.mouse);assert.equal(a.director.context.look,1);
assert.equal(a.director.receive({taskId:'wrong',type:'task.completed',status:'completed'}),false);
// Un blocage ne termine jamais la mission, même après trois minutes.
a.director.receive({taskId:'actual-task',seq:1,type:'task.blocked',reason:'Credentials missing'});
for(const duration of [30,30,120]){step(a.director,duration);assert.equal(a.machine.state,'ERROR_STATE');assert(a.director.active);assert(a.machine.atWorkstation);assert(a.director.context.seated);assert.equal(a.camera.set('conversation'),false);assert.equal(a.machine.set('RETURN_TO_USER'),false);}
assert.equal(a.director.receive({taskId:'actual-task',seq:2,type:'task.completed'}),false);
assert(a.director.resume());assert.equal(a.machine.state,'READING_OUTPUT');
a.director.receive({taskId:'actual-task',seq:3,type:'task.failed',reason:'Erreur réelle'});step(a.director,180);assert(a.director.active);assert(a.machine.atWorkstation);assert.equal(a.machine.state,'ERROR_STATE');

// Rejouabilité, puis le chemin nominal : réussite annoncée par le moteur seul.
const b=setup();assert(b.director.start('Seconde mission',{id:'second'}));step(b.director,14);assert(b.machine.atWorkstation);
assert.equal(b.director.receive({taskId:'second',seq:1,type:'task.completed',status:'completed',result:'Vérifications réelles passées'}),true);
assert.equal(b.machine.state,'SUCCESS_STATE');step(b.director,12);assert.equal(b.machine.state,'IDLE_CONVERSATION');assert(!b.director.active);
assert(b.avatar.root.position.distanceTo(new THREE.Vector3(0,0,2.3))<.001);assert(b.director.start('Suivante',{id:'next'}));

// Un échec reçu pendant le trajet conserve l'arrivée et l'attente au poste.
const c=setup();assert(c.director.start('Réponse immédiate',{id:'fast'}));step(c.director,1);
c.director.receive({taskId:'fast',seq:1,type:'task.failed',reason:'Moteur injoignable'});
step(c.director,180);assert.equal(c.machine.state,'ERROR_STATE');assert(c.machine.atWorkstation);assert(c.director.active);

// Une confirmation accordée rouvre la mission sans quitter le poste.
const e=setup();assert(e.director.start('Action sensible',{id:'ask'}));step(e.director,14);
e.director.receive({taskId:'ask',seq:1,type:'task.blocked',result:'Autorisation requise'});
assert(!e.director.finished());assert(e.director.resume());assert.equal(e.machine.state,'READING_OUTPUT');
step(e.director,30);assert(e.director.active);assert(e.machine.atWorkstation);

console.log('PASS: continuous movement; 3-minute pending mission stays at desk; action/gesture mapping; stale task rejection; blocked and failed missions remain at desk at 30/60/180 seconds; engine-only completion; early failure preserves arrival; confirmation resumes at desk; replay.');
