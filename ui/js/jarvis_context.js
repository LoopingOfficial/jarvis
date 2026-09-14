/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_context.js
   Interface contextuelle : JARVIS ne montre pas tout en permanence, il
   reconfigure sa scène selon la tâche réellement en cours.

   Le contexte est déduit de faits : page active, ouverture de l'Analysis
   Workspace, comparaison Sheet/Site, synchronisation, terminal, agents.
   ========================================================================== */
(function () {
  'use strict';

  const PAGE_CONTEXT = {
    command: 'chat',
    chat: 'chat',
    analyses: 'analysis',
    servers: 'sync',
    agents: 'agents',
    memory: 'memory',
    knowledge: 'memory',
    brain: 'memory',
    terminal: 'terminal',
    code: 'terminal',
    tools: 'tools',
    'avatar-studio': 'visual',
  };

  const JarvisContext = {
    current: null,

    init() {
      this.enter((typeof J !== 'undefined' && J.state && J.state.page) || 'command');

      // App.goto reste la seule route : on l'observe, on ne la remplace pas.
      const app = window.App;
      if (app && !app.__v4Context) {
        const original = app.goto.bind(app);
        app.goto = (page, options) => {
          const result = original(page, options);
          this.enter(page);
          window.JarvisNavigation?.sync(page);
          return result;
        };
        app.__v4Context = true;
      }

      if (typeof J !== 'undefined' && typeof J.on === 'function') {
        J.on('sheet.analysis.started', () => this.set('analysis'));
        J.on('brainrot.compare', () => this.set('compare'));
        J.on('brainrot.sync.started', () => this.set('sync'));
        J.on('agent.started', () => this.hint('agents'));
        J.on('image.generation.started', () => this.hint('visual'));
      }

      // L'Analysis Workspace est un overlay monté sur <body> : on le détecte.
      const probe = () => {
        const ws = document.getElementById('analysisWorkspace') || document.querySelector('.aw-root');
        const open = !!ws && !ws.classList.contains('hidden') && ws.hidden !== true;
        document.documentElement.toggleAttribute('data-workspace-open', open);
        if (open) this.set('analysis');
        const sync = document.getElementById('syncFeedback');
        const syncing = !!sync && !sync.classList.contains('hidden');
        document.documentElement.toggleAttribute('data-sync-open', syncing);
        if (syncing) this.set('sync');
        // Le noyau bascule en mode SYNC tant que le pipeline réel tourne.
        if (syncing !== this._syncing) {
          this._syncing = syncing;
          if (syncing) window.JarvisCore?.setState('SYNCING', { reason: 'synchronisation en cours' });
          else if (window.JarvisCore?.state === 'SYNCING') window.JarvisCore.setState('IDLE');
        }
      };
      // Sonde amortie : l'UI mute beaucoup, le contexte ne doit pas suivre au tick.
      const observer = new MutationObserver(() => {
        clearTimeout(this._probeTimer);
        this._probeTimer = setTimeout(probe, 150);
      });
      observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] });
      probe();
    },

    enter(page) {
      if (!page) return;
      this.set(PAGE_CONTEXT[page] || 'workspace');
    },

    set(context) {
      if (this.current === context) return;
      this.current = context;
      document.documentElement.setAttribute('data-context', context);
      window.JarvisTransitions?.contextShift();
    },

    /** Accent temporaire sans quitter le contexte courant. */
    hint(context) {
      document.documentElement.setAttribute('data-context-hint', context);
      clearTimeout(this._hintTimer);
      this._hintTimer = setTimeout(() => {
        document.documentElement.removeAttribute('data-context-hint');
      }, 4000);
    },
  };

  window.JarvisContext = JarvisContext;
})();
