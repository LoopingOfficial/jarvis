/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_v4.js
   Shell cinématique : construit la nouvelle scène et y RÉIMPLANTE les nœuds
   existants (mêmes ids, mêmes écouteurs, mêmes appels API).

   Principe de migration : on ne recrée AUCUNE logique. Le DOM fonctionnel
   existant est déplacé dans la nouvelle structure (un déplacement de nœud
   conserve ses gestionnaires d'événements), puis re-stylé par jarvis_v4.css.
   Aucune fonction n'est supprimée.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };

  const JarvisV4 = {
    BUILD_ID: 'JARVIS_CINEMATIC_UI_V4',
    booted: false,

    boot() {
      if (this.booted) return;
      this.booted = true;
      document.documentElement.setAttribute('data-jarvis-ui', 'v4');

      try {
        this.buildShell();
        this.relocate();
        this.mountCore();
        window.JarvisHud?.init();
        window.JarvisNavigation?.init();
        window.JarvisChat?.init();
        window.JarvisContext?.init();
        window.JarvisActivity?.init();
        window.JarvisAgents?.init();
        window.JarvisTransitions?.init();
        this.bridgeState();
        document.documentElement.setAttribute('data-v4-ready', '1');
        console.log('[JARVIS UI]', this.BUILD_ID, 'shell ready');
      } catch (error) {
        // Dégradation sûre : en cas d'échec du shell, l'UI d'origine reste utilisable.
        console.error('[JARVIS UI V4] shell failed, fallback legacy', error);
        document.documentElement.removeAttribute('data-jarvis-ui');
      }
    },

    /* ------------------------------------------------------------- scène */
    buildShell() {
      const app = byId('app');
      if (!app) throw new Error('#app introuvable');

      const shell = el('div', 'v4-shell');
      shell.id = 'v4Shell';
      shell.innerHTML = `
        <div class="v4-ambient" aria-hidden="true">
          <span class="v4-scan"></span><span class="v4-vign"></span>
        </div>
        <header class="v4-hud" id="v4Hud" aria-label="Statut système JARVIS"></header>
        <div class="v4-body">
          <nav class="v4-nav" id="v4Nav" aria-label="Navigation JARVIS"></nav>
          <main class="v4-stage" id="v4Stage">
            <section class="v4-core-layer" id="v4CoreLayer" aria-label="Noyau JARVIS">
              <div class="v4-core-frame">
                <canvas id="v4CoreCanvas" aria-hidden="true"></canvas>
                <div class="v4-core-host" id="v4CoreHost"></div>
              </div>
              <div class="v4-core-meta">
                <span class="v4-core-state" id="v4CoreState">IDLE</span>
                <span class="v4-core-reason" id="v4CoreReason">—</span>
              </div>
            </section>
            <section class="v4-canvas" id="v4Canvas" aria-label="Espace de travail"></section>
          </main>
          <aside class="v4-context" id="v4Context" aria-label="Contexte"></aside>
        </div>
        <footer class="v4-command" id="v4Command">
          <div class="v4-activity" id="v4ActivityStream" aria-live="polite"></div>
          <div class="v4-command-bar" id="v4CommandBar"></div>
        </footer>`;
      app.parentNode.insertBefore(shell, app);
      app.classList.add('v4-legacy-host');
    },

    /* -------------------------------------------- réimplantation des nœuds */
    relocate() {
      const move = (node, host) => { if (node && host) host.appendChild(node); };

      // 1. Espace de travail : toutes les pages existantes conservées.
      move(byId('pageWrap'), byId('v4Canvas'));

      // 2. Panneau de contexte : devient permanent (mêmes ids, même Dashboard).
      move(byId('contextPanel'), byId('v4Context'));

      // 3. Scène 3D existante (robot / avatar) au cœur du core.
      move(byId('jarvisStageCard'), byId('v4CoreHost'));

      // 4. Conversation : le log devient central, le composer devient command bar.
      const convZone = byId('convZone');
      const convLog = byId('convLog');
      const convForm = byId('convForm');
      if (convLog) {
        const wrap = el('section', 'v4-chat');
        wrap.id = 'v4Chat';
        wrap.appendChild(el('div', 'v4-chat-head',
          '<span class="v4-kicker">DIALOGUE</span><span class="v4-chat-sig">JARVIS</span>'));
        wrap.appendChild(convLog);
        byId('v4CoreLayer').appendChild(wrap);
      }
      move(convForm, byId('v4CommandBar'));
      if (convZone && !convZone.querySelector('.conv-log')) convZone.remove();

      const input = byId('convInput');
      if (input) input.setAttribute('placeholder', 'Que souhaitez-vous que JARVIS fasse ?');

      // 5. Les éléments legacy restants (header, sidebar, footer) restent dans
      //    le DOM — masqués visuellement — pour ne casser aucun sélecteur.
    },

    mountCore() {
      const canvas = byId('v4CoreCanvas');
      window.JarvisCore?.mount(canvas, { detail: 1 });
    },

    /* --------------------------------------------------- pont d'états réel */
    bridgeState() {
      const core = window.JarvisCore;
      if (!core) return;

      // App.setRobot est le point de passage unique des états réels de JARVIS.
      const app = window.App;
      if (app && typeof app.setRobot === 'function' && !app.__v4Bridged) {
        const original = app.setRobot.bind(app);
        app.setRobot = (state, extra = {}) => {
          original(state, extra);
          core.setState(state, extra);
        };
        app.__v4Bridged = true;
      }

      // Amplitude TTS réelle → pulsation du noyau.
      if (app && typeof app.robotAudioLevel === 'function' && !app.__v4Audio) {
        const originalLevel = app.robotAudioLevel.bind(app);
        app.robotAudioLevel = (level) => { originalLevel(level); core.setLevel(level); };
        app.__v4Audio = true;
      }

      const stateEl = byId('v4CoreState');
      const reasonEl = byId('v4CoreReason');
      window.addEventListener('jarvis:core-state', (e) => {
        const { state, reason } = e.detail;
        if (stateEl) stateEl.textContent = String(state).replace(/_/g, ' ');
        if (reasonEl) reasonEl.textContent = reason || '—';
      });
    },
  };

  window.JarvisV4 = JarvisV4;

  // V4 est désormais le FALLBACK de JARVIS_SPATIAL_OS_V5 : il ne démarre plus
  // de lui-même quand le mode spatial est actif. Le shell V5 l'appelle
  // explicitement (JarvisV4.boot()) s'il échoue ou si le mode est legacy_v4.
  const spatialWanted = () => {
    try {
      const q = new URLSearchParams(location.search).get('ui');
      if (q === 'legacy_v4') return false;
      if (q === 'spatial_v5') return true;
      return (localStorage.getItem('JARVIS_UI_MODE') || 'spatial_v5') === 'spatial_v5';
    } catch (_) { return true; }
  };
  const start = () => setTimeout(() => { if (!spatialWanted()) JarvisV4.boot(); }, 0);
  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', start);
  else start();
})();
