/* ==========================================================================
   JARVIS — cartes métier dans la conversation.

   Deux événements backend n'avaient aucun rendu et restaient orphelins :

     document.generated        → facture ou devis produit (pdf_tools)
     crm.clarification.needed  → plusieurs contacts correspondent (crm_tools)

   Le rendu suit le patron déjà en place pour les artefacts générés
   (image_message.js, model_message.js) : une carte poussée dans les DEUX
   flux de conversation. C'est là que se trouve l'attention de l'utilisateur
   au moment où il demande une facture, et la carte reste dans l'historique.

   Règle tenue : aucune carte n'est fabriquée à partir d'une supposition.
   Chaque montant affiché vient du calcul backend, chaque option de
   désambiguïsation vient d'une fiche réellement trouvée dans le CRM.
   ========================================================================== */
(function () {
  'use strict';

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function containers() {
    return [document.getElementById('convLog'), document.getElementById('consoleLog')]
      .filter(Boolean);
  }

  /** Pousse une même carte dans les deux flux et renvoie les nœuds créés. */
  function push(html, className) {
    const nodes = [];
    containers().forEach((host) => {
      const el = document.createElement('div');
      el.className = 'msg ' + (className || '');
      el.innerHTML = html;
      host.appendChild(el);
      host.scrollTop = host.scrollHeight;
      nodes.push(el);
    });
    return nodes;
  }

  /* ====================================================== DOCUMENTS PDF == */
  const DocumentMessages = {
    /** Dernier document reçu — sert aussi au panneau contextuel V5. */
    last: null,

    card(doc) {
      const label = esc(doc.kind_label || 'DOCUMENT');
      const who = esc(doc.contact_company || doc.contact_name || '');
      const ttc = esc(doc.total_ttc_label || (doc.total_ttc ? doc.total_ttc + ' €' : ''));
      const href = doc.url || (doc.artifact && doc.artifact.url) ||
        '/api/documents/' + encodeURIComponent(doc.filename || '');
      const due = doc.due_on
        ? `<span class="doc-due">Échéance ${esc(frDate(doc.due_on))}</span>` : '';
      const sizeBytes = doc.size || doc.bytes || (doc.artifact && doc.artifact.size) || 0;
      const size = sizeBytes ? `${Math.max(1, Math.round(sizeBytes / 1024))} Ko` : '';
      return `
        <div class="who">JARVIS</div>
        <div class="bubble docbubble">
          <div class="doccard" data-kind="${esc(doc.kind || '')}">
            <div class="doccard-head">
              <span class="doc-kind">${label}</span>
              <b class="doc-number">${esc(doc.number || '')}</b>
              ${due}
            </div>
            <div class="doccard-body">
              ${who ? `<div class="doc-client">${who}</div>` : ''}
              <div class="doc-total">${ttc}<em>TTC</em></div>
              <div class="doc-detail">
                HT ${esc(doc.total_ht || '')} € · TVA ${esc(doc.total_vat || '')} €
              </div>
            </div>
            <div class="doccard-actions">
              <a class="doc-btn primary" href="${href}" target="_blank" rel="noopener">Ouvrir</a>
              <a class="doc-btn" href="${href}" download="${esc(doc.filename || '')}">Télécharger</a>
              <span class="doc-file">${esc(doc.filename || '')}${size ? ' · ' + size : ''}</span>
            </div>
          </div>
        </div>`;
    },

    generated(doc) {
      if (!doc || !doc.filename) return;      // sans fichier, pas de carte
      this.last = doc;
      push(this.card(doc), 'doc-msg');
      // Panneau contextuel V5 : rappel discret du dernier document produit.
      try {
        const who = doc.contact_company || doc.contact_name || '';
        window.JarvisSpatial?.setCtx?.(
          `${doc.kind_label || 'Document'} ${doc.number} · ${doc.total_ttc_label || ''}`
          + (who ? ` · ${who}` : ''));
      } catch (_) { /* le shell V5 peut être absent (mode V4) */ }
    },

    bind() {
      if (this._bound || typeof J === 'undefined') return;
      this._bound = true;
      J.on('document.generated', (d) => this.generated(d));
    },
  };

  function frDate(iso) {
    const parts = String(iso || '').split('-');
    return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : String(iso || '');
  }

  /* ================================================== DÉSAMBIGUÏSATION == */
  const ClarificationMessages = {
    card(payload) {
      const options = (payload.options || []).map((o, i) => `
        <button class="clar-opt" data-choice="${i}">
          <b>${esc(o.label || '')}</b>
          ${o.email ? `<em>${esc(o.email)}</em>` : ''}
        </button>`).join('');
      return `
        <div class="who">JARVIS</div>
        <div class="bubble clarbubble">
          <div class="clarcard">
            <div class="clar-q">${esc(payload.question || 'Quel contact ?')}</div>
            <div class="clar-opts">${options}</div>
          </div>
        </div>`;
    },

    needed(payload) {
      const options = (payload && payload.options) || [];
      if (!options.length) return;
      const nodes = push(this.card(payload), 'clar-msg');
      nodes.forEach((node) => {
        node.querySelectorAll('.clar-opt').forEach((button) => {
          button.addEventListener('click', () => this.choose(payload, nodes, button.dataset.choice));
        });
      });
    },

    /** Le choix repart par le flux de conversation normal : pas de chemin
        parallèle, l'agent reprend sa tâche là où il l'avait laissée. */
    choose(payload, nodes, index) {
      const option = (payload.options || [])[Number(index)];
      if (!option) return;
      nodes.forEach((node) => {
        node.querySelectorAll('.clar-opt').forEach((b) => {
          b.disabled = true;
          b.classList.toggle('chosen', b.dataset.choice === String(index));
        });
        node.querySelector('.clarcard')?.setAttribute('data-resolved', '1');
      });
      const label = option.label || option.id;
      window.App?.send?.(`C'est ${label} (identifiant ${option.id}).`);
    },

    bind() {
      if (this._bound || typeof J === 'undefined') return;
      this._bound = true;
      J.on('crm.clarification.needed', (d) => this.needed(d));
    },
  };

  window.DocumentMessages = DocumentMessages;
  window.ClarificationMessages = ClarificationMessages;

  function boot() { DocumentMessages.bind(); ClarificationMessages.bind(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
