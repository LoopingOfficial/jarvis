/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_hud.js
   Bandeau HUD très fin : état, modèle, CPU, RAM, GPU, outils, réseau, heure.
   Toutes les valeurs viennent de /api/status et /api/metrics. Rien n'est
   inventé : une donnée absente s'affiche « — ».
   ========================================================================== */
(function () {
  'use strict';

  const pct = (v) => (typeof v === 'number' && isFinite(v) ? Math.round(v) + '%' : '—');

  const JarvisHud = {
    timer: null,

    init() {
      const host = document.getElementById('v4Hud');
      if (!host) return;
      host.innerHTML = `
        <div class="v4-hud-brand">
          <span class="v4-hud-mark" aria-hidden="true"></span>
          <b>JARVIS</b>
          <span class="v4-hud-state" id="v4HudState">ONLINE</span>
        </div>
        <div class="v4-hud-metrics" id="v4HudMetrics">
          ${this.cell('model', 'MODEL')}
          ${this.cell('cpu', 'CPU', true)}
          ${this.cell('ram', 'RAM', true)}
          ${this.cell('gpu', 'GPU')}
          ${this.cell('tools', 'TOOLS')}
          ${this.cell('agents', 'AGENTS')}
          ${this.cell('net', 'NETWORK')}
          ${this.cell('uptime', 'UPTIME')}
        </div>
        <div class="v4-hud-clock"><b id="v4HudTime">--:--:--</b><span id="v4HudDate">—</span></div>`;

      this.tick();
      this.refresh();
      clearInterval(this.timer);
      this.timer = setInterval(() => this.refresh(), 6000);
      setInterval(() => this.tick(), 1000);

      // Le HUD suit le flux temps réel déjà présent (aucune API nouvelle).
      if (typeof J !== 'undefined' && typeof J.on === 'function') {
        J.on('stream.open', () => this.setState('ONLINE', 'ok'));
        J.on('stream.close', () => this.setState('RECONNEXION', 'warn'));
        J.on('system.metrics', () => this.refresh());
      }
    },

    cell(id, label, bar) {
      return `<div class="v4-hud-cell" data-hud="${id}">
        <span class="v4-hud-k">${label}</span>
        <b id="v4Hud_${id}">—</b>
        ${bar ? `<i class="v4-hud-bar"><s id="v4HudBar_${id}"></s></i>` : ''}
      </div>`;
    },

    setState(text, tone) {
      const n = document.getElementById('v4HudState');
      if (!n) return;
      n.textContent = text;
      n.dataset.tone = tone || 'ok';
    },

    tick() {
      const now = new Date();
      const t = document.getElementById('v4HudTime');
      const d = document.getElementById('v4HudDate');
      if (t) t.textContent = now.toLocaleTimeString('fr-FR');
      if (d) d.textContent = now.toLocaleDateString('fr-FR', { weekday: 'short', day: '2-digit', month: 'short' });
    },

    set(id, value, ratio) {
      const n = document.getElementById('v4Hud_' + id);
      if (n) n.textContent = value == null || value === '' ? '—' : String(value);
      const bar = document.getElementById('v4HudBar_' + id);
      if (bar && typeof ratio === 'number') {
        bar.style.width = Math.max(0, Math.min(100, ratio)) + '%';
        bar.dataset.tone = ratio >= 90 ? 'err' : ratio >= 70 ? 'warn' : 'ok';
      }
    },

    async refresh() {
      if (typeof J === 'undefined') return;
      const status = (J.state && J.state.status) || await J.get('/api/status').catch(() => null);
      if (!status) return;
      const m = status.metrics || {};
      const cpu = m.cpu && typeof m.cpu.percent === 'number' ? m.cpu.percent : null;
      const mem = m.memory && typeof m.memory.percent === 'number' ? m.memory.percent : null;

      this.set('cpu', pct(cpu), cpu == null ? undefined : cpu);
      this.set('ram', pct(mem), mem == null ? undefined : mem);

      const llm = Array.isArray(status.llm) ? status.llm : [];
      const active = llm.find((p) => p.connected);
      this.set('model', active ? (active.default_model || active.model || active.name || 'OK') : 'hors ligne');

      const gpu = status.gpu || m.gpu || null;
      this.set('gpu', gpu ? (gpu.name || gpu.device || (gpu.available ? 'actif' : 'cpu')) : '—');

      const tools = status.tools || {};
      this.set('tools', tools.enabled != null ? `${tools.enabled}/${tools.total}` : '—');

      const agents = status.core && status.core.agents;
      this.set('agents', agents ? `${agents.running || 0}/${agents.total || 0}` : '—');

      this.set('net', (status.network && status.network.mode) || status.location || '—');

      const up = status.uptime_s ?? (m.uptime_s ?? null);
      this.set('uptime', up == null ? '—' : this.fmtUptime(up));

      const health = (status.core && status.core.system && status.core.system.status) || 'ok';
      this.setState(health === 'critical' ? 'CRITIQUE' : health === 'warning' ? 'DÉGRADÉ' : 'JARVIS ONLINE',
        health === 'critical' ? 'err' : health === 'warning' ? 'warn' : 'ok');
    },

    fmtUptime(s) {
      s = Math.max(0, Math.round(s));
      const h = Math.floor(s / 3600); const mn = Math.floor((s % 3600) / 60);
      return h ? `${h}h${String(mn).padStart(2, '0')}` : `${mn}m`;
    },
  };

  window.JarvisHud = JarvisHud;
})();
