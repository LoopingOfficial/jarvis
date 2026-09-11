/* ==========================================================================
   ModelMessages — bulle « atelier 3D » dans la conversation.

   Cycle piloté UNIQUEMENT par les événements réels du backend :
     blender.job.started    → carte + liste des étapes
     blender.job.progress   → étape courante + barre (progression réelle)
     blender.geometry/…     → étape cochée
     blender.job.completed  → aperçu PNG + viewer GLB + actions
     blender.job.failed     → erreur explicite (jamais de faux succès)
     blender.job.cancelled  → état annulé

   Le viewer 3D n'est instancié qu'à la demande (un contexte WebGL coûte cher).
   ========================================================================== */
const MODEL_STAGES = [
  ['preparing', 'Préparation'],
  ['geometry', 'Géométrie'],
  ['materials', 'Matériaux'],
  ['textures', 'Textures'],
  ['rig', 'Rig'],
  ['animation', 'Animation'],
  ['optimize', 'Optimisation'],
  ['rendering', 'Rendu'],
  ['exporting', 'Export'],
  ['preview', 'Aperçu'],
];
const STAGE_RANK = Object.fromEntries(MODEL_STAGES.map(([id], i) => [id, i]));

const ModelMessages = {
  /** jobId → { cards: [HTMLElement], job: {...}, viewers: Map<HTMLElement, viewer> } */
  jobs: new Map(),

  containers() {
    return [document.getElementById('convLog'), document.getElementById('consoleLog')]
      .filter(Boolean);
  },

  esc(value) {
    const d = document.createElement('span');
    d.textContent = String(value ?? '');
    return d.innerHTML;
  },

  /* -------------------------------------------------------------- gabarit */
  template(job) {
    const steps = MODEL_STAGES.map(([id, label]) =>
      `<li data-step="${id}"><i></i><span>${label}</span></li>`).join('');
    return `
      <div class="who">JARVIS</div>
      <div class="bubble m3dbubble">
        <div class="m3dcard" data-state="pending">
          <div class="m3dcard-head">
            <span class="m3dcard-kicker">Atelier 3D · Blender</span>
            <span class="m3dcard-title" data-title>${this.esc(job.title || '')}</span>
          </div>

          <div class="m3dcard-stage" data-stage-wrap>
            <span class="m3dcard-spinner" aria-hidden="true"></span>
            <span data-stage>Préparation</span>
            <span class="m3dcard-pct" data-pct></span>
          </div>
          <div class="m3dcard-bar"><i data-bar style="width:3%"></i></div>
          <ul class="m3dcard-steps" data-steps>${steps}</ul>

          <div class="m3dcard-frame" data-frame>
            <div class="m3dcard-skeleton" data-skeleton>
              <span class="m3dcard-grid"></span>
              <span class="m3dcard-shimmer"></span>
            </div>
            <img class="m3dcard-preview" data-preview alt="" hidden />
            <div class="m3dcard-stage3d" data-stage3d hidden>
              <canvas data-canvas></canvas>
              <div class="m3dcard-hud" data-hud></div>
              <div class="m3dcard-loading" data-viewer-loading hidden>Chargement du GLB…</div>
            </div>
          </div>

          <div class="m3dcard-facts" data-facts></div>
          <div class="m3dcard-error" data-error hidden></div>
          <div class="m3dcard-actions" data-actions hidden></div>
        </div>
      </div>`;
  },

  /* ------------------------------------------------------------- création */
  ensure(job) {
    let entry = this.jobs.get(job.job_id);
    if (entry) return entry;
    window.App?.clearPendingReply?.();

    const cards = [];
    this.containers().forEach((container) => {
      const el = document.createElement('div');
      el.className = 'msg m3dmsg';
      el.dataset.modelJob = job.job_id;
      el.innerHTML = this.template(job);
      container.appendChild(el);
      container.scrollTop = container.scrollHeight;
      cards.push(el);
    });
    entry = { cards, job: { ...job }, viewers: new Map() };
    this.jobs.set(job.job_id, entry);
    return entry;
  },

  each(jobId, fn) {
    const entry = this.jobs.get(jobId);
    if (!entry) return null;
    entry.cards.forEach((wrapper) => {
      const card = wrapper.querySelector('.m3dcard');
      if (card) fn(card, wrapper);
    });
    return entry;
  },

  /* --------------------------------------------------------- transitions */
  started(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'running';
      const title = card.querySelector('[data-title]');
      if (title && d.title) title.textContent = d.title;
    });
  },

  progress(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d };
    const pct = Math.max(3, Math.round((d.progress || 0) * 100));
    this.each(d.job_id, (card) => {
      card.dataset.state = 'running';
      const bar = card.querySelector('[data-bar]');
      const stage = card.querySelector('[data-stage]');
      const pctEl = card.querySelector('[data-pct]');
      if (bar) bar.style.width = pct + '%';
      if (stage) stage.textContent = d.stage_label || d.message || 'En cours';
      if (pctEl) pctEl.textContent = pct + '%';
      this.markSteps(card, d.stage);
    });
  },

  /** Coche toutes les étapes atteintes, marque l'étape courante. */
  markSteps(card, stage) {
    const rank = STAGE_RANK[stage];
    card.querySelectorAll('[data-step]').forEach((li) => {
      const own = STAGE_RANK[li.dataset.step];
      if (rank === undefined) return;
      li.classList.toggle('done', own < rank);
      li.classList.toggle('active', own === rank);
    });
  },

  stageDone(stage, d) {
    this.each(d.job_id, (card) => {
      const li = card.querySelector(`[data-step="${stage}"]`);
      if (li) { li.classList.add('done'); li.classList.remove('active'); }
    });
  },

  completed(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d, status: 'completed' };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'done';
      const bar = card.querySelector('[data-bar]');
      const stage = card.querySelector('[data-stage]');
      const pctEl = card.querySelector('[data-pct]');
      if (bar) bar.style.width = '100%';
      if (stage) stage.textContent = 'Modèle prêt';
      if (pctEl) pctEl.textContent = '';
      card.querySelectorAll('[data-step]').forEach((li) => {
        li.classList.remove('active');
        li.classList.add('done');
      });
      const preview = card.querySelector('[data-preview]');
      const skeleton = card.querySelector('[data-skeleton]');
      if (preview && d.preview_url) {
        preview.onload = () => {
          preview.classList.add('visible');
          if (skeleton) skeleton.classList.add('gone');
        };
        preview.src = d.preview_url + '?t=' + Date.now();
        preview.hidden = false;
      } else if (skeleton) {
        skeleton.classList.add('gone');
      }
      this.renderFacts(card, d.meta || entry.job.meta || {});
      this.renderActions(card, d);
    });
    this.scrollAll();
  },

  failed(d) {
    const entry = this.ensure(d);
    entry.job = { ...entry.job, ...d, status: d.status || 'failed' };
    this.each(d.job_id, (card) => {
      card.dataset.state = 'error';
      const stage = card.querySelector('[data-stage]');
      const err = card.querySelector('[data-error]');
      const skeleton = card.querySelector('[data-skeleton]');
      if (stage) stage.textContent = d.status === 'cancelled'
        ? 'Job annulé' : 'Création interrompue';
      if (skeleton) skeleton.classList.add('gone');
      card.querySelectorAll('[data-step].active').forEach((li) => li.classList.add('failed'));
      if (err) {
        err.hidden = false;
        err.textContent = d.error || 'Blender n\'a pas terminé le job.';
      }
      const actions = card.querySelector('[data-actions]');
      if (actions) {
        actions.hidden = false;
        actions.innerHTML = '<button class="btn sm" data-retry>Réessayer</button>';
        actions.querySelector('[data-retry]').onclick = () =>
          window.App?.send('Reprends la création 3D : ' + (d.title || '').slice(0, 200));
      }
    });
  },

  /* --------------------------------------------------------------- détail */
  renderFacts(card, meta) {
    const box = card.querySelector('[data-facts]');
    if (!box) return;
    const facts = [];
    if (meta.kind) facts.push(this.esc(meta.kind));
    if (meta.polycount) facts.push(`${meta.polycount} triangles`);
    if (meta.materials?.length) facts.push(`${meta.materials.length} matériaux`);
    if (meta.rig?.rigged) facts.push(`rig ${meta.rig.bones} os`);
    if (meta.animations?.length) facts.push(`${meta.animations.length} animation(s)`);
    const glb = meta.glb || {};
    if (glb.size) facts.push(`GLB ${Math.round(glb.size / 1024)} Ko`);
    if (meta.duration_s) facts.push(`${meta.duration_s}s`);
    box.innerHTML = facts.map((f) => `<span>${f}</span>`).join('');
  },

  renderActions(card, d) {
    const box = card.querySelector('[data-actions]');
    if (!box) return;
    box.hidden = false;
    const glb = d.glb_url || '';
    box.innerHTML = `
      ${glb ? '<button class="btn sm primary" data-view3d>Vue 3D</button>' : ''}
      ${glb ? `<a class="btn sm" href="${glb}" download>Télécharger le GLB</a>` : ''}
      <button class="btn sm" data-render>Rendu réaliste</button>
      <button class="btn sm" data-animate>Animer</button>`;

    const view = box.querySelector('[data-view3d]');
    if (view) view.onclick = () => this.toggleViewer(card, d, view);
    box.querySelector('[data-render]').onclick = () =>
      window.App?.send('Fais un rendu réaliste de ce modèle');
    box.querySelector('[data-animate]').onclick = () =>
      window.App?.send('Anime ce modèle');
  },

  /* -------------------------------------------------------------- viewer */
  async toggleViewer(card, d, button) {
    const stage3d = card.querySelector('[data-stage3d]');
    const preview = card.querySelector('[data-preview]');
    if (!stage3d) return;

    if (!stage3d.hidden) {              // repli : on revient à l'aperçu
      stage3d.hidden = true;
      if (preview) preview.hidden = false;
      button.textContent = 'Vue 3D';
      return;
    }
    stage3d.hidden = false;
    if (preview) preview.hidden = true;
    button.textContent = 'Aperçu';

    if (card._viewer) return;           // déjà instancié pour cette carte
    const loading = card.querySelector('[data-viewer-loading]');
    if (loading) loading.hidden = false;
    try {
      await this.ensureViewerModule();
      const canvas = card.querySelector('[data-canvas]');
      const viewer = window.createModelViewer(canvas, { transparent: false });
      card._viewer = viewer;
      const info = await viewer.load(d.glb_url);
      if (loading) loading.hidden = true;
      this.renderHud(card, viewer, info);
    } catch (err) {
      if (loading) {
        loading.hidden = false;
        loading.textContent = 'Viewer 3D indisponible : ' + (err?.message || err);
      }
    }
  },

  /** Charge le module ES du viewer une seule fois, à la demande. */
  ensureViewerModule() {
    if (window.createModelViewer) return Promise.resolve();
    if (this._modulePromise) return this._modulePromise;
    this._modulePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.type = 'module';
      script.src = '/js/model_viewer.js';
      script.onerror = () => reject(new Error('module viewer introuvable'));
      window.addEventListener('jarvis:model-viewer-ready', () => resolve(), { once: true });
      document.head.appendChild(script);
      setTimeout(() => {
        if (window.createModelViewer) resolve();
        else reject(new Error('WebGL ou Three.js indisponible'));
      }, 8000);
    });
    return this._modulePromise;
  },

  renderHud(card, viewer, info) {
    const hud = card.querySelector('[data-hud]');
    if (!hud) return;
    const clips = info.animations || [];
    hud.innerHTML = `
      <div class="m3dhud-row">
        <button class="m3dhud-btn" data-reset title="Recentrer la caméra">Reset</button>
        <button class="m3dhud-btn" data-wire title="Wireframe">Wireframe</button>
        <button class="m3dhud-btn" data-bg title="Fond transparent">Fond</button>
        <button class="m3dhud-btn" data-full title="Plein écran">Plein écran</button>
      </div>
      ${clips.length ? `<div class="m3dhud-row">
        <button class="m3dhud-btn" data-play>Pause</button>
        <select class="m3dhud-select" data-clip>
          ${clips.map((c, i) => `<option value="${i}">${this.esc(c)}</option>`).join('')}
        </select>
      </div>` : ''}
      <div class="m3dhud-info">${info.meshes} mesh · ${info.triangles} tris ·
        ${info.materials} mat.${info.skinned ? ' · skinné' : ''}</div>`;

    hud.querySelector('[data-reset]').onclick = () => viewer.resetCamera();
    const wire = hud.querySelector('[data-wire]');
    wire.onclick = () => {
      viewer.setWireframe(!viewer.wireframe);
      wire.classList.toggle('on', viewer.wireframe);
    };
    const bg = hud.querySelector('[data-bg]');
    bg.onclick = () => {
      viewer.setTransparent(!viewer.transparent);
      bg.classList.toggle('on', viewer.transparent);
    };
    hud.querySelector('[data-full]').onclick = () => viewer.fullscreen();
    const play = hud.querySelector('[data-play]');
    if (play) {
      play.onclick = () => { play.textContent = viewer.togglePlay() ? 'Pause' : 'Lecture'; };
    }
    const clip = hud.querySelector('[data-clip]');
    if (clip) {
      clip.onchange = () => {
        viewer.playClip(Number(clip.value));
        if (play) play.textContent = 'Pause';
      };
    }
  },

  scrollAll() {
    this.containers().forEach((c) => { c.scrollTop = c.scrollHeight; });
  },

  /* ------------------------------------ restauration depuis l'historique */
  restore(job) {
    if (!job || !job.id) return;
    const payload = {
      job_id: job.id, title: job.title, action: job.action, status: job.status,
      stage: job.stage, stage_label: job.stage_label, progress: job.progress,
      preview_url: job.preview_url, glb_url: job.glb_url, error: job.error,
      meta: job.meta,
    };
    this.started(payload);
    if (job.status === 'completed') this.completed(payload);
    else this.failed(payload);
  },

  reset() {
    this.jobs.forEach((entry) => {
      entry.cards.forEach((wrapper) => {
        const card = wrapper.querySelector('.m3dcard');
        if (card && card._viewer) { card._viewer.dispose(); card._viewer = null; }
      });
    });
    this.jobs.clear();
  },

  /* ------------------------------------------------------------- câblage */
  bind() {
    J.on('blender.job.started', (d) => this.started(d));
    J.on('blender.job.progress', (d) => this.progress(d));
    J.on('blender.job.completed', (d) => this.completed(d));
    J.on('blender.job.failed', (d) => this.failed(d));
    J.on('blender.job.cancelled', (d) => this.failed({ ...d, status: 'cancelled' }));
    ['geometry', 'materials', 'textures', 'rig', 'animation', 'optimize',
      'export', 'preview'].forEach((stage) => {
      const event = stage === 'export' ? 'blender.export.completed'
        : stage === 'preview' ? 'blender.preview.ready'
          : `blender.${stage}.completed`;
      J.on(event, (d) => this.stageDone(stage === 'export' ? 'exporting' : stage, d));
    });
    J.on('blender.render.started', (d) => this.progress({ ...d, stage: 'rendering' }));
    J.on('blender.render.completed', (d) => this.stageDone('rendering', d));
  },
};

window.ModelMessages = ModelMessages;
