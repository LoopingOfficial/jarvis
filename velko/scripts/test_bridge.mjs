/** Contrat de traduction entre les événements du moteur et la scène VELKO. */
import assert from 'node:assert/strict';
import {VelkoEventBus} from '../runtime/core.js';
import {classifyTool,VelkoJarvisClient} from '../runtime/jarvis-client.js';

assert.deepEqual(classifyTool('shell.run'),{kind:'terminal',screen:1});
assert.deepEqual(classifyTool('web.search'),{kind:'browser',screen:2});
assert.deepEqual(classifyTool('discord.send'),{kind:'discord',screen:2});
assert.deepEqual(classifyTool('file.write'),{kind:'code',screen:0});
assert.deepEqual(classifyTool('mémoire'),{kind:'read',screen:1});

function client(){
 const bus=new VelkoEventBus(),applied=[];
 const director={active:true,ready:true,setAction(a){applied.push(a);},receive(){},task:{id:'t'}};
 const c=new VelkoJarvisClient(bus,director,{routeTask(){}});c.id='t';
 return {c,applied,bus};
}
let {c,applied}=client();
c.ingest({type:'tool.started',data:{tool:'shell.run'}});
c.ingest({type:'code.file.modified',data:{absolute_path:'/tmp/a.py'}});
c.ingest({type:'tool.completed',data:{}});
c.ingest({type:'conversation.message',data:{}});          // sans effet sur la scène
assert.deepEqual(applied.map(a=>a&&a.kind),['terminal','code',null]);
assert.equal(applied[1].action,'type');
assert.equal(applied[1].screen,0);

// Avant l'installation au poste, l'action est mise en attente, pas perdue.
({c,applied}=client());c.director.ready=false;
c.ingest({type:'tool.started',data:{tool:'web.search'}});
assert.equal(applied.length,0);assert.equal(c.pendingAction.kind,'browser');
c.director.ready=true;c.bus.emit('workstation.ready',{taskId:'t'});
assert.equal(applied.length,1);assert.equal(applied[0].kind,'browser');

// La progression n'est relayée que lorsque le moteur en fournit une.
({c,applied}=client());let seen=null;c.bus.on('mission.progress',e=>{seen=e.progress;});
c.ingest({type:'agent.progress',data:{progress:42}});assert.equal(seen,42);
c.ingest({type:'agent.progress',data:{}});assert.equal(seen,42);

console.log('PASS: tool routing per screen; buffered action before the desk; progress only when reported.');
