/* ==========================================================================
   COCKPIT — Accueil Command Center "AI Command Center pour entrepreneur".
   Toutes les valeurs affichées proviennent des vraies API JARVIS.
   Jamais de chiffre inventé : si absent → "—" ou widget masqué.
   ========================================================================== */

const FAMILY_LABELS = {
  PROJECTS: 'Projets', PROJECT: 'Projets',
  DOCUMENTS: 'Documents', DOCUMENT: 'Documents',
  CONVERSATIONS: 'Conversations', CONVERSATION: 'Conversations',
  KNOWLEDGE: 'Connaissances', KNOWLEDGE_BASE: 'Connaissances',
  CONTACTS: 'Contacts', PEOPLE: 'Contacts',
  AUTOMATIONS: 'Automatisations', AUTOMATION: 'Automatisations', WORKFLOWS: 'Workflows',
  CODE: 'Code', FILES: 'Fichiers',
  MEMORIES: 'Mémoires', MEMORY: 'Mémoires',
  ANALYSES: 'Analyses', ANALYSIS: 'Analyses', DATA: 'Analyses',
  TASKS: 'Tâches', CALENDAR: 'Calendrier',
  WEB: 'Ressources Web', RESOURCES: 'Ressources Web',
  SERVERS: 'Serveurs', SERVER: 'Serveurs',
  AGENTS: 'Agents', TOOLS: 'Outils', PREFERENCES: 'Préférences',
};

const FAMILY_DOTS = {
  PROJECTS: '#12CFFF', DOCUMENTS: '#208DFF', CONVERSATIONS: '#8B5CFF',
  KNOWLEDGE: '#22D99A', CONTACTS: '#FF5C70', AUTOMATIONS: '#FFAA3C',
  CODE: '#3CB2FF', MEMORIES: '#B18CFF', ANALYSES: '#4FD6FF',
  TASKS: '#7C9FFF', WEB: '#5EEAD4', SERVERS: '#9AA7B8',
  AGENTS: '#22D99A', TOOLS: '#FFB454', PREFERENCES: '#C4B5FD',
};

function familyDot(name) {
  return FAMILY_DOTS[name.toUpperCase()] || FAMILY_DOTS.PROJECTS || '#12CFFF';
}

async function jget(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(4000) });
    return r.ok ? await r.json() : { ok: false };
  } catch { return { ok: false }; }
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function fmtCount(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return new Intl.NumberFormat('fr-FR').format(n);
}

