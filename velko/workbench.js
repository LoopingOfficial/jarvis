import {engineFeed} from './runtime/engine-feed.js';
/** Les trois moniteurs de VELKO, alimentés par le flux réel du moteur.
 *  Rien n'est reconstitué ici : chaque ligne vient d'un événement ou du
 *  magasin de documents du moteur. */
const params=new URLSearchParams(location.search);
const pane=params.get('pane');
document.title=pane==='editor'?'VELKO · Éditeur':pane==='terminal'?'VELKO · Terminal':'VELKO · Atelier';
if(['editor','terminal','application'].includes(pane))document.body.classList.add(pane+'-only');
const $=id=>document.getElementById(id);
const docs=new Map(); let active='';
const MAX_TERMINAL=40000;

$('save').hidden=true;
$('content').disabled=true;
$('saved').textContent='Lecture seule : les fichiers sont écrits par VELKO, pas depuis cet écran.';

function mark(){for(const b of $('files').children)b.classList.toggle('active',b.dataset.path===active);}
function show(path){
 const doc=docs.get(path);if(!doc)return;
 active=path;$('filename').textContent=doc.filename||path;$('content').value=doc.content||'';mark();
}
function register(doc,{focus=false}={}){
 const path=doc.absolute_path||doc.filename||doc.path;if(!path)return;
 docs.set(path,{...docs.get(path),...doc,filename:doc.filename||path.split('/').pop()});
 const known=[...$('files').children].some(b=>b.dataset.path===path);
 if(!known){
  const b=document.createElement('button');b.textContent=doc.filename||path.split('/').pop();
  b.dataset.path=path;b.title=path;b.onclick=()=>show(path);$('files').append(b);
 }
 if(focus||!active)show(path);else if(path===active)show(path);
}
function term(text){
 if(!text)return;
 const node=$('terminal');node.textContent+=text;
 if(node.textContent.length>MAX_TERMINAL)node.textContent=node.textContent.slice(-MAX_TERMINAL);
 node.scrollTop=node.scrollHeight;
}
const stamp=()=>new Date().toLocaleTimeString('fr-FR');

fetch('/api/code/documents').then(r=>r.json()).then(s=>{
 for(const doc of s.documents||[])register(doc,{focus:doc.document_id===s.activeDocumentId});
}).catch(()=>{$('saved').textContent='Magasin de documents injoignable.';});

engineFeed().subscribe(frame=>{
 const type=frame.type||'',data=frame.data||{};
 if(type==='feed.state'){$('status').textContent=data.online?'Moteur connecté':'Flux interrompu — reconnexion';return;}
 if(type.startsWith('code.file.')){
  if(type==='code.file.deleted'||type==='code.file.closed'){
   const path=data.absolute_path||data.filename||data.path;docs.delete(path);
   [...$('files').children].filter(b=>b.dataset.path===path).forEach(b=>b.remove());
   if(active===path){active='';$('content').value='';$('filename').textContent='Sélectionner un fichier';}
   return;
  }
  register(data,{focus:type!=='code.file.opened'||!active});
  if(type==='code.file.error')$('saved').textContent='Erreur sur '+(data.filename||'')+' : '+(data.error||'');
  return;
 }
 if(type==='tool.started'||type==='tool.called'){
  term(`\n[${stamp()}] $ ${data.tool||data.name||data.tool_id||'outil'}`+
       (data.summary?` — ${data.summary}`:'')+'\n');return;
 }
 if(type==='tool.completed'||type==='tool.failed'||type==='tool.denied'){
  const text=data.output||data.result||data.error||'';
  term((typeof text==='string'?text:JSON.stringify(text,null,1))+
       `\n[${type==='tool.completed'?'terminé':type==='tool.denied'?'refusé':'échec'}]\n`);return;
 }
 if(type==='llm.delta')return;
 if(type==='jarvis.state'||type==='agent.started'||type==='agent.completed'||type==='agent.idle'){
  $('application-state').textContent='Moteur VELKO · '+(data.state||type.split('.')[1]);
  if(data.reason||data.detail)$('application-detail').textContent=data.reason||data.detail;
  return;
 }
 if(type.startsWith('connector.')){
  $('application-state').textContent='Connecteur '+(data.connector_id||data.name||'')+' · '+type.split('.')[1];
  return;
 }
 if(type==='jarvis.activity'||type==='activity.trace'||type==='system.warning'||type==='system.info'){
  const text=data.detail||data.message||data.text;if(text)$('result').textContent=text;
 }
});
