/* ==========================================================================
   Settings — General, Appearance, Voice, AI Providers, Connectors, Tools,
   Memory, Security, Automation, Notifications, Developer, Logs.
   ========================================================================== */
const Settings = {
  section: 'general',
  data: null,
  models: [],

  SECTIONS: [
    ['general', 'General'], ['appearance', 'Appearance'], ['voice', 'Voice'], ['image', 'Image Generation'],
    ['ai', 'AI Providers'], ['connectors', 'Connectors'], ['blender', 'Atelier 3D'],
    ['tools', 'Tools'], ['editor', 'Code Editor'],
    ['memory', 'Memory'], ['security', 'Security'], ['automation', 'Automation'],
    ['notifications', 'Notifications'], ['developer', 'Developer'], ['logs', 'Logs'],
  ],

  async render(el, section) {
    if (section) this.section = section;
    const res = await J.get('/api/settings');
    this.data = res.settings || {};
    this.models = res.models || [];
    el.innerHTML = `
      <div class="page-head"><div><h1>Settings</h1>
        <p>Réglages persistés — ils survivent au redémarrage.</p></div></div>
      <div class="settings-layout">
        <div class="settings-nav">${this.SECTIONS.map(([id, label]) =>
          `<button data-sec="${id}" class="${id === this.section ? 'active' : ''}">${label}</button>`).join('')}</div>
        <div id="settingsPane"></div>
      </div>`;
    $$('[data-sec]', el).forEach((b) => b.onclick = () => {
      this.section = b.dataset.sec;
      this.render(el);
    });
    this.renderPane($('#settingsPane', el));
  },

  async renderPane(pane) {
    const fn = this['pane_' + this.section];
    pane.innerHTML = '<div class="empty">Chargement…</div>';
    if (fn) await fn.call(this, pane);
  },

  /* helpers ---------------------------------------------------------- */
  field(label, inputHtml, hint) {
    return `<div class="field"><label>${esc(label)}</label>${inputHtml}
      ${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>`;
  },
  toggle(label, key, value, { locked, hint } = {}) {
    return `<div class="field row"><div style="flex:1">
        <label style="margin:0">${esc(label)}</label>
        ${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>
      <button class="switch ${value ? 'on' : ''} ${locked ? 'locked' : ''}"
        data-toggle="${key}" ${locked ? 'disabled' : ''}></button></div>`;
  },
  async save(section, values) {
    const res = await J.put(`/api/settings/${section}`, values);
    if (res.ok) {
      toast('Réglages enregistrés.', 'ok');
      this.data[section] = res.values;
      J.state.settings = J.state.settings || {};
      J.state.settings[section] = res.values;
      if (section === 'voice') VoiceManager.updateSettings(res.values);
      if (section === 'appearance') App.applyAppearance(res.values);
      if (section === 'general') Dashboard.refresh();
      if (section === 'editor' && typeof CodeEnv !== 'undefined' && CodeEnv.applyEditorSettings) CodeEnv.applyEditorSettings();
    } else toast(res.error || 'Échec.', 'err');
    return res;
  },
  bindToggles(pane, section, onChange) {
    $$('[data-toggle]', pane).forEach((b) => b.onclick = async () => {
      if (b.disabled) return;
      const key = b.dataset.toggle;
      const value = !b.classList.contains('on');
      b.classList.toggle('on', value);
      await this.save(section, { [key]: value });
      if (onChange) onChange(key, value);
    });
  },

  /* ------------------------------------------------------------ GENERAL */
  async pane_general(pane) {
    const g = this.data.general || {};
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>GÉNÉRAL</h2></div><div class="card-body">
      ${this.field('Nom de l\'assistant', `<input data-k="assistant_name" value="${esc(g.assistant_name)}"/>`)}
      ${this.field('Ton prénom', `<input data-k="user_name" value="${esc(g.user_name)}"/>`)}
      ${this.field('Titre affiché', `<input data-k="operator_title" value="${esc(g.operator_title)}"/>`)}
      ${this.field('Langue', `<input data-k="language" value="${esc(g.language)}"/>`)}
      ${this.field('Lieu (affiché en bas de l\'écran)', `<input data-k="location" value="${esc(g.location)}" placeholder="Île-de-France"/>`)}
      ${this.field('Projet par défaut', `<input data-k="default_project" value="${esc(g.default_project)}" placeholder="/Users/jerome/mon-projet"/>`,
        'Utilisé par les outils de code et de déploiement quand aucun chemin n\'est précisé.')}
      ${this.toggle('Ouvrir l\'interface au démarrage', 'launch_ui_on_start', g.launch_ui_on_start)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'general');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => values[i.dataset.k] = i.value);
      this.save('general', values);
    };
  },

  /* --------------------------------------------------------- APPEARANCE */
  async pane_appearance(pane) {
    const a = this.data.appearance || {};
    const quality = ['low', 'balanced', 'ultra'].includes(a.quality) ? a.quality : 'balanced';
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>APPARENCE</h2></div><div class="card-body">
      ${this.field('Couleur d\'accent', `<input type="color" data-k="accent" value="${esc(a.accent || '#22d3ee')}" style="height:38px"/>`)}
      ${this.toggle('Animations', 'animations', a.animations)}
      ${this.toggle('Entité 3D (JARVIS & Brain Atlas)', 'sphere', a.sphere)}
      ${this.field('Qualité 3D', `<select data-k="quality">
          <option value="low" ${quality === 'low' ? 'selected' : ''}>Low (basse)</option>
          <option value="balanced" ${quality === 'balanced' ? 'selected' : ''}>Balanced (équilibrée)</option>
          <option value="ultra" ${quality === 'ultra' ? 'selected' : ''}>Ultra (élevée)</option></select>`,
        'La qualité se reflète dans le nombre de particules et l\u2019anti-aliasing des scènes 3D.')}
      ${this.toggle('Sidebar compacte', 'compact_sidebar', a.compact_sidebar)}
      ${this.toggle('Horloge 24 h', 'clock_24h', a.clock_24h)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'appearance', () => App.applyAppearance(this.data.appearance));
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => values[i.dataset.k] = i.value);
      this.save('appearance', values);
    };
  },

  /* -------------------------------------------------------------- VOICE */
  async pane_voice(pane) {
    const v = this.data.voice || {};
    const provider = v.tts_provider || 'browser';
    // Voix Piper (locales) : toujours interrogées, affichées selon le moteur.
    let piperVoices = [];
    let piperEngine = {};
    try {
      const tv = await J.get('/api/tts/voices');
      if (tv.ok) { piperVoices = tv.voices || []; piperEngine = tv.engine || {}; }
    } catch { /* moteur indisponible */ }
    const piperInstalled = (piperVoices || []).filter((x) => x.installed);
    const voices = ('speechSynthesis' in window ? speechSynthesis.getVoices() : [])
      .filter((x) => x.lang.startsWith((v.language || 'fr').slice(0, 2)) || true);
    const voiceOptions = provider === 'piper'
      ? `<option value="">Automatique</option>` + piperVoices.map((x) =>
          `<option value="${esc(x.id)}" ${v.voice === x.id ? 'selected' : ''}>${esc(x.label)}${x.installed ? '' : ' (à télécharger)'}</option>`).join('')
      : `<option value="">Automatique</option>` + voices.map((x) =>
          `<option value="${esc(x.name)}" ${v.voice === x.name ? 'selected' : ''}>${esc(x.name)} (${esc(x.lang)})</option>`).join('');
    pane.innerHTML = `
      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>MESSAGE D'ACCUEIL</h2></div>
      <div class="card-body">
        ${this.toggle('Saluer au démarrage d\'une session', 'greeting_enabled', v.greeting_enabled,
          { hint: 'JARVIS dit bonjour une seule fois, au début d\'une vraie session utilisateur.' })}
        ${this.field('Texte', `<input data-k="greeting_text" value="${esc(v.greeting_text)}"/>`,
          '{user} est remplacé par ton prénom.')}
        ${this.field('Fréquence', `<select disabled><option>Une seule fois par session</option></select>`)}
        <div class="locked-note">${icon('shield', 11)}
          Verrouillé sur « once_per_session » : aucune option ne peut faire répéter le message.</div>
        ${this.field('Nouvelle session après (heures d\'inactivité)',
          `<input type="number" min="1" max="72" data-k="session_idle_reset_hours" value="${v.session_idle_reset_hours}"/>`,
          'En dessous de ce délai, un rechargement de page reprend la session sans resaluer.')}
        <button class="btn primary" data-save-greeting>Enregistrer</button>
      </div></div>

      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>ÉCOUTE</h2></div><div class="card-body">
        ${this.field('Mode', `<select data-k="mode">
          ${[['push_to_talk', 'Push to Talk'], ['always_listening', 'Always Listening'],
             ['wake_word', 'Wake Word'], ['conversation', 'Conversation Mode']]
            .map(([id, label]) => `<option value="${id}" ${v.mode === id ? 'selected' : ''}>${label}</option>`).join('')}
        </select>`)}
        ${this.field('Mot d\'éveil', `<input data-k="wake_word" value="${esc(v.wake_word)}"/>`)}
        ${this.field('Sensibilité du mot d\'éveil',
          `<input type="range" min="0" max="1" step="0.05" data-k="wake_word_sensitivity" value="${v.wake_word_sensitivity}"/>`)}
        ${this.field('Réponse au mot d\'éveil', `<input data-k="wake_ack" value="${esc(v.wake_ack)}"/>`,
          'Très court. Jamais le message d\'accueil complet.')}
        ${this.toggle('Répondre au mot d\'éveil', 'wake_ack_enabled', v.wake_ack_enabled)}
        ${this.field('Fenêtre de conversation (s)',
          `<input type="number" data-k="conversation_window_s" value="${v.conversation_window_s}"/>`,
          'En mode conversation, durée pendant laquelle tu n\'as plus besoin de dire « Jarvis ».')}
        ${this.field('Timeout de silence (s)',
          `<input type="number" data-k="silence_timeout_s" value="${v.silence_timeout_s}"/>`,
          'Retour silencieux en veille — ne redéclenche jamais de message.')}
        ${this.toggle('Interruption possible pendant que JARVIS parle', 'interruptible_speech', v.interruptible_speech,
          { hint: 'Désactivé, le micro est suspendu pendant la parole : JARVIS ne peut pas s\'entendre lui-même.' })}
        <button class="btn primary" data-save-listen>Enregistrer</button>
      </div></div>

      <div class="card"><div class="card-head"><h2>SYNTHÈSE VOCALE</h2></div><div class="card-body">
        ${this.toggle('Lire les réponses à voix haute', 'speak_responses', v.speak_responses)}
        ${this.field('Moteur de synthèse', `<select data-engine-select data-k="tts_provider">
          <option value="browser" ${provider === 'browser' ? 'selected' : ''}>Browser (Web Speech API)</option>
          <option value="piper" ${provider === 'piper' ? 'selected' : ''}>Piper (voix locales françaises)</option>
        </select>`, provider === 'piper'
          ? (piperEngine.available
              ? `${piperInstalled.length} voix française(s) installée(s) · ${esc(piperEngine.voices_dir || '')}`
              : 'Moteur piper non trouvé sur le serveur.')
          : 'Synthèse du navigateur : aucune donnée ne quitte ta machine.')
          }
        ${this.field('Voix', `<select data-voice-select data-k="voice">${voiceOptions}</select>`)}
        ${this.field('Vitesse', `<input type="range" min="0.5" max="1.6" step="0.05" data-k="speech_rate" value="${v.speech_rate}"/>`)}
        <div data-pitch-row style="${provider === 'piper' ? 'display:none' : ''}">
          ${this.field('Hauteur', `<input type="range" min="0.5" max="1.5" step="0.05" data-k="pitch" value="${v.pitch}"/>`)}
        </div>
        ${this.field('Volume', `<input type="range" min="0" max="1" step="0.05" data-k="volume" value="${v.volume}"/>`)}
        <div style="display:flex;gap:7px">
          <button class="btn primary" data-save-tts>Enregistrer</button>
          <button class="btn" data-test-voice>${icon('mic', 12)} Tester la voix</button>
        </div>
      </div></div>`;

    this.bindToggles(pane, 'voice', () => VoiceManager.updateSettings(this.data.voice));

    // Bascule Browser ↔ Piper : reconstruit la liste de voix et masque la hauteur.
    const voiceOptionsFor = (eng, prev) => eng === 'piper'
      ? [`<option value="">Automatique</option>`].concat(piperVoices.map((x) =>
          `<option value="${esc(x.id)}"${x.id === prev ? ' selected' : ''}>${esc(x.label)}${x.installed ? '' : ' (à télécharger)'}</option>`)).join('')
      : [`<option value="">Automatique</option>`].concat(voices.map((x) =>
          `<option value="${esc(x.name)}"${x.name === prev ? ' selected' : ''}>${esc(x.name)} (${esc(x.lang)})</option>`)).join('');
    const engineSel = $('[data-engine-select]', pane);
    const voiceSel = $('[data-voice-select]', pane);
    const pitchRow = $('[data-pitch-row]', pane);
    if (engineSel) engineSel.onchange = () => {
      voiceSel.innerHTML = voiceOptionsFor(engineSel.value, voiceSel.value || v.voice);
      if (pitchRow) pitchRow.style.display = engineSel.value === 'piper' ? 'none' : '';
    };

    const collect = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => {
        values[i.dataset.k] = i.type === 'number' || i.type === 'range' ? Number(i.value) : i.value;
      });
      return values;
    };
    ['[data-save-greeting]', '[data-save-listen]', '[data-save-tts]'].forEach((sel) => {
      const btn = $(sel, pane);
      if (btn) btn.onclick = async () => { await this.save('voice', collect()); };
    });
    $('[data-test-voice]', pane).onclick = () => {
      VoiceManager.updateSettings({ ...this.data.voice, ...collect() });
      VoiceManager.speak('Systèmes opérationnels. Je suis à ton écoute.', { kind: 'test' });
    };
  },

  /* -------------------------------------------------------- AI PROVIDERS */
  async pane_ai(pane) {
    const ai = this.data.ai || {};
    const llm = await J.get('/api/llm');
    const providers = llm.providers || [];
    const options = (current) => `<option value="">Automatique</option>` + this.models.map((m) =>
      `<option value="${esc(m.value)}" ${current === m.value ? 'selected' : ''}>${esc(m.label)}</option>`).join('');
    pane.innerHTML = `
      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>FOURNISSEURS</h2>
        <button class="link-more" data-add-provider>Ajouter un fournisseur ›</button></div>
      <div class="card-body"><div class="list">
        ${providers.map((p) => `<div class="list-row">
          <div class="meta"><b>${esc(p.name)}</b><small>${esc(p.detail)}</small></div>
          <span class="tag ${p.connected ? 'ok' : ''}">${p.connected ? 'Connecté' : 'Non connecté'}</span>
          ${p.id ? `<div class="acts">
            <button class="btn sm" data-test-provider="${esc(p.id)}">Tester</button>
            <button class="btn sm" data-edit-provider="${esc(p.id)}">${icon('edit', 11)}</button></div>`
            : `<div class="acts"><button class="btn sm" data-new-provider="${esc(p.type)}">Configurer</button></div>`}
        </div>`).join('')}
      </div></div></div>

      <div class="card"><div class="card-head"><h2>RÔLES DE MODÈLES</h2></div><div class="card-body">
        ${this.field('Modèle par défaut', `<select data-k="default_model">${options(ai.default_model)}</select>`)}
        ${this.field('Modèle rapide', `<select data-k="fast_model">${options(ai.fast_model)}</select>`)}
        ${this.field('Modèle de raisonnement', `<select data-k="reasoning_model">${options(ai.reasoning_model)}</select>`)}
        ${this.field('Modèle de code', `<select data-k="coding_model">${options(ai.coding_model)}</select>`)}
        ${this.field('Modèle 3D (spécialiste Blender)', `<select data-k="blender_model">${options(ai.blender_model)}</select>`,
          'Modèle dédié 3D (ex. jarvis-blender) utilisé pour Blender/avatar. Vide = auto-détection.')}
        ${this.field('Modèle de secours', `<select data-k="fallback_model">${options(ai.fallback_model)}</select>`)}
        ${this.field('Modèle d\'embeddings', `<select data-k="embedding_model">${options(ai.embedding_model)}</select>`,
          'Utilisé pour la recherche sémantique en mémoire.')}
        ${this.field('Température', `<input type="number" step="0.05" min="0" max="2" data-k="temperature" value="${ai.temperature}"/>`)}
        ${this.field('Itérations d\'outils max', `<input type="number" data-k="max_tool_iterations" value="${ai.max_tool_iterations}"/>`)}
        ${this.field('Messages de contexte', `<input type="number" data-k="max_context_messages" value="${ai.max_context_messages}"/>`)}
        ${this.toggle('Bascule automatique si un fournisseur tombe', 'auto_fallback', ai.auto_fallback)}
        ${this.field('Instructions supplémentaires',
          `<textarea data-k="system_prompt_extra" placeholder="Consignes permanentes ajoutées au prompt système">${esc(ai.system_prompt_extra || '')}</textarea>`)}
        <button class="btn primary" data-save>Enregistrer</button>
      </div></div>`;

    this.bindToggles(pane, 'ai');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => {
        values[i.dataset.k] = i.type === 'number' ? Number(i.value) : i.value;
      });
      this.save('ai', values);
    };
    $$('[data-test-provider]', pane).forEach((b) => b.onclick = async () => {
      toast('Test en cours…');
      const r = await J.post(`/api/connectors/${b.dataset.testProvider}/test`);
      toast(r.detail || 'Test terminé.', r.connected ? 'ok' : 'err');
      this.renderPane(pane);
    });
    $$('[data-edit-provider]', pane).forEach((b) => b.onclick = () =>
      Connectors.edit(b.dataset.editProvider, () => this.renderPane(pane)));
    $$('[data-new-provider]', pane).forEach((b) => b.onclick = () =>
      Connectors.create(b.dataset.newProvider, () => this.renderPane(pane)));
    $('[data-add-provider]', pane).onclick = () => { this.section = 'connectors'; this.render($('#page-settings')); };
  },

  /* --------------------------------------------------------- CONNECTORS */
  /* ----------------------------------------------------- IMAGE GENERATION */
  async pane_image(pane) {
    const i = this.data.image || {};
    let live = {};
    try { live = await J.get('/api/images/backends'); } catch { /* service arrêté */ }
    const mode = ['auto', 'fast', 'quality'].includes(i.default_mode) ? i.default_mode : 'auto';
    const preset = ['quality_standard', 'quality_high', 'quality_ultra'].includes(i.quality_preset)
      ? i.quality_preset : 'quality_standard';
    const engines = (live.backends || []).flatMap((b) => b.engines || []);
    const engineText = live.available
      ? engines.map((e) => e.label || e.id).join(' · ') || 'ComfyUI détecté'
      : 'ComfyUI non disponible';
    const pipeline = String(i.pipeline_version || 'v2').toLowerCase() === 'v1' ? 'v1' : 'v2';
    const quality = ['FAST', 'BALANCED', 'QUALITY', 'ULTRA']
      .includes(String(i.default_quality || '').toUpperCase())
      ? String(i.default_quality).toUpperCase() : 'BALANCED';
    pane.innerHTML = `<div class="card" style="margin-bottom:11px"><div class="card-head"><h2>IMAGE ENGINE</h2><span class="tools">${esc(engineText)}</span></div><div class="card-body">
      ${this.field('Moteur', `<select data-k="pipeline_version">
        <option value="v2" ${pipeline === 'v2' ? 'selected' : ''}>V2 — Z-Image + hi-res + ESRGAN (recommandé)</option>
        <option value="v1" ${pipeline === 'v1' ? 'selected' : ''}>Legacy V1 — workflow golden figé</option>
      </select>`, 'V2 est le moteur de production. Legacy V1 reste disponible en secours ; aucun code V1 n\'a été supprimé.')}
      ${this.field('Profil par défaut', `<select data-k="default_quality">
        ${[['FAST', 'FAST — 8 steps, 1024², aperçu'],
           ['BALANCED', 'BALANCED — 12 steps + ESRGAN ×1,5'],
           ['QUALITY', 'QUALITY — 12 steps + hi-res 0,25'],
           ['ULTRA', 'ULTRA — base 1152 + hi-res 0,30 + ESRGAN']]
          .map(([v, l]) => `<option value="${v}" ${quality === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>`, 'Réglages issus de Quality Validation V2. JARVIS choisit seul selon la demande ; ceci est le défaut quand rien ne tranche.')}
      <p class="hint">Profils validés par mesure — au-delà de 12 steps la qualité ne progresse plus,
      le denoise hi-res utile est 0,20–0,30, et le sharpening a été supprimé (halos).</p>
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>
    <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>LEGACY V1 — SDXL</h2><span class="tools">inactif si moteur = V2</span></div><div class="card-body">
      ${this.field('Mode par défaut', `<select data-k="default_mode">
        ${[['auto','Auto (aperçu → fast, final → SDXL)'],['fast','Fast — prévisualisation Z-Image'],['quality','Quality — rendu final SDXL']].map(([v,l]) => `<option value="${v}" ${mode === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>`, 'Un appel peut forcer engine_mode=fast ou engine_mode=quality.')}
      ${this.toggle('Activer le moteur rapide', 'fast_enabled', i.fast_enabled !== false,
        { hint: 'Z-Image-Turbo pour previews, brouillons et variantes.' })}
      ${this.toggle('Activer le moteur qualité SDXL', 'quality_enabled', i.quality_enabled !== false,
        { hint: 'Le rendu final échoue clairement si le checkpoint SDXL manque.' })}
      ${this.field('Endpoint ComfyUI Fast', `<input data-k="fast_endpoint" value="${esc(i.fast_endpoint || 'http://127.0.0.1:8188')}"/>`)}
      ${this.field('Endpoint ComfyUI Quality', `<input data-k="quality_endpoint" value="${esc(i.quality_endpoint || 'http://127.0.0.1:8188')}"/>`)}
      ${this.field('Modèle Fast', `<input data-k="fast_model" value="${esc(i.fast_model || 'z_image_turbo_bf16.safetensors')}"/>`)}
      ${this.field('Checkpoint SDXL', `<input data-k="quality_checkpoint" value="${esc(i.quality_checkpoint || '')}" placeholder="ex. sd_xl_base_1.0.safetensors"/>`, 'Le nom doit exister dans ComfyUI/models/checkpoints.')}
      ${this.field('VAE SDXL (facultatif)', `<input data-k="quality_vae" value="${esc(i.quality_vae || '')}" placeholder="Vide = VAE du checkpoint"/>`)}
    </div></div>
    <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>SDXL QUALITY</h2></div><div class="card-body">
      ${this.field('Preset qualité', `<select data-k="quality_preset">
        ${[['quality_standard','Standard · 1024² · 32 steps'],['quality_high','High · 1024×1536 · 40 steps'],['quality_ultra','Ultra · 1536² · 45 steps']].map(([v,l]) => `<option value="${v}" ${preset === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>`)}
      ${this.field('Workflow SDXL txt2img', `<input data-k="quality_txt2img_workflow" value="${esc(i.quality_txt2img_workflow || 'workflows/comfyui/sdxl_quality_txt2img.json')}"/>`)}
      ${this.field('Workflow SDXL img2img', `<input data-k="quality_img2img_workflow" value="${esc(i.quality_img2img_workflow || 'workflows/comfyui/sdxl_quality_img2img.json')}"/>`)}
      ${this.field('Sampler', `<input data-k="quality_sampler" value="${esc(i.quality_sampler || 'dpmpp_2m')}"/>`)}
      ${this.field('Scheduler', `<input data-k="quality_scheduler" value="${esc(i.quality_scheduler || 'karras')}"/>`)}
      ${this.field('Steps', `<input type="number" min="1" max="100" data-k="quality_steps" value="${i.quality_steps || 32}"/>`)}
      ${this.field('CFG', `<input type="number" min="1" max="20" step="0.1" data-k="quality_cfg" value="${i.quality_cfg || 7}"/>`)}
      ${this.field('Résolution qualité par défaut', `<div style="display:flex;gap:7px"><input type="number" min="256" max="2048" data-k="quality_width" value="${i.quality_width || 1024}"/><input type="number" min="256" max="2048" data-k="quality_height" value="${i.quality_height || 1024}"/></div>`)}
      ${this.field('Timeout SDXL (s)', `<input type="number" min="60" max="3600" data-k="quality_timeout_s" value="${i.quality_timeout_s || 900}"/>`)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>
    <div class="card"><div class="card-head"><h2>COMPATIBILITÉ</h2></div><div class="card-body">
      ${this.field('Style préféré', `<select data-k="preferred_style">
        ${['cinematic','photorealistic','illustration','concept_art','product','poster','portrait','transparent_asset'].map(v => `<option value="${v}" ${i.preferred_style === v ? 'selected' : ''}>${v}</option>`).join('')}
      </select>`)}
      ${this.toggle('Contrôle qualité automatique', 'auto_quality_check', i.auto_quality_check,
        { hint: 'Un score est affiché si un moteur Vision est disponible.' })}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'image');
    $$('[data-save]', pane).forEach((button) => button.onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((el) => values[el.dataset.k] = el.type === 'number' ? Number(el.value) : el.value);
      this.save('image', values);
    });
  },

  async pane_connectors(pane) {
    await Connectors.render(pane, () => this.renderPane(pane));
  },

  /* ---------------------------------------------------------- ATELIER 3D */
  async pane_blender(pane) {
    const status = await J.get('/api/blender/status');
    const b = this.data.blender || {};
    const gpu = status.gpu || {};
    const test = status.last_test || {};
    const dot = (ok) => `<i class="dot ${ok ? 'ok' : 'err'}"></i>`;
    const when = test.ts
      ? new Date(test.ts * 1000).toLocaleString('fr-FR')
      : 'jamais testé';

    // État réel : rien n'est affiché comme disponible sans détection effective.
    const detected = `
      <div class="card" style="margin-bottom:11px">
        <div class="card-head"><h2>BLENDER</h2>
          <span class="tools">${status.installed
            ? esc(status.version) : 'non détecté'}</span></div>
        <div class="card-body">
          <div class="field row"><div style="flex:1">
            <label style="margin:0">${dot(status.installed)} Statut</label>
            <div class="hint">${status.installed
              ? 'Blender est installé et répond en mode arrière-plan.'
              : esc(status.hint || 'Blender est introuvable sur ce PC.')}</div>
          </div></div>
          ${status.installed ? `
          ${this.field('Chemin', `<input class="mono" value="${esc(status.executable_path)}" readonly />`)}
          ${this.field('Python Blender', `<input value="${esc(status.python_version || 'inconnu')}" readonly />`)}
          ${this.field('Moteurs de rendu',
            `<input value="${esc((status.render_engines || []).join(', ') || 'inconnus')}" readonly />`)}
          <div class="field row"><div style="flex:1">
            <label style="margin:0">${dot(gpu.available)} GPU</label>
            <div class="hint">${gpu.available
              ? esc(`${gpu.backend} · ${(gpu.devices || []).join(', ')}`)
              : 'Aucun périphérique Cycles détecté : les rendus Cycles utiliseront le CPU.'}</div>
          </div></div>
          ${(status.versions || []).length > 1 ? this.field('Versions installées',
            `<input value="${esc(status.versions.map((v) => v.version).join(', '))}" readonly />`,
            'Choisis la version préférée ci-dessous.') : ''}
          ` : ''}
          <div class="field row"><div style="flex:1">
            <label style="margin:0">Dernier test</label>
            <div class="hint">${esc(when)}${test.ok === false && test.error
              ? ' — ' + esc(String(test.error).slice(0, 160)) : ''}</div>
          </div><button class="btn sm primary" data-test-blender>Tester</button></div>
        </div>
      </div>`;

    const settings = `
      <div class="card" style="margin-bottom:11px">
        <div class="card-head"><h2>RÉGLAGES</h2></div><div class="card-body">
        ${this.field('Chemin de blender.exe (vide = détection automatique)',
          `<input data-k="executable_path" value="${esc(b.executable_path || '')}"
            placeholder="C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" />`)}
        ${this.field('Version préférée',
          `<input data-k="preferred_version" value="${esc(b.preferred_version || '')}"
            placeholder="ex. 4.2" />`, 'Vide = la version la plus récente détectée.')}
        ${this.toggle('Ouvrir la fenêtre Blender', 'show_blender_ui', b.show_blender_ui,
          { hint: 'Désactivé, JARVIS travaille en --background : rien ne s\'affiche à l\'écran.' })}
        ${this.toggle('Démarrage usine (--factory-startup)', 'factory_startup', b.factory_startup,
          { hint: 'Ignore tes préférences Blender : rend les jobs reproductibles.' })}
        ${this.toggle('Utiliser le GPU pour Cycles', 'use_gpu', b.use_gpu,
          { hint: 'Sans GPU réellement détecté, le rendu bascule sur le CPU.' })}
        ${this.toggle('Aperçu automatique après chaque job', 'auto_preview', b.auto_preview)}
        ${this.toggle('Autoriser blender.run_script (sandbox)', 'allow_run_script',
          b.allow_run_script,
          { hint: 'Scripts bpy limités à l\'atelier 3D : imports système, réseau et écriture hors workspace refusés.' })}
        ${this.field('Moteur de rendu par défaut',
          `<select data-k="render_engine">
            <option value="eevee" ${b.render_engine === 'eevee' ? 'selected' : ''}>EEVEE (aperçu rapide)</option>
            <option value="cycles" ${b.render_engine === 'cycles' ? 'selected' : ''}>Cycles (rendu final)</option>
          </select>`)}
        ${this.field('Profil d\'optimisation par défaut',
          `<select data-k="optimize_target">
            <option value="web" ${b.optimize_target === 'web' ? 'selected' : ''}>web (Three.js)</option>
            <option value="jarvis_avatar" ${b.optimize_target === 'jarvis_avatar' ? 'selected' : ''}>jarvis_avatar</option>
            <option value="none" ${b.optimize_target === 'none' ? 'selected' : ''}>aucun</option>
          </select>`)}
        ${this.field('Délai maximum d\'un job (s)',
          `<input type="number" data-k="default_timeout_s" value="${esc(b.default_timeout_s || 600)}" />`)}
        ${this.field('Délai maximum d\'un rendu (s)',
          `<input type="number" data-k="render_timeout_s" value="${esc(b.render_timeout_s || 1200)}" />`)}
        ${this.field('Taille de l\'aperçu (px)',
          `<input type="number" data-k="preview_size" value="${esc(b.preview_size || 640)}" />`)}
        <div class="field"><button class="btn primary" data-save-blender>Enregistrer</button></div>
      </div></div>`;

    const jobs = await J.get('/api/blender/jobs?limit=8');
    const list = (jobs.jobs || []).map((j) => `
      <div class="list-row">
        <div style="flex:1"><label style="margin:0">${esc(j.title || j.action)}</label>
          <div class="hint">${esc(j.action)} · ${esc(j.status)} ·
            ${Math.round((j.progress || 0) * 100)} %
            ${j.meta && j.meta.polycount ? ' · ' + j.meta.polycount + ' tris' : ''}</div></div>
        ${j.glb_url ? `<a class="btn sm" href="${j.glb_url}" download>GLB</a>` : ''}
        ${['queued', 'running'].includes(j.status)
          ? `<button class="btn sm" data-cancel-job="${esc(j.id)}">Annuler</button>` : ''}
      </div>`).join('') || '<div class="empty">Aucun job 3D pour le moment.</div>';

    pane.innerHTML = detected + settings + `
      <div class="card"><div class="card-head"><h2>JOBS 3D</h2>
        <span class="tools">${(jobs.jobs || []).length}</span></div>
      <div class="card-body">${list}</div></div>`;

    this.bindToggles(pane, 'blender', () => this.renderPane(pane));

    $('[data-test-blender]', pane).onclick = async (ev) => {
      const button = ev.currentTarget;
      button.disabled = true;
      button.textContent = 'Test en cours…';
      const res = await J.post('/api/blender/test', {});
      const t = res.test || {};
      if (t.ok) {
        toast(`Blender ${t.version} répond (Python ${t.python_version}).`, 'ok');
      } else {
        toast(t.error || res.error || 'Blender n\'a pas répondu.', 'err');
      }
      this.renderPane(pane);
    };

    $('[data-save-blender]', pane).onclick = async () => {
      const values = {};
      $$('[data-k]', pane).forEach((input) => {
        const key = input.dataset.k;
        values[key] = input.type === 'number' ? Number(input.value) : input.value;
      });
      await this.save('blender', values);
      this.renderPane(pane);
    };

    $$('[data-cancel-job]', pane).forEach((b) => b.onclick = async () => {
      await J.post(`/api/blender/jobs/${b.dataset.cancelJob}/cancel`, {});
      this.renderPane(pane);
    });
  },

  /* -------------------------------------------------------------- TOOLS */
  async pane_tools(pane) {
    const res = await J.get('/api/tools');
    const byCat = {};
    (res.tools || []).forEach((t) => { (byCat[t.category] = byCat[t.category] || []).push(t); });
    pane.innerHTML = Object.entries(byCat).map(([cat, list]) => `
      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>${esc(cat.toUpperCase())}</h2></div>
      <div class="card-body">${list.map((t) => `
        <div class="field row"><div style="flex:1">
          <label style="margin:0">${esc(t.name)} <span class="mono text-faint">${esc(t.id)}</span></label>
          <div class="hint">${esc(t.description)}</div></div>
          <button class="switch ${t.enabled ? 'on' : ''}" data-tool="${esc(t.id)}"></button></div>`).join('')}
      </div></div>`).join('');
    $$('[data-tool]', pane).forEach((b) => b.onclick = async () => {
      const enabled = !b.classList.contains('on');
      b.classList.toggle('on', enabled);
      await J.post(`/api/tools/${b.dataset.tool}/toggle`, { enabled });
    });
  },

  /* ------------------------------------------------------ CODE EDITOR */
  async pane_editor(pane) {
    const e = this.data.editor || {};
    const fontSize = Number(e.fontSize) || 13;
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>CODE EDITOR — MONACO</h2></div><div class="card-body">
      ${this.toggle('Minimap', 'minimap', e.minimap !== false,
        { hint: 'Barre latérale droite avec un aperçu du fichier.' })}
      ${this.toggle('Word Wrap', 'wordWrap', !!e.wordWrap,
        { hint: 'Casser les lignes trop longues à la largeur de l\'éditeur.' })}
      ${this.field('Taille de police', `<input type="number" min="10" max="24" data-k="fontSize" value="${fontSize}"/>`,
        'Taille de la police dans l\'éditeur (10 à 24 px).')}
      ${this.field('Police', `<input data-k="fontFamily" value="${esc(e.fontFamily || 'Cascadia Code')}"
        placeholder="Cascadia Code, Fira Code, JetBrains Mono, Consolas"/>`)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    const collect = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => {
        if (i.value === '') return;
        values[i.dataset.k] = i.dataset.k === 'fontSize' ? Number(i.value) : i.value;
      });
      $$('[data-toggle]', pane).forEach((b) => { values[b.dataset.toggle] = b.classList.contains('on'); });
      return values;
    };
    this.bindToggles(pane, 'editor', () => { const v = collect(); this.save('editor', v); });
    $('[data-save]', pane).onclick = () => this.save('editor', collect());
  },

  /* ------------------------------------------------------------- MEMORY */
  async pane_memory(pane) {
    const m = this.data.memory || {};
    const stats = await J.get('/api/memory?limit=1');
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>MÉMOIRE</h2></div><div class="card-body">
      ${this.toggle('Extraction automatique des faits durables', 'auto_extract', m.auto_extract)}
      ${this.toggle('Recherche sémantique (embeddings)', 'semantic_search', m.semantic_search,
        { hint: 'Nécessite un fournisseur supportant les embeddings (OpenAI, Gemini, Ollama).' })}
      ${this.field('Souvenirs injectés dans le contexte',
        `<input type="number" data-k="max_context_memories" value="${m.max_context_memories}"/>`)}
      ${this.field('Importance minimale pour le contexte',
        `<input type="number" min="1" max="5" data-k="min_importance_for_context" value="${m.min_importance_for_context}"/>`)}
      <div class="sep"></div>
      <div class="list">
        ${Object.entries(stats.stats || {}).filter(([k]) => typeof (stats.stats || {})[k] === 'number')
          .map(([k, v]) => `<div class="list-row" style="padding:6px 9px"><div class="meta">
            <b style="font-size:11px">${esc(k)}</b></div><span class="tag cy">${v}</span></div>`).join('')}
      </div>
      <div class="sep"></div>
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'memory');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => values[i.dataset.k] = Number(i.value));
      this.save('memory', values);
    };
  },

  /* ----------------------------------------------------------- SECURITY */
  async pane_security(pane) {
    const s = this.data.security || {};
    const status = J.state.status || {};
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>SÉCURITÉ</h2></div><div class="card-body">
      <div class="risk-banner" style="border-color:rgba(52,211,153,.35);background:rgba(52,211,153,.07);color:var(--ok)">
        ${icon('shield', 15)}<div>Coffre : <b>${esc(status.security?.vault_backend || '—')}</b> ·
        ${status.security?.secrets ?? 0} secret(s) chiffrés. Aucun secret n'est envoyé aux modèles
        ni écrit dans les logs.</div></div>
      ${this.toggle('Confirmer les actions sensibles', 'confirm_sensitive', s.confirm_sensitive,
        { hint: 'Redémarrage de service, envoi d\'email, déploiement…' })}
      ${this.toggle('Confirmer les actions destructives', 'confirm_destructive', s.confirm_destructive,
        { hint: 'Suppression de base, rm -rf, push --force… Fortement recommandé.' })}
      ${this.toggle('Autoriser le terminal local', 'allowed_shell', s.allowed_shell)}
      ${this.toggle('Masquer les secrets dans les logs', 'mask_secrets_in_logs', s.mask_secrets_in_logs)}
      ${this.field('Délai de confirmation (s)',
        `<input type="number" data-k="confirmation_timeout_s" value="${s.confirmation_timeout_s}"/>`)}
      ${this.field('Timeout des commandes shell (s)',
        `<input type="number" data-k="shell_timeout_s" value="${s.shell_timeout_s}"/>`)}
      ${this.field('Dossiers accessibles aux outils fichiers',
        `<textarea data-k="filesystem_roots">${esc((s.filesystem_roots || []).join('\n'))}</textarea>`,
        'Un chemin par ligne. Tout accès hors de ces dossiers est refusé.')}
      ${this.field('Rétention du journal d\'audit (jours)',
        `<input type="number" data-k="audit_retention_days" value="${s.audit_retention_days}"/>`)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'security');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => {
        values[i.dataset.k] = i.dataset.k === 'filesystem_roots'
          ? i.value.split('\n').map((x) => x.trim()).filter(Boolean)
          : Number(i.value);
      });
      this.save('security', values);
    };
  },

  /* --------------------------------------------------------- AUTOMATION */
  async pane_automation(pane) {
    const a = this.data.automation || {};
    const n8n = J.state.connectors.filter((c) => c.type === 'n8n');
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>AUTOMATISATION</h2></div><div class="card-body">
      ${this.toggle('Planificateur actif', 'scheduler_enabled', a.scheduler_enabled)}
      ${this.field('Intervalle de vérification (s)',
        `<input type="number" data-k="scheduler_tick_s" value="${a.scheduler_tick_s}"/>`)}
      ${this.field('Tâches simultanées max',
        `<input type="number" data-k="max_concurrent_tasks" value="${a.max_concurrent_tasks}"/>`)}
      ${this.field('Connecteur n8n par défaut', `<select data-k="n8n_connector_id">
        <option value="">Aucun (JARVIS reste autonome)</option>
        ${n8n.map((c) => `<option value="${esc(c.id)}" ${a.n8n_connector_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
      </select>`, 'n8n est un moteur optionnel : les automatisations JARVIS fonctionnent sans lui.')}
      ${this.field('Rétention des tâches (jours)',
        `<input type="number" data-k="task_retention_days" value="${a.task_retention_days}"/>`)}
      <button class="btn primary" data-save>Enregistrer</button>
    </div></div>`;
    this.bindToggles(pane, 'automation');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => {
        values[i.dataset.k] = i.tagName === 'SELECT' ? i.value : Number(i.value);
      });
      this.save('automation', values);
    };
  },

  /* ------------------------------------------------------ NOTIFICATIONS */
  async pane_notifications(pane) {
    const n = this.data.notifications || {};
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>NOTIFICATIONS</h2></div><div class="card-body">
      ${this.toggle('Notifications du bureau', 'desktop', n.desktop)}
      ${this.toggle('Live Intelligence Feed', 'feed', n.feed)}
      ${this.toggle('Annoncer les alertes à voix haute', 'speak_important', n.speak_important,
        { hint: 'Uniquement les alertes ; jamais un message d\'accueil.' })}
    </div></div>`;
    this.bindToggles(pane, 'notifications');
  },

  /* ---------------------------------------------------------- DEVELOPER */
  async pane_developer(pane) {
    const d = this.data.developer || {};
    const status = J.state.status || {};
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>DÉVELOPPEUR</h2></div><div class="card-body">
      ${this.toggle('Mode debug (logs HTTP)', 'debug', d.debug)}
      ${this.field('Historique d\'événements en mémoire',
        `<input type="number" data-k="event_history" value="${d.event_history}"/>`)}
      <div class="sep"></div>
      <div class="field"><label>ÉTAT DE LA MACHINE VOCALE</label>
        <div class="mono" style="padding:9px;border:1px solid var(--line-soft);border-radius:7px">
          état : ${esc(status.voice?.state || '—')} ·
          transitions refusées : ${status.voice?.rejected_transitions ?? 0} ·
          sessions : ${status.voice?.sessions?.sessions ?? 0}
          (saluées : ${status.voice?.sessions?.greeted ?? 0})
        </div></div>
      <div class="field"><label>SANTÉ</label>
        <div class="mono" style="padding:9px;border:1px solid var(--line-soft);border-radius:7px">
          version ${esc(status.version || '—')} · build ${esc(status.build_id || '—')} ·
          uptime ${fmtDuration(status.uptime_s)} ·
          outils ${status.tools?.total ?? '—'} · base ${esc(status.environment?.data_dir || '—')}
        </div></div>
      <div class="field"><label>AVATAR STUDIO</label>
        <div class="mono" style="padding:9px;border:1px solid var(--line-soft);border-radius:7px">
          ${esc(window.AVATAR_STUDIO_BUILD || 'Module non chargé')}
        </div></div>
      <div style="display:flex;gap:7px;flex-wrap:wrap">
        <button class="btn primary" data-save>Enregistrer</button>
        <button class="btn" data-backup>${icon('shield', 12)} Sauvegarder la base</button>
      </div>
    </div></div>`;
    this.bindToggles(pane, 'developer');
    $('[data-save]', pane).onclick = () => {
      const values = {};
      $$('[data-k]', pane).forEach((i) => values[i.dataset.k] = Number(i.value));
      this.save('developer', values);
    };
    $('[data-backup]', pane).onclick = async () => {
      const r = await J.post('/api/system/backup');
      toast(r.ok ? 'Sauvegarde créée : ' + r.backup : 'Échec.', r.ok ? 'ok' : 'err');
    };
  },

  /* --------------------------------------------------------------- LOGS */
  async pane_logs(pane) {
    const res = await J.get('/api/audit?limit=200');
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>JOURNAL D'AUDIT</h2>
      <span class="tools">${res.total} entrées</span></div>
      <div class="card-body">
        <input id="logSearch" placeholder="Filtrer…" style="width:100%;margin-bottom:10px;padding:8px 11px;
          border-radius:7px;border:1px solid var(--line);background:rgba(5,14,28,.8);outline:none"/>
        <div class="list" id="logList" style="max-height:60vh;overflow:auto"></div>
      </div></div>`;
    const draw = (entries) => {
      $('#logList', pane).innerHTML = entries.map((e) => `
        <div class="list-row" style="padding:7px 9px"><div class="meta">
          <b style="font-size:11px">${esc(e.action)}</b>
          <small>${fmtDateTime(e.ts)} · ${esc(e.agent || '—')} · ${esc(e.tool || '—')} · ${e.duration_ms || 0} ms</small>
          ${e.detail ? `<small class="text-faint mono">${esc(String(e.detail).slice(0, 160))}</small>` : ''}
        </div><span class="tag ${e.status === 'ok' ? 'ok' : e.status === 'denied' ? 'warn' : 'err'}">${esc(e.status)}</span>
        </div>`).join('') || '<div class="empty">Aucune entrée.</div>';
    };
    draw(res.entries || []);
    let timer;
    $('#logSearch', pane).oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const r = await J.get(`/api/audit?limit=200&search=${encodeURIComponent($('#logSearch', pane).value)}`);
        draw(r.entries || []);
      }, 260);
    };
  },
};

/* ==========================================================================
   CONNECTORS CENTER
   ========================================================================== */
const Connectors = {
  types: [],

  async render(pane, refresh) {
    const res = await J.get('/api/connectors');
    this.types = res.types || [];
    const connectors = res.connectors || [];
    J.state.connectors = connectors;
    const byCat = {};
    this.types.forEach((t) => { (byCat[t.category] = byCat[t.category] || []).push(t); });

    pane.innerHTML = `
      <div class="card" style="margin-bottom:11px">
        <div class="card-head"><h2>CONNECTEURS ENREGISTRÉS</h2>
          <span class="tools">${connectors.length} · ${connectors.filter((c) => c.status === 'connected').length} connecté(s)</span></div>
        <div class="card-body"><div class="list">
          ${connectors.map((c) => `<div class="list-row">
            <div class="meta"><b>${esc(c.name)} <span class="mono text-faint">${esc(c.id)}</span></b>
              <small>${esc(c.label || c.type)}${c.config?.host ? ' · ' + esc(c.config.host) : ''}
                ${c.config?.url ? ' · ' + esc(c.config.url) : ''}</small>
              <small class="text-faint">${esc(c.status_detail || '—')}${c.last_test_at ? ' · testé ' + fmtAgo(c.last_test_at) : ''}
                · permissions : ${(c.permissions || []).join(', ')}
                ${Object.entries(c.secret_fields || {}).filter(([, v]) => v.configured)
                  .map(([k, v]) => ` · ${esc(k)} ${esc(v.preview)}`).join('')}</small></div>
            <span class="tag ${c.status === 'connected' ? 'ok' : c.status === 'error' ? 'err' : ''}">
              ${c.status === 'connected' ? 'Connecté' : c.status === 'error' ? 'Erreur' : 'Inconnu'}</span>
            <div class="acts">
              <button class="btn sm" data-test="${esc(c.id)}">Tester</button>
              <button class="btn sm" data-edit="${esc(c.id)}">${icon('edit', 11)}</button>
              <button class="btn sm" data-toggle-c="${esc(c.id)}">${c.enabled ? 'Off' : 'On'}</button>
              <button class="btn sm danger" data-del="${esc(c.id)}">${icon('trash', 11)}</button>
            </div></div>`).join('')
            || '<div class="empty"><b>Aucun connecteur</b>Ajoute tes accès une seule fois : JARVIS les réutilisera.</div>'}
        </div></div></div>

      <div class="card"><div class="card-head"><h2>AJOUTER UN CONNECTEUR</h2></div><div class="card-body">
        ${Object.entries(byCat).map(([cat, list]) => `
          <div class="field"><label>${esc(cat.toUpperCase())}</label>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              ${list.map((t) => `<button class="btn sm" data-add="${esc(t.type)}">${esc(t.label)}</button>`).join('')}
            </div></div>`).join('')}
      </div></div>`;

    $$('[data-add]', pane).forEach((b) => b.onclick = () => this.create(b.dataset.add, refresh));
    $$('[data-edit]', pane).forEach((b) => b.onclick = () => this.edit(b.dataset.edit, refresh));
    $$('[data-test]', pane).forEach((b) => b.onclick = async () => {
      b.disabled = true;
      b.textContent = '…';
      const r = await J.post(`/api/connectors/${b.dataset.test}/test`);
      toast(r.detail || 'Test terminé.', r.connected ? 'ok' : 'err');
      refresh();
    });
    $$('[data-toggle-c]', pane).forEach((b) => b.onclick = async () => {
      const c = connectors.find((x) => x.id === b.dataset.toggleC);
      await J.post(`/api/connectors/${c.id}/toggle`, { enabled: !c.enabled });
      refresh();
    });
    $$('[data-del]', pane).forEach((b) => b.onclick = async () => {
      if (!await confirmDialog('Supprimer le connecteur',
        'Le connecteur et ses secrets chiffrés seront supprimés définitivement.', { danger: true })) return;
      await J.del(`/api/connectors/${b.dataset.del}`);
      refresh();
    });
  },

  formHtml(type, connector) {
    const spec = this.types.find((t) => t.type === type);
    if (!spec) return '<div class="empty">Type inconnu.</div>';
    const cfg = connector?.config || {};
    const secrets = connector?.secret_fields || {};
    const fields = spec.fields.map((f) => {
      const value = f.secret ? '' : (cfg[f.key] ?? f.default ?? '');
      const configured = secrets[f.key]?.configured;
      if (f.kind === 'bool') {
        return `<div class="field row"><label style="flex:1;margin:0">${esc(f.label)}</label>
          <button class="switch ${value ? 'on' : ''}" data-bool="${esc(f.key)}"></button></div>`;
      }
      if (f.kind === 'select') {
        return `<div class="field"><label>${esc(f.label)}</label>
          <select data-f="${esc(f.key)}">${f.options.map((o) =>
            `<option value="${esc(o)}" ${value === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></div>`;
      }
      if (f.kind === 'textarea') {
        return `<div class="field"><label>${esc(f.label)}${f.secret ? ' 🔒' : ''}</label>
          <textarea data-f="${esc(f.key)}" placeholder="${configured ? '•••• déjà enregistré — laisser vide pour conserver' : esc(f.placeholder)}">${esc(value)}</textarea>
          ${f.help ? `<div class="hint">${esc(f.help)}</div>` : ''}</div>`;
      }
      const inputType = f.kind === 'password' ? 'password' : f.kind === 'number' ? 'number' : 'text';
      return `<div class="field"><label>${esc(f.label)}${f.required ? ' *' : ''}${f.secret ? ' 🔒' : ''}</label>
        <input type="${inputType}" data-f="${esc(f.key)}" value="${esc(value)}"
          placeholder="${configured ? '•••• enregistré — laisser vide pour conserver' : esc(f.placeholder)}"/>
        ${f.help ? `<div class="hint">${esc(f.help)}</div>` : ''}</div>`;
    }).join('');

    const perms = connector?.permissions || spec.default_permissions;
    return `
      <div class="field"><label>Nom</label>
        <input data-name value="${esc(connector?.name || spec.label)}"/></div>
      ${fields}
      <div class="field"><label>Permissions</label>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${['read', 'write', 'execute', 'admin', 'destructive'].map((p) =>
            `<button class="btn sm ${perms.includes(p) ? 'primary' : ''}" data-perm="${p}">${p}</button>`).join('')}
        </div>
        <div class="hint">JARVIS ne pourra exécuter que les actions couvertes par ces permissions.</div></div>
      <div class="risk-banner" style="border-color:rgba(52,211,153,.3);background:rgba(52,211,153,.06);color:var(--ok)">
        ${icon('shield', 15)}<div>Les champs 🔒 sont chiffrés dans le coffre. Ils ne sont jamais réaffichés,
        jamais journalisés, et jamais transmis aux modèles : les agents n'utilisent que l'identifiant du connecteur.</div>
      </div>`;
  },

  bindForm(m, spec) {
    const perms = new Set(spec.perms);
    m.$$('[data-perm]').forEach((b) => b.onclick = () => {
      const p = b.dataset.perm;
      perms.has(p) ? perms.delete(p) : perms.add(p);
      b.classList.toggle('primary', perms.has(p));
    });
    m.$$('[data-bool]').forEach((b) => b.onclick = () => b.classList.toggle('on'));
    return () => {
      const config = {};
      m.$$('[data-f]').forEach((i) => { if (i.value !== '') config[i.dataset.f] = i.value; });
      m.$$('[data-bool]').forEach((b) => config[b.dataset.bool] = b.classList.contains('on'));
      return { name: m.$('[data-name]').value, config, permissions: [...perms] };
    };
  },

  create(type, refresh) {
    const spec = this.types.find((t) => t.type === type);
    if (!spec) return;
    const m = modal({
      title: `Nouveau connecteur — ${spec.label}`, wide: true,
      body: this.formHtml(type, null),
      footer: `<button class="btn" data-close>Annuler</button>
               <button class="btn primary" data-go>Enregistrer</button>`,
    });
    const collect = this.bindForm(m, { perms: spec.default_permissions });
    m.$('[data-go]').onclick = async () => {
      const payload = { type, ...collect() };
      const r = await J.post('/api/connectors', payload);
      if (!r.ok) return toast(r.error || 'Échec.', 'err');
      m.close();
      toast('Connecteur enregistré.', 'ok');
      const test = await J.post(`/api/connectors/${r.connector.id}/test`);
      toast(test.detail || '', test.connected ? 'ok' : 'err');
      refresh();
    };
  },

  async edit(connectorId, refresh) {
    const res = await J.get(`/api/connectors/${connectorId}`);
    if (!res.ok) return toast('Connecteur introuvable.', 'err');
    const c = res.connector;
    const spec = this.types.find((t) => t.type === c.type)
      || (await J.get('/api/connectors')).types.find((t) => t.type === c.type);
    if (!this.types.length) this.types = (await J.get('/api/connectors')).types || [];
    const m = modal({
      title: `${c.name} — ${spec?.label || c.type}`, wide: true,
      body: this.formHtml(c.type, c),
      footer: `<button class="btn" data-close>Annuler</button>
               <button class="btn" data-test>Tester</button>
               <button class="btn primary" data-go>Enregistrer</button>`,
    });
    const collect = this.bindForm(m, { perms: c.permissions });
    m.$('[data-go]').onclick = async () => {
      const r = await J.put(`/api/connectors/${connectorId}`, collect());
      if (!r.ok) return toast(r.error || 'Échec.', 'err');
      m.close();
      toast('Connecteur mis à jour.', 'ok');
      refresh();
    };
    m.$('[data-test]').onclick = async () => {
      await J.put(`/api/connectors/${connectorId}`, collect());
      const t = await J.post(`/api/connectors/${connectorId}/test`);
      toast(t.detail || 'Test terminé.', t.connected ? 'ok' : 'err');
    };
  },
};
