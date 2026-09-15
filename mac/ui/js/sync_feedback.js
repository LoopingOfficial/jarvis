/* ==========================================================================
   SyncFeedback — JARVIS_BRAINROT_SYNC_UI_FEEDBACK_V2_2

   Retour visuel de la synchronisation Brainrot : overlay de progression,
   animation CREATE / UPDATE, modales SUCCESS / ERROR, toasts.

   Règle tenue ici : rien n'est affiché comme réussi tant que le backend ne
   l'a pas confirmé. Pendant l'appel, les étapes sont marquées RUNNING ; à la
   réponse, chacune est reconciliée avec un champ réel du résultat
   (`backup_path`, `results[].status`, `error`). Aucune étape n'est peinte en
   vert par optimisme.

   Ce fichier ne touche ni au moteur de synchronisation, ni au comparateur,
   ni aux garde-fous : il ne fait que rendre ce qu'ils renvoient.
   ========================================================================== */
const SyncFeedback = {
  /* Étapes du pipeline, dans l'ordre réel d'exécution côté serveur. */
  STEPS: [
    { id: 'prepare', label: 'Préparation' },
    { id: 'backup', label: 'Sauvegarde' },
    { id: 'hash', label: 'Vérification du fichier distant' },
    { id: 'write', label: 'Écriture' },
    { id: 'reread', label: 'Relecture serveur' },
    { id: 'verify', label: 'Vérification' },
    { id: 'done', label: 'Terminé' },
  ],

  busy: false,
  state: {},

  reduced() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  },

  /* ------------------------------------------------------------- overlay -- */
  open(entries, plan) {
    if (this.busy) return false;           // second clic sans effet
    this.busy = true;
    this.state = {
      entries, plan, startedAt: performance.now(),
      steps: Object.fromEntries(this.STEPS.map((s) => [s.id, 'PENDING'])),
      applied: [], current: entries[0], result: null,
    };
    this.mount();
    this.el.classList.remove('hidden');
    this.setStep('prepare', 'RUNNING');
    this.render();
    return true;
  },

  close() {
    this.busy = false;
    this.el?.classList.add('hidden');
    this.el?.querySelector('[data-sf-body]')?.replaceChildren();
  },

  mount() {
    if (this.el) return;
    const el = document.createElement('div');
    el.id = 'syncFeedback';
    el.className = 'sf-overlay hidden';
    // aria-busy + capture des clics : l'overlay empêche tout double
    // déclenchement pendant l'écriture.
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.innerHTML = '<div class="sf-panel" data-sf-body></div>';
    document.body.appendChild(el);
    this.el = el;
  },

  setStep(id, status) {
    this.state.steps[id] = status;
  },

  /** Marque terminées toutes les étapes précédant `id`. */
  advanceTo(id, status = 'RUNNING') {
    let seen = false;
    for (const step of this.STEPS) {
      if (step.id === id) { seen = true; this.setStep(step.id, status); continue; }
      if (!seen && this.state.steps[step.id] === 'RUNNING') this.setStep(step.id, 'SUCCESS');
    }
  },

  render() {
    if (!this.el) return;
    const s = this.state;
    const entry = s.current || {};
    const total = s.entries.length;
    const done = s.applied.length;
    this.el.querySelector('[data-sf-body]').innerHTML = `
      <div class="sf-head">
        <div class="sf-ring ${this.reduced() ? 'still' : ''}">
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle class="sf-ring-track" cx="60" cy="60" r="52"/>
            <circle class="sf-ring-arc" cx="60" cy="60" r="52"/>
            <circle class="sf-ring-arc2" cx="60" cy="60" r="44"/>
          </svg>
          <div class="sf-ring-core">${this.thumb(entry)}</div>
        </div>
        <div class="sf-head-txt">
          <small>${esc(entry.action === 'CREATE' ? 'Création' : 'Mise à jour')}</small>
          <b>${esc(entry.identity || '')}</b>
          <span data-sf-caption>${esc(this.caption())}</span>
          ${total > 1 ? `<span class="sf-batch">${done} / ${total} changements appliqués</span>` : ''}
        </div>
      </div>
      <ol class="sf-steps">${this.STEPS.map((step) => `
        <li class="sf-step ${this.state.steps[step.id]}">
          <span class="sf-dot">${this.dot(this.state.steps[step.id])}</span>
          <span class="sf-step-label">${esc(step.label)}</span>
        </li>`).join('')}</ol>
      ${total > 1 ? `<ul class="sf-queue">${s.entries.map((e) => {
        const st = s.applied.find((a) => a.identity === e.identity);
        const cls = st ? (st.status === 'VERIFIED' ? 'SUCCESS' : 'ERROR')
                       : (e.identity === (s.current || {}).identity ? 'RUNNING' : 'PENDING');
        return `<li class="sf-qrow ${cls}"><span class="sf-dot">${this.dot(cls)}</span>
          ${esc(e.action)} ${esc(e.identity)}</li>`;
      }).join('')}</ul>` : ''}`;
  },

  caption() {
    const running = this.STEPS.find((s) => this.state.steps[s.id] === 'RUNNING');
    const failed = this.STEPS.find((s) => this.state.steps[s.id] === 'ERROR');
    if (failed) return `Échec : ${failed.label}`;
    return running ? `${running.label}…` : 'Terminé';
  },

  dot(status) {
    if (status === 'SUCCESS') return '✓';
    if (status === 'ERROR') return '✕';
    if (status === 'RUNNING') return '●';
    return '○';
  },

  thumb(entry) {
    const url = entry && typeof awThumbUrl === 'function' ? awThumbUrl(entry) : '';
    const initial = esc(String(entry.identity || '?').trim().charAt(0) || '?');
    return url
      ? `<img src="${esc(url)}" alt="" loading="eager"
           onerror="this.replaceWith(document.createTextNode('${initial}'))" />`
      : initial;
  },

  /* --------------------------------------------- reconciliation du résultat */
  /** Traduit la réponse backend en états d'étapes. Aucune supposition. */
  reconcile(result) {
    const s = this.state;
    s.result = result;
    const failed = (result.results || []).find((r) => r.status !== 'VERIFIED');
    const verified = (result.results || []).filter((r) => r.status === 'VERIFIED');
    s.applied = result.results || [];

    if (result.error === 'STALE_COMPARISON') {
      this.setStep('prepare', 'SUCCESS');
      this.setStep('hash', 'ERROR');
      return 'STALE_COMPARISON';
    }
    if (result.error && !result.results?.length) {
      // Refus en amont (confirmation, lot, sélection…) : rien n'a été écrit.
      this.setStep('prepare', result.backup_path ? 'SUCCESS' : 'ERROR');
      if (result.backup_path) this.setStep('backup', 'SUCCESS');
      this.setStep('write', 'ERROR');
      return result.error;
    }
    this.setStep('prepare', 'SUCCESS');
    this.setStep('backup', result.backup_path ? 'SUCCESS' : 'ERROR');
    this.setStep('hash', 'SUCCESS');           // franchi : le backend a dépassé le contrôle
    if (failed && failed.status === 'FAILED' && /écriture|write/i.test(failed.reason || '')) {
      this.setStep('write', 'ERROR');
      return 'WRITE_FAILED';
    }
    this.setStep('write', verified.length || failed ? 'SUCCESS' : 'ERROR');
    this.setStep('reread', failed && failed.status === 'FAILED' && !failed.after_hash
      ? 'ERROR' : 'SUCCESS');
    this.setStep('verify', failed ? 'ERROR' : 'SUCCESS');
    this.setStep('done', failed ? 'ERROR' : 'SUCCESS');
    return failed ? (failed.status === 'ROLLED_BACK' ? 'VERIFY_FAILED_ROLLED_BACK'
                                                     : 'VERIFY_FAILED') : 'OK';
  },

  /* ------------------------------------------------------------ résultats - */
  async finish(result, { onCompare, onRetry, onRecompare } = {}) {
    const code = this.reconcile(result);
    this.render();
    await this.wait(this.reduced() ? 0 : 420);
    this.close();
    const seconds = ((performance.now() - this.state.startedAt) / 1000).toFixed(1);
    if (code === 'OK') {
      this.success(result, seconds, onCompare);
    } else {
      this.failure(result, code, { onRetry, onRecompare });
    }
  },

  wait(ms) { return new Promise((r) => setTimeout(r, ms)); },

  success(result, seconds, onCompare) {
    const s = this.state;
    const done = result.results || [];
    const created = done.filter((r) => r.action === 'CREATE');
    const updated = done.filter((r) => r.action === 'UPDATE');
    const entry = s.entries.find((e) => e.identity === (done[0] || {}).identity) || s.entries[0];
    const plan = s.plan || {};
    const remainingCreate = Math.max(0, (plan.counts?.creates || 0) - created.length);
    const remainingUpdate = Math.max(0, (plan.counts?.updates || 0) - updated.length);
    const before = result.site_total_before;
    const after = result.site_total_after;

    const card = entry.action === 'CREATE'
      ? `<div class="sf-card create ${this.reduced() ? 'still' : ''}">
          <div class="sf-card-badge">+ CRÉÉ</div>
          <div class="sf-card-top">${this.thumb(entry)}
            <div><b>${esc(entry.identity)}</b>
            <small>${esc(entry.proposed_values?.rarity || '—')}</small></div></div>
          <div class="sf-kv"><span>Income</span><span>${esc(awVal(entry.proposed_values?.income))}</span></div>
          <div class="sf-kv"><span>Cost</span><span>${esc(awVal(entry.proposed_values?.cost))}</span></div>
        </div>`
      : `<div class="sf-diff">${(entry.changed_fields || []).map((f) => `
          <div class="sf-diff-row">
            <span class="sf-diff-field">${esc(f.site_field)}</span>
            <span class="sf-old ${this.reduced() ? 'still' : ''}">${esc(awVal(f.site))}</span>
            <span class="sf-arrow">→</span>
            <span class="sf-new ${this.reduced() ? 'still' : ''}">${esc(awVal(f.sheet))}</span>
          </div>`).join('')}</div>`;

    const m = modal({
      title: '✓ Synchronisation réussie',
      body: `<div class="sf-result ok">
          ${card}
          <p class="sf-lead">${esc(entry.identity)} a été
            ${entry.action === 'CREATE' ? 'ajouté' : 'mis à jour'} avec succès.</p>
          <div class="sf-summary">
            <div><span>Action</span><b>${esc(entry.action)}</b></div>
            <div><span>Champs modifiés</span><b>${esc((done[0]?.changed_fields || []).join(', ') || '—')}</b></div>
            <div><span>Vérification serveur</span><b class="ok">VERIFIED</b></div>
            ${before !== undefined && after !== undefined
              ? `<div><span>Site</span><b>${before} → ${after} entrées</b></div>` : ''}
            <div><span>CREATE restants</span><b>${remainingCreate}</b></div>
            <div><span>UPDATE restants</span><b>${remainingUpdate}</b></div>
            <div><span>Durée</span><b>${seconds}s</b></div>
            <div class="wide"><span>Sauvegarde</span><b>${esc(result.backup_path || '—')}</b></div>
          </div>
        </div>`,
      footer: `<button class="btn" data-compare>Voir dans la comparaison</button>
               <button class="btn primary" data-close>Fermer</button>`,
    });
    m.$('[data-compare]')?.addEventListener('click', () => { m.close(); onCompare?.(); });
    this.toast('Modification vérifiée sur le serveur', 'success');
  },

  failure(result, code, { onRetry, onRecompare } = {}) {
    const failed = (result.results || []).find((r) => r.status !== 'VERIFIED');
    const rolled = failed?.status === 'ROLLED_BACK';
    const restored = rolled && failed?.rollback !== false;
    const messages = {
      STALE_COMPARISON: 'Le fichier distant a changé depuis la comparaison.',
      CONFIRMATION_REQUIRED: 'La confirmation d\'écriture n\'a pas été accordée.',
      CONFIRMATION_PLAN_MISMATCH: 'La confirmation ne correspond pas à ce plan.',
      BATCH_DISABLED: 'Le mode lot est désactivé : une seule entrée à la fois.',
      NOTHING_APPLICABLE: 'Aucune entrée sélectionnée n\'est applicable.',
      WRITE_FAILED: 'L\'écriture sur le serveur a échoué.',
      VERIFY_FAILED: 'La vérification serveur a échoué.',
      VERIFY_FAILED_ROLLED_BACK: 'La vérification serveur a échoué. Rollback effectué avec succès.',
    };
    const step = this.STEPS.find((s) => this.state.steps[s.id] === 'ERROR');
    const m = modal({
      title: '✕ Synchronisation échouée',
      body: `<div class="sf-result err">
          <div class="sf-err-head">${awIcon('alert', 22)}
            <div><b>${esc(messages[code] || result.error || 'Erreur inconnue')}</b>
            <small>Code : ${esc(result.error || code)}</small></div></div>
          <div class="sf-summary">
            <div><span>Étape en échec</span><b>${esc(step ? step.label : '—')}</b></div>
            <div><span>Rollback effectué</span><b class="${rolled ? 'warn' : ''}">${rolled ? 'OUI' : 'NON'}</b></div>
            <div><span>Production restaurée</span><b class="${restored ? 'ok' : 'warn'}">${
              rolled ? (restored ? 'OUI' : 'NON') : 'AUCUNE ÉCRITURE'}</b></div>
            <div><span>sync_id</span><b>${esc(result.sync_id || '—')}</b></div>
            ${result.backup_path ? `<div class="wide"><span>Sauvegarde</span><b>${esc(result.backup_path)}</b></div>` : ''}
          </div>
          <details class="sf-details"><summary>Voir les détails</summary>
            <pre>${esc((result.results || []).map((r) =>
              `${r.identity} · ${r.action} · ${r.status}${r.reason ? ' · ' + r.reason : ''}`)
              .join('\n') || result.detail || 'Aucun détail supplémentaire.')}</pre>
          </details>
        </div>`,
      footer: `<button class="btn" data-recompare>Relancer la comparaison</button>
               <button class="btn" data-retry>Réessayer</button>
               <button class="btn primary" data-close>Fermer</button>`,
    });
    m.$('[data-retry]')?.addEventListener('click', () => { m.close(); onRetry?.(); });
    m.$('[data-recompare]')?.addEventListener('click', () => { m.close(); onRecompare?.(); });
    // Une erreur ne disparaît pas toute seule : elle reste jusqu'à fermeture.
    this.toast(messages[code] || 'Synchronisation échouée', 'error');
  },

  /* -------------------------------------------------------------- toasts -- */
  toast(message, kind = 'info') {
    let box = document.getElementById('sfToasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'sfToasts';
      box.className = 'sf-toasts';
      document.body.appendChild(box);
    }
    const delays = { success: 4000, info: 4000, warning: 6000, error: 0 };
    const icons = { success: 'check', info: 'info', warning: 'alert', error: 'alert' };
    const el = document.createElement('div');
    el.className = `sf-toast ${kind}`;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.innerHTML = `${awIcon(icons[kind] || 'info', 15)}
      <span>${esc(message)}</span>
      <button class="sf-toast-x" aria-label="Fermer">${awIcon('x', 12)}</button>`;
    const dismiss = () => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 220);
    };
    el.querySelector('.sf-toast-x').onclick = dismiss;
    box.appendChild(el);
    const delay = delays[kind] ?? 4000;
    if (delay) setTimeout(dismiss, delay);
    return { dismiss };
  },
};

window.SyncFeedback = SyncFeedback;
