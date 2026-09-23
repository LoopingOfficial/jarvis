/** VELKO SETTINGS — tiroir latéral discret (⚙, Cmd/Ctrl+,). La scène reste
 *  visible derrière. Chaque état affiché vient d'un vrai test côté moteur ;
 *  les secrets ne sont jamais affichés ni journalisés (aperçu ••••xxxx). */
const SECTIONS=[['general','Général'],['ai','IA & modèles'],['connectors','Connecteurs'],['n8n','n8n'],
 ['ssh','Serveurs / SSH'],['db','Bases de données'],['discord','Discord'],['google','Google'],
 ['browser','Navigateur'],['email','Email'],['voice','Voix'],['security','Sécurité'],['diagnostic','Diagnostic']];
const TYPES={ssh:['ssh','sftp','ftp'],db:['mysql','postgres'],discord:['discord'],google:['google'],email:['email','imap','smtp']};
const STATE={CONNECTED:['●','Connecté','ok'],DEGRADED:['◐','Dégradé','warn'],WAITING_USER:['◌','Action requise','wait'],
 DISCONNECTED:['○','Déconnecté','off'],ERROR:['✕','Erreur','err'],NOT_CONFIGURED:['○','Non configuré','none']};
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const btn=(label,fn,cls='')=>{const b=el('button',cls,label);b.type='button';b.onclick=fn;return b;};
const ago=ts=>{if(!ts)return 'jamais';const s=Math.round(Date.now()/1000-ts);return s<60?`il y a ${s}s`:s<3600?`il y a ${Math.round(s/60)} min`:new Date(ts*1000).toLocaleString('fr-FR');};
async function api(url,opts={}){
 const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts,body:opts.body?JSON.stringify(opts.body):undefined});
 const d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false)throw Error(d.error||`Erreur ${r.status}`);return d;}

export class VelkoSettingsCenter {
 constructor(bus,{voice}={}){
  this.bus=bus;this.voice=voice;this.section='connectors';this.data=null;this.types=null;this.stream='Connexion…';
  this.node=el('aside','vs-drawer');this.node.setAttribute('aria-label','Paramètres VELKO');this.node.setAttribute('aria-hidden','true');
  this.node.innerHTML=`<div class="vs-head"><span class="eyebrow">VELKO</span><b>PARAMÈTRES</b><button class="vs-close" title="Fermer (Échap)">✕</button></div>
   <nav class="vs-nav"></nav><div class="vs-content" tabindex="-1"></div>`;
  document.body.append(this.node);
  this.node.querySelector('.vs-close').onclick=()=>this.close();
  const nav=this.node.querySelector('.vs-nav');
  for(const [id,label] of SECTIONS){const b=btn(label,()=>this.show(id),'vs-tab');b.dataset.s=id;nav.append(b);}
  bus.on('engine.online',()=>this.stream='Connecté');bus.on('engine.offline',()=>this.stream='Interrompu');
 }
 get isOpen(){return this.node.classList.contains('open');}
 toggle(){this.isOpen?this.close():this.open();}
 open(section){this.node.classList.add('open');this.node.setAttribute('aria-hidden','false');this.show(section||this.section);}
 close(){this.node.classList.remove('open');this.node.setAttribute('aria-hidden','true');}
 content(){return this.node.querySelector('.vs-content');}
 async show(section){
  this.section=section;this.node.querySelectorAll('.vs-tab').forEach(t=>t.classList.toggle('active',t.dataset.s===section));
  const c=this.content();c.replaceChildren(el('p','vs-muted','Chargement de l’état réel…'));
  try{if(!this.data||section==='connectors'||section==='diagnostic')this.data=await api('/api/settings/center');}
  catch(e){c.replaceChildren(el('p','vs-errt','Moteur injoignable : '+e.message));return;}
  if(this.section!==section)return;
  c.replaceChildren();const render={general:this.general,ai:this.ai,connectors:this.connectors,n8n:this.n8n,browser:this.browser,
   voice:this.voiceSection,security:this.security,diagnostic:this.diagnostic}[section];
  if(render)await render.call(this,c);else this.typed(c,section);
 }
 title(c,t,sub){c.append(el('h3','vs-h',t));if(sub)c.append(el('p','vs-muted',sub));}
 pill(state){const [i,l,k]=STATE[state]||STATE.ERROR;return el('span','vs-pill vs-'+k,`${i} ${l}`);}

