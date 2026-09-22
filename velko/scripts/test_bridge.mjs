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

// --- Gestes : chaque geste suit un fait réel, jamais l'inverse. -------------
import {VelkoScreenRouter, sourceOf} from '../runtime/screen-router.js';
({c,applied}=client());
c.ingest({type:'terminal.command',data:{command:'npm test',cwd:'/p'}});
c.ingest({type:'terminal.output',data:{text:'FAIL',stream:'stderr'}});
c.ingest({type:'terminal.completed',data:{exit_code:1}});
assert.deepEqual(applied.map(a=>a.gesture),['PressEnter','ReadScreen','ReadScreen']);
// Règle absolue : pas de frappe pendant qu'un processus travaille seul.
assert.deepEqual(applied.map(a=>a.action),['type','read','read']);

({c,applied}=client());
c.ingest({type:'file.opened',data:{path:'/p/a.js'}});
c.ingest({type:'file.changed',data:{path:'/p/a.js'}});
assert.deepEqual(applied.map(a=>a.action),['read','type']);      // ouvrir ≠ écrire
assert.deepEqual(applied.map(a=>a.gesture),['ReadScreen','TypingFast']);
assert.equal(applied[0].screen,0);

// Navigateur reel : la main atteint la souris au clic, puis appuie sur
// l'evenement d'action correspondant. Deux faits reels, deux gestes.
({c,applied}=client());
c.ingest({type:'browser.click',data:{}});
c.ingest({type:'browser.action',data:{kind:'CLICK',target:'Se connecter'}});
c.ingest({type:'browser.scroll',data:{}});
c.ingest({type:'browser.type',data:{}});
c.ingest({type:'browser.loaded',data:{url:'https://x.test'}});
assert.deepEqual(applied.map(a=>a.gesture),
 ['MouseReach','MouseClick','MouseScroll','TypingNormal','ReadScreen']);
assert.deepEqual(applied.map(a=>a.screen),[2,2,2,2,2]);
assert.deepEqual(applied.map(a=>a.action),['click','click','scroll','type','read']);
// Le flux d'image alimente l'ecran, il ne declenche aucun geste.
({c,applied}=client());
c.ingest({type:'browser.frame',data:{jpeg:'xxx'}});
assert.equal(applied.length,0);

// --- Routeur : le troisième écran suit la source réelle, sans choix humain. -
const router=new VelkoScreenRouter();
assert.deepEqual(router.state().panels,['code','terminal','idle']);
assert.equal(sourceOf('terminal.command'),'terminal');
assert.equal(sourceOf('tool.started',{tool:'git.status'}),'git');
assert.equal(sourceOf('conversation.message'),'');            // sans source : rien
router.ingest('ssh.run',{host:'h'});
assert.equal(router.state().panels[2],'ssh');
assert.equal(router.state().focus,2);
router.ingest('file.changed',{path:'/p/a.js'});
assert.equal(router.state().focus,0);
assert.equal(router.state().panels[2],'ssh');                 // la source reste affichée

console.log('PASS: gestes liés aux faits réels; aucune frappe pendant un processus; routage automatique des écrans.');
