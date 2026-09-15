/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_dryrun.js  (PHASE 3.1)
   DRY RUN de synchronisation, garde-fou d'écriture, normalisation du plan,
   diff lisible et gestion explicite des erreurs.

   Le dry-run s'appuie sur une route backend DÉJÀ EN LECTURE SEULE :
     POST /api/brainrot-sync/refresh  « Relit Sheet + site et recalcule le
                                        plan. Lecture seule. »

   Deux barrières :
     1. backend — /refresh n'écrit rien (docstring + `write_performed:false`) ;
     2. frontend — `dryRunOnly` bloque tout appel vers une route d'écriture
        (/apply, /rollback, /confirm-scope) tant qu'il est actif.

   Le garde-fou frontend ne remplace pas les protections backend : il double.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id)=>document.getElementById(id);
  const esc = (s)=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const URL_KEY = 'JARVIS_SYNC_SHEET_URL';
  const WRITE_ROUTES = /\/api\/brainrot-sync\/(apply|rollback|confirm-scope)/i;
  const TIMEOUT_MS = 180000;   // une relecture Sheet + site peut être longue

  /* ------------------------------------------------------------- adapter */
  /* Le backend peut évoluer : on normalise explicitement au lieu de supposer.
     Vocabulaire de référence (jarvis/brainrot_compare.py, brainrot_sync.py) :
       action          CREATE | UPDATE | CONFLICT | INVALID | SERVER_ONLY
       readiness       READY | READY_WITHOUT_IMAGE | NEEDS_REVIEW | BLOCKED
       plan.counts     creates, updates, deletes, blocked, needs_review… */
  const APPLICABLE = ['CREATE','UPDATE','DELETE'];

  function normalizePlan(sync, comparison){
    const raw = sync || {};
    const rawEntries = raw.entries || raw.items || [];
    const entries = rawEntries.map((e)=>{
      const action = String(e.action ?? e.status ?? e.op ?? '').toUpperCase();
      const before = e.current_site_values ?? e.site_values ?? e.before ?? {};
      const after  = e.proposed_values ?? e.new_values ?? e.after ?? {};
      let changed = e.changed_fields ?? e.changed ?? [];
      if(!Array.isArray(changed)) changed = [];
      // Si le serveur ne liste pas les champs modifiés, on les déduit — mais
      // seulement par comparaison stricte des valeurs réellement fournies.
      if(!changed.length && action==='UPDATE'){
        changed = Object.keys(after).filter(k=>String(after[k]??'')!==String(before[k]??''));
      }
      return {
        id: String(e.identity_key ?? e.id ?? e.slug ?? e.identity ?? ''),
        name: String(e.identity ?? e.name ?? e.slug ?? '(sans nom)'),
        slug: String(e.slug ?? ''),
        action,
        applicable: APPLICABLE.includes(action),
        readiness: String(e.readiness_status ?? e.readiness ?? '').toUpperCase(),
        reason: String(e.readiness_reason ?? e.reason ?? ''),
        before, after, changed,
        image: String(e.image_url ?? '') ? 'OFFICIAL' : (e.image_status ?? 'NONE'),
      };
    });

    const c = raw.counts || {};
    const num = (...keys)=>{
      for(const k of keys){ if(typeof c[k]==='number') return c[k]; }
      return null;
    };
    const byAction = (a)=>entries.filter(e=>e.action===a).length;
    const counts = {
      create: num('creates','CREATE','create') ?? byAction('CREATE'),
      update: num('updates','UPDATE','update') ?? byAction('UPDATE'),
      // Le moteur actuel ne produit aucune suppression : 0 explicite, pas « — ».
      delete: num('deletes','DELETE','delete') ?? byAction('DELETE'),
      blocked: num('blocked') ?? entries.filter(e=>e.readiness==='BLOCKED').length,
      needsReview: num('needs_review') ?? entries.filter(e=>e.readiness==='NEEDS_REVIEW').length,
      applicable: num('applicable') ?? entries.filter(e=>e.applicable).length,
    };
    const cc = (comparison && comparison.counts) || {};
    const noChange = (typeof cc.NO_CHANGE==='number') ? cc.NO_CHANGE
      : (typeof cc.no_change==='number' ? cc.no_change : null);

    return {
      ok: raw.ok !== false,
      planHash: String(raw.plan_hash ?? raw.planHash ?? ''),
      syncId: String(raw.sync_id ?? ''),
      entries, counts, noChange,
      total: entries.length,
      source: {
        sheetTotal: comparison?.sheet?.total ?? null,
        siteTotal: comparison?.site?.total ?? null,
        tab: comparison?.sheet?.tab ?? '',
      },
      // Trace : le backend affirme-t-il explicitement n'avoir rien écrit ?
      writePerformed: comparison?.write_performed === true,
    };
  }

  /* ------------------------------------------------------------ module */
  const DryRun = {
    dryRunOnly: true,          // garde-fou frontend : seconde barrière
    plan: null,
    comparison: null,
    requestLog: [],            // url, méthode, durée, statut — pour audit
    busy: false,
    lastAt: 0,
    lastError: null,

    normalizePlan,             // exposé pour les tests

    /** Bloque toute route d'écriture tant que le mode dry-run est actif. */
    guard(url, method){
      if(this.dryRunOnly && WRITE_ROUTES.test(String(url))){
        const err = new Error('DRY_RUN_ONLY: écriture bloquée côté interface — ' + url);
        console.warn('[V5 dry-run]', err.message);
        this.requestLog.push({at:new Date().toISOString(), url, method, blocked:true});
        return err;
      }
      return null;
    },

    /* ---------------------------------------------------------- montage */
    render(){
      const page = byId('page-servers');
      if(!page) return;
      let host = page.querySelector(':scope > .v5-view-host');
      if(!host){
        host = document.createElement('div');
        host.className = 'v5-view-host v5-sync';
        page.prepend(host);
        page.classList.add('v5-migrated');
      }
      let url='';
      try{ url = localStorage.getItem(URL_KEY) || ''; }catch(_){ /* stockage indisponible */ }

      host.innerHTML = `
        <div class="v5-sec-head">
          <span class="v5-kick">SYNCHRONISATION</span>
          <h2>Comparaison Sheet ↔ Site</h2>
          <span class="v5-dry-badge" id="v5DryBadge">DRY RUN · lecture seule</span>
        </div>
        <div class="v5-dry-bar">
          <label class="v5-sr" for="v5DryUrl">URL du Google Sheet à comparer</label>
          <input class="v5-search" id="v5DryUrl" placeholder="URL du Google Sheet à comparer"
                 value="${esc(url)}" spellcheck="false" autocomplete="off" />
          <button class="v5-btn primary" id="v5DryRun">Lancer le DRY RUN</button>
          <button class="v5-btn" id="v5DryOpen" disabled
                  aria-label="Ouvrir le Workspace pour appliquer">Appliquer dans le Workspace…</button>
        </div>
        <p class="v5-dry-note">
          Le DRY RUN relit la feuille et le site, recalcule le plan et affiche le diff.
          Il n'écrit jamais : l'application passe par le Workspace, avec sélection,
          confirmation, idempotence et sauvegarde.
        </p>
        <div class="v5-dry-body" id="v5DryBody">
          <p class="v5-empty">Aucun plan calculé pour l'instant.</p>
        </div>`;

      byId('v5DryRun').addEventListener('click', ()=>this.run());
      byId('v5DryOpen').addEventListener('click', ()=>this.openWorkspace());
      byId('v5DryUrl').addEventListener('keydown',(e)=>{
        if(e.key==='Enter'){ e.preventDefault(); this.run(); }
      });
    },

    /* -------------------------------------------------------- exécution */
    async run(urlOverride){
      if(this.busy) return {ok:false, error:'BUSY'};
      const input = byId('v5DryUrl');
      const url = String(urlOverride ?? input?.value ?? '').trim();
      if(!url){
        input?.focus();
        this.say('Indiquez l\'URL du Google Sheet à comparer.', 'warn');
        return {ok:false, error:'NO_URL'};
      }
      try{ localStorage.setItem(URL_KEY, url); }catch(_){ /* stockage indisponible */ }

      const route = '/api/brainrot-sync/refresh';
      const blocked = this.guard(route, 'POST');
      if(blocked) return {ok:false, error:'DRY_RUN_ONLY'};

      this.busy = true; this.lastError = null;
      const btn = byId('v5DryRun');
      if(btn){ btn.disabled = true; btn.textContent = 'Analyse en cours…'; }
      this.say('Relecture de la feuille et du site…');
      window.ObsidianBrain?.setState('READING', {reason:'dry run'});
      window.ObsidianBrain?.activateZone('KNOWLEDGE', TIMEOUT_MS);

      const started = performance.now();
      const entry = {at:new Date().toISOString(), url:route, method:'POST', blocked:false};
      let res = null, failure = null;
      try{
        res = await Promise.race([
          J.post(route, {url, request_id:'dryrun_'+Date.now()}),
          new Promise((_,rej)=>setTimeout(()=>rej(new Error('TIMEOUT')), TIMEOUT_MS)),
        ]);
      }catch(err){
        failure = (String(err && err.message) === 'TIMEOUT') ? 'TIMEOUT' : 'NETWORK';
      }
      entry.ms = Math.round(performance.now()-started);
      entry.status = failure || (res && res.ok === false ? 'BACKEND_ERROR' : 'OK');
      this.requestLog.push(entry);
      if(this.requestLog.length > 50) this.requestLog.shift();

      this.busy = false;
      if(btn){ btn.disabled = false; btn.textContent = 'Lancer le DRY RUN'; }
      window.ObsidianBrain?.deactivateZone('KNOWLEDGE');

      return this.handle(res, failure);
    },

    /** Un seul endroit décide de ce qui est montré : erreurs incluses. */
    handle(res, failure){
      const fail = (code, msg)=>{
        this.lastError = code;
        this.plan = null;
        byId('v5DryOpen') && (byId('v5DryOpen').disabled = true);
        this.say(msg, 'err');
        window.ObsidianBrain?.setState('ERROR', {reason:'dry run'});
        setTimeout(()=>{ if(window.ObsidianBrain?.state==='ERROR') window.ObsidianBrain.setState('IDLE'); }, 4000);
        return {ok:false, error:code};
      };

      if(failure === 'TIMEOUT') return fail('TIMEOUT', 'Request timed out — la relecture n\'a pas répondu à temps. Aucun changement n\'a été écrit.');
      if(failure === 'NETWORK') return fail('NETWORK', 'Backend injoignable — la relecture n\'a pas pu être lancée. Aucun changement n\'a été écrit.');
      if(!res || typeof res !== 'object') return fail('MALFORMED', 'Réponse inexploitable du serveur (format inattendu).');
      if(res.ok === false){
        const raw = String(res.error || res.response || '').trim();
        const human = /sheet|google|spreadsheet/i.test(raw)
          ? 'Feuille Google inaccessible — la source n\'a pas pu être lue : ' + raw
          : (raw || 'La relecture a échoué côté serveur.');
        return fail('BACKEND_ERROR', human);
      }
      if(!res.sync && !res.comparison) return fail('MALFORMED', 'Le serveur n\'a pas renvoyé de plan exploitable.');

      this.comparison = res.comparison || null;
      this.plan = normalizePlan(res.sync, this.comparison);
      this.lastAt = Date.now();

      if(!this.plan.total){
        byId('v5DryOpen') && (byId('v5DryOpen').disabled = true);
        this.say('Aucun changement détecté — la feuille et le site sont alignés.', 'ok');
        window.ObsidianBrain?.setState('SUCCESS');
        setTimeout(()=>window.ObsidianBrain?.setState('IDLE'), 1600);
        return {ok:true, empty:true, plan:this.plan};
      }

      byId('v5DryOpen') && (byId('v5DryOpen').disabled = !this.plan.counts.applicable);
      this.renderDiff();
      window.ObsidianBrain?.setState('IDLE');
      return {ok:true, plan:this.plan};
    },

    say(text, tone){
      const body = byId('v5DryBody');
      if(body) body.innerHTML = `<p class="v5-empty ${tone||''}" role="status">${esc(text)}</p>`;
    },

    /* ------------------------------------------------------------ diff */
    renderDiff(){
      const body = byId('v5DryBody');
      const plan = this.plan;
      if(!body || !plan) return;

      const creates = plan.entries.filter(e=>e.action==='CREATE');
      const updates = plan.entries.filter(e=>e.action==='UPDATE');
      const deletes = plan.entries.filter(e=>e.action==='DELETE');
      const others  = plan.entries.filter(e=>!e.applicable);

      const field = (e,f)=>{
        const b=e.before[f], a=e.after[f];
        return `<div class="v5-diff-field">
            <code>${esc(f)}</code>
            <span class="was">${esc(b===undefined||b===''?'—':b)}</span>
            <i aria-hidden="true">→</i>
            <span class="now">${esc(a===undefined||a===''?'—':a)}</span>
          </div>`;
      };

      body.innerHTML = `
        <div class="v5-dry-counts">
          <span><b>${plan.counts.create}</b>CREATE</span>
          <span><b>${plan.counts.update}</b>UPDATE</span>
          <span><b>${plan.counts.delete}</b>DELETE</span>
          ${plan.noChange!=null?`<span><b>${plan.noChange}</b>NO_CHANGE</span>`:''}
          ${plan.counts.blocked?`<span class="warn"><b>${plan.counts.blocked}</b>BLOQUÉS</span>`:''}
          <span class="meta">plan ${esc(plan.planHash.slice(0,10)||'—')} · ${new Date(this.lastAt).toLocaleTimeString('fr-FR')}</span>
        </div>

        <section class="v5-diff-block">
          <h3>CREATE <em>${creates.length}</em></h3>
          ${creates.length ? creates.slice(0,40).map(e=>`
            <div class="v5-diff-row create">
              <b>${esc(e.name)}</b>
              <span class="ready ${esc(e.readiness.toLowerCase())}">${esc(e.readiness)}</span>
              ${e.image==='OFFICIAL'?'<span class="img">image</span>':''}
            </div>`).join('') : '<p class="v5-empty">Aucun</p>'}
          ${creates.length>40?`<p class="v5-empty">… et ${creates.length-40} autres</p>`:''}
        </section>

        <section class="v5-diff-block">
          <h3>UPDATE <em>${updates.length}</em></h3>
          ${updates.length ? updates.slice(0,40).map(e=>`
            <div class="v5-diff-row update">
              <b>${esc(e.name)}</b>
              <div class="v5-diff-fields">
                ${e.changed.map(f=>field(e,f)).join('')
                  || '<span class="v5-empty">champs non détaillés par le serveur</span>'}
              </div>
            </div>`).join('') : '<p class="v5-empty">Aucun</p>'}
        </section>

        <section class="v5-diff-block">
          <h3>DELETE <em>${plan.counts.delete}</em></h3>
          ${deletes.length ? deletes.map(e=>`<div class="v5-diff-row"><b>${esc(e.name)}</b></div>`).join('')
            : '<p class="v5-empty">Aucun — la synchronisation ne supprime rien.</p>'}
        </section>

        ${others.length?`
        <section class="v5-diff-block">
          <h3>NON APPLICABLES <em>${others.length}</em></h3>
          ${others.slice(0,20).map(e=>`
            <div class="v5-diff-row blocked">
              <b>${esc(e.name)}</b>
              <span class="ready">${esc(e.action)}</span>
              <span class="why">${esc(e.reason)}</span>
            </div>`).join('')}
        </section>`:''}`;
    },

    /** L'application reste sur le chemin existant, avec ses garde-fous. */
    openWorkspace(){
      const AW = window.AnalysisWorkspace;
      if(AW && AW.payload){ AW.open(AW.payload); return; }
      window.toast?.('Demandez la comparaison à JARVIS pour ouvrir le Workspace d\'application.');
    },
  };

  window.JarvisDryRun = DryRun;

  window.addEventListener('jarvis:page', (e)=>{
    if(e.detail?.page === 'servers') setTimeout(()=>DryRun.render(), 160);
  });
})();
