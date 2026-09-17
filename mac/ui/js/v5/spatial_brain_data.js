/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_brain_data.js
   Couche d'adaptation unique du Brain : canonicalisation, typage, contenu à
   la demande, recherche derrière une abstraction.

   Pourquoi cette couche : le graphe renvoyé par /api/brain agrège sept
   sources réelles et contient des DOUBLONS — un savoir persisté (`br_…`)
   référence souvent une fiche `knowledge` déjà présente (`kb:…`). La
   déduplication est faite ici, en PRÉSENTATION uniquement : aucune donnée
   backend n'est modifiée ni supprimée.

   Le contenu intégral n'est jamais chargé par le graphe : il est récupéré à
   la demande sur /api/knowledge et mis en cache.
   ========================================================================== */
(function () {
  'use strict';

  const fold = (s)=>String(s??'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');

  /* Types d'affichage — ils disent ce qu'une chose EST, pas ce qu'on aimerait. */
  const TYPES = {
    KNOWLEDGE:  'KNOWLEDGE',    // fiche de savoir avec contenu réel
    CAPABILITY: 'CAPABILITY',   // outil, connecteur, automatisation
    MEMORY:     'MEMORY',       // entrée de la table memories
    SYSTEM:     'SYSTEM',       // nœud structurel
  };

  function classify(node){
    const id = String(node.id||'');
    if(id === 'jarvis') return TYPES.SYSTEM;
    if(id.startsWith('mem:')) return TYPES.MEMORY;
    if(id.startsWith('tool:') || id.startsWith('conn:') || id.startsWith('wf:')) return TYPES.CAPABILITY;
    if(id.startsWith('kb:') || id.startsWith('kb_')) return TYPES.KNOWLEDGE;
    // Nœud persisté : son type dépend de ce qu'il référence réellement.
    const ref = node.meta && node.meta.ref_type;
    if(ref === 'knowledge') return TYPES.KNOWLEDGE;
    if(ref === 'tool') return TYPES.CAPABILITY;
    if(['knowledge','procedure'].includes(node.kind)) return TYPES.KNOWLEDGE;
    if(node.kind === 'tool' || node.kind === 'workflow') return TYPES.CAPABILITY;
    if(node.kind === 'memory') return TYPES.MEMORY;
    return TYPES.SYSTEM;
  }

  /** Sous-type d'une capacité : outil, connecteur ou automatisation. */
  function capabilitySubtype(node){
    const id = String(node.id||'');
    if(id.startsWith('tool:')) return 'TOOL';
    if(id.startsWith('conn:')) return 'CONNECTOR';
    if(id.startsWith('wf:'))   return 'WORKFLOW';
    const m = node.meta || {};
    if(m.ref_type === 'tool' || m.tool_id) return 'TOOL';
    if(m.connector_id) return 'CONNECTOR';
    if(m.workflow_id) return 'WORKFLOW';
    if(node.kind === 'workflow') return 'WORKFLOW';
    return '';
  }

  /** Identifiant de fiche knowledge porté par un nœud, quelle que soit sa forme. */
  function knowledgeRef(node){
    const id = String(node.id||'');
    if(id.startsWith('kb:')) return id.slice(3);
    if(id.startsWith('kb_')) return id;
    const m = node.meta || {};
    if(m.ref_type === 'knowledge' && m.ref_id) return String(m.ref_id);
    if(m.id && String(m.id).startsWith('kb_')) return String(m.id);
    return '';
  }

  const BrainData = {
    raw: null,              // graphe brut tel que renvoyé par l'API
    nodes: [],              // nœuds canoniques
    edges: [],              // arêtes remappées sur les canoniques
    byId: new Map(),        // id canonique → nœud
    alias: new Map(),       // id d'origine → id canonique
    stats: null,
    _knowledge: null,       // cache des fiches complètes
    _loading: null,

    TYPES,

    /* ------------------------------------------------------------ chargement */
    async load(force){
      if(this._loading) return this._loading;
      if(this.raw && !force) return this;
      this._loading = (async ()=>{
        const data = await fetch('/api/brain').then(r=>r.json()).catch(()=>null);
        if(data && data.nodes) this.build(data);
        this._loading = null;
        return this;
      })();
      return this._loading;
    },

    /* --------------------------------------------------- canonicalisation */
    build(data){
      this.raw = data;
      const rawNodes = data.nodes || [];
      const rawEdges = data.edges || [];

      // 1. Choix du porteur canonique pour chaque fiche de connaissance.
      //    Règle : la fiche `knowledge` est la source du contenu ; si elle est
      //    présente dans le graphe, c'est elle qui porte le concept.
      const canonicalOfKb = new Map();     // ref knowledge → id canonique
      for(const n of rawNodes){
        const ref = knowledgeRef(n);
        if(!ref) continue;
        const isKb = String(n.id).startsWith('kb');
        if(isKb || !canonicalOfKb.has(ref)) canonicalOfKb.set(ref, n.id);
      }

      // 2. Table d'alias : tout nœud dupliqué pointe vers son canonique.
      this.alias = new Map();
      for(const n of rawNodes){
        const ref = knowledgeRef(n);
        const canon = ref ? (canonicalOfKb.get(ref) || n.id) : n.id;
        this.alias.set(n.id, canon);
      }

      // 3. Fusion : le canonique garde son identité, le doublon apporte ses
      //    métadonnées structurelles (jamais de contenu inventé).
      const merged = new Map();
      for(const n of rawNodes){
        const canon = this.alias.get(n.id);
        if(canon === n.id){
          merged.set(n.id, {
            id: n.id,
            label: n.label,
            family: n.family,
            kind: n.kind,
            weight: n.weight,
            type: classify(n),
            subtype: '',
            meta: {...(n.meta||{})},
            knowledgeRef: knowledgeRef(n),
            mergedFrom: [],
            degree: 0,
          });
        }
      }
      for(const n of rawNodes){
        const canon = this.alias.get(n.id);
        if(canon === n.id) continue;
        const target = merged.get(canon);
        if(!target) continue;
        target.mergedFrom.push(n.id);
        // Enrichissement structurel : on complète les trous, on n'écrase rien.
        for(const [k,v] of Object.entries(n.meta||{})){
          if(target.meta[k] === undefined || target.meta[k] === '' ) target.meta[k] = v;
        }
        if(!target.knowledgeRef) target.knowledgeRef = knowledgeRef(n);
      }

      // 4. Arêtes remappées, sans boucle ni doublon.
      const seen = new Set();
      const edges = [];
      for(const e of rawEdges){
        const a = this.alias.get(e.source) || e.source;
        const b = this.alias.get(e.target) || e.target;
        if(a === b) continue;
        if(!merged.has(a) || !merged.has(b)) continue;
        const key = a < b ? a+'|'+b+'|'+e.kind : b+'|'+a+'|'+e.kind;
        if(seen.has(key)) continue;
        seen.add(key);
        edges.push({source:a, target:b, kind:e.kind});
      }

      // 5. Degré réel — seule mesure d'importance disponible avec la confiance.
      for(const e of edges){
        merged.get(e.source).degree++;
        merged.get(e.target).degree++;
      }

      for(const n of merged.values()){
        if(n.type === TYPES.CAPABILITY) n.subtype = capabilitySubtype(n);
      }
      this.nodes = [...merged.values()];
      this.edges = edges;
      this.byId = merged;

      const byType = {}; const bySub = {};
      for(const n of this.nodes){
        byType[n.type] = (byType[n.type]||0)+1;
        if(n.subtype) bySub[n.subtype] = (bySub[n.subtype]||0)+1;
      }
      this.stats = {
        brut: {nodes: rawNodes.length, edges: rawEdges.length},
        canonique: {nodes: this.nodes.length, edges: edges.length},
        doublons_fusionnes: rawNodes.length - this.nodes.length,
        par_type: byType,
        par_sous_type: bySub,
      };
      return this;
    },

    /* ------------------------------------------- contenu réel à la demande */
    /** Charge une seule fois les 25 fiches complètes (contenu intégral). */
    async knowledge(){
      if(this._knowledge) return this._knowledge;
      const res = await J.get('/api/knowledge').catch(()=>null);
      const items = (res && res.items) || [];
      this._knowledge = new Map(items.map(k=>[k.id, k]));
      return this._knowledge;
    },

    /** Détail complet d'un nœud : le contenu n'est chargé que si nécessaire. */
    async detail(id){
      const node = this.byId.get(this.alias.get(id) || id);
      if(!node) return null;
      const out = {
        id: node.id,
        titre: node.label,
        type: node.type,
        famille: node.family,
        nature: node.kind,
        relations: this.relations(node.id),
        degre: node.degree,
        fusionne_depuis: node.mergedFrom,
        meta: node.meta,
        fiche: null,
      };
      if(node.knowledgeRef){
        const index = await this.knowledge();
        const k = index.get(node.knowledgeRef) || null;
        if(k) out.fiche = k;           // contenu intégral, tel que stocké
        else out.fiche_absente = node.knowledgeRef;
      }
      return out;
    },

    relations(id){
      const canon = this.alias.get(id) || id;
      const out = [];
      for(const e of this.edges){
        if(e.source === canon) out.push({id:e.target, kind:e.kind, sens:'sortant'});
        else if(e.target === canon) out.push({id:e.source, kind:e.kind, sens:'entrant'});
      }
      return out.map(r=>({...r, label: this.byId.get(r.id)?.label || r.id,
        type: this.byId.get(r.id)?.type || ''}));
    },

    /* ------------------------------------------------------------ recherche */
    /* Abstraction : aujourd'hui locale sur 25 fiches ; remplaçable par une
       recherche backend ou vectorielle sans toucher à l'interface. */
    provider: {
      name: 'local',
      async search(query, ctx){
        const q = fold(query).trim();
        if(!q) return [];
        const index = await ctx.knowledge();
        const words = q.split(/\s+/);
        const hits = [];
        for(const node of ctx.nodes){
          const k = node.knowledgeRef ? index.get(node.knowledgeRef) : null;
          const hay = fold([
            node.label, node.family, node.kind, node.type,
            k ? k.title : '', k ? k.content : '',
            k ? (k.tags||[]).join(' ') : (node.meta.tags||[]).join(' '),
            k ? k.source : '', k ? JSON.stringify(k.evidence||'') : '',
          ].join(' '));
          let score = 0, inContent = false;
          for(const w of words){
            if(!hay.includes(w)) { score = 0; break; }
            score += 1;
            if(k && fold(k.content).includes(w)) inContent = true;
          }
          if(score>0){
            if(fold(node.label).includes(q)) score += 2;
            hits.push({node, score, inContent});
          }
        }
        return hits.sort((a,b)=>b.score-a.score);
      },
    },

    async search(query){
      await this.load();
      const hits = await this.provider.search(query, this);
      const byType = {};
      for(const h of hits) byType[h.node.type] = (byType[h.node.type]||0)+1;
      return {query, total: hits.length, par_type: byType,
        ids: hits.map(h=>h.node.id), hits};
    },

    /* ------------------------------------------------------------- recall */
    /* Observation de session : le backend ne persiste ni date ni fréquence de
       rappel. Cette information disparaît au rechargement, et l'UI doit le dire. */
    lastRecall: null,
    sessionRecalls: [],
    noteRecall(payload){
      const ids = (payload && payload.node_ids) || [];
      this.lastRecall = {
        query: (payload && payload.query) || '',
        kind: (payload && payload.kind) || '',
        ids: ids.map(i=>this.alias.get(i) || i),
        labels: (payload && payload.labels) || [],
        at: Date.now(),
        persistant: false,
      };
      this.sessionRecalls.unshift(this.lastRecall);
      if(this.sessionRecalls.length>24) this.sessionRecalls.pop();
      window.dispatchEvent(new CustomEvent('jarvis:brain-recall', {detail:this.lastRecall}));
      return this.lastRecall;
    },
  };

  window.JarvisBrainData = BrainData;

  // Le recall réel vient de brain.search : on l'enregistre tel quel.
  const bind = ()=>{
    if(typeof J === 'undefined' || typeof J.on !== 'function') return setTimeout(bind, 600);
    J.on('brain.search', (d)=>BrainData.noteRecall(d));
    J.on('brain.path', (d)=>{
      const labels = [].concat(...((d && d.steps) || []).map(s=>s.labels||[]));
      if(labels.length) BrainData.noteRecall({query:(d&&d.query)||'', labels, node_ids:[]});
    });
  };
  bind();
})();
