/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_ux.js  (PHASE 3.1)

   1. Brain inspectable  : clic sur le noyau → panneau d'observation.
   2. Command bar        : historique ↑ / ↓, Ctrl+K, Escape.
   3. Palette            : recherche de vues, agents, outils, réglages, mémoire.
   4. Réglages           : recherche de paramètres.
   5. Accessibilité      : libellés, focus clavier, rôles.

   Règle : le panneau du Brain ne montre que des faits OBSERVABLES — état
   courant, zones sollicitées, sources réellement consultées, agents réellement
   appelés, derniers rappels. Jamais de chaîne de pensée du modèle.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id)=>document.getElementById(id);
  const esc = (s)=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const MAX_HISTORY = 60;
  const fold = (s)=>String(s??'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');

  /* ======================================================================
     1. BRAIN INSPECTABLE
     ====================================================================== */
  const BrainPanel = {
    open:false,
    recalls:[],      // rappels mémoire réels
    sources:[],      // outils/sources réellement consultés
    agents:[],       // agents réellement sollicités

    init(){
      const wrap = byId('v5BrainWrap');
      if(!wrap) return;
      wrap.style.pointerEvents='auto';
      wrap.setAttribute('role','button');
      wrap.setAttribute('tabindex','0');
      wrap.setAttribute('aria-label','Ouvrir le Brain Atlas');
      wrap.addEventListener('click',()=>{
        window.App?.goto('memory');
        const rec=window.JarvisBrainData?.lastRecall;
        if(rec?.ids?.length){
          setTimeout(()=>window.JarvisBrainInspector?.Inspector.focus(rec.ids[0]), 1100);
        }
      });
      wrap.addEventListener('keydown',(e)=>{
        if(e.key==='Enter'||e.key===' '){
          e.preventDefault();
          window.App?.goto('memory');
        }
      });

      if(typeof J!=='undefined' && typeof J.on==='function'){
        const push=(list,item)=>{ list.unshift({...item, at:Date.now()}); if(list.length>12) list.pop(); };
        J.on('brain.search',(d)=>{ push(this.recalls,{label:d?.query||'recherche', kind:'search'}); this.refresh(); });
        J.on('brain.path',  (d)=>{ push(this.recalls,{label:d?.label||d?.query||'chemin', kind:'path'}); this.refresh(); });
        J.on('memory.created',(d)=>{ push(this.recalls,{label:d?.title||'souvenir créé', kind:'write'}); this.refresh(); });
        J.on('tool.started',(d)=>{ push(this.sources,{label:d?.name||d?.tool_id||'outil'}); this.refresh(); });
        J.on('agent.started',(d)=>{ push(this.agents,{label:d?.name||d?.id||'agent', action:d?.action||''}); this.refresh(); });
      }
      window.addEventListener('jarvis:brain-state',()=>{ if(this.open) this.refresh(); });
    },

    toggle(){ this.open ? this.close() : this.show(); },

    show(){
      let el = byId('v5BrainPanel');
      if(!el){
        el=document.createElement('aside');
        el.id='v5BrainPanel';
        el.className='v5-brain-panel';
        el.setAttribute('role','dialog');
        el.setAttribute('aria-label','Observation du Brain');
        document.body.appendChild(el);
      }
      this.open=true;
      el.hidden=false;
      this.refresh();
      setTimeout(()=>el.classList.add('in'),20);
    },

    close(){
      const el=byId('v5BrainPanel');
      this.open=false;
      if(!el) return;
      el.classList.remove('in');
      setTimeout(()=>{ if(!this.open) el.hidden=true; },260);
    },

    async refresh(){
      const el=byId('v5BrainPanel');
      if(!el || !this.open) return;
      const brain = window.ObsidianBrain;
      const state = brain?.state || 'IDLE';
      const heat = brain?.instances?.[0]?.zoneHeat || {};
      const hot = Object.keys(heat).filter(k=>heat[k]>0.4);
      const st = (typeof J!=='undefined' && J.state && J.state.status) || null;
      const ago = (t)=>{
        const s=Math.round((Date.now()-t)/1000);
        return s<60 ? `il y a ${s} s` : `il y a ${Math.round(s/60)} min`;
      };
      const list = (items, empty, render)=> items.length
        ? items.slice(0,6).map(render).join('')
        : `<p class="v5-empty">${empty}</p>`;

      el.innerHTML = `
        <header>
          <span class="v5-kick">OBSERVATION</span>
          <h3>${esc(state)}</h3>
          <button class="x" data-close aria-label="Fermer">✕</button>
        </header>

        <div class="v5-bp-zones">
          ${['MEMORY','KNOWLEDGE','CONTEXT','TOOLS'].map(z=>
            `<span class="${hot.includes(z)?'on':''}">${z}</span>`).join('')}
        </div>

        <section>
          <h4>Contexte actif</h4>
          <div class="rows">
            <div class="row"><span>Vue</span><b>${esc(window.JarvisSpatial?.context||'—')}</b></div>
            <div class="row"><span>Zones sollicitées</span><b>${hot.length?esc(hot.join(', ')):'aucune'}</b></div>
            <div class="row"><span>Travaux en cours</span><b>${window.JarvisSpatialEvents?._busy ?? 0}</b></div>
            <div class="row"><span>Mémoire</span><b>${st? (st.memory?.knowledge ?? '—')+' savoirs' : '—'}</b></div>
          </div>
        </section>

        <section>
          <h4>Rappels récents</h4>
          ${list(this.recalls,'Aucun rappel observé depuis l\'ouverture.',
            (r)=>`<div class="v5-bp-item"><b>${esc(r.label)}</b><span>${esc(r.kind)} · ${ago(r.at)}</span></div>`)}
        </section>

        <section>
          <h4>Sources consultées</h4>
          ${list(this.sources,'Aucun outil appelé depuis l\'ouverture.',
            (s)=>`<div class="v5-bp-item"><b>${esc(s.label)}</b><span>${ago(s.at)}</span></div>`)}
        </section>

        <section>
          <h4>Agents sollicités</h4>
          ${list(this.agents,'Aucune délégation observée.',
            (a)=>`<div class="v5-bp-item"><b>${esc(a.label)}</b><span>${esc(a.action||'délégation')} · ${ago(a.at)}</span></div>`)}
        </section>

        <footer>
          <button class="v5-btn" data-goto-memory>Ouvrir le graphe mémoire</button>
        </footer>`;

      el.querySelector('[data-close]').onclick=()=>this.close();
      el.querySelector('[data-goto-memory]').onclick=()=>{ this.close(); window.App?.goto('memory'); };
    },
  };

  /* ======================================================================
     2. COMMAND BAR — historique et raccourcis
     ====================================================================== */
  const CommandBar = {
    history: [],
    cursor: -1,
    draft: '',

    init(){
      const input = byId('convInput');
      const form = byId('convForm');
      if(!input || !form) return;
      try{
        this.history = JSON.parse(localStorage.getItem('JARVIS_CMD_HISTORY') || '[]');
      }catch(_){ this.history = []; }

      form.addEventListener('submit',()=>{
        const v=String(input.value||'').trim();
        if(v) this.push(v);
      });
      // L'envoi passe par keydown Enter : on capte la valeur avant nettoyage.
      input.addEventListener('keydown',(e)=>{
        if(e.key==='Enter' && !e.shiftKey){
          const v=String(input.value||'').trim();
          if(v) this.push(v);
          return;
        }
        if(e.key!=='ArrowUp' && e.key!=='ArrowDown') return;
        // Dans un texte multiligne, les flèches servent d'abord au curseur.
        const multiline = input.value.includes('\n');
        if(multiline) return;
        if(!this.history.length) return;
        e.preventDefault();
        if(e.key==='ArrowUp'){
          if(this.cursor===-1) this.draft=input.value;
          this.cursor=Math.min(this.cursor+1, this.history.length-1);
        }else{
          this.cursor=Math.max(this.cursor-1, -1);
        }
        input.value = this.cursor===-1 ? this.draft : this.history[this.cursor];
        input.setSelectionRange(input.value.length, input.value.length);
      });
    },

    push(text){
      if(this.history[0]===text) { this.cursor=-1; return; }
      this.history.unshift(text);
      if(this.history.length>MAX_HISTORY) this.history.pop();
      this.cursor=-1; this.draft='';
      try{ localStorage.setItem('JARVIS_CMD_HISTORY', JSON.stringify(this.history)); }catch(_){ /* stockage indisponible */ }
    },
  };

  /* ======================================================================
     3. PALETTE — recherche d'actions sûres
     ====================================================================== */
  const Palette = {
    open:false, items:[], filtered:[], index:0,

    async build(){
      const items=[];
      const go=(page,section)=>()=>window.App?.goto(page, section?{section}:undefined);
      [['Accueil','command'],['Chat','chat'],['Analyse','analyses'],['Synchronisation','servers'],
       ['Agents','agents'],['Mémoire','memory'],['Outils','tools'],['Terminal','terminal'],
       ['Réglages','settings'],['Brain Atlas','brain'],['Conversations','conversations'],
       ['Tâches','tasks'],['Automatisations','workflows'],['Avatar Studio','avatar-studio']]
        .forEach(([label,page])=>items.push({kind:'VUE', label, run:go(page)}));

      ['general','voice','appearance','connectors','memory','security','automation','developer']
        .forEach(sec=>items.push({kind:'RÉGLAGE', label:'Réglages · '+sec, run:go('settings',sec)}));

      try{
        const ag=await J.get('/api/agents');
        (ag.agents||[]).forEach(a=>items.push({
          kind:'AGENT', label:a.name||a.id, hint:a.role||'',
          run:()=>{ window.App?.goto('agents'); },
        }));
      }catch(_){ /* agents indisponibles : la palette reste utilisable */ }

      try{
        const tl=await J.get('/api/tools');
        (tl.tools||[]).slice(0,200).forEach(t=>items.push({
          kind:'OUTIL', label:t.name||t.id, hint:t.id,
          // Une entrée d'outil ne l'exécute pas : elle l'affiche.
          run:()=>{ window.App?.goto('tools'); setTimeout(()=>{
            const s=byId('v5ToolSearch'); if(s){ s.value=t.id; s.dispatchEvent(new Event('input')); }
          }, 400); },
        }));
      }catch(_){ /* outils indisponibles */ }

      items.push({kind:'ACTION', label:'Dry run de synchronisation', run:go('servers')});
      items.push({kind:'ACTION', label:'Interface : Legacy V4', run:()=>{
        try{ localStorage.setItem('JARVIS_UI_MODE','legacy_v4'); }catch(_){ /* idem */ }
        location.reload();
      }});
      this.items=items;
      return items;
    },

    async show(){
      if(!this.items.length) await this.build();
      let el=byId('v5Palette');
      if(!el){
        el=document.createElement('div');
        el.id='v5Palette'; el.className='v5-palette';
        el.innerHTML=`
          <div class="v5-palette-box" role="dialog" aria-label="Centre de commande">
            <input id="v5PaletteInput" placeholder="Rechercher une vue, un agent, un outil, un réglage…"
                   autocomplete="off" aria-label="Rechercher" />
            <div class="v5-palette-list" id="v5PaletteList" role="listbox"></div>
          </div>`;
        document.body.appendChild(el);
        el.addEventListener('pointerdown',(e)=>{ if(e.target===el) this.close(); });
        byId('v5PaletteInput').addEventListener('input',()=>this.filter());
        byId('v5PaletteInput').addEventListener('keydown',(e)=>this.key(e));
      }
      this.open=true; el.hidden=false;
      requestAnimationFrame(()=>el.classList.add('in'));
      const input=byId('v5PaletteInput');
      input.value=''; input.focus();
      this.filter();
    },

    close(){
      const el=byId('v5Palette');
      this.open=false;
      if(!el) return;
      el.classList.remove('in');
      setTimeout(()=>{ if(!this.open) el.hidden=true; },180);
    },

    filter(){
      const q=fold(byId('v5PaletteInput')?.value||'').trim();
      this.filtered = !q ? this.items.slice(0,12)
        : this.items.filter(i=>fold(i.label+' '+(i.hint||'')+' '+i.kind).includes(q)).slice(0,40);
      this.index=0;
      this.render();
    },

    render(){
      const list=byId('v5PaletteList');
      if(!list) return;
      list.innerHTML = this.filtered.length
        ? this.filtered.map((i,n)=>`
          <button class="${n===this.index?'on':''}" data-i="${n}" role="option"
                  aria-selected="${n===this.index}">
            <span class="k">${esc(i.kind)}</span>
            <b>${esc(i.label)}</b>
            ${i.hint?`<em>${esc(i.hint)}</em>`:''}
          </button>`).join('')
        : '<p class="v5-empty">Aucun résultat.</p>';
      list.querySelectorAll('[data-i]').forEach(b=>{
        b.onclick=()=>this.run(Number(b.dataset.i));
      });
    },

    key(e){
      if(e.key==='ArrowDown'){ e.preventDefault(); this.index=Math.min(this.index+1,this.filtered.length-1); this.render(); }
      else if(e.key==='ArrowUp'){ e.preventDefault(); this.index=Math.max(this.index-1,0); this.render(); }
      else if(e.key==='Enter'){ e.preventDefault(); this.run(this.index); }
      else if(e.key==='Escape'){ e.preventDefault(); this.close(); }
    },

    run(i){
      const item=this.filtered[i];
      this.close();
      if(item && typeof item.run==='function') item.run();
    },
  };

  /* ======================================================================
     4. RÉGLAGES — recherche de paramètres
     ====================================================================== */
  const SettingsSearch = {
    attach(){
      const page=byId('page-settings');
      const host=page?.querySelector(':scope > .v5-view-host');
      if(!host || host.querySelector('#v5SetSearch')) return;
      const bar=host.querySelector('.v5-sec-head');
      if(!bar) return;
      const input=document.createElement('input');
      input.className='v5-search'; input.id='v5SetSearch';
      input.placeholder='Rechercher un réglage…';
      input.setAttribute('aria-label','Rechercher un réglage');
      bar.appendChild(input);
      input.addEventListener('input',()=>this.filter(input.value));
    },

    /* Filtrage sur le contenu réellement rendu par la page de réglages. */
    filter(q){
      const pane=byId('settingsPane');
      if(!pane) return;
      const query=fold(q).trim();
      const blocks=[...pane.children];
      if(!query){ blocks.forEach(b=>{ b.hidden=false; }); this.mark(pane,''); return; }
      blocks.forEach(b=>{ b.hidden = !fold(b.textContent).includes(query); });
      this.mark(pane, query);
    },

    mark(pane, query){
      pane.querySelectorAll('.v5-hit').forEach(n=>n.classList.remove('v5-hit'));
      if(!query) return;
      pane.querySelectorAll('label, .card-head h2, h3').forEach(n=>{
        if(fold(n.textContent).includes(query)) n.classList.add('v5-hit');
      });
    },
  };

  /* ======================================================================
     5. Raccourcis globaux + accessibilité de base
     ====================================================================== */
  function shortcuts(){
    window.addEventListener('keydown',(e)=>{
      const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'')
        || document.activeElement?.isContentEditable;

      // Ctrl/⌘ + K : palette si une touche Maj l'accompagne, sinon command bar.
      if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='k'){
        e.preventDefault();
        if(e.shiftKey) Palette.show();
        else byId('convInput')?.focus();
        return;
      }
      if((e.ctrlKey||e.metaKey) && e.key===' '){ e.preventDefault(); Palette.show(); return; }

      if(e.key==='Escape'){
        // Ordre de fermeture : palette → panneau Brain → panneau détail → menus.
        if(Palette.open){ Palette.close(); return; }
        if(BrainPanel.open){ BrainPanel.close(); return; }
        const detail=document.querySelector('.v5-node-panel:not([hidden])');
        if(detail){ detail.hidden=true; return; }
        if(window.JarvisSpatial?._pop){ window.JarvisSpatial.closePop(); return; }
        if(inField && document.activeElement===byId('convInput')){ byId('convInput').blur(); return; }
      }
    });
  }

  function a11y(){
    // Libellés des contrôles icon-only du shell (rail déjà étiqueté).
    byId('v5HudSys')?.setAttribute('aria-label','Ouvrir les diagnostics système');
    byId('convForm')?.querySelector('.mic-btn')?.setAttribute('aria-label','Parler à JARVIS');
    byId('convForm')?.querySelector('.btn.primary.send')?.setAttribute('aria-label','Envoyer');
    byId('convInput')?.setAttribute('aria-label','Commande pour JARVIS');
    byId('v5Rail')?.setAttribute('role','navigation');
    byId('v5Chat')?.setAttribute('role','log');
  }

  const UX = {
    BrainPanel, CommandBar, Palette, SettingsSearch,
    init(){
      if(this._done) return; this._done=true;
      BrainPanel.init();
      CommandBar.init();
      shortcuts();
      a11y();
      window.addEventListener('jarvis:page',(e)=>{
        if(e.detail?.page==='settings') setTimeout(()=>SettingsSearch.attach(), 900);
      });
    },
  };

  window.JarvisUX = UX;
  const start=()=>setTimeout(()=>UX.init(), 400);
  if(document.readyState==='loading') addEventListener('DOMContentLoaded',start); else start();
})();
