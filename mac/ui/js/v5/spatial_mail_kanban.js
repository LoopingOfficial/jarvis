/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_mail_kanban.js
   Kanban de tri du courrier. Rien n'est simulé ici : chaque carte correspond
   à un message réellement lu par `email.process_inbox` et classé par les
   règles déterministes de jarvis/mail.py. La carte porte le MOTIF de son
   classement — on doit toujours pouvoir répondre à « pourquoi est-il là ? ».

   Géométrie : le panneau occupe la bande de travail (rail → gouttière droite)
   en réutilisant --v5rail et --v5right posées à la Task 2.1. C'est la seule
   position qui garantit qu'il ne passe ni sous l'aperçu navigateur ni sous
   l'Analysis Workspace, puisque ces deux-là vivent dans la colonne droite.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* Miroir strict de jarvis/mail.py : même ordre, mêmes clés. Si le backend
     renvoie une catégorie inconnue, on ne la jette pas — on l'affiche à part
     plutôt que de faire disparaître un message silencieusement. */
  const COLUMNS = [
    ['reply',   'À répondre'],
    ['forward', 'À transférer'],
    ['invoice', 'Factures'],
    ['quote',   'Devis'],
    ['archive', 'Archives'],
  ];

  const Kanban = {
    open: false,
    counts: {},
    total: 0,
    running: false,

    init() {
      if (this.el) return this;
      const el = document.createElement('section');
      el.className = 'v5-mail';
      el.id = 'v5Mail';
      el.hidden = true;
      el.innerHTML = `
        <header class="vm-head">
          <span class="vm-kick">COURRIER</span>
          <h2>Tri de la boîte de réception</h2>
          <span class="vm-state" id="v5MailState">—</span>
          <button class="vm-run" id="v5MailRun">TRIER LA BOÎTE</button>
          <button class="vm-close" id="v5MailClose" title="Fermer">✕</button>
        </header>
        <div class="vm-board" id="v5MailBoard"></div>
        <p class="vm-empty" id="v5MailEmpty">Aucun tri effectué. Lance « TRIER LA BOÎTE »
          ou demande à JARVIS de relever le courrier.</p>`;
      document.body.appendChild(el);
      this.el = el;
      this.board = byId('v5MailBoard');
      this.stateEl = byId('v5MailState');
      this.emptyEl = byId('v5MailEmpty');
      this.buildColumns();

      byId('v5MailClose').addEventListener('click', () => this.hide());
      byId('v5MailRun').addEventListener('click', () => this.run());
      addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.open) this.hide();
      });
      return this;
    },

    buildColumns() {
      this.board.replaceChildren();
      this.cols = {};
      COLUMNS.forEach(([key, label]) => {
        const col = document.createElement('div');
        col.className = 'vm-col';
        col.dataset.cat = key;
        col.innerHTML = `
          <div class="vm-col-head"><b>${esc(label)}</b><em data-count="0">0</em></div>
          <div class="vm-col-body"></div>`;
        this.board.appendChild(col);
        this.cols[key] = col.querySelector('.vm-col-body');
      });
    },

    show() {
      if (!this.el) this.init();
      this.open = true;
      this.el.hidden = false;
      requestAnimationFrame(() => this.el.classList.add('in'));
    },
    hide() {
      if (!this.el) return;
      this.open = false;
      this.el.classList.remove('in');
      setTimeout(() => { if (!this.open) this.el.hidden = true; }, 260);
    },
    toggle() { this.open ? this.hide() : this.show(); },

    /** Remet le tableau à zéro : un nouveau tri ne s'empile pas sur l'ancien. */
    reset(source) {
      this.counts = {};
      this.total = 0;
      this.buildColumns();
      if (this.emptyEl) this.emptyEl.hidden = true;
      this.setState(source ? `TRI EN COURS · ${String(source).toUpperCase()}` : 'TRI EN COURS', 'run');
    },

    setState(text, tone) {
      if (!this.stateEl) return;
      this.stateEl.textContent = text || '—';
      this.stateEl.dataset.tone = tone || '';
    },

    /** Une carte = un message réellement classé par le backend. */
    addCard(card) {
      if (!this.el) this.init();
      const cat = card && card.category;
      let host = this.cols[cat];
      if (!host) {
        // Catégorie inattendue : on la crée plutôt que de perdre le message.
        const col = document.createElement('div');
        col.className = 'vm-col';
        col.dataset.cat = cat || 'inconnu';
        col.innerHTML = `<div class="vm-col-head"><b>${esc(card.category_label || cat || 'Autre')}</b>
          <em data-count="0">0</em></div><div class="vm-col-body"></div>`;
        this.board.appendChild(col);
        host = this.cols[cat] = col.querySelector('.vm-col-body');
      }
      const node = document.createElement('article');
      node.className = 'vm-card';
      if (card.unread) node.classList.add('unread');
      const att = (card.attachments || []).length;
      node.innerHTML = `
        <div class="vm-from">${esc(card.sender || card.sender_email || 'Expéditeur inconnu')}</div>
        <div class="vm-subject">${esc(card.subject || '(sans objet)')}</div>
        <div class="vm-snippet">${esc(card.snippet || '')}</div>
        <div class="vm-meta">
          <span class="vm-reason" title="Motif du classement">${esc(card.reason || '')}</span>
          ${att ? `<span class="vm-att" title="${esc((card.attachments || []).join(', '))}">${att} PJ</span>` : ''}
        </div>`;
      host.appendChild(node);

      this.counts[cat] = (this.counts[cat] || 0) + 1;
      this.total++;
      const counter = host.parentNode.querySelector('[data-count]');
      if (counter) { counter.textContent = this.counts[cat]; counter.dataset.count = this.counts[cat]; }
    },

    /** Lance le tri par l'outil réel — même chemin que l'agent. */
    run() {
      if (this.running) return;
      this.running = true;
      this.show();
      this.setState('LECTURE…', 'run');
      byId('v5MailRun')?.setAttribute('disabled', 'disabled');
      fetch('/api/tools/email.process_inbox/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arguments: {} }),
      })
        .then((r) => r.json())
        .then((res) => {
          const result = res && res.result;
          if (result && result.ok === false) {
            this.setState('ÉCHEC', 'err');
            window.toast?.(result.output || 'Tri impossible.', 'warn');
          }
          // Le remplissage se fait par les événements SSE : rien à peindre ici.
        })
        .catch(() => this.setState('ÉCHEC', 'err'))
        .finally(() => {
          this.running = false;
          byId('v5MailRun')?.removeAttribute('disabled');
        });
    },
  };

  /* Branchement SSE : les cartes se rangent au fil du tri, pas en bloc. */
  function bind() {
    if (Kanban._sse) return;
    if (typeof J === 'undefined' || typeof J.on !== 'function') {
      setTimeout(bind, 400);
      return;
    }
    Kanban._sse = true;

    J.on('mail.inbox.started', (d) => {
      Kanban.show();
      Kanban.reset(d && d.source);
    });
    J.on('mail.message.classified', (d) => {
      if (!Kanban.open) Kanban.show();
      Kanban.addCard(d || {});
    });
    J.on('mail.inbox.completed', (d) => {
      const total = (d && d.total) || Kanban.total;
      Kanban.setState(total ? `${total} MESSAGE(S) TRIÉ(S)` : 'BOÎTE VIDE', 'ok');
      if (!total && Kanban.emptyEl) {
        Kanban.emptyEl.hidden = false;
        Kanban.emptyEl.textContent = 'Aucun message à trier.';
      }
    });
    J.on('mail.inbox.failed', (d) => {
      Kanban.setState('ÉCHEC', 'err');
      if (Kanban.emptyEl) {
        Kanban.emptyEl.hidden = false;
        Kanban.emptyEl.textContent = (d && d.error) || 'Lecture de la boîte impossible.';
      }
    });
  }

  window.JarvisMailKanban = Kanban;

  function boot() { Kanban.init(); bind(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
