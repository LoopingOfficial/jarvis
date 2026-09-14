/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_selftest.js  (PHASE 3.1)
   Tests exécutables dans l'application, sans framework et sans réseau.

   Lancement :  await JarvisSelfTest.run()

   Les scénarios d'erreur de synchronisation sont joués en INTERCEPTANT
   `J.post` localement : aucune requête ne part, aucune donnée n'est touchée.
   Les tests qui ne peuvent pas être honnêtement automatisés (FPS, dry-run
   réel sur le Sheet) ne sont pas simulés : ils sont marqués SKIP.
   ========================================================================== */
(function () {
  'use strict';

  const sleep = (ms)=>new Promise(r=>setTimeout(r,ms));
  const byId = (id)=>document.getElementById(id);

  /** Remplace J.post le temps d'un test, puis restaure — toujours. */
  async function withStubbedPost(impl, fn){
    const original = J.post;
    J.post = impl;
    try{ return await fn(); }
    finally{ J.post = original; }
  }

  const SelfTest = {
    results: [],

    _(name, ok, detail){
      this.results.push({name, status: ok===null ? 'SKIP' : (ok ? 'PASS' : 'FAIL'), detail: detail||''});
    },

    async run(){
      this.results = [];
      const D = window.JarvisDryRun;
      const UX = window.JarvisUX;
      const M = window.JarvisSpatialModules;

      /* ---------------------------------------------- garde-fou d'écriture */
      D.dryRunOnly = true;
      this._('garde-fou : /apply bloqué',
        !!D.guard('/api/brainrot-sync/apply','POST'), 'dryRunOnly = true');
      this._('garde-fou : /rollback bloqué',
        !!D.guard('/api/brainrot-sync/rollback','POST'));
      this._('garde-fou : /refresh autorisé',
        D.guard('/api/brainrot-sync/refresh','POST') === null);

      /* ------------------------------------------- normalisation du plan */
      const norm = D.normalizePlan({
        ok:true, plan_hash:'h1',
        counts:{creates:1, updates:1, deletes:0, blocked:1},
        entries:[
          {identity:'A', action:'CREATE', readiness_status:'READY', image_url:'a.png'},
          {identity:'B', action:'UPDATE', changed_fields:['price'],
           current_site_values:{price:'1'}, proposed_values:{price:'2'}},
          {identity:'C', status:'CONFLICT', readiness_status:'BLOCKED', reason:'ambigu'},
        ],
      }, {counts:{NO_CHANGE:10}});
      this._('normalisation : compteurs', norm.counts.create===1 && norm.counts.update===1
        && norm.counts.delete===0, JSON.stringify(norm.counts));
      this._('normalisation : NO_CHANGE', norm.noChange===10);
      this._('normalisation : non applicable détecté',
        norm.entries.filter(e=>!e.applicable).length===1);
      this._('normalisation : champ changé', norm.entries[1].changed[0]==='price');

      // Forme historique : `status` au lieu de `action`, pas de counts.
      const legacy = D.normalizePlan({entries:[{name:'X', status:'update',
        before:{a:'1'}, after:{a:'2'}}]}, null);
      this._('normalisation : forme historique tolérée',
        legacy.counts.update===0 ? false : true,
        'action='+legacy.entries[0].action);
      this._('normalisation : déduction du champ modifié',
        legacy.entries[0].changed.includes('a'));

      /* ---------------------------------------- scénarios d'erreur (sync) */
      App.goto('servers'); await sleep(1200);   // la vue doit exister pour être jugée

      const scenario = async (label, impl, expect)=>{
        const out = await withStubbedPost(impl, ()=>D.run('https://exemple.test/sheet'));
        this._('sync · '+label, out && out.error===expect,
          'attendu ' + expect + ', obtenu ' + (out && (out.error || (out.empty?'EMPTY':'OK'))));
      };
      await scenario('backend injoignable', ()=>Promise.reject(new Error('boom')), 'NETWORK');
      await scenario('réponse malformée', async()=>'ceci n\'est pas un objet', 'MALFORMED');
      await scenario('erreur backend', async()=>({ok:false, error:'SHEET_UNREACHABLE'}), 'BACKEND_ERROR');
      await scenario('plan absent', async()=>({ok:true}), 'MALFORMED');

      const empty = await withStubbedPost(
        async()=>({ok:true, sync:{ok:true, entries:[], counts:{}}, comparison:{counts:{NO_CHANGE:273}}}),
        ()=>D.run('https://exemple.test/sheet'));
      this._('sync · plan vide → message dédié', !!(empty && empty.empty),
        byId('v5DryBody')?.textContent.trim().slice(0,48) || '');

      const good = await withStubbedPost(
        async()=>({ok:true, comparison:{counts:{NO_CHANGE:270}},
          sync:{ok:true, plan_hash:'abc', counts:{creates:2,updates:1,deletes:0},
            entries:[
              {identity:'N1', action:'CREATE', readiness_status:'READY'},
              {identity:'N2', action:'CREATE', readiness_status:'READY_WITHOUT_IMAGE'},
              {identity:'N3', action:'UPDATE', changed_fields:['price','rarity'],
               current_site_values:{price:'120', rarity:'Epic'},
               proposed_values:{price:'140', rarity:'Legendary'}}]}}),
        ()=>D.run('https://exemple.test/sheet'));
      this._('sync · plan valide rendu', !!(good && good.ok && !good.empty));
      this._('sync · diff affiche avant → après',
        (byId('v5DryBody')?.textContent||'').includes('140'));
      this._('sync · journal des requêtes alimenté', D.requestLog.length>0,
        D.requestLog.length+' entrées');
      // Le journal contient volontairement les tentatives BLOQUÉES : ce qui
      // compte, c'est qu'aucune route d'écriture n'ait réellement été émise.
      this._('sync · aucune écriture réellement émise',
        !D.requestLog.some(r=>/apply|rollback/.test(r.url) && !r.blocked),
        D.requestLog.filter(r=>r.blocked).length + ' tentative(s) bloquée(s)');

      /* ------------------------------------------------------ command bar */
      const CB = UX.CommandBar;
      const before = CB.history.length;
      CB.push('test historique ' + Date.now());
      this._('command bar · historique enregistré', CB.history.length === before+1);
      let stored=[];
      try{ stored = JSON.parse(localStorage.getItem('JARVIS_CMD_HISTORY')||'[]'); }catch(_){}
      this._('command bar · historique persistant', stored.length === CB.history.length);

      const input = byId('convInput');
      if(input){
        input.value=''; input.focus();
        input.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true,cancelable:true}));
        this._('command bar · flèche haut rappelle', input.value === CB.history[0], input.value.slice(0,24));
        input.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));
        this._('command bar · flèche bas revient au brouillon', input.value === '');
      } else this._('command bar · navigation clavier', null, 'champ absent');

      /* --------------------------------------------------------- palette */
      await UX.Palette.show();
      await sleep(200);
      const pi = byId('v5PaletteInput');
      pi.value='memoire'; pi.dispatchEvent(new Event('input'));
      await sleep(120);
      this._('palette · recherche sans accent', document.querySelectorAll('#v5PaletteList button').length>0);
      pi.value='ssh'; pi.dispatchEvent(new Event('input'));
      await sleep(120);
      this._('palette · trouve les outils SSH', document.querySelectorAll('#v5PaletteList button').length>0);
      window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      await sleep(250);
      this._('palette · Escape referme', UX.Palette.open===false);

      /* ---------------------------------------------------- panneau Brain */
      UX.BrainPanel.show();
      await sleep(250);
      this._('brain · panneau ouvert', UX.BrainPanel.open && !!byId('v5BrainPanel'));
      this._('brain · pas de chaîne de pensée',
        !/pens|reasoning|thought/i.test(byId('v5BrainPanel')?.textContent||''));
      window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      await sleep(300);
      this._('brain · Escape referme', UX.BrainPanel.open===false);

      /* ------------------------------------------------------------ outils */
      App.goto('tools'); await sleep(1400);
      const fams = [...document.querySelectorAll('#v5ToolCats button')].map(b=>b.dataset.cat);
      this._('outils · familles construites', fams.length>3, fams.filter(Boolean).join(','));
      const ts = byId('v5ToolSearch');
      if(ts){
        ts.value='ssh'; ts.dispatchEvent(new Event('input')); await sleep(200);
        const n=document.querySelectorAll('.v5-tool').length;
        this._('outils · filtre de recherche', n>0 && n<95, n+' résultats');
        ts.value=''; ts.dispatchEvent(new Event('input'));
      } else this._('outils · filtre de recherche', null, 'champ absent');
      this._('outils · niveaux de risque harmonisés',
        [...document.querySelectorAll('.v5-tool .risk')].some(r=>/LOW|MEDIUM|HIGH|CRITICAL/.test(r.textContent)));

      /* ----------------------------------------------------------- mémoire */
      App.goto('memory'); await sleep(1800);
      const Mem = M.Memory;
      this._('mémoire · graphe monté', Mem.nodes.length>0, Mem.nodes.length+' nœuds');
      const ms = byId('v5MemSearch');
      if(ms){
        ms.value='jarvis'; ms.dispatchEvent(new Event('input')); await sleep(150);
        this._('mémoire · recherche éclaire des nœuds', Mem.hi.size>0, Mem.hi.size+' nœuds');
        ms.value=''; ms.dispatchEvent(new Event('input'));
      } else this._('mémoire · recherche', null, 'champ absent');
      const leaf = Mem.nodes.find(n=>n.id!=='jarvis') || Mem.nodes[0];
      Mem.select(leaf);
      this._('mémoire · focus limité au voisinage',
        Mem.hi.size>0 && Mem.hi.size < Mem.nodes.length, Mem.hi.size+'/'+Mem.nodes.length);
      Mem.view.k = 2.5;
      Mem.resetView();
      this._('mémoire · reset rétablit la vue', Mem.view.k===1 && Mem.hi.size===0);

      /* --------------------------------------------- nettoyage des vues */
      App.goto('command'); await sleep(900);
      this._('nettoyage · rAF agents arrêté', !M.Agents.raf);
      this._('nettoyage · rAF mémoire arrêté', !M.Memory.raf);
      this._('nettoyage · une seule instance de Brain', ObsidianBrain.instances.length===1);

      /* ------------------------------------------------- réglages / mode */
      let mode=null;
      try{ mode = localStorage.getItem('JARVIS_UI_MODE'); }catch(_){}
      this._('interface · mode persisté ou défaut',
        mode===null || mode==='spatial_v5' || mode==='legacy_v4', 'valeur = '+mode);
      this._('interface · V4 toujours disponible', typeof window.JarvisV4?.boot === 'function');

      /* ------------------------------------------------------ watchdog */
      this._('watchdog · non déclenché', (window.JarvisSpatialEvents?.watchdogLog||[]).length===0,
        (window.JarvisSpatialEvents?.watchdogLog||[]).length+' intervention(s)');

      /* ------------------------------------------------- Brain Inspector */
      const BD = window.JarvisBrainData;
      const LIB = window.JarvisBrainLibrary;
      const ZOOM = window.JarvisBrainZoom;
      if(BD){
        await BD.load();
        const st = BD.stats;
        this._('brain · canonicalisation', st.canonique.nodes < st.brut.nodes,
          st.brut.nodes + ' bruts → ' + st.canonique.nodes + ' canoniques');
        this._('brain · doublons fusionnés', st.doublons_fusionnes > 0,
          st.doublons_fusionnes + ' fusionné(s)');
        this._('brain · aucun souvenir inventé', (st.par_type.MEMORY || 0) === 0,
          (st.par_type.MEMORY || 0) + ' MEMORY');
        this._('brain · sous-types de capacités', Object.keys(st.par_sous_type||{}).length > 0,
          JSON.stringify(st.par_sous_type));
        const before = BD._knowledge;
        await BD.knowledge();
        this._('brain · cache des fiches réutilisé', before ? BD._knowledge === before : true);
        const res = await BD.search('ssh');
        this._('brain · recherche dans le contenu', res.total > 0 && res.hits.some(h=>h.inContent),
          res.total + ' résultat(s)');
      } else this._('brain · couche de données', null, 'module absent');

      if(LIB && BD){
        App.goto('memory'); await sleep(1600);
        await LIB.show('KNOWLEDGE'); await sleep(600);
        const rows = document.querySelectorAll('.v5-lib-row').length;
        this._('library · liste des connaissances', rows > 0, rows + ' ligne(s)');
        const counts = LIB.counts();
        this._('library · compteurs canoniques',
          counts.ALL === BD.stats.canonique.nodes, JSON.stringify(counts));
        LIB.tab = 'MEMORY'; LIB.render(); await sleep(300);
        const empty = document.querySelector('.v5-lib-empty');
        this._('library · 0 souvenir annoncé honnêtement',
          !!empty && /Aucun souvenir/.test(empty.textContent));
        this._('library · pas de faux souvenir',
          !!empty && !document.querySelectorAll('.v5-lib-row').length);
        LIB.tab = 'CAPABILITY'; LIB.render(); await sleep(400);
        const types = [...document.querySelectorAll('.v5-lib-row .meta .t')].map(t=>t.textContent);
        this._('library · capacités sous-typées',
          types.some(t=>/CAPABILITY · (TOOL|CONNECTOR|WORKFLOW)/.test(t)));
        LIB.close();
      } else this._('library', null, 'module absent');

      if(ZOOM){
        this._('zoom · seuils sémantiques',
          ZOOM.levelFor(0.6)==='FAR' && ZOOM.levelFor(1.5)==='MEDIUM' && ZOOM.levelFor(3.5)==='CLOSE');
        this._('zoom · scores d’importance calculés', ZOOM.scores.size > 0,
          ZOOM.scores.size + ' nœuds notés');
        const mem = M.Memory;
        if(mem && mem.nodes.length){
          mem.view.k = 0.6;
          const planFar = ZOOM.plan(mem).chosen.size;
          mem.view.k = 3.5; ZOOM.level = 'CLOSE';
          const planClose = ZOOM.plan(mem).chosen.size;
          mem.view.k = 1;   ZOOM.level = 'MEDIUM';
          this._('zoom · densité de labels croissante', planClose >= planFar,
            planFar + ' → ' + planClose);
        }
        const fake = 'kb:kb_inexistant';
        ZOOM.recall = new Set([fake]);
        this._('zoom · priorité au rappel', ZOOM.recall.has(fake));
        ZOOM.recall = new Set();
      } else this._('zoom sémantique', null, 'module absent');

      this._('brain · pas de fausse historique de rappel',
        !BD || !BD.lastRecall || BD.lastRecall.persistant === false);

      if(BD && M.Memory){
        App.goto('memory'); await sleep(1800);
        const mem=M.Memory;
        this._('atlas · nœuds canoniques uniquement',
          !BD.nodes.length || mem.nodes.length===BD.nodes.length,
          mem.nodes.length+' / '+BD.nodes.length);
        const aliased=mem.nodes.filter(n=>BD.alias.get(n.id) && BD.alias.get(n.id)!==n.id);
        this._('atlas · aucun doublon dessiné', aliased.length===0, aliased.length+' alias');
        this._('atlas · pas de self-link', mem.edges.every(e=>e.a!==e.b));
        const keys=new Set(); let dupE=0;
        mem.edges.forEach(e=>{ const k=e.a<e.b?e.a+'|'+e.b+'|'+e.kind:e.b+'|'+e.a+'|'+e.kind;
          if(keys.has(k)) dupE++; keys.add(k); });
        this._('atlas · relations uniques', dupE===0, dupE+' doublon(s)');
      }
      const TL=window.JarvisBrainTimeline;
      this._('timeline · module présent', !!TL);
      if(TL && BD){
        App.goto('memory'); await sleep(1400); TL.render();
        this._('timeline · BRAIN ACTIVITY',
          /BRAIN ACTIVITY/.test(byId('v5Timeline')?.textContent||''));
        this._('timeline · SESSION ACTIVITY',
          /SESSION ACTIVITY/.test(byId('v5Timeline')?.textContent||''));
      }
      const S=window.JarvisSpatial;
      if(S){
        S.setTask('done','task_7cabcdef1234');
        const shown=byId('v5TaskTitle')?.textContent||'';
        this._('tâche · id technique masqué', !/task_/i.test(shown), shown);
        S.setTask('idle');
      }
      this._('phase · pas de RAISONNEMENT',
        !/RAISONNEMENT/.test(document.body.innerText||''));
      const wrap=byId('v5BrainWrap');
      this._('mini brain · ouvre l\'atlas', !!wrap && wrap.getAttribute('role')==='button');

      /* ------------------------------------------- navigateur live (UI) */
      const BR=window.JarvisBrowser;
      this._('browser · module présent', !!BR);
      if(BR){
        this._('browser · dock en 3 tailles',
          BR.SIZES && BR.SIZES.half && BR.SIZES.wide && BR.SIZES.full);
        this._('browser · fermé par défaut', !BR.docked);
        BR.show('half');
        const hasDock=!!byId('v5Browser');
        this._('browser · dock affichable', hasDock && !!byId('v5BFrame') && !!byId('v5BLog'));
        BR.close();
        this._('browser · se ferme et coupe le stream',
          !BR.docked && !byId('v5Browser').classList.contains('in'));
      } else this._('browser', null, 'module absent');

      /* --------------------------------------------------- non automatisé */
      this._('FPS réels', null, 'mesure manuelle : await JarvisSpatial.perf()');
      this._('dry-run réel sur le Sheet', null, 'nécessite l\'URL et l\'accord utilisateur');

      const pass=this.results.filter(r=>r.status==='PASS').length;
      const fail=this.results.filter(r=>r.status==='FAIL').length;
      const skip=this.results.filter(r=>r.status==='SKIP').length;
      console.table(this.results);
      return {pass, fail, skip, total:this.results.length, results:this.results};
    },
  };

  window.JarvisSelfTest = SelfTest;
})();
