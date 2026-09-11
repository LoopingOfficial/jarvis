/* ==========================================================================
   Pont entre le bus d'événements JARVIS et le corps de l'avatar.

   Une seule règle : chaque mouvement vient d'un fait. Aucun état n'est joué
   « pour faire joli » — si le backend ne dit rien, l'avatar reste en IDLE et
   se contente de vivre (respirer, cligner, regarder).

   Ce fichier est volontairement séparé de app.js : la câblerie corporelle
   doit rester lisible d'un seul tenant.
   ========================================================================== */
const AvatarBridge = {
  avatar: null,
  _speaking: false,
  _lastUserSpeech: 0,

  attach(avatar) {
    this.avatar = avatar;
    this._applySettings(J.state.settings?.avatar);
    this._bindEvents();
    this._bindVoice();
    this._spawn();
  },

  _applySettings(settings) {
    if (!settings || !this.avatar) return;
    if (settings.quality) this.avatar.setQuality(settings.quality);
    if (settings.view) this.avatar.setViewMode(settings.view);
  },

  /* ------------------------------------------------------- entrée en scène */
  _spawn() {
    const settings = J.state.settings?.avatar || {};
    if (settings.spawn_animation === false) return;
    // Il entre par le côté, se place, puis regarde la caméra.
    const stage = this.avatar.stage.get('offstage');
    this.avatar.avatarRoot.position.copy(stage.position);
    this.avatar.locomotion.facing = stage.facing;
    this.avatar.setCameraMode('FULL_BODY', { instant: true });
    setTimeout(() => {
      this.avatar.moveTo('home', {
        face: 0,
        onArrive: () => {
          this.avatar.setCameraMode(
            (J.state.settings?.avatar?.view || 'CALL') === 'FULL_BODY'
              ? 'FULL_BODY' : 'CALL');
          this.avatar.gesture({ gesture: 'neutral', emotion: 'friendly', intensity: 0.3 });
        },
      });
    }, 600);
  },

  /* ------------------------------------------------------------ événements */
  _bindEvents() {
    const a = this.avatar;

    // --- états décidés par le backend ---
    J.on('avatar.state', (d) => a.setState(String(d.state || 'IDLE').toUpperCase(),
      { reason: d.reason }));
    J.on('avatar.gesture', (d) => a.gesture(d));
    J.on('avatar.view', (d) => a.setViewMode(d.view));
    J.on('avatar.look', (d) => { if (d.place) a.lookAt(d.place); });
    J.on('avatar.move_requested', (d) => {
      if (!d.place) return;
      a.moveTo(d.place, { onArrive: () => a.setState('IDLE') });
    });

    // --- état global de l'orchestrateur ---
    J.on('jarvis.state', (d) => {
      const map = {
        THINKING: 'THINKING', RECALLING: 'RECALLING', ACTING: 'ACTING',
        SPEAKING: 'SPEAKING', LEARNING: 'RECALLING', VERIFYING: 'ACTING',
        ERROR: 'ERROR', WAITING: 'WARNING',
      };
      const state = map[String(d.state || '').toUpperCase()];
      if (state) a.setState(state, { reason: d.reason });
    });

    // --- écoute réelle du micro ---
    J.on('speech.listening.started', () => a.setState('LISTENING'));
    J.on('speech.listening.stopped', () => {
      if (a.state === 'LISTENING') a.setState('UNDERSTANDING');
    });
    J.on('voice.transcript', () => {
      this._lastUserSpeech = performance.now();
      if (a.state === 'LISTENING' && Math.random() < 0.4) {
        a.gesture({ gesture: 'acknowledge', intensity: 0.25 });
      }
    });

    // --- outils réellement exécutés ---
    J.on('tool.started', (d) => {
      const name = String(d.tool_id || d.tool || '');
      if (/^(memory|knowledge)\./.test(name)) {
        a.setState('RECALLING', { reason: name });
        a.lookAt('brain');
      } else if (/^(fs|terminal|code|git|blender)\./.test(name)) {
        a.setState('CODING', { reason: name });
      } else if (/^(web|http)\./.test(name)) {
        a.setState('BROWSING', { reason: name });
      } else if (/^(deploy|docker|ssh|ftp|cpanel|whm)\./.test(name)) {
        a.setState('DEPLOYING', { reason: name });
      } else {
        a.setState('ACTING', { reason: name });
      }
    });
    J.on('tool.completed', (d) => {
      if (d.ok === false) return;
      a.gesture({ gesture: 'acknowledge', emotion: 'focused', intensity: 0.25 });
    });
    J.on('tool.failed', () => a.setState('WARNING', { reason: 'outil en échec' }));

    // --- génération d'image : le GPU est sollicité, on lui laisse la place ---
    J.on('image.generation.started', () => {
      a.setGpuBusy(true);
      a.setState('ACTING', { reason: 'génération image' });
    });
    J.on('image.generation.completed', () => {
      a.setGpuBusy(false);
      a.gesture({ gesture: 'success', emotion: 'pleased', intensity: 0.5 });
    });
    J.on('image.generation.failed', () => {
      a.setGpuBusy(false);
      a.setState('ERROR', { reason: 'génération échouée' });
    });

    // --- Brain Atlas : le corps regarde ce que la mémoire consulte ---
    J.on('brain.search', () => a.lookAt('brain'));
    J.on('knowledge.learn.created', () => {
      a.lookAt('brain');
      a.gesture({ gesture: 'acknowledge', intensity: 0.3 });
    });

    // --- fin d'échange ---
    J.on('task.completed', () => {
      if (a.state === 'ACTING' || a.state === 'CODING' || a.state === 'DEPLOYING') {
        a.setState('IDLE');
      }
    });

    // --- réglages ---
    J.on('settings.updated', async () => {
      const res = await J.get('/api/settings');
      if (res.ok) this._applySettings(res.settings.avatar);
    });
  },

  /* ------------------------------------------------------------------ voix */
  _bindVoice() {
    const a = this.avatar;

    // Lip sync : piloté par le texte réellement prononcé.
    J.on('tts.started', (d) => {
      this._speaking = true;
      a.speak(String(d.text || ''), { duration: Number(d.duration) || 0 });
    });
    J.on('tts.audio_level', (d) => a.setAudioLevel(Number(d.level) || 0));
    J.on('tts.completed', () => {
      this._speaking = false;
      a.stopSpeaking();
    });

    // Barge-in : l'utilisateur coupe la parole.
    J.on('voice.local', ({ state }) => {
      if (state === 'LISTENING' && this._speaking) {
        this._speaking = false;
        a.stopSpeaking({ bargeIn: true });
      }
    });
  },
};

window.AvatarBridge = AvatarBridge;
window.addEventListener('jarvis-avatar-ready', (e) => {
  try {
    AvatarBridge.attach(e.detail);
    console.info('[avatar] corps connecté au bus JARVIS');
  } catch (err) {
    console.error('[avatar] câblage impossible', err);
  }
});
