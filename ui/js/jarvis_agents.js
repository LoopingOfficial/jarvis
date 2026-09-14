/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_agents.js
   Vue constellation des agents : chaque agent est un nœud relié au core.
   Les cartes existantes restent sous la constellation (aucune perte de
   fonction : lancer / désactiver / détails sont inchangés).

   Les nœuds reflètent /api/agents + les événements agent.* réels.
   ========================================================================== */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };

  const JarvisAgents = {
    nodes: [],

    init() {
      if (typeof J !== 'undefined' && typeof J.on === 'function') {
        ['agent.started', 'agent.progress', 'agent.completed', 'agent.failed', 'agent.idle']
          .forEach((evt) => J.on(evt, (d) => this.mark(evt, d || {})));
      }
      // La page Agents est rendue par Pages.render : on s'y greffe à l'arrivée.
      const observer = new MutationObserver(() => {
        clearTimeout(this._t);
        this._t = setTimeout(() => this.ensure(), 120);
      });
      observer.observe(document.body, { childList: true, subtree: true });
      this.ensure();
    },

    async ensure() {
      const page = document.getElementById('page-agents');
      if (!page || !page.classList.contains('active')) return;
      if (page.querySelector('.v4-constellation')) return;
      if (this._loading) return;
      this._loading = true;
      try {
        const res = await J.get('/api/agents').catch(() => null);
        const agents = (res && (res.agents || res.items)) || [];
        if (!agents.length) return;
        this.render(page, agents);
      } finally {
        this._loading = false;
      }
    },

    render(page, agents) {
      const host = document.createElement('section');
      host.className = 'v4-constellation card';
      host.innerHTML = '<div class="v4-kicker">RÉSEAU D\'AGENTS</div>';

      const W = 720; const H = 300;
      const svg = mk('svg', { viewBox: `0 0 ${W} ${H}`, class: 'v4-constellation-svg' });
      const cx = W / 2; const cy = H / 2;

      // Un seul agent tient le centre : les autres orbitent, aucun doublon.
      const core = agents.find((a) => /core/i.test(a.id || a.name || '')) || { name: 'JARVIS CORE' };
      const others = agents.filter((a) => a !== core);

      this.nodes = [];
      others.forEach((agent, i) => {
        const angle = (i / others.length) * Math.PI * 2 - Math.PI / 2;
        const rx = 250; const ry = 105;
        const x = cx + Math.cos(angle) * rx;
        const y = cy + Math.sin(angle) * ry;

        const link = mk('line', { x1: cx, y1: cy, x2: x, y2: y, class: 'v4-link' });
        svg.appendChild(link);

        const g = mk('g', { class: 'v4-node', transform: `translate(${x} ${y})` });
        g.appendChild(mk('circle', { r: 16, class: 'v4-node-halo' }));
        g.appendChild(mk('circle', { r: 6, class: 'v4-node-dot' }));
        const label = mk('text', { y: 32, 'text-anchor': 'middle', class: 'v4-node-label' });
        label.textContent = (agent.name || agent.id || '').toUpperCase();
        g.appendChild(label);
        const st = mk('text', { y: 44, 'text-anchor': 'middle', class: 'v4-node-state' });
        st.textContent = agent.status || agent.state || 'standby';
        g.appendChild(st);
        svg.appendChild(g);

        this.nodes.push({ id: (agent.id || agent.name || '').toLowerCase(), g, link, st });
      });

      const gc = mk('g', { class: 'v4-node core', transform: `translate(${cx} ${cy})` });
      gc.appendChild(mk('circle', { r: 26, class: 'v4-node-halo' }));
      gc.appendChild(mk('circle', { r: 9, class: 'v4-node-dot' }));
      const cl = mk('text', { y: 44, 'text-anchor': 'middle', class: 'v4-node-label' });
      cl.textContent = (core.name || 'JARVIS CORE').toUpperCase();
      gc.appendChild(cl);
      svg.appendChild(gc);

      host.appendChild(svg);
      page.insertBefore(host, page.querySelector('.page-head')?.nextSibling || page.firstChild);
    },

    /** Flux réel : un agent sollicité s'allume et pousse vers le core. */
    mark(type, data) {
      const key = String(data.id || data.name || '').toLowerCase();
      const node = this.nodes.find((n) => key && (n.id === key || n.id.includes(key) || key.includes(n.id)));
      if (!node) return;
      const tone = type === 'agent.failed' ? 'err'
        : type === 'agent.completed' ? 'ok'
          : type === 'agent.idle' ? '' : 'live';
      node.g.setAttribute('data-tone', tone);
      node.link.setAttribute('data-tone', tone);
      if (node.st) node.st.textContent = type.replace('agent.', '');
    },
  };

  window.JarvisAgents = JarvisAgents;
})();
