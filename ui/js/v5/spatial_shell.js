/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_shell.js
   Le shell spatial : scène, navigation, Obsidian Brain, avatar, command bar,
   contextes. Il RÉIMPLANTE le DOM fonctionnel existant (mêmes ids, mêmes
   écouteurs, mêmes appels API) — aucune logique n'est réécrite.

   Mode d'interface :
     localStorage.JARVIS_UI_MODE = 'spatial_v5' | 'legacy_v4'   (défaut V5)
     ?ui=legacy_v4 / ?ui=spatial_v5 force le mode pour une session.
   En cas d'échec du montage, on bascule automatiquement sur V4.
   ========================================================================== */
(function () {
  'use strict';

  const MODE_KEY = 'JARVIS_UI_MODE';
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const lerp = (a,b,t)=>a+(b-a)*t;
  const byId = (id)=>document.getElementById(id);
  const reduced = ()=>!!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const dprOf = ()=>clamp(window.devicePixelRatio||1,1,2);

  function readMode(){
    try{
      const q=new URLSearchParams(location.search).get('ui');
      if(q==='legacy_v4'||q==='spatial_v5'){ localStorage.setItem(MODE_KEY,q); return q; }
      return localStorage.getItem(MODE_KEY) || 'spatial_v5';
    }catch(_){ return 'spatial_v5'; }
  }

  /* ------------------------------------------------------------ contextes */
  /* La hiérarchie Brain / Avatar se LIT : qui domine change avec la tâche. */
  const LAYOUT = {
    home:  {brain:{x:.585,y:.45,s:.355,dim:0},  avatar:{x:.215,y:.55,s:.27,op:1,dim:0}, work:false, chat:false, greet:true},
    chat:  {brain:{x:.90,y:.30,s:.11,dim:.35},  avatar:{x:.12,y:.58,s:.20,op:1,dim:0},  work:false, chat:true,  greet:false},
    voice: {brain:{x:.88,y:.28,s:.13,dim:.45},  avatar:{x:.46,y:.53,s:.38,op:1,dim:0},  work:false, chat:true,  greet:false},
    data:  {brain:{x:.055,y:.88,s:.075,dim:.15},avatar:{x:.08,y:.62,s:.15,op:0,dim:1},  work:true,  chat:false, greet:false},
    sync:  {brain:{x:.50,y:.42,s:.34,dim:0},    avatar:{x:.10,y:.60,s:.15,op:.2,dim:.8},work:true,  chat:false, greet:false},
    work:  {brain:{x:.055,y:.88,s:.075,dim:.2}, avatar:{x:.08,y:.62,s:.15,op:0,dim:1},  work:true,  chat:false, greet:false},
  };
  /* Chaque page existante est rattachée à un contexte visuel. */
  const PAGE_CONTEXT = {
    command:'home', chat:'chat', conversations:'chat',
    analyses:'data', projects:'work', finance:'work', marketing:'work', social:'work',
    servers:'sync', workflows:'work', tasks:'work', calendar:'work',
    agents:'work', memory:'work', knowledge:'work', brain:'work', learning:'work',
    tools:'work', terminal:'work', code:'work', settings:'work',
    aicore:'work', 'avatar-studio':'voice',
  };

  /* Rail : les entrées de la maquette, chacune vers une vraie page. */
  const ICONS = {
    home:'<circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="8.6" opacity=".5"/>',
    chat:'<path d="M20 15a2 2 0 0 1-2 2H8l-4 3V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
    brain:'<path d="M12 3a4 4 0 0 0-4 4 3 3 0 0 0-1 5.8V15a4 4 0 0 0 8 0v-2.2A3 3 0 0 0 16 7a4 4 0 0 0-4-4z"/>',
    agents:'<circle cx="12" cy="5" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M12 7 6.4 15.6M12 7l5.6 8.6" opacity=".6"/>',
    tools:'<path d="M14.7 6.3a4 4 0 0 0 5 5L15 16l-3 3-4-4 3-3z"/>',
    sync:'<path d="M20 12a8 8 0 0 1-13.6 5.7M4 12a8 8 0 0 1 13.6-5.7"/><path d="M4 5v4h4M20 19v-4h-4"/>',
    workspace:'<path d="M4 18V9M10 18V4M16 18v-6M21 21H3"/>',
    voice:'<path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    settings:'<circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/>',
  };
  const RAIL = [
    ['home','HOME','command'], ['chat','CHAT','chat'], ['brain','BRAIN','memory'],
    ['agents','AGENTS','agents'], ['tools','TOOLS','tools'], ['sync','SYNC','servers'],
    ['workspace','WORKSPACE','code'], ['voice','VOICE','avatar-studio'], ['settings','SETTINGS','settings'],
  ];
  const SUBMENUS = {
    home:[['ACCUEIL'],['command','Command Center']],
    chat:[['CONVERSATION'],['chat','Chat'],['__voice','Mode Vocal']],
    brain:[['BRAIN ATLAS'],['memory','Brain & Mémoire'],['knowledge','Connaissances'],['conversations','Conversations']],
    agents:[['AGENTS & TÂCHES'],['agents','Agents IA'],['tasks','Tâches'],['projects','Projets'],
      ['COURRIER'],['__mail','Kanban Courrier']],
    tools:[['OUTILS'],['tools','Catalogue d\'outils'],['terminal','Terminal'],['calendar','Calendrier'],['learning','Apprentissage']],
    sync:[['SYNCHRONISATION'],['servers','Sites & Serveurs'],['workflows','Automatisations']],
    workspace:[['ESPACE DE TRAVAIL'],['code','Fichiers & Code'],['analyses','Observatoire / Analyses'],['__workspace','Analysis Workspace']],
    voice:[['VOIX & AVATAR'],['avatar-studio','Avatar Studio'],['settings|voice','Voix & TTS']],
    system:[['SYSTÈME'],['terminal','Terminal'],['aicore','AI Core'],['servers','Infrastructure'],['finance','Finance'],['marketing','Marketing'],['social','Réseaux sociaux']],
    settings:[['PARAMÈTRES'],['settings|general','Général'],['settings|voice','Voix & TTS'],
      ['settings|appearance','Apparence'],['settings|connectors','Connecteurs'],
      ['settings|llm','Modèles LLM'],['avatar-studio','Avatar Studio'],['settings|developer','Developer'],
      ['DEVELOPER'],['__ui|legacy_v4','Interface : revenir à V4'],['__ui|spatial_v5','Interface : Spatial OS V5']],
  };
  const VOICE_MODE = 'avatar-studio';

  const Spatial = {
    BUILD_ID:'JARVIS_SPATIAL_OS_V5',
    mode:'spatial_v5',
    booted:false,
    context:'home',
    L:LAYOUT.home,
    Lc:JSON.parse(JSON.stringify(LAYOUT.home)),
    mx:0,my:0,tx:0,ty:0,
    listening:false,
    avatar:null,
    brain:null,

    /* ------------------------------------------------------------- boot */
    boot(){
      if(this.booted) return true;
      this.mode = readMode();
      if(this.mode !== 'spatial_v5') return false;
      document.documentElement.setAttribute('data-jarvis-ui','spatial_v5');
      try{
        this.buildScene();
        this.relocate();
        this.mountBrain();
        this.buildRail();
        this.buildHud();
        this.bindCommandBar();
        this.bindApp();
        this.mountAvatar();
        window.JarvisSpatialEvents?.init?.();
        this.startLoop();
        this.refreshStatus();
        setInterval(()=>this.refreshStatus(), 8000);
        this.booted = true;
        document.documentElement.setAttribute('data-v5-ready','1');
        console.log('[JARVIS UI]', this.BUILD_ID, 'spatial shell ready');
        return true;
      }catch(err){
        console.error('[JARVIS UI V5] montage impossible — bascule sur legacy_v4', err);
        this.fallback();
        return false;
      }
    },

    /** Bascule contrôlée : V4 reprend la main, rien n'est perdu. */
    fallback(){
      try{ byId('v5Scene')?.remove(); byId('v5Portal')?.remove();
           document.querySelectorAll('.v5-view,.v5-work,.v5-cmd,.v5-hud,.v5-rail,.v5-mods,.v5-greet,.v5-activity')
             .forEach(n=>n.remove()); }catch(_){ /* rien à nettoyer */ }
      document.documentElement.removeAttribute('data-jarvis-ui');
      byId('app')?.classList.remove('v5-legacy-host');
      try{ localStorage.setItem(MODE_KEY,'legacy_v4'); }catch(_){ /* stockage indisponible */ }
      window.JarvisV4?.boot?.();
    },

    /* ------------------------------------------------------------ scène */
    buildScene(){
      const app = byId('app');
      if(!app) throw new Error('#app introuvable');
      app.classList.add('v5-legacy-host');

      const frag = document.createElement('div');
      frag.innerHTML = `
        <div class="v5-scene" id="v5Scene">
          <div class="v5-sky" data-depth="2"><canvas id="v5Stars"></canvas></div>
          <div class="v5-horizon" data-depth="5"></div>
          <div class="v5-avatar" id="v5Avatar" data-depth="9">
            <div class="v5-avatar-glow"></div>
            <canvas id="v5AvatarCv"></canvas>
            <div class="v5-avatar-fb" id="v5AvatarFb" hidden>
              <svg viewBox="0 0 120 140" fill="none" stroke="rgba(157,244,255,.45)" stroke-width="1.2">
                <ellipse cx="60" cy="44" rx="25" ry="30"/>
                <path d="M18 140c2-32 18-46 42-46s40 14 42 46"/>
              </svg>
            </div>
          </div>
          <div class="v5-brain-wrap" id="v5BrainWrap" data-depth="14">
            <canvas id="v5BrainCanvas"></canvas>
            <div class="v5-mini-meta" id="v5MiniMeta" hidden>
              <span class="k">BRAIN</span>
              <b id="v5MiniState">IDLE</b>
              <em id="v5MiniDetail"></em>
            </div>
          </div>
          <div class="v5-atmos"></div><div class="v5-grain"></div>
        </div>
        <canvas id="v5Leads"></canvas>
        <header class="v5-hud" id="v5Hud"></header>
        <nav class="v5-rail" id="v5Rail" aria-label="Navigation JARVIS"></nav>
        <div id="v5Zones"></div>
        <div class="v5-greet" id="v5Greet">
          <h1>Bonsoir, <span id="v5Operator">Opérateur</span>.</h1>
          <p id="v5Sub">Tous les systèmes sont prêts</p>
          <div class="st"><s></s><span id="v5State">IDLE</span><s style="transform:scaleX(-1)"></s></div>
        </div>
        <div id="v5Sats"></div>
        <aside class="v5-rpanel" id="v5RPanel" aria-label="Contexte JARVIS">
          <button class="rp-brain" id="v5RpBrain" aria-label="Ouvrir le Brain Atlas">
            <span class="k">BRAIN</span>
            <b id="v5RpState">IDLE</b>
            <em id="v5RpStats"></em>
          </button>
          <section class="rp-agent">
            <span class="k">AGENT ACTIF</span>
            <b id="v5RpAgent">Aucune tâche en cours</b>
            <em id="v5RpAgentAct"></em>
          </section>
          <section class="rp-ctx">
            <span class="k">CONTEXTE</span>
            <b id="v5RpCtx">—</b>
          </section>
        </aside>
        <section class="v5-view v5-chat" id="v5Chat"></section>
        <section class="v5-work" id="v5Work"></section>
        <div class="v5-mods" id="v5Mods"></div>
        <div id="v5Cards"></div>
        <div class="v5-activity" id="v5Activity" aria-live="polite"></div>
        <div class="v5-task" id="v5Task" hidden>
          <i id="v5TaskDot"></i>
          <div>
            <b id="v5TaskLabel"></b>
            <span id="v5TaskTitle"></span>
          </div>
          <em id="v5TaskMeta"></em>
          <div class="v5-steps" id="v5TaskSteps" hidden></div>
        </div>
        <div class="v5-cmd" id="v5Cmd"><canvas id="v5Wave"></canvas></div>
        <div id="v5Portal"></div>`;
      while(frag.firstElementChild) document.body.appendChild(frag.firstElementChild);
      this.seedStars();
      addEventListener('resize', ()=>{ this.seedStars(); this.closePop(); });
      addEventListener('pointermove', (e)=>{
        this.tx=(e.clientX/innerWidth-.5)*2; this.ty=(e.clientY/innerHeight-.5)*2;
        window.ObsidianBrain?.aim(this.tx,this.ty);
        this.avatar?.lookAt?.({x:this.tx,y:this.ty});
      });
      const rpBrain=byId('v5RpBrain');
      if(rpBrain) rpBrain.addEventListener('click',()=>window.App?.goto('memory'));
    },

    /* ------------------------------------------ réimplantation du DOM réel */
    relocate(){
      const work = byId('v5Work');
      const pageWrap = byId('pageWrap');
      if(pageWrap && work) work.appendChild(pageWrap);

      const chat = byId('v5Chat');
      const convLog = byId('convLog');
      if(convLog && chat) chat.appendChild(convLog);

      const cmd = byId('v5Cmd');
      const convForm = byId('convForm');
      if(convForm && cmd){
        cmd.appendChild(convForm);
        const kbd=document.createElement('span'); kbd.className='kbd'; kbd.textContent='⌘K';
        convForm.querySelector('.composer-actions')?.prepend(kbd);
      }
      const input = byId('convInput');
      if(input) input.setAttribute('placeholder','Demandez quelque chose à JARVIS…');

      // La barre de commande change de hauteur (saisie multi-lignes, mode
      // écoute). Le bandeau de tâche s'ancre dessus via --cmdh plutôt que sur
      // une constante : sans ça, la checklist passe derrière le composeur.
      if(cmd && typeof ResizeObserver === 'function'){
        const sync = () => document.documentElement.style.setProperty(
          '--cmdh', Math.round(cmd.getBoundingClientRect().height) + 'px');
        new ResizeObserver(sync).observe(cmd);
        sync();
      }
    },

    mountBrain(){
      const api = window.ObsidianBrain;
      if(!api) throw new Error('ObsidianBrain absent');
      this.brain = api.mount(byId('v5BrainCanvas'));
      // Étiquettes des zones, posées hors de la matière.
      const host = byId('v5Zones');
      this.zoneEls = {};
      for(const k in api.ZONES){
        const el=document.createElement('div');
        el.className='v5-zone'; el.textContent=k;
        host.appendChild(el); this.zoneEls[k]=el;
      }
      window.addEventListener('jarvis:brain-state',(e)=>{
        const n=byId('v5State'); if(n) n.textContent=e.detail.state;
        this.refreshMiniMeta(e.detail);
      });
      this.refreshMiniMeta({state:'IDLE'});
    },

    /* ----------------------------------------------------------- avatar */
    async mountAvatar(){
      try{
        // Les imports dynamiques portent eux aussi une version : sans elle, un
        // module modifie continue d'etre servi depuis le cache du navigateur.
        const V = '?v=v2.3-gate';
        const mod = await import('/js/avatar/jarvis_avatar_3d.js' + V);
        const src = await import('/js/avatar/avatar_source.js' + V);
        // L'UI ne dépend d'aucun modèle précis : l'asset est remplaçable.
        // Le choix et la VERSION viennent d'avatar_source.js — sans quoi cette
        // surface chargeait son propre GLB, en dur et sans version, donc
        // insensible à `?avatar=` et servi depuis le cache après publication.
        const modelUrl = (window.JARVIS_AVATAR_MODEL) || src.resolveAvatarUrl();
        const a = new mod.JarvisAvatar3D(byId('v5AvatarCv'), {
          quality:'balanced', viewMode:'CALL', modelUrl,
          fallbackUrl: src.fallbackAvatarUrl(), fallbackEl: byId('v5AvatarFb'),
        });
        const ok = await a.mount();
        if(!ok){ this.avatar=null; return; }
        this.avatar = a; window.JarvisAvatarSpatial = a;
        const e0 = a.engine;
        if(e0){
          e0.ground && (e0.ground.visible=false);
          e0.ring && (e0.ring.visible=false);
          e0.pool && (e0.pool.visible=false);
          if(e0.scene) e0.scene.fog=null;
          e0.director?.setMode?.('HALF_BODY');
          if(e0.avatarRoot) e0.avatarRoot.rotation.y=-0.30;
          if(e0.renderer) e0.renderer.toneMappingExposure=0.62;
          e0.scene?.traverse?.((o)=>{ if(o.isHemisphereLight) o.intensity=0.18; });
          if(e0.key){ e0.key.intensity=0.85; e0.key.position.set(2.3,2.4,1.2); }
          if(e0.fill){ e0.fill.intensity=0.20; e0.fill.position.set(-2.6,1.2,1.8); }
          if(e0.rim){ e0.rim.intensity=2.6; e0.rim.position.set(-1.6,1.9,-2.4); }
          const tint=(root)=>root?.traverse?.((o)=>{
            const mats=o.material?(Array.isArray(o.material)?o.material:[o.material]):[];
            for(const m of mats){
              if(!m||m.__v5) continue; m.__v5=true;
              m.color?.multiplyScalar?.(0.30);
              if('roughness' in m) m.roughness=Math.min(1,(m.roughness??.7)*1.05+.18);
              if('metalness' in m) m.metalness=0.04;
              if('envMapIntensity' in m) m.envMapIntensity=0.25;
              m.needsUpdate=true;
            }
          });
          tint(e0.model?.root||e0.avatarRoot);
          setTimeout(()=>tint(e0.model?.root||e0.avatarRoot),1500);
        }
      }catch(err){
        console.warn('[V5] avatar indisponible (l\'UI continue sans lui)', err);
        const fb=byId('v5AvatarFb'); if(fb) fb.hidden=false;
      }
    },

    /* ------------------------------------------------------------- rail */
    buildRail(){
      const rail = byId('v5Rail');
      RAIL.forEach(([id,label,page])=>{
        if(id==='settings'){ const s=document.createElement('div'); s.className='v5-rail-sep'; rail.appendChild(s); }
        const b=document.createElement('button');
        b.dataset.rail=id; b.dataset.page=page; b.setAttribute('aria-label',label);
        if(id==='home') b.classList.add('on');
        b.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
          stroke-linecap="round" stroke-linejoin="round">${ICONS[id]}</svg><span class="lbl">${label}</span>`;
        b.addEventListener('pointerenter',()=>this.tip(b,label));
        b.addEventListener('pointerleave',()=>this.hideTip());
        b.addEventListener('click',()=>{
          this.hideTip();
          const sub=SUBMENUS[id];
          if(sub){ this.popup(b, sub); }
          else { this.closePop(); window.App?.goto(page); }
          this.markRail(id);
        });
        rail.appendChild(b);
      });
      addEventListener('pointerdown',(e)=>{
        if(this._pop && !e.target.closest('.v5-pop') && !e.target.closest('#v5Rail') && !e.target.closest('#v5HudSys'))
          this.closePop();
      });
      addEventListener('keydown',(e)=>{ if(e.key==='Escape') this.closePop(); });
    },
    markRail(id){
      byId('v5Rail')?.querySelectorAll('button').forEach(b=>b.classList.toggle('on', b.dataset.rail===id));
    },

    /* ------------------------------------------------- menus en portail */
    popup(anchor, items){
      const same = this._popOwner===anchor;
      this.closePop();
      if(same) return;
      const el=document.createElement('div');
      el.className='v5-pop';
      el.innerHTML = items.map((it)=> it.length===1
        ? `<div class="grp">${it[0]}</div>`
        : `<button data-target="${it[0]}">${it[1]}</button>`).join('');
      byId('v5Portal').appendChild(el);
      const a=anchor.getBoundingClientRect(), r=el.getBoundingClientRect();
      let x=a.right+10, y=a.top+a.height/2-r.height/2;
      if(x+r.width>innerWidth-10) x=a.left-r.width-10;   // bascule si débordement
      if(x<10) x=10;
      y=clamp(y,46,Math.max(46,innerHeight-r.height-10));
      el.style.left=x+'px'; el.style.top=y+'px';
      el.addEventListener('click',(e)=>{
        const b=e.target.closest('button[data-target]'); if(!b) return;
        this.closePop();
        this.route(b.dataset.target);
      });
      this._pop=el; this._popOwner=anchor;
    },
    closePop(){
      if(!this._pop) return;
      const el=this._pop; this._pop=null; this._popOwner=null;
      el.classList.add('closing'); setTimeout(()=>el.remove(),130);
    },
    route(target){
      if(String(target).startsWith('__ui|')){
        // Bascule d'interface explicite : V4 reste disponible tant que la
        // migration n'est pas terminée.
        const mode=String(target).split('|')[1];
        try{ localStorage.setItem(MODE_KEY, mode); }catch(_){ /* stockage indisponible */ }
        location.reload();
        return;
      }
      if(target==='__mail'){
        // Le Kanban est un panneau autonome : il ne remplace pas la page
        // courante, il s'ouvre dans la bande de travail.
        window.JarvisMailKanban?.show();
        return;
      }
      if(target==='__workspace'){
        // Le Workspace se rouvre sur la dernière analyse RÉELLE ; sans analyse
        // chargée, on le dit au lieu d'ouvrir une coquille vide.
        const AW=window.AnalysisWorkspace;
        if(AW && AW.payload) AW.open(AW.payload);
        else window.toast?.('Aucune analyse chargée — demandez une analyse à JARVIS.');
        window.App?.goto('analyses');
        return;
      }
      const [page,section]=String(target).split('|');
      window.App?.goto(page, section?{section}:undefined);
    },
    tip(anchor,text){
      this.hideTip();
      const el=document.createElement('div'); el.className='v5-tip'; el.textContent=text;
      byId('v5Portal').appendChild(el);
      const a=anchor.getBoundingClientRect(), r=el.getBoundingClientRect();
      el.style.left=(a.right+10)+'px';
      el.style.top=(a.top+a.height/2-r.height/2)+'px';
      this._tip=el;
    },
    hideTip(){ this._tip?.remove(); this._tip=null; },

    /* -------------------------------------------------------------- HUD */
    buildHud(){
      byId('v5Hud').innerHTML = `
        <div class="v5-hud-id"><i id="v5HudDot"></i>JARVIS</div>
        <button class="v5-hud-sys" id="v5HudSys" title="Diagnostics système">
          <span>CPU <b id="v5Cpu">—</b></span><span>RAM <b id="v5Ram">—</b></span>
          <span>GPU <b id="v5Gpu">—</b></span><span><b id="v5Tools">—</b> TOOLS</span>
          <span id="v5Model">—</span>
        </button>
        <div class="v5-hud-spacer"></div>
        <div class="v5-hud-time" id="v5Clock">--:--:--</div>`;
      byId('v5HudSys').addEventListener('click',(e)=>{
        this.popup(e.currentTarget, SUBMENUS.system);
        this.showModules('system');
      });
      setInterval(()=>{
        const n=byId('v5Clock'); if(n) n.textContent=new Date().toLocaleTimeString('fr-FR');
      },1000);
    },

    async refreshStatus(){
      if(typeof J==='undefined') return;
      const st = (J.state && J.state.status) || await J.get('/api/status').catch(()=>null);
      if(!st) return;
      const set=(id,v)=>{ const n=byId(id); if(n) n.textContent=(v==null||v==='')?'—':String(v); };
      const m=st.metrics||{};
      set('v5Cpu', m.cpu && typeof m.cpu.percent==='number' ? Math.round(m.cpu.percent)+'%' : '—');
      const ramPct = m.memory && typeof m.memory.percent==='number' ? Math.round(m.memory.percent) : null;
      set('v5Ram', ramPct==null ? '—' : ramPct+'%');
      const ramEl=byId('v5Ram');
      if(ramEl) ramEl.classList.toggle('hot', ramPct!=null && ramPct>=85);
      // Le backend ne publie pas (encore) de métrique GPU : plutôt qu'un
      // emplacement vide en permanence, la case disparaît.
      const gpu=st.gpu||m.gpu;
      const gpuCell=byId('v5Gpu')?.closest('span');
      if(gpu){ set('v5Gpu', gpu.name||gpu.device||(gpu.available?'actif':'cpu')); if(gpuCell) gpuCell.hidden=false; }
      else if(gpuCell){ gpuCell.hidden=true; }
      set('v5Tools', st.tools ? st.tools.enabled : '—');
      const llm=(st.llm||[]).find(p=>p.connected);
      set('v5Model', llm ? (llm.default_model||llm.name) : 'hors ligne');
      const health=(st.core&&st.core.system&&st.core.system.status)||'ok';
      const dot=byId('v5HudDot');
      if(dot) dot.dataset.tone = health==='critical'?'err':health==='warning'?'warn':'ok';

      const op=byId('v5Operator'); if(op) op.textContent=st.user_name||'Opérateur';
      const h=new Date().getHours();
      const greet=byId('v5Greet')?.querySelector('h1');
      if(greet) greet.childNodes[0].nodeValue = (h<6||h>=18?'Bonsoir, ':'Bonjour, ');
      const sub=byId('v5Sub');
      if(sub) sub.textContent = health==='critical' ? 'Système en alerte'
        : health==='warning' ? 'Système dégradé' : 'Tous les systèmes sont prêts';

      this.renderSats(st);
    },

    /* Satellites : uniquement des faits réels, en orbite, jamais en cartes. */
    renderSats(st){
      const host=byId('v5Sats');
      const agents=st.core&&st.core.agents;
      const conn=(st.connectors&&st.connectors.items)||[];
      const ssh=conn.find(c=>/ssh/i.test(c.id||c.name||''));
      const ollama=(st.llm||[]).find(p=>/ollama/i.test(p.id||p.name||''));
      const data=[
        {k:st.tools?st.tools.enabled:'—', l:'OUTILS', tone:'', ang:-163},
        {k:agents?agents.total:'—', l:'AGENTS PRÊTS', tone:'', ang:-118},
        {k:st.memory?(st.memory.knowledge??st.memory.total??'—'):'—', l:'SAVOIRS', tone:'', ang:-62},
        {k:'SSH', l:ssh?(ssh.status==='connected'?'CONNECTÉ':'HORS LIGNE'):'—',
         tone:ssh&&ssh.status==='connected'?'ok':'warn', ang:-17},
        {k:'OLLAMA', l:ollama&&ollama.connected?'PRÊT':'HORS LIGNE',
         tone:ollama&&ollama.connected?'ok':'warn', ang:22},
        {k:(st.memory&&st.memory.conversations)??(st.conversations&&st.conversations.total)??'—',
         l:'CONVERSATIONS', tone:'', ang:158},
      ];
      if(!this._sats){
        this._sats=data.map(d=>{
          const el=document.createElement('div'); el.className='v5-sat';
          host.appendChild(el); return {el, ang:d.ang};
        });
      }
      data.forEach((d,i)=>{
        const s=this._sats[i]; if(!s) return;
        s.el.dataset.tone=d.tone||'';
        s.el.innerHTML=`<i></i><b>${d.k}</b>${d.l}`;
      });
    },

    /* ---------------------------------------------- modules contextuels */
    showModules(key){
      const host=byId('v5Mods');
      [...host.children].forEach(c=>{ c.classList.add('out'); setTimeout(()=>c.remove(),300); });
      const st=(typeof J!=='undefined' && J.state && J.state.status) || null;
      if(!key || !st) return;
      const m=st.metrics||{};
      const defs={
        system:[
          {t:'DIAGNOSTIC', rows:[
            ['Processeur', m.cpu? Math.round(m.cpu.percent)+' %':'—'],
            ['Mémoire', m.memory? Math.round(m.memory.percent)+' %':'—'],
            ['Disque', m.disk? Math.round(m.disk.percent)+' %':'—'],
            ['Uptime', st.uptime_s? Math.round(st.uptime_s/60)+' min':'—'],
          ]},
          {t:'LIAISONS', rows:((st.connectors&&st.connectors.items)||[]).slice(0,6)
            .map(c=>[c.name||c.id, c.status==='connected'?'connecté':c.status])},
        ],
        memory:[{t:'MÉMOIRE', rows:[
          ['Souvenirs', st.memory? st.memory.total:'—'],
          ['Conversations', st.conversations? st.conversations.total:'—'],
        ]}],
        agents:[{t:'AGENTS', rows:(st.agents||[]).slice(0,7).map(a=>[a.name||a.id, a.status||'—'])}],
        tools:[{t:'OUTILS', rows:[
          ['Disponibles', st.tools? st.tools.total:'—'],
          ['Actifs', st.tools? st.tools.enabled:'—'],
          ['Catégories', st.tools&&st.tools.categories? st.tools.categories.length:'—'],
        ]}],
      }[key];
      (defs||[]).forEach((d,i)=>{
        const el=document.createElement('div');
        el.className='v5-mod'; el.style.animationDelay=(i*.07)+'s';
        el.innerHTML=`<h4><i></i>${d.t}</h4>`+d.rows
          .map(r=>`<div class="row"><span>${r[0]}</span><b>${r[1]}</b></div>`).join('');
        el.appendChild(document.createElement('span'));
        byId('v5Mods').appendChild(el);
      });
    },

    /* ------------------------------------------------------ command bar */
    bindCommandBar(){
      const cmd=byId('v5Cmd'), input=byId('convInput');
      if(input){
        input.addEventListener('focus',()=>cmd.classList.add('focus'));
        input.addEventListener('blur',()=>cmd.classList.remove('focus'));
        input.addEventListener('input',()=>{
          input.style.height='auto';
          input.style.height=Math.min(120,input.scrollHeight)+'px';
        });
        input.addEventListener('keydown',(e)=>{
          if(e.key!=='Enter'||e.shiftKey||e.isComposing) return;
          e.preventDefault();
          const f=byId('convForm');
          f?.requestSubmit ? f.requestSubmit() : f?.dispatchEvent(new Event('submit'));
          this.setContext('chat'); this.markRail('chat');
        });
      }
      addEventListener('keydown',(e)=>{
        if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){ e.preventDefault(); input?.focus(); }
      });
      byId('convForm')?.addEventListener('submit',()=>{
        this.setContext('chat'); this.markRail('chat');
      });
    },

    /* ----------------------------------------- App : route et états réels */
    bindApp(){
      const app=window.App;
      if(app && !app.__v5){
        const goto=app.goto.bind(app);
        app.goto=(page,options)=>{
          const r=goto(page,options);
          const ctx=PAGE_CONTEXT[page]||'work';
          this.setContext(ctx);
          const railId=RAIL.find(([id,,p])=>p===page)?.[0]
            || (ctx==='home'?'home':ctx==='chat'?'chat':ctx==='data'?'data':ctx==='sync'?'sync':null);
          if(railId) this.markRail(railId);
          this.showModules(['agents','memory','tools','system'].includes(railId)?railId:null);
          // Les modules V5 (agents, mémoire, outils, réglages) s'accrochent ici.
          window.dispatchEvent(new CustomEvent('jarvis:page',{detail:{page,options}}));
          return r;
        };
        app.__v5=true;
      }
      // Pendant la génération, le message en attente affiche la phase RÉELLE
      // (dérivée de l'état du Brain), jamais un « … » générique.
      if(app && typeof app.pushMessage==='function' && !app.__v5Chat){
        const push=app.pushMessage.bind(app);
        app.pushMessage=(role,text,options={})=>{
          const node=push(role,text,options);
          if(options.pending){
            (app.pendingReplyNodes||[]).forEach(n=>{
              if(!n) return;
              n.classList.add('v5-pending');
              const b=n.querySelector('.bubble');
              if(b) b.innerHTML='<span class="v5-phase" data-phase>ANALYSE</span>'
                + '<span class="v5-phase-dots"><i></i><i></i><i></i></span>';
            });
          }
          return node;
        };
        app.__v5Chat=true;
      }
      const PHASES={THINKING:'TRAITEMENT',RECALLING:'RECALL',SEARCHING:'RECHERCHE',
        READING:'LECTURE',CODING:'OUTIL',DELEGATING:'AGENT',GENERATING:'GÉNÉRATION',
        SYNCING:'SYNCHRONISATION',SPEAKING:'RÉPONSE',LISTENING:'ÉCOUTE',SUCCESS:'TERMINÉ'};
      window.addEventListener('jarvis:brain-state',(e)=>{
        const label=PHASES[e.detail.state];
        document.querySelectorAll('.v5-pending [data-phase]')
          .forEach(n=>{ n.textContent=label||'ANALYSE'; });
      });

      // L'avatar suit l'état réel de JARVIS : il parle quand le TTS parle,
      // écoute quand le micro écoute, et revient à IDLE par transition.
      const AV = {SPEAKING:'SPEAKING', LISTENING:'LISTENING', ERROR:'ERROR',
        SUCCESS:'SUCCESS', IDLE:'IDLE'};
      window.addEventListener('jarvis:brain-state',(e)=>{
        const s=e.detail.state;
        this.avatar?.setState?.(AV[s] || 'THINKING');
      });
      // Niveau sonore réel du TTS → lipsync de l'avatar (aucune simulation).
      if(app && typeof app.robotAudioLevel==='function' && !app.__v5AvatarAudio){
        const orig=app.robotAudioLevel.bind(app);
        app.robotAudioLevel=(lvl)=>{ orig(lvl); this.avatar?.setAudioLevel?.(lvl); };
        app.__v5AvatarAudio=true;
      }

      // Le micro bascule en contexte VOICE tant qu'il écoute.
      window.addEventListener('jarvis:voice-state',(e)=>{
        const s=e.detail?.state;
        this.listening=['LISTENING','WAKE'].includes(s);
        byId('v5Cmd')?.classList.toggle('listening', this.listening);
        if(this.listening) this.setContext('voice');
        else if(this.context==='voice') this.setContext('chat');
      });
    },

    setContext(ctx){
      if(!LAYOUT[ctx] || this.context===ctx) return;
      this.context=ctx; this.L=LAYOUT[ctx];
      document.documentElement.setAttribute('data-context',ctx);
      byId('v5Work')?.classList.toggle('in', !!this.L.work);
      byId('v5Chat')?.classList.toggle('in', !!this.L.chat);
      const g=byId('v5Greet'); if(g) g.style.opacity=this.L.greet?1:0;
      byId('v5Sats')?.querySelectorAll('.v5-sat').forEach(s=>s.style.opacity=this.L.greet?1:0);
    },

    refreshMiniMeta(detail){
      const wrap=byId('v5BrainWrap'), meta=byId('v5MiniMeta');
      if(!wrap||!meta) return;
      const small=!!this.L?.chat || (this.Lc?.brain?.s||1)<0.2;
      meta.hidden=!small;
      const st=byId('v5MiniState'), det=byId('v5MiniDetail');
      const state=(detail&&detail.state)||window.ObsidianBrain?.state||'IDLE';
      const rec=window.JarvisBrainData?.lastRecall;
      const n=window.JarvisBrainData?.stats?.canonique?.nodes;
      if(st) st.textContent = rec && state==='RECALLING' ? 'RECALLING' : state;
      if(det){
        if(rec && (state==='RECALLING'||Date.now()-(rec.at||0)<8000))
          det.textContent=(rec.ids?.length||0)+' concepts';
        else if(window.JarvisSpatialEvents?._lastAgent)
          det.textContent='AGENT · '+window.JarvisSpatialEvents._lastAgent;
        else det.textContent=n!=null ? n+' concepts' : '';
      }
      // panneau droit : état du Brain
      const rpState=byId('v5RpState'), rpStats=byId('v5RpStats');
      if(rpState) rpState.textContent=state;
      if(rpStats) rpStats.textContent=n!=null ? n+' concepts' : '';
    },

    _dev(){
      try{ return localStorage.getItem('JARVIS_DEV')==='1'; }catch(_){ return false; }
    },
    /* Marqueurs de prompt interne : ces textes sont destines au modele
       (contenu du classeur, politique de sources, consignes de grounding) et ne
       doivent JAMAIS atteindre le bandeau. Le backend les filtre deja a
       l'emission ; ce garde-fou couvre tout autre emetteur. */
    INTERNAL_MARKERS: /CONTENU STRUCTUR|SOURCE_POLICY|ANALYSE_DETERMINISTE|DETERMINISTIC_WORKBOOK|PERIMETRE_OBLIGATOIRE|FORMAT_REPONSE_ANALYSE|DEMANDE DE L'UTILISATEUR|VALIDATION_FAILED|FAITS_VERIFIES|PASSAGE_A_CORRIGER|VALEURS_REFUSEES/i,
    _publicTitle(raw){
      const s=String(raw||'').trim();
      if(!s) return '';
      if(!this._dev() && /^(task_|agt_|agent_)?[a-f0-9-]{8,}$/i.test(s.replace(/\s/g,''))) return '';
      if(!this._dev() && /^task_/i.test(s)) return '';
      if(this.INTERNAL_MARKERS.test(s)) return 'Analyse des donnees';
      return s.slice(0,72);
    },

    setTask(status, title, extra){
      const el=byId('v5Task'); if(!el) return;
      const label=byId('v5TaskLabel'), tit=byId('v5TaskTitle'), meta=byId('v5TaskMeta'), dot=byId('v5TaskDot');
      const pub=this._publicTitle(title);
      if(status==='idle' || (!pub && status!=='run' && status!=='done' && status!=='err')){
        el.hidden=true; byId('v5Cmd')?.classList.remove('busy'); this._syncRpanelAgent(status,title); return;
      }
      el.hidden=false; el.dataset.tone=status==='done'?'ok':status==='err'?'err':'cy';
      if(dot) dot.className=status==='run'?'spin':'';
      if(label) label.textContent=status==='run'?'EN COURS':status==='done'?'TERMINÉ':status==='err'?'ÉCHEC':'ACTIVITÉ';
      if(tit) tit.textContent=pub||'Travail en cours';
      if(meta) meta.textContent=extra||'';
      byId('v5Cmd')?.classList.toggle('busy', status==='run');
      clearTimeout(this._taskT);
      if(status==='done'||status==='err') this._taskT=setTimeout(()=>{
        el.hidden=true; byId('v5Cmd')?.classList.remove('busy');
        this.setTaskSteps(null);   // la checklist reste lisible jusqu'ici
      }, 4200);
      this._syncRpanelAgent(status, pub);
    },
    _syncRpanelAgent(status, title){
      const ag=byId('v5RpAgent'), agAct=byId('v5RpAgentAct');
      if(!ag) return;
      if(status==='idle'||status==='done'||status==='err'){
        ag.textContent='Aucune tâche en cours';
        if(agAct) agAct.textContent='';
        return;
      }
      const who=window.JarvisSpatialEvents?._lastAgent||'Agent';
      ag.textContent=who+' · '+(title||'Travail en cours');
      if(agAct) agAct.textContent=status==='run'?'En cours…':status.toUpperCase();
    },
    setCtx(ctx){
      const el=byId('v5RpCtx');
      if(el && ctx) el.textContent=String(ctx).slice(0,60);
    },
    resetCtx(){
      const el=byId('v5RpCtx');
      if(el) el.textContent='—';
    },
    /* Checklist d'exécution — chaque ligne correspond à une étape que le
       backend a réellement déclarée (pipeline à phases) ou réellement
       exécutée (boucle d'outils). Aucune étape n'est anticipée ici. */
    setTaskSteps(steps){
      const host=byId('v5TaskSteps'); if(!host) return;
      if(!steps||!steps.length){ host.hidden=true; host.replaceChildren(); return; }
      const GLYPH={done:'✓', run:'●', err:'✕', idle:'○'};
      const done=steps.filter(s=>s.state==='done').length;
      const total=steps.length;
      host.hidden=false;
      host.replaceChildren();
      steps.forEach((s,i)=>{
        const st=GLYPH[s.state]?s.state:'idle';
        const n=document.createElement('span');
        n.dataset.st=st;
        n.title=s.label||'';
        n.textContent=`[${i+1}/${total}] ${s.label} ${GLYPH[st]}`;
        host.appendChild(n);
      });
      const meta=byId('v5TaskMeta');
      if(meta && done<total) meta.textContent=`${done}/${total}`;
    },

    /* --------------------------------------------- activité & cartes outil */
    activity(label,detail,tone){
      if(/TÂCHE/.test(label)) return;
      const host=byId('v5Activity'); if(!host) return;
      const row=document.createElement('div');
      row.className='v5-act'; row.dataset.tone=tone||'cy';
      row.innerHTML='<b></b><span></span>';
      row.querySelector('b').textContent=label;
      const pub=this._publicTitle(detail);
      row.querySelector('span').textContent=pub;
      if(!pub && !label) return;
      host.prepend(row);
      while(host.children.length>2) host.lastElementChild.remove();
      setTimeout(()=>row.classList.add('fade'),5200);
      setTimeout(()=>row.remove(),6200);
    },
    toolCard(tag,detail,ms){
      const wrap=byId('v5BrainWrap'); if(!wrap) return;
      const r=wrap.getBoundingClientRect();
      const el=document.createElement('div');
      el.className='v5-toolcard';
      el.innerHTML=`<div class="t">${tag}</div><div class="d">${detail||''}</div>`;
      byId('v5Cards').appendChild(el);
      el.style.left=clamp(r.left+r.width*0.58, 70, innerWidth-el.offsetWidth-22)+'px';
      el.style.top=Math.max(52, r.top+r.height*0.1)+'px';
      setTimeout(()=>{ el.classList.add('out'); setTimeout(()=>el.remove(),300); }, ms||3400);
    },

    /* --------------------------------------------------------- rendu scène */
    seedStars(){
      const cv=byId('v5Stars'); if(!cv) return;
      const d=dprOf();
      cv.width=innerWidth*d; cv.height=innerHeight*d;
      this._stars=Array.from({length:Math.round(innerWidth*innerHeight/16000)},()=>({
        x:Math.random()*cv.width,y:Math.random()*cv.height,
        r:(Math.random()*1.1+.2)*d,o:.10+Math.random()*.42,tw:Math.random()*Math.PI*2}));
    },
    drawStars(t){
      const cv=byId('v5Stars'); if(!cv||!this._stars) return;
      const c=cv.getContext('2d');
      c.clearRect(0,0,cv.width,cv.height);
      for(const s of this._stars){
        c.beginPath(); c.arc(s.x,s.y,s.r,0,Math.PI*2);
        c.fillStyle=`rgba(180,225,245,${s.o*(.6+.4*Math.sin(t*1.1+s.tw))})`; c.fill();
      }
    },

    applyLayout(dt){
      const k=1-Math.pow(.004,dt), W=innerWidth, H=innerHeight;
      const Lc=this.Lc, L=this.L;
      for(const key of ['x','y','s','dim']){
        Lc.brain[key]=lerp(Lc.brain[key]??0, L.brain[key]??0, k);
        Lc.avatar[key]=lerp(Lc.avatar[key]??0, L.avatar[key]??0, k);
      }
      Lc.avatar.op=lerp(Lc.avatar.op, L.avatar.op, k);

      const showGreet=!!L.greet;
      const topLimit=52, botLimit=H-(showGreet?218:118);
      // En contexte données, le Brain devient une veilleuse d'angle de taille
      // fixe : il montre que JARVIS reste actif sans manger le contenu.
      const bs = L.work
        ? lerp(170, 120, clamp(Lc.brain.dim*3,0,1))
        : Math.max(170, Math.min(Math.min(W,H)*Lc.brain.s*2.1, botLimit-topLimit));
      const bcy = L.work ? (H-96) : clamp(H*Lc.brain.y, topLimit+bs/2, botLimit-bs/2);
      const bw=byId('v5BrainWrap');
      bw.style.width=bs+'px'; bw.style.height=bs+'px';
      const bcx = L.work ? 66 : (W*Lc.brain.x-this.mx*14);
      bw.style.left=(bcx-bs/2)+'px';
      bw.style.top=(bcy-bs/2-this.my*14)+'px';
      bw.style.opacity=(1-Lc.brain.dim*.62).toFixed(3);
      bw.style.filter=Lc.brain.dim>.05?`blur(${(Lc.brain.dim*1.5).toFixed(2)}px)`:'none';

      this.refreshMiniMeta({state:window.ObsidianBrain?.state});
      const as=Math.min(W,H)*Lc.avatar.s*(this.L.chat&&!this.listening?1.55:1.9);
      const aw=byId('v5Avatar');
      aw.style.width=as+'px'; aw.style.height=(as*1.15)+'px';
      aw.style.left=(W*Lc.avatar.x-as/2-this.mx*9)+'px';
      aw.style.top=(H*Lc.avatar.y-as*.58-this.my*9)+'px';
      aw.style.opacity=Lc.avatar.op;
      aw.style.filter=Lc.avatar.dim>.05?`blur(${(Lc.avatar.dim*2).toFixed(2)}px)`:'none';

      const g=byId('v5Greet');
      g.style.left=(L.work?bcx:W*Lc.brain.x)+'px';
      g.style.top=(bcy+bs*0.5+16)+'px';

      const sky=document.querySelector('.v5-sky'), hz=document.querySelector('.v5-horizon');
      if(sky) sky.style.transform=`translate3d(${-this.mx*2}px,${-this.my*2}px,0)`;
      if(hz) hz.style.transform=`translate3d(${-this.mx*5}px,${-this.my*5}px,0)`;

      this.layoutZones(bs,bcy,showGreet,bcx);
      this.layoutSats(bs,bcy,showGreet,bcx);
    },

    layoutZones(bs,cy0,show,cx0){
      const api=window.ObsidianBrain; if(!api||!this.zoneEls) return;
      const cv=byId('v5Leads'); const d=dprOf();
      if(cv.width!==Math.round(innerWidth*d)){ cv.width=Math.round(innerWidth*d); cv.height=Math.round(innerHeight*d); }
      const ctx=cv.getContext('2d');
      ctx.setTransform(d,0,0,d,0,0); ctx.clearRect(0,0,innerWidth,innerHeight);
      const cx=(cx0??(innerWidth*this.Lc.brain.x-this.mx*14)), cy=cy0-this.my*14, S=bs*0.40;
      const small=this.Lc.brain.s<0.2;
      for(const k in api.ZONES){
        const z=api.ZONES[k], el=this.zoneEls[k];
        const ax=cx+z.at[0]*S, ay=cy+z.at[1]*S;
        const right=z.at[0]>0;
        const lx=cx+z.at[0]*S*1.9+(right?62:-62), ly=cy+z.at[1]*S*1.6;
        el.style.left=(lx-(right?0:el.offsetWidth))+'px';
        el.style.top=(ly-6)+'px';
        const hot=(this.brain?.zoneHeat?.[k]||0)>.4;
        el.classList.toggle('hot',hot);
        el.style.opacity=(show&&!small)?1:0;
        if(!show||small) continue;
        const c=z.col;
        ctx.beginPath(); ctx.moveTo(ax,ay); ctx.lineTo(lx+(right?-8:8),ly+5);
        ctx.strokeStyle=`rgba(${c[0]},${c[1]},${c[2]},${hot?.45:.12})`;
        ctx.lineWidth=1; ctx.setLineDash([2,4]); ctx.stroke(); ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(ax,ay,hot?3:1.8,0,Math.PI*2);
        ctx.fillStyle=`rgba(${c[0]},${c[1]},${c[2]},${hot?.9:.32})`; ctx.fill();
      }
    },

    layoutSats(bs,cy0,show,cx0){
      if(!this._sats) return;
      const cx=(cx0??(innerWidth*this.Lc.brain.x-this.mx*14)), cy=cy0-this.my*14;
      const rx=bs*0.66, ry=bs*0.46;
      for(const s of this._sats){
        const a=s.ang*Math.PI/180;
        const w=s.el.offsetWidth||120;
        const right=Math.cos(a)>0;
        let x=cx+Math.cos(a)*rx;
        x = right ? Math.min(x, innerWidth-16-w) : Math.max(x, 16+w);
        const y=clamp(cy+Math.sin(a)*ry, 52, innerHeight-150);
        s.el.style.opacity=show?1:0;
        s.el.style.left=(x-(right?0:s.el.offsetWidth))+'px';
        s.el.style.top=(y-8)+'px';
      }
    },

    drawWave(t){
      const cv=byId('v5Wave'); if(!cv||!this.listening) return;
      const d=dprOf(), r=cv.getBoundingClientRect();
      if(cv.width!==Math.round(r.width*d)){ cv.width=Math.round(r.width*d); cv.height=Math.round(r.height*d); }
      const c=cv.getContext('2d');
      c.clearRect(0,0,cv.width,cv.height);
      const bars=Math.floor(cv.width/(5*d));
      for(let i=0;i<bars;i++){
        const n=Math.sin(t*7+i*.42)*Math.sin(t*2.3+i*.11);
        const h=(.12+Math.abs(n)*.8)*cv.height*.8;
        c.fillStyle=`rgba(56,223,255,${.25+Math.abs(n)*.7})`;
        c.fillRect(i*5*d+1.5*d,(cv.height-h)/2,2.2*d,h);
      }
    },

    /* ---------------------------------------------------------- mesure perf
       À lancer depuis la console de TON navigateur (pas depuis un panneau
       d'outil, qui bride le rafraîchissement) :  await JarvisSpatial.perf()  */
    perf(seconds){
      const dur=(seconds||5)*1000;
      return new Promise((resolve)=>{
        let n=0, worst=Infinity, over33=0, over50=0, sum=0, last=performance.now();
        const t0=last;
        const tick=(now)=>{
          const dt=now-last; last=now;
          if(dt>0){
            worst=Math.min(worst, 1000/dt);
            sum+=dt; n++;
            if(dt>33) over33++;
            if(dt>50) over50++;
          }
          if(now-t0<dur) requestAnimationFrame(tick);
          else{
            const secs=(now-t0)/1000;
            const res={
              vue: this.context,
              duree_s: +secs.toFixed(1),
              fps_moyen: Math.round(n/secs),
              fps_min: Math.round(worst),
              frame_moyenne_ms: +(sum/Math.max(1,n)).toFixed(1),
              frames_sup_33ms: over33,
              frames_sup_50ms: over50,
              heap_mo: performance.memory
                ? Math.round(performance.memory.usedJSHeapSize/1048576) : null,
              avatar: !!this.avatar,
              brain_instances: window.ObsidianBrain?.instances.length || 0,
              chargement_ms: (()=>{ const nav=performance.getEntriesByType('navigation')[0];
                return nav? Math.round(nav.loadEventEnd-nav.startTime) : null; })(),
              viewport: innerWidth+'×'+innerHeight,
            };
            // Seuils annoncés : on signale, on ne masque pas.
            res.alertes=[];
            if(res.fps_moyen<55) res.alertes.push('FPS moyen < 55');
            if(res.frames_sup_50ms>0) res.alertes.push(res.frames_sup_50ms+' frame(s) > 50 ms');
            else if(res.frames_sup_33ms>3) res.alertes.push(res.frames_sup_33ms+' frame(s) > 33 ms');
            console.table(res);
            resolve(res);
          }
        };
        requestAnimationFrame(tick);
      });
    },

    /** Enchaîne une mesure sur chaque vue. Usage : await JarvisSpatial.perfAll() */
    async perfAll(seconds){
      const vues=[['command','HOME'],['chat','CHAT'],['memory','MEMORY'],['agents','AGENTS'],
        ['tools','TOOLS'],['settings','SETTINGS'],['servers','SYNC']];
      const out=[];
      for(const [page,label] of vues){
        window.App?.goto(page);
        await new Promise(r=>setTimeout(r,1400));
        const r=await this.perf(seconds||5);
        out.push({vue:label, ...r});
      }
      console.table(out);
      return out;
    },

    startLoop(){
      let last=performance.now();
      const loop=(now)=>{
        requestAnimationFrame(loop);
        const dt=clamp((now-last)/1000,0,.1); last=now;
        if(document.hidden) return;
        this.mx=lerp(this.mx,this.tx,1-Math.pow(.004,dt));
        this.my=lerp(this.my,this.ty,1-Math.pow(.004,dt));
        this.applyLayout(dt);
        this.drawStars(now/1000);
        this.drawWave(now/1000);
      };
      requestAnimationFrame(loop);
    },
  };

  window.JarvisSpatial = Spatial;

  const start=()=>setTimeout(()=>{
    if(!Spatial.boot()) window.JarvisV4?.boot?.();      // legacy_v4 explicite
  },0);
  if(document.readyState==='loading') addEventListener('DOMContentLoaded',start);
  else start();
})();