 // --- Connecteurs --------------------------------------------------------
 connectors(c){this.title(c,'Connecteurs','Chaque état provient d’un test réel. Vérification automatique toutes les 90 s.');
  c.append(btn('Tout tester maintenant',async e=>{e.target.disabled=true;e.target.textContent='Tests en cours…';
   try{await api('/api/connectors/health/check',{method:'POST',body:{}});this.data=null;}catch{}this.show('connectors');}));
  for(const r of this.data.connectors)c.append(this.row(r));}
 row(r){
  const box=el('div','vs-row');const top=el('div','vs-row-top');
  const name=el('div','vs-name');name.append(el('b',null,r.name),el('small',null,`${r.label||r.type} · ${r.type}`));
  top.append(name,this.pill(r.state));box.append(top);
  box.append(el('p','vs-meta',[r.latency_ms!=null?`latence ${r.latency_ms} ms`:null,`vérifié ${ago(r.last_check)}`,r.stale?'(état non revérifié)':null].filter(Boolean).join(' · ')));
  const result=el('p','vs-result');const det=el('p','vs-detail hidden',r.detail||'Aucun détail.');
  const acts=el('div','vs-acts');
  acts.append(btn(r.id?'Configurer':'Ajouter',()=>r.type==='n8n'?this.show('n8n'):this.form(box,r)));
  if(r.id)acts.append(btn('Tester',async e=>{e.target.disabled=true;result.textContent='Test en cours…';
   try{const d=await api('/api/connectors/health/check',{method:'POST',body:{id:r.id}});const x=d.results[0];
    result.textContent=['CONNECTED','DEGRADED'].includes(x.state)?`Connexion réussie · latence ${x.latency_ms} ms`:`Échec · ${x.detail}`;
    result.className='vs-result '+(['CONNECTED','DEGRADED'].includes(x.state)?'vs-okt':'vs-errt');top.replaceChild(this.pill(x.state),top.lastChild);
    if(['CONNECTED','DEGRADED'].includes(x.state))this.bus.emit('connector.ready',{type:r.type});}
   catch(err){result.textContent='Échec · '+err.message;}e.target.disabled=false;}));
  acts.append(btn('Détails',()=>det.classList.toggle('hidden')));
  box.append(acts,result,det);return box;}
 async form(box,r){
  box.querySelector('.vs-form')?.remove();
  if(!this.types)this.types=(await api('/api/connectors')).types;
  const spec=this.types.find(t=>t.type===r.type);if(!spec){box.append(el('p','vs-errt','Type non configurable ici.'));return;}
  const current=r.id?(await api('/api/connectors/'+encodeURIComponent(r.id))).connector:{config:{},secret_fields:{}};
  const f=el('form','vs-form');const inputs={};
  for(const fd of spec.fields){
   const lab=el('label',null,fd.label+(fd.required?' *':''));let input;
   if(fd.secret){const s=(current.secret_fields||{})[fd.key]||{};
    if(s.configured){const mask=el('span','vs-mask',s.preview||'••••••••');const edit=btn('Modifier',()=>{mask.replaceWith(input);edit.remove();input.focus();});
     input=el('input');input.type='password';input.autocomplete='new-password';lab.append(mask,edit);inputs[fd.key]=input;f.append(lab);continue;}
    input=el('input');input.type='password';input.autocomplete='new-password';}
   else if(fd.kind==='bool'){input=el('input');input.type='checkbox';input.checked=(current.config[fd.key]??fd.default)!==false;}
   else if(fd.kind==='select'){input=el('select');fd.options.forEach((o,i)=>{const op=el('option',null,fd.options_labels?.[i]||o);op.value=o;input.append(op);});input.value=current.config[fd.key]??fd.default??'';}
   else{input=el('input');input.type=fd.kind==='number'?'number':'text';input.value=current.config[fd.key]??fd.default??'';input.placeholder=fd.placeholder||'';}
   inputs[fd.key]=input;lab.append(input);f.append(lab);}
  const msg=el('p','vs-result');
  f.append(el('div','vs-acts'),msg);f.lastChild.previousSibling.append(btn('Enregistrer',async()=>{
   const config={};for(const fd of spec.fields){const i=inputs[fd.key];if(!i)continue;
    if(fd.secret){if(i.value)config[fd.key]=i.value;continue;}
    config[fd.key]=fd.kind==='bool'?i.checked:fd.kind==='number'?Number(i.value):i.value;}
   msg.textContent='Enregistrement…';
   try{const d=r.id?await api('/api/connectors/'+encodeURIComponent(r.id),{method:'PUT',body:{config}})
     :await api('/api/connectors',{method:'POST',body:{type:r.type,name:r.name,config}});
    Object.values(inputs).forEach(i=>{if(i.type==='password')i.value='';});
    const t=await api('/api/connectors/health/check',{method:'POST',body:{id:d.connector.id}});const x=t.results[0];
    msg.textContent=['CONNECTED','DEGRADED'].includes(x.state)?`Enregistré · connexion réussie (${x.latency_ms} ms)`:`Enregistré · test : ${x.detail}`;
    if(['CONNECTED','DEGRADED'].includes(x.state))this.bus.emit('connector.ready',{type:r.type});this.data=null;}
   catch(e){msg.textContent='Échec : '+e.message;}},'vs-primary'),btn('Fermer',()=>f.remove()));
  box.append(f);}
 typed(c,section){const label=SECTIONS.find(s=>s[0]===section)[1];this.title(c,label);
  const rows=this.data.connectors.filter(r=>TYPES[section].includes(r.type));
  if(!rows.length)rows.push({id:'',type:TYPES[section][0],name:label,state:'NOT_CONFIGURED',detail:'Aucun connecteur configuré.'});
  rows.forEach(r=>c.append(this.row(r)));}

