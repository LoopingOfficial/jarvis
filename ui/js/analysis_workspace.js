/* ==========================================================================
   AnalysisWorkspace — JARVIS_ANALYSIS_WORKSPACE_UI_V2_1

   Dashboard de contrôle pour l'analyse de sources et la comparaison Sheet↔site.

   Deux règles tenues par ce fichier :

   1. Tout est rendu depuis le payload structuré du backend
      (AnalysisWorkspacePayload + comparison). Le texte du LLM n'est affiché
      que dans la zone « Synthèse », et aucune valeur ne s'en déduit.

   2. Aucun rendu massif. La table pagine, et un tri / filtre / changement de
      page ne reconstruit QUE son <tbody> : 280+ entrées ne produisent jamais
      280 blocs dans le DOM.
   ========================================================================== */
const AW_ICONS = {
  sheet: '<path d="M4 2h9l5 5v13a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M13 2v5h5"/>',
  db: '<ellipse cx="11" cy="5" rx="8" ry="3"/><path d="M3 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M3 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  check: '<circle cx="11" cy="11" r="8"/><path d="M7.5 11.2l2.4 2.4 4.6-4.8"/>',
  refresh: '<path d="M19 5v5h-5"/><path d="M3 17v-5h5"/><path d="M18.4 10a7.5 7.5 0 0 0-13-3.2L3 10"/><path d="M3.6 12a7.5 7.5 0 0 0 13 3.2L19 12"/>',
  plus: '<circle cx="11" cy="11" r="8"/><path d="M11 7.5v7M7.5 11h7"/>',
  alert: '<path d="M11 3.5 2.8 18h16.4L11 3.5z"/><path d="M11 9v4M11 15.6v.1"/>',
  ban: '<circle cx="11" cy="11" r="8"/><path d="M5.4 5.4l11.2 11.2"/>',
  server: '<rect x="3" y="4" width="16" height="6" rx="1.5"/><rect x="3" y="12" width="16" height="6" rx="1.5"/><path d="M6.5 7h.1M6.5 15h.1"/>',
  search: '<circle cx="10" cy="10" r="6.5"/><path d="M15 15l4 4"/>',
  clock: '<circle cx="11" cy="11" r="8"/><path d="M11 6.5V11l3 1.8"/>',
  link: '<path d="M9.5 12.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5l-1.2 1.2"/><path d="M12.5 9.5a3.5 3.5 0 0 0-5 0L5 12a3.5 3.5 0 0 0 5 5l1.2-1.2"/>',
  eye: '<path d="M1.8 11S5 5 11 5s9.2 6 9.2 6-3.2 6-9.2 6-9.2-6-9.2-6z"/><circle cx="11" cy="11" r="2.6"/>',
  dots: '<circle cx="11" cy="5" r="1.4"/><circle cx="11" cy="11" r="1.4"/><circle cx="11" cy="17" r="1.4"/>',
  x: '<path d="M5 5l12 12M17 5L5 17"/>',
  left: '<path d="M13.5 5L7 11l6.5 6"/>',
  right: '<path d="M8.5 5L15 11l-6.5 6"/>',
  first: '<path d="M14 5l-6 6 6 6"/><path d="M6.5 5v12"/>',
  last: '<path d="M8 5l6 6-6 6"/><path d="M15.5 5v12"/>',
  list: '<path d="M7 6h12M7 11h12M7 16h12"/><path d="M3.5 6h.1M3.5 11h.1M3.5 16h.1"/>',
  grid: '<rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.4"/><rect x="12" y="3.5" width="6.5" height="6.5" rx="1.4"/><rect x="3.5" y="12" width="6.5" height="6.5" rx="1.4"/><rect x="12" y="12" width="6.5" height="6.5" rx="1.4"/>',
  columns: '<rect x="3" y="4" width="16" height="14" rx="1.6"/><path d="M8.5 4v14M14 4v14"/>',
  export: '<path d="M11 3v10"/><path d="M7 9.5l4 4 4-4"/><path d="M3.5 15v3h15v-3"/>',
  back: '<circle cx="11" cy="11" r="8"/><path d="M13 7.5L9 11l4 3.5"/>',
  up: '<path d="M11 17V6"/><path d="M6.5 10L11 5.5 15.5 10"/>',
  doc: '<path d="M4 2h9l5 5v13a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/>',
  bulb: '<path d="M8 16h6"/><path d="M9 19h4"/><path d="M11 3a5.5 5.5 0 0 1 3.3 9.9c-.5.4-.8 1-.8 1.6H8.5c0-.6-.3-1.2-.8-1.6A5.5 5.5 0 0 1 11 3z"/>',
  table: '<rect x="3" y="4" width="16" height="14" rx="1.6"/><path d="M3 9h16M9 9v9"/>',
  chart: '<path d="M3.5 18V9M9 18V4.5M14.5 18v-6M20 18V7"/>',
  shield: '<path d="M11 3l7 3v5c0 4.2-2.9 7.8-7 9-4.1-1.2-7-4.8-7-9V6l7-3z"/>',
  info: '<circle cx="11" cy="11" r="8"/><path d="M11 10v5M11 7.4v.1"/>',
  user: '<circle cx="11" cy="7.5" r="3.5"/><path d="M4.5 18.5a6.5 6.5 0 0 1 13 0"/>',
  sort: '<path d="M7 8l3-3 3 3"/><path d="M7 14l3 3 3-3"/>',
  swap: '<path d="M4 8h11l-3-3"/><path d="M18 14H7l3 3"/>',
  tag: '<path d="M3.5 11.5V4.5a1 1 0 0 1 1-1h7l7.5 7.5-8 8-7.5-7.5z"/><path d="M7.5 7.5h.1"/>',
};

const AW_STATUS = {
  NO_CHANGE: { label: 'Aucun changement', icon: 'check' },
  UPDATE: { label: 'À mettre à jour', icon: 'refresh' },
  CREATE: { label: 'À créer', icon: 'plus' },
  CONFLICT: { label: 'Conflit', icon: 'alert' },
  INVALID: { label: 'Invalide', icon: 'ban' },
  SERVER_ONLY: { label: 'Serveur uniquement', icon: 'server' },
};

const AW_SECTION_ICONS = {
  request: 'user', site_comparison: 'swap', site_sync: 'refresh', summary: 'doc', metrics: 'chart',
  findings: 'bulb', warnings: 'alert', recommendations: 'search',
  evidence: 'shield', tables: 'table', statistics: 'chart',
};

/** Colonnes de la table : id, libellé, clé de tri, activable par l'utilisateur. */
const AW_COLUMNS = [
  { id: 'index', label: '#', sort: 'index', fixed: true },
  { id: 'name', label: 'Nom', sort: 'name', fixed: true },
  { id: 'status', label: 'Statut', sort: 'status' },
  { id: 'rarity', label: 'Rareté', sort: 'rarity' },
  { id: 'sheet', label: 'Valeur (Sheet)', sort: 'sheet', num: true },
  { id: 'site', label: 'Valeur (Site)', sort: 'site', num: true },
  { id: 'mods', label: 'Modifications' },
  { id: 'actions', label: 'Actions', fixed: true, num: true },
];

