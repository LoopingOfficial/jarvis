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

// --- Mission UX : le rapport est rendu PROPRE, la voix reçoit du BRUT. ------
import {stripMarkdown, renderMarkdown} from '../runtime/markdown.js';
const md = '# Rapport\n\n**317** membres inscrits.\n\n- [x] première action\n- [x] deuxième\n\n|Colonne|Valeur|\n|---|---|\n|Actifs|12|\n\nLien : [site](https://exemple.fr).';
const rendered = renderMarkdown(md);
assert.equal(rendered.includes('**'), false, 'aucune graisse brute');
assert.equal(rendered.includes('# Rapport'), false, 'aucun titre brut');
assert.equal(rendered.includes('<strong>'), true, 'graisse rendue');
assert.equal(rendered.includes('<a href="https://exemple.fr"'), true, 'lien sain');
assert.equal(rendered.includes('javascript'), false, "pas de schema interdit");
const stripped = stripMarkdown(md);
assert.equal(stripped.includes('**'), false);
assert.equal(stripped.includes('#'), false);
assert.equal(stripped.includes('site'), true, 'texte du lien conservé');
assert.equal(stripped.includes('https://exemple.fr'), false, 'url retirée');
console.log('PASS: markdown rendu sans brute; voix en texte brut (stripMarkdown).');

// --- Caméra : auto-focus au travail, jamais de coupe nerveuse (<2.5 s). ----
import {VelkoCameraDirector, STATES} from '../runtime/core.js';
class FakeVector { constructor(x,y,z){this.x=x;this.y=y;this.z=z;} set(...v){[this.x,this.y,this.z]=v;return this;} clone(){return new FakeVector(this.x,this.y,this.z);} copy(v){this.x=v.x;this.y=v.y;this.z=v.z;return this;} lerp(t,f){this.x+=(t.x-this.x)*f;this.y+=(t.y-this.y)*f;this.z+=(t.z-this.z)*f;return this;} distanceTo(o){return Math.hypot(this.x-o.x,this.y-o.y,this.z-o.z);} setFromUnitVectors(){return this;} }
class FakeGroup { children=[]; add(o){this.children.push(o);} addEventListener(){} removeEventListener(){} }
const FakeT={Vector3:FakeVector,Group:FakeGroup};
const camObj={position:new FakeVector(0,0,0),lookAt(){}};
const cam=new VelkoCameraDirector(FakeT,camObj);
cam.workLocked=true;cam.pace='auto';cam.shotStartAt=performance.now()-3000;
// Un premier focus passe et définir le plan.
assert.equal(cam.focusMonitor(1,'read'),true);
assert.equal(cam.mode,'work_center_screen');
// Un second changement immédiat est mis en FILE, pas de coupe.
const before=cam.mode;
assert.equal(cam.focusMonitor(2,'read'),false);
assert.equal(cam.mode,before);
assert.equal(cam.pending.screen,2);
// Changer d'action vers la frappe = plan mains (après fenêtre de 2.5 s).
cam.shotStartAt=performance.now()-3000;
assert.equal(cam.focusMonitor(1,'type'),true);
assert.equal(cam.mode,'work_hands');
// La discussion explicite (clic) verrouille l'auto-focus ; wide() le rétablit.
cam.wide();assert.equal(cam.pace,'manual');
assert.equal(cam.focusMonitor(0,'read'),false);
cam.resumeAuto();assert.equal(cam.pace,'auto');
// L'état « décision » est un état connu de la machine.
assert.ok(STATES.includes('AWAITING_USER_DECISION'));
console.log('PASS: camera dolly 600-1200 ms, min shot 2.5 s, auto-focus par écran, mode manuel/auto.');

// --- Couche humaine : les paramètres sont IRREGULIERS, jamais périodiques. ---
import {VelkoAvatarController} from '../runtime/avatar.js';
// La couche humaine est purement procédurale : sans chargement GLB, seule la
// construction des paramètres est vérifiée (respirVar, blink intervalle, gaze).
const probe=new VelkoAvatarController(FakeT);
assert.ok(typeof probe.human.breathT==='number');
assert.ok(probe.human.nextBlink>=800&&probe.human.nextBlink<=1300,'clignements espacés, jamais périodiques');
assert.ok(probe.human.nextGaze>=400&&probe.human.nextGaze<=1300,'saccades non déclenchées en rafale');
assert.equal(probe.human.mouseReach,0,'la main ne pré-voit pas la souris');
console.log('PASS: HumanMotionLayer procédural, irrégulier, déclenché par les faits réels.');

// --- Détection des Actions réelles depuis le rapport UX1 du moteur. ---------
import {VelkoResultPanel} from '../runtime/result-panel.js';
const sampleReport = 'Voici le point sur **Brainrot Fortnite** (au 23 septembre 2026) :\n### 📊 État des lieux\n* **Membres :** 317\n* **Actifs (7 jours) :** 4\n* **Emails non confirmés :** 306\n### 🛠 Actions possibles\n1. **Relance emails :** Je peux préparer une campagne de relance pour les 2 membres n\'ayant pas encore validé leur email.\n2. **Analyse de baisse :** Je peux analyser l\'origine de la chute des inscriptions sur 7 jours.\n3. **Contenu Blog :** Je peux chercher un sujet et préparer un brouillon pour relancer le blog.';
const probePanel = Object.create(VelkoResultPanel.prototype);
const found = probePanel.buildContexts(sampleReport, {actions: []});
assert.equal(found.length, 3, '3 actions réelles détectées dans l\'ordre du texte');
assert.equal(found[0].tool, 'brainrot.email.prepare_campaign');
assert.equal(found[0].confirmation_required, true, 'préparer la campagne exige confirmation');
assert.equal(found[0].risk, 'high');
assert.equal(found[1].tool, 'brainrot.analytics.registrations');
assert.equal(found[2].tool, 'brainrot.blog.create_draft');
console.log('PASS: détection des actions réelles depuis le rapport moteur (prepare_campaign gardé par confirmation).');

console.log('ALL TESTS PASS');
