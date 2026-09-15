/* JARVIS — Self Upgrades V1.
   Page montée dynamiquement (aucune modification d'app.js / index.html requise) :
   1. ajoute la section #page-self-upgrades dans #pageWrap
   2. enregistre Pages.self_upgrades
   3. ajoute l'entrée de navigation SYSTÈME · Self Upgrades
   Contrat API : /api/self-upgrade/* + événements temps réel upgrade.* (SSE). */
(() => {
  if (window.__selfUpgradesBooted) return;
  window.__selfUpgradesBooted = true;

  const LABELS = {
    queued: 'En file', planning: 'Analyse', plan_ready: 'Plan prêt', building: 'Build',
    testing: 'Tests', candidate: 'Candidate', promoting: 'Promotion', installed: 'Installé',
    build_failed: 'Build échoué', plan_failed: 'Plan échoué', promotion_blocked: 'Promotion bloquée',
    failed: 'Échoué', cancelled: 'Annulé', rolled_back: 'Rollback effectué', cancelling: 'Annulation…',
  };
  const COLORS = {
    queued: '', planning: 'cy', plan_ready: 'cy', building: 'cy', testing: 'warn', candidate: 'cy',
    promoting: 'warn', installed: 'ok', build_failed: '', plan_failed: '', promotion_blocked: 'warn',
    failed: '', cancelled: '', rolled_back: 'warn', cancelling: 'warn',
  };

  function tag(status) {
    const t = LABELS[status] || status;
    return `<span class="tag ${COLORS[status] || ''}">${esc(t)}</span>`;
  }

  const SelfUpgrades = {
    timer: null,

    boot() {
      const wrap = document.getElementById('pageWrap');
      if (!wrap || document.getElementById('page-self-upgrades')) return;
      const sec = document.createElement('section');
      sec.id = 'page-self-upgrades';
      sec.className = 'page';
      sec.setAttribute('aria-label', 'Self Upgrades');
      wrap.appendChild(sec);
      const style = document.createElement('style');
      style.textContent = `
        #page-self-upgrades .field > span { display:block; font-size:10px; letter-spacing:1.3px;
          color: var(--text-faint); margin:0 0 6px; text-transform:uppercase; }
        #page-self-upgrades .field-row { display:flex; gap:10px; align-items:flex-end; }
        #page-self-upgrades .field-row .field { flex:1; margin:0; }
        #page-self-upgrades .row-actions { display:flex; gap:6px; align-items:center; }
        #page-self-upgrades .config-grid, #page-self-upgrades .two-col { display:grid;
          grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap:8px; }
        #page-self-upgrades .config-grid span, #page-self-upgrades .two-col span { display:block;
          font-size:9px; letter-spacing:1.2px; color:var(--text-faint); text-transform:uppercase; }
        #page-self-upgrades .config-grid b, #page-self-upgrades .two-col b { display:block;
          font-size:11px; color:var(--text); margin-top:3px; overflow-wrap:anywhere; }
        #page-self-upgrades .ellipsis { max-width:280px; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; }
        #page-self-upgrades .progress-track { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
        #page-self-upgrades .pt-step { font-size:9px; letter-spacing:.8px; padding:4px 9px;
          border-radius:999px; border:1px solid var(--line-soft); color:var(--text-faint);
          text-transform:uppercase; }
        #page-self-upgrades .pt-step.on { color:var(--cyan); border-color:rgba(34,211,238,.5);
          background:rgba(34,211,238,.08); }
        #page-self-upgrades .su-report { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
          gap:8px; margin-top:10px; }
        #page-self-upgrades .su-report span { display:block; font-size:9px; letter-spacing:1.2px;
          color:var(--text-faint); text-transform:uppercase; }
        #page-self-upgrades .su-report b { font-size:11px; margin:3px 0 0 6px; }
        #page-self-upgrades .su-pre { font-family:ui-monospace,"Cascadia Code",Consolas,monospace;
          font-size:10.5px; white-space:pre-wrap; background:rgba(6,12,24,.5);
          border:1px solid var(--line-soft); border-radius:9px; padding:10px; max-height:320px;
          overflow:auto; margin-top:6px; }
        #page-self-upgrades .su-files { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
        #page-self-upgrades textarea { min-height:64px; }`;
      document.head.appendChild(style);
      if (window.Pages) Pages.self_upgrades = (el) => this.render(el);
      if (window.App && Array.isArray(App.NAV)) {
        const sys = App.NAV.find((g) => g.label === 'SYSTÈME');
        if (sys && !sys.items.some(([id]) => id === 'self-upgrades')) {
          sys.items.push(['self-upgrades', 'Self Upgrades', 'bolt']);
        }
      }
      if (window.J) J.on('event', (e) => {
        if (J.state.page === 'self-upgrades' && /^upgrade\./.test(e.type || '')) {
          this.render(document.getElementById('page-self-upgrades'));
        }
      });
    },

    async render(el) {
      if (!el) el = document.getElementById('page-self-upgrades');
      if (!el) return;
      if (this.timer) { clearInterval(this.timer); this.timer = null; }
      if (J.state.page === 'self-upgrades') {
        this.timer = setInterval(() => {
          if (J.state.page !== 'self-upgrades') { clearInterval(this.timer); this.timer = null; return; }
          this.refreshPartial(el);
        }, 4000);
      }
      const [cfg, active, list, build] = await Promise.all([
        J.get('/api/self-upgrade/config'), J.get('/api/self-upgrade/active'),
        J.get('/api/self-upgrade/upgrades'), J.get('/api/self-upgrade/build-id'),
      ]).catch(() => [null, null, null, null]);
      this.state = { cfg: cfg?.config, active: active?.active, list: list?.upgrades || [],
                     build: build?.build_id };
      this.paint(el);
    },

    async refreshPartial(el) {
      if (J.state.page !== 'self-upgrades') return;
      const active = await J.get('/api/self-upgrade/active').catch(() => null);
      const list = await J.get('/api/self-upgrade/upgrades').catch(() => null);
      if (!active && !list) return;
      if (this.state) {
        const act = JSON.stringify(active?.active || null) === JSON.stringify(this.state.active);
        const lst = JSON.stringify(list?.upgrades || []) === JSON.stringify(this.state.list);
        if (act && lst) return;
      }
      this.render(document.getElementById('page-self-upgrades'));
    },

    paint(el) {
      const st = this.state;
      const cfg = st.cfg || {};
      const active = st.active;
      const sup = cfg.supervisor || {};
      const rows = st.list.map((u) => `
        <div class="list-row">
          <div class="meta"><b class="mono">${esc(u.id)}</b>
            <small>${esc((u.prompt || '').slice(0, 90))} · branche ${esc(u.branch || '—')}</small></div>
          <div class="row-actions">
            ${tag(u.status)}
            ${(u.status === 'build_done' || u.status === 'promotion_blocked') ? `
              <button class="btn sm cy" data-install="${esc(u.id)}">${icon('play', 12)} Installer</button>` : ''}
            ${(u.status === 'installed' || u.status === 'promoting') ? `
              <button class="btn sm" data-rollback="${esc(u.id)}">${icon('refresh', 12)} Rollback</button>` : ''}
            <button class="btn sm" data-detail="${esc(u.id)}">Det</button>
          </div>
        </div>`).join('') || '<div class="empty">Aucune amélioration pour l’instant.</div>';

      el.innerHTML = `
        <div class="page-head">
          <div><h1>Self Upgrades</h1><p>JARVIS améliore son propre code via Ollama local, valide dans un
            workspace Git, teste, vérifie une candidate, puis le Supervisor installe.</p></div>
          <div class="page-actions">
            <span class="tag cy">${esc(st.build || '')}<span>
            <span class="tag ${sup.ok ? 'ok' : ''}">${sup.ok ? 'Supervisor OK' : 'Supervisor hors ligne'}</span>
          </div>
        </div>

        <div class="grid-2">
          <div class="card"><div class="card-head"><h2>Lancer une amélioration</h2></div><div class="card-body">
            <label class="field"><span>Objectif de l’amélioration</span>
              <textarea id="suPrompt" rows="3" placeholder="Ex. : Ajoute un endpoint consigne retraçant chaque commande reçue, et installe-le si les tests passent."></textarea></label>
            <div class="field-row">
              <label class="field"><span>Mode</span>
                <select id="suMode">
                  <option value="auto">AUTO — builds + tests + candidate + installation</option>
                  <option value="build">BUILD — builds + tests, sans installation</option>
                  <option value="plan">PLAN — analyse seule</option>
                </select></label>
              <button class="btn primary" id="suRun">${icon('bolt', 13)} Lancer l’amélioration</button>
            </div>
            <div class="sep"></div>
            <div class="config-grid mono">
              <div><span>Orchestrateur</span><b>${esc(cfg.orchestrator_model || '—')}</b></div>
              <div><span>Coder</span><b>${esc(cfg.coder_model || '—')}</b></div>
              <div><span>Ollama</span><b>${esc(cfg.base_url || '—')}</b></div>
              <div><span>Candidate port</span><b>${cfg.candidate_port ?? '—'}</b></div>
              <div><span>Supervisor</span><b>${esc(cfg.supervisor_url || '—')}</b></div>
              <div><span>Python</span><b class="ellipsis" title="${esc(cfg.python || '')}">${esc(cfg.python || '—')}</b></div>
            </div>
          </div></div>

          <div class="card"><div class="card-head"><h2>En cours</h2></div><div class="card-body">
            ${active ? `
              <div class="list">
                <div class="list-row"><div class="meta"><b class="mono">${esc(active.id)}</b>
                  <small>${esc((active.prompt || '').slice(0, 90))}</small></div>
                  ${tag(active.status)}</div>
              </div>
              <div class="progress-track">
                ${['planning', 'building', 'testing', 'candidate', 'promoting', 'installed']
                  .map((s) => `<div class="pt-step ${active.status === s ? 'on' : ''}">${LABELS[s] || s}</div>`).join('')}
              </div>
              ${active.report ? this.reportBlock(active.report) : ''}` : `
              <div class="empty">Aucune amélioration en cours. Décris un objectif et lance-la.</div>`}
          </div></div>
        </div>

        <div class="card"><div class="card-head"><h2>Historique</h2></div><div class="card-body">
          <div class="list">${rows}</div>
        </div></div>

        <div id="suDetail"></div>`;

      $('#suRun', el)?.addEventListener('click', () => this.runUpgrade(el));
      el.querySelectorAll('[data-install]').forEach((b) => b.addEventListener('click', async () => {
        const r = await J.post(`/api/self-upgrade/upgrades/${b.dataset.install}/install`);
        tag(r.ok ? 'Installation acceptée — le Supervisor déploie.' : (r.error || 'Échec'),
            r.ok ? 'ok' : ''); this.render(el);
      }));
      el.querySelectorAll('[data-rollback]').forEach((b) => b.addEventListener('click', async () => {
        const r = await J.post(`/api/self-upgrade/upgrades/${b.dataset.rollback}/rollback`);
        toast(r.ok ? 'Rollback demandé au Supervisor.' : (r.error || 'Échec'), r.ok ? 'ok' : 'err');
        this.render(el);
      }));
      el.querySelectorAll('[data-detail]').forEach((b) => b.addEventListener('click', async () => {
        const r = await J.get(`/api/self-upgrade/upgrades/${b.dataset.detail}`);
        this.detail(r?.id ? r : b.dataset.detail);
      }));
    },

    reportBlock(report) {
      const p = report.planned || {};
      const ok = (v) => tag(v ? 'PASS' : 'FAIL');
      return `<div class="su-report">
        <div><span>Verification</span><b>${esc(p.verification || '—')}</b> ${ok(report.verified)}</div>
        <div><span>Tests</span><b>${report.tests_passed ?? 0}/${report.tests_total ?? 0}</b> ${ok(report.tests_ok)}</div>
        <div><span>Health</span><b>${report.health_ok ? 'OK' : 'KO'}</b> ${ok(report.health_ok)}</div>
        <div><span>Install</span><b>${esc(report.install_status || report.status || '—')}</b></div>
      </div>`;
    },

    async runUpgrade(el) {
      const prompt = $('#suPrompt', el)?.value.trim();
      if (!prompt) { toast('Décris d’abord l’objectif.', 'err'); return; }
      const mode = $('#suMode', el)?.value || 'auto';
      const btn = $('#suRun', el); btn.disabled = true;
      const r = await J.post('/api/self-upgrade/run', { prompt, mode });
      btn.disabled = false;
      if (!r.ok) { toast(r.error || 'Échec du lancement.', 'err'); return; }
      toast(`Upgrade ${r.upgrade_id} lancée (mode ${r.mode}).`, 'ok');
      this.render(el);
    },

    async detail(upgradeId) {
      if (typeof upgradeId !== 'string') return;
      const r = await J.get(`/api/self-upgrade/upgrades/${upgradeId}`);
      const host = document.getElementById('suDetail');
      if (!host) return;
      const row = r.id ? r : null;
      host.innerHTML = r.error ? `<div class="empty">${esc(r.error)}</div>` : `
        <div class="card"><div class="card-head"><h2>Upgrade ${esc(row.id)}</h2>
          <span class="tag cy">${tag(row.status)}</span></div><div class="card-body">
          <div class="mono two-col">
            <div><span>Prompt</span><b>${esc(row.prompt)}</b></div>
            <div><span>Branche</span><b>${esc(row.branch || '—')}</b></div>
            <div><span>Workspace</span><b>${esc(row.workspace_path || '—')}</b></div>
            <div><span>Avant / Après</span><b>${esc(row.version_before || '—')} → ${esc(row.version_after || '—')}</b></div>
            <div><span>Tests</span><b>${esc(JSON.stringify(row.tests_result || {}).slice(0, 240))}</b></div>
            <div><span>Health</span><b>${esc(JSON.stringify(row.health_status || {}))}</b></div>
            <div><span>Install</span><b>${esc(row.install_status || '—')}</b></div>
            <div><span>Rollback</span><b>${esc(row.rollback_status || '—')}</b></div>
          </div>
          <div class="sep"></div>
          <b>Plan</b><pre class="su-pre">${esc(JSON.stringify(row.plan || {}, null, 2))}</pre>
          <div class="sep"></div>
          <b>Fichiers modifiés</b>
          <div class="su-files">${(row.files_changed || []).map((f) =>
            `<button class="btn sm mono" data-open="${esc(f)}">${esc(f)}</button>`).join('') || '—'}</div>
        </div></div>`;
      host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    },
  };

  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', () => SelfUpgrades.boot());
  else SelfUpgrades.boot();
  window.SelfUpgrades = SelfUpgrades;
})();