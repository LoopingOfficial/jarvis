/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_brain_library.js
   BRAIN LIBRARY : parcourir les données réelles du Brain sous forme lisible.

   Elle ne remplace pas le graphe : elle l'accompagne. Les deux partagent
   exactement la même identité canonique (JarvisBrainData), le même moteur de
   recherche et le même inspecteur.

   Trois questions, trois réponses honnêtes :
     « Que sait JARVIS ? »        → KNOWLEDGE
     « Que sait-il faire ? »      → CAPABILITIES (TOOL / CONNECTOR / WORKFLOW)
     « De quoi se souvient-il ? » → MEMORIES — aujourd'hui : 0.

   Aucun champ absent n'est affiché : pas de « — — — ».
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id)=>document.getElementById(id);
  const B = ()=>window.JarvisBrainData;
  const INS = ()=>window.JarvisBrainInspector?.Inspector;
  const fold = (s)=>String(s??'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');

  const el = (tag, cls, text)=>{
    const n = document.createElement(tag);
    if(cls) n.className = cls;
    if(text !== undefined) n.textContent = String(text);
    return n;
  };
  const when = (ts)=> ts ? new Date(ts*1000).toLocaleDateString('fr-FR',
    {day:'2-digit', month:'short', year:'2-digit'}) : '';

  const TAB_LABEL = {ALL:'ALL', KNOWLEDGE:'KNOWLEDGE', CAPABILITY:'CAPABILITIES', MEMORY:'MEMORIES'};
  const SUB_LABEL = {TOOL:'TOOL', CONNECTOR:'CONNECTOR', WORKFLOW:'WORKFLOW'};

  const Library = {
    open:false,
    tab:'ALL',
    query:'',
    sort:'relations',
    filters:{tag:'', source:'', verified:false, minConfidence:0},
    _rows:[],

    /* ------------------------------------------------------------ ouverture */
    async show(tab){
      const data = B();
      if(!data) return;
      await data.load();
      await data.knowledge();          // cache partagé : un seul chargement
      if(tab) this.tab = tab;
      this.open = true;
      this.panel().hidden = false;
      requestAnimationFrame(()=>this.panel().classList.add('in'));
      this.render();
    },

    close(){
      this.open = false;
      const p = byId('v5Library');
      if(!p) return;
      p.classList.remove('in');
      setTimeout(()=>{ if(!this.open) p.hidden = true; }, 260);
    },

    toggle(){ this.open ? this.close() : this.show(); },

    panel(){
      let p = byId('v5Library');
      if(!p){
        p = document.createElement('section');
        p.id = 'v5Library';
        p.className = 'v5-library';
        p.setAttribute('role','dialog');
        p.setAttribute('aria-label','Brain Library');
        p.hidden = true;
        document.body.appendChild(p);
      }
      return p;
    },

    /* --------------------------------------------------------- composition */
    counts(){
      const data = B();
      const c = {ALL:0, KNOWLEDGE:0, CAPABILITY:0, MEMORY:0, SYSTEM:0};
      for(const n of data.nodes){ c.ALL++; c[n.type] = (c[n.type]||0)+1; }
      return c;
    },

    /** Lignes affichables : les données réelles, enrichies de leur fiche. */
    rows(){
      const data = B();
      const index = data._knowledge || new Map();
      let list = data.nodes.filter(n=>{
        if(this.tab === 'ALL') return true;
        return n.type === this.tab;
      });

      const out = list.map(n=>{
        const k = n.knowledgeRef ? index.get(n.knowledgeRef) : null;
        return {
          node: n,
          fiche: k || null,
          titre: (k && k.title) || n.label,
          resume: k ? String(k.content||'').replace(/\s+/g,' ').trim()
                    : String(n.meta.summary || n.meta.content || '').replace(/\s+/g,' ').trim(),
          tags: (k && Array.isArray(k.tags) && k.tags) || n.meta.tags || [],
          source: (k && k.source) || n.meta.source || '',
          confidence: k && typeof k.confidence_score === 'number' ? k.confidence_score : null,
          validations: k && k.validation_count ? k.validation_count : 0,
          verified: !!(k && k.verification_method),
          evidence: !!(k && k.evidence && k.evidence !== '{}' ),
          created: (k && k.created_at) || n.meta.created_at || null,
          updated: (k && k.updated_at) || n.meta.updated_at || null,
          degree: n.degree,
        };
      });

      const f = this.filters;
      let filtered = out.filter(r=>{
        if(f.tag && !r.tags.some(t=>fold(t)===fold(f.tag))) return false;
        if(f.source && fold(r.source).indexOf(fold(f.source)) < 0) return false;
        if(f.verified && !r.verified) return false;
        if(f.minConfidence && (r.confidence ?? 0) < f.minConfidence) return false;
        return true;
      });

      if(this.query.trim()){
        const ids = new Set(this._searchIds || []);
        filtered = filtered.filter(r=>ids.has(r.node.id));
      }

      const by = {
        title: (a,b)=>a.titre.localeCompare(b.titre,'fr'),
        created: (a,b)=>(b.created||0)-(a.created||0),
        updated: (a,b)=>(b.updated||0)-(a.updated||0),
        confidence: (a,b)=>(b.confidence??-1)-(a.confidence??-1),
        validations: (a,b)=>b.validations-a.validations,
        relations: (a,b)=>b.degree-a.degree,
      }[this.sort] || ((a,b)=>b.degree-a.degree);
      filtered.sort(by);
      return filtered;
    },

    /* ------------------------------------------------------------- rendu */
    render(){
      const p = this.panel();
      const data = B();
      const c = this.counts();
      p.replaceChildren();

      /* en-tête */
      const head = el('header');
      const title = el('div','v5-lib-title');
      title.appendChild(el('span','v5-kick','BRAIN LIBRARY'));
      title.appendChild(el('h3', null, data.stats.canonique.nodes + ' concepts canoniques'));
      head.appendChild(title);
      const close = el('button','x','✕');
      close.setAttribute('aria-label','Fermer la Library');
      close.onclick = ()=>this.close();
      head.appendChild(close);
      p.appendChild(head);

      /* onglets avec les compteurs canoniques réels */
      const tabs = el('div','v5-lib-tabs');
      tabs.setAttribute('role','tablist');
      for(const t of ['ALL','KNOWLEDGE','CAPABILITY','MEMORY']){
        const b = el('button', this.tab===t ? 'on' : null);
        b.setAttribute('role','tab');
        b.setAttribute('aria-selected', this.tab===t ? 'true' : 'false');
        b.appendChild(el('span', null, TAB_LABEL[t]));
        b.appendChild(el('s', null, c[t] ?? 0));
        b.onclick = ()=>{ this.tab = t; this.render(); };
        tabs.appendChild(b);
      }
      p.appendChild(tabs);

      /* barre : recherche unifiée + tri */
      const bar = el('div','v5-lib-bar');
      const search = document.createElement('input');
      search.className = 'v5-search';
      search.id = 'v5LibSearch';
      search.placeholder = 'Rechercher dans le contenu…';
      search.value = this.query;
      search.setAttribute('aria-label','Rechercher dans la Library');
      let timer=null;
      search.addEventListener('input', ()=>{
        clearTimeout(timer);
        timer = setTimeout(()=>this.search(search.value), 180);
      });
      bar.appendChild(search);

      const sort = document.createElement('select');
      sort.className = 'v5-lib-sort';
      sort.setAttribute('aria-label','Trier');
      [['relations','Relations'],['updated','Modifié'],['created','Créé'],
       ['confidence','Confiance'],['validations','Validations'],['title','Titre']]
        .forEach(([v,l])=>{
          const o=document.createElement('option'); o.value=v; o.textContent=l;
          if(this.sort===v) o.selected=true; sort.appendChild(o);
        });
      sort.onchange = ()=>{ this.sort = sort.value; this.renderList(); };
      bar.appendChild(sort);
      p.appendChild(bar);

      /* filtres — uniquement ceux que les données supportent réellement */
      p.appendChild(this.filterBar());

      /* corps */
      const body = el('div','v5-lib-body');
      body.id = 'v5LibBody';
      p.appendChild(body);
      this.renderList();
    },

    filterBar(){
      const data = B();
      const index = data._knowledge || new Map();
      const wrap = el('div','v5-lib-filters');

      // Tags réellement présents
      const tagCount = new Map();
      for(const n of data.nodes){
        const k = n.knowledgeRef ? index.get(n.knowledgeRef) : null;
        const tags = (k && k.tags) || n.meta.tags || [];
        for(const t of tags) tagCount.set(t, (tagCount.get(t)||0)+1);
      }
      const topTags = [...tagCount.entries()].sort((a,b)=>b[1]-a[1]).slice(0,8);
      if(topTags.length){
        const grp = el('div','grp');
        grp.appendChild(el('span','lbl','TAGS'));
        topTags.forEach(([t,n])=>{
          const b = el('button', this.filters.tag===t ? 'on' : null, t);
          b.appendChild(el('s', null, n));
          b.onclick = ()=>{ this.filters.tag = this.filters.tag===t ? '' : t; this.render(); };
          grp.appendChild(b);
        });
        wrap.appendChild(grp);
      }

      // Qualité : n'apparaît que si au moins une fiche la porte
      const anyVerified = [...index.values()].some(k=>k.verification_method);
      const anyConfidence = [...index.values()].some(k=>typeof k.confidence_score==='number');
      if(anyVerified || anyConfidence){
        const grp = el('div','grp');
        grp.appendChild(el('span','lbl','QUALITÉ'));
        if(anyVerified){
          const b = el('button', this.filters.verified ? 'on' : null, 'Vérifié');
          b.onclick = ()=>{ this.filters.verified = !this.filters.verified; this.render(); };
          grp.appendChild(b);
        }
        if(anyConfidence){
          const b = el('button', this.filters.minConfidence ? 'on' : null, 'Confiance ≥ 60 %');
          b.onclick = ()=>{ this.filters.minConfidence = this.filters.minConfidence ? 0 : 0.6; this.render(); };
          grp.appendChild(b);
        }
        wrap.appendChild(grp);
      }

      // Sous-types de capacités, s'ils existent
      if(this.tab === 'CAPABILITY' && data.stats.par_sous_type){
        const subs = Object.entries(data.stats.par_sous_type);
        if(subs.length){
          const grp = el('div','grp');
          grp.appendChild(el('span','lbl','NATURE'));
          subs.forEach(([s,n])=>{
            const b = el('button', this.filters.source===('sub:'+s) ? 'on' : null, SUB_LABEL[s]||s);
            b.appendChild(el('s', null, n));
            b.onclick = ()=>{
              this._subFilter = this._subFilter===s ? '' : s;
              this.renderList();
              b.classList.toggle('on', this._subFilter===s);
            };
            grp.appendChild(b);
          });
          wrap.appendChild(grp);
        }
      }

      const reset = el('button','v5-lib-reset','Réinitialiser');
      reset.onclick = ()=>{
        this.filters = {tag:'', source:'', verified:false, minConfidence:0};
        this._subFilter=''; this.query=''; this._searchIds=null; this.render();
      };
      wrap.appendChild(reset);
      return wrap;
    },

    renderList(){
      const body = byId('v5LibBody');
      if(!body) return;
      body.replaceChildren();

      /* MEMORIES : la réponse honnête, pas un repli sur les connaissances. */
      if(this.tab === 'MEMORY'){
        const c = this.counts();
        if(!c.MEMORY){
          const empty = el('div','v5-lib-empty');
          empty.appendChild(el('b', null, 'Aucun souvenir persistant enregistré.'));
          empty.appendChild(el('p', null,
            'La table des souvenirs est vide. Les connaissances de JARVIS sont stockées '
            + 'séparément et restent consultables dans l\'onglet KNOWLEDGE.'));
          const go = el('button','v5-btn','Voir les connaissances');
          go.onclick = ()=>{ this.tab='KNOWLEDGE'; this.render(); };
          empty.appendChild(go);
          body.appendChild(empty);
          return;
        }
      }

      let rows = this.rows();
      if(this.tab==='CAPABILITY' && this._subFilter){
        rows = rows.filter(r=>r.node.subtype===this._subFilter);
      }
      this._rows = rows;

      if(!rows.length){
        const empty = el('div','v5-lib-empty');
        empty.appendChild(el('b', null, this.query ? 'Aucun résultat.' : 'Rien à afficher ici.'));
        if(this.query) empty.appendChild(el('p', null, 'Aucun concept ne correspond à « '+this.query+' ».'));
        body.appendChild(empty);
        return;
      }

      const count = el('div','v5-lib-count',
        rows.length + ' élément' + (rows.length>1?'s':'')
        + (this.query ? ' pour « '+this.query+' »' : ''));
      body.appendChild(count);

      for(const r of rows){
        const row = el('button','v5-lib-row');
        row.setAttribute('aria-label', r.titre);

        const main = el('div','v5-lib-main');
        main.appendChild(el('b', null, r.titre));
        if(r.resume) main.appendChild(el('p', null, r.resume.slice(0,150)));
        const meta = el('div','meta');

        // Type et, pour une capacité, sa nature réelle.
        const type = el('span','t',
          r.node.type === 'CAPABILITY' && r.node.subtype
            ? 'CAPABILITY · ' + (SUB_LABEL[r.node.subtype]||r.node.subtype)
            : (r.node.type === 'KNOWLEDGE' ? 'KNOWLEDGE' : r.node.type));
        meta.appendChild(type);

        if(r.degree) meta.appendChild(el('span','d', r.degree + ' rel.'));
        if(r.confidence !== null) meta.appendChild(el('span','c', Math.round(r.confidence*100)+' %'));
        if(r.validations) meta.appendChild(el('span','v', r.validations + '×'));
        if(r.verified) meta.appendChild(el('span','ok','vérifié'));
        if(r.source) meta.appendChild(el('span','s', r.source.slice(0,28)));
        if(r.updated) meta.appendChild(el('span','date', when(r.updated)));
        main.appendChild(meta);

        if(r.tags.length){
          const tags = el('div','tags');
          r.tags.slice(0,5).forEach(t=>tags.appendChild(el('span', null, t)));
          main.appendChild(tags);
        }
        row.appendChild(main);
        row.onclick = ()=>this.openNode(r.node.id);
        body.appendChild(row);
      }
    },

    /* -------------------------------------------- recherche unifiée */
    /* Réutilise strictement le provider de JarvisBrainData : un seul moteur. */
    async search(q){
      this.query = String(q||'');
      if(!this.query.trim()){ this._searchIds = null; this.renderList(); return; }
      const res = await B().search(this.query);
      this._searchIds = res.ids;
      this.renderList();
    },

    /* ------------------------------------------- Library → Atlas → inspecteur */
    openNode(id){
      const ins = INS();
      // Le graphe doit être à l'écran pour être centré.
      if(window.App && (window.J?.state?.page !== 'memory')){
        window.App.goto('memory');
        setTimeout(()=>{ ins?.focus(id); }, 1200);
      } else {
        ins?.focus(id);
      }
      this.close();
    },
  };

  /* ------------------------------------------- points d'entrée dans l'UI */
  function mountButton(){
    const bar = document.querySelector('#page-memory .v5-graph-bar');
    if(!bar || bar.querySelector('#v5LibOpen')) return;
    const b = el('button','v5-btn','Brain Library');
    b.id = 'v5LibOpen';
    b.setAttribute('aria-label','Ouvrir la Brain Library');
    b.onclick = ()=>Library.toggle();
    bar.insertBefore(b, bar.querySelector('.v5-graph-hint') || null);
  }

  window.addEventListener('jarvis:page',(e)=>{
    if(['memory','brain','knowledge'].includes(e.detail?.page)){
      setTimeout(mountButton, 1000);
      setTimeout(mountButton, 2200);
    }
  });
  window.addEventListener('keydown',(e)=>{
    if(e.key==='Escape' && Library.open) Library.close();
  });

  window.JarvisBrainLibrary = Library;
})();
