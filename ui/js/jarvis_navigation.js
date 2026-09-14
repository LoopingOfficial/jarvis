/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_navigation.js
   Rail de navigation flottant. Il pilote App.goto() : aucune route nouvelle,
   aucune page perdue — le « Plus » expose l'intégralité de App.NAV.
   ========================================================================== */
(function () {
  'use strict';

  const ICONS = {
    command: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    analyses: '<path d="M4 19V9M10 19V4M16 19v-7M22 19H2"/>',
    sync: '<path d="M21 12a9 9 0 0 1-15.5 6.2M3 12a9 9 0 0 1 15.5-6.2"/><path d="M3 4v5h5M21 20v-5h-5"/>',
    agents: '<circle cx="12" cy="5" r="2.2"/><circle cx="5" cy="17" r="2.2"/><circle cx="19" cy="17" r="2.2"/><path d="M12 7.2 6.4 15M12 7.2 17.6 15M7.2 17h9.6"/>',
    memory: '<path d="M12 3a4 4 0 0 0-4 4 3 3 0 0 0-1 5.8V15a4 4 0 0 0 8 0v-2.2A3 3 0 0 0 16 7a4 4 0 0 0-4-4z"/>',
    tools: '<path d="M14.7 6.3a4 4 0 0 0 5 5L15 16l-3 3-4-4 3-3z"/>',
    system: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M4 12h2M18 12h2M12 4v2M12 18v2"/>',
    more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  };

  /* Entrées principales du rail — chacune pointe vers une page existante. */
  const RAIL = [
    ['command', 'Accueil', 'command'],
    ['chat', 'Chat', 'chat'],
    ['analyses', 'Analyse', 'analyses'],
    ['servers', 'Synchronisation', 'sync'],
    ['agents', 'Agents', 'agents'],
    ['memory', 'Mémoire', 'memory'],
    ['tools', 'Outils', 'tools'],
    ['terminal', 'Système', 'system'],
    ['settings', 'Paramètres', 'settings'],
  ];

  const svg = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;

  const JarvisNavigation = {
    init() {
      const host = document.getElementById('v4Nav');
      if (!host) return;
      this.host = host;

      host.innerHTML = `
        <button class="v4-nav-toggle" id="v4NavToggle" title="Replier la navigation" aria-label="Replier la navigation">
          ${svg('<path d="m15 6-6 6 6 6"/>')}
        </button>
        <div class="v4-nav-list">
          ${RAIL.map(([page, label, ic]) => `
            <button class="v4-nav-item" data-v4nav="${page}" title="${label}">
              <span class="v4-nav-arc" aria-hidden="true"></span>
              ${svg(ICONS[ic] || ICONS.command)}
              <span class="v4-nav-label">${label}</span>
            </button>`).join('')}
          <button class="v4-nav-item" id="v4NavMore" title="Toutes les sections">
            <span class="v4-nav-arc" aria-hidden="true"></span>
            ${svg(ICONS.more)}<span class="v4-nav-label">Plus</span>
          </button>
        </div>
        <div class="v4-nav-drawer" id="v4NavDrawer" hidden></div>`;

      host.querySelectorAll('[data-v4nav]').forEach((btn) => {
        btn.addEventListener('click', () => this.go(btn.dataset.v4nav));
      });

      document.getElementById('v4NavToggle').addEventListener('click', () => {
        const collapsed = host.classList.toggle('collapsed');
        try { localStorage.setItem('jarvis.v4.nav', collapsed ? '1' : '0'); } catch (_) { /* stockage indisponible */ }
      });
      try { if (localStorage.getItem('jarvis.v4.nav') === '1') host.classList.add('collapsed'); } catch (_) { /* idem */ }

      this.buildDrawer();
      this.sync();
      window.addEventListener('hashchange', () => this.sync());
    },

    /* Tiroir complet : rien de l'ancienne sidebar n'est perdu. */
    buildDrawer() {
      const drawer = document.getElementById('v4NavDrawer');
      const groups = (window.App && App.NAV) || [];
      drawer.innerHTML = groups.map((g) => `
        <div class="v4-drawer-group">
          <span class="v4-kicker">${g.label}</span>
          ${g.items.map(([id, label]) => `<button data-v4nav-more="${id}">${label}</button>`).join('')}
        </div>`).join('');
      drawer.querySelectorAll('[data-v4nav-more]').forEach((b) => {
        b.addEventListener('click', () => { this.go(b.dataset.v4navMore); drawer.hidden = true; });
      });
      document.getElementById('v4NavMore').addEventListener('click', () => {
        drawer.hidden = !drawer.hidden;
      });
      document.addEventListener('click', (e) => {
        if (drawer.hidden) return;
        if (!drawer.contains(e.target) && !e.target.closest('#v4NavMore')) drawer.hidden = true;
      });
    },

    go(page) {
      window.App?.goto(page);
      window.JarvisContext?.enter(page);
      this.sync(page);
    },

    sync(page) {
      const current = page || (typeof J !== 'undefined' && J.state && J.state.page) || location.hash.replace('#', '') || 'command';
      this.host?.querySelectorAll('[data-v4nav]').forEach((b) => {
        b.classList.toggle('active', b.dataset.v4nav === current);
      });
    },
  };

  window.JarvisNavigation = JarvisNavigation;
})();
