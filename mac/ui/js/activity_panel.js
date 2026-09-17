/* JARVIS 4 — activity_panel.js.
 * Affiche en temps réel l'action en cours (activité réelle du backend,
 * des événements activity.trace / tool.* / jarvis.state / bossa brain.*).
 * Rien n'est simulé : chaque ligne correspond à un événement reçu.
 */
(function () {
  'use strict';

  const MAX_ITEMS = 60;
  const listEl = document.getElementById('activityList');

  const stateColors = {
    ACTING: '#60a5fa', THINKING: '#38bdf8', RECALLING: '#4ade80',
    LEARNING: '#4ade80', SPEAKING: '#a78bfa', ERROR: '#fb7185',
    WAITING: '#fbbf24', MODE: '#22d3ee',
  };

  function stamp() {
    const d = new Date();
    return d.toTimeString().slice(0, 8);
  }

  function iconFor(type, kind) {
    if (type === 'tool') return 'hex';
    if (type === 'brain') return 'node';
    if (type === 'state') return 'state';
    if (type === 'learn') return 'node';
    if (type === 'voice') return 'voice';
    return 'dot';
  }

  function render(item) {
    const color = item.color || 'var(--ok)';
    return `<div class="act-item">
      <div class="act-ic ${iconFor(item.type, item.kind)}" style="color:${color}"></div>
      <div class="act-body">
        <div class="act-line"><b style="color:${color}">${esc(item.title || '')}</b></div>
        <div class="act-meta">${esc(item.meta || '')}</div>
      </div>
      <span class="act-time">${item.time || ''}</span>
    </div>`;
  }

  function esc(s) {
    const d = document.createElement('span');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function add(item) {
    if (!listEl) return;
    const node = document.createElement('div');
    node.className = 'act-wrap';
    node.innerHTML = render(item);
    listEl.appendChild(node);
    while (listEl.children.length > MAX_ITEMS) listEl.removeChild(listEl.firstChild);
    if (listEl.scrollTop > listEl.scrollHeight - listEl.clientHeight - 60 || listEl.children.length <= 4) {
      listEl.scrollTop = listEl.scrollHeight;
    }
  }

  const ActivityPanel = {
    add,
    clear() { if (listEl) listEl.innerHTML = ''; },
    ensureEmpty() {
      if (listEl && !listEl.children.length) {
        add({ title: 'JARVIS en veille.', meta: 'Aucune activité pour le moment.', type: 'state', time: stamp() });
      }
    },
  };

  function processEvent(e) {
    const d = e.data || {};
    const time = stamp();
    switch (e.type) {
      case 'jarvis.state':
        add({
          type: 'state',
          color: stateColors[d.state] || (d.state === 'SPEAKING' ? '#a78bfa' : '#38bdf8'),
          title: 'JARVIS · ' + (d.state || ''),
          meta: e.meta || d.reason || '',
          time,
        });
        break;
      case 'activity.trace':
        add({
          type: 'tool',
          color: '#60a5fa',
          title: d.icon ? d.icon + ' ' + d.label : (d.label || e.meta || 'Activité'),
          meta: typeof d.details === 'string' ? d.details : (d.tool || ''),
          time,
        });
        break;
      case 'tool.started':
        add({ type: 'tool', color: '#fbbf24', title: 'Outil · ' + (d.name || e.meta || ''), meta: d.args || '', time });
        break;
      case 'tool.completed':
        add({ type: 'tool', color: '#34d399', title: 'Outil OK · ' + (d.name || e.meta || ''), meta: (d.result || '').slice(0, 140), time });
        break;
      case 'tool.failed':
        add({ type: 'tool', color: '#fb7185', title: 'Outil ÉCHEC · ' + (d.name || e.meta || ''), meta: (d.error || '').slice(0, 140), time });
        break;
      case 'brain.search':
        add({ type: 'brain', color: '#38bdf8', title: 'Recherche mémoire', meta: ('« ' + (d.query || '') + ' »').slice(0, 120), time });
        break;
      case 'brain.node.selected':
        add({ type: 'brain', color: '#4ade80', title: 'Nœud consulté', meta: d.label || d.id || '', time });
        break;
      case 'brain.learn.created':
        add({ type: 'learn', color: '#4ade80', title: 'Auto-apprentissage', meta: (d.label || d.title || '') + ' ✓', time });
        break;
      case 'brain.learn.updated':
        add({ type: 'learn', color: '#34d399', title: 'Mémoire renforcée', meta: (d.label || d.title || ''), time });
        break;
      case 'brain.tool.active':
        add({ type: 'brain', color: '#fbbf24', title: 'Réaction neuronale', meta: d.tool || d.label || '', time });
        break;
      case 'knowledge.learn.created':
        add({ type: 'learn', color: '#4ade80', title: 'Auto-apprentissage', meta: (d.title || e.meta || '') + ' ✓', time });
        break;
      case 'llm.started':
        add({ type: 'state', color: '#38bdf8', title: 'Réflexion en cours', meta: e.meta || '', time });
        break;
      case 'speech.listening.start':
        add({ type: 'voice', color: '#22d3ee', title: 'Écoute', meta: 'Micro actif', time });
        break;
      case 'speech.listening.stop':
        add({ type: 'voice', color: '#64748b', title: 'Écoute terminée', meta: '', time });
        break;
      default:
        if ((e.type || '').startsWith('brain.')) {
          add({ type: 'brain', color: '#38bdf8', title: 'Atlas · ' + e.type, meta: d.label || e.meta || '', time });
        }
    }
  }

  window.ActivityPanel = ActivityPanel;
  window.__activityProcessEvent = processEvent;
})();