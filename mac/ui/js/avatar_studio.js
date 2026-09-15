/* ==========================================================================
   Avatar Studio LIVE — atelier 3D où l'on REGARDE JARVIS construire son avatar.

   Tout ce qui s'affiche ici vient d'événements réels du worker Blender
   (`avatar.*` sur le flux SSE) et des fichiers qu'il a réellement écrits :

     avatar.job.started / progress / completed / failed / cancelled / paused
     avatar.stage.started / progress / completed / skipped
     avatar.operation            opération précise en cours
     avatar.preview.glb          nouvelle preview 3D → le viewer se recharge
     avatar.preview.render       nouveaux rendus face / 3-4 / profil / plein pied
     avatar.iteration.* / avatar.evaluation.completed

   Aucune progression n'est interpolée : le pourcentage vient du poids des
   étapes que Blender a réellement terminées. 100 % = job terminé + GLB final
   validé côté serveur.
   ========================================================================== */
const AvatarStudioLive = {
  BUILD: 'AVATAR_QUALITY_PIPELINE_20260911_A',
  STORAGE_KEY: 'jarvis.avatar.job',
  VIEWS: [
    ['front', 'Face'],
    ['three_quarter', '3/4'],
    ['side', 'Profil'],
    ['full_body', 'Plein pied'],
  ],
  OPTION_FIELDS: [
    { key: 'modify_face', label: 'Visage', on: true },
    { key: 'modify_hair', label: 'Cheveux', on: true },
    { key: 'modify_proportions', label: 'Corps', on: false },
    { key: 'modify_outfit', label: 'Tenue', on: true },
    { key: 'modify_materials', label: 'Matériaux', on: true },
  ],
  PRESERVE_FIELDS: [
    { key: 'preserve_rig', label: 'Conserver le rig', on: true },
    { key: 'preserve_identity', label: "Conserver l'identité", on: true },
  ],

  root: null,
  job: null,
  jobId: '',
  viewer: null,
  viewerLoading: false,
  pendingVersion: 0,
  selectedRef: '',
  refs: [],
  _bound: false,
  _viewerPromise: null,

  /* ------------------------------------------------------------- amorçage */
  init() {
    if (this._init) return;
    this._init = true;
    this.injectStyles();
    this.bind();
    try { this.jobId = localStorage.getItem(this.STORAGE_KEY) || ''; } catch { /* ok */ }
    console.log('[AvatarStudioLive] init', this.BUILD);
  },

  esc(v) { return typeof esc === 'function' ? esc(v) : String(v ?? ''); },

  /* ------------------------------------------------------------ événements */
  bind() {
    // `J` est un const lexical de core.js : il n'existe PAS sur window.
    if (this._bound || typeof J === 'undefined') return;
    this._bound = true;
    const own = (d) => d && d.job_id && (!this.jobId || d.job_id === this.jobId);

    J.on('avatar.job.started', (d) => {
      this.jobId = d.job_id;
      try { localStorage.setItem(this.STORAGE_KEY, this.jobId); } catch { /* ok */ }
      this.resync();
      this.updateSidebarBadge();
      this.updateCommandCard();
    });
    ['avatar.job.progress', 'avatar.stage.started', 'avatar.stage.completed',
     'avatar.stage.skipped', 'avatar.job.paused', 'avatar.job.resumed',
     'avatar.job.stalled', 'avatar.job.warning']
      .forEach((type) => J.on(type, (d) => { if (own(d)) this.onProgress(d); }));

    J.on('avatar.operation', (d) => { if (own(d)) this.onOperation(d); });
    J.on('avatar.preview.glb', (d) => { if (own(d)) this.onPreviewGlb(d); });
    J.on('avatar.preview.render', (d) => { if (own(d)) this.onPreviewRender(d); });
    J.on('avatar.evaluation.completed', (d) => { if (own(d)) this.resync(); });
    J.on('avatar.iteration.completed', (d) => { if (own(d)) this.resync(); });
    ['avatar.job.completed', 'avatar.job.failed', 'avatar.job.cancelled',
     'avatar.job.accepted', 'avatar.job.rejected']
      .forEach((type) => J.on(type, (d) => {
        if (!own(d)) return;
        this.resync();
        this.updateSidebarBadge();
        this.updateCommandCard();
      }));

    // Reconnexion SSE : on ne recharge JAMAIS JARVIS, on resynchronise le job.
    J.on('stream.open', () => { if (this.jobId) this.resync(); });
  },

  /* --------------------------------------------------------------- données */
  async resync() {
    if (!this.jobId) {
      const bundle = await J.get('/api/avatar/jobs?limit=1');
      const job = bundle.ok ? (bundle.active || (bundle.jobs || [])[0]) : null;
      if (!job) { this.render(); return; }
      this.jobId = job.id;
    }
    const res = await J.get(`/api/avatar/jobs/${encodeURIComponent(this.jobId)}`);
    if (!res.ok) { this.jobId = ''; this.render(); return; }
    this.job = res.job;
    try { localStorage.setItem(this.STORAGE_KEY, this.jobId); } catch { /* ok */ }
    this.render();
    this.syncViewer();
  },

  live(path, version) {
    if (!this.jobId) return '';
    const v = version ?? (this.job?.version || 0);
    return `/api/avatar/jobs/${encodeURIComponent(this.jobId)}/live/${path}?v=${v}`;
  },

  /* ------------------------------------------------------------ événements */
  onProgress(d) {
    if (!this.job) { this.resync(); return; }
    Object.assign(this.job, {
      status: d.status || this.job.status,
      stage: d.stage || this.job.stage,
      stage_label: d.stage_label || this.job.stage_label,
      progress: typeof d.progress === 'number' ? d.progress : this.job.progress,
      stalled: !!d.stalled,
    });
    if (d.message) this.job.operation = d.message;
    // La liste d'étapes détaillée vient du serveur : on la resynchronise, mais
    // sans bloquer l'affichage immédiat de la barre.
    this.paintProgress();
    clearTimeout(this._resyncTimer);
    this._resyncTimer = setTimeout(() => this.resync(), 400);
  },

  onOperation(d) {
    if (!this.job) return;
    this.job.operation = d.message || '';
    this.pushTimeline({ ts: Date.now() / 1000, message: d.message, kind: 'op' });
    const node = this.root?.querySelector('#alOperation');
    if (node) node.textContent = d.message || '—';
  },

  onPreviewGlb(d) {
    const version = Number(d.version || 0);
    if (!version) return;
    if (this.job) this.job.version = version;
    this.pendingVersion = version;
    this.pushTimeline({ ts: Date.now() / 1000, kind: 'preview',
      message: `Preview v${version} · ${d.label || ''}` });
    this.syncViewer();
    this.paintProgress();
  },

  onPreviewRender(d) {
    const version = Number(d.version || this.job?.version || 0);
    (d.views || []).forEach((view) => {
      const img = this.root?.querySelector(`[data-view="${view}"] img`);
      if (!img) return;
      const url = this.live(view.replace(/_/g, '-'), version);
      // Chargement hors écran : une preview indisponible ne casse pas la grille.
      const probe = new Image();
      probe.onload = () => {
        img.src = url;
        img.closest('.al-view')?.classList.remove('missing');
      };
      probe.onerror = () => { /* on garde la dernière image valide */ };
      probe.src = url;
    });
    const overlay = this.root?.querySelector('#alOverlayCurrent');
    if (overlay) overlay.src = this.live('front', version);
    const after = this.root?.querySelector('#alAfterImg');
    if (after) after.src = this.live('front', version);
  },

  pushTimeline(entry) {
    if (!this.job) return;
    (this.job.timeline = this.job.timeline || []).push(entry);
    const box = this.root?.querySelector('#alTimeline');
    if (box) {
      box.insertAdjacentHTML('afterbegin', this.timelineRow(entry));
      while (box.children.length > 120) box.lastElementChild.remove();
    }
  },

  /* ---------------------------------------------------------------- viewer */
  async ensureViewer() {
    if (window.createAvatarLiveViewer) return;
    if (this._viewerPromise) return this._viewerPromise;
    this._viewerPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.type = 'module';
      script.src = `/js/avatar_live_viewer.js?v=${this.BUILD}`;
      script.onerror = () => reject(new Error('viewer 3D indisponible'));
      window.addEventListener('jarvis:avatar-live-viewer-ready', () => resolve(),
        { once: true });
      document.head.appendChild(script);
      setTimeout(() => (window.createAvatarLiveViewer ? resolve()
        : reject(new Error('viewer 3D : délai dépassé'))), 12000);
    });
    return this._viewerPromise;
  },

  /** Recharge le modèle si une version plus récente est disponible. */
  async syncViewer() {
    const canvas = this.root?.querySelector('#alCanvas');
    if (!canvas || !this.job) return;
    const version = Math.max(this.pendingVersion, this.job.version || 0);
    if (!version) { this.setViewerNote('En attente de la première preview…'); return; }
    if (this.viewerLoading) return;
    if (this.viewer && this.viewer.loadedVersion >= version) return;

    this.viewerLoading = true;
    try {
      await this.ensureViewer();
      if (!this.viewer) {
        this.viewer = window.createAvatarLiveViewer(canvas, { transparent: false });
        this.viewer.setBackground('studio');
      }
      const url = this.live('model', version);
      await this.viewer.swapTo(url, version);
      this.setViewerNote('');
      const badge = this.root?.querySelector('#alVersionBadge');
      if (badge) badge.textContent = `v${version}`;
      const info = this.viewer.info();
      const stats = this.root?.querySelector('#alViewerStats');
      if (stats) {
        stats.textContent = `${info.meshes} mesh · ${info.triangles.toLocaleString('fr-FR')} tris`
          + (info.skinned ? ' · rig' : '');
      }
      const skelBtn = this.root?.querySelector('#alSkeleton');
      if (skelBtn) skelBtn.disabled = !this.viewer.hasSkeleton();
    } catch (err) {
      // Preview invalide : la précédente reste affichée, on le DIT.
      const kept = this.viewer?.loadedVersion || 0;
      this.setViewerNote(kept
        ? `Preview v${version} invalide — v${kept} conservée.`
        : `Preview v${version} illisible : ${err.message}`);
    } finally {
      this.viewerLoading = false;
      // Une version est peut-être arrivée pendant le chargement.
      if (this.viewer && this.pendingVersion > this.viewer.loadedVersion) {
        setTimeout(() => this.syncViewer(), 60);
      }
    }
  },

  setViewerNote(text) {
    const node = this.root?.querySelector('#alViewerNote');
    if (!node) return;
    node.textContent = text || '';
    node.hidden = !text;
  },

  /* ------------------------------------------------------------- rendu UI */
  async mountPage(el) {
    this.init();
    this.bind();   // idempotent : rattrape un J indisponible au chargement
    this.root = el;
    this.viewer = null;
    el.innerHTML = this.shell();
    this.wire(el);
    this.refs = await this.loadReferences();
    await this.resync();
    this.paintReferences();
  },

  shell() {
    return `
      <div class="al-head">
        <div class="al-title">
          <h1>Avatar Studio</h1>
          <p>Atelier 3D live — le modèle évolue sous tes yeux pendant que Blender travaille.</p>
        </div>
        <div class="al-head-actions">
          <span class="al-pill" id="alStatusPill">—</span>
          <label class="al-quality">Qualité live
            <select id="alQuality">
              <option value="low">Low · Workbench</option>
              <option value="balanced" selected>Balanced · EEVEE</option>
              <option value="high">High · plus de previews</option>
            </select>
          </label>
          <button class="btn primary sm" id="alRun">Lancer la refonte</button>
          <button class="btn sm" id="alPause" disabled>Pause</button>
          <button class="btn sm" id="alCancel" disabled>Annuler</button>
        </div>
      </div>

      <div class="al-grid">
        <!-- ----------------------------------------------------- gauche -->
        <aside class="al-col">
          <section class="al-card">
            <header><h2>Référence</h2><span id="alRefMeta">—</span></header>
            <div class="al-ref-stage" id="alRefStage">
              <img id="alRefImg" alt="image de référence" />
              <div class="al-empty" id="alRefEmpty">Aucune référence.</div>
            </div>
            <div class="al-ref-tools">
              <button class="al-chip" data-reftool="zoom-out">−</button>
              <input type="range" id="alRefZoom" min="1" max="3" step="0.05" value="1" />
              <button class="al-chip" data-reftool="zoom-in">+</button>
              <button class="al-chip" data-reftool="flip">Miroir</button>
              <button class="al-chip" data-reftool="bg">Fond neutre</button>
              <button class="al-chip" data-reftool="reset">Reset</button>
            </div>
            <div class="al-upload">
              <input type="file" id="alRefFile"
                accept="image/png,image/jpeg,image/webp,image/bmp,image/gif" />
              <button class="btn sm" id="alRefUpload" disabled>Uploader</button>
            </div>
            <div class="al-refs" id="alRefList"></div>
          </section>

          <section class="al-card">
            <header><h2>Avant / Après</h2></header>
            <div class="al-ba" id="alBeforeAfter">
              <img id="alBeforeImg" class="al-ba-before" alt="avant" />
              <div class="al-ba-after"><img id="alAfterImg" alt="après" /></div>
              <div class="al-ba-handle"></div>
            </div>
            <input type="range" id="alBaSlider" min="0" max="100" value="50" />
            <div class="al-note">Gauche : avatar avant le job · Droite : dernière preview.</div>
          </section>

          <section class="al-card">
            <header><h2>Zones modifiées</h2></header>
            <div class="al-options" id="alOptions">
              ${this.OPTION_FIELDS.map((o) => `<label class="al-check">
                <input type="checkbox" value="${o.key}" ${o.on ? 'checked' : ''}/>
                ${o.label}</label>`).join('')}
            </div>
            <div class="al-sep"></div>
            <div class="al-options">
              ${this.PRESERVE_FIELDS.map((o) => `<label class="al-check">
                <input type="checkbox" data-preserve value="${o.key}" ${o.on ? 'checked' : ''}/>
                ${o.label}</label>`).join('')}
            </div>
            <div class="al-note" id="alRunStatus"></div>
          </section>
        </aside>

        <!-- ------------------------------------------------------ centre -->
        <main class="al-col al-col-main">
          <section class="al-card al-stage-card">
            <header>
              <h2>Live avatar</h2>
              <span class="al-badge" id="alVersionBadge">—</span>
              <span class="al-dim" id="alViewerStats"></span>
            </header>
            <div class="al-viewer" id="alViewer">
              <canvas id="alCanvas"></canvas>
              <div class="al-viewer-note" id="alViewerNote" hidden></div>
              <div class="al-viewer-bar">
                <button class="al-chip" data-framing="face">Visage</button>
                <button class="al-chip" data-framing="upper">Buste</button>
                <button class="al-chip" data-framing="full">Plein pied</button>
                <button class="al-chip" data-framing="free">Libre</button>
                <span class="al-bar-sep"></span>
                <button class="al-chip" id="alWireframe">Wireframe</button>
                <button class="al-chip" id="alSkeleton" disabled>Squelette</button>
                <button class="al-chip" id="alPlay">Animation</button>
                <select class="al-chip" id="alBackground">
                  <option value="studio">Studio</option>
                  <option value="dark">Sombre</option>
                  <option value="neutral">Neutre</option>
                  <option value="transparent">Transparent</option>
                </select>
                <span class="al-bar-sep"></span>
                <button class="al-chip" id="alReset">Recadrer</button>
                <button class="al-chip" id="alFullscreen">Plein écran</button>
              </div>
            </div>
          </section>

          <section class="al-card">
            <header><h2>Rendus live</h2><span class="al-dim">4 angles fixes, mêmes caméras à chaque version</span></header>
            <div class="al-views">
              ${this.VIEWS.map(([id, label]) => `
                <figure class="al-view missing" data-view="${id}">
                  <div class="al-view-frame"><img alt="${label}" /></div>
                  <figcaption>${label}</figcaption>
                </figure>`).join('')}
            </div>
          </section>

          <section class="al-card">
            <header><h2>Comparaison référence</h2>
              <span class="al-dim" id="alOverlayPct">50 %</span></header>
            <div class="al-overlay" id="alOverlay">
              <img id="alOverlayRef" alt="référence" />
              <img id="alOverlayCurrent" alt="rendu actuel" />
            </div>
            <input type="range" id="alOverlaySlider" min="0" max="100" value="50" />
          </section>

          <section class="al-card">
            <header><h2>Identité 3D</h2><span class="al-dim" id="alEngineBuild">—</span></header>
            <div class="al-identity-compare" id="alIdentityCompare">
              <figure><img id="alIdentityReference" alt="référence" /><figcaption>REFERENCE</figcaption></figure>
              <figure><div class="al-identity-model" id="alIdentityCurrent">CURRENT</div>
                <figcaption>Avatar actif</figcaption></figure>
              <figure><div class="al-identity-model" id="alIdentityCandidate">CANDIDATE</div>
                <figcaption>En attente d'acceptation</figcaption></figure>
            </div>
            <div class="al-note" id="alIdentityNote">Le candidat ne remplace jamais CURRENT sans validation.</div>
          </section>
        </main>

        <!-- ------------------------------------------------------ droite -->
        <aside class="al-col">
          <section class="al-card">
            <header><h2>Progression</h2><span class="al-badge" id="alPct">0 %</span></header>
            <div class="al-progress"><i id="alBar" style="width:0%"></i></div>
            <div class="al-operation" id="alOperation">—</div>
            <ul class="al-stages" id="alStages"></ul>
            <div class="al-alert" id="alAlert" hidden></div>
          </section>

          <section class="al-card">
            <header><h2>Versions</h2></header>
            <div class="al-versions" id="alVersions"></div>
          </section>

          <section class="al-card">
            <header><h2>Analyse de référence</h2><span id="alVisionTag">—</span></header>
            <details class="al-analysis"><summary>Features extraites</summary>
              <div id="alFeatures"></div></details>
          </section>

          <section class="al-card">
            <header><h2>Évaluation</h2></header>
            <div class="al-scores" id="alScores"></div>
            <div class="al-iterations" id="alIterations"></div>
          </section>

          <section class="al-card">
            <header><h2>Consigne en cours de job</h2></header>
            <div class="al-adjust">
              <input type="text" id="alAdjustText"
                placeholder="ex. fais les cheveux plus courts" />
              <button class="btn sm" id="alAdjustSend" disabled>Envoyer</button>
            </div>
            <div class="al-adjust-list" id="alAdjustList"></div>
          </section>

          <section class="al-card">
            <header><h2>Résultat</h2></header>
            <div class="al-result-actions">
              <button class="btn primary sm" id="alAccept" disabled>Accepter</button>
              <button class="btn sm" id="alReject" disabled>Rejeter</button>
            </div>
            <div class="al-note" id="alResultNote">Le Command Center garde l'avatar actuel
              tant que tu n'as pas accepté.</div>
          </section>
        </aside>
      </div>

      <section class="al-card al-journal">
        <header><h2>Journal live</h2></header>
        <div class="al-timeline" id="alTimeline"></div>
      </section>`;
  },

  /* ------------------------------------------------------------- câblage */
  wire(el) {
    const $p = (sel) => el.querySelector(sel);

    // --- viewer -------------------------------------------------------
    el.querySelectorAll('[data-framing]').forEach((btn) => {
      btn.onclick = () => {
        el.querySelectorAll('[data-framing]').forEach((b) => b.classList.remove('on'));
        btn.classList.add('on');
        this.viewer?.setFraming(btn.dataset.framing);
      };
    });
    $p('#alWireframe').onclick = (e) => {
      if (!this.viewer) return;
      const on = !this.viewer.wireframe;
      this.viewer.setWireframe(on);
      e.currentTarget.classList.toggle('on', on);
    };
    $p('#alSkeleton').onclick = (e) => {
      if (!this.viewer) return;
      const on = this.viewer.setSkeleton(!this.viewer.showSkeleton);
      e.currentTarget.classList.toggle('on', on);
    };
    $p('#alPlay').onclick = (e) => {
      if (!this.viewer) return;
      e.currentTarget.classList.toggle('on', this.viewer.togglePlay());
    };
    $p('#alBackground').onchange = (e) => this.viewer?.setBackground(e.target.value);
    $p('#alReset').onclick = () => { this.viewer?.frame(); };
    $p('#alFullscreen').onclick = () => this.viewer?.fullscreen();

    // --- sliders de comparaison ---------------------------------------
    const overlay = $p('#alOverlaySlider');
    overlay.oninput = () => {
      const pct = Number(overlay.value);
      $p('#alOverlayCurrent').style.opacity = String(pct / 100);
      $p('#alOverlayPct').textContent = `${pct} %`;
    };
    const ba = $p('#alBaSlider');
    ba.oninput = () => {
      const pct = Number(ba.value);
      $p('.al-ba-after').style.clipPath = `inset(0 0 0 ${pct}%)`;
      $p('.al-ba-handle').style.left = `${pct}%`;
    };
    ba.dispatchEvent(new Event('input'));

    // --- outils référence ---------------------------------------------
    const refImg = $p('#alRefImg');
    const state = { zoom: 1, flip: false, bg: false };
    const applyRef = () => {
      refImg.style.transform =
        `scale(${state.zoom}) scaleX(${state.flip ? -1 : 1})`;
      $p('#alRefStage').classList.toggle('neutral', state.bg);
      $p('#alRefZoom').value = String(state.zoom);
    };
    el.querySelectorAll('[data-reftool]').forEach((btn) => {
      btn.onclick = () => {
        const tool = btn.dataset.reftool;
        if (tool === 'zoom-in') state.zoom = Math.min(3, state.zoom + 0.2);
        if (tool === 'zoom-out') state.zoom = Math.max(1, state.zoom - 0.2);
        if (tool === 'flip') { state.flip = !state.flip; btn.classList.toggle('on', state.flip); }
        if (tool === 'bg') { state.bg = !state.bg; btn.classList.toggle('on', state.bg); }
        if (tool === 'reset') { state.zoom = 1; state.flip = false; state.bg = false;
          el.querySelectorAll('[data-reftool]').forEach((b) => b.classList.remove('on')); }
        applyRef();
      };
    });
    $p('#alRefZoom').oninput = (e) => { state.zoom = Number(e.target.value); applyRef(); };

    const file = $p('#alRefFile');
    file.onchange = () => { $p('#alRefUpload').disabled = !file.files.length; };
    $p('#alRefUpload').onclick = async () => {
      if (!file.files.length) return;
      $p('#alRefUpload').disabled = true;
      const res = await this.uploadReference(file.files[0]);
      if (res.ok && res.reference) {
        this.selectedRef = res.reference.id;
        file.value = '';
        this.refs = await this.loadReferences();
        this.paintReferences();
        toast('Référence enregistrée.', 'ok');
      } else {
        $p('#alRefUpload').disabled = false;
        toast(res.error || 'Upload impossible.', 'err');
      }
    };

    // --- contrôles de job ---------------------------------------------
    $p('#alRun').onclick = () => this.startJob(el);
    $p('#alPause').onclick = async () => {
      const paused = this.job?.status === 'paused';
      const res = await J.post(`/api/avatar/jobs/${this.jobId}/${paused ? 'resume' : 'pause'}`, {});
      if (!res.ok) toast(res.error || 'Action impossible.', 'err');
      else this.resync();
    };
    $p('#alCancel').onclick = async () => {
      if (!(await confirmDialog('Annuler ce job ?',
        "Le worker Blender s'arrête, l'avatar actif reste inchangé."))) return;
      const res = await J.post(`/api/avatar/jobs/${this.jobId}/cancel`, {});
      if (!res.ok) toast(res.error || 'Annulation impossible.', 'err');
    };
    $p('#alAccept').onclick = async () => {
      // Exigence 40/47 : sous les seuils, on ne remplace pas l'avatar sans
      // que l'utilisateur confirme explicitement.
      const gate = this.job?.quality_gate || {};
      if (this.job?.quality_ok === false) {
        const detail = (gate.failures || [])
          .map((f) => `${f.metric.replace('_score', '')} ${f.score}/${f.required}`)
          .join(' · ');
        if (!(await confirmDialog(
          'Adopter malgré une qualité insuffisante ?',
          `Les seuils ne sont pas atteints (${detail}). `
          + "L'avatar du Command Center sera remplacé."))) return;
      }
      const res = await J.post(`/api/avatar/jobs/${this.jobId}/accept`, {});
      if (!res.ok) { toast(res.error || 'Adoption impossible.', 'err'); return; }
      toast('Avatar adopté — Command Center rechargé.', 'ok');
      this.reloadCommandCenterAvatar();
      this.resync();
    };
    $p('#alReject').onclick = async () => {
      const res = await J.post(`/api/avatar/jobs/${this.jobId}/reject`, {});
      if (!res.ok) { toast(res.error || 'Action impossible.', 'err'); return; }
      toast('Résultat rejeté — avatar précédent conservé.');
      this.resync();
    };

    const adjust = $p('#alAdjustText');
    const send = async () => {
      const text = adjust.value.trim();
      if (!text || !this.jobId) return;
      const res = await J.post(`/api/avatar/jobs/${this.jobId}/adjust`, { text });
      if (!res.ok) { toast(res.error || 'Consigne refusée.', 'err'); return; }
      adjust.value = '';
      this.resync();
    };
    $p('#alAdjustSend').onclick = send;
    adjust.onkeydown = (e) => { if (e.key === 'Enter') send(); };
  },

  /* -------------------------------------------------------------- actions */
  async startJob(el) {
    if (!this.selectedRef) { toast('Sélectionne une image de référence.', 'err'); return; }
    const options = {};
    el.querySelectorAll('#alOptions input[type="checkbox"]').forEach((cb) => {
      options[cb.value] = cb.checked;
    });
    el.querySelectorAll('[data-preserve]').forEach((cb) => {
      options[cb.value] = cb.checked;
    });
    const btn = el.querySelector('#alRun');
    btn.disabled = true;
    el.querySelector('#alRunStatus').textContent = 'Démarrage du worker Blender…';
    const res = await J.post('/api/avatar/jobs', {
      reference_id: this.selectedRef,
      options,
      quality: el.querySelector('#alQuality').value,
      conversation_id: J.state.conversation || '',
    });
    if (!res.ok) {
      btn.disabled = false;
      el.querySelector('#alRunStatus').textContent = res.error || 'Échec du lancement.';
      toast(res.error || 'Lancement impossible.', 'err');
      return;
    }
    this.jobId = res.job.id;
    this.job = res.job;
    this.pendingVersion = 0;
    if (this.viewer) this.viewer.loadedVersion = 0;
    try { localStorage.setItem(this.STORAGE_KEY, this.jobId); } catch { /* ok */ }
    el.querySelector('#alRunStatus').textContent = 'Job lancé.';
    this.render();
  },

  reloadCommandCenterAvatar() {
    // L'avatar principal n'est remplacé qu'ici, après Accept.
    try {
      window.dispatchEvent(new CustomEvent('jarvis:avatar-reload',
        { detail: { ts: Date.now() } }));
      window.AvatarBridge?.reload?.();
    } catch { /* ok */ }
  },

  async loadReferences() {
    const res = await J.get('/api/avatar/references?limit=30');
    return res.ok ? (res.references || []) : [];
  },

  async uploadReference(file) {
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
      reader.onerror = () => reject(new Error('lecture impossible'));
      reader.readAsDataURL(file);
    });
    return J.post('/api/avatar/references/upload', {
      filename: file.name, data_b64: base64, reference_type: 'mixed',
    });
  },

  /* ---------------------------------------------------------------- rendu */
  render() {
    const el = this.root;
    if (!el || !document.body.contains(el)) return;
    const job = this.job;
    const $p = (sel) => el.querySelector(sel);

    const status = job?.status || 'idle';
    const running = ['queued', 'running', 'paused'].includes(status);
    const pill = $p('#alStatusPill');
    if (pill) {
      const labels = {
        idle: 'Aucun job', queued: 'En file', running: 'Blender travaille',
        paused: 'En pause', completed: 'Terminé', failed: 'Échec',
        cancelled: 'Annulé', analysis_failed: 'Analyse indisponible',
      };
      let label = labels[status] || status;
      let state = status;
      if (job?.stalled) { label = 'Blender semble bloqué'; state = 'stalled'; }
      else if (status === 'completed' && job?.quality_ok === false) {
        label = 'Qualité insuffisante'; state = 'failed';
      }
      pill.textContent = label;
      pill.dataset.state = state;
    }
    $p('#alRun').disabled = running || !this.selectedRef;
    $p('#alRun').textContent = (job?.status === 'analysis_failed' && job?.retryable)
      ? 'Réessayer' : 'Lancer la refonte';
    $p('#alPause').disabled = !(status === 'running' || status === 'paused');
    $p('#alPause').textContent = status === 'paused' ? 'Reprendre' : 'Pause';
    $p('#alCancel').disabled = !running;
    $p('#alAdjustSend').disabled = !running;
    $p('#alAccept').disabled = !(job && status === 'completed' && !job.accepted);
    $p('#alReject').disabled = !(job && status === 'completed');

    const alert = $p('#alAlert');
    if (alert) {
      let message = '';
      if (job?.status === 'analysis_failed') {
        message = 'Analyse de la référence indisponible — génération suspendue. '
          + (job.error || '');
      } else if (job?.error) message = job.error;
      else if (job?.stalled) message = 'Blender semble bloqué — aucun signal reçu. '
        + 'La progression n\'avancera pas toute seule.';
      else if ((job?.warnings || []).length) message = job.warnings[job.warnings.length - 1];
      alert.textContent = message;
      alert.hidden = !message;
      alert.dataset.kind = job?.error ? 'error' : 'warn';
    }

    const result = $p('#alResultNote');
    if (result && job) {
      const gate = job.quality_gate || {};
      if (job.accepted) result.textContent = 'Adopté — cet avatar est désormais actif.';
      else if (status === 'completed' && job.quality_ok === false) {
        result.textContent = 'Qualité sous les seuils ('
          + (gate.failures || []).map((f) =>
              `${f.metric.replace('_score', '')} ${f.score}/${f.required}`).join(', ')
          + ") — candidate conservée, l'avatar actuel reste actif.";
      } else if (status === 'completed' && gate.decided === false) {
        result.textContent = 'Évaluation indisponible — qualité non vérifiée. '
          + "L'avatar actuel reste actif.";
      }
      else if (job.rejected) result.textContent = 'Rejeté — avatar précédent conservé.';
      else if (status === 'cancelled') result.textContent = 'Job annulé — avatar actif inchangé.';
      else if (status === 'failed') result.textContent = 'Échec — la dernière preview valide est conservée.';
    }

    this.paintProgress();
    this.paintStages();
    this.paintVersions();
    this.paintScores();
    this.paintAnalysis();
    this.paintAdjustments();
    this.paintTimeline();
    this.paintBeforeAfter();
    this.paintRenders();
    this.paintIdentityComparison();
    this.updateSidebarBadge();
    this.updateCommandCard();
  },

  paintProgress() {
    const el = this.root;
    if (!el || !this.job) return;
    const pct = Math.round((this.job.progress || 0) * 100);
    const bar = el.querySelector('#alBar');
    if (bar) bar.style.width = `${pct}%`;
    const label = el.querySelector('#alPct');
    if (label) label.textContent = `${pct} %`;
    const op = el.querySelector('#alOperation');
    if (op) op.textContent = this.job.operation || this.job.stage_label || '—';
    const badge = el.querySelector('#alVersionBadge');
    if (badge && this.job.version) badge.textContent = `v${this.job.version}`;
  },

  paintStages() {
    const box = this.root?.querySelector('#alStages');
    if (!box) return;
    const stages = this.job?.stages || [];
    if (!stages.length) {
      box.innerHTML = '<li class="al-empty">Aucun job — lance une refonte.</li>';
      return;
    }
    const marks = { done: '✓', active: '●', skipped: '—', pending: '○' };
    box.innerHTML = stages.map((s) => `
      <li data-state="${s.state}">
        <i>${marks[s.state] || '○'}</i>
        <span>${this.esc(s.label)}</span>
        <b>${s.state === 'active' ? Math.round(s.progress * 100) + ' %'
          : s.state === 'skipped' ? 'ignoré'
          : s.state === 'done' ? '' : ''}</b>
        <em>${Math.round(s.weight * 100)}</em>
      </li>`).join('');
  },

  paintVersions() {
    const box = this.root?.querySelector('#alVersions');
    if (!box) return;
    const revisions = (this.job?.revisions || []).slice(-12).reverse();
    if (!revisions.length) {
      box.innerHTML = '<div class="al-empty">Aucune version encore produite.</div>';
      return;
    }
    const current = this.job?.version || 0;
    box.innerHTML = revisions.map((r) => `
      <button class="al-version ${r.version === current ? 'on' : ''}" data-version="${r.version}">
        <img src="/api/avatar/jobs/${this.jobId}/revisions/${r.version}/front"
          alt="v${r.version}" onerror="this.style.visibility='hidden'" />
        <span>v${r.version}${Number.isFinite(r.score) ? ' · ' + r.score : ''}</span>
        <small>${this.esc(r.label || r.stage || '')}</small>
      </button>`).join('');
    box.querySelectorAll('[data-version]').forEach((btn) => {
      btn.onclick = async () => {
        const version = Number(btn.dataset.version);
        if (!this.viewer) return;
        try {
          await this.viewer.swapTo(
            `/api/avatar/jobs/${this.jobId}/revisions/${version}/model`, version);
          this.setViewerNote(version === current ? '' : `Aperçu de la version v${version}.`);
          const badge = this.root.querySelector('#alVersionBadge');
          if (badge) badge.textContent = `v${version}`;
        } catch {
          this.setViewerNote(`Version v${version} illisible — modèle courant conservé.`);
        }
      };
    });
  },

  paintScores() {
    const box = this.root?.querySelector('#alScores');
    const iters = this.root?.querySelector('#alIterations');
    if (!box || !iters) return;
    const evaluation = this.job?.evaluation || {};

    // Exigence 5/39 : jamais de score fabriqué. Sans évaluation réelle, N/A.
    if (evaluation.available === false || !Object.keys(evaluation).length) {
      const reason = evaluation.reason || "L'évaluation n'a pas encore tourné.";
      box.innerHTML = `<div class="al-na">Évaluation indisponible — N/A
        <small>${this.esc(reason)}</small></div>`;
      iters.innerHTML = '';
      return;
    }

    const rows = [
      ['Visage', evaluation.face_score],
      ['Cheveux', evaluation.hair_score],
      ['Tenue', evaluation.outfit_score],
      ['Corps', evaluation.body_score],
      ['Style', evaluation.style_score],
      ['Global', evaluation.overall_score],
    ];
    box.innerHTML = rows.map(([label, value]) => {
      if (!Number.isFinite(value)) {
        return `<div class="al-score"><span>${label}</span>
          <i></i><em class="na">N/A</em></div>`;
      }
      return `<div class="al-score"><span>${label}</span>
        <i><b style="width:${Math.max(0, Math.min(100, value))}%"></b></i>
        <em>${value}</em></div>`;
    }).join('');

    const issues = evaluation.issues || [];
    if (issues.length) {
      box.insertAdjacentHTML('beforeend', `
        <div class="al-issues"><b>À améliorer :</b>
          <ul>${issues.map((i) => `<li>${this.esc(i)}</li>`).join('')}</ul></div>`);
    }
    if (evaluation.vision_model) {
      box.insertAdjacentHTML('beforeend',
        `<div class="al-na-src">vision : ${this.esc(evaluation.vision_model)}</div>`);
    }

    const iterations = this.job?.iterations || [];
    iters.innerHTML = iterations.length
      ? iterations.map((it, i) => {
        const score = it.scores?.overall_score;
        return `<div class="al-iter ${i === iterations.length - 1 ? 'on' : ''}">
            <b>Itération ${it.index}</b><span>v${it.version}</span>
            <em>${Number.isFinite(score) ? score : 'N/A'}</em>
            ${i === iterations.length - 1 ? '<i>← courante</i>' : ''}</div>`;
      }).join('')
      : '';
  },

  /** Exigence 49 : ce que JARVIS a RÉELLEMENT compris de l'image. */
  paintAnalysis() {
    const box = this.root?.querySelector('#alFeatures');
    const tag = this.root?.querySelector('#alVisionTag');
    if (!box || !tag) return;
    const vision = this.job?.vision || {};
    const features = this.job?.features || {};
    if (vision.success) {
      const dims = vision.image_dimensions || [0, 0];
      tag.textContent = `${vision.model || vision.provider} · ${dims[0]}×${dims[1]}`;
      tag.className = 'al-badge';
    } else {
      tag.textContent = this.job ? 'analyse indisponible' : '—';
      tag.className = 'al-dim';
    }
    const groups = ['character_style', 'body', 'head', 'eyes', 'eyebrows', 'nose',
      'mouth', 'hair', 'skin', 'outfit', 'palette', 'style'];
    const rows = groups.filter((g) => features[g] !== undefined).map((g) => {
      const value = features[g];
      const text = (value && typeof value === 'object')
        ? Object.entries(value).map(([k, v]) => `${k}: ${
            v && typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')
        : String(value);
      return `<div class="al-feat"><b>${g}</b><span>${this.esc(text)}</span></div>`;
    });
    box.innerHTML = rows.length ? rows.join('')
      : `<div class="al-empty">${this.esc(features.analysis_error
          || 'Aucune analyse disponible.')}</div>`;
  },

  paintAdjustments() {
    const box = this.root?.querySelector('#alAdjustList');
    if (!box) return;
    const list = this.job?.adjustments || [];
    box.innerHTML = list.length
      ? list.map((a) => `<div class="al-adjust-item ${a.applied ? 'on' : ''}">
          <i>${a.applied ? '✓' : '⏳'}</i><span>${this.esc(a.text)}</span></div>`).join('')
      : '<div class="al-empty">Les consignes sont appliquées au prochain point sûr.</div>';
  },

  timelineRow(entry) {
    const time = new Date((entry.ts || 0) * 1000)
      .toLocaleTimeString('fr-FR', { hour12: false });
    return `<div class="al-tl-row" data-kind="${entry.kind || 'info'}">
      <time>${time}</time><span>${this.esc(entry.message)}</span></div>`;
  },

  paintTimeline() {
    const box = this.root?.querySelector('#alTimeline');
    if (!box) return;
    const entries = (this.job?.timeline || []).slice(-120).reverse();
    box.innerHTML = entries.length
      ? entries.map((e) => this.timelineRow(e)).join('')
      : '<div class="al-empty">Le journal se remplit avec les événements réels du worker.</div>';
  },

  paintRenders() {
    const version = this.job?.version || 0;
    const available = this.job?.latest_renders || {};
    this.VIEWS.forEach(([id]) => {
      const fig = this.root?.querySelector(`[data-view="${id}"]`);
      if (!fig) return;
      const url = available[id];
      if (!url) return;
      const img = fig.querySelector('img');
      const probe = new Image();
      probe.onload = () => { img.src = url; fig.classList.remove('missing'); };
      probe.src = url;
    });
    const overlayCurrent = this.root?.querySelector('#alOverlayCurrent');
    if (overlayCurrent && available.front) overlayCurrent.src = available.front;
    const after = this.root?.querySelector('#alAfterImg');
    if (after && available.front) after.src = available.front;
    void version;
  },

  paintBeforeAfter() {
    const before = this.root?.querySelector('#alBeforeImg');
    if (!before) return;
    const url = this.job?.before?.glb ? '' : '';
    // L'état « avant » est illustré par le rendu de la première preview (v1),
    // qui est l'avatar tel qu'il était à l'ouverture du .blend.
    const first = (this.job?.revisions || [])[0];
    if (first) {
      before.src = `/api/avatar/jobs/${this.jobId}/revisions/${first.version}/front`;
      before.onerror = () => { before.style.visibility = 'hidden'; };
    }
    void url;
  },

  paintReferences() {
    const el = this.root;
    if (!el) return;
    if (!this.selectedRef && this.refs.length) this.selectedRef = this.refs[0].id;
    const img = el.querySelector('#alRefImg');
    const empty = el.querySelector('#alRefEmpty');
    if (this.selectedRef) {
      const url = `/api/avatar/references/${encodeURIComponent(this.selectedRef)}/image`;
      img.src = url;
      img.style.display = '';
      empty.hidden = true;
    const overlayRef = el.querySelector('#alOverlayRef');
      if (overlayRef) overlayRef.src = url;
    } else {
      img.style.display = 'none';
      empty.hidden = false;
    }
    const meta = el.querySelector('#alRefMeta');
    if (meta) meta.textContent = `${this.refs.length} référence(s)`;
    const list = el.querySelector('#alRefList');
    if (list) {
      list.innerHTML = this.refs.length ? this.refs.map((r) => `
        <button class="al-ref ${r.id === this.selectedRef ? 'on' : ''}" data-ref="${r.id}">
          <img src="/api/avatar/references/${encodeURIComponent(r.id)}/image"
            alt="" onerror="this.style.visibility='hidden'" />
        </button>`).join('')
        : '<div class="al-empty">Uploade une image de référence.</div>';
      list.querySelectorAll('[data-ref]').forEach((btn) => {
        btn.onclick = () => { this.selectedRef = btn.dataset.ref; this.paintReferences();
          this.render(); };
      });
    }
    const run = el.querySelector('#alRun');
    if (run && !['queued', 'running', 'paused'].includes(this.job?.status)) {
      run.disabled = !this.selectedRef;
    }
    this.paintIdentityComparison();
  },

  async paintIdentityComparison() {
    const root = this.root;
    if (!root) return;
    const query = this.selectedRef
      ? `?reference_id=${encodeURIComponent(this.selectedRef)}` : '';
    const result = await J.get(`/api/avatar/engine/comparison${query}`);
    if (!result.ok || !root.isConnected) return;
    const ref = root.querySelector('#alIdentityReference');
    if (ref && result.reference_url) ref.src = result.reference_url;
    const build = root.querySelector('#alEngineBuild');
    if (build) build.textContent = result.build || '—';
    const current = root.querySelector('#alIdentityCurrent');
    const candidate = root.querySelector('#alIdentityCandidate');
    if (current) current.innerHTML = result.current
      ? `<a href="${result.current_url || '/assets/avatar/jarvis_avatar.glb'}" target="_blank">GLB actif ↗</a>`
      : 'absent';
    if (candidate) candidate.innerHTML = result.candidate
      ? `<a href="${result.candidate_url}" target="_blank">GLB candidat ↗</a>`
      : 'aucun candidat';
  },

  /* ------------------------------------------------- badge hors de la page */
  updateSidebarBadge() {
    const job = this.job;
    const running = job && ['queued', 'running', 'paused'].includes(job.status);
    let badge = document.getElementById('avatarJobBadge');
    if (!running) { badge?.remove(); return; }
    if (!badge) {
      badge = document.createElement('button');
      badge.id = 'avatarJobBadge';
      badge.className = 'al-sidebar-badge';
      badge.onclick = () => (window.App?.goto
        ? App.goto('avatar-studio') : (location.hash = 'avatar-studio'));
      (document.getElementById('nav') || document.getElementById('sidebar')
        || document.body).appendChild(badge);
    }
    const pct = Math.round((job.progress || 0) * 100);
    badge.innerHTML = `
      <img src="${this.live('front', job.version)}" alt=""
        onerror="this.style.visibility='hidden'" />
      <span>Avatar · ${pct} %</span>`;
  },

  /**
   * Mini-carte live sur le Command Center. L'avatar principal affiché reste
   * l'ancien, stable : seule cette vignette montre la preview en cours.
   */
  updateCommandCard() {
    const job = this.job;
    const running = job && ['queued', 'running', 'paused'].includes(job.status);
    let card = document.getElementById('avatarLiveCard');
    if (!running) { card?.remove(); return; }
    if (!card) {
      const host = document.querySelector('#page-command .robot-stage');
      if (!host) return;
      card = document.createElement('button');
      card.id = 'avatarLiveCard';
      card.className = 'al-live-card';
      card.onclick = () => (window.App?.goto
        ? App.goto('avatar-studio') : (location.hash = 'avatar-studio'));
      host.appendChild(card);
    }
    const pct = Math.round((job.progress || 0) * 100);
    card.innerHTML = `
      <img src="${this.live('front', job.version)}" alt=""
        onerror="this.style.visibility='hidden'" />
      <span><b>Avatar en cours de modification — ${pct} %</b>
        <small>${this.esc(job.operation || job.stage_label || '')}</small></span>`;
  },

  /* ----------------------------------------------------------------- style */
  injectStyles() {
    if (document.getElementById('avatar-studio-live-css')) return;
    const style = document.createElement('style');
    style.id = 'avatar-studio-live-css';
    style.textContent = `
    .al-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
      flex-wrap:wrap;margin-bottom:14px}
    .al-title h1{margin:0;font-size:22px;letter-spacing:.04em}
    .al-title p{margin:4px 0 0;font-size:12px;color:var(--text-dim)}
    .al-head-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .al-quality{display:flex;gap:6px;align-items:center;font-size:11px;color:var(--text-dim)}
    .al-quality select{background:rgba(8,14,22,.9);color:var(--text);
      border:1px solid var(--line);border-radius:8px;padding:4px 6px;font-size:11px}
    .al-pill{font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);
      color:var(--text-dim);background:rgba(8,14,22,.6)}
    .al-pill[data-state=running]{color:#7ee3ff;border-color:#1d6b83;
      box-shadow:0 0 12px rgba(45,212,255,.2)}
    .al-pill[data-state=paused]{color:#ffd479;border-color:#7a5f1f}
    .al-pill[data-state=completed]{color:#6ee7a8;border-color:#1f6b46}
    .al-pill[data-state=failed],.al-pill[data-state=stalled]{color:#ff8b8b;border-color:#7a2b2b}

    .al-grid{display:grid;grid-template-columns:300px minmax(0,1fr) 320px;gap:14px;align-items:start}
    @media (max-width:1400px){.al-grid{grid-template-columns:280px minmax(0,1fr)}
      .al-grid>aside:last-child{grid-column:1/-1}}
    @media (max-width:1000px){.al-grid{grid-template-columns:1fr}
      .al-grid>aside:last-child{grid-column:auto}}
    .al-col{display:flex;flex-direction:column;gap:14px;min-width:0}

    .al-card{background:linear-gradient(180deg,rgba(13,20,30,.92),rgba(8,12,19,.92));
      border:1px solid var(--line);border-radius:14px;padding:12px;position:relative;
      box-shadow:0 18px 40px rgba(0,0,0,.35)}
    .al-card::before{content:'';position:absolute;inset:0 0 auto;height:1px;
      background:linear-gradient(90deg,transparent,rgba(45,212,255,.45),transparent)}
    .al-card>header{display:flex;align-items:center;gap:8px;margin-bottom:10px}
    .al-card h2{margin:0;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
      color:var(--text-dim)}
    .al-card>header>span{margin-left:auto}
    .al-dim{font-size:10px;color:var(--text-faint)}
    .al-badge{font-size:10px;padding:2px 8px;border-radius:999px;color:#7ee3ff;
      border:1px solid #1d6b83;background:rgba(20,60,80,.35);font-variant-numeric:tabular-nums}
    .al-note{font-size:10px;color:var(--text-faint);margin-top:8px;line-height:1.5}
    .al-empty{font-size:11px;color:var(--text-faint);padding:8px 2px}
    .al-sep{height:1px;background:var(--line);margin:10px 0}

    /* ------------------------------------------------------------ viewer */
    .al-stage-card{padding-bottom:12px}
    .al-viewer{position:relative;aspect-ratio:1/1;max-height:620px;border-radius:12px;
      overflow:hidden;background:radial-gradient(ellipse at 50% 0%,#101c2a,#05080d 70%);
      border:1px solid rgba(45,212,255,.14)}
    .al-viewer canvas{width:100%;height:100%;display:block}
    .al-viewer-note{position:absolute;top:10px;left:10px;right:10px;font-size:11px;
      padding:6px 10px;border-radius:8px;background:rgba(90,30,30,.8);color:#ffd2d2;
      border:1px solid rgba(255,120,120,.35)}
    .al-viewer-bar{position:absolute;left:10px;right:10px;bottom:10px;display:flex;
      flex-wrap:wrap;gap:5px;align-items:center;padding:6px;border-radius:10px;
      background:rgba(5,9,15,.78);backdrop-filter:blur(8px);border:1px solid var(--line)}
    .al-bar-sep{width:1px;height:16px;background:var(--line);margin:0 3px}
    .al-chip{font-size:10px;padding:4px 9px;border-radius:7px;cursor:pointer;
      background:rgba(255,255,255,.04);color:var(--text-dim);border:1px solid transparent}
    .al-chip:hover:not(:disabled){color:var(--text);border-color:rgba(45,212,255,.4)}
    .al-chip.on{color:#05080d;background:#2dd4ff;border-color:#2dd4ff}
    .al-chip:disabled{opacity:.35;cursor:not-allowed}
    select.al-chip{padding:3px 6px}

    /* ------------------------------------------------------------ rendus */
      .al-views{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
      .al-identity-compare{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
      .al-identity-compare figure{margin:0;border:1px solid var(--line);border-radius:9px;
        overflow:hidden;background:rgba(8,14,22,.55);min-height:96px;display:flex;
        flex-direction:column;justify-content:space-between}
      .al-identity-compare img{width:100%;height:78px;object-fit:cover;background:#111}
      .al-identity-model{height:78px;display:grid;place-items:center;color:var(--text-dim);
        font-size:10px;letter-spacing:.08em}
      .al-identity-model a{color:#7ee3ff;text-decoration:none;font-size:11px;letter-spacing:0}
      .al-identity-compare figcaption{padding:6px 7px;font-size:10px;color:var(--text-dim)}
    @media (max-width:1200px){.al-views{grid-template-columns:repeat(2,1fr)}}
    .al-view{margin:0}
    .al-view-frame{aspect-ratio:1/1;border-radius:10px;overflow:hidden;
      background:#070b11;border:1px solid var(--line);display:flex}
    .al-view-frame img{width:100%;height:100%;object-fit:contain}
    .al-view figcaption{font-size:10px;color:var(--text-faint);text-align:center;margin-top:4px}
    .al-view.missing .al-view-frame{background:
      repeating-linear-gradient(45deg,#0a0f16,#0a0f16 8px,#0d141d 8px,#0d141d 16px)}

    /* ------------------------------------------------- overlay & avant/après */
    .al-overlay{position:relative;aspect-ratio:1/1;max-height:340px;border-radius:10px;
      overflow:hidden;background:#070b11;border:1px solid var(--line)}
    .al-overlay img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
    #alOverlayCurrent{opacity:.5}
    .al-ba{position:relative;aspect-ratio:1/1;max-height:240px;border-radius:10px;
      overflow:hidden;background:#070b11;border:1px solid var(--line)}
    .al-ba img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
    .al-ba-after{position:absolute;inset:0;clip-path:inset(0 0 0 50%)}
    .al-ba-handle{position:absolute;top:0;bottom:0;width:1px;background:#2dd4ff;
      box-shadow:0 0 10px rgba(45,212,255,.8)}
    input[type=range]{width:100%;margin-top:8px;accent-color:#2dd4ff}

    /* --------------------------------------------------------- référence */
    .al-ref-stage{position:relative;aspect-ratio:1/1;max-height:240px;border-radius:10px;
      overflow:hidden;background:#070b11;border:1px solid var(--line);display:flex}
    .al-ref-stage.neutral{background:#8a929c}
    .al-ref-stage img{width:100%;height:100%;object-fit:contain;transition:transform .15s}
    .al-ref-tools{display:flex;gap:4px;align-items:center;margin-top:8px;flex-wrap:wrap}
    .al-ref-tools input[type=range]{flex:1;min-width:60px;margin:0}
    .al-upload{display:flex;gap:6px;align-items:center;margin-top:8px}
    .al-upload input[type=file]{flex:1;min-width:0;font-size:10px;color:var(--text-dim)}
    .al-refs{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
    .al-ref{width:44px;height:44px;padding:0;border-radius:8px;overflow:hidden;
      border:1px solid var(--line);background:#070b11;cursor:pointer}
    .al-ref.on{border-color:#2dd4ff;box-shadow:0 0 0 1px #2dd4ff}
    .al-ref img{width:100%;height:100%;object-fit:cover}

    /* ------------------------------------------------------- progression */
    .al-progress{height:6px;border-radius:999px;background:rgba(255,255,255,.06);
      overflow:hidden}
    .al-progress i{display:block;height:100%;border-radius:999px;
      background:linear-gradient(90deg,#1b7fa0,#2dd4ff);transition:width .35s ease}
    .al-operation{font-size:12px;color:var(--text);margin:10px 0 8px;min-height:16px}
    .al-stages{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
    .al-stages li{display:flex;align-items:center;gap:8px;font-size:11px;
      color:var(--text-faint);padding:3px 0}
    .al-stages li i{font-style:normal;width:14px;text-align:center}
    .al-stages li span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
      white-space:nowrap}
    .al-stages li b{font-weight:500;font-variant-numeric:tabular-nums;font-size:10px}
    .al-stages li em{font-style:normal;font-size:9px;opacity:.4;width:18px;text-align:right}
    .al-stages li[data-state=done]{color:#6ee7a8}
    .al-stages li[data-state=active]{color:#2dd4ff}
    .al-stages li[data-state=active] i{animation:alPulse 1.2s infinite}
    .al-stages li[data-state=skipped]{opacity:.45;text-decoration:line-through}
    @keyframes alPulse{0%,100%{opacity:1}50%{opacity:.25}}
    .al-alert{margin-top:10px;font-size:11px;padding:8px 10px;border-radius:8px;
      background:rgba(90,60,20,.45);color:#ffd479;border:1px solid rgba(255,200,100,.25)}
    .al-alert[data-kind=error]{background:rgba(90,25,25,.5);color:#ffb0b0;
      border-color:rgba(255,120,120,.3)}

    /* ----------------------------------------------------------- versions */
    .al-versions{display:flex;gap:6px;flex-wrap:wrap}
    .al-version{width:60px;padding:3px;border-radius:8px;border:1px solid var(--line);
      background:#070b11;cursor:pointer;display:flex;flex-direction:column;gap:2px}
    .al-version.on{border-color:#2dd4ff;box-shadow:0 0 0 1px #2dd4ff}
    .al-version img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:5px}
    .al-version span{font-size:9px;color:var(--text)}
    .al-version small{font-size:8px;color:var(--text-faint);overflow:hidden;
      text-overflow:ellipsis;white-space:nowrap}

    /* ------------------------------------------------------------- scores */
    .al-score{display:flex;align-items:center;gap:8px;font-size:11px;
      color:var(--text-dim);padding:2px 0}
    .al-score span{width:56px}
    .al-score i{flex:1;height:4px;border-radius:999px;background:rgba(255,255,255,.07)}
    .al-score i b{display:block;height:100%;border-radius:999px;background:#2dd4ff}
    .al-na{font-size:11px;color:#ffd479;padding:6px 8px;border-radius:8px;
      background:rgba(90,60,20,.3);border:1px solid rgba(255,200,100,.22);
      display:flex;flex-direction:column;gap:3px}
    .al-na small{color:var(--text-faint);font-size:10px}
    .al-na-src{font-size:9px;color:var(--text-faint);margin-top:6px;text-align:right}
    .al-issues{margin-top:8px;font-size:10px;color:var(--text-dim)}
    .al-issues b{color:#ffd479;font-weight:500}
    .al-issues ul{margin:4px 0 0;padding-left:14px;display:flex;
      flex-direction:column;gap:2px}
    .al-analysis summary{font-size:11px;color:var(--text-dim);cursor:pointer}
    .al-feat{display:flex;gap:6px;font-size:10px;padding:2px 0;
      border-bottom:1px solid rgba(255,255,255,.04)}
    .al-feat b{color:#2dd4ff;font-weight:500;min-width:76px}
    .al-feat span{color:var(--text-dim);word-break:break-word}
    .al-score em.na{color:var(--text-faint)}
    .al-score em{font-style:normal;width:24px;text-align:right;
      font-variant-numeric:tabular-nums}
    .al-iterations{margin-top:8px;display:flex;flex-direction:column;gap:3px}
    .al-iter{display:flex;gap:8px;align-items:center;font-size:10px;color:var(--text-faint)}
    .al-iter.on{color:#2dd4ff}
    .al-iter em{font-style:normal;margin-left:auto;font-variant-numeric:tabular-nums}

    /* --------------------------------------------------------- consignes */
    .al-adjust{display:flex;gap:6px}
    .al-adjust input{flex:1;min-width:0;background:rgba(8,14,22,.9);color:var(--text);
      border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:11px}
    .al-adjust-list{margin-top:8px;display:flex;flex-direction:column;gap:3px}
    .al-adjust-item{display:flex;gap:6px;font-size:10px;color:var(--text-faint)}
    .al-adjust-item.on{color:#6ee7a8}
    .al-result-actions{display:flex;gap:6px}

    /* --------------------------------------------------------- journal */
    .al-journal{margin-top:14px}
    .al-timeline{max-height:220px;overflow:auto;display:flex;flex-direction:column;gap:1px}
    .al-tl-row{display:flex;gap:10px;font-size:11px;padding:3px 6px;border-radius:6px;
      color:var(--text-dim)}
    .al-tl-row:hover{background:rgba(255,255,255,.03)}
    .al-tl-row time{color:var(--text-faint);font-variant-numeric:tabular-nums;
      font-size:10px;min-width:56px}
    .al-tl-row[data-kind=done]{color:#6ee7a8}
    .al-tl-row[data-kind=preview]{color:#2dd4ff}
    .al-tl-row[data-kind=error]{color:#ff9b9b}
    .al-tl-row[data-kind=warn]{color:#ffd479}
    .al-tl-row[data-kind=skip]{opacity:.5}

    /* ------------------------------------------------------- badge latéral */
    .al-sidebar-badge{display:flex;align-items:center;gap:8px;width:calc(100% - 16px);
      margin:8px;padding:6px;border-radius:10px;cursor:pointer;font-size:11px;
      color:#2dd4ff;background:rgba(20,60,80,.3);border:1px solid rgba(45,212,255,.3)}
    .al-sidebar-badge img{width:26px;height:26px;border-radius:6px;object-fit:cover;
      background:#070b11}
    .al-live-card{position:absolute;left:12px;bottom:12px;z-index:6;display:flex;
      gap:8px;align-items:center;padding:6px 10px 6px 6px;border-radius:12px;
      cursor:pointer;text-align:left;background:rgba(5,9,15,.85);
      backdrop-filter:blur(10px);border:1px solid rgba(45,212,255,.35);
      box-shadow:0 10px 30px rgba(0,0,0,.45)}
    .al-live-card img{width:36px;height:36px;border-radius:8px;object-fit:cover;
      background:#070b11}
    .al-live-card span{display:flex;flex-direction:column;gap:1px}
    .al-live-card b{font-size:11px;color:#2dd4ff;font-weight:500}
    .al-live-card small{font-size:9px;color:var(--text-faint);max-width:200px;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    `;
    document.head.appendChild(style);
  },
};

window.AvatarStudioLive = window.AvatarStudioLive || AvatarStudioLive;
window.AVATAR_STUDIO_LIVE_BUILD = AvatarStudioLive.BUILD;
AvatarStudioLive.init();
if (window.Pages) {
  Pages['avatar-studio'] = (el) => AvatarStudioLive.mountPage(el);
}
console.log('[AvatarStudioLive] script loaded', AvatarStudioLive.BUILD);
