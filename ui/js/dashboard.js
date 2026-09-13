/* ==========================================================================
   Dashboard — Command Center : statut système, indicateurs temps réel,
   HUD du Brain Atlas, réseau/uptime + bandeau d'exploitation (agents/tâches).
   Les helpers ($, $$, esc…) viennent de core.js — aucune redéclaration.
   ========================================================================== */
const Dashboard = {
  lastMetrics: null,
  startedAt: Date.now(),
  refreshTimer: null,
  agents: [],          // snapshot /api/agents
  tasks: [],
  live: new Map(),     // état temps réel des agents (events SSE) : id → {status, action}

  /* -------------------------------------------------------------- refresh */
  async refresh() {
    const status = J.state.status || await J.get('/api/status');
    J.state.status = status;
    this.renderHeader(status);
    this.renderFooter(status);
    this.renderBrain(status);
    await this.renderOps();
    window.ActivityPanel?.ensureEmpty();
  },

  refreshSoon(ms = 6000) {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => this.refresh(), ms);
  },

  renderHeader(status) {
    const dot = $('#sysDot');
    const health = status.system?.health || 'ok';
    dot.classList.remove('err', 'warn');
    if (health === 'warning') dot.classList.add('warn');
    if (health === 'critical') dot.classList.add('err');
    $('#sysStatusTextHdr').textContent = status.system?.message || 'ONLINE';
    $('#sysStatusTextHdr').style.color = health === 'warning'
      ? 'var(--warn)' : health === 'critical' ? 'var(--danger)' : 'var(--ok)';
    $('#sysStatusText').textContent = health === 'warning' ? 'DÉGRADÉ' : health === 'critical' ? 'CRITIQUE' : 'OPTIMAL';
    $('#sysStatusText').style.color = health === 'warning' && $('#sysStatusText').style.color !== 'tomato'
      ? 'var(--warn)' : 'inherit';
    const env = status.environment || {};
    $('#operatorName').textContent = status.general?.user_name || 'Operator';
    $('#operatorTitle').textContent = status.general?.operator_title || 'Commander';
    $('#footLocation').textContent = status.location || (env.platform || '—');
    $('#qualityChip').textContent = (J.state.settings?.appearance?.quality || 'balanced').toUpperCase();
  },

  renderFooter(status) {
    const net = status.network || {};
    const uptime = status.uptime_seconds ?? status.system?.uptime_seconds ?? null;
    $('#footNetwork').textContent = net.mode || '—';
    const t = $('#footUptime');
    t.textContent = uptime ? this._fmtUptime(uptime) : (net.uptime_s ? this._fmtUptime(net.uptime_s) : '—');
    if (status.uptime_updated) this.startedAt = Date.now() - (status.uptime_updated * 1000 - Date.now());
  },

  /* -------------------------------------------------------- OPS RAIL (live) */
  async renderOps() {
    const tile = (id, val) => { const el = $(id); if (el) el.textContent = val; };
    try {
      const [ag, tk] = await Promise.all([
        J.get('/api/agents').catch(() => ({ agents: [] })),
        J.get('/api/tasks?status=active&limit=30').catch(() => ({ tasks: [], stats: {} })),
      ]);
      this.agents = (ag.agents || []).filter((a) => a.id !== 'jarvis');
      this.tasks = tk.tasks || [];

      const enabled = this.agents.filter((a) => a.enabled);
      const active = this.agents.filter((a) => a.status === 'active')
        .concat([...this.live.values()].filter((l) => l.status === 'active'));

      tile('#opsAgents', enabled.length || '—');
      tile('#opsAgentsActive', active.length);
      tile('#opsTodos', this.tasks.filter((t) => ['queued', 'running', 'planning'].includes(t.status)).length);
      tile('#opsTasksActive', this.tasks.filter((t) => ['waiting_confirmation', 'running', 'queued', 'planning'].includes(t.status)).length);

      const activeEl = $('#opsAgentsActive');
      if (activeEl) activeEl.classList.toggle('pulse', active.length > 0);

      this.renderOpsLive();
    } catch { /* panneau optionnel */ }
  },

  /* Liste temps réel : agents convoqués par JARVIS + premières tâches actives. */
  renderOpsLive() {
    const host = $('#opsLive');
    if (!host) return;
    const entries = [];

    for (const a of this.agents) {
      const live = this.live.get(a.id) || {};
      const status = a.status === 'active' ? 'active' : live.status || 'idle';
      if (status === 'active' || a.status === 'failed') {
        entries.push({
          id: a.id, cls: status, // active / failed / idle
          name: a.name, detail: (live.action || a.current_action || a.role || 'en attente').slice(0, 60),
        });
      }
    }
    // To-dos = tâches en cours / planifiées (les "que j'ai à faire").
    for (const t of this.tasks) {
      if (['running', 'queued', 'planning', 'waiting_confirmation'].includes(t.status)) {
        entries.push({
          id: t.id, cls: t.status === 'waiting_confirmation' ? 'failed' : 'active',
          name: t.agent ? '↳ ' + t.agent : 'tâche', detail: (t.name || '').slice(0, 52) + (t.status === 'waiting_confirmation' ? ' ⚠' : ''),
        });
      }
    }

    if (!entries.length) {
      host.innerHTML = '';
      return;
    }
    const frag = document.createDocumentFragment();
    for (const en of entries.slice(0, 12)) {
      const chip = document.createElement('span');
      chip.className = 'agent-chip ' + en.cls;
      chip.innerHTML = `<i class="a-dot"></i><b>${esc(en.name)}</b><span>${esc(en.detail)}</span>`;
      frag.appendChild(chip);
    }
    host.innerHTML = '';
    host.appendChild(frag);
  },

  /* Met à jour l'état temps réel depuis les events SSE (agent.*). */
  agentEvent(type, data) {
    data = data || {};
    if (!data.id) return;
    this.live.set(data.id, {
      status: type === 'agent.started' ? 'active'
        : type === 'agent.failed' ? 'failed'
        : type === 'agent.completed' || type === 'agent.idle' ? 'idle' : this.live.get(data.id)?.status || 'idle',
      action: data.action || data.task_id || '',
    });
    // L'event est le moment le plus frais : on rafraîchit la vignette en direct.
    this.renderOpsLive();

    const activeEl = $('#opsAgentsActive');
    const running = [...this.live.values()].filter((l) => l.status === 'active').length;
    if (activeEl) {
      activeEl.textContent = running || this.agents.filter((a) => a.status === 'active').length || 0;
      activeEl.classList.toggle('pulse', running > 0);
    }
  },

  /* --------------------------------------------------------- Brain HUD */
  async renderBrain(status) {
    const hudN = $('#brainNodeCount');
    const hudE = $('#brainEdgeCount');
    const memN = $('#memNodeCount');
    if (!hudN && !hudE && !memN) return;
    try {
      const st = await fetch('/api/brain/stats', { signal: AbortSignal.timeout(3000) }).then((r) => r.json());
      if (hudN) hudN.textContent = st.nodes ?? '—';
      if (hudE) hudE.textContent = st.edges ?? '—';
      // Carte-résumé mémoire du Command Center (données réelles /api/brain/stats).
      if (memN) memN.textContent = st.brains ?? st.nodes ?? '—';
      const memE = $('#memEdgeCount');
      if (memE) memE.textContent = st.edges ?? '—';
      const memK = $('#memKnowledge');
      if (memK) memK.textContent = st.knowledge ?? '—';
      const families = Object.entries(st.families || {})
        .map(([name, count]) => ({ name, count: Number(count) || 0 }))
        .sort((a, b) => b.count - a.count);
      const legend = $('#brainLegend');
      if (legend && !legend.children.length && families.length) {
        families.slice(0, 12).forEach((f) => legend.appendChild(this._familyItem(f)));
      }
      const memFam = $('#memFamilies');
      if (memFam && !memFam.querySelector('.mem-fam') && families.length) {
        memFam.innerHTML = '';
        families.slice(0, 8).forEach((f) => memFam.appendChild(this._memFamilyChip(f)));
      }
    } catch { /* le HUD 3D fournit déjà ces compteurs */ }
  },

  _familyItem(f) {
    const d = document.createElement('span');
    d.className = 'legend-item';
    d.innerHTML = `<i class="legend-dot"></i>${esc(f.name)} (${f.count})`;
    d.style.setProperty('--ld', BrainDotColor(f.name));
    return d;
  },

  _memFamilyChip(f) {
    const d = document.createElement('span');
    d.className = 'mem-fam';
    d.innerHTML = `<i class="legend-dot"></i>${esc(f.name)}<b>${f.count}</b>`;
    d.style.setProperty('--ld', BrainDotColor(f.name));
    return d;
  },

  renderMonitor() { /* conservé pour compatibilité */ },
  renderQuick() { /* conservé pour compatibilité */ },

  _fmtUptime(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return d > 0 ? `${d}j ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  },
};

function BrainDotColor(family) {
  const map = {
    MEMORY: '#22d3ee', KNOWLEDGE: '#4ade80', PROJECTS: '#60a5fa', PEOPLE: '#f472b6',
    SERVERS: '#94a3b8', TOOLS: '#fbbf24', WORKFLOWS: '#a78bfa', APPLICATIONS: '#34d399',
    AUTOMATIONS: '#fb923c', DECISIONS: '#fbbf24', ERRORS: '#fb7185',
    SOLUTIONS: '#4ade80', DOCUMENTS: '#818cf8', JARVIS: '#22d3ee',
  };
  return map[String(family || '').toUpperCase()] || '#22d3ee';
}

window.Dashboard = Dashboard;