/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_transitions.js
   Apparition des modules, expansion depuis le core, respect strict de
   prefers-reduced-motion (toutes les animations sont alors neutralisées).
   ========================================================================== */
(function () {
  'use strict';

  const reduced = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  const JarvisTransitions = {
    init() {
      document.documentElement.toggleAttribute('data-reduced-motion', reduced());
      if (window.matchMedia) {
        const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
        const onChange = () => document.documentElement.toggleAttribute('data-reduced-motion', reduced());
        mq.addEventListener ? mq.addEventListener('change', onChange) : mq.addListener(onChange);
      }

      // Apparition progressive des modules quand ils entrent dans la vue.
      this.io = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            // État final indépendant de toute transition : plus de module
            // bloqué à mi-opacité si le DOM est remanié pendant l'animation.
            entry.target.classList.remove('v4-enter');
            entry.target.classList.add('v4-in');
            this.io.unobserve(entry.target);
          }
        }
      }, { rootMargin: '0px 0px -8% 0px' });

      this.scan();
      const mo = new MutationObserver(() => this.scanSoon());
      mo.observe(document.body, { childList: true, subtree: true });
    },

    scanSoon() {
      clearTimeout(this._t);
      this._t = setTimeout(() => this.scan(), 120);
    },

    scan() {
      if (reduced()) return;
      document.querySelectorAll('.card, .ctx-card, .aw-panel, .v4-chat')
        .forEach((n) => {
          if (n.dataset.v4Anim) return;
          n.dataset.v4Anim = '1';
          n.classList.add('v4-enter');
          this.io.observe(n);
        });
    },

    /** Le changement de contexte « explose » depuis le noyau. */
    contextShift() {
      if (reduced()) return;
      const stage = document.getElementById('v4Canvas');
      if (!stage) return;
      stage.classList.remove('v4-shift');
      void stage.offsetWidth;
      stage.classList.add('v4-shift');
    },
  };

  window.JarvisTransitions = JarvisTransitions;
})();
