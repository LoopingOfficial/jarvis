/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_activity.js
   Flux d'activité de la barre de commande : ce que JARVIS fait, maintenant.
   Chaque ligne correspond à un événement SSE réellement reçu.
   ========================================================================== */
(function () {
  'use strict';

  const LABELS = {
    'tool.start': ['OUTIL', 'cy'],
    'tool.end': ['OUTIL OK', 'ok'],
    'tool.error': ['OUTIL ÉCHEC', 'err'],
    'agent.started': ['AGENT', 'cy'],
    'agent.completed': ['AGENT OK', 'ok'],
    'agent.failed': ['AGENT ÉCHEC', 'err'],
    'task.created': ['TÂCHE', 'cy'],
    'task.completed': ['TÂCHE OK', 'ok'],
    'task.failed': ['TÂCHE ÉCHEC', 'err'],
    'brainrot.sync.started': ['SYNC', 'warn'],
    'brainrot.sync.done': ['SYNC VERIFIED', 'ok'],
    'image.generation.started': ['IMAGE', 'cy'],
    'memory.stored': ['MÉMOIRE', 'cy'],
  };

  const JarvisActivity = {
    max: 4,

    init() {
      this.host = document.getElementById('v4ActivityStream');
      if (!this.host || typeof J === 'undefined' || typeof J.on !== 'function') return;
      J.on('*', (type, payload) => this.push(type, payload));
      window.addEventListener('jarvis:core-state', (e) => {
        if (e.detail.reason) this.line(String(e.detail.state).replace(/_/g, ' '), e.detail.reason, 'cy');
      });
    },

    push(type, payload) {
      const known = LABELS[type];
      if (!known) return;
      payload = payload || {};
      const detail = payload.name || payload.tool || payload.title || payload.label
        || payload.message || payload.id || '';
      this.line(known[0], detail, known[1]);
    },

    line(label, detail, tone) {
      if (!this.host) return;
      const row = document.createElement('div');
      row.className = 'v4-act';
      row.dataset.tone = tone || 'cy';
      row.innerHTML = `<i></i><b></b><span></span>`;
      row.querySelector('b').textContent = label;
      row.querySelector('span').textContent = detail ? String(detail).slice(0, 90) : '';
      this.host.prepend(row);
      while (this.host.children.length > this.max) this.host.lastElementChild.remove();
      // Nettoyage : l'activité n'est pas un historique, c'est un flux.
      setTimeout(() => row.classList.add('fade'), 6000);
      setTimeout(() => row.remove(), 7000);
    },
  };

  window.JarvisActivity = JarvisActivity;
})();
