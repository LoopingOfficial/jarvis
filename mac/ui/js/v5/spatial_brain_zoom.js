/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_brain_zoom.js
   ZOOM SÉMANTIQUE du graphe : le niveau de zoom ne change pas seulement
   l'échelle, il change la QUANTITÉ D'INFORMATION affichée.

   FAR     structure : clusters et concepts fortement connectés.
   MEDIUM  exploration : concepts canoniques, titres, relations importantes.
   CLOSE   lecture : résumé court réel, type, source, nombre de relations.

   Trois règles tenues ici :
     1. le contenu intégral ne s'affiche JAMAIS sur le canvas — il reste dans
        l'inspecteur ;
     2. les labels sont priorisés et se raréfient quand la densité monte, pour
        éviter l'empilement illisible ;
     3. un rappel réel (brain.search) est PRIORITAIRE sur le zoom : les nœuds
        rappelés restent visibles même si le niveau les aurait masqués.

   Importance d'un nœud — formule documentée, sans métrique inventée :
       score = degré_normalisé × 0.7
             + confiance × 0.2            (si la fiche en porte une)
             + validations_normalisées × 0.1
   La topologie reste dominante : la confiance ne peut pas l'écraser.
   ========================================================================== */
(function () {
  'use strict';

  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const B = ()=>window.JarvisBrainData;
  const MEM = ()=>window.JarvisSpatialModules?.Memory;

  const LEVELS = {
    FAR:    {max:0.85, labels:8,  edges:'important', detail:'none'},
    MEDIUM: {max:2.2,  labels:22, edges:'normal',    detail:'title'},
    CLOSE:  {max:99,   labels:60, edges:'focus',     detail:'preview'},
  };

  const Zoom = {
    level:'MEDIUM',
    scores:new Map(),      // id canonique → importance 0..1
    summaries:new Map(),   // id canonique → résumé court réel
    recall:new Set(),      // nœuds réellement rappelés (priorité absolue)
    _built:false,

    levelFor(k){
      if(k <= LEVELS.FAR.max) return 'FAR';
      if(k <= LEVELS.MEDIUM.max) return 'MEDIUM';
      return 'CLOSE';
    },

    /** Prépare scores et résumés à partir des données réelles déjà chargées. */
    async build(){
      const data = B();
      if(!data) return;
      await data.load();
      const maxDeg = Math.max(1, ...data.nodes.map(n=>n.degree));
      const index = data._knowledge || null;   // pas de chargement forcé ici
      let maxVal = 1;
      if(index) for(const k of index.values()) maxVal = Math.max(maxVal, k.validation_count||0);

      this.scores = new Map();
      this.summaries = new Map();
      for(const n of data.nodes){
        const k = (index && n.knowledgeRef) ? index.get(n.knowledgeRef) : null;
        const degree = n.degree / maxDeg;
        const conf = k && typeof k.confidence_score === 'number' ? k.confidence_score : 0;
        const val = k && k.validation_count ? k.validation_count / maxVal : 0;
        this.scores.set(n.id, clamp(degree*0.7 + conf*0.2 + val*0.1, 0, 1));
        // Résumé court : jamais le contenu intégral, jamais inventé.
        const raw = k ? k.content : (n.meta.summary || n.meta.content || '');
        if(raw) this.summaries.set(n.id, String(raw).replace(/\s+/g,' ').trim().slice(0, 64));
      }
      this._built = true;
    },

    /** Correspondance entre un nœud du rendu et son concept canonique. */
    canonical(node){
      const data = B();
      if(!data) return node.id;
      if(data.byId.has(node.id)) return node.id;
      if(data.alias.has(node.id)) return data.alias.get(node.id);
      if(data.byId.has('kb:'+node.id)) return 'kb:'+node.id;
      return node.id;
    },

    /* --------------------------------------------------- décision d'affichage */
    /** Quels nœuds ont droit à un label, par ordre de priorité. */
    plan(mem){
      const spec = LEVELS[this.level];
      const scored = mem.nodes.map((n,i)=>{
        const cid = this.canonical(n);
        return {i, n, cid, score:this.scores.get(cid) ?? 0};
      });

      const chosen = new Set();
      const push = (list, limit)=>{
        for(const item of list){
          if(chosen.size >= limit) break;
          chosen.add(item.i);
        }
      };

      // 1. sélection  2. rappel réel  3. résultats de recherche
      //    4. nœuds fortement connectés  5. le reste
      const selIndex = mem.sel ? mem.nodes.indexOf(mem.sel) : -1;
      if(selIndex >= 0) chosen.add(selIndex);

      const recalled = scored.filter(s=>this.recall.has(s.cid));
      push(recalled, spec.labels + recalled.length);      // jamais sacrifiés

      if(mem.hi && mem.hi.size){
        push(scored.filter(s=>mem.hi.has(s.i)).sort((a,b)=>b.score-a.score), spec.labels + recalled.length);
      }
      push([...scored].sort((a,b)=>b.score-a.score), spec.labels + recalled.length);
      return {chosen, spec};
    },

    /* ------------------------------------------------------------- rendu */
    /** Dessine l'information textuelle par-dessus le canvas du graphe. */
    draw(mem, ctx, cx, cy, S){
      if(!this._built) return;
      this.level = this.levelFor(mem.view.k);
      const spec = LEVELS[this.level];
      // Boîtes déjà occupées : un label qui chevauche n'est pas dessiné.
      const boxes = [];
      const fits = (x,y,w,h)=>{
        for(const b of boxes){
          if(x < b.x+b.w && b.x < x+w && y < b.y+b.h && b.y < y+h) return false;
        }
        boxes.push({x,y,w,h});
        return true;
      };
      let drawn = 0;
      const {chosen} = this.plan(mem);
      const dpr = 1;

      ctx.save();
      ctx.textBaseline = 'middle';
      for(const i of chosen){
        const n = mem.nodes[i];
        if(!n) continue;
        const cid = this.canonical(n);
        const node = B().byId.get(cid);
        const x = cx + n.x*S, y = cy + n.y*S;
        if(x < -80 || x > innerWidth+80 || y < -40 || y > innerHeight+40) continue;

        const isRecall = this.recall.has(cid);
        const isSel = mem.sel === n;
        const score = this.scores.get(cid) ?? 0;
        const r = 3 + Math.min(5, (node?.degree||0)*0.35);

        // FAR : seulement les concepts structurants, en capitales discrètes.
        const label = String(node?.label || n.label || '');
        if(!label) continue;

        const alpha = isSel ? 1 : isRecall ? 0.95 : clamp(0.45 + score*0.5, 0, 0.9);
        ctx.font = (isSel||isRecall ? '600 ' : '') +
          (this.level==='FAR' ? '10px' : '10.5px') + ' ui-monospace, monospace';
        ctx.fillStyle = isRecall ? 'rgba(157,244,255,'+alpha+')'
          : isSel ? 'rgba(238,250,255,'+alpha+')'
          : 'rgba(198,222,236,'+alpha+')';
        const text = this.level==='FAR' ? label.toUpperCase().slice(0,22) : label.slice(0,34);
        const w = ctx.measureText(text).width;
        const priority = isSel || isRecall;
        const lines = (spec.detail === 'preview' && (priority || score > 0.25)) ? 3 : 1;
        if(!priority){
          if(drawn >= spec.labels) continue;
          if(!fits(x + r + 4, y - 8, w + 10, 12 * lines + 4)) continue;
        } else {
          fits(x + r + 4, y - 8, w + 10, 12 * lines + 4);
        }
        drawn++;
        ctx.fillText(text, x + r + 6, y);

        // CLOSE : une ligne de contexte réelle, jamais le contenu entier.
        if(spec.detail === 'preview' && (isSel || isRecall || score > 0.25)){
          const sum = this.summaries.get(cid);
          const type = node?.type === 'CAPABILITY' && node.subtype
            ? node.subtype : (node?.type || '');
          ctx.font = '9px ui-monospace, monospace';
          ctx.fillStyle = 'rgba(140,180,205,.55)';
          const meta = [type, (node?.degree||0)+' rel.'].filter(Boolean).join(' · ');
          ctx.fillText(meta, x + r + 6, y + 12);
          if(sum){
            ctx.fillStyle = 'rgba(140,180,205,.42)';
            ctx.fillText(sum.slice(0,44), x + r + 6, y + 23);
          }
        }
      }
      ctx.restore();
    },

    /** Intensité d'une relation selon le niveau — on ne trace pas tout pareil. */
    edgeAlpha(mem, e, lit, hiOn){
      const spec = LEVELS[this.level];
      if(lit) return 0.55;
      const a = this.scores.get(this.canonical(mem.nodes[e.a])) ?? 0;
      const b = this.scores.get(this.canonical(mem.nodes[e.b])) ?? 0;
      const strength = Math.max(a,b);
      if(spec.edges === 'important') return strength > 0.35 ? 0.16 : 0.03;
      if(spec.edges === 'focus'){
        if(!mem.sel) return hiOn ? 0.05 : 0.11;
        const si = mem.nodes.indexOf(mem.sel);
        return (e.a===si || e.b===si) ? 0.42 : 0.05;
      }
      return hiOn ? 0.05 : (strength > 0.2 ? 0.15 : 0.07);
    },
  };

  /* ----------------------------------------------------- greffe sur le graphe */
  function attach(){
    const mem = MEM();
    if(!mem || mem.__zoom) return setTimeout(attach, 900);
    mem.__zoom = true;
    Zoom.build();

    const legacyFrame = mem.frame.bind(mem);
    mem.frame = function(cv, dt){
      // Niveau courant : dérivé du zoom réel de la vue.
      Zoom.level = Zoom.levelFor(this.view.k);
      legacyFrame(cv, dt);

      // Couche d'information par-dessus le graphe déjà dessiné.
      const box = cv.parentElement.getBoundingClientRect();
      const ctx = cv.getContext('2d');
      const K = this.view.k, OX = this.view.ox, OY = this.view.oy;
      const cx = box.width/2 + OX, cy = box.height/2 + OY;
      const S = Math.min(box.width, box.height) * 0.46 * K;
      Zoom.draw(this, ctx, cx, cy, S);

      // Indicateur de niveau, discret.
      ctx.save();
      ctx.font = '8.5px ui-monospace, monospace';
      ctx.fillStyle = 'rgba(140,180,205,.45)';
      ctx.fillText('ZOOM · ' + Zoom.level, 10, box.height - 12);
      ctx.restore();
    };

  }

  /* Le rappel réel prend la priorité, puis s'estompe. */
  window.addEventListener('jarvis:brain-recall',(e)=>{
    Zoom.recall = new Set(e.detail.ids || []);
    clearTimeout(Zoom._t);
    Zoom._t = setTimeout(()=>{ Zoom.recall = new Set(); }, 20000);
  });

  window.addEventListener('jarvis:page',(e)=>{
    if(['memory','brain','knowledge'].includes(e.detail?.page)){
      setTimeout(attach, 1000);
      setTimeout(()=>Zoom.build(), 1800);
    }
  });

  window.JarvisBrainZoom = Zoom;
})();