const AppHome = {
  QUICK_PROMPTS: {
    'analyse-ventes': 'Analyse mes ventes et dégage les tendances essentielles.',
    'post-discord': 'Prépare un post pour notre communauté Discord.',
    'optim-serveur': 'Optimise mon serveur (CPU, RAM, stockage, services).',
    'planning': 'Prépare mon planning de la semaine.',
    'recherche-web': 'Recherche sur le web les dernières tendances utiles à mon activité.',
    'creer-workflow': 'Crée un workflow pour automatiser une de mes tâches récurrentes.',
  },

  _cache: {},

  async render() {
    this.renderGreeting();
    this.renderBrainCounts();
    this.renderProjects();
    this.renderWidgets();
    this.renderAgenda();
    this.renderAgentsMini();
    this.renderConnectors();
    this.renderShortcuts();
  },

  /* ------------------------------------------------------- salutation */
  async renderGreeting() {
    const op = $('#homeOperator');
    if (op) op.textContent = '—';
    const st = await jget('/api/status');
    if (st.ok !== false && st.user_name) {
      const name = String(st.user_name).trim();
      if (name && op) op.textContent = name.split(/\s/)[0];
      const title = $('#operatorTitle');
      if (title && st.operator_title) title.textContent = st.operator_title;
    }
    const date = $('#homeDate');
    if (date) date.textContent = new Date().toLocaleDateString('fr-FR', {
      weekday: 'long', day: 'numeric', month: 'long',
    });
    const sys = $('#homeSystem');
    if (sys) {
      const up = st && st.uptime_s ? st.uptime_s : null;
      sys.textContent = up ? 'En ligne · ' + this._uptime(up) : '—';
    }
  },

  _uptime(sec) {
    sec = Math.max(0, Number(sec) || 0);
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return d > 0 ? `${d}j ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  },

  /* --------------------------------------------- brain : contenu visible */
  async renderBrainCounts() {
    const host = $('#brainCounts');
    if (!host) return;
    host.innerHTML = '<span class="bc-empty">Mémoire en préparation…</span>';
    const st = await jget('/api/brain/stats');
    if (st.ok === false) return;
    const chips = [];
    const fam = st.families || {};
    const entries = Object.entries(fam).map(([k, v]) => ({ k, v: Number(v) || 0 }))
      .sort((a, b) => b.v - a.v);
    for (const e of entries.slice(0, 12)) {
      chips.push(`<span class="bc-chip" title="${esc(e.k)}">
        <i style="background:${familyDot(e.k)}"></i><b>${esc(FAMILY_LABELS[e.k.toUpperCase()] || e.k)}</b>
        <em>${fmtCount(e.v)}</em></span>`);
    }
    const extra = [
      st.brains != null ? ['Savoirs', st.brains] : null,
      st.knowledge != null ? ['Connaissances', st.knowledge] : null,
      st.tools != null ? ['Outils', st.tools] : null,
      st.connectors != null ? ['Connecteurs', st.connectors] : null,
    ].filter(Boolean);
    for (const [l, v] of extra) {
      chips.push(`<span class="bc-chip bc-plain"><b>${esc(l)}</b><em>${fmtCount(v)}</em></span>`);
    }
    host.innerHTML = chips.join('') || '<span class="bc-empty">Mémoire en préparation…</span>';
  },

  /* ------------------------------------------------------ projets actifs */
  async renderProjects() {
    const grid = $('#projectsGrid');
    if (!grid) return;
    grid.innerHTML = '';
    const brain = await jget('/api/brain');
    if (brain.ok === false) return;
    const nodes = brain.nodes || [];
    const edges = brain.edges || [];
    const projects = nodes
      .filter((n) => String(n.family || '').toUpperCase() === 'PROJECTS')
      .slice(0, 6);
    if (!projects.length) {
      grid.innerHTML = `<div class="projects-empty">Aucun projet actif en mémoire.
        <button class="btn sm primary" data-quick="creer-workflow">Demander à JARVIS</button></div>`;
      grid.querySelector('[data-quick]').onclick = () => App.handleQuick('creer-workflow');
      return;
    }
    const frag = document.createDocumentFragment();
    for (const p of projects) {
      const links = edges.filter((e) => e.a === p.id || e.b === p.id).length;
      const card = document.createElement('article');
      card.className = 'project-card';
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', `Ouvrir ${p.label || p.id}`);
      const title = esc(p.label || p.id);
      const snippet = (p.meta?.summary || p.summary || p.kind || 'Projet connecté').slice(0, 64);
      card.innerHTML = `<i class="p-dot" style="background:${familyDot(p.family)}"></i>
        <div class="p-body">
          <b>${title}</b>
          <span>${esc(snippet)}</span>
          <em>${links} ${links > 1 ? 'liens' : 'lien'} dans le Brain</em>
        </div>`;
      card.onclick = () => App.showNodeDetails(p);
      card.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); App.showNodeDetails(p); } };
      frag.appendChild(card);
    }
    grid.appendChild(frag);
  },

  /* -------------------------------------------------------- widgets */
  async renderWidgets() {
    const host = $('#widgetsGrid');
    if (!host) return;
    const [tasks, conn, wf, mem, conv, st] = await Promise.all([
      jget('/api/tasks'), jget('/api/connectors'), jget('/api/workflows'),
      jget('/api/memory?limit=1&scope=global'), jget('/api/conversations?limit=1'),
      jget('/api/status'),
    ]);
    const widgets = [];

    const activeTasks = (tasks.tasks || []).filter((t) =>
      ['running', 'queued', 'planning', 'waiting_confirmation'].includes(t.status)).length;
    widgets.push({ k: 'TÂCHES ACTIVES', v: activeTasks || '0',
      sub: (tasks.stats && tasks.stats.completed != null)
        ? `${fmtCount(tasks.stats.completed)} terminées` : 'Tâches réelles', tone: '' });

    const conns = conn.connectors || [];
    const connOk = conns.filter((c) => c.enabled || c.status === 'connected').length;
    widgets.push({ k: 'CONNECTEURS', v: conns.length ? String(connOk) : '—',
      sub: conns.length ? `${conns.length} configuré${conns.length > 1 ? 's' : ''}` : 'Aucun configuré', tone: 'muted' });

    const flows = (wf.workflows || []);
    const flowsOn = flows.filter((x) => x.enabled).length;
    widgets.push({ k: 'AUTOMATISATIONS', v: flows.length ? String(flowsOn) : '—',
      sub: flows.length ? `sur ${flows.length} workflow${flows.length > 1 ? 's' : ''}` : 'Aucun workflow', tone: '' });

    let memCount = mem.stats?.total ?? mem.stats?.memories ?? mem.memories?.length;
    widgets.push({ k: 'MÉMOIRES', v: memCount != null ? fmtCount(memCount) : '—',
      sub: 'Souvenirs en base', tone: '' });

    const convs = conv.conversations || [];
    widgets.push({ k: 'CONVERSATIONS', v: convs.length ? fmtCount(convs.length) : '—',
      sub: 'Historique de dialogue', tone: '' });

    if (st.ok !== false && st.uptime_s != null) {
      widgets.push({ k: 'EN LIGNE DEPUIS', v: this._uptime(st.uptime_s), sub: `v${esc(st.version || '')} · JARVIS`, tone: 'ok' });
    }

    if (!widgets.length) {
      host.innerHTML = '<div class="w-empty">Aucune donnée système disponible pour l’instant.</div>';
      return;
    }
    host.innerHTML = widgets.map((w) => `
      <div class="widget ${w.tone ? 'tone-' + w.tone : ''}">
        <span class="w-k">${esc(w.k)}</span>
        <b>${esc(w.v)}</b>
        <small>${esc(w.sub)}</small>
      </div>`).join('');
    const note = $('#widgetNote');
    if (note) note.textContent = 'Données réelles · mises à jour en continu';
  },

  /* ------------------------------------------------------------ agenda */
  async renderAgenda() {
    const list = $('#agendaList');
    if (!list) return;
    list.innerHTML = '<div class="agenda-empty">Aucun rendez-vous aujourd’hui.</div>';
    const cal = await jget('/api/calendar');
    if (cal.ok === false) return;
    const events = cal.events || [];
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const todayEnd = todayStart + 86400000;
    const todays = events
      .map((e) => ({ e, t: new Date(e.start_at || e.start).getTime() }))
      .filter((x) => !Number.isNaN(x.t) && x.t >= todayStart && x.t < todayEnd)
      .sort((a, b) => a.t - b.t);
    if (!todays.length) return;
    list.innerHTML = '';
    const frag = document.createDocumentFragment();
    for (const { e, t } of todays.slice(0, 6)) {
      const row = document.createElement('button');
      row.className = 'agenda-item';
      row.type = 'button';
      row.onclick = () => App.goto('calendar');
      row.innerHTML = `<time>${fmtTime(e.start_at || e.start)}</time>
        <div><b>${esc(e.title || e.name || 'Événement')}</b>
        ${e.end_at ? `<small>→ ${fmtTime(e.end_at)}</small>` : ''}</div>`;
      frag.appendChild(row);
    }
    list.appendChild(frag);
  },

  /* ---------------------------------------------- agents (état réel) */
  statusLabel(s) {
    switch (s) {
      case 'active': return 'Actif';
      case 'done': return 'Terminé';
      case 'error': return 'Erreur';
      case 'standby':
      default: return 'Disponible';
    }
  },

  async renderAgentsMini() {
    const host = $('#agentsMini');
    if (!host) return;
    const ag = await jget('/api/agents');
    const agents = (ag.agents || []).filter((a) => a.id !== 'jarvis');
    host.innerHTML = '';
    if (!agents.length) return;
    const frag = document.createDocumentFragment();
    for (const a of agents.slice(0, 8)) {
      const row = document.createElement('button');
      row.className = 'agent-mini';
      row.type = 'button';
      const state = a.status === 'active' ? 'on' : 'off';
      row.innerHTML = `<i class="adot ${state}"></i>
        <div><b>${esc(a.name)}</b><span>${esc(this.statusLabel(a.status))}</span></div>`;
      row.onclick = () => App.goto('agents');
      frag.appendChild(row);
    }
    host.appendChild(frag);
  },

  /* -------------------------------------------------------- connecteurs */
  connState(c) {
    const s = String(c.status || '').toLowerCase();
    if (s === 'connected') return 'ok';
    if (s === 'error') return 'err';
    if (s === 'needs_auth') return 'warn';
    if (c.enabled) return 'off';
    return 'off';
  },
  connLabel(c) {
    const s = String(c.status || '').toLowerCase();
    if (s === 'connected') return 'Connecté';
    if (s === 'error') return 'Erreur';
    if (s === 'needs_auth') return 'Auth requise';
    if (c.enabled) return 'Configuré';
    return 'Non connecté';
  },

  async renderConnectors() {
    const host = $('#connectorsMini');
    if (!host) return;
    host.innerHTML = '<span class="conn-empty">Aucun connecteur configuré.</span>';
    const res = await jget('/api/connectors');
    const conns = (res.connectors || []);
    if (!conns.length) return;
    host.innerHTML = '';
    const frag = document.createDocumentFragment();
    for (const c of conns.slice(0, 10)) {
      const chip = document.createElement('button');
      chip.className = 'conn-chip ' + this.connState(c);
      chip.type = 'button';
      chip.title = `${c.type || ''} · ${c.status || ''}`;
      chip.innerHTML = `<i></i><b>${esc(c.name || c.type || '—')}</b><span>${esc(this.connLabel(c))}</span>`;
      chip.onclick = () => App.goto('settings', { section: 'connectors' });
      frag.appendChild(chip);
    }
    host.appendChild(frag);
  },

  /* ------------------------------------------------------- raccourcis */
  async renderShortcuts() {
    const host = $('#navShortcuts');
    if (!host) return;
    const frag = document.createDocumentFragment();
    const [conn, brain] = await Promise.all([jget('/api/connectors'), jget('/api/brain')]);
    const conns = (conn.connectors || []).slice(0, 6);
    const projects = (brain.nodes || [])
      .filter((n) => String(n.family || '').toUpperCase() === 'PROJECTS').slice(0, 4);

    for (const c of conns) {
      const btn = document.createElement('button');
      btn.className = 'shortcut';
      btn.type = 'button';
      btn.innerHTML = `<i class="s-ico ${this.connState(c)}"></i><span>${esc(c.name || c.type || '—')}</span>`;
      btn.title = `Connecteur ${c.type || ''} — configurer`;
      btn.onclick = () => App.goto('settings', { section: 'connectors' });
      frag.appendChild(btn);
    }
    for (const p of projects) {
      const btn = document.createElement('button');
      btn.className = 'shortcut project';
      btn.type = 'button';
      btn.innerHTML = `<i class="s-dot" style="background:${familyDot(p.family)}"></i><span>${esc(p.label || p.id)}</span>`;
      btn.title = 'Ouvrir le projet dans le Brain';
      btn.onclick = () => App.showNodeDetails(p);
      frag.appendChild(btn);
    }
    if (!frag.childElementCount) {
      const hint = document.createElement('button');
      hint.className = 'shortcut add';
      hint.type = 'button';
      hint.innerHTML = `<i class="s-add">+</i><span>Configurer</span>`;
      hint.onclick = () => App.goto('settings', { section: 'connectors' });
      frag.appendChild(hint);
    } else {
      const more = document.createElement('button');
      more.className = 'shortcut add';
      more.type = 'button';
      more.innerHTML = `<i class="s-add">+</i><span>Ajouter</span>`;
      more.onclick = () => App.goto('settings', { section: 'connectors' });
      frag.appendChild(more);
    }
    host.innerHTML = '';
    host.appendChild(frag);
  },

  /* --------------------------------------------------------- lanceur */
  renderLauncher() {
    const pop = $('#launcherPop');
    if (!pop || !pop.hidden) return;
    const groups = (App && App.NAV || []).map((g) => `
      <div class="launch-group">
        <div class="nav-group-label">${esc(g.label)}</div>
        ${g.items.map(([id, label, ic]) =>
          `<button class="launch-item" data-launch="${id}">${icon(ic, 14)}<span>${esc(label)}</span></button>`).join('')}
      </div>`).join('');
    const quick = `<div class="launch-group">
      <div class="nav-group-label">ACTIONS</div>
      <button class="launch-item" data-launch="focus-chat">${icon('chat', 14)}<span>Discuter avec JARVIS</span></button>
      <button class="launch-item" data-launch="settings-connectors">${icon('link', 14)}<span>Configurer les connecteurs</span></button>
    </div>`;
    pop.innerHTML = groups + quick;
  },
};

/* =========================================================== PAGES ===== */

Pages.projects = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = '<div class="page-head"><div class="zone-kicker">PROJETS</div><h2 class="page-title">Projets en mémoire</h2></div>';
  const brain = await jget('/api/brain');
  const nodes = (brain.nodes || []);
  const edges = (brain.edges || []);
  const projects = nodes.filter((n) => String(n.family || '').toUpperCase() === 'PROJECTS');
  if (!projects.length) {
    zone.innerHTML += '<p class="muted">Aucun projet dans le Brain Atlas pour le moment. Demandez à JARVIS d’en créer un, ou ajoutez une mémoire de projet.</p>';
    return;
  }
  let html = '<div class="projects-full">';
  for (const p of projects) {
    const links = edges.filter((e) => e.a === p.id || e.b === p.id);
    const sub = links.slice(0, 10).map((e) => {
      const other = nodes.find((n) => n.id === (e.a === p.id ? e.b : e.a));
      return other ? `<span class="sub-item" style="--dc:${familyDot(other.family)}"><i></i>${esc(other.label || other.id)}</span>` : '';
    }).join('');
    html += `<article class="project-full" data-node="${esc(p.id)}">
      <i class="p-dot big" style="background:${familyDot(p.family)}"></i>
      <div>
        <b>${esc(p.label || p.id)}</b>
        <p>${esc((p.meta?.summary || p.summary || p.kind || '').toString())}</p>
        <em>${links.length} ${links.length > 1 ? 'liens' : 'lien'} · ${esc(p.family || '')}</em>
      </div>
      <div class="subs">${sub || '<span class="muted">Aucun sous-élément</span>'}</div>
    </article>`;
  }
  html += '</div>';
  zone.innerHTML += html;
  zone.querySelectorAll('.project-full[data-node]').forEach((card) => {
    card.addEventListener('click', () => {
      const n = nodes.find((x) => x.id === card.dataset.node);
      if (n) App.showNodeDetails(n);
    });
  });
};

Pages.analyses = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = '<div class="page-head"><div class="zone-kicker">ANALYSES</div><h2 class="page-title">Observatoire JARVIS</h2></div>';
  const [st, tasks, mem, wf, met] = await Promise.all([
    jget('/api/brain/stats'), jget('/api/tasks'), jget('/api/memory?limit=1&scope=global'),
    jget('/api/workflows'), jget('/api/metrics'),
  ]);
  const cards = [];
  if (st.ok !== false) {
    const fam = Object.entries(st.families || {}).map(([k, v]) => ({ k, v: Number(v) || 0 }))
      .sort((a, b) => b.v - a.v);
    cards.push(`<div class="ana-card"><span>BRAIN</span>
      <b>${fmtCount(st.nodes)} nœuds · ${fmtCount(st.edges)} liens</b>
      <div class="ana-chips">${fam.slice(0, 14).map((f) =>
        `<span class="bc-chip"><i style="background:${familyDot(f.k)}"></i><b>${esc(FAMILY_LABELS[f.k.toUpperCase()] || f.k)}</b><em>${fmtCount(f.v)}</em></span>`).join('')}</div></div>`);
  }
  const active = (tasks.tasks || []).filter((t) =>
    ['running', 'queued', 'planning', 'waiting_confirmation'].includes(t.status)).length;
  cards.push(`<div class="ana-card"><span>TÂCHES</span>
    <b>${active} active${active > 1 ? 's' : ''}</b>
    <div class="ana-chips">${(tasks.stats && tasks.stats.completed != null) ? `<span class="bc-chip"><b>Terminées</b><em>${fmtCount(tasks.stats.completed)}</em></span>` : ''}
    ${(tasks.stats && tasks.stats.total != null) ? `<span class="bc-chip"><b>Total</b><em>${fmtCount(tasks.stats.total)}</em></span>` : ''}</div></div>`);
  const flowsOn = (wf.workflows || []).filter((x) => x.enabled).length;
  cards.push(`<div class="ana-card"><span>AUTOMATISATIONS</span>
    <b>${(wf.workflows || []).length} workflows · ${flowsOn} actif${flowsOn > 1 ? 's' : ''}</b>
    <div class="ana-chips muted">Déclenchés par JARVIS ou sur événement.</div></div>`);
  if (met.ok !== false && met.metrics) {
    const m = met.metrics;
    const pct = (x) => `${Math.round(Number(x) || 0)}%`;
    cards.push(`<div class="ana-card"><span>SYSTÈME</span>
      <b>CPU ${m.cpu?.percent != null ? pct(m.cpu.percent) : '—'} · RAM ${m.memory?.percent != null ? pct(m.memory.percent) : '—'}</b>
      <div class="ana-chips">${m.disk?.percent != null ? `<span class="bc-chip"><b>Stockage</b><em>${pct(m.disk.percent)}</em></span>` : ''}
      ${m.cpu?.cores ? `<span class="bc-chip"><b>Fermes</b><em>${m.cpu.cores}</em></span>` : ''}</div></div>`);
  }
  if (!cards.length) cards.push('<div class="muted">Aucune donnée d’analyse disponible.</div>');
  zone.innerHTML += `<div class="ana-grid">${cards.join('')}</div>
    <p class="muted">Besoin d’une analyse ciblée&nbsp;? <button class="btn sm primary" data-quick="analyse-ventes">Demander une analyse à JARVIS</button></p>`;
  const b = zone.querySelector('[data-quick]');
  if (b) b.onclick = () => App.handleQuick('analyse-ventes');
};

function moduleEmptyHtml(title, kicker, hint, action, actionLabel) {
  return `<div class="zone-head"><div class="zone-title"><span class="zone-kicker">${esc(kicker)}</span>
      <h2 class="page-title">${esc(title)}</h2></div></div>
    <div class="module-empty">
      <div class="me-icon">${icon('link', 22)}</div>
      <p class="muted">${esc(hint)}</p>
      <p class="muted small">Aucune donnée inventée ici&nbsp;: ce module n’affiche que des informations réelles lorsqu’un connecteur est configuré.</p>
      <button class="btn primary" data-me-action="connectors">${esc(actionLabel || 'Configurer les connecteurs')}</button>
    </div>`;
}

function bindModuleAction(el) {
  const b = el.querySelector('[data-me-action]');
  if (b) b.onclick = () => App.goto('settings', { section: 'connectors' });
}

Pages.finance = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = moduleEmptyHtml('Finance', 'FINANCE', 'Aucun connecteur financier configuré (banque, paiement, compta). Une fois branché, JARVIS affichera ici vos données réelles.', 'none', 'Configurer un connecteur');
  bindModuleAction(zone);
};

Pages.marketing = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = moduleEmptyHtml('Marketing', 'MARKETING', 'Aucun canal marketing configuré. Reliez un connecteur (mails, publicité, analytics) pour que JARVIS pilote vos campagnes depuis ici.', 'none', 'Configurer un connecteur');
  bindModuleAction(zone);
};

Pages.social = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = moduleEmptyHtml('Réseaux sociaux', 'SOCIAL', 'Aucun compte relié (Discord, X, Instagram…). Dès qu’un connecteur social est actif, vos communautés apparaîtront ici.', 'none', 'Configurer un connecteur');
  bindModuleAction(zone);
};

Pages.servers = async function (el) {
  const zone = el.querySelector('.page-zone') || el;
  zone.innerHTML = '<div class="page-head"><div class="zone-kicker">SITES & SERVEURS</div><h2 class="page-title">Infrastructure</h2></div>';
  const [met, conn, st] = await Promise.all([jget('/api/metrics'), jget('/api/connectors'), jget('/api/status')]);
  const m = (met.ok !== false && met.metrics) ? met.metrics : null;
  const pct = (x) => `${Math.round(Number(x) || 0)}%`;
  let html = '<div class="ana-grid">';
  html += `<div class="ana-card"><span>HÔTE LOCAL</span>
    <b>${m ? `CPU ${pct(m.cpu?.percent)} · RAM ${pct(m.memory?.percent)} · Stockage ${pct(m.disk?.percent)}` : '—'}</b>
    <div class="ana-chips">${m && m.cpu ? `<span class="bc-chip"><b>Fermes</b><em>${m.cpu.cores ?? '—'}</em></span>` : ''}
    ${st && st.uptime_s != null ? `<span class="bc-chip"><b>Uptime</b><em>${AppHome._uptime(st.uptime_s)}</em></span>` : ''}</div></div>`;
  const serverConns = (conn.connectors || []).filter((c) =>
    /ssh|sftp|docker|vps|server|n8n/i.test(c.type || ''));
  if (serverConns.length) {
    html += `<div class="ana-card"><span>SERVEURS RELIÉS</span><b>${serverConns.length} connecteur${serverConns.length > 1 ? 's' : ''}</b>
      <div class="ana-chips">${serverConns.map((c) =>
        `<span class="bc-chip"><b>${esc(c.name || c.type)}</b><em>${esc(AppHome.connLabel(c))}</em></span>`).join('')}</div></div>`;
  } else {
    html += `<div class="ana-card"><span>SERVEURS RELIÉS</span><b>Aucun pour l’instant</b>
      <div class="ana-chips muted">Ajoutez un connecteur SSH / VPS / Docker pour surveiller vos machines.</div></div>`;
  }
  html += '</div>';
  html += `<p class="muted">Vérifier l’état d’un serveur&nbsp;? <button class="btn sm primary" data-quick="optim-serveur">Demander un contrôle à JARVIS</button></p>`;
  zone.innerHTML += html;
  const b = zone.querySelector('[data-quick]');
  if (b) b.onclick = () => App.handleQuick('optim-serveur');
};