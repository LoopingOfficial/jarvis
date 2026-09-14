/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_chat.js
   Couche de présentation du dialogue : signature holographique, horodatage
   discret, et phase de génération RÉELLE (pas de « … » générique).

   La phase affichée est dérivée de l'état réel publié par App.setRobot :
   ANALYSE / LECTURE / RAISONNEMENT / OUTIL / VALIDATION.
   Aucune phase n'est inventée quand le backend reste silencieux : on retombe
   sur « ANALYSE », l'état effectivement en cours côté app.
   ========================================================================== */
(function () {
  'use strict';

  const PHASES = {
    THINKING: 'RAISONNEMENT',
    RECALLING: 'LECTURE MÉMOIRE',
    READING_FILE: 'LECTURE',
    USING_TOOL: 'OUTIL',
    CODING: 'OUTIL · CODE',
    BROWSING: 'OUTIL · WEB',
    DEPLOYING: 'OUTIL · DÉPLOIEMENT',
    VERIFYING: 'VALIDATION',
    LEARNING: 'APPRENTISSAGE',
    SYNCING: 'SYNCHRONISATION',
    SPEAKING: 'RÉPONSE',
    LISTENING: 'ÉCOUTE',
  };

  const JarvisChat = {
    init() {
      this.log = document.getElementById('convLog');
      if (!this.log) return;
      this.wrapPush();
      this.decorate(this.log);

      // Les modules existants (images, 3D, avatar) injectent aussi dans #convLog.
      this.observer = new MutationObserver((records) => {
        for (const r of records) {
          r.addedNodes.forEach((n) => {
            if (n.nodeType === 1 && n.classList.contains('msg')) this.stamp(n);
          });
        }
        this.log.scrollTop = this.log.scrollHeight;
      });
      this.observer.observe(this.log, { childList: true });

      window.addEventListener('jarvis:core-state', (e) => this.phase(e.detail.state));

      // ⌘K / Ctrl+K visait la barre de recherche du header legacy, désormais
      // hors vue : le raccourci pointe sur la barre de commande.
      window.addEventListener('keydown', (e) => {
        if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return;
        e.preventDefault();
        document.getElementById('convInput')?.focus();
      }, true);

      const input = document.getElementById('convInput');
      const bar = document.getElementById('v4CommandBar');
      if (input && bar) {
        input.addEventListener('focus', () => bar.classList.add('focused'));
        input.addEventListener('blur', () => bar.classList.remove('focused'));
        // Barre de commande : Entrée envoie, Maj+Entrée retourne à la ligne.
        input.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
          e.preventDefault();
          const form = document.getElementById('convForm');
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
        });
        input.addEventListener('input', () => {
          input.style.height = 'auto';
          input.style.height = Math.min(160, input.scrollHeight) + 'px';
        });
      }
    },

    /* Marque le message d'attente pour pouvoir y afficher la phase réelle. */
    wrapPush() {
      const app = window.App;
      if (!app || app.__v4Chat) return;
      const original = app.pushMessage.bind(app);
      app.pushMessage = (role, text, options = {}) => {
        const node = original(role, text, options);
        if (options.pending) {
          (app.pendingReplyNodes || []).forEach((n) => {
            if (!n) return;
            n.classList.add('v4-pending');
            const bubble = n.querySelector('.bubble');
            if (bubble) {
              bubble.innerHTML = `<span class="v4-phase" data-phase>ANALYSE</span>
                <span class="v4-phase-dots"><i></i><i></i><i></i></span>`;
            }
          });
        }
        return node;
      };
      app.__v4Chat = true;
    },

    phase(state) {
      const label = PHASES[state];
      document.querySelectorAll('.v4-pending [data-phase]').forEach((n) => {
        n.textContent = label || 'ANALYSE';
      });
    },

    decorate(container) {
      container.querySelectorAll('.msg').forEach((n) => this.stamp(n));
    },

    stamp(node) {
      if (node.dataset.v4Stamped) return;
      node.dataset.v4Stamped = '1';
      const time = document.createElement('span');
      time.className = 'v4-msg-time';
      time.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
      node.appendChild(time);
    },
  };

  window.JarvisChat = JarvisChat;
})();
