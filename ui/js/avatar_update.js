/* ==========================================================================
   AvatarStudio — refonte de l'avatar 3D depuis une image de référence.

   Modifie l'avatar de JARVIS en Blender (jamais le master) puis compare et
   itère. Chaque état est piloté par les événements RÉELS du backend :
     avatar.update_started   → carte de progression
     avatar.update_progress  → étape + barre
     avatar.update_completed → aperçus avant/après + accepter / rejeter
     avatar.update_failed    → erreur explicite
     avatar.revision_activated → badge « avatar actuel »

   UI liée aux endpoints /api/avatar/*. Aucune valeur n'est inventée ici.
   ========================================================================== */

const AvatarStudio = {
  BUILD: 'AVATAR_STUDIO_BUILD_1',
  STAGES: [
    ['analyzing', 'Analyse de la référence'],
    ['planning', 'Construction du plan'],
    ['modifying', 'Modification Blender'],
    ['rendering', 'Rendu des aperçus'],
    ['evaluating', 'Évaluation'],
  ],
  VIEWS: [
    ['preview_front.png', 'Face'],
    ['preview_34.png', '3/4'],
    ['preview_side.png', 'Profil'],
    ['preview_full.png', 'Full body'],
  ],
  STAGE_RANK: {},
  ACTIVE: null,
  cards: new Map(),
  pageRoot: null,
  pageSelectedRef: '',
  pageSelectedRev: '',
  _bound: false,

  init() {
    if (this.initialized) return;
    this.initialized = true;
    console.log('[AvatarStudio] init');
    this.STAGE_RANK = Object.fromEntries(this.STAGES.map(([id], i) => [id, i]));
    this.injectStyles();
    this.bind();
  },

  /* ------------------------------------------------------------- helpers */
  containers() {
    return [document.getElementById('convLog'), document.getElementById('consoleLog')]
      .filter(Boolean);
  },
  fileUrl(revId, name) {
    return `/api/avatar/revisions/${encodeURIComponent(revId)}/files/${name}`;
  },
  refImageUrl(refId) {
    return `/api/avatar/references/${encodeURIComponent(refId)}/image`;
  },

  /* ------------------------------------------------------- gabarit de carte */
  template(stageLabel) {
    const steps = this.STAGES.map(([id, label]) =>
      `<li data-step="${id}"><i></i><span>${label}</span></li>`).join('');
    return `
      <div class="who">JARVIS</div>
      <div class="bubble m3dbubble">
        <div class="m3dcard avatar-update-card" data-state="pending">
          <div class="m3dcard-head">
            <span class="m3dcard-kicker">Refonte Avatar · Blender</span>
            <span class="m3dcard-title" data-title>Mise à jour de l'avatar</span>
          </div>
          <div class="m3dcard-stage" data-stage-wrap>
            <span class="m3dcard-spinner" aria-hidden="true"></span>
            <span data-stage>${stageLabel}</span>
            <span class="m3dcard-pct" data-pct></span>
          </div>
          <div class="m3dcard-bar"><i data-bar style="width:3%"></i></div>
          <ul class="m3dcard-steps" data-steps>${steps}</ul>

          <div class="avatar-update-compare" data-compare hidden>
            <figure class="au-cmp">
              <figcaption>Avant · référence</figcaption>
              <div class="au-frame"><img data-ref-img alt="référence" /></div>
            </figure>
            <figure class="au-cmp">
              <figcaption>Après · nouvelles modifications</figcaption>
              <div class="au-frame au-after"><img data-preview alt="aperçu" /></div>
            </figure>
          </div>
          <div class="avatar-update-views" data-views hidden></div>
          <div class="m3dcard-facts" data-facts></div>
          <div class="m3dcard-error" data-error hidden></div>
          <div class="m3dcard-actions" data-actions hidden></div>
        </div>
      </div>`;
  },

  ensure(d) {
    const key = d.run_id || d.revision_id || 'avatar-update';
    let wrapper = this.cards.get(key);
    if (wrapper) return wrapper;

    window.App?.clearPendingReply?.();
    wrapper = document.createElement('div');
    wrapper.className = 'msg m3dmsg';
    wrapper.dataset.avatarRun = key;
    wrapper.innerHTML = this.template('Préparation');
    this.containers().forEach((c) => {
      c.appendChild(wrapper.cloneNode(true));
      c.scrollTop = c.scrollHeight;
    });
    const el = wrapper.querySelector('.m3dcard');
    if (d.reference_id) {
      const img = el.querySelector('[data-ref-img]');
      if (img) img.src = `/api/avatar/references/${encodeURIComponent(d.reference_id)}/image?t=${Date.now()}`;
    }
    this.cards.set(key, wrapper);
    return wrapper;
  },

  each(key, fn) {
    const wrapper = this.cards.get(key);
    if (!wrapper) return null;
    const el = wrapper.querySelector('.m3dcard');
    if (el) fn(el);
    return el;
  },

  scrollAll() {
    this.containers().forEach((c) => { c.scrollTop = c.scrollHeight; });
  },

  /* ------------------------------------------------------------ événements */
  started(d) {
    const wrapper = this.ensure(d);
    const el = wrapper.querySelector('.m3dcard');
    if (!el) return;
    el.dataset.state = 'running';
    const title = el.querySelector('[data-title]');
    if (title) title.textContent = 'Mise à jour de l\'avatar en cours';
    this.ACTIVE = d.run_id || null;
  },

  progress(d) {
    const key = d.run_id || d.revision_id || 'avatar-update';
    const wrapper = this.ensure({ ...d, reference_id: d.reference_id });
    const el = wrapper.querySelector('.m3dcard');
    if (!el) return;
    el.dataset.state = 'running';
    const bar = el.querySelector('[data-bar]');
    const stage = el.querySelector('[data-stage]');
    const pct = el.querySelector('[data-pct]');
    const p = Math.max(3, Math.round((d.progress || 0) * 100));
    if (bar) bar.style.width = p + '%';
    if (stage) stage.textContent = d.message || d.stage || 'En cours';
    if (pct) pct.textContent = p + '%';
    this.markSteps(el, d.stage);
  },

  markSteps(el, stage) {
    const rank = this.STAGE_RANK[stage];
    el.querySelectorAll('[data-step]').forEach((li) => {
      const own = this.STAGE_RANK[li.dataset.step];
      if (rank === undefined) return;
      li.classList.toggle('done', own < rank);
      li.classList.toggle('active', own === rank);
    });
  },

  async completed(d) {
    const key = d.run_id || d.revision_id || 'avatar-update';
    const wrapper = this.ensure({ ...d, revision_id: d.revision_id });
    const el = wrapper.querySelector('.m3dcard');
    if (!el) return;
    el.dataset.state = 'done';
    const bar = el.querySelector('[data-bar]');
    const stage = el.querySelector('[data-stage]');
    const pct = el.querySelector('[data-pct]');
    if (bar) bar.style.width = '100%';
    if (stage) stage.textContent = 'Nouveau visage prêt';
    if (pct) pct.textContent = '';
    el.querySelectorAll('[data-step]').forEach((li) => {
      li.classList.remove('active');
      li.classList.add('done');
    });

    if (d.revision_id) {
      const rev = await AvatarStudio.getRevision(d.revision_id);
      if (rev) AvatarStudio.renderRevision(el, rev);
    } else if (d.overall_score) {
      this.renderFacts(el, { evaluation: { overall_score: d.overall_score } });
    }
    this.scrollAll();
  },

  failed(d) {
    const key = d.run_id || d.revision_id || 'avatar-update';
    const wrapper = this.ensure({ ...d, revision_id: d.revision_id });
    const el = wrapper.querySelector('.m3dcard');
    if (!el) return;
    el.dataset.state = 'error';
    const stage = el.querySelector('[data-stage]');
    if (stage) stage.textContent = 'Mise à jour interrompue';
    const err = el.querySelector('[data-error]');
    if (err) {
      err.hidden = false;
      err.textContent = d.error || 'Le pipeline d\'avatar n\'a pas abouti.';
    }
    const actions = el.querySelector('[data-actions]');
    if (actions) {
      actions.hidden = false;
      actions.innerHTML = '<button class="btn sm" data-retry>Réessayer</button>';
      actions.querySelector('[data-retry]').onclick = () =>
        window.App?.send('Modifie ton avatar selon la dernière image de référence');
    }
  },

  /* -------------------------------------------------------------- aperçus */
  renderRevision(el, rev) {
    const revId = rev.id;
    el.dataset.revId = revId;
    const views = ['front', 'side', '34', 'full'];
    const existing = [];

    const compare = el.querySelector('[data-compare]');
    if (compare) {
      compare.hidden = false;
      const img = compare.querySelector('[data-preview]');
      if (img) {
        img.src = this.fileUrl(revId, 'preview_front.png') + '?t=' + Date.now();
        img.onerror = () => { img.closest('figure')?.classList.add('missing'); };
      }
    }

    const box = el.querySelector('[data-views]');
    if (box) {
      box.hidden = false;
      box.innerHTML = '<div class="au-views-label">Quatre angles</div>' + views.map((v) => {
        const label = { front: 'Face', side: 'Profil', '34': '3/4', full: 'Corps' }[v] || v;
        return `<figure class="au-view" data-view="${v}">
          <figcaption>${label}</figcaption>
          <div class="au-frame"><img data-src="${this.esc(this.fileUrl(revId, `preview_${v}.png`))}" alt="${label}" /></div>
        </figure>`;
      }).join('');
      box.querySelectorAll('img').forEach((img) => {
        img.src = img.dataset.src + '?t=' + Date.now();
        img.addEventListener('error', () => {
          img.closest('figure')?.classList.add('missing');
        }, { once: true });
      });
    }

    this.renderFacts(el, rev);
    this.renderActions(el, rev);
  },

  renderFacts(el, rev) {
    const box = el.querySelector('[data-facts]');
    if (!box) return;
    const evalData = rev.evaluation || rev.meta?.evaluation || {};
    const facts = [];
    const score = evalData.overall_score;
    if (Number.isFinite(score)) {
      facts.push(`score ${Math.round(score * 100)} / 100`);
    }
    const defects = evalData.defects || [];
    if (defects.length) facts.push(`${defects.length} point(s) à améliorer`);
    if (rev.active) facts.push('avatar actuel');
    box.innerHTML = facts.map((f) => `<span>${this.esc(f)}</span>`).join('');
  },

  renderActions(el, rev) {
    const box = el.querySelector('[data-actions]');
    if (!box) return;
    box.hidden = false;
    const active = rev.active;
    box.innerHTML = `
      <button class="btn sm primary" data-accept ${active ? 'disabled' : ''}>
        ${active ? 'Avatar actuel' : 'Adopter cet avatar'}</button>
      <button class="btn sm" data-rollback>Rejeter</button>`;

    const accept = box.querySelector('[data-accept]');
    if (accept && !active) {
      accept.onclick = async () => {
        const res = await J.post(`/api/avatar/revisions/${rev.id}/accept`, {});
        if (res.ok) {
          toast('Avatar adopté.', 'ok');
          this.markActive(rev.id);
        } else toast(res.error || 'Impossible d\'adopter.', 'err');
      };
    }
    const rollback = box.querySelector('[data-rollback]');
    rollback.onclick = async () => {
      if (!(await confirmDialog('Rejeter cette révision ?',
        'Cette version ne sera plus active. Les fichiers restent en place.'))) return;
      const res = await J.post(`/api/avatar/revisions/${rev.id}/rollback`, {});
      if (res.ok) {
        toast('Révision rejetée.');
        el.dataset.state = 'done';
      } else toast(res.error || 'Impossible de rejeter.', 'err');
    };
  },

  markActive(revId) {
    this.cards.forEach((wrapper) => {
      const el = wrapper.querySelector('.m3dcard');
      if (!el) return;
      const facts = el.querySelector('[data-facts]');
      if (!facts) return;
      facts.innerHTML = facts.innerHTML.replace(/<span>avatar actuel<\/span>/g, '');
      if (el.dataset.revId === revId) {
        facts.innerHTML += '<span>avatar actuel</span>';
      }
    });
  },

  /* ------------------------------------------------------------------ API */
  async getReferences() {
    const res = await J.get('/api/avatar/references?limit=30');
    return res.ok ? (res.references || []) : [];
  },
  async getRevision(revId) {
    const res = await J.get(`/api/avatar/revisions/${encodeURIComponent(revId)}`);
    return res.ok ? res.revision : null;
  },
  async getRevisions(referenceId = '') {
    const q = referenceId ? `?reference_id=${encodeURIComponent(referenceId)}` : '';
    const res = await J.get('/api/avatar/revisions' + q);
    return res.ok ? (res.revisions || []) : [];
  },
  async deleteReference(refId) {
    return J.del(`/api/avatar/references/${refId}`);
  },

  /** Conversation courante côté UI (J.state.conversation). */
  conversationId() {
    return (window.J && J.state && J.state.conversation) || '';
  },

  /** Upload d'une image (base64) → POST /api/avatar/references/upload */
  async uploadReference(file, type = 'mixed') {
    if (!file) return { ok: false, error: 'Aucun fichier.' };
    if (file.size > 3_000_000) {
      return { ok: false, error: 'Image trop volumineuse (max 3 Mo).' };
    }
    const dataB64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const raw = String(reader.result || '').split(',')[1] || '';
        resolve(raw);
      };
      reader.onerror = () => reject(new Error('Lecture du fichier impossible'));
      reader.readAsDataURL(file);
    });
    return J.post('/api/avatar/references/upload', {
      filename: file.name || 'reference.png',
      data_b64: dataB64,
      reference_type: type,
      conversation_id: this.conversationId(),
    });
  },

  /** Lance le pipeline complet (mode asynchrone, progression via SSE). */
  async run(refId, options = {}) {
    return J.post('/api/avatar/update', {
      reference_id: refId,
      options,
      background: true,
      max_iterations: 3,
      conversation_id: this.conversationId(),
    });
  },

  /* ------------------------------------------------------------ studio */
  async open() {
    if (!window.AvatarStudio) return;
    const m = modal({
      title: 'Refonte de l\'avatar',
      wide: true,
      body: `<div class="avatar-studio" id="avatarStudio">
        <section class="as-col">
          <div class="zone-kicker">IMAGE DE RÉFÉRENCE</div>
          <input type="file" id="asFile" accept="image/png,image/jpeg,image/webp,image/bmp,image/gif" />
          <div class="as-file-hint">PNG, JPG, WebP · max 3 Mo</div>
          <label class="as-select">Type de référence
            <select id="asType">
              <option value="mixed">Mixte (tout)</option>
              <option value="face">Visage seulement</option>
              <option value="outfit">Tenue seulement</option>
              <option value="style">Style artistique</option>
            </select>
          </label>
          <button class="btn primary sm" id="asUpload" disabled>Enregistrer la référence</button>
          <div id="asUploadStatus" class="as-status"></div>
        </section>
        <section class="as-col as-col-options">
          <div class="zone-kicker">OPTIONS DE MODIFICATION</div>
          <div class="as-options" id="asOptions">
            ${this.OPTION_FIELDS.map((o) =>
              `<label class="as-check"><input type="checkbox" value="${o.key}" ${o.on ? 'checked' : ''}/>${o.label}</label>`
            ).join('')}
          </div>
          <label class="as-range">Influence de la référence
            <input type="range" id="asStrength" min="0" max="1" step="0.05" value="0.6" />
            <b id="asStrengthVal">60 %</b>
          </label>
          <label class="as-select">Rendu
            <select id="asRealism">
              <option value="realistic">Réaliste</option>
              <option value="balanced" selected>Équilibré</option>
              <option value="cartoon">Cartoon / stylisé</option>
              <option value="anime">Anime / manga</option>
            </select>
          </label>
          <label class="as-check"><input type="checkbox" id="asIdentity" checked/>Conserver l'identité de JARVIS</label>
          <button class="btn primary sm" id="asRun" disabled>Lancer la refonte</button>
          <div class="as-status" id="asRunStatus"></div>
        </section>
        <section class="as-col">
          <div class="zone-kicker">RÉVISIONS</div>
          <div class="as-revisions" id="asRevisions"></div>
        </section>
      </div>`,
    });

    const scope = m.$;
    const refs = await this.getReferences();
    let selected = refs[0]?.id || '';
    let uploading = false;

    const renderRevs = async () => {
      const revs = await this.getRevisions(selected);
      const active = revs.find((r) => r.active);
      scope('#asRevisions').innerHTML = revs.length
        ? revs.map((r) => `
          <div class="as-rev ${r.id === active?.id ? 'active' : ''} ${r.id === selected ? 'selected' : ''}" data-rev="${r.id}">
            <img src="${this.fileUrl(r.id, 'preview_front.png')}?t=${Date.now()}" alt="aperçu"
              onerror="this.closest('.as-rev').classList.add('missing')" />
            <div class="as-rev-body">
              <b>${this.esc(fmtDateTime(r.created_at))}</b>
              <span>${r.active ? '· avatar actuel' : ''}${Number.isFinite(r.evaluation?.overall_score) ? ' · ' + Math.round(r.evaluation.overall_score * 100) + '/100' : ''}</span>
            </div>
          </div>`).join('')
        : '<div class="as-empty">Aucune révision. Lance une refonte.</div>';
      scope('#asRevisions').querySelectorAll('.as-rev').forEach((node) => {
        node.onclick = () => {
          scope('#asRevisions').querySelectorAll('.selected').forEach((n) => n.classList.remove('selected'));
          node.classList.add('selected');
          window.App?.send?.(`Affiche l'avatar de la révision ${node.dataset.rev} et dis-moi ce qui a changé`);
        };
      });
    };

    const refreshRefs = async () => {
      const list = await this.getReferences();
      if (!list) return;
      if (!selected || !list.some((r) => r.id === selected)) selected = list[0]?.id || '';
      scope('#asUploadStatus').textContent = list.length
        ? `${list.length} référence(s) disponible(s)`
        : 'Aucune référence. Ajoute une image.';
      scope('#asRun').disabled = !selected;
      await renderRevs();
    };

    const fileInput = scope('#asFile');
    fileInput.onchange = () => { scope('#asUpload').disabled = !fileInput.files.length; };
    scope('#asUpload').onclick = async () => {
      if (uploading || !fileInput.files.length) return;
      uploading = true;
      scope('#asUpload').disabled = true;
      scope('#asUploadStatus').textContent = 'Enregistrement…';
      const res = await this.uploadReference(fileInput.files[0], scope('#asType').value);
      uploading = false;
      if (res.ok && res.reference) {
        selected = res.reference.id;
        scope('#asUploadStatus').textContent = 'Référence enregistrée.';
        toast('Référence enregistrée.', 'ok');
        await refreshRefs();
      } else {
        scope('#asUploadStatus').textContent = res.error || 'Échec.';
      }
    };

    const updateStrength = () => {
      const v = Math.round((scope('#asStrength').value || 0.6) * 100);
      scope('#asStrengthVal').textContent = v + ' %';
    };
    const strength = scope('#asStrength');
    strength.oninput = updateStrength;

    scope('#asRun').onclick = async () => {
      if (!selected) return;
      const options = {};
      scope('#asOptions').querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        options[cb.value] = cb.checked;
      });
      options.preserve_identity = scope('#asIdentity').checked;
      options.style_strength = parseFloat(strength.value || 0.6);
      options.realism_level = scope('#asRealism').value;
      scope('#asRun').disabled = true;
      scope('#asRunStatus').textContent = 'Lancement du pipeline Blender…';
      const res = await this.run(selected, options);
      if (res.ok) {
        scope('#asRunStatus').textContent = 'Refonte lancée — suivi dans la conversation.';
        m.close();
      } else {
        scope('#asRun').disabled = false;
        scope('#asRunStatus').textContent = res.error || 'Échec du lancement.';
      }
    };

    await refreshRefs();
    updateStrength();
  },

  /* ------------------------------------------------------------- styles */
  injectStyles() {
    if (document.getElementById('avatar-update-styles')) return;
    const style = document.createElement('style');
    style.id = 'avatar-update-styles';
    style.textContent = `
    .avatar-update-card[data-state="done"] .m3dcard-spinner{display:none}
    .avatar-update-compare{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
    .avatar-update-views{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px}
    .au-views-label{font-size:9px;letter-spacing:.14em;color:var(--text-faint);margin-bottom:4px}
    .au-cmp,.au-view{margin:0}
    .au-cmp figcaption,.au-view figcaption{font-size:9px;letter-spacing:.1em;color:var(--text-faint);margin-bottom:3px}
    .au-frame{position:relative;aspect-ratio:1/1;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 30%,rgba(34,211,238,.08),transparent 70%),#05070a}
    .au-frame img{width:100%;height:100%;object-fit:contain}
    .au-cmp.missing img,.au-view.missing img{opacity:.18}
    .au-cmp.missing::after,.au-view.missing::after{content:"aperçu manquant";position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text-faint)}
    .avatar-studio{display:grid;grid-template-columns:220px 1fr 220px;gap:18px}
    @media (max-width:900px){.avatar-studio{grid-template-columns:1fr}}
    .avatar-studio .as-col{min-width:0}
    .as-file-hint{font-size:10px;color:var(--text-faint);margin:4px 0 10px}
    .as-select{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--text-dim);margin:8px 0}
    .as-select select,.as-status{width:100%}
    .as-options{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
    .as-check{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text-dim);border:1px solid var(--line);border-radius:8px;padding:4px 8px;background:rgba(255,255,255,.02)}
    .as-check input{accent-color:var(--cyan)}
    .as-range{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-dim);margin:8px 0}
    .as-range input{flex:1}
    .as-range b{width:38px;text-align:right;font-variant-numeric:tabular-nums}
    .as-revisions{display:flex;flex-direction:column;gap:8px;max-height:340px;overflow:auto}
    .as-rev{display:flex;gap:8px;align-items:center;border:1px solid var(--line);border-radius:10px;padding:5px;cursor:pointer;background:rgba(255,255,255,.02)}
    .as-rev:hover{border-color:var(--cyan)}
    .as-rev.selected{border-color:var(--cyan);box-shadow:0 0 0 1px var(--cyan)}
    .as-rev.active{outline:1px solid var(--ok)}
    .as-rev img{width:56px;height:56px;object-fit:cover;border-radius:8px;background:#05070a}
    .as-rev-body{display:flex;flex-direction:column;font-size:10px;color:var(--text-dim);gap:2px;min-width:0}
    .as-rev-body b{color:var(--text)}
    .as-empty,.as-status{font-size:11px;color:var(--text-faint);margin-top:6px}
    .as-page{display:flex;flex-direction:column;gap:14px}
    .as-views{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
    @media (max-width:900px){.as-views{grid-template-columns:repeat(2,1fr)}}
    .as-upload-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
    .as-upload-row input[type=file]{flex:1;min-width:160px}
    .as-ref-preview{margin:10px 0;aspect-ratio:1/1;max-height:220px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#05070a}
    .as-ref-preview img{width:100%;height:100%;object-fit:contain}
    .as-ref-list{display:flex;flex-direction:column;gap:6px;max-height:180px;overflow:auto}
    .as-ref-item{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:8px;padding:4px 8px;background:transparent;color:var(--text-dim);cursor:pointer;text-align:left}
    .as-ref-item img{width:36px;height:36px;object-fit:cover;border-radius:6px}
    .as-ref-item.selected{border-color:var(--cyan);color:var(--text)}
    .as-page-actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}
    `;
    document.head.appendChild(style);
  },

  /* ------------------------------------------------------------- câblage */
  bind() {
    if (this._bound) return;
    this._bound = true;
    J.on('avatar.update_started', (d) => this.started(d));
    J.on('avatar.update_progress', (d) => this.progress(d));
    J.on('avatar.update_completed', (d) => {
      this.completed(d);
      this.refreshPage();
    });
    J.on('avatar.update_failed', (d) => {
      this.failed(d);
      this.refreshPage();
    });
    J.on('avatar.revision_activated', (d) => {
      if (d.revision_id) this.markActive(d.revision_id);
      this.refreshPage();
    });
  },

  /* ---------------------------------------------------------- page studio */
  async mountPage(el) {
    this.restoreAvatarStage();
    console.log('[AvatarStudio] UI mounted');
    this.pageRoot = el;
    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Avatar Studio</h1>
          <p>Refonte de l'avatar 3D depuis une image de référence. Le master .blend n'est jamais écrasé.</p>
        </div>
        <div class="page-actions">
          <span class="tag cy" id="asBuildBadge">${this.esc(this.BUILD)}</span>
          <button class="btn" id="asOpenModal">${icon('edit', 13)} Atelier (modale)</button>
          <button class="btn primary" id="asModify">${icon('avatar', 13)} Modifier l'avatar</button>
        </div>
      </div>
      <div class="as-page">
        <div class="grid-2">
          <div class="card"><div class="card-head"><h2>AVATAR ACTUEL</h2>
            <span class="tools" id="asCurrentMeta">—</span></div>
            <div class="card-body"><div class="as-views" id="asCurrentViews"></div></div></div>
          <div class="card"><div class="card-head"><h2>IMAGE DE RÉFÉRENCE</h2></div>
            <div class="card-body">
              <div class="as-upload-row">
                <input type="file" id="asPageFile" accept="image/png,image/jpeg,image/webp,image/bmp,image/gif" />
                <select id="asPageType">
                  <option value="mixed">Mixte</option>
                  <option value="face">Visage</option>
                  <option value="outfit">Tenue</option>
                  <option value="style">Style</option>
                </select>
                <button class="btn primary sm" id="asPageUpload" disabled>Uploader</button>
              </div>
              <div class="as-status" id="asPageUploadStatus">PNG, JPG, WebP · max 3 Mo</div>
              <div class="as-ref-preview" id="asRefPreview"></div>
              <div class="as-ref-list" id="asRefList"></div>
            </div></div>
        </div>
        <div class="card"><div class="card-head"><h2>RENDUS</h2>
          <span class="tools">Face · 3/4 · Profil · Full body</span></div>
          <div class="card-body"><div class="as-views" id="asRenders"></div></div></div>
        <div class="grid-2">
          <div class="card"><div class="card-head"><h2>AVANT / APRÈS</h2></div>
            <div class="card-body"><div class="avatar-update-compare" id="asCompare"></div></div></div>
          <div class="card"><div class="card-head"><h2>OPTIONS</h2></div>
            <div class="card-body">
              <div class="as-options" id="asPageOptions">
                ${this.OPTION_FIELDS.map((o) =>
                  `<label class="as-check"><input type="checkbox" value="${o.key}" ${o.on ? 'checked' : ''}/>${o.label}</label>`
                ).join('')}
              </div>
              <label class="as-range">Influence
                <input type="range" id="asPageStrength" min="0" max="1" step="0.05" value="0.6" />
                <b id="asPageStrengthVal">60 %</b>
              </label>
              <label class="as-select">Rendu
                <select id="asPageRealism">
                  <option value="realistic">Réaliste</option>
                  <option value="balanced" selected>Équilibré</option>
                  <option value="cartoon">Cartoon / stylisé</option>
                  <option value="anime">Anime / manga</option>
                </select>
              </label>
              <label class="as-check"><input type="checkbox" id="asPageIdentity" checked/>Conserver l'identité de JARVIS</label>
              <div class="as-page-actions">
                <button class="btn primary sm" id="asPageRun" disabled>Lancer la refonte</button>
                <button class="btn sm" id="asPageAccept" disabled>Accepter</button>
                <button class="btn sm" id="asPageReject" disabled>Rejeter</button>
                <button class="btn sm" id="asPageRollback" disabled>Rollback</button>
              </div>
              <div class="as-status" id="asPageRunStatus"></div>
            </div></div>
        </div>
        <div class="card"><div class="card-head"><h2>HISTORIQUE DES RÉVISIONS</h2></div>
          <div class="card-body"><div class="as-revisions" id="asPageRevs"></div></div></div>
      </div>`;

    const $p = (sel) => el.querySelector(sel);
    $p('#asOpenModal').onclick = () => this.open();
    $p('#asModify').onclick = () => this.open();
    $p('#asPageStrength').oninput = () => {
      $p('#asPageStrengthVal').textContent = Math.round(($p('#asPageStrength').value || 0.6) * 100) + ' %';
    };
    const fileInput = $p('#asPageFile');
    fileInput.onchange = () => { $p('#asPageUpload').disabled = !fileInput.files.length; };
    $p('#asPageUpload').onclick = async () => {
      if (!fileInput.files.length) return;
      $p('#asPageUpload').disabled = true;
      $p('#asPageUploadStatus').textContent = 'Enregistrement…';
      const res = await this.uploadReference(fileInput.files[0], $p('#asPageType').value);
      if (res.ok && res.reference) {
        this.pageSelectedRef = res.reference.id;
        $p('#asPageUploadStatus').textContent = 'Référence enregistrée.';
        toast('Référence enregistrée.', 'ok');
        fileInput.value = '';
        await this.refreshPage();
      } else {
        $p('#asPageUpload').disabled = false;
        $p('#asPageUploadStatus').textContent = res.error || 'Échec.';
      }
    };
    $p('#asPageRun').onclick = async () => {
      if (!this.pageSelectedRef) return;
      const options = {};
      $p('#asPageOptions').querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        options[cb.value] = cb.checked;
      });
      options.preserve_identity = $p('#asPageIdentity').checked;
      options.style_strength = parseFloat($p('#asPageStrength').value || 0.6);
      options.realism_level = $p('#asPageRealism').value;
      $p('#asPageRun').disabled = true;
      $p('#asPageRunStatus').textContent = 'Lancement du pipeline Blender…';
      const res = await this.run(this.pageSelectedRef, options);
      if (res.ok) {
        $p('#asPageRunStatus').textContent = 'Refonte lancée — suivi dans la conversation.';
      } else {
        $p('#asPageRun').disabled = false;
        $p('#asPageRunStatus').textContent = res.error || 'Échec du lancement.';
      }
    };
    const act = async (kind) => {
      const id = this.pageSelectedRev;
      if (!id) return;
      if (kind !== 'accept') {
        if (!(await confirmDialog(
          kind === 'rollback' ? 'Rollback de cette révision ?' : 'Rejeter cette révision ?',
          'Cette version ne sera plus active. Les fichiers restent en place.'))) return;
      }
      const path = kind === 'accept'
        ? `/api/avatar/revisions/${id}/accept`
        : `/api/avatar/revisions/${id}/rollback`;
      const res = await J.post(path, {});
      if (res.ok) {
        toast(kind === 'accept' ? 'Avatar adopté.' : 'Révision rejetée.', kind === 'accept' ? 'ok' : '');
        await this.refreshPage();
      } else toast(res.error || 'Action impossible.', 'err');
    };
    $p('#asPageAccept').onclick = () => act('accept');
    $p('#asPageReject').onclick = () => act('reject');
    $p('#asPageRollback').onclick = () => act('rollback');
    await this.refreshPage();
  },

  viewsHtml(revId) {
    if (!revId) {
      return this.VIEWS.map(([, label]) =>
        `<figure class="au-view missing"><figcaption>${label}</figcaption>
           <div class="au-frame"></div></figure>`).join('');
    }
    const t = Date.now();
    return this.VIEWS.map(([file, label]) =>
      `<figure class="au-view"><figcaption>${label}</figcaption>
         <div class="au-frame"><img src="${this.fileUrl(revId, file)}?t=${t}" alt="${label}"
           onerror="this.closest('.au-view').classList.add('missing')"/></div></figure>`).join('');
  },

  restoreAvatarStage() {
    if (this.avatarStageHome && this.avatarStage) {
      this.avatarStageHome.appendChild(this.avatarStage);
      this.avatarStage.style.height = this.avatarStageHeight;
      this.avatarStageHome = null;
    }
  },

  async refreshPage() {
    const el = this.pageRoot;
    if (!el || !document.body.contains(el) || (window.J && J.state.page !== 'avatar-studio')) return;
    const refs = await this.getReferences();
    const bundle = await J.get('/api/avatar/revisions?limit=30');
    const revs = bundle.ok ? (bundle.revisions || []) : [];
    const active = bundle.ok ? bundle.active : null;
    if (!this.pageSelectedRef || !refs.some((r) => r.id === this.pageSelectedRef)) {
      this.pageSelectedRef = refs[0]?.id || '';
    }
    if (!this.pageSelectedRev || !revs.some((r) => r.id === this.pageSelectedRev)) {
      this.pageSelectedRev = active?.id || revs[0]?.id || '';
    }
    const current = active || null;
    const $p = (sel) => el.querySelector(sel);
    const currentViews = $p('#asCurrentViews');
    const renders = $p('#asRenders');
    if (currentViews) {
      if (current) {
        this.restoreAvatarStage();
        currentViews.innerHTML = this.viewsHtml(current.id);
      } else if (!currentViews.contains(this.avatarStage)) {
        // Réutilise le vrai avatar déjà chargé, y compris avant toute révision.
        const stage = document.querySelector('#robotStage')?.parentElement;
        if (stage) {
          this.avatarStage = stage;
          this.avatarStageHome = stage.parentElement;
          this.avatarStageHeight = stage.style.height;
          currentViews.innerHTML = '';
          currentViews.style.display = 'block';
          stage.style.height = '300px';
          currentViews.appendChild(stage);
        }
      }
    }
    if (renders) renders.innerHTML = this.viewsHtml(this.pageSelectedRev || current?.id);
    const meta = $p('#asCurrentMeta');
    if (meta) {
      meta.textContent = current
        ? (current.active ? 'actif · ' : '') + (fmtDateTime(current.created_at) || current.id)
        : 'avatar par défaut (master)';
    }
    const preview = $p('#asRefPreview');
    if (preview) {
      preview.innerHTML = this.pageSelectedRef
        ? `<img src="${this.refImageUrl(this.pageSelectedRef)}?t=${Date.now()}" alt="référence"
             onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'as-empty',textContent:'image indisponible'}))" />`
        : '<div class="as-empty">Aucune référence sélectionnée.</div>';
    }
    const list = $p('#asRefList');
    if (list) {
      list.innerHTML = refs.length
        ? refs.map((r) => `
            <button class="as-ref-item ${r.id === this.pageSelectedRef ? 'selected' : ''}" data-ref="${r.id}">
              <img src="${this.refImageUrl(r.id)}" alt="" onerror="this.style.display='none'" />
              <span>${this.esc(r.reference_type || 'mixed')} · ${this.esc(fmtDateTime(r.created_at) || r.id)}</span>
            </button>`).join('')
        : '<div class="as-empty">Aucune référence. Uploade une image.</div>';
      list.querySelectorAll('[data-ref]').forEach((btn) => {
        btn.onclick = () => {
          this.pageSelectedRef = btn.dataset.ref;
          this.refreshPage();
        };
      });
    }
    const compare = $p('#asCompare');
    if (compare) {
      const afterId = this.pageSelectedRev || current?.id;
      compare.innerHTML = `
        <figure class="au-cmp ${this.pageSelectedRef ? '' : 'missing'}">
          <figcaption>Avant · référence</figcaption>
          <div class="au-frame">${this.pageSelectedRef
            ? `<img src="${this.refImageUrl(this.pageSelectedRef)}?t=${Date.now()}" alt="référence" />` : ''}</div>
        </figure>
        <figure class="au-cmp ${afterId ? '' : 'missing'}">
          <figcaption>Après · révision</figcaption>
          <div class="au-frame">${afterId
            ? `<img src="${this.fileUrl(afterId, 'preview_front.png')}?t=${Date.now()}" alt="aperçu"
                 onerror="this.closest('.au-cmp').classList.add('missing')" />` : ''}</div>
        </figure>`;
    }
    const revBox = $p('#asPageRevs');
    if (revBox) {
      revBox.innerHTML = revs.length
        ? revs.map((r) => `
            <div class="as-rev ${r.active ? 'active' : ''} ${r.id === this.pageSelectedRev ? 'selected' : ''}" data-rev="${r.id}">
              <img src="${this.fileUrl(r.id, 'preview_front.png')}?t=${Date.now()}" alt="aperçu"
                onerror="this.closest('.as-rev').classList.add('missing')" />
              <div class="as-rev-body">
                <b>${this.esc(fmtDateTime(r.created_at))}</b>
                <span>${r.active ? '· avatar actuel' : ''}${Number.isFinite(r.evaluation?.overall_score)
                  ? ' · ' + Math.round(r.evaluation.overall_score * 100) + '/100' : ''}</span>
              </div>
            </div>`).join('')
        : '<div class="as-empty">Aucune révision. Lance une refonte.</div>';
      revBox.querySelectorAll('[data-rev]').forEach((node) => {
        node.onclick = () => {
          this.pageSelectedRev = node.dataset.rev;
          this.refreshPage();
        };
      });
    }
    const hasRef = !!this.pageSelectedRef;
    const hasRev = !!this.pageSelectedRev;
    const runBtn = $p('#asPageRun');
    if (runBtn) runBtn.disabled = !hasRef;
    ['#asPageAccept', '#asPageReject', '#asPageRollback'].forEach((sel) => {
      const b = $p(sel);
      if (b) b.disabled = !hasRev;
    });
  },
};

/* Champs d'options modifiant l'avatar (miroir du backend). */
AvatarStudio.OPTION_FIELDS = [
  { key: 'modify_face', label: 'Visage', on: true },
  { key: 'modify_hair', label: 'Coiffure', on: true },
  { key: 'modify_outfit', label: 'Tenue' },
  { key: 'modify_colors', label: 'Couleurs' },
  { key: 'modify_materials', label: 'Matériaux' },
  { key: 'modify_pose', label: 'Pose' },
  { key: 'modify_proportions', label: 'Proportions' },
];

/* Réutilise l'échappement m3dcard s'il existe, sinon tombe sur core.js `esc`. */
Object.defineProperty(AvatarStudio, 'esc', {
  value: (value) => (typeof esc === 'function' ? esc(value) : String(value)),
  enumerable: false,
});

window.AVATAR_STUDIO_BUILD = AvatarStudio.BUILD;
window.AvatarStudio = AvatarStudio;
if (window.Pages) {
  Pages['avatar-studio'] = (el) => AvatarStudio.mountPage(el);
}
console.log('[AvatarStudio] script loaded');
