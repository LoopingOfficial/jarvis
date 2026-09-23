import * as THREE from '../vendor/three.module.js';
import {VelkoAvatarController} from './avatar.js';
import {VelkoEnvironmentController} from './environment.js';
import {VelkoEventBus,VelkoStateMachine,VelkoSceneDirector,VelkoCameraDirector} from './core.js';
import {VelkoScreenManager} from './screens.js';
import {VelkoJarvisClient} from './jarvis-client.js';
import {VelkoResultPanel} from './result-panel.js';
import {stripMarkdown} from './markdown.js';
import {engineFeed} from './engine-feed.js';
import {VelkoBlockedActionPanel} from './blocked-panel.js';
import {VelkoSettingsCenter} from './settings-center.js';
const $=s=>document.querySelector(s);
try {
engineFeed();   // ouvert avant les écrans : ils s'y raccrochent au lieu d'ouvrir le leur.
const renderer=new THREE.WebGLRenderer({canvas:$('#world'),antialias:true,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setSize(innerWidth,innerHeight);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
const scene=new THREE.Scene();scene.background=new THREE.Color('#142331');scene.fog=new THREE.Fog('#142331',16,38);const camera=new THREE.PerspectiveCamera(43,innerWidth/innerHeight,.05,70);
const fill=new THREE.HemisphereLight('#cedbea','#30251b',.35);scene.add(fill);
const environment=new VelkoEnvironmentController(THREE,scene),avatar=new VelkoAvatarController(THREE);scene.add(avatar.root);
await avatar.ready;
const bus=new VelkoEventBus(),machine=new VelkoStateMachine(bus),cameras=new VelkoCameraDirector(THREE,camera);cameras.set('conversation');const screens=new VelkoScreenManager(THREE,environment.screens,bus,{camera});
let voicePrefs={};try{voicePrefs=JSON.parse(localStorage.getItem('velko.voice')||'{}');}catch{}
let sound=voicePrefs.enabled!==false,recognition=null;function speak(text){$('#subtitle').textContent=text;if(sound&&'speechSynthesis'in window){speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='fr-FR';u.rate=voicePrefs.rate??1.04;u.volume=voicePrefs.volume??1;const voices=speechSynthesis.getVoices();u.voice=(voicePrefs.voice&&voices.find(v=>v.name===voicePrefs.voice))||voices.find(v=>v.lang.startsWith('fr')&&/Thomas|Daniel|Henri/.test(v.name))||voices.find(v=>v.lang.startsWith('fr'))||null;speechSynthesis.speak(u);}}
const director=new VelkoSceneDirector(bus,machine,avatar,cameras,speak,environment);
const missionClient=new VelkoJarvisClient(bus,director,screens);missionClient.connect();
const resultPanel=new VelkoResultPanel(bus,missionClient);
// --- Paramètres & récupération : une action bloquée n'immobilise jamais VELKO.
const settingsCenter=new VelkoSettingsCenter(bus,{voice:{
 get:()=>({...voicePrefs,enabled:sound}),
 set:p=>{voicePrefs={...voicePrefs,...p};sound=p.enabled!==false;$('#sound').textContent=sound?'Voix activée':'Voix coupée';if(!sound)speechSynthesis?.cancel();try{localStorage.setItem('velko.voice',JSON.stringify(voicePrefs));}catch{}},
 test:()=>speak('Bonjour. Voici la voix de VELKO avec vos réglages.')}});
const blockedPanel=new VelkoBlockedActionPanel(bus,{
 onConfigure:type=>settingsCenter.open({n8n:'n8n',ssh:'ssh',sftp:'ssh',mysql:'db',postgres:'db',discord:'discord',google:'google',email:'email'}[type]||'connectors'),
 onResume:b=>{missionClient.resume(b.taskId,b.task).catch(e=>{$('#subtitle').textContent=e.message;});},
 onRetry:b=>start(b.task||''),
 onChangeMethod:b=>{$('#task-input').value=b.task||'';$('#task-input').focus();$('#subtitle').textContent='Reformulez la demande ou indiquez une autre méthode, puis envoyez.';},
 onCancel:b=>{if(b.taskId)fetch(`/api/tasks/${encodeURIComponent(b.taskId)}/cancel`,{method:'POST'}).catch(()=>{});director.park();$('#subtitle').textContent='Mission annulée. Que voulez-vous faire ?';}});
bus.on('connector.ready',({type})=>{if(blockedPanel.connectorReady(type))speak('Le connecteur est prêt. Vous pouvez reprendre la mission.');});
$('#gear').onclick=()=>settingsCenter.toggle();
addEventListener('keydown',e=>{
 if((e.metaKey||e.ctrlKey)&&(e.key===','||e.code==='Comma'||e.code==='KeyM'&&e.key===',')){e.preventDefault();settingsCenter.toggle();return;}
 if(e.key==='Escape'){if(settingsCenter.isOpen)settingsCenter.close();else if(blockedPanel.isOpen())blockedPanel.close();else $('#info').classList.add('hidden');}
});
let activityMode='USER';
const labels={IDLE_CONVERSATION:'À vos côtés',IDLE:'À vos côtés',LISTENING:'Je vous écoute',THINKING:'Je réfléchis',SPEAKING:'En conversation',MOVING_TO_WORKSTATION:'Vers le poste de travail',SITTING:'Installation',WORKING_CODE:'Écriture du fichier réel',WORKING_TERMINAL:'Terminal en activité',WORKING_DISCORD:'Discord en activité',WORKING_BROWSER:'Navigation en cours',READING_OUTPUT:'Lecture / attente du processus',SUCCESS_STATE:'Travail terminé',ERROR_STATE:'Au poste · intervention requise',RETURN_TO_USER:'De retour vers vous',AWAITING_USER_DECISION:'Votre décision est attendue'};
bus.on('state.changed',({state})=>{$('#state').textContent=labels[state]||state;$('#wave').classList.toggle('active',state==='SPEAKING'||state==='LISTENING');});
const log=(title,text)=>{const el=document.createElement('div');el.className='event';const t=document.createElement('time');t.textContent=new Date().toLocaleTimeString('fr-FR')+' / '+title;const p=document.createElement('p');p.textContent=text;el.append(t,p);$('#events').append(el);while($('#events').children.length>4)$('#events').firstChild.remove();};
// Mode ACTIVITÉ : USER = langage humain (défaut), DEV = libellé technique réel.
const humanActivity=k=>({code:'Lectures et écritures dans les fichiers',terminal:'Terminal en activité',browser:'Navigation en cours',discord:'Discord en activité',sheet:'Google Sheets · données réelles',analytics:'Analyse des données Brainrot',blog:'Blog · contenu réel',email:'Email · campagne réelle',database:'Base de données · requêtes réelles',read:'Lecture / attente du processus'}[k]||'Activité en cours');
$('#activity-mode').onclick=()=>{activityMode=activityMode==='USER'?'DEV':'USER';$('#activity-mode').textContent=activityMode;$('#activity-mode').classList.toggle('on',activityMode==='DEV');$('#events').replaceChildren();log(activityMode==='USER'?'ACTIVITÉ':'MOTEUR',activityMode==='USER'?'L’activité s’affiche en langage humain. VELKO exécute les opérations réelles sur les écrans.':'Détails techniques des opérations réelles.');};
bus.on('TASK_STARTED',e=>{stopDecisionListening();$('#confirm-bar').classList.add('hidden');$('#mission').textContent=e.task;$('#percent').textContent='EN COURS';$('#progress').style.width='0';$('#progress').parentElement.classList.add('indeterminate');resultPanel.hide();});
bus.on('action.focus',a=>log(activityMode==='USER'?(humanActivity(a.kind).split('·')[0].trim().toUpperCase()):(a.kind||'mission').toUpperCase(),
 activityMode==='USER'?(a.label&&!a.label.includes('/')?a.label:(humanActivity(a.kind))):(a.label||a.path||'Opération en cours')));
bus.on('engine.notice',e=>{if(e.text)log(activityMode==='USER'?'TRAVAIL EN COURS':'MOTEUR',e.text);});
bus.on('mission.progress',e=>{$('#progress').parentElement.classList.remove('indeterminate');$('#progress').style.width=e.progress+'%';$('#percent').textContent=Math.round(e.progress)+' %';});
bus.on('engine.offline',e=>{$('#phase').textContent='Moteur injoignable';$('#subtitle').textContent=e.reason;});
bus.on('engine.online',()=>{if(!director.active)$('#phase').textContent='Moteur connecté';});
bus.on('mission.snapshot',s=>{$('#phase').textContent={running:'Travail en cours',blocked:'Votre réponse est attendue',completed:'Travail terminé',failed:'Erreur du moteur'}[s.status]||s.status;if(s.status==='completed'){$('#progress').parentElement.classList.remove('indeterminate');$('#progress').style.width='100%';$('#percent').textContent='TERMINÉ';$('#subtitle').textContent='Mission terminée. VELKO revient vers vous.';}if(s.status==='blocked'||s.status==='failed'){$('#progress').parentElement.classList.remove('indeterminate');$('#percent').textContent='BLOQUÉE';$('#subtitle').textContent=(s.recovery?.cause)||s.result||'Intervention requise.';release();if(!s.confirmation)blockedPanel.show({...s,missionId:s.id});}});
bus.on('backend.error',e=>{$('#phase').textContent='Connexion au moteur interrompue';$('#subtitle').textContent=e.reason;});
async function start(task){if(!task.trim())return;if(director.active&&['blocked','failed'].includes(director.task?.status))director.park();if(director.active){$('#subtitle').textContent='Une mission est en cours. Attendez le retour de VELKO.';return;}recognition?.stop();$('#demo').disabled=true;$('#send').disabled=true;try{await missionClient.start(task);$('#task-input').value='';$('#subtitle').textContent='Votre demande est reçue. Les opérations commenceront au poste de travail.';}catch(e){$('#subtitle').textContent=e.message;$('#demo').disabled=false;$('#send').disabled=false;}}
bus.on('mission.confirmation',c=>{
 const label=[c.action,c.reason].filter(Boolean).join(' — ');
 $('#confirm-text').textContent=(c.message||'VELKO demande votre autorisation.')+(label?' ('+label+')':'');
 $('#confirm-bar').classList.remove('hidden');
 speak(c.speech||c.message||'J’ai besoin de votre autorisation pour continuer.');
});
// Le rapport final : VELKO résume à voix haute en une phrase, puis le panneau
// premium affiche le détail structuré. Le Markdown brut ne touche plus la scène.
const shortSummary=text=>{const clean=String(text).replace(/[#*_`>`]/g,'').replace(/\s+/g,' ').trim();const first=clean.split('\n').find(Boolean)||clean;return first.length>160?first.slice(0,157)+'…':first;};
bus.on('TASK_COMPLETED',e=>{resultPanel.currentTaskId=e.taskId;resultPanel.progress('Terminé — préparation du rapport…');setTimeout(()=>{resultPanel.show({result:e.result,status:'completed'});speak(stripMarkdown(e.result)||'Mission terminée. Le rapport détaillé est à droite, et les actions sont disponibles.');},350);});
$('#rp-close').onclick=()=>resultPanel.hide();
const answer=approved=>{$('#confirm-bar').classList.add('hidden');$('#demo').disabled=true;$('#send').disabled=true;
 $('#subtitle').textContent=approved?'Autorisation accordée. Je poursuis.':'Autorisation refusée. J’arrête cette action.';
 missionClient.answer(approved);};
$('#confirm-yes').onclick=()=>answer(true);$('#confirm-no').onclick=()=>answer(false);
bus.on('mission.blocked',e=>{if(!missionClient.confirmation)speak(e.reason||'Je ne peux pas aller plus loin sans vous.');});
const release=()=>{$('#demo').disabled=false;$('#send').disabled=false;};bus.on('mission.finished',()=>{release();if(resultPanel.pendingContext())startDecisionListening();});$('#demo').onclick=()=>start($('#task-input').value.trim()||'Fais le point sur l’état du système et de tes connecteurs.');$('#task-form').onsubmit=e=>{e.preventDefault();start($('#task-input').value);};
$('#open-editor').onclick=()=>window.open('workbench.html?pane=editor','velko-editor','popup,width=1100,height=750');
$('#open-terminal').onclick=()=>window.open('workbench.html?pane=terminal','velko-terminal','popup,width=1050,height=700');
for(let i=0;i<11;i++)$('#wave').append(document.createElement('i'));
$('#sound').textContent=sound?'Voix activée':'Voix coupée';$('#sound').onclick=()=>{sound=!sound;voicePrefs.enabled=sound;try{localStorage.setItem('velko.voice',JSON.stringify(voicePrefs));}catch{}$('#sound').textContent=sound?'Voix activée':'Voix coupée';if(!sound)speechSynthesis?.cancel();};
// --- Décision en attente : la voix ET le clic répondent ensemble, l'état de
// VELKO le dit, et l'indicateur vocal reste allumé tant qu'on attend. --------
let decisionListening=false,listeningIntent=false;
function setMicIndicator(on){
 $('#mic').classList.toggle('recording',on);
 $('#voice-status').textContent=on?'🎙 Je vous écoute…':'Texte ou microphone · conversation en français';
}
function startDecisionListening(){
 if(decisionListening||!resultPanel.pendingContext())return;
 decisionListening=true;listeningIntent=true;
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!SR){setMicIndicator(false);return;}
 if(recognition){try{recognition.stop();}catch{}}
 recognition=new SR();recognition.lang='fr-FR';recognition.interimResults=false;
 recognition.onstart=()=>{setMicIndicator(true);machine.set('AWAITING_USER_DECISION');};
 recognition.onresult=e=>{
  const t=Array.from(e.results).map(r=>r[0].transcript).join(' ').trim();
  $('#task-input').value=t;
  const reply=resultPanel.voice(t);
  if(reply!==null){speak(reply);}
 };
 recognition.onerror=()=>{};
 recognition.onend=()=>{recognition=null;
  if(decisionListening)setTimeout(startDecisionListening,700);   // ré-écoute continue
 };
 try{recognition.start();}catch{}
}
function stopDecisionListening(){
 decisionListening=false;listeningIntent=false;
 if(recognition){try{recognition.stop();}catch{}}
 recognition=null;setMicIndicator(false);
}
bus.on('action.pending',ctx=>{
 if(!director.active&&ctx){startDecisionListening();$('#demo').disabled=true;
  setTimeout(()=>{if(decisionListening)speak(`Voici quelques actions proposées. ${ctx.message}`);},900);}
});
// Une décision prise ou une confirmation donnée libère l'écoute.
bus.on('action.selected',stopDecisionListening);
bus.on('action.decided',()=>{const pending=resultPanel.pendingContext();if(!pending)stopDecisionListening();});
bus.on('action.allDeclined',()=>stopDecisionListening());
bus.on('action.await',ctx=>{speak(ctx.message)});
$('#mic').onclick=()=>{
 if(director.active&&!['blocked','failed'].includes(director.task?.status)){$('#voice-status').textContent='Une mission est en cours. Attendez le retour de VELKO.';return;}
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!SR){$('#voice-status').textContent='Reconnaissance vocale indisponible ici : utilisez Chrome ou la saisie texte.';return;}
 if(decisionListening){stopDecisionListening();return;}
 if(recognition){recognition.stop();recognition=null;return;}
 $('#voice-status').textContent='Microphone : le service du navigateur peut traiter votre audio.';
 listeningIntent=true;
 recognition=new SR();recognition.lang='fr-FR';recognition.interimResults=true;recognition.onstart=()=>{machine.set('LISTENING');$('#mic').classList.add('recording');};recognition.onresult=e=>{const text=Array.from(e.results).map(r=>r[0].transcript).join(' ');$('#task-input').value=text;if(e.results[e.results.length-1].isFinal)start(text);};recognition.onerror=e=>{$('#voice-status').textContent='Microphone : '+e.error+'. La saisie texte reste disponible.';};recognition.onend=()=>{recognition=null;$('#mic').classList.remove('recording');if(!director.active&&!resultPanel.pendingContext())machine.set('IDLE_CONVERSATION');};recognition.start();};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{if(!cameras.set(b.dataset.view)){$('#subtitle').textContent='La mission n’est pas terminée : VELKO reste à son poste.';return;}document.querySelectorAll('[data-view]').forEach(v=>v.classList.toggle('active',v===b));});$('#screen-toggle').onclick=()=>$('#screens-panel').classList.toggle('hidden');$('#close-screens').onclick=()=>$('#screens-panel').classList.add('hidden');$('#settings').onclick=()=>$('#info').classList.remove('hidden');$('#close-info').onclick=()=>$('#info').classList.add('hidden');
$('#export-scene').onclick=()=>{$('#export-status').textContent='Les exports sont disponibles dans velko/exports : velko_master.blend, velko_runtime.glb, velko_office.blend et velko_office.glb. Régénération : voir README.';};
// --- Clic sur un moniteur : plan rapproché sur cet écran + retour au travail.
const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();
$('#world').addEventListener('click',e=>{
 if(e.target.dataset?.ignore)return;
 const rect=$('#world').getBoundingClientRect();
 pointer.x=((e.clientX-rect.left)/rect.width)*2-1;pointer.y=-(((e.clientY-rect.top)/rect.height)*2-1);
 ray.setFromCamera(pointer,camera);
 const hit=ray.intersectObjects(environment.screens,false)[0];
 if(!hit)return;
 const i=environment.screens.indexOf(hit.object);
 const closeup=['work_left_screen','work_center_screen','work_right_screen'][i];
 cameras.wide('manual');
 cameras.set(closeup);
 cameraControlMode='manual';
 $('#return-to-work').classList.remove('hidden');
 // Aligne aussi le focus des rôles sur l'écran cliqué.
 screens.bus?.emit('action.focus',{screen:i});
});
$('#return-to-work').onclick=()=>{
 cameras.resumeAuto();cameraControlMode='auto';
 $('#return-to-work').classList.add('hidden');
};
// Tant qu'une mission tourne, l'auto-focus garde la main sur les changements
// d'écran du routeur; le mode manuel n'est débloqué que par un clic explicite.
let cameraControlMode='auto';
let last=performance.now(),elapsed=0,frames=0,frameWindow=0;function frame(now){const dt=Math.min((now-last)/1000,.05);last=now;elapsed+=dt;director.update(dt);environment.update(dt,elapsed);cameras.update(dt);screens.update(dt,elapsed);renderer.render(scene,camera);frames++;frameWindow+=dt;if(frameWindow>1){$('#fps').textContent=Math.round(frames/frameWindow)+' FPS';frames=0;frameWindow=0;$('#clock').textContent=new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}requestAnimationFrame(frame);}requestAnimationFrame(frame);
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);});$('#loading').remove();fetch('/api/status').then(r=>r.json()).then(s=>{$('#phase').textContent=s.ok===false?'Moteur en erreur':'Prêt à vous écouter';}).catch(()=>bus.emit('engine.offline',{reason:'Le moteur VELKO ne répond pas sur ce port.'}));window.velko={scene,renderer,avatar,environment,director,machine,bus,screens,cameras,missionClient,start,settingsCenter,blockedPanel};
}catch(e){$('#loading').replaceChildren();const msg=document.createElement('span');msg.textContent='Impossible de démarrer la scène : '+e.message;$('#loading').append(msg);console.error(e);}