/* ==========================================================================
   ImageStudio — panneau « Génération d'images » (JARVIS_IMAGE_GENERATION_REBUILD_V1)

   Affiche, pour chaque génération : le prompt de l'utilisateur, le prompt
   enrichi (repliable), le mode FAST/BALANCED/QUALITY/ULTRA, la progression
   réelle (PREPARING → PROMPTING → GENERATING → REFINING → UPSCALING →
   COMPLETE), l'aperçu, la résolution, le modèle et la durée.

   Toutes les valeurs proviennent des événements backend : rien n'est simulé.
   Une étape absente du plan n'est jamais affichée comme franchie.
   ========================================================================== */
(() => {
  const STAGES = ['PREPARING', 'PROMPTING', 'LOADING_MODEL', 'GENERATING',
    'HI_RES', 'UPSCALE', 'SAVING', 'COMPLETE'];
  const STAGE_LABEL = {
    PREPARING: 'Préparation',
    PROMPTING: 'Construction du prompt',
    LOADING_MODEL: 'Chargement du modèle',
    SEGMENTING: 'Segmentation',
    MASKING: 'Masque',
    GENERATING: 'Génération',
    HI_RES: 'Hi-res',
    UPSCALE: 'Agrandissement',
    COMPOSITING: 'Composition',
    TYPOGRAPHY: 'Typographie',
    SAVING: 'Enregistrement',
    COMPLETE: 'Terminé',
  };
  const EDIT_OPERATIONS = [
    ['replace_background', 'Changer le fond', true],
    ['cutout', 'PNG transparent', false],
    ['restyle', 'Restyler', true],
    ['upscale', 'Upscale', false],
    ['remove', 'Supprimer le fond', false],
  ];
  const MODES = [
    ['FAST', 'Aperçu rapide'],
    ['BALANCED', 'Équilibré'],
    ['QUALITY', 'Qualité'],
    ['ULTRA', 'Ultra'],
  ];

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const ImageStudio = {
    /** jobId → job le plus récent reçu du backend. */
    jobs: new Map(),
    mode: '',          // '' = automatique
    current: null,

    /* ------------------------------------------------------------ montage */
    render(host) {
      const root = host || document.getElementById('page-image-studio');
      if (!root) return;
      this.root = root;
      root.innerHTML = `
        <div class="imgstudio">
          <header class="imgstudio-head">
            <div>
              <h2>Génération d'images</h2>
              <p class="imgstudio-sub">Pipeline Z-Image V2 — profils de qualité, raffinement hi-res, upscale ESRGAN.</p>
            </div>
            <div class="imgstudio-modes" role="group" aria-label="Mode de qualité">
              <button class="imgmode active" data-mode="">Auto</button>
              ${MODES.map(([id, label]) =>
                `<button class="imgmode" data-mode="${id}" title="${label}">${id}</button>`).join('')}
            </div>
          </header>

          <form class="imgstudio-compose" id="imgStudioForm">
            <textarea id="imgStudioPrompt" rows="3"
              placeholder="Décris l'image voulue — par exemple : une affiche premium pour mon tournoi d'esport"></textarea>
            <button type="submit" class="imgstudio-go">Générer</button>
          </form>
          <div class="imgstudio-import">
            <label class="imgstudio-importbtn">
              Importer une image à modifier
              <input type="file" id="imgStudioFile" accept="image/png,image/jpeg,image/webp" hidden>
            </label>
            <span class="imgstudio-meta">PNG, JPEG ou WebP — 25 Mo max</span>
          </div>

          <div class="imgstudio-current" id="imgStudioCurrent"></div>
          <h3 class="imgstudio-kicker">Historique de la session</h3>
          <div class="imgstudio-grid" id="imgStudioGrid"></div>
        </div>`;

      root.querySelectorAll('[data-mode]').forEach((btn) => {
        btn.addEventListener('click', () => {
          this.mode = btn.dataset.mode;
          root.querySelectorAll('[data-mode]').forEach((b) =>
            b.classList.toggle('active', b === btn));
        });
      });
      root.querySelector('#imgStudioFile').addEventListener('change', (event) => {
        const file = event.target.files && event.target.files[0];
        if (file) this.importImage(file);
        event.target.value = '';
      });
      root.querySelector('#imgStudioForm').addEventListener('submit', (event) => {
        event.preventDefault();
        const input = root.querySelector('#imgStudioPrompt');
        const prompt = input.value.trim();
        if (prompt) { this.generate(prompt); input.value = ''; }
      });
      this.paint();
    },

    /* ----------------------------------------------------------- requêtes */
    async call(path, body) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },

    generate(prompt, extra) {
      return this.call('/api/images/generate', {
        prompt,
        context: { quality_mode: this.mode || undefined, ...(extra || {}) },
      }).catch((error) => this.showError(prompt, error));
    },

    action(job, kind) {
      const prompt = job?.meta?.original_prompt || job?.prompt || '';
      if (kind === 'regenerate') return this.generate(prompt);
      if (kind === 'improve') {
        const order = ['FAST', 'BALANCED', 'QUALITY', 'ULTRA'];
        const at = order.indexOf(job?.meta?.quality_mode || 'BALANCED');
        const next = order[Math.min(order.length - 1, at + 1)];
        return this.call('/api/images/generate', {
          prompt, context: { quality_mode: next },
        }).catch((error) => this.showError(prompt, error));
      }
      if (kind === 'variant') {
        return this.call('/api/images/generate', {
          prompt, seed: 0, context: { quality_mode: job?.meta?.quality_mode },
        }).catch((error) => this.showError(prompt, error));
      }
      if (kind === 'upscale') {
        return this.call('/api/images/generate', {
          prompt: 'upscale', mode: 'upscale', source_job_id: job.id,
        }).catch((error) => this.showError(prompt, error));
      }
      if (kind === 'edit') {
        const instruction = window.prompt('Que veux-tu modifier sur cette image ?');
        if (!instruction) return undefined;
        return this.call('/api/images/generate', {
          prompt: instruction, mode: 'edit', source_job_id: job.id,
        }).catch((error) => this.showError(instruction, error));
      }
      if (kind === 'mask') return this.showMask(job);
      if (kind === 'cutout') {
        return this.call(`/api/images/${encodeURIComponent(job.id)}/cutout`, {})
          .then((r) => this.showAside(job, `<img class="imgstudio-img" src="${r.url}?t=${Date.now()}" alt="Détourage">`,
            'Détourage PNG (fond transparent, aucune diffusion)'))
          .catch((error) => this.showError(prompt, error));
      }
      if (kind === 'poster') {
        const raw = window.prompt('Lignes du texte, séparées par « | » (titre | sous-titre | bas)');
        if (!raw) return undefined;
        return this.call(`/api/images/${encodeURIComponent(job.id)}/poster-text`,
          { lines: raw.split('|').map((x) => x.trim()) })
          .then((r) => this.showAside(job, `<img class="imgstudio-img" src="${r.url}?t=${Date.now()}" alt="Affiche">`,
            'Affiche avec typographie composée'))
          .catch((error) => this.showError(prompt, error));
      }
      if (kind === 'save') {
        const link = document.createElement('a');
        link.href = this.imageUrl(job);
        link.download = `${job.id}.png`;
        link.click();
        return undefined;
      }
      return undefined;
    },



    /* ------------------------------------------------------- import/édition */
    importImage(file) {
      const reader = new FileReader();
      reader.onload = () => {
        const data = String(reader.result || '').split(',')[1] || '';
        this.call('/api/images/import', { filename: file.name, data_b64: data })
          .then((r) => {
            this.track({ ...r.job, status: 'completed' });
            this.showEditPanel(r.job);
          })
          .catch((error) => this.showError(file.name, error));
      };
      reader.onerror = () => this.showError(file.name, new Error('Lecture impossible.'));
      reader.readAsDataURL(file);
    },

    showEditPanel(job) {
      const buttons = EDIT_OPERATIONS.map(([id, label, needsPrompt]) =>
        `<button data-editop="${id}" data-needsprompt="${needsPrompt ? 1 : 0}">${label}</button>`).join('');
      this.showAside(job, `
        <img class="imgstudio-img" src="${this.imageUrl(job)}" alt="Image importée">
        <div class="imgstudio-masktools">
          <span>Édition :</span>${buttons}
          <button data-editop="__mask">Voir le masque</button>
        </div>`, 'Image importée — Image Edit V3');
      this.root.querySelectorAll('[data-editop]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const op = btn.dataset.editop;
          if (op === '__mask') return this.showMask(job);
          let prompt = '';
          if (btn.dataset.needsprompt === '1') {
            prompt = window.prompt(op === 'replace_background'
              ? 'Décris le nouveau fond'
              : 'Décris le style voulu') || '';
            if (!prompt) return undefined;
          }
          return this.runEdit(job, op, prompt);
        });
      });
    },

    runEdit(job, operation, prompt) {
      return this.call(`/api/images/${encodeURIComponent(job.id)}/edit`,
        { operation, prompt })
        .then((r) => { this.track(r.job); this.paint(); })
        .catch((error) => this.showError(prompt || operation, error));
    },

    /* --------------------------------------------------------- masque */
    maskState: { grow: 3, target: 'background' },

    showMask(job) {
      const body = {
        operation: 'replace_background',
        target: this.maskState.target,
        grow: this.maskState.grow,
      };
      return this.call(`/api/images/${encodeURIComponent(job.id)}/mask`, body)
        .then((r) => {
          const t = this.maskState.target === 'background' ? 'arrière-plan' : 'sujet';
          this.showAside(job, `
            <img class="imgstudio-img" src="${r.url}?t=${Date.now()}" alt="Masque">
            <div class="imgstudio-masktools">
              <span>Zone : <b>${t}</b> · dilatation ${this.maskState.grow} px</span>
              <button data-mask="invert">Inverser</button>
              <button data-mask="grow">Dilater</button>
              <button data-mask="erode">Éroder</button>
              <button data-mask="reset">Réinitialiser</button>
            </div>`, `Masque automatique (BiRefNet) — ${r.seconds.toFixed(1)} s`);
          this.root.querySelectorAll('[data-mask]').forEach((btn) => {
            btn.addEventListener('click', () => {
              const kind = btn.dataset.mask;
              if (kind === 'invert') {
                this.maskState.target =
                  this.maskState.target === 'background' ? 'subject' : 'background';
              } else if (kind === 'grow') this.maskState.grow += 2;
              else if (kind === 'erode') this.maskState.grow -= 2;
              else this.maskState = { grow: 3, target: 'background' };
              this.showMask(job);
            });
          });
        })
        .catch((error) => this.showError(job?.meta?.original_prompt || '', error));
    },

    showAside(job, html, title) {
      const host = this.root?.querySelector('#imgStudioCurrent');
      if (!host) return;
      const panel = document.createElement('div');
      panel.className = 'imgstudio-card imgstudio-aside';
      panel.innerHTML = `<div class="imgstudio-card-head"><strong>${esc(title)}</strong>
        <span class="imgstudio-spacer"></span>
        <button class="imgstudio-close" aria-label="Fermer">×</button></div>${html}`;
      host.querySelector('.imgstudio-aside')?.remove();
      host.appendChild(panel);
      panel.querySelector('.imgstudio-close')
        .addEventListener('click', () => panel.remove());
    },

    imageUrl(job) {
      return `/api/images/${encodeURIComponent(job.id)}/file?t=${job.meta?.bytes || 0}`;
    },

    showError(prompt, error) {
      const host = this.root?.querySelector('#imgStudioCurrent');
      if (!host) return;
      host.innerHTML = `<div class="imgstudio-card is-error">
        <div class="imgstudio-card-head"><strong>Échec de la génération</strong></div>
        <p class="imgstudio-prompt">${esc(prompt)}</p>
        <p class="imgstudio-err">${esc(error?.message || error)}</p></div>`;
    },

    /* ------------------------------------------------------------ rendu */
    stageRow(job) {
      const planned = job?.meta?.stages || STAGES;
      const current = String(job.stage || '').toUpperCase();
      const reached = planned.indexOf(current);
      return `<ol class="imgstudio-stages">${planned.map((stage, index) => {
        const state = job.status === 'completed' ? 'done'
          : index < reached ? 'done' : index === reached ? 'active' : '';
        return `<li class="${state}">${esc(STAGE_LABEL[stage] || stage)}</li>`;
      }).join('')}</ol>`;
    },

    card(job, { detailed } = {}) {
      const meta = job.meta || {};
      const mode = meta.quality_mode || '';
      const type = meta.image_type_label || meta.image_type || '';
      const operation = meta.edit_operation || '';
      const pipeline = meta.pipeline_version ? `pipeline ${meta.pipeline_version}` : '';
      const seed = job.seed || meta.model?.seed || '';
      const model = meta.model?.diffusion_model || meta.model?.name || '';
      const duration = meta.duration_s ? `${Number(meta.duration_s).toFixed(1)} s` : '';
      const size = job.width && job.height ? `${job.width}×${job.height}` : '';
      const done = job.status === 'completed';
      const failed = job.status === 'failed';
      const warn = meta.warning ? `<p class="imgstudio-warn">${esc(meta.warning)}</p>` : '';

      const enriched = detailed && meta.final_prompt && meta.final_prompt !== meta.original_prompt
        ? `<details class="imgstudio-enriched"><summary>Prompt enrichi</summary>
             <p>${esc(meta.final_prompt)}</p>
             ${meta.quality_mode_reason
               ? `<p class="imgstudio-reason">Mode choisi : ${esc(meta.quality_mode_reason)}</p>` : ''}
           </details>`
        : '';

      const actions = done ? `<div class="imgstudio-actions">
          ${[['regenerate', 'Régénérer'], ['improve', 'Améliorer'], ['upscale', 'Upscale'],
             ['edit', 'Modifier'], ['mask', 'Voir le masque'], ['cutout', 'Détourer PNG'],
             ['poster', 'Ajouter le texte'], ['variant', 'Créer variante'],
             ['save', 'Enregistrer']]
            .map(([kind, label]) =>
              `<button data-act="${kind}" data-job="${esc(job.id)}">${label}</button>`).join('')}
        </div>` : '';

      const visual = done
        ? `<img class="imgstudio-img" src="${esc(this.imageUrl(job))}" alt="${esc(meta.original_prompt || '')}" loading="lazy">`
        : failed
          ? `<p class="imgstudio-err">${esc(job.error || 'Échec inconnu')}</p>`
          : `<div class="imgstudio-skeleton" style="--p:${Math.round((job.progress || 0) * 100)}%"></div>`;

      return `<article class="imgstudio-card ${failed ? 'is-error' : ''} ${done ? 'is-done' : ''}"
                       data-job="${esc(job.id)}">
        <div class="imgstudio-card-head">
          ${mode ? `<span class="imgstudio-badge mode-${esc(mode)}">${esc(mode)}</span>` : ''}
          ${type ? `<span class="imgstudio-badge soft">${esc(type)}</span>` : ''}
          ${operation ? `<span class="imgstudio-badge soft">${esc(operation)}</span>` : ''}
          ${pipeline ? `<span class="imgstudio-badge soft">${esc(pipeline)}</span>` : ''}
          <span class="imgstudio-spacer"></span>
          ${size ? `<span class="imgstudio-meta">${esc(size)}</span>` : ''}
          ${duration ? `<span class="imgstudio-meta">${esc(duration)}</span>` : ''}
          ${seed ? `<span class="imgstudio-meta">seed ${esc(seed)}</span>` : ''}
        </div>
        <p class="imgstudio-prompt">${esc(meta.original_prompt || job.prompt || '')}</p>
        ${enriched}
        ${done || failed ? '' : this.stageRow(job)}
        ${warn}
        ${visual}
        ${model ? `<p class="imgstudio-model">${esc(model)}</p>` : ''}
        ${actions}
      </article>`;
    },

    paint() {
      if (!this.root) return;
      const currentHost = this.root.querySelector('#imgStudioCurrent');
      const gridHost = this.root.querySelector('#imgStudioGrid');
      if (!currentHost || !gridHost) return;

      const all = [...this.jobs.values()].sort(
        (a, b) => (b.created_at || 0) - (a.created_at || 0));
      const active = all.find((job) => job.status === 'running' || job.status === 'queued');
      const featured = active || all[0];

      currentHost.innerHTML = featured ? this.card(featured, { detailed: true }) : '';
      gridHost.innerHTML = all.filter((job) => job !== featured)
        .map((job) => this.card(job)).join('');

      this.root.querySelectorAll('[data-act]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const job = this.jobs.get(btn.dataset.job);
          if (job) this.action(job, btn.dataset.act);
        });
      });
    },

    track(job) {
      if (!job || !job.id) return;
      const previous = this.jobs.get(job.id) || {};
      this.jobs.set(job.id, { ...previous, ...job, meta: { ...previous.meta, ...job.meta } });
      this.paint();
    },

    /* ------------------------------------------------------------- init */
    init() {
      if (typeof J === 'undefined' || !J.on) return;
      ['started', 'queued', 'progress', 'completed', 'failed', 'cancelled']
        .forEach((event) => J.on(`image.generation.${event}`, (data) => {
          this.track(data?.job || data);
        }));
    },
  };

  window.ImageStudio = ImageStudio;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ImageStudio.init());
  } else {
    ImageStudio.init();
  }
})();