function awIcon(name, size = 15) {
  const path = AW_ICONS[name] || AW_ICONS.info;
  return `<svg width="${size}" height="${size}" viewBox="0 0 22 22" fill="none" stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function awNum(value) {
  if (value === null || value === undefined || value === '') return '';
  const n = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
  if (!Number.isFinite(n)) return String(value);
  if (Math.abs(n) >= 1e12) return `${trimZero(n / 1e12)}T`;
  if (Math.abs(n) >= 1e9) return `${trimZero(n / 1e9)}B`;
  if (Math.abs(n) >= 1e6) return `${trimZero(n / 1e6)}M`;
  if (Math.abs(n) >= 1e4) return `${trimZero(n / 1e3)}K`;
  return trimZero(n);
}

function trimZero(n) {
  return String(Math.round(n * 100) / 100);
}

/** Emplacement vignette. Aucune URL n'est inventee : si le backend en publie
 *  une un jour (cle `image`, `thumbnail` ou `icon` de l'entree ou des valeurs
 *  Sheet), elle s'affiche automatiquement ; sinon, monogramme discret. */
function awThumbUrl(entry) {
  const pools = [entry, entry.sheet_values || {}, entry.site_values || {}];
  for (const pool of pools) {
    for (const key of ['image', 'image_url', 'thumbnail', 'icon', 'avatar']) {
      const v = pool[key];
      if (typeof v === 'string' && /^(https?:|data:image\/)/i.test(v.trim())) return v.trim();
    }
  }
  return '';
}

function awThumb(name, entry, cls = '') {
  const url = entry ? awThumbUrl(entry) : '';
  const initial = esc((String(name || '?').trim().charAt(0) || '?'));
  if (url) {
    return `<span class="aw-avatar ${cls} has-img"><img src="${esc(url)}" alt="" loading="lazy"
      onerror="this.parentNode.classList.remove('has-img');this.remove()" /><i>${initial}</i></span>`;
  }
  return `<span class="aw-avatar ${cls}">${initial}</span>`;
}

function awVal(value) {
  if (value === null || value === undefined || value === '') return '—';
  return typeof value === 'number' ? awNum(value) : String(value);
}

const AnalysisWorkspace = {
  payload: null,
  section: 'summary',
  query: '',
  openProof: null,
  sheetFilter: null,
  /* état de la table de comparaison */
  t: { page: 1, size: 25, sortKey: 'index', sortDir: 'asc', filter: 'ALL', q: '',
       view: 'list', selected: new Set(), hidden: new Set() },
  collapsed: false,
  detail: { key: null, tab: 'comparison' },
  /* Mode SYNCHRONISATION : selection et filtres propres au plan. */
  sy: { selected: new Set(), only: 'ALL', hideReady: false, hideReview: false, result: null,
        refreshing: false, refreshState: 'IDLE', refreshError: '', lastRefresh: 0,
        staleSelection: [], history: [], applying: false },

  /* ------------------------------------------------------------- ouverture */
  open(payload) {
    if (!payload || !payload.sections) return;
    this.payload = payload;
    this.section = payload.focus || 'summary';
    this.query = '';
    this.openProof = null;
    this.sheetFilter = null;
    this.t = { ...this.t, page: 1, filter: 'ALL', q: '', selected: new Set(),
               sortKey: 'index', sortDir: 'asc' };
    // Première ligne sélectionnée avant le rendu : le panneau détail est
    // visible d'emblée, et l'ouverture ne coûte qu'un seul rendu.
    const first = this.section === 'site_comparison' ? this.rows()[0] : null;
    this.detail = { key: first ? first.key : null, tab: 'comparison' };
    this.mount();
    this.el.classList.remove('hidden');
    this.render();
  },

  close() { this.el?.classList.add('hidden'); },
  isOpen() { return !!this.el && !this.el.classList.contains('hidden'); },

  mount() {
    if (this.el) return;
    const el = document.createElement('div');
    el.className = 'aw-overlay hidden';
    el.id = 'analysisWorkspace';
    el.innerHTML = `
      <aside class="aw-side">
        <div class="aw-brand">
          <div class="aw-brand-mark">
            <svg viewBox="0 0 32 32" fill="none">
              <circle cx="16" cy="16" r="13" stroke="rgba(34,211,238,.45)" stroke-width="1.4"/>
              <circle cx="16" cy="16" r="8.5" stroke="rgba(34,211,238,.7)" stroke-width="1.2"
                      stroke-dasharray="3 5"/>
              <circle cx="16" cy="16" r="3.2" fill="#22d3ee"/>
            </svg>
          </div>
          <div class="aw-brand-txt"><b>JARVIS</b><small>Command Center</small></div>
          <button class="aw-collapse" data-aw-collapse title="Réduire le panneau">${awIcon('left', 13)}</button>
        </div>
        <div class="aw-search">
          <div class="aw-search-box">
            ${awIcon('search', 14)}
            <input type="search" placeholder="Rechercher dans l'analyse…" data-aw-search />
            <span class="aw-kbd">Ctrl K</span>
          </div>
        </div>
        <nav class="aw-nav" data-aw-nav></nav>
        <div class="aw-side-foot">
          <div class="aw-foot-txt"><b>Espace de travail</b><small data-aw-footstate>Prêt pour l'analyse</small></div>
          <span class="aw-live" title="Connecté">Connecté</span>
        </div>
      </aside>
      <div class="aw-content">
        <header class="aw-head">
          <button class="aw-btn icon aw-side-toggle" data-aw-sidetoggle title="Afficher les sections">${awIcon('list', 15)}</button>
          <div class="aw-head-icon">${awIcon('swap', 17)}</div>
          <div class="aw-head-title">
            <b>Analysis Workspace</b>
            <small data-aw-source></small>
          </div>
          <div class="aw-head-actions">
            <span data-aw-grounding></span>
            <span class="aw-pill ghost" data-aw-duration></span>
            <button class="aw-btn" data-aw-refresh>${awIcon('refresh', 14)}<span>Actualiser</span></button>
            <button class="aw-btn" data-aw-export>${awIcon('export', 14)}<span>Exporter</span></button>
            <button class="aw-btn primary" data-aw-back>${awIcon('back', 14)}<span>Retour au résumé</span></button>
            <button class="aw-btn icon" data-aw-close title="Fermer">${awIcon('x', 15)}</button>
          </div>
        </header>
        <main class="aw-main" data-aw-main></main>
      </div>`;
    document.body.appendChild(el);
    this.el = el;

    el.querySelector('[data-aw-close]').onclick = () => this.close();
    el.querySelector('[data-aw-back]').onclick = () => this.show('summary');
    el.querySelector('[data-aw-refresh]').onclick = () => this.refresh();
    el.querySelector('[data-aw-export]').onclick = () => this.exportCsv();
    el.querySelector('[data-aw-sidetoggle]').onclick = () => el.classList.toggle('side-open');
    el.querySelector('[data-aw-collapse]').onclick = () => {
      this.collapsed = !this.collapsed;
      el.classList.toggle('collapsed', this.collapsed);
      try { localStorage.setItem('aw.collapsed', this.collapsed ? '1' : '0'); } catch (_) {}
    };
    try { this.collapsed = localStorage.getItem('aw.collapsed') === '1'; } catch (_) {}
    el.classList.toggle('collapsed', this.collapsed);
    el.querySelector('[data-aw-search]').oninput = (e) => {
      this.query = e.target.value.trim().toLowerCase();
      this.renderNav();
      this.renderMain();
    };
    document.addEventListener('keydown', (e) => {
      if (!this.isOpen()) return;
      if (e.key === 'Escape') {
        if (this.detail.key) { this.detail.key = null; this.renderMain(); }
        else this.close();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        this.el.querySelector('[data-aw-search]')?.focus();
      }
    });
  },

  show(section) {
    this.section = section;
    this.openProof = null;
    this.el.classList.remove('side-open');
    this.renderNav();
    this.renderMain();
    this.el.querySelector('[data-aw-main]').scrollTop = 0;
  },

  /* ---------------------------------------------------------------- header */
  render() {
    const p = this.payload;
    const q = (s) => this.el.querySelector(s);
    const cmp = p.comparison;
    q('[data-aw-source]').textContent = cmp?.ok
      ? `Google Sheet • ${p.source.label || ''} — ${cmp.totals.sheet} lignes Sheet / ${cmp.totals.site} entrées site`
      : `${this.sourceKindLabel(p.source.kind)} • ${p.source.label || ''} • ${p.source.sheet_count} onglet(s)`;
    q('[data-aw-grounding]').innerHTML = p.grounding.ok
      ? '<span class="aw-pill ok">GROUNDED</span>'
      : `<span class="aw-pill warn">NON VÉRIFIÉ${p.grounding.code ? ' · ' + esc(p.grounding.code) : ''}</span>`;
    q('[data-aw-duration]').textContent = p.duration_ms ? `${(p.duration_ms / 1000).toFixed(1)}s` : '—';
    q('[data-aw-footstate]').textContent = cmp?.ok
      ? `${cmp.entries.length} éléments comparés` : 'Prêt pour l\'analyse';
    this.renderNav();
    this.renderMain();
  },

  sourceKindLabel(kind) {
    return { google_sheet: 'Google Sheet', csv: 'CSV', pdf: 'PDF' }[kind] || String(kind || 'Source');
  },

  counts() {
    const p = this.payload;
    return {
      site_comparison: p.comparison?.entries?.length || 0,
      site_sync: p.sync?.entries?.length || 0,
      request: 0, summary: 0, metrics: p.metrics.length, findings: p.findings.length,
      warnings: p.warnings.length, recommendations: p.recommendations.length,
      evidence: p.evidence.length, tables: p.tables.length,
      statistics: p.statistics.length + (p.distributions || []).length,
    };
  },

  renderNav() {
    const p = this.payload;
    const counts = this.counts();
    const nav = this.el.querySelector('[data-aw-nav]');
    const sections = p.sections.map((s) => `
      <button data-section="${esc(s.id)}" class="${s.id === this.section ? 'active' : ''}"
              title="${esc(s.label)}${counts[s.id] ? ` (${counts[s.id]})` : ''}">
        ${awIcon(AW_SECTION_ICONS[s.id] || 'info', 15)}
        <span class="aw-nav-label">${esc(s.label)}</span>
        ${counts[s.id] ? `<span class="aw-count">${counts[s.id]}</span>` : ''}
      </button>`).join('');
    const sheets = (p.source.sheets || []).map((name) => `
      <button data-sheet="${esc(name)}" class="${this.sheetFilter === name ? 'active' : ''}"
              title="${esc(name)}">
        ${awIcon('sheet', 14)}<span class="aw-nav-label">${esc(name)}</span></button>`).join('');
    nav.innerHTML = `<div class="aw-nav-group">Analyse</div>${sections}
      <div class="aw-nav-group">Onglets détectés</div>${sheets || '<div class="aw-empty">Aucun</div>'}`;
    nav.querySelectorAll('[data-section]').forEach((b) => {
      b.onclick = () => { this.sheetFilter = null; this.show(b.dataset.section); };
    });
    nav.querySelectorAll('[data-sheet]').forEach((b) => {
      b.onclick = () => {
        this.sheetFilter = this.sheetFilter === b.dataset.sheet ? null : b.dataset.sheet;
        this.show('tables');
      };
    });
  },

  matches(...parts) {
    if (!this.query) return true;
    return parts.filter(Boolean).join(' ').toLowerCase().includes(this.query);
  },

  renderMain() {
    const main = this.el.querySelector('[data-aw-main]');
    const fn = this[`section_${this.section}`];
    main.innerHTML = `<div class="aw-section">${fn ? fn.call(this) : ''}</div>`;
    this.bind(main);
  },

  /* ============================================ COMPARAISON : la table ==== */
  entries() { return this.payload.comparison?.entries || []; },

  /** Ligne normalisée pour l'affichage — dérivée du payload, jamais recalculée. */
  rows() {
    const map = this.payload.comparison?.mapping || {};
    const keyOf = (label) => map[label];
    return this.entries().map((e, i) => {
      const sv = e.sheet_values || {};
      const tv = e.site_values || {};
      const incomeLabel = Object.keys(map).find((k) => map[k] === 'income');
      const costLabel = Object.keys(map).find((k) => map[k] === 'cost');
      const rarityLabel = Object.keys(map).find((k) => map[k] === 'rarity');
      return {
        key: `${e.status}:${e.identity}:${e.sheet_row ?? 'x'}:${e.site_index ?? 'x'}`,
        index: i + 1, entry: e, status: e.status, name: String(e.identity || ''),
        rarity: sv[rarityLabel] ?? tv.rarity ?? '',
        sheet: incomeLabel ? sv[incomeLabel] : undefined,
        site: keyOf ? tv.income : undefined,
        sheetCost: costLabel ? sv[costLabel] : undefined,
        siteCost: tv.cost,
        mods: (e.changed_fields || []).length,
      };
    });
  },

  filtered() {
    const f = this.t.filter;
    const q = this.t.q;
    let rows = this.rows();
    if (f === 'CHANGED') rows = rows.filter((r) => r.status === 'UPDATE' || r.status === 'CREATE');
    else if (f !== 'ALL') rows = rows.filter((r) => r.status === f);
    if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q));
    const dir = this.t.sortDir === 'asc' ? 1 : -1;
    const key = this.t.sortKey;
    rows.sort((a, b) => {
      let x = a[key], y = b[key];
      if (key === 'name' || key === 'status' || key === 'rarity') {
        x = String(x || '').toLowerCase(); y = String(y || '').toLowerCase();
        return x < y ? -dir : x > y ? dir : 0;
      }
      x = Number(x) || 0; y = Number(y) || 0;
      return (x - y) * dir;
    });
    return rows;
  },

  pageRows(rows) {
    const size = this.t.size;
    const pages = Math.max(1, Math.ceil(rows.length / size));
    if (this.t.page > pages) this.t.page = pages;
    const start = (this.t.page - 1) * size;
    return { slice: rows.slice(start, start + size), pages };
  },


  /* ============================================ MODE SYNCHRONISATION ====== */
  syncEntries() {
    const plan = this.payload.sync;
    if (!plan?.ok) return [];
    let rows = plan.entries;
    if (this.sy.only === 'CREATE') rows = rows.filter((e) => e.action === 'CREATE');
    if (this.sy.only === 'UPDATE') rows = rows.filter((e) => e.action === 'UPDATE');
    if (this.sy.only === 'READY') rows = rows.filter((e) => e.readiness_status.startsWith('READY'));
    if (this.sy.only === 'NEEDS_REVIEW') {
      rows = rows.filter((e) => e.readiness_status === 'NEEDS_REVIEW');
    }
    if (this.sy.hideReady) rows = rows.filter((e) => !e.readiness_status.startsWith('READY'));
    if (this.sy.hideReview) rows = rows.filter((e) => e.readiness_status !== 'NEEDS_REVIEW');
    if (this.t.q) rows = rows.filter((e) => e.identity.toLowerCase().includes(this.t.q));
    return rows;
  },

  /** Seules ces entrées peuvent être cochées : le reste n'est jamais applicable. */
  applicableKeys() {
    const plan = this.payload.sync;
    return new Set((plan?.entries || [])
      .filter((e) => e.readiness_status === 'READY' || e.readiness_status === 'READY_WITHOUT_IMAGE')
      .map((e) => e.identity));
  },


  /* ===================================== REFRESH / ÉTAT PERSISTANT ======== */
  /* L'état de travail (URL, filtres, recherche, page, tri, vue, colonnes,
     scroll, historique) survit à chaque action : le Workspace ne se ferme
     pas, ne renvoie pas au chat et ne redemande jamais l'URL. */
  syncSnapshot() {
    const main = this.el?.querySelector('[data-aw-main]');
    return {
      section: this.section, query: this.query, sheetFilter: this.sheetFilter,
      t: { ...this.t, selected: new Set(this.t.selected), hidden: new Set(this.t.hidden) },
      // Seuls les reglages de travail sont captures : l'etat du refresh,
      // l'historique et le dernier resultat ne doivent pas revenir en arriere.
      sy: { only: this.sy.only, hideReady: this.sy.hideReady,
            hideReview: this.sy.hideReview, selected: new Set(this.sy.selected) },
      detailKey: this.detail.key, detailTab: this.detail.tab,
      scroll: main ? main.scrollTop : 0,
    };
  },

  syncRestore(snap) {
    if (!snap) return;
    this.section = snap.section;
    this.query = snap.query;
    this.sheetFilter = snap.sheetFilter;
    this.t = { ...snap.t, selected: new Set(snap.t.selected), hidden: new Set(snap.t.hidden) };
    this.sy = { ...this.sy, ...snap.sy, selected: new Set(snap.sy.selected) };
    this.detail = { key: snap.detailKey, tab: snap.detailTab };
    this.renderNav();
    this.renderMain();
    const main = this.el?.querySelector('[data-aw-main]');
    if (main) main.scrollTop = snap.scroll;
  },

  /** Entrées encore applicables après un refresh (les autres sont périmées). */
  pruneSelection() {
    const alive = new Set((this.payload.sync?.entries || []).map((e) => e.identity));
    const stale = [...this.sy.selected].filter((k) => !alive.has(k));
    stale.forEach((k) => this.sy.selected.delete(k));
    return stale;
  },

  async refreshSync({ auto = false } = {}) {
    if (this.sy.refreshing) return false;      // pas de double refresh
    this.sy.refreshing = true;
    this.sy.refreshState = 'REFRESHING';
    const snap = this.syncSnapshot();
    this.renderMain();
    try {
      const res = await J.post('/api/brainrot-sync/refresh', {
        url: this.payload.source?.url || '', request_id: this.payload.sync?.sync_id || '',
      });
      if (!res?.ok) throw new Error(res?.error || 'REFRESH_FAILED');
      // On ne remplace la liste qu'une fois le nouveau résultat en main :
      // en cas d'échec, l'ancienne reste affichée.
      this.payload.comparison = res.comparison;
      this.payload.sync = res.sync;
      if (res.evidence) this.payload.evidence = res.evidence;
      if (res.metrics) this.payload.metrics = res.metrics;
      if (res.warnings) this.payload.warnings = res.warnings;
      if (res.recommendations) this.payload.recommendations = res.recommendations;
      this.sy.refreshState = 'UPDATED';
      this.sy.lastRefresh = Date.now();
      const stale = this.pruneSelection();
      this.syncRestore(snap);
      if (stale.length) {
        this.sy.staleSelection = stale;
        SyncFeedback.toast(
          `${stale.length} entrée(s) sélectionnée(s) ne sont plus applicables — `
          + 'sélection à refaire.', 'warning');
      }
      if (!auto) SyncFeedback.toast('Liste actualisée.', 'success');
      return true;
    } catch (err) {
      this.sy.refreshState = 'ERROR';
      this.sy.refreshError = err.message;
      this.syncRestore(snap);                   // l'état de travail est préservé
      SyncFeedback.toast(
        auto ? 'Synchronisation réussie, mais actualisation impossible.'
             : 'Actualisation impossible : ' + err.message, auto ? 'warning' : 'error');
      return false;
    } finally {
      this.sy.refreshing = false;
      this.renderMain();
    }
  },

  /* ------------------------------------------------- historique de session */
  pushHistory(kind, text) {
    (this.sy.history ||= []).unshift({ kind, text, at: Date.now() });
    this.sy.history = this.sy.history.slice(0, 40);
  },

  historyPanel() {
    const items = this.sy.history || [];
    const remaining = (this.payload.sync?.counts?.applicable) || 0;
    const glyph = { ok: '✓', err: '✕', info: '○' };
    return `<div class="aw-panel sy-history">
      <div class="aw-panel-head"><span class="aw-ph-ic">${awIcon('clock', 15)}</span>
        <div><b>Historique de session</b><small>Trace d'interface — l'audit serveur fait foi</small></div>
      </div>
      <ul class="sy-hist-list">
        ${items.map((h) => `<li class="${esc(h.kind)}">${glyph[h.kind] || '○'} ${esc(h.text)}</li>`).join('')
          || '<li class="info">○ Aucune action dans cette session.</li>'}
        <li class="info">○ ${remaining} changement(s) restant(s)</li>
      </ul></div>`;
  },

  section_site_sync() {
    const plan = this.payload.sync;
    if (!plan) return '<h2>Synchronisation</h2><div class="aw-empty">Aucun plan préparé.</div>';
    if (!plan.ok) {
      return `<h2>Synchronisation</h2><div class="aw-note danger">${awIcon('alert', 17)}
        <div><b>${esc(plan.error || 'Plan indisponible')}</b></div></div>`;
    }
    const c = plan.counts;
    const rows = this.syncEntries();
    const applicable = this.applicableKeys();
    const chosen = [...this.sy.selected].filter((k) => applicable.has(k));
    const tiles = [
      ['Créations', c.creates, '#22d3ee'],
      ['Mises à jour', c.updates, '#fb923c'],
      ['Sans image', c.without_image, '#a5b4fc'],
      ['À revoir', c.needs_review, '#fbbf24'],
      ['Bloquées', c.blocked, '#fb7185'],
      ['Sélectionnées', chosen.length, '#34d399'],
    ];
    return `<h2>Synchronisation</h2>
      <div class="aw-sub">Plan ${esc(plan.sync_id)} · magasin ${esc(plan.store_path)} ·
        0 suppression · rien n'est écrit sans confirmation explicite.</div>
      <div class="aw-cmp-readonly" style="border-color:rgba(251,146,60,.4);
           background:rgba(251,146,60,.09);color:var(--warn)">
        MODE SYNCHRONISATION · ÉCRITURE APRÈS CONFIRMATION${plan.batch_enabled ? ''
          : ' · LOT DÉSACTIVÉ (une entrée à la fois)'}</div>
      <div class="aw-cmp-totals">${tiles.map(([label, n, col]) => `
        <div class="aw-cmp-tile"><b style="color:${col}">${n}</b>
          <small>${esc(label)}</small></div>`).join('')}</div>
      <div class="aw-toolbar">
        <div class="aw-filters">
          ${[['ALL', 'Tout'], ['CREATE', 'CREATE'], ['UPDATE', 'UPDATE'],
             ['READY', 'READY'], ['NEEDS_REVIEW', 'NEEDS_REVIEW']].map(([k, l]) =>
            `<button class="aw-filter ${this.sy.only === k ? 'active' : ''}"
               data-sy-only="${k}">${l}</button>`).join('')}
          <button class="aw-filter ${this.sy.hideReady ? 'active' : ''}"
            data-sy-toggle="hideReady">Masquer READY</button>
          <button class="aw-filter ${this.sy.hideReview ? 'active' : ''}"
            data-sy-toggle="hideReview">Masquer NEEDS_REVIEW</button>
        </div>
        <div class="aw-tool-right">
          <span class="sy-count">Sélectionnés : <b>${chosen.length}</b></span>
          <button class="aw-btn" data-sy-pick="ALL">Tout sélectionner</button>
          <button class="aw-btn" data-sy-pick="CREATE">CREATE</button>
          <button class="aw-btn" data-sy-pick="UPDATE">UPDATE</button>
          <button class="aw-btn" data-sy-pick="NONE">Tout désélectionner</button>
          <button class="aw-btn ${this.sy.refreshing ? 'busy' : ''}" data-sy-refresh
            ${this.sy.refreshing ? 'disabled' : ''}>${awIcon('refresh', 13)}
            <span>${this.sy.refreshing ? 'Actualisation…' : 'Rafraîchir'}</span></button>
        </div>
      </div>
      <div class="aw-panel">
        <div class="aw-panel-head"><span class="aw-ph-ic">${awIcon('refresh', 16)}</span>
          <div><b>Changements proposés (${rows.length})</b>
            <small>${chosen.length} sélectionné(s) · ${c.applicable} applicable(s)</small></div>
        </div>
        <div class="aw-tscroll"><table class="aw-grid">
          <thead><tr><th style="width:34px"></th><th>Nom</th><th>Action</th><th>Rareté</th>
            <th class="num">Avant</th><th class="num">Après</th><th>Image</th><th>Statut</th>
            <th>Preuves</th></tr></thead>
          <tbody>${rows.length ? rows.map((e) => this.syncRow(e, applicable)).join('')
            : '<tr><td colspan="9" class="aw-empty">Aucune entrée pour ce filtre.</td></tr>'}</tbody>
        </table></div>
        <div class="aw-tfoot">
          <button class="aw-btn" data-sy-cancel>Annuler</button>
          <button class="aw-btn" data-sy-prepare>${awIcon('refresh', 13)} Préparer la synchronisation</button>
          <button class="aw-btn primary" data-sy-apply
            ${chosen.length && !this.sy.refreshing && !this.sy.applying ? '' : 'disabled'}>
            Appliquer ${chosen.length} changement${chosen.length > 1 ? 's' : ''}</button>
          <span style="margin-left:auto">Aucune suppression ne sera effectuée.</span>
        </div>
      </div>
      ${this.syncOutcome()}
      ${this.refreshBanner()}
      ${this.historyPanel()}`;
  },

  refreshBanner() {
    if (this.sy.refreshState === 'ERROR') {
      return `<div class="aw-note warn" style="margin-top:12px">${awIcon('alert', 17)}
        <div><b>Synchronisation réussie, mais actualisation impossible.</b>
        <p>${esc(this.sy.refreshError || '')} L'état affiché reste celui d'avant l'actualisation.</p></div>
        <button class="aw-btn aw-note-act" data-sy-refresh>Actualiser maintenant</button></div>`;
    }
    if (this.sy.staleSelection?.length) {
      return `<div class="aw-note warn" style="margin-top:12px">${awIcon('alert', 17)}
        <div><b>Sélection périmée (STALE_SELECTION)</b>
        <p>${esc(this.sy.staleSelection.join(', '))} — ces entrées ne sont plus applicables.
           Refais une sélection et une confirmation.</p></div></div>`;
    }
    return '';
  },

  syncRow(e, applicable) {
    const can = applicable.has(e.identity);
    const on = this.sy.selected.has(e.identity) && can;
    // Avant/Apres portent sur le champ REELLEMENT modifie : afficher l'income
    // pour un changement de rarete laisserait croire a une valeur identique.
    const field = e.changed_fields && e.changed_fields.length ? e.changed_fields[0] : null;
    const before = e.action === 'CREATE' ? '—'
      : awVal(field ? field.site : e.current_site_values.income);
    const after = awVal(field ? field.sheet : e.proposed_values.income);
    const fieldLabel = field ? field.site_field : 'income';
    const badge = { READY: 'GROUNDED', READY_WITHOUT_IMAGE: 'CALCULATED',
                    NEEDS_REVIEW: 'WARNING', BLOCKED: 'CONFLICT' }[e.readiness_status] || 'WARNING';
    const proof = this.openProof
      && (this.openProof === e.sheet_evidence || this.openProof === e.site_evidence)
      ? `<tr><td colspan="9">${this.proofBlock(this.openProof)}</td></tr>` : '';
    return `<tr data-sy-row="${esc(e.identity)}" class="${on ? 'selected' : ''}">
      <td>${can
        ? `<span class="aw-check ${on ? 'on' : ''}" data-sy-sel="${esc(e.identity)}">${awIcon('check', 11)}</span>`
        : `<span class="aw-check" style="opacity:.3" title="${esc(e.readiness_reason)}"></span>`}</td>
      <td class="aw-name"><div class="aw-ident">${awThumb(e.identity, e)}
        <span class="aw-rowname">${esc(e.identity)}</span></div></td>
      <td><span class="aw-st k-${esc(e.action)}">${awIcon(e.action === 'CREATE' ? 'plus' : 'refresh', 12)}
        ${e.action === 'CREATE' ? 'Créer' : e.action === 'UPDATE' ? 'Mettre à jour' : esc(e.action)}</span></td>
      <td>${e.proposed_values.rarity ? `<span class="aw-rar">${esc(e.proposed_values.rarity)}</span>` : '—'}</td>
      <td class="aw-num" title="${esc(fieldLabel)}">${esc(before)}</td>
      <td class="aw-num" style="color:var(--ok)" title="${esc(fieldLabel)}">${esc(after)}</td>
      <td>${e.image_status === 'OFFICIAL'
        ? '<span class="aw-rar" style="color:var(--ok)">officielle</span>'
        : '<span class="aw-rar" title="Aucune image inventée">aucune</span>'}</td>
      <td><span class="aw-badge ${badge}" title="${esc(e.readiness_reason)}">${esc(e.readiness_status.replace(/_/g, ' '))}</span></td>
      <td class="aw-acts">
        ${e.sheet_evidence ? `<button class="aw-rowbtn" data-evidence="${esc(e.sheet_evidence)}" title="Preuve Sheet">${awIcon('sheet', 13)}</button>` : ''}
        ${e.site_evidence ? `<button class="aw-rowbtn" data-evidence="${esc(e.site_evidence)}" title="Preuve Site">${awIcon('db', 13)}</button>` : ''}
      </td></tr>${proof}`;
  },

  syncOutcome() {
    const r = this.sy.result;
    if (!r) return '';
    const rolled = (r.results || []).some((x) => x.status === 'ROLLED_BACK');
    const kind = r.ok ? 'ok' : (rolled ? 'danger' : 'warn');
    const lines = (r.results || []).map((x) =>
      `${x.identity} — ${x.action} — ${x.status}${x.reason ? ' (' + x.reason + ')' : ''}`).join('\n');
    return `<div class="aw-note ${kind}" style="margin-top:14px">${awIcon(r.ok ? 'check' : 'alert', 18)}
      <div><b>${r.ok ? 'Synchronisation appliquée et vérifiée'
                     : esc(r.error || 'Synchronisation interrompue')}</b>
      <p style="white-space:pre-wrap">${esc(lines || r.detail || '')}</p>
      ${r.backup_path ? `<p style="margin-top:6px">Sauvegarde : ${esc(r.backup_path)}</p>` : ''}</div>
      ${r.backup_path ? `<button class="aw-btn aw-note-act" data-sy-rollback="${esc(r.backup_path)}">Restaurer</button>` : ''}</div>`;
  },

  /* Fenêtre de confirmation : elle nomme la cible, résume l'impact et exige un
     accord explicite. Aucune écriture ne part sans passer par ici. */
  async confirmSync(keys) {
    const plan = this.payload.sync;
    const chosen = plan.entries.filter((e) => keys.includes(e.identity));
    const creates = chosen.filter((e) => e.action === 'CREATE').length;
    const updates = chosen.filter((e) => e.action === 'UPDATE').length;
    // La portee est ouverte AVANT la modale : ce qui est confirme est
    // exactement ce qui est affiche, et rien d'autre ne pourra etre applique.
    let scope = null;
    try {
      const opened = await J.post('/api/brainrot-sync/confirm-scope',
                                  { plan_hash: plan.plan_hash, selection: keys });
      if (!opened || !opened.ok) throw new Error((opened && opened.error) || 'SCOPE_REFUSED');
      scope = opened.scope;
      this.sy.storeHash = opened.store_hash;
    } catch (err) {
      SyncFeedback.toast('Confirmation impossible : ' + err.message, 'error');
      return;
    }
    const m = modal({
      title: 'JARVIS va modifier Brainrot-Fortnite.com',
      body: `<div class="risk-banner destructive">${icon('alert', 16)}
          <div><b>Écriture réelle sur le site</b><br>
          Fichier ciblé : ${esc(plan.store_path)}</div></div>
        <p style="font-size:13px;margin-top:12px"><b>Vous autorisez ${keys.length}
          changement${keys.length > 1 ? 's' : ''}</b></p>
        <p style="font-size:12.5px;line-height:1.9">
          <b>${creates}</b> création(s)<br><b>${updates}</b> mise(s) à jour<br>
          <b>0</b> suppression</p>
        <div class="sy-confirm-list">
          ${chosen.map((e) => `<div class="sy-confirm-row">
            <b>${esc(e.action)} ${esc(e.identity)}</b>
            ${(e.changed_fields || []).map((f) => `<span>${esc(f.site_field)} : ${esc(awVal(f.site))}
              → ${esc(awVal(f.sheet))}</span>`).join('')}</div>`).join('')}
        </div>
        <div class="sy-confirm-meta">
          <div><span>Sauvegarde prévue</span><b>oui, horodatée avant écriture</b></div>
          <div><span>Hash courant</span><b>${esc(String(this.sy.storeHash || '').slice(0, 24))}</b></div>
          <div><span>selection_hash</span><b>${esc(String(scope.selection_hash).slice(0, 24))}</b></div>
        </div>
        <p class="text-dim" style="font-size:11.5px;margin-top:10px">
          Une sauvegarde horodatée est prise avant écriture. Chaque entrée est relue et
          vérifiée ; en cas d'écart, JARVIS restaure la sauvegarde.</p>`,
      footer: `<button class="btn" data-close>Annuler</button>
               <button class="btn danger" data-go>Confirmer les ${keys.length} changement${keys.length > 1 ? 's' : ''}</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      m.close();
      await this.runSync(keys, '', scope.confirmation_id);
    };
  },

  /** Invite d'ecriture du runner, presentee telle quelle a l'utilisateur. */
  askWriteConfirmation(keys, pending) {
    const m = modal({
      title: "Confirmation d'écriture requise",
      body: `<div class="risk-banner destructive">${icon('alert', 16)}
          <div><b>${esc(pending.action || '')}</b><br>${esc(pending.reason || '')}</div></div>
        <p class="text-dim" style="font-size:11.5px;margin-top:10px">
          Une sauvegarde a déjà été prise. Après écriture, JARVIS relit le fichier,
          vérifie l'entrée champ par champ et restaure la sauvegarde au moindre écart.</p>`,
      footer: `<button class="btn" data-close>Refuser</button>
               <button class="btn danger" data-go>Confirmer l'écriture</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      m.close();
      await J.post('/api/confirm', { confirmation_id: pending.id, approved: true });
      SyncFeedback.advanceTo('write');
      SyncFeedback.render();
      await this.runSync(keys, pending.id);
    };
    m.$('[data-close]').onclick = () => {
      m.close();
      this.sy.result = { ok: false, error: "Écriture refusée — rien n'a été modifié." };
      SyncFeedback.close();
      SyncFeedback.toast("Écriture refusée — rien n'a été modifié.", 'warning');
      this.renderMain();
    };
  },

  async runSync(keys, confirmationId = '', scopeId = '') {
    const plan = this.payload.sync;
    const entries = plan.entries.filter((e) => keys.includes(e.identity));
    if (this.sy.applying && !confirmationId) return;     // double clic sans effet
    // L'overlay verrouille l'action : un second clic pendant l'ecriture est
    // sans effet, et les etapes ne passent au vert que sur preuve du backend.
    if (!confirmationId && !SyncFeedback.open(entries, plan)) return;
    this.sy.applying = true;
    if (scopeId) this.sy.scopeId = scopeId;
    // Cle d'idempotence stable pour CETTE operation : un rejeu reseau ou un
    // second POST renvoie le resultat deja produit, sans reecrire.
    if (!confirmationId) {
      this.sy.idempotencyKey = plan.plan_hash + ':' + (this.sy.scopeId || '')
        + ':' + keys.slice().sort().join('|');
    }
    SyncFeedback.advanceTo('backup');
    SyncFeedback.render();
    try {
      this.sy.result = await J.post('/api/brainrot-sync/apply', {
        plan_hash: plan.plan_hash, selection: keys, approved: true, request_id: plan.sync_id,
        confirmation_id: confirmationId, scope_id: this.sy.scopeId || '',
        idempotency_key: this.sy.idempotencyKey || '',
      });
      // Le runner exige sa propre confirmation d'ecriture : on la presente et
      // on rejoue la meme application une fois l'utilisateur d'accord.
      if (this.sy.result?.error === 'CONFIRMATION_REQUIRED' && this.sy.result.confirmation) {
        SyncFeedback.advanceTo('write');
        SyncFeedback.render();
        return this.askWriteConfirmation(keys, this.sy.result.confirmation);
      }
      (this.sy.result.results || []).forEach((r) => {
        this.pushHistory(r.status === 'VERIFIED' ? 'ok' : 'err',
          r.identity + ' ' + (r.action === 'CREATE' ? 'créé' : 'mis à jour') + ' — ' + r.status);
      });
      await SyncFeedback.finish(this.sy.result, {
        onCompare: () => this.show('site_comparison'),
        onRetry: () => { this.sy.idempotencyKey = ''; this.runSync(keys); },
        onRecompare: () => this.refreshSync(),
      });
      if (this.sy.result.ok) {
        // Auto-refresh : le tableau reflete l'etat reel du site sans que
        // l'utilisateur retape quoi que ce soit. Un echec ne perd aucun etat.
        this.sy.selected = new Set();
        this.sy.scopeId = '';
        this.sy.idempotencyKey = '';
        await this.refreshSync({ auto: true });
      }
    } catch (err) {
      this.sy.result = { ok: false, error: err.message };
      this.pushHistory('err', 'Échec : ' + err.message);
      await SyncFeedback.finish(this.sy.result, {
        onRetry: () => { this.sy.idempotencyKey = ''; this.runSync(keys); } });
    } finally {
      this.sy.applying = false;
    }
    this.renderMain();
  },

  section_site_comparison() {
    const c = this.payload.comparison;
    if (!c) return `<h2>Comparaison site</h2><div class="aw-empty">Aucune comparaison dans ce payload.</div>`;
    if (!c.ok) return this.comparisonError(c);
    const rows = this.filtered();
    const { slice, pages } = this.pageRows(rows);
    return `
      ${this.hero(c)}
      ${this.kpis(c)}
      ${this.toolbar(c, rows)}
      <div class="aw-split">
        <div class="aw-tablewrap">
          <div class="aw-panel">
            <div class="aw-panel-head">
              <span class="aw-ph-ic">${awIcon('table', 16)}</span>
              <div><b>Résultats de comparaison (${rows.length})</b>
                <small data-aw-selinfo>${rows.length} éléments • ${this.t.selected.size} sélectionné(s)</small></div>
              ${this.pager(pages)}
            </div>
            <div data-aw-tablebody>${this.tableBody(slice)}</div>
            ${this.tableFoot(rows)}
          </div>
        </div>
        ${this.detail.key ? this.detailPanel() : ''}
      </div>
      ${this.detail.key ? '<div class="aw-drawer-back" data-aw-drawerback></div>' : ''}
      ${this.bottomCards()}`;
  },

  comparisonError(c) {
    const tab = esc(c.tab || c.tab_requested || '');
    const tabs = (c.available_tabs || []).slice(0, 8);
    const tabIssue = c.interrupted || /SHEET.?TAB.?NOT.?FOUND/i.test(String(c.code || ''));
    const retriable = tabIssue && !!(this.payload?.source?.url)
                      && typeof window.App?.sendJarvisMessage === 'function';
    const chips = tabs.length
      ? `<div class="aw-err-tabs">${tabs.map((t) => `<span class="aw-chip aw-err-tab">${esc(t)}</span>`).join('')}</div>`
      : '';
    return `<h2>Comparaison site</h2>
      <div class="aw-note danger aw-err ${tabIssue ? 'is-tab' : ''}">
        ${awIcon('alert', 20)}
        <div class="aw-err-body">
          <b>${tabIssue
            ? esc('⚠ COMPARAISON INTERROMPUE — Onglet Google Sheet introuvable')
            : esc(c.error || 'Comparaison indisponible')}</b>
          ${tabIssue ? `<p>Le Google Sheet a bien été trouvé, mais l'onglet demandé n'a pas pu
            être identifié${tab ? ` : <code>${tab}</code>` : ''}.</p>` : ''}
          ${c.detail ? `<p>${esc(c.detail)}</p>` : ''}
          ${chips}
          ${c.suggestion ? `<p class="aw-err-sug">${esc(c.suggestion)}</p>` : ''}
          ${retriable
            ? `<button class="btn primary sm" data-detab>Détecter l'onglet puis comparer</button>
               <p class="aw-err-hint">JARVIS relancera la comparaison sur ce Google Sheet en
               résolvant l'onglet automatiquement.</p>`
            : ''}
        </div>
      </div>`;
  },

  hero(c) {
    const when = this.payload.request?.at ? new Date(this.payload.request.at * 1000) : new Date();
    const time = when.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    return `<div class="aw-hero">
      <div class="aw-hero-icon">${awIcon('doc', 22)}</div>
      <div class="aw-hero-text">
        <h2>Comparaison site ${awIcon('link', 15)}</h2>
        <p>${c.totals.sheet} lignes Sheet comparées à ${c.totals.site} entrées du site
           • ${esc(c.sheet.tab)} / ${esc(c.sheet.table)}</p>
      </div>
      <div class="aw-hero-tiles">
        <div class="aw-hero-tile">${awIcon('sheet', 16)}
          <div><small>Source active</small><b>${esc(this.payload.source.label || 'Google Sheet')}</b>
          <span>${this.payload.source.sheet_count} onglets • ${esc(c.sheet.tab)}</span></div></div>
        <div class="aw-hero-tile">${awIcon('clock', 16)}
          <div><small>Dernière analyse</small><b>Aujourd'hui à ${time}</b>
          <span>${(this.payload.duration_ms / 1000 || 0).toFixed(1)} secondes</span></div></div>
        <div class="aw-hero-tile">${awIcon('server', 16)}
          <div><small>Cible distante</small><b>${esc(c.site.path)}</b>
          <span>lecture seule • aucune écriture</span></div></div>
      </div>
    </div>`;
  },

  kpis(c) {
    const total = c.totals.sheet || 1;
    const pct = (n) => `${(n / total * 100).toFixed(1).replace('.', ',')}%`;
    const cards = [
      { cls: 'k-sheet', icon: 'sheet', val: c.totals.sheet, label: 'Total Sheet' },
      { cls: 'k-site', icon: 'db', val: c.totals.site, label: 'Total Site' },
      ...Object.keys(AW_STATUS).map((s) => ({
        cls: `k-${s}`, icon: AW_STATUS[s].icon, val: c.counts[s] ?? 0,
        label: AW_STATUS[s].label, pct: pct(c.counts[s] ?? 0), status: s,
      })),
    ];
    return `<div class="aw-kpis">${cards.map((k, i) => `
      <div class="aw-kpi ${k.cls} ${k.status ? 'clickable' : ''} ${k.status && this.t.filter === k.status ? 'active' : ''}"
           style="animation-delay:${i * 20}ms"
           ${k.status ? `data-kpi="${k.status}" title="Filtrer : ${esc(k.label)}"` : ''}>
        <div class="aw-kpi-ic">${awIcon(k.icon, 20)}</div>
        <div class="aw-kpi-body">
          <div class="aw-kpi-val">${k.val}</div>
          <div class="aw-kpi-label">${esc(k.label)}</div>
          ${k.pct !== undefined ? `<span class="aw-kpi-pct">${k.pct}</span>` : ''}
        </div>
      </div>`).join('')}</div>`;
  },

  toolbar(c, rows) {
    const f = this.t.filter;
    const anomalies = this.payload.warnings.length;
    const chips = [
      { id: 'ALL', label: `Tous (${this.entries().length})`, icon: 'list' },
      { id: 'CHANGED', label: `Changements (${(c.counts.UPDATE || 0) + (c.counts.CREATE || 0)})`,
        icon: 'swap', hero: true },
      { id: 'UPDATE', label: `Updates (${c.counts.UPDATE || 0})`, icon: 'up' },
      { id: 'CREATE', label: `Creates (${c.counts.CREATE || 0})`, icon: 'plus' },
      { id: 'CONFLICT', label: `Conflits (${c.counts.CONFLICT || 0})`, icon: 'alert' },
      { id: 'NO_CHANGE', label: `Aucun changement (${c.counts.NO_CHANGE || 0})`, icon: 'check' },
    ];
    return `<div class="aw-toolbar">
      <div class="aw-filters">
        ${chips.map((ch) => `<button class="aw-filter f-${ch.id} ${ch.hero ? 'hero' : ''} ${f === ch.id ? 'active' : ''}"
          data-filter="${ch.id}">${awIcon(ch.icon, 14)}${esc(ch.label)}</button>`).join('')}
        <button class="aw-filter f-ANOMALIES" data-goto="warnings">${awIcon('alert', 14)}Anomalies (${anomalies})</button>
      </div>
      <div class="aw-tool-right">
        <div class="aw-search-box aw-tool-search">
          ${awIcon('search', 14)}
          <input type="search" placeholder="Rechercher un brainrot…" value="${esc(this.t.q)}" data-aw-tq />
        </div>
        <div class="aw-sortwrap"><span>Trier par</span>
          <select class="aw-select" data-aw-sort>
            ${[['index:asc', 'Ordre Sheet'], ['name:asc', 'Nom (A → Z)'], ['name:desc', 'Nom (Z → A)'],
               ['status:asc', 'Statut'], ['sheet:desc', 'Valeur Sheet ↓'], ['mods:desc', 'Modifications ↓']]
              .map(([v, l]) => `<option value="${v}" ${`${this.t.sortKey}:${this.t.sortDir}` === v ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
        </div>
        <div class="aw-seg">
          <button class="${this.t.view === 'list' ? 'active' : ''}" data-view="list" title="Liste">${awIcon('list', 14)}</button>
          <button class="${this.t.view === 'grid' ? 'active' : ''}" data-view="grid" title="Grille">${awIcon('grid', 14)}</button>
        </div>
        <button class="aw-btn" data-aw-columns>${awIcon('columns', 14)}<span>Colonnes</span></button>
      </div>
    </div>`;
  },

  pager(pages) {
    const p = this.t.page;
    return `<div class="aw-pager">
      <button class="aw-btn icon" data-page="1" ${p <= 1 ? 'disabled' : ''}>${awIcon('first', 13)}</button>
      <button class="aw-btn icon" data-page="${p - 1}" ${p <= 1 ? 'disabled' : ''}>${awIcon('left', 13)}</button>
      <span class="aw-pageno">Page ${p} sur ${pages}</span>
      <button class="aw-btn icon" data-page="${p + 1}" ${p >= pages ? 'disabled' : ''}>${awIcon('right', 13)}</button>
      <button class="aw-btn icon" data-page="${pages}" ${p >= pages ? 'disabled' : ''}>${awIcon('last', 13)}</button>
    </div>`;
  },

  cols() { return AW_COLUMNS.filter((c) => c.fixed || !this.t.hidden.has(c.id)); },

  tableBody(slice) {
    if (this.t.view === 'grid') return this.gridBody(slice);
    if (!slice.length) return '<div class="aw-empty">Aucun élément pour ce filtre.</div>';
    const cols = this.cols();
    const th = cols.map((c) => {
      const sorted = c.sort && this.t.sortKey === c.sort;
      return `<th class="${c.sort ? 'sortable' : ''} ${sorted ? 'sorted' : ''} ${c.num ? 'num' : ''}"
        ${c.sort ? `data-sort="${c.sort}"` : ''}>${esc(c.label)}
        ${c.sort ? `<span class="aw-sortic">${sorted ? (this.t.sortDir === 'asc' ? '↑' : '↓') : '↕'}</span>` : ''}</th>`;
    }).join('');
    const allOn = slice.every((r) => this.t.selected.has(r.key));
    return `<div class="aw-tscroll"><table class="aw-grid">
      <thead><tr>
        <th style="width:34px"><span class="aw-check ${allOn ? 'on' : ''}" data-selall>${awIcon('check', 11)}</span></th>
        ${th}</tr></thead>
      <tbody>${slice.map((r) => this.row(r, cols)).join('')}</tbody>
    </table></div>`;
  },

  row(r, cols) {
    const e = r.entry;
    const on = this.t.selected.has(r.key);
    const active = this.detail.key === r.key;
    const cell = {
      index: `<td class="aw-idx">${r.index}</td>`,
      name: `<td class="aw-name"><div class="aw-ident">
        ${awThumb(r.name, e)}
        <span class="aw-rowname">${esc(r.name || '(sans nom)')}</span></div></td>`,
      status: `<td>${this.statusBadge(r.status)}</td>`,
      rarity: `<td>${r.rarity ? `<span class="aw-rar">${esc(r.rarity)}</span>` : '<span class="aw-dim">—</span>'}</td>`,
      sheet: `<td class="aw-num">${r.sheet === undefined ? '—' : '$' + awNum(r.sheet)}</td>`,
      site: `<td class="aw-num">${r.site === undefined || r.site === null ? '—' : '$' + awNum(r.site)}</td>`,
      mods: `<td>${this.modCell(e)}</td>`,
      actions: `<td class="aw-acts">${this.directActionBtn(e)}<button class="aw-rowbtn" data-open="${esc(r.key)}" title="Détail">${awIcon('eye', 14)}</button><button class="aw-rowbtn" data-open="${esc(r.key)}" title="Preuves">${awIcon('dots', 14)}</button></td>`,
    };
    return `<tr data-row="${esc(r.key)}" class="${active || on ? 'selected' : ''}">
      <td><span class="aw-check ${on ? 'on' : ''}" data-sel="${esc(r.key)}">${awIcon('check', 11)}</span></td>
      ${cols.map((c) => cell[c.id]).join('')}</tr>`;
  },

  /* ===================================== ACTIONS DIRECTES ================= */
  /* Un clic sur [+ Créer] / [Mettre à jour] emprunte EXACTEMENT le pipeline du
     lot : confirmSync -> confirm-scope -> backup -> write -> verify -> refresh.
     La sélection ne contient qu'une entrée ; aucune logique métier n'est
     dupliquée, donc portée de confirmation, expected_sha256, selection_hash,
     idempotence, audit et rollback s'appliquent à l'identique. */

  /** Entrée du plan de synchronisation correspondant à une ligne de comparaison. */
  planEntryFor(identity) {
    return (this.payload.sync?.entries || []).find((x) => x.identity === identity) || null;
  },

  /** Bouton d'action directe, ou l'état « Synchronisé » quand il n'y a rien à faire. */
  directActionBtn(e) {
    const id = String(e.identity || '');
    if (e.status === 'NO_CHANGE') {
      return `<span class="aw-synced" title="Aucun écart avec le site">${awIcon('check', 12)}Synchronisé</span>`;
    }
    if (e.status !== 'CREATE' && e.status !== 'UPDATE') return '';
    const planned = this.planEntryFor(id);
    if (!planned) return '';
    const ready = planned.readiness_status === 'READY'
               || planned.readiness_status === 'READY_WITHOUT_IMAGE';
    if (!ready) {
      return `<span class="aw-synced blocked" title="${esc(planned.readiness_reason
        || 'Entrée non applicable en l\'état')}">${awIcon('alert', 12)}Non applicable</span>`;
    }
    const isCreate = e.status === 'CREATE';
    return `<button class="aw-rowbtn aw-direct ${isCreate ? 'create' : 'update'}"
      data-sy-direct="${esc(id)}" title="${isCreate ? 'Créer cette entrée sur le site'
      : 'Appliquer cette modification sur le site'}">
      ${awIcon(isCreate ? 'plus' : 'refresh', 13)}${isCreate ? 'Créer' : 'Mettre à jour'}</button>`;
  },

  /** Point d'entrée unique des actions directes (ligne ET panneau détail). */
  async directAction(identity) {
    const planned = this.planEntryFor(identity);
    if (!planned) {
      SyncFeedback.toast('Cette entrée n\'est plus dans le plan — relance un rafraîchissement.',
                         'warning');
      return;
    }
    const ready = planned.readiness_status === 'READY'
               || planned.readiness_status === 'READY_WITHOUT_IMAGE';
    if (!ready) {
      SyncFeedback.toast(planned.readiness_reason || 'Entrée non applicable en l\'état.',
                         'warning');
      return;
    }
    if (this.sy.applying) return;               // double clic sans effet
    // Le lot garde sa propre sélection : une action directe ne la modifie pas.
    await this.confirmSync([identity]);
  },

  statusBadge(status) {
    const s = AW_STATUS[status] || { label: status, icon: 'info' };
    return `<span class="aw-st k-${status}">${awIcon(s.icon, 12)}${esc(s.label)}</span>`;
  },

  modCell(e) {
    if (e.status === 'UPDATE') {
      return `<span class="aw-mod update">${awIcon('up', 12)}Mettre à jour (${(e.changed_fields || []).length})</span>`;
    }
    if (e.status === 'CREATE') return `<span class="aw-mod create">${awIcon('plus', 12)}Créer</span>`;
    if (e.status === 'CONFLICT') return `<span class="aw-mod" style="color:var(--danger)">${awIcon('alert', 12)}Ambigu</span>`;
    return '<span class="aw-dim">—</span>';
  },

  gridBody(slice) {
    if (!slice.length) return '<div class="aw-empty">Aucun élément pour ce filtre.</div>';
    return `<div class="aw-cards">${slice.map((r) => `
      <div class="aw-gcard ${this.detail.key === r.key ? 'selected' : ''}" data-row="${esc(r.key)}">
        <div class="aw-gcard-top">
          ${awThumb(r.name, r.entry)}
          <b>${esc(r.name)}</b></div>
        ${this.statusBadge(r.status)}
        <div style="margin-top:9px">
          <div class="aw-gcard-row"><span>Sheet</span><span>${r.sheet === undefined ? '—' : '$' + awNum(r.sheet)}</span></div>
          <div class="aw-gcard-row"><span>Site</span><span>${r.site === undefined || r.site === null ? '—' : '$' + awNum(r.site)}</span></div>
          <div class="aw-gcard-row"><span>Rareté</span><span>${esc(r.rarity || '—')}</span></div>
        </div>
      </div>`).join('')}</div>`;
  },

  tableFoot(rows) {
    return `<div class="aw-tfoot">
      <span class="aw-tfoot-lbl">Lignes</span>
      <div class="aw-seg sm">${[15, 25, 50, 100].map((n) =>
        `<button data-size="${n}" class="${this.t.size === n ? 'active' : ''}">${n}</button>`).join('')}</div>
      <span>${rows.length} résultat(s)${this.t.selected.size ? ` • ${this.t.selected.size} sélectionné(s)` : ''}</span>
      <span style="margin-left:auto" class="aw-pill ok">LECTURE SEULE · AUCUNE ÉCRITURE</span>
    </div>`;
  },

  bottomCards() {
    const recos = this.payload.recommendations;
    const warns = this.payload.warnings;
    // Synthese construite depuis les donnees reelles du payload : les libelles
    // les plus frequents, jamais un texte decoratif.
    const top = (items, key) => {
      const c = {};
      items.forEach((i) => { const k = i[key] || ''; c[k] = (c[k] || 0) + 1; });
      return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 2)
        .map(([k, n]) => `${k}${n > 1 ? ` ×${n}` : ''}`).join(' · ');
    };
    const warnSummary = warns.length
      ? top(warns, 'badge').replace(/_/g, ' ') + ` — ${new Set(warns.map((w) => w.sheet)).size} onglet(s) concerné(s)`
      : 'Aucune anomalie structurelle détectée.';
    const recoSummary = recos.length
      ? recos[0].title + (recos.length > 1 ? ` · +${recos.length - 1} autre(s)` : '')
      : 'Aucune action corrective requise.';
    const card = (id, target, color, ic, title, count, summary) => `
      <div class="aw-bcard" data-goto="${target}" style="--bc:${color}">
        <div class="aw-bcard-ic">${awIcon(ic, 19)}</div>
        <div class="aw-bcard-body">
          <b>${title}<span class="aw-bcard-n">${count}</span></b>
          <small>${esc(summary)}</small>
        </div>
        <span class="aw-bcard-cta">Voir ${awIcon('right', 13)}</span>
      </div>`;
    return `<div class="aw-bottom">
      ${card('r', 'recommendations', '#22d3ee', 'bulb', 'Recommandations', recos.length, recoSummary)}
      ${card('a', 'warnings', '#fb7185', 'alert', 'Anomalies détectées', warns.length, warnSummary)}
    </div>`;
  },

  /* ==================================================== PANNEAU DE DÉTAIL = */
  currentRow() { return this.rows().find((r) => r.key === this.detail.key); },

  detailPanel() {
    const row = this.currentRow();
    if (!row) return '';
    const e = row.entry;
    const list = this.filtered();
    const pos = list.findIndex((r) => r.key === row.key);
    const tabs = [['comparison', 'Comparaison'],
                  ['evidence', `Preuves (${[e.evidence_sheet, e.evidence_site].filter(Boolean).length})`],
                  ['meta', 'Métadonnées'], ['reco', 'Recommandations']];
    return `<aside class="aw-detail">
      <div class="aw-detail-head">
        <div class="aw-detail-top">
          ${awThumb(row.name, row.entry, 'lg')}
          <div class="aw-detail-id">
            <h3>${esc(row.name || '(sans nom)')} ${this.statusBadge(row.status)}</h3>
            <p>${esc(e.reason || '')}${e.match_rule ? ` • Identité : ${esc(e.match_rule)}` : ''}</p>
          </div>
          <div class="aw-detail-nav">
            <span class="aw-idx">#${row.index}</span>
            <button class="aw-btn icon" data-step="-1" ${pos <= 0 ? 'disabled' : ''}>${awIcon('left', 13)}</button>
            <button class="aw-btn icon" data-step="1" ${pos >= list.length - 1 ? 'disabled' : ''}>${awIcon('right', 13)}</button>
            <button class="aw-btn icon" data-aw-closedetail>${awIcon('x', 14)}</button>
          </div>
        </div>
        <div class="aw-tags">
          ${row.rarity ? `<span class="aw-rar">${esc(row.rarity)}</span>` : ''}
          ${e.sheet_row ? `<span class="aw-rar">Sheet L${e.sheet_row}</span>` : ''}
          ${e.site_line ? `<span class="aw-rar">Site L${e.site_line}</span>` : ''}
        </div>
      </div>
      <div class="aw-tabs">${tabs.map(([id, label]) =>
        `<button data-tab="${id}" class="${this.detail.tab === id ? 'active' : ''}">${esc(label)}</button>`).join('')}</div>
      <div class="aw-detail-body">${this.detailBody(row)}</div>
    </aside>`;
  },

  detailBody(row) {
    if (this.detail.tab === 'evidence') return this.detailEvidence(row);
    if (this.detail.tab === 'meta') return this.detailMeta(row);
    if (this.detail.tab === 'reco') return this.detailReco(row);
    return this.detailComparison(row);
  },

  detailComparison(row) {
    const e = row.entry;
    const map = this.payload.comparison.mapping;
    const labels = Object.keys(map);
    const changed = new Set((e.changed_fields || []).map((c) => c.field));
    const missing = new Set((e.missing_fields || []).map((c) => c.field));
    const changedBy = Object.fromEntries((e.changed_fields || []).map((c) => [c.field, c]));
    const isCreate = e.status === 'CREATE';
    const isConflict = e.status === 'CONFLICT';

    if (isConflict) {
      return `<div class="aw-note danger">${awIcon('alert', 18)}
        <div><b>Identité ambiguë — aucune action automatique</b>
        <p>${esc(e.reason)}</p></div></div>
        <div class="aw-note info">${awIcon('info', 17)}
          <div><b>Pourquoi le rapprochement est bloqué</b>
          <p>L'échelle d'identité (identifiant canonique, puis slug, puis nom normalisé) a trouvé
             plusieurs candidats. JARVIS ne choisit jamais à votre place : aucune modification
             n'est proposée tant que l'ambiguïté n'est pas levée à la source.</p></div></div>
        ${this.mappedChips(labels, changed, missing)}`;
    }

    const sheetRows = labels.map((l) => {
      const v = (e.sheet_values || {})[l];
      return `<div class="aw-vs-row ${changed.has(l) ? 'diff' : ''} ${missing.has(l) ? 'absent' : ''}">
        <span class="aw-k">${esc(l)}</span><span class="aw-v">${esc(awVal(v))}</span></div>`;
    }).join('');
    const siteRows = labels.map((l) => {
      const key = map[l];
      const v = isCreate ? undefined : (e.site_values || {})[key];
      const ch = changedBy[l];
      const body = isCreate
        ? '<span class="aw-v" style="font-style:italic;color:var(--text-faint)">Absent</span>'
        : (ch ? `<span class="aw-v"><span class="aw-old">${esc(awVal(ch.site))}</span>
                 <span class="aw-new">${esc(awVal(ch.sheet))}</span></span>`
              : `<span class="aw-v">${esc(awVal(v))}</span>`);
      return `<div class="aw-vs-row ${ch ? 'diff' : ''}"><span class="aw-k">${esc(key)}</span>${body}</div>`;
    }).join('');

    const diffs = changed.size;
    const eq = diffs === 0 && !isCreate;
    const banner = isCreate
      ? `<div class="aw-note info">${awIcon('plus', 18)}
          <div><b>À créer sur le site</b>
          <p>Cette entrée existe dans le Sheet et n'a aucun équivalent sur le site.</p></div>
          <span class="aw-pill ghost aw-note-act">À créer</span></div>`
      : eq
        ? `<div class="aw-note ok">${awIcon('check', 18)}
            <div><b>Aucune différence détectée</b>
            <p>Les valeurs du Sheet sont identiques à celles du site pour tous les champs mappés.</p></div></div>`
        : `<div class="aw-note warn">${awIcon('alert', 18)}
            <div><b>${diffs} différence${diffs > 1 ? 's' : ''} détectée${diffs > 1 ? 's' : ''}</b>
            <p>Champs concernés : ${esc([...changed].join(', '))}. L'ancienne valeur du site est barrée,
               la valeur du Sheet est mise en évidence.</p></div></div>`;

    return `<div class="aw-vs">
        <div class="aw-vs-card"><header>${awIcon('sheet', 15)}Données Sheet</header>${sheetRows}</div>
        <div class="aw-vs-mid">
          <div class="aw-eq ${eq ? '' : 'ne'}">${eq ? '=' : '≠'}</div>
          <small>${eq ? 'Identique' : isCreate ? 'Absent du site' : 'Différent'}</small>
        </div>
        <div class="aw-vs-card site"><header>${awIcon('db', 15)}Données Site</header>${siteRows}</div>
      </div>
      ${banner}
      ${this.mappedChips(labels, changed, missing)}
      <div class="aw-note info">${awIcon('info', 17)}
        <div><b>Statut</b><p>${esc(e.recommended_action || '')}</p></div>
        <span class="aw-pill ok aw-note-act">Lecture seule</span></div>`;
  },

  mappedChips(labels, changed, missing) {
    return `<div class="aw-sub-title">Champs mappés vérifiés</div>
      <div class="aw-chips">${labels.map((l) => {
        const off = changed.has(l) || missing.has(l);
        return `<span class="aw-chip ${off ? 'off' : ''}">${awIcon(off ? 'alert' : 'check', 12)}${esc(l)}</span>`;
      }).join('')}</div>`;
  },

  detailEvidence(row) {
    const e = row.entry;
    const ids = [e.evidence_sheet, e.evidence_site].filter(Boolean);
    if (!ids.length) return '<div class="aw-empty">Aucune preuve rattachée à cet élément.</div>';
    return ids.map((id) => {
      const ev = this.payload.evidence.find((x) => x.id === id);
      if (!ev) return '';
      return `<div class="aw-sub-title">${esc(ev.sheet)}</div>${this.proofBlock(id)}`;
    }).join('');
  },

  detailMeta(row) {
    const e = row.entry;
    const meta = [
      ['Statut', e.status], ['Règle d\'identité', e.match_rule || '—'],
      ['Slug', e.slug || '—'], ['Ligne Sheet', e.sheet_row ?? '—'],
      ['Entrée site', e.site_index ?? '—'], ['Ligne fichier site', e.site_line ?? '—'],
      ['Champs modifiés', (e.changed_fields || []).length],
      ['Champs vides côté Sheet', (e.missing_fields || []).length],
    ];
    return `<div class="aw-vs-card">${meta.map(([k, v]) =>
      `<div class="aw-vs-row"><span class="aw-k">${esc(k)}</span><span class="aw-v">${esc(String(v))}</span></div>`).join('')}</div>
      <div class="aw-note info" style="margin-top:12px">${awIcon('shield', 17)}
        <div><b>Origine des données</b>
        <p>Sheet : ${esc(this.payload.comparison.sheet.tab)} / ${esc(this.payload.comparison.sheet.table)}.
           Site : ${esc(this.payload.comparison.site.path)} lu en SSH lecture seule.</p></div></div>`;
  },

  /** Action directe du panneau détail — même chemin que le bouton de ligne. */
  detailActionBtn(e) {
    const id = String(e.identity || '');
    if (e.status === 'NO_CHANGE') return '';
    if (e.status !== 'CREATE' && e.status !== 'UPDATE') return '';
    const planned = this.planEntryFor(id);
    if (!planned) return '';
    const ready = planned.readiness_status === 'READY'
               || planned.readiness_status === 'READY_WITHOUT_IMAGE';
    if (!ready) return '';
    const isCreate = e.status === 'CREATE';
    return `<button class="aw-btn aw-note-act aw-direct ${isCreate ? 'create' : 'update'}"
      data-sy-direct="${esc(id)}">${awIcon(isCreate ? 'plus' : 'refresh', 13)}
      ${isCreate ? 'Créer sur le site' : 'Appliquer cette modification'}</button>`;
  },

  detailReco(row) {
    const e = row.entry;
    return `<div class="aw-note ${e.status === 'NO_CHANGE' ? 'ok' : 'info'}">
        ${awIcon(e.status === 'NO_CHANGE' ? 'check' : 'bulb', 18)}
        <div><b>Action recommandée</b><p>${esc(e.recommended_action || '—')}</p></div>
        ${this.detailActionBtn(e)}</div>
      ${(e.missing_fields || []).length ? `<div class="aw-note warn">${awIcon('alert', 17)}
        <div><b>Champs vides côté Sheet</b>
        <p>${esc((e.missing_fields || []).map((c) => c.field).join(', '))} — le Sheet ne dit rien sur
           ces champs ; le site conserve sa valeur, rien n'est effacé.</p></div></div>` : ''}
      <div class="aw-sub-title">Recommandations globales</div>
      ${this.payload.recommendations.map((r) => `<div class="aw-card">
        <h3>${esc(r.title)}</h3><p>${esc(r.detail)}</p></div>`).join('')}`;
  },

  /* ================================================================ liens = */
  bind(root) {
    const rerenderBody = () => {
      const rows = this.filtered();
      const { slice, pages } = this.pageRows(rows);
      const host = root.querySelector('[data-aw-tablebody]');
      if (!host) return this.renderMain();
      // Seul le corps de table est reconstruit : le reste du dashboard,
      // panneau de détail compris, n'est jamais re-rendu pour un tri.
      host.innerHTML = this.tableBody(slice);
      const info = root.querySelector('[data-aw-selinfo]');
      if (info) info.textContent = `${rows.length} éléments • ${this.t.selected.size} sélectionné(s)`;
      const pg = root.querySelector('.aw-pager');
      if (pg) pg.outerHTML = this.pager(pages);
      const foot = root.querySelector('.aw-tfoot');
      if (foot) foot.outerHTML = this.tableFoot(rows);
      this.bind(root);
    };

    root.querySelectorAll('[data-evidence]').forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        const id = el.dataset.evidence;
        this.openProof = this.openProof === id ? null : id;
        this.renderMain();
      };
    });
    root.querySelectorAll('[data-goto]').forEach((el) => {
      el.onclick = (ev) => { ev.stopPropagation(); this.show(el.dataset.goto); };
    });
    root.querySelectorAll('[data-filter]').forEach((el) => {
      el.onclick = () => { this.t.filter = el.dataset.filter; this.t.page = 1; this.renderMain(); };
    });
    root.querySelectorAll('[data-kpi]').forEach((el) => {
      el.onclick = () => { this.t.filter = el.dataset.kpi; this.t.page = 1; this.renderMain(); };
    });
    root.querySelectorAll('[data-view]').forEach((el) => {
      el.onclick = () => { this.t.view = el.dataset.view; this.renderMain(); };
    });
    root.querySelectorAll('[data-page]').forEach((el) => {
      el.onclick = () => { this.t.page = Math.max(1, Number(el.dataset.page)); rerenderBody(); };
    });
    root.querySelectorAll('[data-sort]').forEach((el) => {
      el.onclick = () => {
        const key = el.dataset.sort;
        if (this.t.sortKey === key) this.t.sortDir = this.t.sortDir === 'asc' ? 'desc' : 'asc';
        else { this.t.sortKey = key; this.t.sortDir = 'asc'; }
        rerenderBody();
      };
    });
    root.querySelectorAll('[data-row]').forEach((el) => {
      el.onclick = (ev) => {
        if (ev.target.closest('[data-sel]')) return;
        const row = this.rows().find((r) => r.key === el.dataset.row);
        if (row) { this.detail.key = row.key; this.renderMain(); }
      };
    });
    // Actions directes : le clic ne doit ni ouvrir le détail, ni cocher la ligne.
    root.querySelectorAll('[data-sy-direct]').forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        this.directAction(el.dataset.syDirect);
      };
    });
    root.querySelectorAll('[data-open]').forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        this.detail.key = el.dataset.open;
        this.detail.tab = el.title === 'Preuves' ? 'evidence' : 'comparison';
        this.renderMain();
      };
    });
    root.querySelectorAll('[data-sel]').forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        const k = el.dataset.sel;
        this.t.selected.has(k) ? this.t.selected.delete(k) : this.t.selected.add(k);
        rerenderBody();
      };
    });
    const selAll = root.querySelector('[data-selall]');
    if (selAll) selAll.onclick = () => {
      const { slice } = this.pageRows(this.filtered());
      const allOn = slice.every((r) => this.t.selected.has(r.key));
      slice.forEach((r) => (allOn ? this.t.selected.delete(r.key) : this.t.selected.add(r.key)));
      rerenderBody();
    };
    root.querySelectorAll('[data-tab]').forEach((el) => {
      el.onclick = () => { this.detail.tab = el.dataset.tab; this.renderMain(); };
    });
    root.querySelectorAll('[data-step]').forEach((el) => {
      el.onclick = () => {
        const list = this.filtered();
        const i = list.findIndex((r) => r.key === this.detail.key) + Number(el.dataset.step);
        if (list[i]) { this.detail.key = list[i].key; this.renderMain(); }
      };
    });
    const closeDetail = root.querySelector('[data-aw-closedetail]');
    if (closeDetail) closeDetail.onclick = () => { this.detail.key = null; this.renderMain(); };
    const back = root.querySelector('[data-aw-drawerback]');
    if (back) back.onclick = () => { this.detail.key = null; this.renderMain(); };

    const tq = root.querySelector('[data-aw-tq]');
    if (tq) tq.oninput = (ev) => {
      this.t.q = ev.target.value.trim().toLowerCase();
      this.t.page = 1;
      rerenderBody();
    };
    const sort = root.querySelector('[data-aw-sort]');
    if (sort) sort.onchange = (ev) => {
      const [k, d] = ev.target.value.split(':');
      this.t.sortKey = k; this.t.sortDir = d;
      rerenderBody();
    };
    root.querySelectorAll('[data-size]').forEach((el) => {
      el.onclick = () => { this.t.size = Number(el.dataset.size); this.t.page = 1; rerenderBody(); };
    });
    root.querySelectorAll('[data-sy-only]').forEach((el) => {
      el.onclick = () => { this.sy.only = el.dataset.syOnly; this.renderMain(); };
    });
    root.querySelectorAll('[data-sy-toggle]').forEach((el) => {
      el.onclick = () => {
        this.sy[el.dataset.syToggle] = !this.sy[el.dataset.syToggle];
        this.renderMain();
      };
    });
    root.querySelectorAll('[data-sy-sel]').forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        const k = el.dataset.sySel;
        if (this.sy.selected.has(k)) this.sy.selected.delete(k);
        else this.sy.selected.add(k);
        this.renderMain();
      };
    });
    root.querySelectorAll('[data-sy-pick]').forEach((el) => {
      el.onclick = () => {
        const kind = el.dataset.syPick;
        const applicable = this.applicableKeys();
        this.sy.selected = new Set(kind === 'NONE' ? []
          : (this.payload.sync.entries || [])
            .filter((e) => (kind === 'ALL' || e.action === kind) && applicable.has(e.identity))
            .map((e) => e.identity));
        this.renderMain();
      };
    });
    const syCancel = root.querySelector('[data-sy-cancel]');
    if (syCancel) syCancel.onclick = () => {
      this.sy.selected = new Set();
      this.sy.result = null;
      this.renderMain();
    };
    const syPrepare = root.querySelector('[data-sy-prepare]');
    if (syPrepare) syPrepare.onclick = () => {
      this.close();
      window.App?.sendJarvisMessage?.('Prépare la synchronisation', 'text');
    };
    const detab = root.querySelector('[data-detab]');
    if (detab) detab.onclick = () => {
      const url = this.payload?.source?.url || '';
      this.close();
      window.App?.sendJarvisMessage?.(
        url
          ? `Compare ce Google Sheet à mon site et détecte automatiquement le bon onglet : ${url}`
          : 'Compare ce Google Sheet à mon site en détectant automatiquement le bon onglet.',
        'text');
    };
    const syApply = root.querySelector('[data-sy-apply]');
    if (syApply) syApply.onclick = () => {
      const applicable = this.applicableKeys();
      const keys = [...this.sy.selected].filter((k) => applicable.has(k));
      if (keys.length) this.confirmSync(keys);
    };
    root.querySelectorAll('[data-sy-refresh]').forEach((el) => {
      el.onclick = () => this.refreshSync();
    });
    const syRollback = root.querySelector('[data-sy-rollback]');
    if (syRollback) syRollback.onclick = async () => {
      const res = await J.post('/api/brainrot-sync/rollback',
                               { backup_path: syRollback.dataset.syRollback });
      toast(res.ok ? 'Sauvegarde restaurée.' : 'Restauration impossible.');
      this.sy.result = { ...(this.sy.result || {}), rollback_done: !!res.ok };
      this.renderMain();
    };
    const colsBtn = root.querySelector('[data-aw-columns]');
    if (colsBtn) colsBtn.onclick = () => this.columnPicker();
    this.nudgeImages(root);
  },

  /* Les vignettes restent en `loading="lazy"` — c'est le bon défaut pour une
     longue liste. Mais certains contextes (conteneur scrollable imbriqué,
     onglet en arrière-plan) ne déclenchent jamais l'observateur et laissent
     l'image vide. Ce filet repasse en chargement direct celles qui n'ont
     toujours rien demandé, pour qu'aucune vignette réelle ne manque. */
  nudgeImages(root) {
    clearTimeout(this._nudge);
    this._nudge = setTimeout(() => {
      root.querySelectorAll('.aw-avatar img[loading="lazy"]').forEach((img) => {
        if (!img.complete && !img.currentSrc) img.loading = 'eager';
      });
    }, 220);
  },

  columnPicker() {
    const body = AW_COLUMNS.filter((c) => !c.fixed).map((c) => `
      <label style="display:flex;align-items:center;gap:9px;padding:7px 2px;font-size:12px;cursor:pointer">
        <input type="checkbox" data-col="${c.id}" ${this.t.hidden.has(c.id) ? '' : 'checked'} />
        ${esc(c.label)}</label>`).join('');
    const m = modal({ title: 'Colonnes affichées', body,
                      footer: '<button class="btn primary" data-close>Fermer</button>' });
    m.$$('[data-col]').forEach((input) => {
      input.onchange = () => {
        input.checked ? this.t.hidden.delete(input.dataset.col) : this.t.hidden.add(input.dataset.col);
        this.renderMain();
      };
    });
  },

  /* --------------------------------------------------------------- actions */
  refresh() {
    const text = this.payload?.request?.text;
    if (!text) return toast('Aucune commande à rejouer.');
    this.close();
    toast('Nouvelle analyse lancée…');
    // On rejoue la commande d'origine : les données viennent toujours du
    // backend, jamais d'un cache local réinterprété.
    window.App?.sendJarvisMessage?.(text, 'text');
  },

  exportCsv() {
    const c = this.payload.comparison;
    if (!c?.ok) return toast('Rien à exporter.');
    const map = c.mapping;
    const head = ['statut', 'nom', 'regle_identite', 'ligne_sheet', 'ligne_site',
                  ...Object.keys(map).map((k) => `sheet_${k}`),
                  ...Object.values(map).map((k) => `site_${k}`), 'champs_modifies', 'action'];
    const esc2 = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [head.join(';')];
    this.filtered().forEach((r) => {
      const e = r.entry;
      lines.push([e.status, e.identity, e.match_rule, e.sheet_row ?? '', e.site_line ?? '',
        ...Object.keys(map).map((k) => (e.sheet_values || {})[k] ?? ''),
        ...Object.values(map).map((k) => (e.site_values || {})[k] ?? ''),
        (e.changed_fields || []).map((f) => f.field).join('|'),
        e.recommended_action].map(esc2).join(';'));
    });
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `comparaison-${Date.now()}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast(`${this.filtered().length} lignes exportées.`);
  },

  /* =============================================== SECTIONS CLASSIQUES ==== */
  head(id, sub) {
    const label = this.payload.sections.find((s) => s.id === id)?.label || id;
    return `<h2>${esc(label)}</h2><div class="aw-sub">${esc(sub)}</div>`;
  },

  section_request() {
    const r = this.payload.request;
    return `${this.head('request', 'La commande exacte qui a déclenché cette analyse.')}
      <div class="aw-card"><p class="aw-narrative">${esc(r.text)}</p>
      <span class="aw-narrative-tag">Intent détecté : ${esc(r.intent)} · vue ouverte : ${esc(this.payload.focus)}</span></div>`;
  },

  section_summary() {
    const s = this.payload.summary;
    return `${this.head('summary', s.headline)}
      ${this.metricsGrid()}
      <div class="aw-card">
        <h3>Synthèse <span class="aw-badge ${esc(s.badge)}">${esc(s.badge)}</span></h3>
        <p class="aw-narrative">${esc(s.narrative || 'Aucune synthèse générée.')}</p>
        <span class="aw-narrative-tag">Texte génératif encadré par le grounding.
          Toutes les métriques, tables et preuves de ce workspace sont calculées par le backend,
          jamais extraites de ce texte.</span>
      </div>
      <div class="aw-card"><h3>Poursuivre</h3><p>
        ${this.payload.actions.map((a) => `<button class="aw-btn" data-goto="${esc(a.target)}">${esc(a.label)}</button>`).join(' ')}
      </p></div>`;
  },

  metricsGrid() {
    return `<div class="aw-metrics">${this.payload.metrics.map((m) => `
      <div class="aw-metric">
        <div class="aw-metric-value">${esc(m.value)}</div>
        <div class="aw-metric-label">${esc(m.label)}</div>
        <div class="aw-metric-detail">${esc(m.detail)}</div>
        <span class="aw-badge ${esc(m.badge)}">${esc(m.badge)}</span>
      </div>`).join('')}</div>`;
  },

  section_metrics() {
    return `${this.head('metrics', 'Chiffres comptés sur les cellules réellement lues.')}${this.metricsGrid()}`;
  },

  section_findings() {
    const items = this.payload.findings.filter((f) => this.matches(f.title, f.detail));
    return `${this.head('findings', 'Chaque point ouvre sa preuve source au clic.')}
      ${this.claimList(items, 'Aucun point ne correspond à la recherche.')}`;
  },

  section_warnings() {
    const items = this.payload.warnings.filter((w) => this.matches(w.title, w.detail));
    return `${this.head('warnings', 'Anomalies structurelles détectées par calcul, pas par inférence.')}
      ${this.claimList(items, 'Aucune anomalie structurelle détectée.')}`;
  },

  section_recommendations() {
    const items = this.payload.recommendations.filter((r) => this.matches(r.title, r.detail));
    return `${this.head('recommendations', 'Déduites des anomalies réelles ci-dessus.')}
      ${this.claimList(items, 'Aucune recommandation.')}`;
  },

  claimList(items, empty) {
    if (!items.length) return `<div class="aw-empty">${esc(empty)}</div>`;
    return items.map((item) => {
      const proof = item.evidence && this.openProof === item.evidence ? this.proofBlock(item.evidence) : '';
      return `<div class="aw-claim ${item.evidence ? '' : 'no-evidence'}"
                   ${item.evidence ? `data-evidence="${esc(item.evidence)}"` : ''}>
        <span class="aw-badge ${esc(item.badge)}">${esc(String(item.badge).replace('_', ' '))}</span>
        <div class="aw-claim-body">
          <div class="aw-claim-title">${esc(item.title)}</div>
          <div class="aw-claim-detail">${esc(item.detail)}</div>
          ${item.evidence ? `<div class="aw-claim-hint">${proof ? 'Masquer la preuve' : 'Afficher la preuve source'}</div>`
                          : '<div class="aw-claim-hint">Constat structurel, sans cellule à citer.</div>'}
          ${proof}
        </div></div>`;
    }).join('');
  },

  proofBlock(evidenceId) {
    const ev = this.payload.evidence.find((e) => e.id === evidenceId);
    if (!ev) return '';
    const table = ev.rows.length ? `
      <div class="aw-table-wrap"><table class="aw-table">
        <thead><tr>${ev.headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${ev.rows.map((row) => `<tr>${row.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody>
      </table></div>` : '';
    return `<div class="aw-proof">
      <div class="aw-proof-head">
        <span>Source <b>${esc(ev.sheet)}</b></span>
        <span>Région <b>${esc(ev.region)}</b></span>
        <span>${esc(ev.location)}</span>
        ${ev.header_row ? `<span>En-tête ligne <b>${esc(ev.header_row)}</b></span>` : ''}
      </div>
      ${ev.computation ? `<div class="aw-proof-calc">${esc(ev.computation)}</div>` : ''}
      ${table}</div>`;
  },

  section_evidence() {
    const items = this.payload.evidence.filter((e) =>
      this.matches(e.sheet, e.region, e.computation, e.headers.join(' ')));
    if (!items.length) return `${this.head('evidence', '')}<div class="aw-empty">Aucune preuve ne correspond.</div>`;
    const shown = items.slice(0, 60);
    return `${this.head('evidence', `${items.length} sources citées par cette analyse.`)}
      ${shown.map((ev) => `<div class="aw-card">
        <h3>${esc(ev.sheet)} · ${esc(ev.region)}
          <span class="aw-badge ${ev.kind === 'computation' ? 'CALCULATED' : 'GROUNDED'}">${ev.kind === 'computation' ? 'CALCULATED' : 'GROUNDED'}</span></h3>
        ${this.proofBlock(ev.id)}</div>`).join('')}
      ${items.length > shown.length ? `<div class="aw-empty">${items.length - shown.length} autres preuves — affinez la recherche.</div>` : ''}`;
  },

  section_tables() {
    let tables = this.payload.tables.filter((t) => this.matches(t.title, t.sheet, t.columns.join(' ')));
    if (this.sheetFilter) tables = tables.filter((t) => t.sheet === this.sheetFilter);
    if (!tables.length) return `${this.head('tables', '')}<div class="aw-empty">Aucune table ne correspond.</div>`;
    return `${this.head('tables',
      `${tables.length} région(s) de données indépendantes. Deux tables d'un même onglet ne sont jamais fusionnées.`)}
      ${tables.map((t) => `<div class="aw-card">
        <h3>${esc(t.sheet)} · ${esc(t.title)} <span class="aw-badge GROUNDED">GROUNDED</span>
          ${t.duplicate_rows ? `<span class="aw-badge CONFLICT">${t.duplicate_rows} doublons</span>` : ''}</h3>
        <p>${esc(t.rows)} lignes · ${t.columns.length} colonnes · ${esc(t.location)}</p>
        <div class="aw-table-wrap" style="margin-top:10px"><table class="aw-table">
          <thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
          <tbody>${t.sample_rows.map((row) => `<tr>${row.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}
          <tr>${t.column_profiles.map((p) => `<td class="aw-generic">${esc(p.kind)} · ${esc(p.filled)} val.${
            p.missing ? ` · ${esc(p.missing)} manquantes` : ''}</td>`).join('')}</tr></tbody>
        </table></div></div>`).join('')}`;
  },

  section_statistics() {
    const stats = this.payload.statistics.filter((s) => this.matches(s.column, s.table, s.sheet));
    const dists = (this.payload.distributions || []).filter((d) => this.matches(d.column, d.table));
    if (!stats.length && !dists.length) {
      return `${this.head('statistics', '')}<div class="aw-empty">
        Aucune colonne numérique exploitable : aucune statistique n'est inventée.</div>`;
    }
    const statCards = stats.map((s) => {
      const v = s.stats;
      const proof = this.openProof === s.evidence ? this.proofBlock(s.evidence) : '';
      return `<div class="aw-card aw-claim" data-evidence="${esc(s.evidence)}" style="display:block;cursor:pointer">
        <h3>${esc(s.column)} <span class="aw-badge CALCULATED">CALCULATED</span></h3>
        <p>${esc(s.sheet)} · ${esc(s.table)} · ${esc(v.count)} valeurs numériques</p>
        <div class="aw-stat-grid">${['min', 'max', 'sum', 'mean'].map((k) =>
          `<div class="aw-stat-cell"><b>${esc(awNum(v[k]))}</b><small>${k}</small></div>`).join('')}</div>
        ${proof}</div>`;
    }).join('');
    const distCards = dists.map((d) => {
      const total = Object.values(d.distribution).reduce((a, b) => a + b, 0) || 1;
      return `<div class="aw-card">
        <h3>${esc(d.column)} <span class="aw-badge CALCULATED">CALCULATED</span></h3>
        <p>${esc(d.sheet)} · ${esc(d.table)} · répartition comptée</p>
        ${Object.entries(d.distribution).map(([k, n]) => `
          <div style="margin-top:8px">
            <div class="aw-stat-row"><span>${esc(k)}</span><span>${esc(n)}</span></div>
            <div class="aw-bar"><i style="width:${Math.round((n / total) * 100)}%"></i></div>
          </div>`).join('')}</div>`;
    }).join('');
    return `${this.head('statistics', 'Uniquement les colonnes réellement numériques d\'un tableau identifié.')}
      ${statCards}${distCards}`;
  },
};

window.AnalysisWorkspace = AnalysisWorkspace;
