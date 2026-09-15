/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_brain_inspector.js
   Inspecteur de nœud, aperçu au survol, recherche dans le contenu, recall.

   SÉCURITÉ : le contenu des fiches `knowledge` est écrit par des agents et
   des sources externes. Il est traité comme une donnée NON FIABLE :
   construction par nœuds de texte (`textContent`), jamais par innerHTML.
   Aucun HTML, aucun script, aucun gestionnaire d'événement ne peut en sortir.

   HONNÊTETÉ : le backend ne persiste ni date ni fréquence de rappel. Le
   dernier rappel est donc présenté comme une observation de session, et
   l'inspecteur le dit explicitement.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id)=>document.getElementById(id);
  const B = ()=>window.JarvisBrainData;
  const MEM = ()=>window.JarvisSpatialModules?.Memory;

  const TYPE_LABEL = {
    KNOWLEDGE:'CONNAISSANCE', CAPABILITY:'CAPACITÉ',
    MEMORY:'SOUVENIR', SYSTEM:'SYSTÈME',
  };

  /* ---------------------------------------------------------- rendu sûr -- */
  /** Écrit du texte non fiable sans jamais passer par innerHTML. */
  function safeText(host, text){
    const raw = String(text ?? '');
    // Découpage en paragraphes / lignes : mise en forme minimale, zéro balise.
    for(const block of raw.split(/\n{2,}/)){
      const p = document.createElement('p');
      p.className = 'v5-ins-p';
      const lines = block.split('\n');
      lines.forEach((line, i)=>{
        if(i) p.appendChild(document.createElement('br'));
        p.appendChild(document.createTextNode(line));
      });
      host.appendChild(p);
    }
  }
  const el = (tag, cls, text)=>{
    const n = document.createElement(tag);
    if(cls) n.className = cls;
    if(text !== undefined) n.textContent = String(text);
    return n;
  };
  const when = (ts)=> ts ? new Date(ts*1000).toLocaleString('fr-FR') : '';

  /* ===================================================================== */
  const Inspector = {
    advanced:false,
    current:null,

    panel(){
      let p = byId('v5Inspector');
      if(!p){
        p = document.createElement('aside');
        p.id = 'v5Inspector';
        p.className = 'v5-inspector';
        p.setAttribute('role','dialog');
        p.setAttribute('aria-label','Inspecteur de nœud');
        p.hidden = true;
        document.body.appendChild(p);
      }
      return p;
    },

    close(){
      const p = byId('v5Inspector');
      if(!p) return;
      p.classList.remove('in');
      this.current = null;
      setTimeout(()=>{ if(!this.current) p.hidden = true; }, 240);
    },

    async show(nodeId){
      const data = B();
      if(!data) return;
      await data.load();
      const detail = await data.detail(nodeId);
      if(!detail) return;
      this.current = detail.id;

      const p = this.panel();
      p.hidden = false;
      p.replaceChildren();
      requestAnimationFrame(()=>p.classList.add('in'));

      /* ---- en-tête : type visible, jamais deviné -------------------- */
      const head = el('header');
      head.appendChild(el('span','v5-kick', TYPE_LABEL[detail.type] || detail.type));
      head.appendChild(el('h3', null, detail.titre));
      const close = el('button','x','✕');
      close.setAttribute('aria-label','Fermer');
      close.onclick = ()=>this.close();
      head.appendChild(close);
      p.appendChild(head);

      const chips = el('div','v5-ins-chips');
      chips.appendChild(el('span','chip', detail.famille));
      chips.appendChild(el('span','chip', detail.nature));
      chips.appendChild(el('span','chip', detail.degre + ' relation' + (detail.degre>1?'s':'')));
      if(detail.fusionne_depuis.length){
        chips.appendChild(el('span','chip merged',
          'fusionné · ' + detail.fusionne_depuis.length + ' doublon' + (detail.fusionne_depuis.length>1?'s':'')));
      }
      p.appendChild(chips);

      const k = detail.fiche;

      /* ---- contenu réel --------------------------------------------- */
      if(k && k.content){
        const sec = el('section');
        sec.appendChild(el('h4', null, 'Contenu'));
        const body = el('div','v5-ins-content');
        safeText(body, k.content);          // rendu strictement textuel
        sec.appendChild(body);
        p.appendChild(sec);
      } else if(detail.type === 'KNOWLEDGE'){
        const sec = el('section');
        sec.appendChild(el('h4', null, 'Contenu'));
        sec.appendChild(el('p','v5-empty', detail.fiche_absente
          ? 'Fiche ' + detail.fiche_absente + ' non renvoyée par /api/knowledge.'
          : 'Aucun contenu stocké pour ce nœud.'));
        p.appendChild(sec);
      } else {
        const sec = el('section');
        sec.appendChild(el('h4', null, "Nature de l'élément"));
        const m = detail.meta || {};
        const txt = detail.type === 'CAPABILITY'
          ? 'Capacité de JARVIS (outil, connecteur ou automatisation) — ce n\'est pas un souvenir.'
          : 'Nœud structurel du Brain.';
        sec.appendChild(el('p','v5-ins-note', txt));
        const rows = el('div','rows');
        const add=(label,val)=>{ if(val===undefined||val===''||val===null) return;
          const r=el('div','row'); r.appendChild(el('span',null,label));
          r.appendChild(el('b',null,val)); rows.appendChild(r); };
        add('Identifiant outil', m.tool_id);
        add('Catégorie', m.category);
        add('Risque', m.risk);
        add('Connecteur', m.name);
        add('Type', m.type);
        add('État', m.status);
        add('Déclencheur', m.trigger);
        if(rows.children.length) sec.appendChild(rows);
        p.appendChild(sec);
      }

      /* ---- provenance et qualité, uniquement si présentes ------------ */
      if(k){
        const sec = el('section');
        sec.appendChild(el('h4', null, 'Provenance'));
        const rows = el('div','rows');
        const add=(label,val)=>{ if(val===undefined||val===''||val===null) return;
          const r=el('div','row'); r.appendChild(el('span',null,label));
          r.appendChild(el('b',null,val)); rows.appendChild(r); };
        add('Source', k.source);
        add('Nature', k.kind);
        add('Projet', k.project);
        add('Créée le', when(k.created_at));
        add('Modifiée le', when(k.updated_at));
        if(typeof k.confidence_score === 'number')
          add('Confiance', Math.round(k.confidence_score*100) + ' %');
        if(k.validation_count) add('Validations', k.validation_count);
        if(k.failure_count) add('Échecs', k.failure_count);
        if(k.last_validated_at) add('Vérifiée le', when(k.last_validated_at));
        add('Méthode de vérification', k.verification_method);
        sec.appendChild(rows);

        const tags = Array.isArray(k.tags) ? k.tags : [];
        if(tags.length){
          const t = el('div','v5-ins-tags');
          tags.forEach(x=>t.appendChild(el('span','tag', x)));
          sec.appendChild(t);
        }
        const tools = Array.isArray(k.tools) ? k.tools : [];
        if(tools.length){
          sec.appendChild(el('h4', null, 'Outils associés'));
          const t = el('div','v5-ins-tags');
          tools.forEach(x=>t.appendChild(el('span','tag tool', x)));
          sec.appendChild(t);
        }

        // evidence : présent sur 10 fiches sur 25 — affiché seulement si utile.
        let ev = k.evidence;
        if(typeof ev === 'string'){ try{ ev = JSON.parse(ev); }catch(_){ ev = null; } }
        if(ev && typeof ev === 'object' && Object.keys(ev).length){
          sec.appendChild(el('h4', null, 'Preuves'));
          const rows2 = el('div','rows');
          const map = {source_type:'Type de source', source_version:'Version',
            verified_at:'Vérifié le', docs_validated:'Documentation validée',
            tests_run:'Tests exécutés', tests_passed:'Tests réussis',
            error_reason:'Cause d\'erreur', test_scope:'Portée du test',
            source_hash:'Empreinte'};
          for(const [key,label] of Object.entries(map)){
            let v = ev[key];
            if(v===undefined || v==='' || v===null) continue;
            if(key==='verified_at') v = when(v);
            if(key==='source_hash') v = String(v).slice(0,16) + '…';
            if(typeof v === 'boolean') v = v ? 'oui' : 'non';
            const r=el('div','row'); r.appendChild(el('span',null,label));
            r.appendChild(el('b',null,String(v))); rows2.appendChild(r);
          }
          if(rows2.children.length) sec.appendChild(rows2);
        }
        p.appendChild(sec);
      }

      /* ---- relations cliquables ------------------------------------- */
      const rel = el('section');
      rel.appendChild(el('h4', null, 'Relié à (' + detail.relations.length + ')'));
      if(detail.relations.length){
        const list = el('div','v5-ins-rel');
        detail.relations.slice(0,24).forEach(r=>{
          const b = el('button', null);
          b.appendChild(el('i','arrow', r.sens==='sortant' ? '→' : '←'));
          b.appendChild(el('b', null, r.label));
          b.appendChild(el('em', null, r.kind));
          b.onclick = ()=>this.focus(r.id);
          list.appendChild(b);
        });
        rel.appendChild(list);
      } else {
        rel.appendChild(el('p','v5-empty','Aucune relation.'));
      }
      p.appendChild(rel);

      /* ---- rappel observé (non persistant) --------------------------- */
      const recall = B().lastRecall;
      if(recall && recall.ids.includes(detail.id)){
        const sec = el('section','v5-ins-recall');
        sec.appendChild(el('h4', null, 'Rappel observé'));
        if(recall.query) sec.appendChild(el('p','v5-ins-note', 'Requête : « ' + recall.query + ' »'));
        sec.appendChild(el('p','v5-empty',
          'Observation de cette session uniquement — le backend ne conserve ni date ni fréquence de rappel.'));
        p.appendChild(sec);
      }

      /* ---- mode technique ------------------------------------------- */
      const foot = el('footer');
      const lib = el('button','v5-btn','Ouvrir dans la Library');
      lib.onclick = ()=>{
        const L = window.JarvisBrainLibrary;
        if(!L) return;
        // On ouvre l'onglet correspondant au type réel du concept inspecté.
        L.show(detail.type === 'CAPABILITY' ? 'CAPABILITY'
          : detail.type === 'MEMORY' ? 'MEMORY' : 'KNOWLEDGE');
      };
      foot.appendChild(lib);
      const adv = el('button','v5-btn', this.advanced ? 'Masquer le technique' : 'Technique');
      adv.onclick = ()=>{ this.advanced = !this.advanced; this.show(detail.id); };
      foot.appendChild(adv);
      p.appendChild(foot);

      if(this.advanced){
        const sec = el('section','v5-ins-tech');
        sec.appendChild(el('h4', null, 'Technique'));
        const rows = el('div','rows');
        const add=(label,val)=>{ if(val===undefined||val===''||val===null) return;
          const r=el('div','row'); r.appendChild(el('span',null,label));
          r.appendChild(el('b',null,String(val))); rows.appendChild(r); };
        add('ID canonique', detail.id);
        add('Fusionné depuis', detail.fusionne_depuis.join(', '));
        add('Famille', detail.famille);
        add('Nature', detail.nature);
        add('Référence', detail.meta.ref_type ? detail.meta.ref_type + ' · ' + detail.meta.ref_id : '');
        if(k){
          add('ID fiche', k.id);
          add('Statut', k.status);
          add('Stockage', 'SQLite · table knowledge');
          add('Taille du contenu', k.content.length + ' caractères');
        } else {
          add('Stockage', detail.id.startsWith('br_') ? 'SQLite · table brain_nodes' : 'construit à la volée');
        }
        sec.appendChild(rows);
        p.appendChild(sec);
      }
    },

    /** Sélectionne et centre le nœud canonique dans le graphe, puis l'inspecte. */
    async focus(id){
      const data = B();
      const canon = data.alias.get(id) || id;
      const mem = MEM();
      if(mem && mem.nodes && mem.nodes.length){
        const target = mem.nodes.find(n=>n.id===canon);
        if(target){
          mem.select(target);
          mem.view.ox = -target.x * (Math.min(innerWidth,innerHeight)*0.46) * mem.view.k;
          mem.view.oy = -target.y * (Math.min(innerWidth,innerHeight)*0.46) * mem.view.k;
        }
      }
      this.show(canon);
    },
  };

  /* ===================================================== aperçu au survol */
  const Preview = {
    el:null,
    show(node, x, y){
      if(!node) return this.hide();
      if(!this.el){
        this.el = document.createElement('div');
        this.el.className = 'v5-preview';
        document.body.appendChild(this.el);
      }
      const data = B();
      const canon = data?.byId.get(data.alias.get(node.id) || node.id);
      const meta = canon?.meta || {};
      // Aucun appel réseau ici : on se contente du résumé déjà présent.
      const summary = meta.summary || meta.content || '';
      this.el.replaceChildren();
      this.el.appendChild(el('b', null, canon?.label || node.label));
      this.el.appendChild(el('span','t', TYPE_LABEL[canon?.type] || canon?.type || ''));
      if(summary) this.el.appendChild(el('p', null, String(summary).slice(0,160)));
      this.el.appendChild(el('span','r', (canon?.degree ?? 0) + ' relation'
        + ((canon?.degree ?? 0)>1?'s':'')));
      this.el.style.left = Math.min(x+16, innerWidth-260) + 'px';
      this.el.style.top = Math.min(y+16, innerHeight-150) + 'px';
      this.el.classList.add('in');
    },
    hide(){ this.el?.classList.remove('in'); },
  };

  /* ============================================== greffe sur la vue Memory */
  /** L'en-tête doit annoncer le graphe RÉELLEMENT affiché, pas le brut. */
  async function retitle(){
    const data = B(); if(!data) return;
    await data.load();
    const h = document.querySelector('#page-memory .v5-sec-head h2');
    if(!h || !data.stats) return;
    const st = data.stats;
    h.replaceChildren();
    h.appendChild(document.createTextNode(
      st.canonique.nodes + ' concepts · ' + st.canonique.edges + ' relations'));
    const note = el('small','v5-head-note',
      st.doublons_fusionnes + ' doublon' + (st.doublons_fusionnes>1?'s':'') + ' fusionné'
      + (st.doublons_fusionnes>1?'s':'') + ' · '
      + (st.par_type.KNOWLEDGE||0) + ' connaissances · '
      + (st.par_type.CAPABILITY||0) + ' capacités · '
      + (st.par_type.MEMORY||0) + ' souvenirs');
    h.appendChild(note);
  }

  /* La greffe sur le moteur du graphe se fait UNE fois ; l'installation des
     contrôles doit se refaire à chaque rendu de la vue, car son DOM est
     reconstruit. Confondre les deux empêchait la recherche de s'installer. */
  function attach(){
    const mem = MEM();
    if(!mem){ setTimeout(attach, 800); return; }

    if(!mem.__inspector){
      mem.__inspector = true;

      // 1. Le clic ouvre l'inspecteur au lieu du panneau minimal.
      const legacySelect = mem.select.bind(mem);
      mem.select = (node)=>{
        legacySelect(node);
        const p = byId('v5MemPanel');
        if(p) p.hidden = true;
        Inspector.show(node.id);
      };

      // 2. Le survol montre un aperçu, sans appel réseau.
      const legacyPick = mem.pick.bind(mem);
      mem.pick = (ev, cv, click)=>{
        legacyPick(ev, cv, click);
        if(click) return;
        if(mem.hover) Preview.show(mem.hover, ev.clientX, ev.clientY);
        else Preview.hide();
      };
    }

    bindControls();
  }

  /** Contrôles de la vue : réinstallés tant que le DOM est recréé. */
  function bindControls(){
    const input = byId('v5MemSearch');
    if(input && !input.__contentSearch){
      input.__contentSearch = true;
      input.placeholder = 'Rechercher dans le contenu…';
      let timer = null;
      input.addEventListener('input', ()=>{
        clearTimeout(timer);
        timer = setTimeout(()=>runSearch(String(input.value||'')), 180);
      });
      ensureResultBar();
      retitle();
    }
  }

  async function runSearch(query){
    const mem = MEM(); const data = B();
    if(!mem || !data) return;
    const host = ensureResultBar();
    if(!query.trim()){
      mem.hi = new Set(); mem.query = '';
      host.replaceChildren();
      host.hidden = true;
      return;
    }
    const res = await data.search(query);
    // Éclairer exactement les nœuds trouvés — ni plus, ni moins.
    const wanted = new Set(res.ids);
    mem.hi = new Set();
    mem.nodes.forEach((n,i)=>{ if(wanted.has(n.id)) mem.hi.add(i); });
    host.hidden = false;
    host.replaceChildren();
    const counts = Object.entries(res.par_type)
      .map(([t,n])=>n+' '+(TYPE_LABEL[t]||t).toLowerCase()).join(' · ');
    host.appendChild(el('span','n', res.total + ' résultat' + (res.total>1?'s':'')));
    if(counts) host.appendChild(el('span','c', counts));
    res.hits.slice(0,8).forEach(h=>{
      const b = el('button', null, h.node.label);
      if(h.inContent) b.appendChild(el('em', null, 'contenu'));
      b.onclick = ()=>Inspector.focus(h.node.id);
      host.appendChild(b);
    });
  }

  function ensureResultBar(){
    let bar = byId('v5MemResults');
    if(!bar){
      bar = document.createElement('div');
      bar.id = 'v5MemResults';
      bar.className = 'v5-mem-results';
      bar.hidden = true;
      byId('v5MemSearch')?.closest('.v5-graph-bar')?.after(bar);
    }
    return bar;
  }

  /* ------------------------------------------------------------- recall */
  function ensureRecallBanner(){
    let b = byId('v5Recall');
    if(!b){
      b = document.createElement('div');
      b.id = 'v5Recall';
      b.className = 'v5-recall';
      b.hidden = true;
      document.body.appendChild(b);
    }
    return b;
  }

  window.addEventListener('jarvis:brain-recall', (e)=>{
    const r = e.detail;
    const b = ensureRecallBanner();
    b.replaceChildren();
    b.hidden = false;
    b.appendChild(el('span','k','RECALLING'));
    if(r.query) b.appendChild(el('b', null, '« ' + r.query + ' »'));
    const n = r.ids.length || r.labels.length;
    b.appendChild(el('span','n', n + ' nœud' + (n>1?'s':'') + ' rappelé' + (n>1?'s':'')));
    (r.labels||[]).slice(0,4).forEach((l,i)=>{
      const btn = el('button', null, l);
      btn.onclick = ()=>{ const id=r.ids[i]; if(id) Inspector.focus(id); };
      b.appendChild(btn);
    });
    b.classList.add('in');
    clearTimeout(window.__recallTimer);
    window.__recallTimer = setTimeout(()=>{ b.classList.remove('in');
      setTimeout(()=>{ b.hidden = true; }, 400); }, 12000);

    // Éclairer uniquement les nœuds réellement rappelés.
    const mem = MEM();
    if(mem && mem.nodes?.length && r.ids.length){
      const wanted = new Set(r.ids);
      mem.hi = new Set();
      mem.nodes.forEach((n,i)=>{ if(wanted.has(n.id)) mem.hi.add(i); });
    }
  });

  window.addEventListener('keydown',(e)=>{
    if(e.key==='Escape' && Inspector.current) Inspector.close();
  });

  window.JarvisBrainInspector = {Inspector, Preview, runSearch};
  window.addEventListener('jarvis:page',(e)=>{
    if(['memory','brain','knowledge'].includes(e.detail?.page)){
      setTimeout(attach, 900); setTimeout(attach, 2000);
    }
  });
  setTimeout(attach, 2500);
})();