 // --- n8n ----------------------------------------------------------------
 async n8n(c){
  this.title(c,'n8n','Connecteur réel vers l’API publique n8n. La clé est stockée dans le coffre chiffré.');
  const st=el('div','vs-card');c.append(st);st.append(el('p','vs-muted','Test de l’instance…'));
  let s;try{s=(await api('/api/n8n/status')).n8n;}catch(e){s={state:'ERROR',detail:e.message};}
  st.replaceChildren();const grid=el('dl','vs-grid');const add=(k,v)=>{if(v===undefined||v==='')return;grid.append(el('dt',null,k),el('dd',null,String(v)));};
  const head=el('div','vs-row-top');head.append(el('b',null,'État'),this.pill(s.state));st.append(head);
  add('Instance',s.instance);if(s.configured)add('Version',s.version||'non exposée');add('Workflows',s.workflows);add('Actifs',s.active);add('Inactifs',s.inactive);
  add('Latence',s.latency_ms!=null?s.latency_ms+' ms':'');add('Dernière vérification',s.checked_at?ago(s.checked_at):'');
  st.append(grid);if(s.state!=='CONNECTED'&&s.detail)st.append(el('p','vs-errt',s.detail));
  const acts=el('div','vs-acts');st.append(acts);
  if(['CONNECTED','DEGRADED'].includes(s.state))acts.append(btn('Voir les workflows',()=>this.workflows(c),'vs-primary'));
  acts.append(btn('Test connexion',()=>this.show('n8n')));
  // Formulaire
  const f=el('form','vs-form');f.onsubmit=e=>e.preventDefault();
  const field=(label,input)=>{const l=el('label',null,label);l.append(input);f.append(l);return input;};
  const url=field('URL n8n',Object.assign(el('input'),{type:'url',value:s.url||'',placeholder:'https://n8n.exemple.com'}));
  const keyLab=el('label',null,'Clé API');const key=Object.assign(el('input'),{type:'password',autocomplete:'new-password',placeholder:'Collez la clé API n8n'});
  if(s.api_key?.configured){const mask=el('span','vs-mask',s.api_key.preview||'••••••••');const edit=btn('Modifier',()=>{mask.replaceWith(key);edit.remove();key.focus();});keyLab.append(mask,edit);}else keyLab.append(key);f.append(keyLab);
  const timeout=field('Timeout (s)',Object.assign(el('input'),{type:'number',min:3,max:120,value:s.timeout||20}));
  const ssl=field('Vérification SSL',Object.assign(el('input'),{type:'checkbox',checked:s.verify_ssl!==false}));
  const msg=el('p','vs-result');const fa=el('div','vs-acts');f.append(fa,msg);
  const save=async()=>{msg.textContent='Enregistrement puis test réel…';
   try{const d=await api('/api/n8n/configure',{method:'POST',body:{url:url.value.trim(),api_key:key.value.trim(),timeout:Number(timeout.value),verify_ssl:ssl.checked}});
    key.value='';const n=d.n8n;
    if(['CONNECTED','DEGRADED'].includes(n.state)){msg.className='vs-result vs-okt';msg.textContent=`Connexion réussie · latence ${n.latency_ms} ms · ${n.workflows} workflows`;
     this.bus.emit('connector.ready',{type:'n8n'});this.data=null;setTimeout(()=>this.show('n8n'),900);}
    else{msg.className='vs-result vs-errt';msg.textContent=`Échec · ${n.detail}`;}}
   catch(e){msg.className='vs-result vs-errt';msg.textContent='Échec · '+e.message;}};
  fa.append(btn('Tester & enregistrer',save,'vs-primary'));
  c.append(el('h4','vs-h4','Configurer'),f);}
 async workflows(c){
  c.replaceChildren();this.title(c,'Workflows n8n','Liste réelle de votre instance.');
  const q=Object.assign(el('input','vs-search'),{type:'search',placeholder:'Rechercher (nom, tags, nodes, description)…'});
  const filters=el('div','vs-filters');let filter='all',rows=[],errors=null;
  const list=el('div','vs-wf-list');c.append(btn('← Retour n8n',()=>this.show('n8n')),q,filters,list);
  for(const [id,l] of [['all','Tous'],['active','Actifs'],['inactive','Inactifs'],['error','Erreur récente']])
   filters.append(btn(l,async e=>{filter=id;filters.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b===e.target));
    if(id==='error'&&!errors){try{errors=new Set((await api('/api/n8n/executions?status=error&limit=50')).executions.map(x=>String(x.workflow_id)));}catch{errors=new Set();}}draw();},id==='all'?'active':''));
  const draw=()=>{list.replaceChildren();const shown=rows.filter(w=>filter==='all'||(filter==='active'?w.active:filter==='inactive'?!w.active:errors?.has(w.workflow_id)));
   list.append(el('p','vs-muted',`N8N — ${shown.length} workflow${shown.length>1?'s':''}`));
   for(const w of shown){const it=el('div','vs-wf');const h=el('div','vs-row-top');
    h.append(el('b',null,(w.active?'● ':'○ ')+w.name),el('span','vs-pill '+(w.active?'vs-ok':'vs-off'),w.active?'Actif':'Inactif'));
    it.append(h,el('p','vs-meta',`ID ${w.workflow_id} · modifié ${w.updated_at?new Date(w.updated_at).toLocaleString('fr-FR'):'—'}`));
    const extra=el('div','vs-detail hidden');const a=el('div','vs-acts');
    a.append(btn('Voir détails',async()=>{extra.classList.toggle('hidden');if(extra.dataset.loaded)return;extra.textContent='Chargement…';
     try{const d=await api('/api/n8n/workflows/'+encodeURIComponent(w.workflow_id));extra.dataset.loaded=1;extra.replaceChildren(
      el('p',null,d.workflow.description||'Pas de description.'),el('p',null,'Nodes : '+d.workflow.node_list.map(n=>n.name).join(', ')),
      el('p',null,d.executions[0]?`Dernière exécution : ${new Date(d.executions[0].started_at).toLocaleString('fr-FR')} — ${d.executions[0].status}`:'Aucune exécution enregistrée.'));
      it.dataset.open=d.open_url;this.bus.emit('n8n.focus',{workflow:d.workflow,executions:d.executions});}catch(e){extra.textContent=e.message;}}));
    a.append(btn('Ouvrir',async()=>{let u=it.dataset.open;if(!u){try{u=(await api('/api/n8n/workflows/'+encodeURIComponent(w.workflow_id))).open_url;}catch{}}if(u)window.open(u,'_blank','noopener');}));
    a.append(btn('Lancer',()=>this.launch(it,w)));
    it.append(a,extra);list.append(it);}};
  let timer;q.oninput=()=>{clearTimeout(timer);timer=setTimeout(load,300);};
  const load=async()=>{list.replaceChildren(el('p','vs-muted','Chargement des workflows réels…'));
   try{const d=await api('/api/n8n/workflows'+(q.value?'?q='+encodeURIComponent(q.value):''));
    if(d.error){list.replaceChildren(el('p','vs-errt',d.error));return;}rows=d.workflows;draw();}
   catch(e){list.replaceChildren(el('p','vs-errt',e.message));}};
  load();}
 /** Lancer = même chemin confirmé que la voix : jamais d'exécution sans accord. */
 async launch(it,w){it.querySelector('.vs-confirm')?.remove();
  const card=el('div','vs-confirm');it.append(card);card.append(el('p','vs-muted','Analyse du workflow…'));
  try{const d=await api(`/api/n8n/workflows/${encodeURIComponent(w.workflow_id)}/action`,{method:'POST',body:{action:'run'}});
   card.replaceChildren();if(!d.needs_confirmation){card.append(el('p','vs-errt',d.recovery?.cause||d.response));return;}
   const cf=d.needs_confirmation;card.append(el('b',null,'LANCER LE WORKFLOW'),el('p',null,w.name),el('p','vs-muted',cf.reason));
   const a=el('div','vs-acts');card.append(a);const res=el('p','vs-result');card.append(res);
   const answer=async ok=>{a.remove();res.textContent=ok?'Exécution…':'Annulé.';
    try{const r=await api('/api/confirm',{method:'POST',body:{confirmation_id:cf.id,approved:ok}});res.textContent=r.recovery?.cause||r.response;}catch(e){res.textContent=e.message;}};
   a.append(btn('Exécuter',()=>answer(true),'vs-primary'),btn('Annuler',()=>answer(false)));}
  catch(e){card.replaceChildren(el('p','vs-errt',e.message));}}

 // --- Autres sections ----------------------------------------------------
 async general(c){this.title(c,'Général');let s={};try{s=await (await fetch('/api/status')).json();}catch{}
  const g=el('dl','vs-grid');const add=(k,v)=>g.append(el('dt',null,k),el('dd',null,String(v??'—')));
  add('Moteur',s.ok===false?'En erreur':'En ligne');add('Version',s.version||s.build||'—');add('Flux d’événements',this.stream);
  add('Raccourcis','⌘/Ctrl + ,  ouvrir · Échap  fermer');c.append(g);}
 ai(c){this.title(c,'IA & modèles','Configuration réellement active. Aucun modèle n’est modifié automatiquement ici.');
  const g=el('dl','vs-grid');const a=this.data.ai;const add=(k,v)=>g.append(el('dt',null,k),el('dd',null,(v||'—').replace(/^ollama:/,'')));
  add('Défaut',a.default_model);add('Code',a.coding_model);add('Rapide',a.fast_model);add('Raisonnement',a.reasoning_model);
  add('Secours',a.fallback_model||(a.auto_fallback?'automatique':''));c.append(g);
  c.append(el('h4','vs-h4',`Modèles disponibles (${this.data.models.length})`));const ul=el('ul','vs-list');
  this.data.models.slice(0,30).forEach(m=>ul.append(el('li',null,m.label)));c.append(ul);}
 browser(c){this.title(c,'Navigateur');const b=this.data.browser;const g=el('dl','vs-grid');
  const add=(k,v)=>g.append(el('dt',null,k),el('dd',null,String(v||'—')));
  const h=el('div','vs-row-top');h.append(el('b',null,'Navigateur intégré'),this.pill(b.available?'CONNECTED':'DISCONNECTED'));c.append(h);
  add('Session',b.active?'active':'inactive');add('État',b.state);add('Page',b.title||b.url);c.append(g);}
 voiceSection(c){this.title(c,'Voix','Réglages de lecture de VELKO dans ce navigateur. La configuration moteur n’est pas modifiée.');
  const v=this.voice?.get()||{};const f=el('form','vs-form');f.onsubmit=e=>e.preventDefault();
  const field=(label,input)=>{const l=el('label',null,label);l.append(input);f.append(l);return input;};
  const on=field('Voix activée',Object.assign(el('input'),{type:'checkbox',checked:v.enabled!==false}));
  f.append(el('p','vs-muted',`Moteur TTS configuré : ${this.data.voice.tts_provider||'navigateur'} (${this.data.voice.voice||'défaut'})`));
  const sel=field('Voix du navigateur',el('select'));const fill=()=>{sel.replaceChildren(el('option',null,'Automatique (français)'));sel.firstChild.value='';
   (window.speechSynthesis?.getVoices()||[]).filter(x=>x.lang.startsWith('fr')).forEach(x=>{const o=el('option',null,`${x.name} (${x.lang})`);o.value=x.name;sel.append(o);});sel.value=v.voice||'';};
  fill();if(window.speechSynthesis)speechSynthesis.onvoiceschanged=fill;
  const vol=field('Volume',Object.assign(el('input'),{type:'range',min:0,max:1,step:.05,value:v.volume??1}));
  const rate=field('Vitesse',Object.assign(el('input'),{type:'range',min:.6,max:1.6,step:.05,value:v.rate??1.04}));
  const mic=field('Microphone',el('select'));mic.append(Object.assign(el('option',null,'Micro par défaut du système'),{value:''}));
  navigator.mediaDevices?.enumerateDevices?.().then(ds=>ds.filter(d=>d.kind==='audioinput'&&d.deviceId!=='default').forEach((d,i)=>{const o=el('option',null,d.label||`Micro ${i+1} (autorisez le micro pour voir son nom)`);o.value=d.deviceId;mic.append(o);mic.value=v.mic||'';})).catch(()=>{});
  f.append(el('p','vs-muted','La reconnaissance vocale utilise le service du navigateur (micro par défaut).'));
  const apply=()=>this.voice?.set({enabled:on.checked,voice:sel.value,volume:Number(vol.value),rate:Number(rate.value),mic:mic.value});
  [on,sel,vol,rate,mic].forEach(i=>i.onchange=apply);
  const a=el('div','vs-acts');a.append(btn('Tester la voix',()=>{apply();this.voice?.test();},'vs-primary'));f.append(a);c.append(f);}
 security(c){this.title(c,'Sécurité');const secrets=this.data.connectors.flatMap(r=>Object.entries(r.secret_fields||{}).filter(([,s])=>s.configured).map(([k,s])=>[r.name,k,s.preview]));
  const ul=el('ul','vs-list');[['Coffre','Secrets chiffrés dans le SecretVault, jamais renvoyés en clair.'],
   ['Lecture','Autonome (lister, consulter, rechercher).'],['Lancer un workflow','Confirmation obligatoire.'],
   ['Activer / désactiver','Confirmation obligatoire.'],['Modifier / supprimer','Confirmation forte ; non exposé dans cette version.']]
   .forEach(([k,v])=>{const li=el('li');li.append(el('b',null,k+' — '),document.createTextNode(v));ul.append(li);});c.append(ul);
  c.append(el('h4','vs-h4',`Secrets enregistrés (${secrets.length})`));const g=el('dl','vs-grid');
  secrets.forEach(([n,k,p])=>g.append(el('dt',null,`${n} · ${k}`),el('dd','vs-mask',p||'••••••••')));c.append(g);}
 diagnostic(c){this.title(c,'Diagnostic');
  const rows=[...this.data.diagnostics.slice(0,1),{name:'Event stream',state:this.stream==='Connecté'?'CONNECTED':'DISCONNECTED',detail:this.stream},...this.data.diagnostics.slice(1)];
  for(const r of rows){const d=el('div','vs-row');const h=el('div','vs-row-top');h.append(el('b',null,r.name),this.pill(r.state));d.append(h,el('p','vs-meta',r.detail||''));c.append(d);}}
}
