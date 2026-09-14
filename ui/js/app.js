/* ==========================================================================
   Application — Command Center : navigation, console, câblerie temps réel.
   Chaque événement SSE est routé vers sa représentation RÉELLE :
   - jarvis.state / tool.* / voice.local  → états du robot 3D + activité
   - brain.*  → impulsions du Brain Atlas + activité
   - tts.*    → niveau sonore réel (bouche du robot)
   Rien n'est simulé : si le backend ne dit rien, rien ne bouge.
   ========================================================================== */
const App = {
NAV: [
    { label: 'CORE', items: [
      ['command', 'Accueil', 'command'],
      ['chat', 'Chat', 'chat'],
      ['projects', 'Projets', 'folder'],
      ['agents', 'Agents IA', 'agents'],
      ['workflows', 'Automatisations', 'workflows'],
      ['calendar', 'Calendrier', 'calendar'],
      ['tasks', 'Tâches', 'tasks'],
    ]},
    { label: 'RESSOURCES', items: [
      ['knowledge', 'Connaissances', 'knowledge'],
      ['memory', 'Mémoire', 'memory'],
      ['code', 'Fichiers & Code', 'code'],
      ['analyses', 'Analyses', 'database'],
      ['terminal', 'Terminal', 'terminal'],
      ['brain', 'Brain Atlas', 'brain'],
    ]},
    { label: 'ESPACES', items: [
      ['marketing', 'Marketing', 'globe'],
      ['finance', 'Finance', 'mail'],
      ['social', 'Réseaux sociaux', 'link'],
      ['servers', 'Sites & Serveurs', 'server'],
      ['tools', 'Outils', 'tools'],
    ]},
    { label: 'SYSTÈME', items: [
      ['avatar-studio', 'Avatar Studio', 'avatar'],
      ['image-studio', "Génération d'images", 'avatar'],
      ['aicore', 'AI Core', 'core'],
      ['settings', 'Paramètres', 'settings'],
    ]},
  ],
  phase: 0,
  focusMode: false,
  confirmationShown: null,
  _stateToken: 0,

  /* ------------------------------------------------------------- démarrage */
  async init() {
    if (this.initialized) return;
    this.initialized = true;
    console.log('[JARVIS UI BOOT]', 'CHAT_RELOAD_FIX_20260911_A', Date.now(), performance.getEntriesByType('navigation'));
    this.renderNav();
    this.bindUI();
    this.startClock();
    this.startAnimation();
    J.connectStream();
    this.bindStream();
    CodeEnv.bind();
    this.conversationReady = this.loadConversation().catch((error) => {
      console.error('[CHAT-UI] history restore failed', error);
    });
    await this.conversationReady;

    const settings = await J.get('/api/settings');
    if (settings.ok) {
      J.state.settings = settings.settings;
      this.applyAppearance(settings.settings.appearance);
      await VoiceManager.init(settings.settings.voice);
      const chip = $('#voiceModeChip');
      chip.textContent = {
        push_to_talk: 'PTT', always_listening: 'AUTO', wake_word: 'WAKE', conversation: 'CONV',
      }[settings.settings.voice.mode] || '—';
      this.applyQualityListeners(settings.settings.appearance?.quality);
    }

    Dashboard.refreshSoon(6000);
    await Dashboard.refresh();
    AppHome?.render();
    const brain = window.JarvisBrain;
    if (brain) {
      brain.onNodeClick = (node) => this.showNodeDetails(node);
      brain.onGraphLoaded = (counts) => {
        const hudN = $('#brainNodeCount');
        const hudE = $('#brainEdgeCount');
        if (hudN) hudN.textContent = counts.nodes;
        if (hudE) hudE.textContent = counts.edges;
      };
    }
    setInterval(() => Dashboard.refresh(), 6000);
    this.goto(location.hash.replace('#', '') || 'command');
  },

  renderNav() {
    const groups = this.NAV.map((g) => `
      <div class="nav-group">
        <div class="nav-group-label">${g.label}</div>
        ${g.items.map(([id, label, ic]) => `
          <button class="nav-item ${id === 'command' ? 'active' : ''}" data-nav="${id}" title="${label}">
            ${icon(ic, 15)}<span>${label}</span>
          </button>`).join('')}
      </div>`).join('');
    $('#nav').innerHTML = groups;
    $$('[data-nav]').forEach((b) => b.onclick = () => this.goto(b.dataset.nav));
  },

goto(page, options = {}) {
    window.AvatarStudio?.restoreAvatarStage();
    let brainFocus = false;
    let chatFocus = false;
    if (page === 'brain') { page = 'command'; brainFocus = true; }
    else if (page === 'chat') { page = 'command'; chatFocus = true; }
    if (!document.getElementById('page-' + page)) page = 'command';
    J.state.page = page;
    location.hash = page;
    $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + page));
    $$('[data-nav]').forEach((b) => b.classList.toggle('active', b.dataset.nav === page));
    $('#sidebar').classList.remove('open');
    if (page === 'command') Dashboard.refresh();
    else if (page === 'settings') Settings.render($('#page-settings'), options.section);
    else if (page === 'terminal') Terminal.render();
    else if (page === 'image-studio') ImageStudio.render($('#page-image-studio'));
    else if (page === 'code') CodeEnv.render();
    else if (page === 'projects' || page === 'analyses' || page === 'finance'
      || page === 'marketing' || page === 'social' || page === 'servers') Pages.render(page);
    else if (page === 'conversations') Pages.render('conversations');
    else Pages.render(page);
    if (brainFocus) {
      requestAnimationFrame(() => {
        if (!$('#brainZone')?.classList.contains('is-expanded')) $('#brainExpand')?.click();
      });
    }
    if (chatFocus) setTimeout(() => this.focusChat(), 60);
  },

  focusChat() {
    const row = $('#chatRow');
    if (row) {
      row.classList.remove('chat-flash');
      void row.offsetWidth;
      row.classList.add('chat-flash');
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    setTimeout(() => $('#convInput')?.focus(), 300);
  },

  toggleLauncher(force) {
    const pop = $('#launcherPop');
    if (!pop) return;
    const hide = force !== undefined ? force : !pop.hidden;
    pop.hidden = hide;
    pop.classList.toggle('open', !hide);
    $('#appLauncher')?.setAttribute('aria-expanded', String(!hide));
  },

  handleNavAction(action) {
    if (action === 'focus-chat') return this.focusChat();
    if (action) this.goto(action);
  },

  /* --------------------------------------------------------------- UI */
  bindUI() {
    $('#micBtn').onclick = () => this.toggleVoice();
    $('#talkBar').onclick = () => this.toggleVoice();
    const sidebarToggle = $('#sidebarToggle');
    if (sidebarToggle) {
      sidebarToggle.onclick = async () => {
        const compact = !$('#app').classList.contains('sidebar-compact');
        $('#app').classList.toggle('sidebar-compact', compact);
        sidebarToggle.setAttribute('aria-expanded', String(!compact));
        const res = await J.put('/api/settings/appearance', { compact_sidebar: compact });
        if (res.ok) toast(compact ? 'Barre latérale réduite.' : 'Barre latérale déployée.', 'ok');
      };
    }
    $('#consoleToggle').onclick = () => this.toggleConsole();
    $('#consoleClose').onclick = () => this.closeConsole();
    $('#consoleNew').onclick = async () => {
      const r = await J.post('/api/conversations');
      J.state.conversation = r.conversation.id;
      J.state.messages = [];
      $('#consoleLog').innerHTML = '';
      $('#convLog').innerHTML = '';
      $('#consoleConv').textContent = 'nouvelle';
      toast('Nouvelle conversation.', 'ok');
    };
    $('#consoleForm').onsubmit = (e) => {
      e.preventDefault();
      console.debug('[CHAT-UI] submit preventDefault', e.defaultPrevented);
      const input = $('#consoleInput');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      this.send(text);
    };
    $('#convForm').onsubmit = (e) => {
      e.preventDefault();
      console.debug('[CHAT-UI] submit preventDefault', e.defaultPrevented);
      const input = $('#convInput');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      this.send(text);
    };
    $('#convOpen').onclick = () => this.openConsole();
    const studioBtn = $('#avatarStudioOpen');
    if (studioBtn) {
      studioBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        window.AvatarStudio?.open();
      };
    }
    const centerBtn = $('#avatarStudioCenter');
    if (centerBtn) {
      centerBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.goto('avatar-studio');
      };
    }
    const pageAvatarBtn = $('#avatarStudioBack');
    if (pageAvatarBtn) pageAvatarBtn.onclick = () => this.goto('command');
    $('#briefBtn').onclick = () => this.send('Qu\'est-ce qui nécessite mon attention ?');
    $('#notifBtn').onclick = () => this.goto('tasks');
    $('#activityClear').onclick = () => { window.ActivityPanel?.clear(); };
    $('#npClose').onclick = () => {
      $('#nodePanel').classList.add('hidden');
      window.JarvisBrain?.deselect();
    };
    $('#qualityChip').onclick = () => this.goto('settings', { section: 'appearance' });
    $('#brainLabels').onclick = (e) => {
      const visible = window.JarvisBrain?.toggleLabels();
      e.currentTarget.setAttribute('aria-pressed', String(visible ?? true));
    };
    $('#brainReset').onclick = () => {
      $('#brainSearch').value = '';
      window.JarvisBrain?.highlight('');
      window.JarvisBrain?.discover();
      $('#nodePanel').classList.add('hidden');
    };
    const expandBrain = (expanded) => {
      $('#brainZone').classList.toggle('is-expanded', expanded);
      $('#brainExpand').setAttribute('aria-expanded', String(expanded));
      $('#brainExpand').setAttribute('aria-label', expanded ? 'Réduire le Brain Atlas' : 'Agrandir le Brain Atlas');
      $('#brainExpand').title = expanded ? 'Réduire le Brain Atlas' : 'Agrandir le Brain Atlas';
      requestAnimationFrame(() => window.JarvisBrain?.discover(false));
    };
    $('#brainExpand').onclick = () => expandBrain(!$('#brainZone').classList.contains('is-expanded'));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && $('#brainZone').classList.contains('is-expanded')) {
        expandBrain(false);
        $('#brainExpand').focus();
      }
    });
    $('#termClear').onclick = () => Terminal.clear();
    $('#codeRefresh').onclick = () => CodeEnv.refresh(true);
    $$('[data-goto]').forEach((b) => b.onclick = () =>
      this.goto(b.dataset.goto, { section: b.dataset.section }));

    // Recherche dans le Brain Atlas (filtre visuel temps réel).
    let brainTimer;
    $('#brainSearch').addEventListener('input', (e) => {
      clearTimeout(brainTimer);
      brainTimer = setTimeout(() => {
        window.JarvisBrain?.highlight(e.target.value);
      }, 120);
    });
    $$('[data-avatar-mode]').forEach((button) => {
      button.onclick = () => {
        const mode = button.dataset.avatarMode;
        window.JarvisRobot?.setMode(mode);
        $$('[data-avatar-mode]').forEach((b) => b.classList.toggle('active', b === button));
      };
    });

    // Bouton de commande par recherche globale.
    $('#globalSearch').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.value.trim()) {
        this.send(e.target.value.trim());
        e.target.value = '';
      }
    });

    document.addEventListener('click', (e) => {
      const quick = e.target.closest('[data-quick]');
      if (quick) this.handleQuick(quick.dataset.quick);
      const goto = e.target.closest('[data-goto]');
      if (goto && !goto.dataset.bound) this.goto(goto.dataset.goto, { section: goto.dataset.section });
      const agent = e.target.closest('[data-agent]');
      if (agent) this.goto('agents');
      const taskRow = e.target.closest('.tl-row[data-task]');
      if (taskRow) Pages.showTask(taskRow.dataset.task);
      const navAct = e.target.closest('[data-nav-action]');
      if (navAct) this.handleNavAction(navAct.dataset.navAction);
      const launcher = e.target.closest('[data-launcher], #appLauncher');
      if (launcher) {
        if (!e.target.closest('#appLauncher')) { AppHome?.renderLauncher(); this.toggleLauncher(); }
      } else {
        this.toggleLauncher(true);
      }
    });

    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        $('#globalSearch').focus();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault();
        this.toggleConsole();
      }
      if (e.key === 'Escape') {
        this.closeConsole();
        $('#nodePanel').classList.add('hidden');
        this.toggleLauncher(true);
      }
    });

    // Qualité 3D (Low / Balanced / Ultra).
    $$('#qualitySwitch button').forEach((b) => b.onclick = async () => {
      const q = b.dataset.quality;
      this.applyQualityListeners(q);
      const res = await J.put('/api/settings/appearance', { quality: q });
      if (res.ok) {
        const chip = $('#qualityChip');
        chip.textContent = q.toUpperCase();
        toast('Qualité 3D : ' + q + ' (recharge pour l\'appliquer à toute la scène).');
      }
    });

    // ---- Command bar de l'accueil (pilotée par les vraies routes JARVIS) ----
    const homeForm = $('#homeCmdForm');
    if (homeForm) homeForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const el = $('#homeCmd');
      const text = (el.value || '').trim();
      if (!text) return;
      el.value = '';
      this.send(text);
    });
    const homeMic = $('#homeMic');
    if (homeMic) homeMic.onclick = () => this.toggleVoice();

    // ---- Lanceur d'applications ----
    $('#appLauncher').onclick = (e) => {
      e.stopPropagation();
      AppHome?.renderLauncher();
      this.toggleLauncher();
    };
    $('#launcherPop').addEventListener('click', (e) => {
      const go = e.target.closest('[data-launch]');
      if (go) {
        this.toggleLauncher(true);
        const v = go.dataset.launch;
        if (v === 'focus-chat') this.focusChat();
        else if (v === 'settings-connectors') this.goto('settings', { section: 'connectors' });
        else this.goto(v);
      }
    });
  },

  applyQualityListeners(q) {
    q = ['low', 'balanced', 'ultra'].includes(q) ? q : 'balanced';
    window.JarvisRobot?.setQuality(q);
    window.JarvisBrain?.setQuality(q);
    $$('#qualitySwitch button').forEach((b) =>
      b.classList.toggle('active', b.dataset.quality === q));
    try {
      sessionStorage.setItem('jarvis.settings', JSON.stringify(
        { ...JSON.parse(sessionStorage.getItem('jarvis.settings') || '{}'),
          appearance: { quality: q } }));
    } catch { /* ignore */ }
  },

  handleQuick(action) {
    if (action.startsWith('goto:')) return this.goto(action.split(':')[1]);
    if (action === 'voice') return this.toggleVoice();
    if (action === 'new-task') return this.newTaskDialog();
    if (action === 'run-workflow') return this.runWorkflowDialog();
    const prompt = AppHome?.QUICK_PROMPTS?.[action];
    if (prompt) return this.send(prompt);
    return this.send(action);
  },

  /* ------------------------------------------------------------ console */
  openConsole() { $('#consoleDock').classList.remove('hidden'); },
  closeConsole() { $('#consoleDock').classList.add('hidden'); },
  toggleConsole() {
    $('#consoleDock').classList.toggle('hidden');
    if (!$('#consoleDock').classList.contains('hidden')) $('#consoleInput').focus();
  },

  pushMessage(role, text, options = {}) {
    if (!options.pending) J.state.messages.push({ role, content: text, options });
    const el = this._appendMsg($('#consoleLog'), role, text, options);
    const mirror = this._appendMsg($('#convLog'), role, text, options);
    if (options.pending) this.pendingReplyNodes = [el, mirror];
    return el;
  },

  _appendMsg(container, role, text, options = {}) {
    if (!container) return null;
    const el = document.createElement('div');
    el.className = `msg ${role === 'user' ? 'user' : ''} ${options.error ? 'error' : ''}`;
    const stamp = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    el.innerHTML = `<div class="who">${role === 'user' ? 'VOUS' : 'JARVIS'} <span class="msg-time">· ${stamp}</span></div>
      <div class="bubble">${role === 'user' ? esc(text) : mdToHtml(text)}</div>`;
    if (options.actions) {
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.innerHTML = options.actions;
      el.appendChild(actions);
    }
    const near = (container.scrollHeight - container.scrollTop - container.clientHeight) < 96;
    container.appendChild(el);
    if (near || options.pending || role === 'user') container.scrollTop = container.scrollHeight;
    return el;
  },

  async loadConversation() {
    const res = await J.get('/api/conversations?limit=1');
    const current = res.current;
    if (!current) return;
    J.state.conversation = current;
    const conv = await J.get(`/api/conversations/${current}?limit=30`);
    if (!conv.ok) return;
    J.state.messages = [];
    $('#consoleConv').textContent = (conv.conversation.title || '').slice(0, 22) || '—';
    $('#consoleLog').innerHTML = '';
    $('#convLog').innerHTML = '';
    window.ImageMessages?.reset();
    window.ModelMessages?.reset();
    const jobsById = {};
    try {
      const imgs = await J.get(`/api/images?conversation_id=${current}&limit=40`);
      (imgs.jobs || []).forEach((job) => { jobsById[job.id] = job; });
    } catch { /* historique image indisponible : la conversation reste lisible */ }

    const modelsById = {};
    try {
      const models = await J.get(`/api/blender/jobs?conversation_id=${current}&limit=40`);
      (models.jobs || []).forEach((job) => { modelsById[job.id] = job; });
    } catch { /* historique 3D indisponible : la conversation reste lisible */ }

    (conv.conversation.messages || []).forEach((m) => {
      if (m.role !== 'user' && m.role !== 'assistant') return;
      const jobIds = (m.meta && m.meta.image_jobs) || [];
      if (jobIds.length) {
        // Le message porte une image : on rejoue la carte, puis le texte.
        jobIds.forEach((id) => window.ImageMessages?.restore(jobsById[id]));
        if (m.content && m.content !== "C'est prêt. Voici l'image.") {
          this.pushMessage(m.role, m.content);
        }
        return;
      }
      const modelIds = (m.meta && m.meta.blender_jobs) || [];
      if (modelIds.length) {
        // Le message porte un modèle 3D : carte + aperçu + viewer à la demande.
        modelIds.forEach((id) => window.ModelMessages?.restore(modelsById[id]));
        if (m.content && m.content !== "C'est pret. Voici le modele 3D.") {
          this.pushMessage(m.role, m.content);
        }
        return;
      }
      this.pushMessage(m.role, m.content);
    });
  },

  clearPendingReply() {
    (this.pendingReplyNodes || []).forEach((node) => node?.remove());
    this.pendingReplyNodes = [];
    if (this.pendingReply) {
      const pending = this.pendingReply;
      this.pendingReply = null;
      pending.remove();
    }
  },

  /** Unique point de rendu de la réponse de JARVIS (événement jarvis.reply). */
  renderReply(text, result) {
    clearTimeout(this._sheetStall);
    this.clearPendingReply();
    const el = this.pushMessage('jarvis', text, { error: result.ok === false });
    console.debug('[CHAT-UI] assistant message added');
    if (result.task_id) {
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.innerHTML = `<button class="btn sm" data-open-task="${result.task_id}">${icon('eye', 11)} Détail d'exécution</button>`;
      actions.querySelector('button').onclick = () => Pages.showTask(result.task_id);
      el.appendChild(actions);
    }
    if (result.analysis_workspace) this.attachWorkspace(el, result.analysis_workspace);
    // Duree totale, discrete : mesuree par le pipeline, pas estimee ici.
    if (result.timings && result.timings.total_ms) {
      const t = document.createElement('div');
      t.className = 'sheet-duration';
      t.textContent = `Analyse terminee en ${(result.timings.total_ms / 1000).toFixed(1)} s`;
      el?.appendChild(t);
    }
    if (result.needs_confirmation) this.showConfirmation(result.needs_confirmation);
    Dashboard.refresh();
  },

  /* Analyse de source : le chat reste court, le detail s'ouvre dans le
     Analysis Workspace rendu depuis le payload structure du backend. */
  attachWorkspace(el, payload) {
    const actions = document.createElement('div');
    actions.className = 'actions';
    actions.innerHTML = `<button class="btn sm primary" data-open-workspace>${icon('eye', 11)} Ouvrir l'Analysis Workspace</button>`;
    // Le bouton reste le filet de securite : meme si l'ouverture automatique
    // echoue, le resultat de l'analyse n'est jamais perdu.
    actions.querySelector('button').onclick = () => this.openWorkspaceSafely(payload);
    el?.appendChild(actions);
    this.openWorkspaceSafely(payload, el);
  },

  /* Ouverture du Workspace : une exception ici ne doit jamais faire disparaitre
     l'analyse en silence. On trace, et le message porte de quoi la rouvrir. */
  openWorkspaceSafely(payload, el) {
    const AW = window.AnalysisWorkspace;
    try {
      if (!AW || typeof AW.open !== 'function') throw new Error('AnalysisWorkspace indisponible');
      AW.open(payload);
      if (!AW.isOpen?.()) throw new Error('Workspace monte mais non visible');
      console.debug('[SHEET-UI] workspace_rendered', payload?.intent, (payload?.sections || []).length);
      return true;
    } catch (error) {
      console.error('[SHEET-UI] workspace_render_failed', error);
      if (el) {
        const note = document.createElement('div');
        note.className = 'actions';
        note.textContent = 'Analyse terminee — ouvrir le Workspace';
        el.appendChild(note);
      }
      return false;
    }
  },

  async send(text) {
    return this.sendJarvisMessage(text, 'text');
  },

  async sendJarvisMessage(text, source = 'text', attachments = null) {
    text = String(text || '').trim();
    // Les pièces jointes sont prélevées ici, au moment où l'utilisateur
    // envoie : la barre se vide tout de suite, mais si l'envoi est mis en
    // file (envoi déjà en cours) les ids voyagent avec le message.
    if (attachments === null) {
      attachments = window.AttachmentsUI?.takeIds() || [];
      if (attachments.length) window.AttachmentsUI.clear();
    }
    if (!text) return;
    if (this.sending) {
      return new Promise((resolve) => {
        (this.sendQueue ||= []).push({ text, source, attachments, resolve });
      });
    }
    this.sending = true;
    J.state.chatStatus = 'pending';
    try {
    await this.conversationReady;
    this.openConsole();
    this.pushMessage('user', text);
    console.debug('[CHAT-UI] user message added', source);
    this.pendingReply = this.pushMessage('jarvis', '…', { pending: true });
    const res = await VoiceManager.submit(text, { silent: true, source, attachments });
    if (!res) { this.clearPendingReply(); return; }
    if (res.needs_confirmation) return;
    const response = res.response || res.error || 'Terminé.';
    if (VoiceManager.settings?.speak_responses !== false && res.ok !== false) {
      VoiceManager.speak(response, { kind: 'reply' });
    } else if (res.ok === false) {
      this.setRobot('ERROR', { reason: 'commande en échec' });
    }
    return res;
    } catch (error) {
      this.renderReply('Erreur IA : ' + error.message, { ok: false });
      VoiceManager.setState('IDLE', 'erreur API');
    } finally {
      this.clearPendingReply();
      this.sending = false;
      J.state.chatStatus = 'idle';
      const next = this.sendQueue?.shift();
      if (next) this.sendJarvisMessage(next.text, next.source, next.attachments).then(next.resolve);
    }
  },

  /* ------------------------------------------------------- confirmation */
  showConfirmation(pending) {
    if (!pending || this.confirmationShown === pending.id) return;
    this.confirmationShown = pending.id;
    const danger = pending.risk === 'destructive';
    const m = modal({
      title: 'Confirmation requise',
      body: `<div class="risk-banner ${danger ? 'destructive' : ''}">${icon('alert', 16)}
          <div><b>${esc(pending.action || '')}</b><br>${esc(pending.reason || '')}</div></div>
        <p class="text-dim" style="font-size:11.5px;margin:0">
          Niveau de risque : <b>${esc(pending.risk_label || pending.risk)}</b>.
          JARVIS ne demande confirmation que pour les actions sensibles ou irréversibles.</p>`,
      footer: `<button class="btn" data-no>Refuser</button>
               <button class="btn ${danger ? 'danger' : 'primary'}" data-yes>Confirmer et exécuter</button>`,
    });
    const resolve = async (approved) => {
      m.close();
      this.confirmationShown = null;
      const res = await J.post('/api/confirm', { confirmation_id: pending.id, approved });
      const text = res.response || (approved ? 'Exécuté.' : 'Annulé.');
      this.pushMessage('jarvis', text);
      this.openConsole();
      if (approved && VoiceManager.settings?.speak_responses !== false) VoiceManager.speak(text);
      Dashboard.refresh();
    };
    m.$('[data-yes]').onclick = () => resolve(true);
    m.$('[data-no]').onclick = () => resolve(false);
  },

  /* ------------------------------------------------------------ dialogs */
  newTaskDialog() {
    const m = modal({
      title: 'Nouvelle tâche',
      body: `<div class="field"><label>Que dois-je faire ?</label>
        <textarea id="taskText" placeholder="Ex. vérifie mon serveur de production et corrige le problème"></textarea></div>
        <div class="field row"><label style="flex:1;margin:0">Exécuter en arrière-plan</label>
          <button class="switch on" id="taskBg"></button></div>`,
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Lancer</button>`,
    });
    m.$('#taskBg').onclick = (e) => e.currentTarget.classList.toggle('on');
    m.$('[data-go]').onclick = async () => {
      const text = m.$('#taskText').value.trim();
      if (!text) return;
      const background = m.$('#taskBg').classList.contains('on');
      m.close();
      if (background) {
        const res = await J.post('/api/command', { text, background: true,
          conversation_id: J.state.conversation });
        this.pushMessage('user', text);
        this.pushMessage('jarvis', res.response || 'Je m\'en occupe.');
        this.openConsole();
        Dashboard.refresh();
      } else this.send(text);
    };
  },

  async runWorkflowDialog() {
    const res = await J.get('/api/workflows');
    const workflows = res.workflows || [];
    if (!workflows.length) return this.newWorkflowDialog();
    const m = modal({
      title: 'Lancer un workflow',
      body: `<div class="list">${workflows.map((w) => `
        <div class="list-row"><div class="meta"><b>${esc(w.name)}</b>
          <small>${esc(Pages.triggerLabel(w.trigger))}</small></div>
          <button class="btn sm primary" data-run="${esc(w.id)}">${icon('play', 11)} Lancer</button></div>`).join('')}</div>`,
      footer: `<button class="btn" data-close>Fermer</button>
               <button class="btn primary" data-new>Nouvelle automatisation</button>`,
    });
    m.$$('[data-run]').forEach((b) => b.onclick = async () => {
      m.close();
      toast('Exécution…');
      const r = await J.post(`/api/workflows/${b.dataset.run}/run`);
      this.pushMessage('jarvis', r.output || (r.ok ? 'Workflow terminé.' : 'Échec.'));
      this.openConsole();
    });
    m.$('[data-new]').onclick = () => { m.close(); this.newWorkflowDialog(); };
  },

  newWorkflowDialog() {
    const m = modal({
      title: 'Nouvelle automatisation',
      body: `<div class="field"><label>Nom</label><input id="wfName" placeholder="Surveillance du site"/></div>
        <div class="field"><label>Que doit faire JARVIS ?</label>
          <textarea id="wfInstruction" placeholder="Vérifie que alain-pizza.fr répond et préviens-moi sinon"></textarea></div>
        <div class="field"><label>Quand ?</label>
          <input id="wfTrigger" placeholder="chaque matin à 8h · toutes les 30 minutes · chaque lundi à 9h"/>
          <div class="hint">Écris en français : JARVIS traduit en déclencheur (cron, intervalle, quotidien, hebdomadaire).</div></div>`,
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Créer</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      const r = await J.post('/api/workflows', {
        name: m.$('#wfName').value, instruction: m.$('#wfInstruction').value,
        trigger: m.$('#wfTrigger').value,
      });
      if (!r.ok) return toast(r.error || 'Déclencheur non compris.', 'err');
      m.close();
      toast('Automatisation créée.', 'ok');
      if (J.state.page === 'workflows') Pages.render('workflows');
    };
  },

  /* ------------------------------------------------- Fabric 3D : robot */
  setRobot(state, extra = {}) {
    const robot = window.JarvisRobot;
    if (!robot) return;
    robot.setState(state, extra);
    const badge = {
      IDLE: 'IDLE', LISTENING: 'LISTENING', THINKING: 'THINKING', RECALLING: 'RECALLING',
      USING_TOOL: 'USING TOOL', CODING: 'CODING', BROWSING: 'BROWSING', DEPLOYING: 'DEPLOYING',
      LEARNING: 'LEARNING', VERIFYING: 'VERIFYING', SPEAKING: 'SPEAKING', SUCCESS: 'SUCCESS',
      WARNING: 'WARNING', ERROR: 'ERROR', SLEEPING: 'SLEEPING',
    };
    const label = $('#robotStateLabel');
    const dot = $('#robotStateDot');
    const reasonEl = $('#robotReason');
    if (label) label.textContent = badge[state] || state;
    if (dot) {
      dot.className = 'dot ' + (state === 'ERROR' ? 'err' : state === 'WARNING' ? 'warn' : state === 'SUCCESS' ? 'ok' : 'idle');
    }
    if (reasonEl) reasonEl.textContent = extra.reason || '—';
    const pulse = $('#robotPulse');
    if (pulse) pulse.classList.toggle('on', state !== 'IDLE' && state !== 'SLEEPING');
  },

  _flashState(state, extra = {}, ms = 1600) {
    const token = ++this._stateToken;
    const robot = window.JarvisRobot;
    const prev = robot?.state;
    this.setRobot(state, extra);
    setTimeout(() => {
      // Ne réinitialise que si l'état flashé est toujours le courant
      // (jamais d'écrasement d'une parole en cours, d'un outil actif…).
      if (token === this._stateToken && window.JarvisRobot?.state === state) {
        this.setRobot('IDLE');
      }
    }, ms);
  },

  toolRobotState(name = '') {
    // Une génération d'image n'est pas de la navigation web : pas de BROWSING.
    if (/^image\.|imagegen|diffusion/.test(name)) return 'USING_TOOL';
    if (/opencode|cursor|editor|\.code\./.test(name)) return 'CODING';
    if (/browser|web\.|search|\.web\b|scrape/.test(name)) return 'BROWSING';
    if (/ssh|ftp|sftp|scp|deploy|rsync|docker.*compose|\.deploy\./.test(name)) return 'DEPLOYING';
    if (/llm|infer|chat/.test(name)) return 'THINKING';
    return 'USING_TOOL';
  },

  /* -------------------------------------------------------------- agents */
  onAgentEvent(type, data) {
    data = data || {};
    Dashboard?.agentEvent(type, data);
    const robot = window.JarvisRobot;
    const agentLabel = data.name || data.id || 'agent';
    if (type === 'agent.started') {
      this.setRobot('USING_TOOL', { reason: agentLabel + ' : ' + (data.action || 'délégation') });
    } else if (type === 'agent.completed') {
      this._flashState('SUCCESS', { reason: agentLabel + ' a terminé' }, 1800);
    } else if (type === 'agent.failed') {
      this._flashState('ERROR', { reason: agentLabel + ' en échec' }, 3000);
    } else if (type === 'agent.idle' && robot) {
      const cur = robot.state;
      if (cur === 'USING_TOOL' || cur === 'CODING' || cur === 'BROWSING') this.setRobot('IDLE');
    }
  },

  refreshBrain() {
    if (!window.JarvisBrain) return;
    fetch('/api/brain').then((r) => r.json()).then((data) => {
      window.JarvisBrain.loadBrain(data);
      window.JarvisBrain.scaleSizes();
      const hudN = $('#brainNodeCount');
      const hudE = $('#brainEdgeCount');
      if (hudN) hudN.textContent = (data.nodes || []).length;
      if (hudE) hudE.textContent = (data.edges || []).length;
    }).catch(() => {});
  },

async showNodeDetails(node) {
    if (!node) return;
    window.JarvisBrain?.selectNode(node.id);
    const sub = (x) => { window.JarvisBrain?.pulseFamily(node.family || '', ''); void x; };
    const kicker = (node.family || 'NŒUD') + (node.kind ? ' · ' + node.kind.toUpperCase() : '');
    $('#npKicker').textContent = kicker;
    $('#npTitle').textContent = node.label || node.id;
    const score = Number(node.score ?? node.confidence);
    const meta = node.meta || {};

    const rows = [];
    if (Number.isFinite(score)) {
      rows.push(`<div class="np-row"><span>Confiance</span><div class="np-score"><i style="width:${Math.max(0, Math.min(100, Math.round(score * 100)))}%"></i></div><b>${Math.round(score * 100)}%</b></div>`);
    }
    rows.push(`<div class="np-row"><span>Identifiant</span><code>${esc(node.id)}</code></div>`);
    if (node.kind === 'knowledge' || node.kind === 'memory') {
      rows.push(`<div class="np-row"><span>Validation</span><b>${meta.validated ? 'Mémoire durable validée' : 'Mémoire du jour (à valider)'}</b></div>`);
    }
    const friendly = ['connector_id', 'tool_id', 'status', 'type', 'source', 'updated_at', 'count', 'importance', 'trigger', 'project'];
    for (const k of friendly) {
      const v = meta[k];
      if (v == null || v === '') continue;
      rows.push(`<div class="np-row"><span>${esc(k)}</span><b>${esc(String(v))}</b></div>`);
    }

    // Compte-rendu : relations réelles du nœud (via le graphe, jamais inventées).
    const rel = await this._nodeRelations(node.id);
    if (rel && rel.list.length) {
      rows.push(`<div class="np-row np-relhead"><span>Relations</span></div>`);
      for (const r of rel.list) {
        rows.push(`<div class="np-rel"><i class="legend-dot" style="--ld:${BrainDotColor(r.family)}"></i><b>${esc(r.label)}</b><small>${esc(r.family)}</small></div>`);
      }
      if (rel.total > rel.list.length) {
        rows.push(`<div class="np-row"><span>&nbsp;</span><b>+ ${rel.total - rel.list.length} autres relations</b></div>`);
      }
    }

    const body = $('#npBody');
    body.innerHTML = rows.join('') + (node.summary ? `<p class="np-summary">${esc(node.summary)}</p>` : '')
      + `<div class="np-actions">
           <button class="btn sm" data-np-ask>Demander à JARVIS d'en parler</button>
           ${node.meta?.tool_id ? `<button class="btn sm primary" data-np-run>Lancer l'outil</button>` : ''}
         </div>
         <div class="np-meta">Compte-rendu réel du graphe /api/brain/graph — aucune donnée inventée.</div>`;
    $('#nodePanel').classList.remove('hidden');
    void sub;

    body.querySelector('[data-np-ask]').onclick = () => {
      $('#nodePanel').classList.add('hidden');
      this.send(`En quelques mots, dis-moi ce qu'est « ${node.label} » et ce que tu en penses.`);
    };
    const runBtn = body.querySelector('[data-np-run]');
    if (runBtn) runBtn.onclick = () => {
      $('#nodePanel').classList.add('hidden');
      this.goto('terminal');
      Terminal.type(`${node.meta.tool_id} --help`);
    };
  },

  _relCache: null,
  async _nodeRelations(id) {
    try {
      if (!this._relCache) {
        const g = await fetch('/api/brain/graph?limit=300', { signal: AbortSignal.timeout(4000) }).then((r) => r.json());
        this._relCache = {
          edges: (g.graph?.edges || []),
          nodes: (g.graph?.nodes || []).map((n) => [n.id, n]),
        };
      }
      const { edges, nodes } = this._relCache;
      const index = new Map(nodes);
      const seen = new Set();
      const list = [];
      let total = 0;
      for (const e of edges) {
        if (e.source !== id && e.target !== id) continue;
        total++;
        const oid = e.source === id ? e.target : e.source;
        const other = index.get(oid);
        if (other && !seen.has(oid) && list.length < 8) {
          seen.add(oid);
          list.push({ id: oid, label: other.label, family: other.family });
        }
      }
      return { list, total };
    } catch {
      return null;
    }
  },

  /** Amplitude réelle du TTS Piper (analyser WebAudio) → bouche du robot. */
  robotAudioLevel(level) {
    window.JarvisRobot?.setSpeakingLevel(Math.max(0, Math.min(1, level || 0)));
  },

  toggleVoice() {
    if (VoiceManager.state === 'SPEAKING') return VoiceManager.stopSpeaking('clic utilisateur');
    const listening = VoiceManager.toggleListening();
    if (listening) toast('Micro actif. Parle à JARVIS.');
  },

  updateVoiceUI(state) {
    const label = VOICE_LABELS[state] || state;
    $('#voiceStateLabel').textContent = label;
    const mic = $('#micBtn');
    mic.classList.toggle('live', ['LISTENING', 'WAKE'].includes(state));
    mic.classList.toggle('speaking', state === 'SPEAKING');
    mic.classList.toggle('busy', ['PROCESSING', 'EXECUTING'].includes(state));
    const talk = $('#talkBar');
    talk.classList.toggle('listening', ['LISTENING', 'WAKE'].includes(state));
    talk.classList.toggle('speaking', state === 'SPEAKING');
    const status = {
      IDLE: 'Appuie pour parler', WAKE: 'Je t\'écoute…', LISTENING: 'Je t\'écoute…',
      PROCESSING: 'Je réfléchis…', EXECUTING: 'J\'exécute…', SPEAKING: 'Je réponds…',
      INTERRUPTED: 'Interrompu',
    }[state] || '';
    $('#talkStatus').textContent = status;
    $('#micHint').textContent = state === 'IDLE' ? 'Tap to Speak' : label;

    // Robot : miroir du flux vocal local (une brique parmi les autres).
    const robotMap = {
      LISTENING: 'LISTENING', WAKE: 'LISTENING', PROCESSING: 'THINKING',
      EXECUTING: 'USING_TOOL', SPEAKING: 'SPEAKING',
    };
    if (robotMap[state]) this.setRobot(robotMap[state], { reason: state.toLowerCase() });
    else if (state === 'IDLE') {
      const cur = window.JarvisRobot?.state;
      if (cur && ['LISTENING', 'THINKING', 'SPEAKING'].includes(cur)) this.setRobot('IDLE');
    }
  },

  /* ------------------------------------------- progression Google Sheet */
  /* Chaque etape vient d'un evenement `sheet.progress` emis par le pipeline.
     Rien n'est simule : pas de barre 0->100 pilotee par un minuteur. */
  sheetProgress(data) {
    const bubble = this.pendingReply;
    if (!bubble) return;
    // Les faits deja etablis RESTENT affiches aux etapes suivantes : une fois
    // les 20 onglets lus, l'information ne redevient pas inconnue.
    const seen = (this._sheetFacts = data.stage === 'sheet_connect' ? {} : (this._sheetFacts || {}));
    if (data.sheet_count) seen.sheets = data.sheet_count;
    if (data.table_count) seen.tables = data.table_count;
    const facts = [];
    if (seen.sheets) facts.push(`Google Sheet · ${seen.sheets} onglets detectes`);
    if (seen.tables) facts.push(`${seen.tables} tableaux detectes`);
    if (data.total_ms) facts.push(`${(data.total_ms / 1000).toFixed(1)} s`);

    // Le libelle vient d'une allowlist backend ; seule la correction precise
    // son volume reel. Jamais « nouvelle generation complete ».
    let label = data.label || '';
    if (data.stage === 'sheet_repair' && data.claims) {
      label = `Correction de ${data.claims} affirmation${data.claims > 1 ? 's' : ''} non verifiee${data.claims > 1 ? 's' : ''}`;
    }

    // Etat du cerveau : miroir de l'etape REELLE, jamais une animation libre.
    const brain = {
      sheet_connect: 'USING_TOOL', sheet_tabs: 'USING_TOOL',
      sheet_semantic: 'THINKING', sheet_llm: 'THINKING',
      sheet_grounding: 'VERIFYING', sheet_repair: 'VERIFYING',
      sheet_workspace: 'THINKING', sheet_done: 'SUCCESS',
    }[data.stage];
    if (brain) this.setRobot(brain, { reason: label });
    window.dispatchEvent(new CustomEvent('jarvis:brain-state',
      { detail: { state: brain || 'THINKING', reason: label, zone: this.sheetZone(data.stage) } }));

    const body = bubble.querySelector('.bubble') || bubble;
    body.innerHTML = `<div class="sheet-progress">`
      + `<b>${esc(label)}</b>`
      + ` <span class="sheet-step">${data.step || 0}/${data.total || 9}</span>`
      + `<div class="sheet-facts">${esc(facts.join(' · '))}</div>`
      + `<div class="sheet-stall"></div>`
      + `</div>`;

    // Etape longue : on le DIT, dans une ligne DEDIEE. L'etape courante et les
    // faits deja obtenus restent affiches : attendre n'est pas tout perdre.
    clearTimeout(this._sheetStall);
    if (data.stage !== 'sheet_done') {
      this._sheetStall = setTimeout(() => {
        const note = bubble.querySelector('.sheet-stall');
        if (note) note.textContent = `Analyse toujours en cours — ${data.label || 'lecture du classeur'}`;
      }, 12000);
    }
  },

  sheetZone(stage) {
    if (stage === 'sheet_connect' || stage === 'sheet_tabs') return 'TOOLS';
    if (stage === 'sheet_semantic' || stage === 'sheet_llm') return 'KNOWLEDGE';
    return '';
  },

  /* ------------------------------------------------------------- flux SSE */
  bindStream() {
    // Câblerie unique : chaque événement brut est routé (robot, atlas, activité).
    J.on('event', (event) => this.routeEvent(event));

    J.on('stream.open', () => { $('#sysDot').classList.remove('err'); });
    J.on('stream.close', () => {
      $('#sysStatusTextHdr').textContent = 'RECONNEXION';
      $('#sysStatusTextHdr').style.color = 'var(--warn)';
    });
    J.on('voice.local', ({ state }) => this.updateVoiceUI(state));
    J.on('jarvis.greeting', ({ text }) => this.pushMessage('jarvis', text));
    J.on('jarvis.user', ({ text }) => {
      if (J.state.page === 'command' && VoiceManager.state !== 'IDLE') this.openConsole();
    });
    J.on('jarvis.reply', ({ text, result }) => this.renderReply(text, result || {}));
    J.on('sheet.progress', (data) => this.sheetProgress(data || {}));
    J.on('jarvis.confirmation', (pending) => this.showConfirmation(pending));
    // Génération d'image : le composant dédié écoute image.generation.*
    window.ImageMessages?.bind();
    // Atelier 3D : le composant dedie ecoute blender.*
    window.ModelMessages?.bind();
    // Refonte d'avatar : le composant dedie ecoute avatar.update_*
    window.AvatarStudio?.init();
    // Pièces jointes : bouton 📎, drag & drop et collage sur la barre de commande.
    window.AttachmentsUI?.init();

    J.on('feed.new', () => Dashboard.refresh());
    J.on('task.completed', () => Dashboard.refresh());
    J.on('task.failed', () => Dashboard.refresh());
    J.on('task.waiting_confirmation', (data) => {
      if (data.confirmation_id) this.showConfirmation({ ...data, id: data.confirmation_id });
    });
    J.on('agent.started', (d) => this.onAgentEvent('agent.started', d));
    J.on('agent.progress', (d) => this.onAgentEvent('agent.progress', d));
    J.on('agent.completed', (d) => this.onAgentEvent('agent.completed', d));
    J.on('agent.failed', (d) => this.onAgentEvent('agent.failed', d));
    J.on('agent.idle', (d) => this.onAgentEvent('agent.idle', d));
    J.on('task.completed', () => Dashboard.refreshSoon(1500));
    J.on('task.failed', () => Dashboard.refreshSoon(1500));
    J.on('task.created', () => Dashboard.refreshSoon(1500));
    J.on('system.metrics', () => Dashboard.refreshSoon(4000));
    J.on('connector.connected', () => Dashboard.refresh());
    J.on('connector.failed', () => Dashboard.refresh());
    J.on('workflow.completed', () => Dashboard.refresh());
    J.on('settings.updated', async () => {
      const s = await J.get('/api/settings');
      if (s.ok) {
        J.state.settings = s.settings;
        VoiceManager.updateSettings(s.settings.voice);
        this.applyQualityListeners(s.settings.appearance?.quality);
      }
    });
  },

  routeEvent(e) {
    const d = e.data || {};
    window.__activityProcessEvent?.(e);
    const robot = window.JarvisRobot;

    switch (e.type) {
      // --- états du backend (orchestrateur) ---
      case 'jarvis.state':
        if (robot) {
          const st = String(d.state || '').toUpperCase();
          if (['THINKING', 'RECALLING', 'VERIFYING', 'LEARNING'].includes(st)) {
            this.setRobot(st, { reason: d.reason || '' });
          } else if (st === 'ACTING') {
            this.setRobot(this.toolRobotState(d.tool), { reason: d.tool || d.reason || '' });
          } else if (st === 'SPEAKING') {
            this.setRobot('SPEAKING', { reason: d.reason || '' });
          } else if (st === 'ERROR') {
            this._flashState('ERROR', { reason: d.reason || d.error || 'erreur' }, 3000);
          } else if (st === 'WAITING') {
            this.setRobot('WARNING', { reason: d.reason || 'en attente' });
          }
        }
        break;

      // --- outils en cours ---
      case 'tool.started':
        this.setRobot(this.toolRobotState(d.name || d.tool), { reason: (d.name || d.tool || '') + ' ' + (d.tooltip || '') });
        window.JarvisBrain?.pulseFamily('TOOLS');
        break;
      case 'tool.completed':
        this._pulseNodeById(d.node_id);
        if (robot) this._flashState('SUCCESS', { reason: (d.name || 'outil') + ' terminé' });
        break;
      case 'tool.failed':
        if (robot) this._flashState('ERROR', { reason: (d.name || 'outil') + ' en échec' }, 3200);
        break;

      // --- avatar choreography (only explicit backend intents move the body) ---
      case 'avatar.move_requested':
        robot?.moveTo(d.position || d.target, Number(d.duration) || 900);
        break;
      case 'avatar.mode':
        robot?.setMode(d.mode || 'CALL');
        break;
      case 'avatar.gesture':
        robot?.gesture(d.gesture || 'neutral', Number(d.intensity) || 0.25);
        break;

      // --- agents spécialisés convoqués par JARVIS ---
      case 'agent.started':
      case 'agent.progress':
      case 'agent.completed':
      case 'agent.failed':
      case 'agent.idle': {
        const kindC = { 'agent.started': 'agent', 'agent.progress': 'agent', 'agent.completed': 'tool',
          'agent.failed': 'voice', 'agent.idle': 'agent' }[e.type] || 'agent';
        window.__activityProcessEvent?.({
          type: e.type, data: {
            ts: d.ts || Date.now() / 1000,
            kind: kindC, title: (d.name || d.id || 'agent') + (e.type === 'agent.idle' ? ' en attente' : ''),
            detail: d.action || '', state: e.type === 'agent.failed' ? 'ERROR' : 'ACTIVE',
          },
        });
        break;
      }

      // --- Brain Atlas ---
      case 'brain.search':
        window.JarvisBrain?.highlight(d.query);
        this._pulseByIds(d.node_ids || d.ids);
        break;
      case 'brain.node.selected':
        this._pulseByIds([d.id].filter(Boolean));
        break;
      case 'brain.path': {
        // Chemin d'impulsions : les étapes référencent des familles + labels réels.
        if (Array.isArray(d.steps)) {
          const ids = [];
          for (const step of d.steps.slice(0, 6)) {
            for (const lbl of (step.labels || []).slice(0, 8)) {
              const s = window.JarvisBrain?.simNodes?.find((x) =>
                (x.node.label || '').toLowerCase().includes(String(lbl).toLowerCase()));
              if (s && !ids.includes(s.node.id)) ids.push(s.node.id);
            }
          }
          if (ids.length) window.JarvisBrain?.pulse(ids.slice(0, 12));
        } else if (Array.isArray(d.ids)) {
          window.JarvisBrain?.pulse(d.ids.slice(0, 8));
        }
        break;
      }
      case 'brain.tool.active':
        window.JarvisBrain?.pulseFamily(d.family || 'TOOLS', d.tool_id || '');
        break;
      case 'brain.learn.created':
      case 'brain.learn.updated':
      case 'knowledge.learn.created':
      case 'knowledge.learn.updated':
      case 'learning.session.started':
      case 'learning.topic.selected':
      case 'learning.knowledge.created':
      case 'learning.knowledge.updated':
      case 'learning.session.completed':
        this._debouncedBrainRefresh();
        if (J.state.page === 'learning') Pages.render('learning');
        if (robot) this._flashState('LEARNING', { reason: d.label || d.title || 'mémoire' }, 2000);
        this._pulseByIds([d.id].filter(Boolean));
        break;

      // --- TTS : niveau sonore RÉEL (bouche du robot) ---
      case 'tts.started':
        if (robot) this.setRobot('SPEAKING', { reason: 'réponse vocale' });
        break;
      case 'tts.audio_level':
        if (robot) robot.setSpeakingLevel(Math.max(0, Math.min(1, Number(d.level ?? 0) / 100)));
        break;
      case 'tts.completed':
        if (robot) robot.setSpeakingLevel(0);
        break;
      case 'speech.sanitize.rejected':
        if (robot) this._flashState('WARNING', { reason: 'réponse non vocale', ms: 1400 });
        break;
    }
  },

  _pulseNodeById(id) {
    if (!id || !window.JarvisBrain) return;
    const ids = [id];
    // Le second nœud (si présent dans l'event) prolonge l'impulsion.
    window.JarvisBrain.pulse(ids);
  },

  _pulseByIds(ids) {
    const arr = Array.isArray(ids) ? ids.filter(Boolean) : [];
    if (arr.length && window.JarvisBrain) window.JarvisBrain.pulse(arr.slice(0, 12));
  },

  _debouncedBrainRefresh() {
    clearTimeout(this._brainRefreshTimer);
    this._brainRefreshTimer = setTimeout(() => this.refreshBrain(), 900);
  },

  /* ----------------------------------------------------------- apparence */
  applyAppearance(appearance) {
    if (!appearance) return;
    document.documentElement.style.setProperty('--cyan', appearance.accent || '#22d3ee');
    $('#app').classList.toggle('sidebar-compact', !!appearance.compact_sidebar);
    if (appearance.animations === false) {
      document.body.style.setProperty('--anim', 'none');
    }
    // Entité 3D : bascule réelle des scènes (robot + atlas).
    const show3d = appearance.sphere !== false;
    this._toggle3D(show3d);
  },

  _toggle3D(show) {
    const robotCanvas = $('#robotStage');
    const brainCanvas = $('#brainStage');
    if (robotCanvas) robotCanvas.style.display = show ? '' : 'none';
    if (brainCanvas) brainCanvas.style.display = show ? '' : 'none';
    const robotFb = $('#robotFallback');
    const brainFb = $('#brainFallback');
    if (robotFb) robotFb.hidden = show;
    if (brainFb) brainFb.hidden = show;
    window.JarvisRobot?.setVisible?.(show);
  },

  /* -------------------------------------------------------------- horloge */
  startClock() {
    const tick = () => {
      const now = new Date();
      const use24 = J.state.settings?.appearance?.clock_24h !== false;
      $('#headerTime').textContent = now.toLocaleTimeString('fr-FR',
        { hour12: !use24, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      $('#headerDate').textContent = now.toLocaleDateString('fr-FR',
        { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
        .replace(/^\w/, (c) => c.toUpperCase());
    };
    tick();
    setInterval(tick, 1000);
  },

  /* ------------------------------------------------------------ animation */
  startAnimation() {
    $('#footDots').innerHTML = Array.from({ length: 26 }, () => '<i></i>').join('');
    const loop = () => {
      this.phase += 0.08;
      const state = VoiceManager.state;
      const active = ['LISTENING', 'WAKE'].includes(state);
      const speaking = state === 'SPEAKING';
      const busy = ['PROCESSING', 'EXECUTING'].includes(state);
      const amplitude = speaking ? 0.75 : active ? 0.6 : busy ? 0.4 : 0.16;
      const color = speaking ? '#a78bfa' : busy ? '#fbbf24' : '#22d3ee';

      drawWave($('#voiceWave'), { amplitude, phase: this.phase, color, bars: 34 });
      drawWave($('#talkWaveL'), { amplitude, phase: this.phase, color, bars: 14 });
      drawWave($('#talkWaveR'), { amplitude, phase: this.phase + 1.2, color, bars: 14 });

      const dots = $$('#footDots i');
      dots.forEach((d, i) => {
        const wave = Math.sin(this.phase * 0.8 + i * 0.4);
        d.style.opacity = String(0.18 + Math.max(0, wave) * 0.75);
        d.style.transform = `scale(${1 + Math.max(0, wave) * 0.7})`;
      });
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  },
};

window.addEventListener('DOMContentLoaded', () => App.init());
window.App = App;
window.addEventListener('hashchange', () => {
  const page = location.hash.replace('#', '');
  if (page && page !== J.state.page) App.goto(page);
});
