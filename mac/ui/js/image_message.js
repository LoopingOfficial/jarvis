/* ==========================================================================
   GeneratingImageMessage — bulle de génération d'image dans la conversation.

   Cycle piloté UNIQUEMENT par les événements réels du backend :
     image.generation.started   → bulle + squelette shimmer
     image.generation.queued    → « En file d'attente »
     image.generation.progress  → barre + étape (Préparation / Génération /
                                  Rendu final / Finalisation)
     image.generation.preview   → aperçu intermédiaire (fondu progressif)
     image.generation.completed → image finale + actions
     image.generation.failed    → état d'erreur explicite (jamais de faux succès)

   La bulle est rendue dans les DEUX flux de conversation (dock console et
   panneau conversation) : une même carte, deux points d'affichage.
   ========================================================================== */
const ImageMessages = {
  /** jobId → { cards: [HTMLElement], job: {...} } */
  jobs: new Map(),

  containers() {
    return [document.getElementById('convLog'), document.getElementById('consoleLog')]
      .filter(Boolean);
  },

  /* -------------------------------------------------------------- gabarit */
  template(job) {
    const label = job.mode === 'edit' ? 'Retouche'
      : job.mode === 'upscale' ? 'Agrandissement' : 'Génération d\'image';
    const model = job.model?.name || job.model?.id || '';
    const quality = job.meta?.generation_profile?.quality_mode || '';
    const engineMode = job.engine_mode || job.meta?.engine_mode || 'auto';
    const engineLabel = job.engine_label || job.meta?.generation_profile?.engine_label
      || (engineMode === 'quality' ? 'SDXL Quality' : 'Z-Image-Turbo Fast');
    const modeLabel = engineMode === 'quality' ? 'Rendu final qualité SDXL'
      : engineMode === 'fast' ? 'Prévisualisation rapide' : 'Sélection automatique';
    return `
      <div class="who">JARVIS</div>
      <div class="bubble imgbubble">
        <div class="imgcard" data-state="pending">
          <div class="imgcard-head">
            <span class="imgcard-kicker">${label}</span>
            <span class="imgcard-engine" data-engine>${esc(engineLabel)}</span>
          </div>
          <div class="imgcard-meta"><span data-mode>${esc(modeLabel)}</span>${model ? ` · <span data-model>${esc(model)}</span>` : ''}${quality ? ` · ${esc(quality)}` : ''}</div>

          <div class="imgcard-stage" data-stage-wrap>
            <span class="imgcard-spinner" aria-hidden="true"></span>
            <span data-stage>Préparation de l'image</span>
            <span class="imgcard-pct" data-pct></span>
          </div>

          <div class="imgcard-bar"><i data-bar style="width:2%"></i></div>

          <div class="imgcard-frame" data-frame>
            <div class="imgcard-skeleton" data-skeleton>
              <span class="imgcard-shimmer"></span>
              <span class="imgcard-grain"></span>
            </div>
            <img class="imgcard-img" data-preview alt="" hidden />
            <img class="imgcard-img final" data-final alt="" hidden />
          </div>

          <div class="imgcard-prompt"><span class="text-faint">Demande :</span> <span data-prompt title=""></span></div>
          <div class="imgcard-quality" data-quality hidden></div>
          <div class="imgcard-error" data-error hidden></div>
          <div class="imgcard-actions" data-actions hidden></div>
        </div>
      </div>`;
  },

  /* ------------------------------------------------------------- création */
  ensure(job) {
    let entry = this.jobs.get(job.job_id);
    if (entry) return entry;

    // La bulle « … » d'attente laisse la place à la carte image.
    window.App?.clearPendingReply?.();

    const cards = [];
    this.containers().forEach((container) => {
      const el = document.createElement('div');
      el.className = 'msg imgmsg';
      el.dataset.imageJob = job.job_id;
      el.innerHTML = this.template(job);
      container.appendChild(el);
      container.scrollTop = container.scrollHeight;
      cards.push(el);
    });
    entry = { cards, job: { ...job } };
    this.jobs.set(job.job_id, entry);
    this.setPrompt(entry, job.prompt);
    return entry;
  },

  each(jobId, fn) {
    const entry = this.jobs.get(jobId);
    if (!entry) return null;
    entry.cards.forEach((card) => {
      const card_ = card.querySelector('.imgcard');
      if (card_) fn(card_, card);
    });
    return entry;
  },

  setPrompt(entry, prompt) {
    if (!prompt) return;
    entry.cards.forEach((card) => {
      const el = card.querySelector('[data-prompt]');
      if (!el) return;
      el.textContent = prompt.length > 150 ? prompt.slice(0, 150) + '…' : prompt;
      el.title = prompt;
    });
  },

  details(d) {
    const meta = d.meta || {};
    const model = meta.model || {};
    const profile = meta.generation_profile || {};
    const m = modal({title: 'Détails de génération', wide: true, body: `<div class="mono" style="white-space:pre-wrap;line-height:1.55">
      Demande originale : ${esc(meta.original_prompt || d.original_prompt || d.prompt || '')}
      \nPrompt généré : ${esc(meta.final_prompt || d.prompt || '')}
      \nNegative prompt : ${esc(meta.negative_prompt || d.negative_prompt || '')}
      \nModèle : ${esc(model.name || d.backend_label || d.backend || '—')}
      \nMoteur : ${esc(d.engine_label || meta.generation_profile?.engine_label || d.engine_mode || 'auto')}
      \nRaison du choix : ${esc(d.engine_reason || meta.generation_profile?.engine_reason || '—')}
      \nWorkflow : ${esc(meta.workflow || '—')}
      \nSeed : ${esc(String(d.seed ?? profile.seed ?? '—'))} · Résolution : ${esc(`${d.width || profile.width || '—'}×${d.height || profile.height || '—'}`)}
      \nSteps : ${esc(String(d.steps ?? profile.steps ?? '—'))} · CFG : ${esc(String(profile.cfg ?? '—'))}
      \nSampler : ${esc(profile.sampler || '—')} · Scheduler : ${esc(profile.scheduler || '—')}
    </div>`, footer: '<button class="btn" data-close>Fermer</button>'});
    return m;
  },

  /* --------------------------------------------------------- transitions */
  started(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'running';
      const engine = card.querySelector('[data-engine]');
      if (engine) engine.textContent = d.engine_label || (d.engine_mode === 'quality' ? 'SDXL Quality' : 'Z-Image-Turbo Fast');
      const mode = card.querySelector('[data-mode]');
      if (mode) mode.textContent = d.engine_mode === 'quality' ? 'Rendu final qualité SDXL'
        : d.engine_mode === 'fast' ? 'Prévisualisation rapide' : 'Sélection automatique';
      const model = card.querySelector('[data-model]');
      if (model && (d.model?.name || d.model?.id)) model.textContent = d.model.name || d.model.id;
      const actions = card.querySelector('[data-actions]');
      if (actions) {
        actions.hidden = false;
        actions.innerHTML = '<button class="btn sm danger" data-cancel>Annuler</button>';
        const cancel = actions.querySelector('[data-cancel]');
        if (cancel) cancel.onclick = async () => {
          cancel.disabled = true;
          await J.post(`/api/images/${d.job_id}/cancel`);
          cancel.textContent = 'Annulation…';
        };
      }
    });
  },

  queued(d) {
    this.ensure(d);
    this.each(d.job_id, (card) => {
      const stage = card.querySelector('[data-stage]');
      if (stage) stage.textContent = 'En file d\'attente';
    });
  },

  progress(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d };
    const pct = Math.max(2, Math.round((d.progress || 0) * 100));
    this.each(d.job_id, (card) => {
      card.dataset.state = 'running';
      const bar = card.querySelector('[data-bar]');
      const stage = card.querySelector('[data-stage]');
      const pctEl = card.querySelector('[data-pct]');
      const engine = card.querySelector('[data-engine]');
      if (bar) bar.style.width = pct + '%';
      if (stage && d.stage_label) stage.textContent = d.stage_label;
      if (pctEl) pctEl.textContent = pct + '%';
      if (engine && d.backend_label) engine.textContent = d.backend_label;
    });
    this.setPrompt(entry, d.prompt);
  },

  preview(d) {
    this.ensure(d);
    if (!d.preview) return;
    this.each(d.job_id, (card) => {
      const img = card.querySelector('[data-preview]');
      const skeleton = card.querySelector('[data-skeleton]');
      if (!img) return;
      img.src = d.preview;
      img.hidden = false;
      img.classList.add('visible');
      if (skeleton) skeleton.classList.add('fading');
      card.dataset.hasPreview = '1';
    });
  },

  quality(d) {
    const entry = this.ensure(d);
    const q = d.quality || {};
    if (q.overall == null) return;
    this.each(d.job_id, (card) => {
      const el = card.querySelector('[data-quality]');
      if (!el) return;
      el.hidden = false;
      el.textContent = `Sujet ${Math.round((q.subject_match || 0) * 100)} · Netteté ${Math.round((q.sharpness || 0) * 100)} · Composition ${Math.round((q.composition || 0) * 100)}`;
    });
  },

  completed(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d, status: 'completed' };
    const url = d.url || `/api/images/${d.job_id}/file`;
    this.each(d.job_id, (card) => {
      const final = card.querySelector('[data-final]');
      const preview = card.querySelector('[data-preview]');
      const skeleton = card.querySelector('[data-skeleton]');
      const bar = card.querySelector('[data-bar]');
      const stage = card.querySelector('[data-stage]');
      const pctEl = card.querySelector('[data-pct]');
      if (bar) bar.style.width = '100%';
      if (stage) stage.textContent = 'Image prête';
      if (pctEl) pctEl.textContent = '';
      if (final) {
        final.onload = () => {
          final.classList.add('visible');
          if (preview) preview.classList.remove('visible');
          if (skeleton) skeleton.classList.add('gone');
          card.dataset.state = 'done';
        };
        final.src = url + '?t=' + Date.now();
        final.hidden = false;
      }
      this.renderActions(card, { ...d, url });
    });
    this.scrollAll();
  },

  failed(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d, status: 'failed' };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'error';
      const stage = card.querySelector('[data-stage]');
      const err = card.querySelector('[data-error]');
      const skeleton = card.querySelector('[data-skeleton]');
      if (stage) stage.textContent = 'Génération interrompue';
      if (skeleton) skeleton.classList.add('gone');
      if (err) {
        err.hidden = false;
        err.textContent = d.error || 'Le moteur image ne répond pas actuellement.';
      }
      this.renderActions(card, d, true);
    });
  },

  cancelled(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d, status: 'cancelled' };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'error';
      const stage = card.querySelector('[data-stage]');
      const err = card.querySelector('[data-error]');
      if (stage) stage.textContent = 'Génération annulée';
      if (err) { err.hidden = false; err.textContent = 'Génération annulée.'; }
    });
  },

  /* ------------------------------------------------------------- actions */
  renderActions(card, d, isError = false) {
    const box = card.querySelector('[data-actions]');
    if (!box) return;
    box.hidden = false;
    if (isError) {
      const quality = (d.engine_mode || d.meta?.engine_mode) === 'quality';
      box.innerHTML = `<button class="btn sm" data-retry>Réessayer</button>${quality ? '<button class="btn sm" data-fast-fallback>Relancer en fast</button>' : ''}`;
      const retry = box.querySelector('[data-retry]');
      if (retry) {
        retry.onclick = () => window.App?.send(
          (quality ? 'Rends à nouveau cette image en SDXL qualité : ' : 'Génère à nouveau cette image : ') + (d.prompt || '').slice(0, 300));
      }
      const fallback = box.querySelector('[data-fast-fallback]');
      if (fallback) fallback.onclick = () => window.App?.send(
        'Génère cette image en version rapide : ' + (d.prompt || '').slice(0, 300));
      return;
    }
    const url = d.url || `/api/images/${d.job_id}/file`;
    box.innerHTML = `
      <a class="btn sm" href="${url}" target="_blank" rel="noopener">Ouvrir</a>
      <button class="btn sm" data-edit>Retoucher</button>
      <button class="btn sm" data-upscale>Agrandir</button>
      <button class="btn sm" data-variation>Variante</button>
      <button class="btn sm" data-improve>Améliorer</button>
      ${(d.engine_mode || d.meta?.engine_mode) === 'fast' ? '<button class="btn sm" data-sdxl>Rendre en SDXL</button>' : ''}
      <button class="btn sm" data-details>Détails</button>`;
    const edit = box.querySelector('[data-edit]');
    const up = box.querySelector('[data-upscale]');
    const variation = box.querySelector('[data-variation]');
    const improve = box.querySelector('[data-improve]');
    const sdxl = box.querySelector('[data-sdxl]');
    const details = box.querySelector('[data-details]');
    if (edit) edit.onclick = () => window.App?.send('Retouche cette image : ');
    if (up) up.onclick = () => window.App?.send('Agrandis cette image');
    if (variation) variation.onclick = () => window.App?.send('Crée une variante de cette image');
    if (improve) improve.onclick = () => window.App?.send('Améliore automatiquement cette image');
    if (sdxl) sdxl.onclick = () => window.App?.send('Rends cette image en SDXL qualité');
    if (details) details.onclick = () => this.details({ ...d, ...(this.jobs.get(d.job_id)?.job || {}) });
    const final = card.querySelector('[data-final]');
    if (final) final.onclick = () => window.open(url, '_blank', 'noopener');
  },

  scrollAll() {
    this.containers().forEach((c) => { c.scrollTop = c.scrollHeight; });
  },

  /* ------------------------------------- restauration depuis l'historique */
  /** Rejoue une image terminée quand on recharge une conversation. */
  restore(job) {
    if (!job || !job.id) return;
    const payload = {
      job_id: job.id, prompt: job.prompt, original_prompt: (job.meta || {}).original_prompt || job.prompt, meta: job.meta || {}, mode: job.mode,
      backend_label: job.backend_label || job.backend, status: job.status,
      stage: job.stage, stage_label: job.stage_label, progress: job.progress,
      url: job.url, error: job.error,
    };
    if (job.status === 'completed') {
      this.started(payload);
      this.completed(payload);
    } else if (job.status === 'failed') {
      this.started(payload);
      this.failed(payload);
    }
  },

  reset() {
    this.jobs.clear();
  },

  /* ------------------------------------------------------------- câblage */
  bind() {
    J.on('image.generation.started', (d) => this.started(d));
    J.on('image.generation.queued', (d) => this.queued(d));
    J.on('image.generation.progress', (d) => this.progress(d));
    J.on('image.generation.preview', (d) => this.preview(d));
    J.on('image.generation.quality', (d) => this.quality(d));
    J.on('image.generation.completed', (d) => this.completed(d));
    J.on('image.generation.failed', (d) => this.failed(d));
    J.on('image.generation.cancelled', (d) => this.cancelled(d));
  },
};

window.ImageMessages = ImageMessages;
