import * as THREE from '../vendor/three.module.js';
import {VelkoAvatarController} from './avatar.js';
import {VelkoEnvironmentController} from './environment.js';
import {VelkoEventBus,VelkoStateMachine,VelkoSceneDirector,VelkoCameraDirector} from './core.js';
import {VelkoScreenManager} from './screens.js';
import {VelkoJarvisClient} from './jarvis-client.js';
import {engineFeed} from './engine-feed.js';
const $=s=>document.querySelector(s);
try {
engineFeed();   // ouvert avant les écrans : ils s'y raccrochent au lieu d'ouvrir le leur.
const renderer=new THREE.WebGLRenderer({canvas:$('#world'),antialias:true,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setSize(innerWidth,innerHeight);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
const scene=new THREE.Scene();scene.background=new THREE.Color('#142331');scene.fog=new THREE.Fog('#142331',16,38);const camera=new THREE.PerspectiveCamera(43,innerWidth/innerHeight,.05,70);
const fill=new THREE.HemisphereLight('#cedbea','#30251b',.35);scene.add(fill);
const environment=new VelkoEnvironmentController(THREE,scene),avatar=new VelkoAvatarController(THREE);scene.add(avatar.root);
await avatar.ready;
const bus=new VelkoEventBus(),machine=new VelkoStateMachine(bus),cameras=new VelkoCameraDirector(THREE,camera);cameras.set('conversation');const screens=new VelkoScreenManager(THREE,environment.screens,bus,{camera});
let sound=true,recognition=null;function speak(text){$('#subtitle').textContent=text;if(sound&&'speechSynthesis'in window){speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='fr-FR';u.rate=1.04;const voices=speechSynthesis.getVoices();u.voice=voices.find(v=>v.lang.startsWith('fr')&&/Thomas|Daniel|Henri/.test(v.name))||voices.find(v=>v.lang.startsWith('fr'))||null;speechSynthesis.speak(u);}}
const director=new VelkoSceneDirector(bus,machine,avatar,cameras,speak);
const missionClient=new VelkoJarvisClient(bus,director,screens);missionClient.connect();
const labels={IDLE_CONVERSATION:'À vos côtés',IDLE:'À vos côtés',LISTENING:'Je vous écoute',THINKING:'Je réfléchis',SPEAKING:'En conversation',MOVING_TO_WORKSTATION:'Vers le poste de travail',SITTING:'Installation',WORKING_CODE:'Écriture du fichier réel',WORKING_TERMINAL:'Terminal en activité',WORKING_DISCORD:'Discord en activité',WORKING_BROWSER:'Navigation en cours',READING_OUTPUT:'Lecture / attente du processus',SUCCESS_STATE:'Travail terminé',ERROR_STATE:'Au poste · intervention requise',RETURN_TO_USER:'De retour vers vous'};
bus.on('state.changed',({state})=>{$('#state').textContent=labels[state]||state;$('#wave').classList.toggle('active',state==='SPEAKING'||state==='LISTENING');});
const log=(title,text)=>{const el=document.createElement('div');el.className='event';const t=document.createElement('time');t.textContent=new Date().toLocaleTimeString('fr-FR')+' / '+title;const p=document.createElement('p');p.textContent=text;el.append(t,p);$('#events').append(el);while($('#events').children.length>4)$('#events').firstChild.remove();};
bus.on('TASK_STARTED',e=>{$('#confirm-bar').classList.add('hidden');$('#mission').textContent=e.task;$('#percent').textContent='EN COURS';$('#progress').style.width='0';$('#progress').parentElement.classList.add('indeterminate');});
bus.on('action.focus',a=>log((a.kind||'mission').toUpperCase(),a.label||a.path||'Opération en cours'));
bus.on('engine.notice',e=>{if(e.text)log('MOTEUR',e.text);});
bus.on('mission.progress',e=>{$('#progress').parentElement.classList.remove('indeterminate');$('#progress').style.width=e.progress+'%';$('#percent').textContent=Math.round(e.progress)+' %';});
bus.on('engine.offline',e=>{$('#phase').textContent='Moteur injoignable';$('#subtitle').textContent=e.reason;});
bus.on('engine.online',()=>{if(!director.active)$('#phase').textContent='Moteur connecté';});
bus.on('mission.snapshot',s=>{$('#phase').textContent={running:'Travail en cours',blocked:'Votre réponse est attendue',completed:'Travail terminé',failed:'Erreur du moteur'}[s.status]||s.status;if(s.status==='completed'){$('#progress').parentElement.classList.remove('indeterminate');$('#progress').style.width='100%';$('#percent').textContent='TERMINÉ';}if(s.status==='blocked'||s.status==='failed'){$('#progress').parentElement.classList.remove('indeterminate');$('#percent').textContent='BLOQUÉE';$('#subtitle').textContent=s.result||'Intervention requise. Je reste au poste.';}});
bus.on('backend.error',e=>{$('#phase').textContent='Connexion au moteur interrompue';$('#subtitle').textContent=e.reason;});
async function start(task){if(!task.trim()||director.active)return;recognition?.stop();$('#demo').disabled=true;$('#send').disabled=true;try{await missionClient.start(task);$('#task-input').value='';$('#subtitle').textContent='Votre demande est reçue. Les opérations commenceront au poste de travail.';}catch(e){$('#subtitle').textContent=e.message;$('#demo').disabled=false;$('#send').disabled=false;}}
bus.on('mission.confirmation',c=>{
 const label=[c.action,c.reason].filter(Boolean).join(' — ');
 $('#confirm-text').textContent=(c.message||'VELKO demande votre autorisation.')+(label?' ('+label+')':'');
 $('#confirm-bar').classList.remove('hidden');
 speak(c.speech||c.message||'J’ai besoin de votre autorisation pour continuer.');
});
const answer=approved=>{$('#confirm-bar').classList.add('hidden');$('#demo').disabled=true;$('#send').disabled=true;
 $('#subtitle').textContent=approved?'Autorisation accordée. Je poursuis.':'Autorisation refusée. J’arrête cette action.';
 missionClient.answer(approved);};
$('#confirm-yes').onclick=()=>answer(true);$('#confirm-no').onclick=()=>answer(false);
bus.on('mission.blocked',e=>{if(!missionClient.confirmation)speak(e.reason||'Je ne peux pas aller plus loin sans vous.');});
const release=()=>{$('#demo').disabled=false;$('#send').disabled=false;};bus.on('mission.finished',release);$('#demo').onclick=()=>start($('#task-input').value.trim()||'Fais le point sur l’état du système et de tes connecteurs.');$('#task-form').onsubmit=e=>{e.preventDefault();start($('#task-input').value);};
$('#open-editor').onclick=()=>window.open('workbench.html?pane=editor','velko-editor','popup,width=1100,height=750');
$('#open-terminal').onclick=()=>window.open('workbench.html?pane=terminal','velko-terminal','popup,width=1050,height=700');
for(let i=0;i<11;i++)$('#wave').append(document.createElement('i'));
$('#sound').onclick=()=>{sound=!sound;$('#sound').textContent=sound?'Voix activée':'Voix coupée';if(!sound)speechSynthesis?.cancel();};
$('#mic').onclick=()=>{if(director.active){$('#voice-status').textContent='Une mission est en cours. Attendez le retour de VELKO.';return;}const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){$('#voice-status').textContent='Reconnaissance vocale indisponible ici : utilisez Chrome ou la saisie texte.';return;}if(recognition){recognition.stop();recognition=null;return;}$('#voice-status').textContent='Microphone : le service du navigateur peut traiter votre audio.';recognition=new SR();recognition.lang='fr-FR';recognition.interimResults=true;recognition.onstart=()=>{machine.set('LISTENING');$('#mic').classList.add('recording');};recognition.onresult=e=>{const text=Array.from(e.results).map(r=>r[0].transcript).join(' ');$('#task-input').value=text;if(e.results[e.results.length-1].isFinal)start(text);};recognition.onerror=e=>{$('#voice-status').textContent='Microphone : '+e.error+'. La saisie texte reste disponible.';};recognition.onend=()=>{recognition=null;$('#mic').classList.remove('recording');if(!director.active)machine.set('IDLE_CONVERSATION');};recognition.start();};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{if(!cameras.set(b.dataset.view)){$('#subtitle').textContent='La mission n’est pas terminée : VELKO reste à son poste.';return;}document.querySelectorAll('[data-view]').forEach(v=>v.classList.toggle('active',v===b));});$('#screen-toggle').onclick=()=>$('#screens-panel').classList.toggle('hidden');$('#close-screens').onclick=()=>$('#screens-panel').classList.add('hidden');$('#settings').onclick=()=>$('#info').classList.remove('hidden');$('#close-info').onclick=()=>$('#info').classList.add('hidden');
$('#export-scene').onclick=()=>{$('#export-status').textContent='Les exports sont disponibles dans velko/exports : velko_master.blend, velko_runtime.glb, velko_office.blend et velko_office.glb. Régénération : voir README.';};
let last=performance.now(),elapsed=0,frames=0,frameWindow=0;function frame(now){const dt=Math.min((now-last)/1000,.05);last=now;elapsed+=dt;director.update(dt);environment.update(dt,elapsed);cameras.update(dt);screens.update(dt,elapsed);renderer.render(scene,camera);frames++;frameWindow+=dt;if(frameWindow>1){$('#fps').textContent=Math.round(frames/frameWindow)+' FPS';frames=0;frameWindow=0;$('#clock').textContent=new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}requestAnimationFrame(frame);}requestAnimationFrame(frame);
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);});$('#loading').remove();fetch('/api/status').then(r=>r.json()).then(s=>{$('#phase').textContent=s.ok===false?'Moteur en erreur':'Prêt à vous écouter';}).catch(()=>bus.emit('engine.offline',{reason:'Le moteur VELKO ne répond pas sur ce port.'}));window.velko={scene,renderer,avatar,environment,director,machine,bus,screens,cameras,missionClient,start};
}catch(e){$('#loading').replaceChildren();const msg=document.createElement('span');msg.textContent='Impossible de démarrer la scène : '+e.message;$('#loading').append(msg);console.error(e);}