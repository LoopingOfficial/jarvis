/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_modules.js  (PHASE 2)
   Migration des vues encore héritées : AGENTS (constellation), MEMORY
   (graphe de connaissances), TOOLS (recherche + catégories), SETTINGS
   (navigation compacte).

   Principe : ces vues PILOTENT les contrôles existants (elles ne les
   réimplémentent pas). Le DOM hérité reste dans la page, masqué, et chaque
   action de la vue V5 déclenche le vrai bouton — donc aucune fonction perdue.
   Aucune donnée n'est inventée : tout vient de /api/agents, /api/brain,
   /api/tools, /api/settings et des événements agent.* / brain.* réels.
   ========================================================================== */
(function () {
  'use strict';

  const TAU = Math.PI * 2;
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const lerp = (a,b,t)=>a+(b-a)*t;
  const byId = (id)=>document.getElementById(id);
  const dprOf = ()=>clamp(window.devicePixelRatio||1,1,2);
  const esc = (s)=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const fold = (s)=>String(s??'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');
  const reduced = ()=>!!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);

  /** settings.js déclare  : lexical, jamais sur window. */
  const SET = ()=> (typeof Settings!=='undefined' ? Settings : (window.Settings||null));

  /** Enveloppe V5 posée en tête de page ; le DOM hérité passe dessous. */
  function mountView(pageId, cls){
    const page = byId(pageId);
    if(!page) return null;
    let host = page.querySelector(':scope > .v5-view-host');
    if(!host){
      host = document.createElement('div');
      host.className = 'v5-view-host ' + cls;
      page.prepend(host);
      page.classList.add('v5-migrated');
    }
    return host;
  }

  /* ======================================================================
     1. AGENTS — constellation réelle
     ====================================================================== */
  const Agents = {
    nodes: [], raf:null, t:0, flows:[], selected:null,

    async render(){
      if(!byId('page-agents')) return;
      const res = await J.get('/api/agents').catch(()=>null);
      const agents = (res && res.agents) || [];
      if(!agents.length) return;
      const host = mountView('page-agents','v5-agents');   // après le réseau
      if(!host) return;

      host.innerHTML = `
        <div class="v5-sec-head">
          <span class="v5-kick">RÉSEAU D'AGENTS</span>
          <h2>${agents.length} agents · <span id="v5AgActive">0</span> actif(s)</h2>
        </div>
        <div class="v5-constel">
          <canvas id="v5AgCanvas"></canvas>
          <div class="v5-constel-nodes" id="v5AgNodes"></div>
        </div>
        <aside class="v5-node-panel" id="v5AgPanel" hidden></aside>`;

      const core = agents.find(a=>/core|jarvis/i.test(a.id)) || agents[0];
      const others = agents.filter(a=>a!==core);
      this.core = core;
      this.nodes = others.map((a,i)=>{
        const ang = (i/others.length)*TAU - Math.PI/2;
        return {a, ang, r:1, pulse:0, el:null, task:''};
      });

      const nodeHost = byId('v5AgNodes');
      nodeHost.innerHTML = this.nodes.map((n,i)=>`
        <button class="v5-node" data-node="${i}">
          <i class="dot"></i>
          <b>${esc((n.a.name||n.a.id).toUpperCase())}</b>
          <span class="role">${esc(n.a.role||'')}</span>
          <span class="task" data-task></span>
        </button>`).join('')
        + `<div class="v5-node core"><i class="dot"></i><b>${esc((core.name||'JARVIS').toUpperCase())}</b>
           <span class="role">${esc(core.role||'Orchestrateur')}</span></div>`;

      this.nodes.forEach((n,i)=>{
        n.el = nodeHost.querySelector(`[data-node="${i}"]`);
        n.el.addEventListener('click',()=>this.select(i));
      });
      this.sync(agents);
      this.start();
    },

    /** État réel de chaque agent (aucune activité n'est supposée). */
    sync(agents){
      let active=0;
      for(const n of this.nodes){
        const fresh = agents?.find(a=>a.id===n.a.id);
        if(fresh) n.a = fresh;
        const on = n.a.status==='active' || !!n.a.current_action;
        if(on) active++;
        n.el?.classList.toggle('live', on);
        n.el?.classList.toggle('off', n.a.enabled===false);
        n.el?.classList.toggle('err', n.a.status==='error');
        const t = n.el?.querySelector('[data-task]');
        if(t) t.textContent = n.a.current_action || '';
      }
      const c=byId('v5AgActive'); if(c) c.textContent=String(active);
    },

    select(i){
      const n=this.nodes[i]; if(!n) return;
      this.selected=i;
      const p=byId('v5AgPanel'); if(!p) return;
      const page=byId('page-agents');
      const run=page?.querySelector(`[data-run-agent="${CSS.escape(n.a.id)}"]`);
      const tog=page?.querySelector(`[data-toggle-agent="${CSS.escape(n.a.id)}"]`);
      p.hidden=false;
      p.innerHTML=`
        <header><span class="v5-kick">AGENT</span><h3>${esc(n.a.name||n.a.id)}</h3>
          <button class="x" data-close>✕</button></header>
        <div class="rows">
          <div class="row"><span>Spécialité</span><b>${esc(n.a.role||'—')}</b></div>
          <div class="row"><span>État</span><b>${esc(n.a.status||'—')}</b></div>
          <div class="row"><span>Exécutions</span><b>${n.a.runs||0}</b></div>
          ${n.a.current_action?`<div class="row"><span>En cours</span><b>${esc(n.a.current_action)}</b></div>`:''}
          ${n.a.last_error?`<div class="row err"><span>Dernière erreur</span><b>${esc(String(n.a.last_error).slice(0,90))}</b></div>`:''}
          <div class="row"><span>Outils</span><b>${esc((n.a.tool_prefixes||[]).join(', ')||'tous')}</b></div>
        </div>
        <div class="acts">
          ${run?'<button data-act="run">Lancer</button>':''}
          ${tog?`<button data-act="toggle">${n.a.enabled?'Désactiver':'Activer'}</button>`:''}
        </div>`;
      p.querySelector('[data-close]').onclick=()=>{ p.hidden=true; this.selected=null; };
      // Les actions déclenchent les VRAIS contrôles de la page héritée.
      p.querySelector('[data-act="run"]')?.addEventListener('click',()=>run?.click());
      p.querySelector('[data-act="toggle"]')?.addEventListener('click',()=>tog?.click());
    },

    /** Flux réel Brain → agent (agent.started) et agent → Brain (completed). */
    flow(agentId, dir){
      const i=this.nodes.findIndex(n=>n.a.id===agentId
        || (n.a.name||'').toLowerCase().includes(String(agentId).toLowerCase()));
      if(i<0) return;
      this.flows.push({i, dir, t:0});
      this.nodes[i].pulse=1;
    },

    start(){
      if(this.raf) cancelAnimationFrame(this.raf);
      const cv=byId('v5AgCanvas'); if(!cv) return;
      let last=performance.now();
      const loop=(now)=>{
        this.raf=requestAnimationFrame(loop);
        if(document.hidden || !cv.isConnected || cv.offsetParent===null) return;
        const dt=clamp((now-last)/1000,0,.1); last=now;
        this.frame(cv,dt);
      };
      this.raf=requestAnimationFrame(loop);
    },

    frame(cv,dt){
      const box=cv.parentElement.getBoundingClientRect();
      const d=dprOf();
      if(cv.width!==Math.round(box.width*d)){ cv.width=Math.round(box.width*d); cv.height=Math.round(box.height*d); }
      const ctx=cv.getContext('2d');
      const W=box.width, H=box.height;
      ctx.setTransform(d,0,0,d,0,0);
      ctx.clearRect(0,0,W,H);
      this.t+=dt;
      const cx=W/2, cy=H/2, rx=Math.min(W*0.38,420), ry=Math.min(H*0.36,210);

      // positions (DOM) + liens (canvas) : le texte reste net, les flux fluides
      const pts=this.nodes.map((n,i)=>{
        const ang=n.ang + (reduced()?0:this.t*0.035);
        const x=cx+Math.cos(ang)*rx, y=cy+Math.sin(ang)*ry;
        if(n.el){ n.el.style.left=x+'px'; n.el.style.top=y+'px'; }
        n.pulse=Math.max(0,n.pulse-dt*0.6);
        return {x,y,n};
      });
      const coreEl=cv.parentElement.querySelector('.v5-node.core');
      if(coreEl){ coreEl.style.left=cx+'px'; coreEl.style.top=cy+'px'; }

      for(const p of pts){
        const live=p.n.el?.classList.contains('live');
        ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(p.x,p.y);
        ctx.strokeStyle=live?'rgba(56,223,255,.38)':'rgba(140,180,205,.10)';
        ctx.lineWidth=live?1.4:1; ctx.setLineDash(live?[]:[2,6]); ctx.stroke(); ctx.setLineDash([]);
      }
      // flux de délégation réels
      this.flows=this.flows.filter(f=>f.t<1);
      for(const f of this.flows){
        f.t+=dt*0.9;
        const p=pts[f.i]; if(!p) continue;
        const e=f.t*f.t*(3-2*f.t);
        const x=f.dir>0?lerp(cx,p.x,e):lerp(p.x,cx,e);
        const y=f.dir>0?lerp(cy,p.y,e):lerp(p.y,cy,e);
        const g=ctx.createRadialGradient(x,y,0,x,y,9);
        g.addColorStop(0,'rgba(255,255,255,.95)');
        g.addColorStop(.4,f.dir>0?'rgba(56,223,255,.75)':'rgba(61,240,166,.75)');
        g.addColorStop(1,'rgba(56,223,255,0)');
        ctx.fillStyle=g; ctx.beginPath(); ctx.arc(x,y,9,0,TAU); ctx.fill();
      }
      // noyau
      const pulse=1+Math.sin(this.t*1.6)*0.06;
      const g2=ctx.createRadialGradient(cx,cy,0,cx,cy,30*pulse);
      g2.addColorStop(0,'rgba(210,245,255,.9)');
      g2.addColorStop(.35,'rgba(56,223,255,.5)');
      g2.addColorStop(1,'rgba(56,223,255,0)');
      ctx.fillStyle=g2; ctx.beginPath(); ctx.arc(cx,cy,30*pulse,0,TAU); ctx.fill();
    },
  };

  /* ======================================================================
     2. MEMORY — graphe de connaissances
     ====================================================================== */
  const Memory = {
    nodes:[], edges:[], raf:null, t:0, hi:new Set(), sel:null, hover:null,
    view:{k:1, ox:0, oy:0}, focus:null, depth:1, query:'',

    async render(){
      if(!byId('page-memory')) return;
      const BD = window.JarvisBrainData;
      const loaded = BD ? await BD.load() : null;
      const graph = loaded && loaded.nodes?.length
        ? {nodes: loaded.nodes, edges: loaded.edges, raw: loaded.raw}
        : await fetch('/api/brain').then(r=>r.json()).catch(()=>null);
      if(!graph || !graph.nodes) return;
      this.raw = graph.raw || graph;
      const host = mountView('page-memory','v5-memory');
      if(!host) return;
      const nCount = graph.nodes.length, eCount = (graph.edges||[]).length;

      host.innerHTML = `
        <div class="v5-sec-head">
          <span class="v5-kick">MÉMOIRE DE JARVIS</span>
          <h2>${nCount} concepts · ${eCount} relations</h2>
          <div class="v5-legend" id="v5MemLegend"></div>
        </div>
        <div class="v5-graph-bar">
          <label class="v5-sr" for="v5MemSearch">Rechercher dans la mémoire</label>
          <input class="v5-search" id="v5MemSearch" placeholder="Rechercher un nœud…" autocomplete="off" />
          <button class="v5-btn" id="v5MemRecenter" aria-label="Recentrer le graphe">Recentrer</button>
          <button class="v5-btn" id="v5MemReset" aria-label="Réinitialiser la vue">Reset</button>
          <span class="v5-graph-hint" id="v5MemHint">molette : zoom · glisser : déplacer · clic : focus · ESC : quitter</span>
        </div>
        <div class="v5-graph"><canvas id="v5MemCanvas"></canvas></div>
        <aside class="v5-node-panel" id="v5MemPanel" hidden></aside>`;

      // clusters = familles réelles renvoyées par /api/brain
      const fams=[...new Set(graph.nodes.map(n=>n.family||'AUTRE'))];
      const COLORS=[[56,223,255],[155,140,255],[61,240,166],[255,176,87],[255,120,180],[120,200,255],[200,160,120]];
      this.fam={};
      fams.forEach((f,i)=>{ this.fam[f]=COLORS[i%COLORS.length]; });
      const legend=host.querySelector('#v5MemLegend');
      if(legend) legend.innerHTML=fams.map(f=>{
        const c=this.fam[f];
        return `<span><i style="background:rgb(${c[0]},${c[1]},${c[2]})"></i>${esc(f)}</span>`;
      }).join('');

      // disposition : un anneau par cluster, puis relaxation courte
      const byFam={};
      graph.nodes.forEach(n=>{ (byFam[n.family||'AUTRE']=byFam[n.family||'AUTRE']||[]).push(n); });
      this.nodes=[];
      fams.forEach((f,fi)=>{
        const list=byFam[f], a0=(fi/fams.length)*TAU;
        const cxx=Math.cos(a0)*0.62, cyy=Math.sin(a0)*0.62;
        list.forEach((n,i)=>{
          const a=(i/Math.max(1,list.length))*TAU, r=0.10+Math.sqrt(i/Math.max(1,list.length))*0.22;
          this.nodes.push({id:n.id, label:n.label||n.id, family:f, kind:n.kind, meta:n.meta||{},
            weight:n.degree||n.weight||1, x:cxx+Math.cos(a)*r, y:cyy+Math.sin(a)*r*0.8, vx:0, vy:0});
        });
      });
      const index=new Map(this.nodes.map((n,i)=>[n.id,i]));
      const seen=new Set();
      this.edges=(graph.edges||[]).map(e=>{
        const a=index.get(e.source), b=index.get(e.target);
        if(a==null||b==null||a===b) return null;
        const key=a<b?a+'|'+b+'|'+e.kind:b+'|'+a+'|'+e.kind;
        if(seen.has(key)) return null;
        seen.add(key);
        return {a,b,kind:e.kind};
      }).filter(Boolean);
      this.relax(140); this.normalise();

      const cv=byId('v5MemCanvas');
      this.bindNav(cv);
      this.start();
    },

    /** Relaxation courte : lisible sans simulation permanente (coût nul après). */
    relax(steps){
      for(let s=0;s<steps;s++){
        for(const e of this.edges){
          const a=this.nodes[e.a], b=this.nodes[e.b];
          const dx=b.x-a.x, dy=b.y-a.y;
          const d=Math.hypot(dx,dy)||1e-3, f=(d-0.17)*0.022;
          a.vx+=dx/d*f; a.vy+=dy/d*f; b.vx-=dx/d*f; b.vy-=dy/d*f;
        }
        for(let i=0;i<this.nodes.length;i++){
          for(let j=i+1;j<this.nodes.length;j++){
            const a=this.nodes[i], b=this.nodes[j];
            const dx=b.x-a.x, dy=b.y-a.y;
            const d2=dx*dx+dy*dy;
            if(d2>0.10||d2<1e-8) continue;
            const f=0.00075/Math.max(d2,4e-4);
            a.vx-=dx*f; a.vy-=dy*f; b.vx+=dx*f; b.vy+=dy*f;
          }
        }
        for(const n of this.nodes){
          n.x+=n.vx; n.y+=n.vy; n.vx*=0.72; n.vy*=0.72;
          n.x=clamp(n.x,-0.95,0.95); n.y=clamp(n.y,-0.92,0.92);
        }
      }
    },

    /** Navigation : zoom molette centré curseur, pan au glisser, focus au clic. */
    bindNav(cv){
      let drag=null;
      cv.addEventListener('pointermove',(e)=>{
        if(drag){
          this.view.ox += e.clientX-drag.x; this.view.oy += e.clientY-drag.y;
          drag={x:e.clientX,y:e.clientY};
          return;
        }
        this.pick(e,cv,false);
      });
      cv.addEventListener('pointerdown',(e)=>{
        drag={x:e.clientX,y:e.clientY}; this._moved=false;
        cv.setPointerCapture?.(e.pointerId);
      });
      cv.addEventListener('pointerup',(e)=>{
        const moved = drag && (Math.abs(e.clientX-drag.x)>3 || Math.abs(e.clientY-drag.y)>3);
        drag=null;
        if(!moved) this.pick(e,cv,true);   // un glissement n'est pas un clic
      });
      cv.addEventListener('pointerleave',()=>{ drag=null; });
      cv.addEventListener('wheel',(e)=>{
        e.preventDefault();
        const box=cv.getBoundingClientRect();
        const mx=e.clientX-box.left-box.width/2-this.view.ox;
        const my=e.clientY-box.top-box.height/2-this.view.oy;
        const before=this.view.k;
        this.view.k=clamp(this.view.k*(e.deltaY<0?1.15:0.87), 0.35, 6);
        const r=this.view.k/before;
        this.view.ox -= mx*(r-1); this.view.oy -= my*(r-1);
      }, {passive:false});

      byId('v5MemSearch')?.addEventListener('input',(e)=>{
        this.query=String(e.target.value||'').toLowerCase();
        this.highlight(this.query);
      });
      byId('v5MemRecenter')?.addEventListener('click',()=>{ this.view.ox=0; this.view.oy=0; });
      byId('v5MemReset')?.addEventListener('click',()=>this.resetView());
      if(!this._esc){
        this._esc=(e)=>{ if(e.key==='Escape' && this.raf) this.resetView(); };
        window.addEventListener('keydown', this._esc);
      }
    },

    resetView(){
      this.view={k:1,ox:0,oy:0};
      this.focus=null; this.sel=null; this.hi=new Set(); this.query='';
      const s=byId('v5MemSearch'); if(s) s.value='';
      const p=byId('v5MemPanel'); if(p) p.hidden=true;
    },

    /** Recadre le graphe pour qu'il occupe vraiment la surface. */
    normalise(){
      let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;
      for(const n of this.nodes){ x0=Math.min(x0,n.x); x1=Math.max(x1,n.x); y0=Math.min(y0,n.y); y1=Math.max(y1,n.y); }
      const sx=(x1-x0)||1, sy=(y1-y0)||1, k=1.76/Math.max(sx,sy);
      const mx=(x0+x1)/2, my=(y0+y1)/2;
      for(const n of this.nodes){ n.x=(n.x-mx)*k; n.y=(n.y-my)*k; }
    },

    project(cv){
      const box=cv.getBoundingClientRect();
      const S=Math.min(box.width,box.height)*0.46;
      return {cx:box.width/2, cy:box.height/2, S, box};
    },

    pick(ev,cv,click){
      const {cx,cy,S,box}=this.project(cv);
      const mx=ev.clientX-box.left, my=ev.clientY-box.top;
      const K=this.view.k, OX=this.view.ox, OY=this.view.oy;
      let best=null, bd=14;
      for(const n of this.nodes){
        const d=Math.hypot(cx+OX+n.x*S*K-mx, cy+OY+n.y*S*K-my);
        if(d<bd){ bd=d; best=n; }
      }
      this.hover=best;
      cv.style.cursor=best?'pointer':'default';
      if(click){ if(best) this.select(best); else this.blur(); }
    },

    blur(){
      this.focus=null; this.sel=null;
      if(!this.query) this.hi=new Set();
      const p=byId('v5MemPanel'); if(p) p.hidden=true;
    },

    select(node){
      this.sel=node;
      // Focus : le nœud, ses relations directes et ses voisins ; le reste s'atténue.
      const idx=this.nodes.indexOf(node);
      const set=new Set([idx]);
      let frontier=new Set([idx]);
      for(let d=0; d<Math.max(1,this.depth); d++){
        const next=new Set();
        for(const e of this.edges){
          if(frontier.has(e.a) && !set.has(e.b)) next.add(e.b);
          if(frontier.has(e.b) && !set.has(e.a)) next.add(e.a);
        }
        next.forEach(i=>set.add(i));
        frontier=next;
        if(!next.size) break;
      }
      this.focus=idx; this.hi=set;
      const p=byId('v5MemPanel'); if(!p) return;
      const links=this.edges.filter(e=>this.nodes[e.a]===node||this.nodes[e.b]===node)
        .map(e=>this.nodes[e.a]===node?this.nodes[e.b]:this.nodes[e.a]);
      const meta=node.meta||{};
      p.hidden=false;
      p.innerHTML=`
        <header><span class="v5-kick">${esc(node.family)}</span><h3>${esc(node.label)}</h3>
          <button class="x" data-close>✕</button></header>
        <div class="rows">
          <div class="row"><span>Type</span><b>${esc(node.kind||'—')}</b></div>
          <div class="row"><span>Relations</span><b>${links.length}</b></div>
          ${meta.source?`<div class="row"><span>Source</span><b>${esc(String(meta.source).slice(0,60))}</b></div>`:''}
          ${meta.updated_at?`<div class="row"><span>Mis à jour</span><b>${new Date(meta.updated_at*1000).toLocaleString('fr-FR')}</b></div>`:''}
        </div>
        <div class="links">${links.slice(0,10).map(l=>`<button data-go="${esc(l.id)}">${esc(l.label)}</button>`).join('')}</div>`;
      p.querySelector('[data-close]').onclick=()=>{ p.hidden=true; this.sel=null; };
      p.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{
        const n=this.nodes.find(x=>x.id===b.dataset.go); if(n) this.select(n);
      });
    },

    /** Rappel réel : on éclaire le chemin effectivement emprunté. */
    highlight(terms){
      const q=fold(terms);
      this.hi=new Set();
      if(!q) return;
      for(let i=0;i<this.nodes.length;i++){
        const n=this.nodes[i];
        if(fold(n.label).includes(q) || fold(n.id).includes(q)) this.hi.add(i);
      }
      // un saut de voisinage : le contexte réellement mobilisé
      for(const e of this.edges){
        if(this.hi.has(e.a)) this.hi.add(e.b);
        else if(this.hi.has(e.b)) this.hi.add(e.a);
      }
      this.hiAt=performance.now();
    },

    start(){
      if(this.raf) cancelAnimationFrame(this.raf);
      const cv=byId('v5MemCanvas'); if(!cv) return;
      let last=performance.now();
      const loop=(now)=>{
        this.raf=requestAnimationFrame(loop);
        if(document.hidden || !cv.isConnected || cv.offsetParent===null) return;
        const dt=clamp((now-last)/1000,0,.1); last=now;
        this.frame(cv,dt);
      };
      this.raf=requestAnimationFrame(loop);
    },

    frame(cv,dt){
      const d=dprOf(), box=cv.parentElement.getBoundingClientRect();
      if(cv.width!==Math.round(box.width*d)){ cv.width=Math.round(box.width*d); cv.height=Math.round(box.height*d); }
      const ctx=cv.getContext('2d');
      ctx.setTransform(d,0,0,d,0,0);
      ctx.clearRect(0,0,box.width,box.height);
      this.t+=dt;
      const K=this.view.k, OX=this.view.ox, OY=this.view.oy;
      const cx=box.width/2+OX, cy=box.height/2+OY;
      const S=Math.min(box.width,box.height)*0.46*K;
      const hiOn=this.hi.size>0;

      for(const e of this.edges){
        const a=this.nodes[e.a], b=this.nodes[e.b];
        const lit=hiOn && this.hi.has(e.a) && this.hi.has(e.b);
        ctx.beginPath();
        ctx.moveTo(cx+a.x*S, cy+a.y*S); ctx.lineTo(cx+b.x*S, cy+b.y*S);
        // Le zoom sémantique module l'intensité des liens selon le niveau.
        const za = window.JarvisBrainZoom?._built
          ? window.JarvisBrainZoom.edgeAlpha(this, e, lit, hiOn) : null;
        ctx.strokeStyle = za!==null ? 'rgba(140,190,225,'+za+')'
          : (lit?'rgba(157,244,255,.55)':(hiOn?'rgba(120,160,185,.05)':'rgba(120,170,200,.13)'));
        ctx.lineWidth=lit?1.5:0.8;
        ctx.stroke();
      }
      for(let i=0;i<this.nodes.length;i++){
        const n=this.nodes[i], c=this.fam[n.family]||[120,180,220];
        const lit=hiOn && this.hi.has(i);
        const sel=this.sel===n, hov=this.hover===n;
        const r=(2.2+Math.min(4,n.weight*0.9))*(lit?1.5:1)*(sel?1.6:1);
        const a=hiOn ? (lit?1:0.14) : (0.55+0.25*Math.sin(this.t*1.2+i));
        ctx.beginPath(); ctx.arc(cx+n.x*S, cy+n.y*S, r, 0, TAU);
        ctx.fillStyle=`rgba(${c[0]},${c[1]},${c[2]},${a})`;
        if(lit||sel||hov){ ctx.shadowBlur=12; ctx.shadowColor=`rgba(${c[0]},${c[1]},${c[2]},.9)`; }
        ctx.fill(); ctx.shadowBlur=0;
        // Les libellés sont pris en charge par le zoom sémantique quand il est
        // chargé : sans cela, les deux couches écriraient l'une sur l'autre.
        if(!window.JarvisBrainZoom && (sel||hov||(lit&&n.weight>1))){
          ctx.font='10px ui-monospace, monospace';
          ctx.fillStyle='rgba(230,246,255,.85)';
          ctx.fillText(String(n.label).slice(0,28), cx+n.x*S+r+5, cy+n.y*S+3);
        }
      }
    },
  };

  /* ======================================================================
     3. TOOLS — recherche, catégories, état (plus 95 cartes)
     ====================================================================== */
  /* Famille déduite de l'identifiant réel de l'outil. Un outil dont le type
     n'est pas reconnaissable va dans OTHER : on ne l'affecte pas au hasard. */
  const TOOL_FAMILIES = [
    ['SSH',           /^(ssh|sftp|scp).|rsync/],
    ['BROWSER',       /^(browser|playwright|puppeteer)./],
    ['WEB',           /^(web|http|url|scrape|search)./],
    ['FILES',         /^(fs|file|files|folder|doc|documents)./],
    ['CODE',          /^(code|git|editor|repo|build|npm|python)./],
    ['DATABASE',      /^(db|sql|sqlite|postgres|mysql|mongo)./],
    ['API',           /^(api|rest|graphql|webhook|n8n)./],
    ['COMMUNICATION', /^(mail|email|discord|slack|sms|telegram|notify)./],
    ['MEDIA',         /^(image|video|audio|blender|comfy|render|avatar)./],
    ['AI',            /^(llm|model|agent|embed|vision|prompt)./],
    ['AUTOMATION',    /^(workflow|automation|schedule|cron|task)./],
    ['MEMORY',        /^(memory|brain|knowledge|recall|conversation)./],
    ['SECURITY',      /^(security|auth|secret|vault|permission)./],
    ['DATA',          /^(sheet|csv|xlsx|data|table|export|import)./],
    ['SYSTEM',        /^(system|app|process|power|screen|clipboard|terminal|shell|cmd)./],
  ];
  function toolFamily(tool){
    const id=String(tool.id||'').toLowerCase();
    for(const [name,re] of TOOL_FAMILIES){ if(re.test(id)) return name; }
    return 'OTHER';
  }

  /* Niveau de risque harmonisé à partir du champ réel du backend. */
  function toolRisk(tool){
    const r=String(tool.risk||'').toLowerCase();
    if(/destruct|critical|danger/.test(r)) return 'CRITICAL';
    if(/sensitive|high|exec/.test(r)) return 'HIGH';
    if(/write|safe_write|modif/.test(r)) return 'MEDIUM';
    if(/read|safe|lecture/.test(r)) return 'LOW';
    return r ? 'MEDIUM' : 'LOW';
  }

  const Tools = {
    async render(){
      if(!byId('page-tools')) return;
      const res=await J.get('/api/tools').catch(()=>null);
      const tools=(res && res.tools)||[];
      if(!tools.length) return;
      const host=mountView('page-tools','v5-tools');        // après le réseau
      if(!host) return;
      this.tools=tools.map(t=>({...t, family:toolFamily(t), level:toolRisk(t)}));
      const order=['WEB','FILES','SYSTEM','CODE','DATABASE','SSH','API','BROWSER',
        'COMMUNICATION','MEDIA','AI','AUTOMATION','MEMORY','SECURITY','DATA','OTHER'];
      const present=new Set(this.tools.map(t=>t.family));
      const cats=order.filter(f=>present.has(f));
      host.innerHTML=`
        <div class="v5-sec-head">
          <span class="v5-kick">OUTILS</span>
          <h2>${tools.length} outils · ${cats.length} familles</h2>
          <input class="v5-search" id="v5ToolSearch" placeholder="Rechercher un outil…" />
        </div>
        <div class="v5-chips" id="v5ToolCats">
          <button class="on" data-cat="">Tous</button>
          ${cats.map(c=>`<button data-cat="${esc(c)}">${esc(c)} <s>${this.tools.filter(t=>t.family===c).length}</s></button>`).join('')}
        </div>
        <div class="v5-tool-list" id="v5ToolList"></div>`;
      this.cat=''; this.q='';
      byId('v5ToolSearch').addEventListener('input',(e)=>{ this.q=fold(e.target.value); this.list(); });
      byId('v5ToolCats').addEventListener('click',(e)=>{
        const b=e.target.closest('[data-cat]'); if(!b) return;
        byId('v5ToolCats').querySelectorAll('button').forEach(x=>x.classList.remove('on'));
        b.classList.add('on'); this.cat=b.dataset.cat; this.list();
      });
      this.list();
    },
    list(){
      const host=byId('v5ToolList'); if(!host) return;
      const rows=this.tools.filter(t=>
        (!this.cat || t.family===this.cat) &&
        (!this.q || fold(t.id+' '+t.name+' '+(t.description||'')+' '+t.family).includes(this.q)));
      host.innerHTML=rows.slice(0,120).map(t=>`
        <div class="v5-tool ${t.enabled===false?'off':''}" title="${esc(t.description||'')}">
          <b>${esc(t.name)}</b>
          <code>${esc(t.id)}</code>
          <span class="cat">${esc(t.family)}</span>
          ${t.connector_type?`<span class="conn">${esc(t.connector_type)}</span>`:''}
          <span class="risk lvl-${esc(t.level.toLowerCase())}">${esc(t.level)}</span>
          <span class="state">${t.enabled===false?'désactivé':'actif'}</span>
        </div>`).join('') || '<p class="v5-empty">Aucun outil ne correspond.</p>';
    },
  };

  /* ======================================================================
     BLOG — tableau éditorial réel, sans publication implicite
     ====================================================================== */
  const Blog = {
    async render(){
      const page=byId('page-blog'); if(!page) return;
      const res=await J.get('/api/blog/dashboard').catch(()=>null);
      if(!res || !res.ok){ page.innerHTML='<div class="v5-empty">Blog indisponible.</div>'; return; }
      const b=res.buckets||{};
      const card=(label,key)=>`<div class="v5-blog-col"><header><span>${label}</span><b>${(b[key]||[]).length}</b></header><div class="v5-blog-list">${(b[key]||[]).map(p=>`<article class="v5-blog-card" data-post="${esc(p.id)}"><b>${esc(p.title||'Sans titre')}</b><small>${esc(p.status||'')}</small><div class="v5-blog-actions"><button data-preview="${esc(p.id)}">Preview</button>${key==='drafts'?'<button data-edit="'+esc(p.id)+'">Modifier</button><button data-publish="'+esc(p.id)+'">Publier</button><button data-publish-discord="'+esc(p.id)+'">Publier + Discord</button>':''}</div></article>`).join('')||'<div class="v5-empty">Aucun élément.</div>'}</div></div>`;
      const host=mountView('page-blog','v5-blog'); if(!host) return;
      host.innerHTML=`<div class="v5-sec-head"><span class="v5-kick">JARVIS_BLOG_PUBLISHER_V1</span><h2>Editorial control room</h2><button class="v5-btn" id="v5BlogRefresh">Rafraîchir</button></div><div class="v5-blog-grid">${card('BROUILLONS','drafts')}${card('À VÉRIFIER','review')}${card('PUBLIÉS','published')}${card('ÉCHECS','failures')}</div><aside class="v5-blog-preview" id="v5BlogPreview" hidden></aside>`;
      byId('v5BlogRefresh')?.addEventListener('click',()=>this.render());
      host.querySelectorAll('[data-preview]').forEach(x=>x.onclick=()=>this.preview(x.dataset.preview,b));
      host.querySelectorAll('[data-edit]').forEach(x=>x.onclick=()=>this.preview(x.dataset.edit,b,true));
      host.querySelectorAll('[data-publish],[data-publish-discord]').forEach(x=>x.onclick=()=>toast('Publication manuelle via l’agent Blog requise.', 'warn'));
    },
    preview(id,b,edit=false){
      const all=[...(b.drafts||[]),...(b.review||[]),...(b.published||[])]; const p=all.find(x=>String(x.id)===String(id)); const box=byId('v5BlogPreview'); if(!p||!box)return;
      box.hidden=false; box.innerHTML=`<header><span class="v5-kick">PREVIEW ARTICLE</span><button class="x">×</button></header><h3>${esc(p.title||'')}</h3><div class="v5-blog-content">${p.content||esc(p.excerpt||'')}</div>${edit?'<button class="v5-btn">Modifier dans l’éditeur</button>':''}`; box.querySelector('.x').onclick=()=>box.hidden=true;
    }
  };

  /* ======================================================================
     4. SETTINGS — navigation compacte, sans seconde sidebar
     ====================================================================== */
  const SettingsV5 = {
    ORDER:[['general','GENERAL'],['voice','VOICE'],['appearance','AVATAR & APPARENCE'],['blog','BLOG / DISCORD'],
      ['image','IMAGE GENERATION'],['connectors','CONNECTORS'],['tools','TOOLS'],
      ['memory','MEMORY'],['security','SECURITY'],['automation','AUTOMATION'],
      ['blender','ATELIER 3D'],['ai','AI PROVIDERS'],['editor','CODE EDITOR'],
      ['notifications','NOTIFICATIONS'],['logs','LOGS'],['developer','DEVELOPER']],

    render(){
      const page=byId('page-settings'); if(!page) return;
      this.wrap();
      let host=page.querySelector(':scope > .v5-view-host');
      if(!host){
        host=document.createElement('div');
        host.className='v5-view-host v5-settings';
        page.prepend(host);
      }
      const cur=(SET() && SET().section)||'general';
      host.innerHTML=`
        <div class="v5-sec-head"><span class="v5-kick">RÉGLAGES</span><h2>Configuration de JARVIS</h2></div>
        <div class="v5-chips" id="v5SetChips">
          ${this.ORDER.map(([id,label])=>
            `<button data-sec="${id}" class="${id===cur?'on':''}">${label}</button>`).join('')}
        </div>`;
      host.querySelector('#v5SetChips').addEventListener('click',(e)=>{
        const b=e.target.closest('[data-sec]'); if(!b) return;
        host.querySelectorAll('#v5SetChips button').forEach(x=>x.classList.remove('on'));
        b.classList.add('on');
        // On pilote la navigation héritée : tous les réglages restent actifs.
        const legacy=page.querySelector(`.settings-nav [data-sec="${CSS.escape(b.dataset.sec)}"]`);
        if(legacy) legacy.click();
        else SET()?.render?.(page, b.dataset.sec);
        setTimeout(()=>this.decorate(page), 120);
      });
      this.decorate(page);
    },

    /** Settings.render() remplace l'innerHTML de la page : on se réinstalle
        après coup, sinon la barre compacte disparaît au changement d'onglet. */
    wrap(){
      const S=SET();
      if(!S || S.__v5) return;
      S.__v5=true;
      const orig=S.render.bind(S);
      S.render=async (el, section)=>{
        const r=await orig(el, section);
        setTimeout(()=>{ this.render(); }, 40);
        return r;
      };
    },

    /** Ajoute le sélecteur d'interface dans Developer (V4 reste accessible). */
    decorate(page){
      const pane=page.querySelector('#settingsPane');
      const sec=(SET() && SET().section)||'';
      if(sec!=='developer' || !pane || pane.querySelector('#v5UiMode')) return;
      const box=document.createElement('div');
      box.className='card'; box.id='v5UiMode';
      const mode=(()=>{ try{ return localStorage.getItem('JARVIS_UI_MODE')||'spatial_v5'; }catch(_){ return 'spatial_v5'; } })();
      box.innerHTML=`
        <div class="card-head"><h2>INTERFACE</h2></div>
        <div class="card-body">
          <p class="text-dim" style="font-size:11px;margin:0 0 10px">
            Spatial OS V5 est l'interface par défaut. Legacy V4 reste disponible
            le temps de la migration.</p>
          <div class="v5-chips">
            <button data-ui="spatial_v5" class="${mode==='spatial_v5'?'on':''}">Spatial V5</button>
            <button data-ui="legacy_v4" class="${mode==='legacy_v4'?'on':''}">Legacy V4</button>
          </div>
        </div>`;
      pane.prepend(box);
      box.querySelectorAll('[data-ui]').forEach(b=>b.onclick=()=>{
        try{ localStorage.setItem('JARVIS_UI_MODE', b.dataset.ui); }catch(_){ /* stockage indisponible */ }
        location.reload();
      });
    },
  };

  /* ======================================================================
     Câblage : pages + événements réels
     ====================================================================== */
  const Modules = {
    init(){
      if(this._bound) return; this._bound=true;
      SettingsV5.wrap();          // avant tout rendu hérité de Settings

      window.addEventListener('jarvis:page', (e)=>{
        const p=e.detail?.page;
        // Arrêt franc des boucles des vues quittées : pas de rAF orphelin.
        if(p!=='agents' && Agents.raf){ cancelAnimationFrame(Agents.raf); Agents.raf=null; }
        if(!['memory','brain','knowledge'].includes(p) && Memory.raf){
          cancelAnimationFrame(Memory.raf); Memory.raf=null;
        }
        // Un léger différé laisse la page héritée finir son rendu.
        setTimeout(()=>{
          if(p==='agents') Agents.render();
           else if(p==='memory'||p==='brain'||p==='knowledge') Memory.render();
           else if(p==='tools') Tools.render();
           else if(p==='blog') Blog.render();
          else if(p==='settings') SettingsV5.render();
          if(p==='settings') setTimeout(()=>SettingsV5.render(), 700);
        }, 160);
      });

      if(typeof J!=='undefined' && typeof J.on==='function'){
        J.on('agent.started',  (d)=>{ Agents.flow(d?.id||d?.name,  1); Agents.refresh(); });
        J.on('agent.progress', (d)=>{ Agents.flow(d?.id||d?.name,  1); });
        J.on('agent.completed',(d)=>{ Agents.flow(d?.id||d?.name, -1); Agents.refresh(); });
        J.on('agent.failed',   (d)=>{ Agents.flow(d?.id||d?.name, -1); Agents.refresh(); });
        J.on('agent.idle',     ()=> Agents.refresh());
        // Un rappel mémoire réel éclaire le chemin dans le graphe.
        J.on('brain.search', (d)=> Memory.highlight(d?.query||''));
        J.on('brain.path',   (d)=> Memory.highlight(d?.label||d?.query||''));
      }
    },
  };

  Agents.refresh = async function(){
    if(!this.nodes.length) return;
    const res=await J.get('/api/agents').catch(()=>null);
    if(res && res.agents) this.sync(res.agents);
  };

  window.JarvisSpatialModules = { Modules, Agents, Memory, Tools, Blog, SettingsV5 };
  const start=()=>Modules.init();
  if(document.readyState==='loading') addEventListener('DOMContentLoaded',start); else start();
})();
