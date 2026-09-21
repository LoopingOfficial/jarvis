/* ==========================================================================
   AUDIO MANAGER — instance UNIQUE contrôlant STT, VAD, wake word, TTS, micro.

   Ce fichier corrige définitivement le bug historique :
     « Bonjour Jérôme, comment puis-je vous aider ? » répété en boucle,
     et JARVIS qui coupait ses propres phrases.

   Règles structurelles (aucune exception) :
   1.  Une SEULE instance de reconnaissance et une SEULE file TTS.
       Aucun second listener ne peut être créé (garde `_singleton`).
   2.  Le greeting n'est JAMAIS déclenché ici. Il est demandé une seule fois
       au serveur (`POST /api/voice/greeting`) au démarrage de la page ; le
       serveur répond `speak:true` au plus une fois par session utilisateur.
       Aucun autre chemin de code n'appelle cet endpoint.
   3.  Aucun timer, heartbeat, reconnexion SSE, redémarrage STT, timeout VAD
       ou fin de TTS ne produit de parole. Ils se contentent de remettre la
       machine en IDLE, silencieusement.
   4.  Pendant SPEAKING, la reconnaissance est suspendue (ou filtrée si
       barge-in activé) : JARVIS ne peut pas s'entendre lui-même.
   5.  Une réponse vocale va jusqu'au bout, sauf STOP explicite de
       l'utilisateur, nouvelle commande utilisateur, ou erreur réelle.
   ========================================================================== */
(function () {
  const STATES = {
    IDLE: 'IDLE', WAKE: 'WAKE', LISTENING: 'LISTENING', PROCESSING: 'PROCESSING',
    EXECUTING: 'EXECUTING', SPEAKING: 'SPEAKING', INTERRUPTED: 'INTERRUPTED',
  };
  const LABELS = {
    IDLE: 'Idle', WAKE: 'Wake', LISTENING: 'Listening', PROCESSING: 'Thinking',
    EXECUTING: 'Executing', SPEAKING: 'Speaking', INTERRUPTED: 'Interrupted',
  };

  let _singleton = null;

  class AudioManager {
    constructor() {
      if (_singleton) return _singleton;      // garde anti-listeners concurrents
      _singleton = this;

      this.state = STATES.IDLE;
      this.session = null;
      this.settings = null;
      this.clientId = this._clientId();

      // reconnaissance
      this.recognition = null;
      this.recognitionActive = false;
      this.wantsListening = false;      // l'utilisateur veut-il que le micro tourne ?
      this.manualStop = false;
      this.restartTimer = null;
      this.silenceTimer = null;

      // synthèse
      this.ttsQueue = [];
      this.ttsSpeaking = false;
      this.currentUtterance = null;
      this.voices = [];
      this.suppressUntil = 0;           // fenêtre anti-écho après TTS

      // synthèse locale Piper (serveur → WAV)
      this.piperAvailable = false;
      this.piperVoices = [];
      this.ttsGeneration = 0;           // invalide toute lecture en cours au stop
      this.currentAudio = null;

      // conversation
      this.conversationUntil = 0;
      this.lastSpokenText = '';
      this.greetingRequested = false;   // verrou local : une seule requête par chargement

      this.available = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
      // Repli serveur : enregistrement local transcrit par faster-whisper.
      this.serverSttReady = false;
      this.serverSttInfo = null;
      this.recordingActive = false;
      this.mediaStream = null;
      this.recorder = null;
      this._stopSegment = null;
      this.ttsAvailable = 'speechSynthesis' in window;
      this._loadVoices();
    }

    /* ------------------------------------------------------------ init */
    async init(settings) {
      this.settings = settings;
      // Ouverture/reprise de session — la reprise ne resalue jamais.
      const res = await J.post('/api/voice/session', { client_id: this.clientId });
      if (res.ok) {
        this.session = res.session;
        try { localStorage.setItem('jarvis.client_id', this.session.client_id); } catch { /* ignore */ }
      }
      this._setupRecognition();
      await this._sttSync();
      this._startHeartbeat();
      await this._piperSync();
      await this._maybeGreetOnce();
      this._applyMode();
      return this.session;
    }

    _clientId() {
      try {
        let id = localStorage.getItem('jarvis.client_id');
        if (!id) {
          id = 'web_' + Math.random().toString(36).slice(2, 12);
          localStorage.setItem('jarvis.client_id', id);
        }
        return id;
      } catch {
        return 'web_' + Math.random().toString(36).slice(2, 12);
      }
    }

    /**
     * SEUL point d'entrée du message d'accueil de toute l'application.
     * Appelé une fois au chargement. Le serveur tranche ; en cas de session
     * reprise (rechargement, reconnexion, second onglet) la réponse est
     * `speak:false` et rien n'est prononcé.
     */
    async _maybeGreetOnce() {
      if (this.greetingRequested || !this.session) return;
      this.greetingRequested = true;
      if (this.session.resumed) return;               // session reprise → silence
      const res = await J.post('/api/voice/greeting', { session_id: this.session.session_id });
      if (res.ok && res.speak && res.text) {
        J.fire('jarvis.greeting', { text: res.text });
        this.speak(res.text, { kind: 'greeting' });
      }
    }

    /* --------------------------------------------------- machine d'états */
    canTransition(target) {
      const allowed = {
        IDLE: ['WAKE', 'LISTENING', 'PROCESSING', 'SPEAKING', 'IDLE'],
        // WAKE → SPEAKING : uniquement l'accusé bref (« Oui ? »), jamais le greeting.
        WAKE: ['LISTENING', 'IDLE', 'PROCESSING', 'SPEAKING'],
        LISTENING: ['PROCESSING', 'IDLE', 'WAKE', 'LISTENING'],
        PROCESSING: ['EXECUTING', 'SPEAKING', 'IDLE', 'INTERRUPTED'],
        EXECUTING: ['SPEAKING', 'IDLE', 'EXECUTING', 'INTERRUPTED', 'PROCESSING'],
        SPEAKING: ['IDLE', 'INTERRUPTED', 'LISTENING', 'SPEAKING'],
        INTERRUPTED: ['IDLE', 'LISTENING'],
      };
      return (allowed[this.state] || []).includes(target);
    }

    setState(target, reason = '') {
      if (!STATES[target]) return false;
      if (!this.canTransition(target)) {
        console.debug(`[voice] transition refusée ${this.state}→${target} (${reason})`);
        return false;
      }
      const previous = this.state;
      this.state = target;
      J.fire('voice.local', { state: target, previous, reason });
      // Informe le serveur (pour le dashboard) — sans effet sur le greeting.
      J.post('/api/voice/state', { state: target, reason }).catch(() => {});
      return true;
    }

    /* ---------------------------------------------------------- micro */
    _setupRecognition() {
      if (!this.available || this.recognition) return;
      const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
      const rec = new Ctor();
      rec.lang = (this.settings?.stt_language) || 'fr-FR';
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;

      rec.onstart = () => {
        this.recognitionActive = true;
        J.fire('voice.mic', { active: true });
      };

      rec.onresult = (event) => {
        // Pendant que JARVIS parle, on ignore tout (anti auto-déclenchement).
        if (this.state === STATES.SPEAKING && !this._bargeInEnabled()) return;
        if (Date.now() < this.suppressUntil) return;

        let interim = '';
        let final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const chunk = event.results[i][0].transcript;
          if (event.results[i].isFinal) final += chunk;
          else interim += chunk;
        }
        if (interim) {
          // BARGE-IN : dès que le micro entend l'utilisateur, la parole en
          // cours est coupée. On n'attend pas la transcription finale — c'est
          // ce délai-là qui donnait l'impression que JARVIS n'écoutait pas.
          if (this.state === STATES.SPEAKING && this._bargeInEnabled()) {
            this.stopSpeaking('barge-in');
            // L'utilisateur est en train de parler : on affiche l'écoute, pas
            // un retour en veille.
            if (this.wantsListening) this.setState(STATES.LISTENING, 'barge-in');
          }
          J.fire('voice.interim', { text: interim });
          this._armSilenceTimer();
        }
        if (!final.trim()) return;
        this._handleTranscript(final.trim());
      };

      rec.onerror = (event) => {
        // « no-speech », « aborted », « audio-capture » : jamais de parole ici.
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          this.wantsListening = false;
          J.fire('voice.error', { error: 'micro refusé' });
          toast('Micro refusé par le navigateur. Autorise-le pour parler à JARVIS.', 'err');
        } else if (event.error === 'audio-capture') {
          J.fire('voice.error', { error: 'aucun micro' });
        }
      };

      rec.onend = () => {
        this.recognitionActive = false;
        J.fire('voice.mic', { active: false });
        // Redémarrage silencieux si l'utilisateur veut continuer d'écouter.
        // Ce chemin ne produit AUCUN son ni aucun message.
        if (this.wantsListening && !this.manualStop && this.state !== STATES.SPEAKING) {
          clearTimeout(this.restartTimer);
          this.restartTimer = setTimeout(() => this._startRecognition(), 320);
        } else if (!this.wantsListening && this.state === STATES.LISTENING) {
          this.setState(STATES.IDLE, 'fin de reconnaissance');
        }
      };

      this.recognition = rec;
    }

    _startRecognition() {
      if (!this.recognition || this.recognitionActive) return;
      if (this.state === STATES.SPEAKING && !this._bargeInEnabled()) return;
      try {
        this.manualStop = false;
        this.recognition.start();
      } catch { /* déjà démarrée */ }
    }

    _stopRecognition({ manual = false } = {}) {
      clearTimeout(this.restartTimer);
      if (manual) this.manualStop = true;
      if (this.recognition && this.recognitionActive) {
        try { this.recognition.stop(); } catch { /* ignore */ }
      }
    }

    _armSilenceTimer() {
      clearTimeout(this.silenceTimer);
      const seconds = Number(this.settings?.silence_timeout_s || 6);
      this.silenceTimer = setTimeout(() => {
        // Timeout VAD : retour silencieux en veille. Jamais de greeting.
        if (this.state === STATES.LISTENING || this.state === STATES.WAKE) {
          this.setState(STATES.IDLE, 'silence');
          if (this.settings?.mode === 'push_to_talk') this.stopListening();
        }
      }, Math.max(2, seconds) * 1000);
    }

    /* ----------------------------------------------------- transcription */
    _handleTranscript(text) {
      clearTimeout(this.silenceTimer);
      const mode = this.settings?.mode || 'wake_word';
      const normalized = text.toLowerCase().trim();

      // Mot d'arrêt explicite : seule chose qui coupe la parole de JARVIS.
      const stopWords = this.settings?.stop_words || ['stop', 'arrête', 'silence'];
      if (stopWords.some((w) => normalized === w || normalized.startsWith(w + ' '))) {
        this.stopSpeaking('demande utilisateur');
        return;
      }

      J.fire('voice.transcript', { text });
      J.post('/api/voice/transcript', { text, final: true }).catch(() => {});

      let command = text;
      const inConversation = Date.now() < this.conversationUntil;

      if (mode === 'wake_word' || (mode === 'conversation' && !inConversation)) {
        const stripped = this._stripWakeWord(normalized, text);
        if (stripped === null) return;                    // pas de mot d'éveil → on ignore
        command = stripped;
        this.setState(STATES.WAKE, 'wake word');
        if (!command) {
          // « Jarvis » seul : accusé de réception très bref, jamais le greeting.
          if (this.settings?.wake_ack_enabled !== false) {
            this.speak(this.settings?.wake_ack || 'Oui ?', { kind: 'ack', short: true });
          }
          this.setState(STATES.LISTENING, 'attente de la commande');
          this._armSilenceTimer();
          this._extendConversation();
          return;
        }
      }

      if (!command.trim()) return;
      this._extendConversation();
      window.App.sendJarvisMessage(command, 'voice');
    }

    _stripWakeWord(normalized, original) {
      const word = (this.settings?.wake_word || 'jarvis').toLowerCase();
      const aliases = [word, ...(this.settings?.wake_word_aliases || [])].map((w) => w.toLowerCase());
      for (const alias of aliases) {
        const rx = new RegExp(`^(?:h[ée]|ok|dis|salut)?\\s*${alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b[\\s,.:;!?]*`, 'i');
        if (rx.test(normalized)) return original.replace(rx, '').trim();
      }
      return null;
    }

    _extendConversation() {
      const window_s = Number(this.settings?.conversation_window_s || 45);
      if ((this.settings?.mode || '') === 'conversation') {
        this.conversationUntil = Date.now() + window_s * 1000;
      }
    }

    /* ------------------------------------------------------- soumission */
    async submit(text, { silent = false, source = silent ? 'text' : 'voice', attachments = null, signal = null } = {}) {
      if (!text || !text.trim()) return null;
      // Nouvelle commande utilisateur : on arrête proprement la parole en cours.
      if (this.state === STATES.SPEAKING) this.stopSpeaking('nouvelle commande');
      this.setState(STATES.PROCESSING, 'commande');
      J.fire('jarvis.user', { text });

      const payload = { text, source };
      // Commande vocale : rien n'est joint, l'appelant ne passe pas d'ids.
      // Commande texte : les `attachment_id` déjà uploadés accompagnent le texte.
      const attached = attachments || [];
      if (attached.length) payload.attachments = attached;
      if (J.state.conversation) payload.conversation_id = J.state.conversation;
      console.debug('[CHAT-UI] API request started', payload.conversation_id);
      const res = await J.post('/api/command', payload, signal ? { signal } : undefined);
      console.debug('[CHAT-UI] API response received', res.ok);

      if (res.conversation_id) J.state.conversation = res.conversation_id;
      const response = res.response || (res.ok ? 'Terminé.' : (res.error || 'Échec.'));
      J.fire('jarvis.reply', { text: response, result: res });

      if (res.needs_confirmation) {
        this.setState(STATES.SPEAKING, 'demande de confirmation');
        // À l'oral, on pose la question courte fournie par le garde-fou
        // (« Envoyer un e-mail à Pierre. Tu confirmes ? ») ; l'écran garde la
        // description complète de l'action et son motif.
        this.speak(res.needs_confirmation.speech || response, { kind: 'confirmation' });
        J.fire('jarvis.confirmation', res.needs_confirmation);
        return res;
      }
      if (!silent && this.settings?.speak_responses !== false) {
        this.speak(response, { kind: 'reply' });
      } else {
        this.setState(STATES.IDLE, 'réponse texte');
      }
      return res;
    }

    /* -------------------------------------------------------------- TTS */
    _loadVoices() {
      if (!this.ttsAvailable) return;
      const load = () => { this.voices = window.speechSynthesis.getVoices() || []; };
      load();
      window.speechSynthesis.onvoiceschanged = load;
    }

    _pickVoice() {
      const wanted = this.settings?.voice;
      if (wanted) {
        const match = this.voices.find((v) => v.name === wanted);
        if (match) return match;
      }
      const lang = (this.settings?.language || 'fr-FR').slice(0, 2);
      const preferred = ['Thomas', 'Daniel', 'Amelie', 'Google français', 'Aurelie'];
      for (const name of preferred) {
        const match = this.voices.find((v) => v.name.includes(name) && v.lang.startsWith(lang));
        if (match) return match;
      }
      return this.voices.find((v) => v.lang.startsWith(lang)) || null;
    }

    /* ------------------------------------------------------ TTS distant Piper */
    /** True quand le moteur doit être Piper (vérifié contre le serveur). */
    _ttsRemote() {
      return (this.settings?.tts_provider === 'piper' && this.piperAvailable)
        || (this.settings?.tts_provider === 'edge' && this.ttsFallback);
    }

    /** Récupère l'état des voix françaises Piper auprès du serveur. */
    async _piperSync() {
      try {
        const res = await J.get('/api/tts/voices');
        if (res.ok) {
          this.piperVoices = res.voices || [];
          // Le serveur bascule sur les voix Edge quand aucun modèle Piper n'est
          // installé : la synthèse distante reste donc jouable dans ce cas.
          this.ttsFallback = !!(res.fallback?.available);
          this.piperAvailable = (!!(res.engine?.available)
            && (this.piperVoices || []).some((v) => v.installed))
            || this.ttsFallback;
        } else {
          this.piperVoices = [];
          this.ttsFallback = false;
          this.piperAvailable = false;
        }
      } catch {
        this.piperAvailable = false;
      }
    }

    _pickPiperVoice() {
      const wanted = this.settings?.voice;
      if (this.settings?.tts_provider === 'edge') return wanted || 'fr-FR-HenriNeural';
      if (wanted && (this.piperVoices || []).some((v) => v.id === wanted && v.installed)) return wanted;
      const installed = (this.piperVoices || []).filter((v) => v.installed);
      return installed.length ? installed[0].id : '';
    }

    /** Demande au serveur la voix d'un texte → Blob WAV. */
    async _synthRemote(text) {
      const res = await fetch('/api/tts/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          voice: this._pickPiperVoice() || '',
          rate: Number(this.settings?.speech_rate || 1),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Synthèse impossible.');
      }
      return res.blob();
    }

    /** Met une phrase dans la file. Ne coupe jamais ce qui est en cours. */
    speak(text, { kind = 'reply', short = false } = {}) {
      if (!text) return;
      // Sanitisation complète côté client (URLs, markdown, nombres…) :
      // garantit que ni le navigateur ni Piper n'énonce de syntaxe brute.
      let clean = window.pipeSpeech ? window.pipeSpeech(text) : String(text);
      // Le greeting de session est la seule salutation légitime : il vient du
      // serveur et passe intact. Tout le reste est débarrassé des formules de
      // courtoisie que le modèle remet à chaque tour.
      if (kind !== 'greeting' && window.speechStripCourtesy) {
        clean = window.speechStripCourtesy(clean);
      }
      if (!clean) return;
      this.lastSpokenText = clean;
      if (!this.ttsAvailable && !this._ttsRemote()) {
        this.setState(STATES.IDLE, 'tts indisponible');
        if (window.App?.setRobot) window.App.setRobot('IDLE');
        return;
      }
      if (this.settings?.speak_responses === false) {
        this.setState(STATES.IDLE, 'tts désactivé');
        return;
      }
      this.ttsQueue.push({ text: short ? clean.slice(0, 80) : clean, kind });
      if (!this.ttsSpeaking) this._drainQueue();
    }

    _drainQueue() {
      const next = this.ttsQueue.shift();
      if (!next) {
        this.ttsSpeaking = false;
        this._afterSpeaking();
        return;
      }
      this.ttsSpeaking = true;
      if (this.state !== STATES.SPEAKING) this.setState(STATES.SPEAKING, next.kind);

      // Anti-écho : on suspend la reconnaissance si le barge-in n'est pas activé.
      if (!this._bargeInEnabled()) this._stopRecognition();

      // Routage : voix locale Piper (serveur) ou synthèse navigateur.
      if (this._ttsRemote()) this._drainRemote(next);
      else this._drainBrowser(next);
    }

    /* --- chemin « navigateur » : Web Speech API -------------------------- */
    _drainBrowser(next) {
      // Découpe en phrases : le navigateur tronque les longues énonciations.
      const chunks = this._chunk(next.text);
      let index = 0;

      const speakChunk = () => {
        if (index >= chunks.length) {
          this.currentUtterance = null;
          this._drainQueue();
          return;
        }
        const chunk = chunks[index++];
        const utterance = new SpeechSynthesisUtterance(chunk);
        // Le corps de JARVIS a besoin du texte RÉEL et de sa durée estimée
        // pour articuler : sans ça, la bouche ne ferait qu'onduler.
        const rateForSync = Number(this.settings?.speech_rate || 1);
        J.fire('tts.started', {
          text: chunk,
          duration: Math.max(0.4, (chunk.length / 14) / Math.max(0.5, rateForSync)),
        });
        utterance.onboundary = (ev) => {
          if (typeof ev.charIndex !== 'number') return;
          if (window.JarvisAvatar) window.JarvisAvatar.resyncSpeech(ev.charIndex, chunk.length);
          // La progression réelle passe aussi par le bus : c'est la seule
          // mesure vraie dont dispose une bouche pour se recaler, et elle ne
          // doit pas rester réservée à un unique consommateur codé en dur.
          J.fire('tts.boundary', { charIndex: ev.charIndex, total: chunk.length });
        };
        utterance.lang = this.settings?.language || 'fr-FR';
        utterance.rate = Number(this.settings?.speech_rate || 1);
        utterance.pitch = Number(this.settings?.pitch || 0.9);
        utterance.volume = Number(this.settings?.volume || 1);
        const voice = this._pickVoice();
        if (voice) utterance.voice = voice;

        // Chien de garde : certains environnements (aucune voix installée,
        // onglet en arrière-plan, moteur TTS défaillant) ne déclenchent jamais
        // `onend`. Sans ce filet, JARVIS resterait bloqué en SPEAKING et le
        // micro ne reprendrait jamais. Le délai est estimé sur la longueur du
        // texte, avec une marge large : il ne peut donc pas couper une phrase
        // réellement en cours.
        let done = false;
        const budget = 2500 + (chunk.length / Math.max(0.5, utterance.rate)) * 110;
        const watchdog = setTimeout(() => {
          if (done) return;
          done = true;
          J.fire('voice.error', { error: 'tts-timeout', chunk: chunk.slice(0, 40) });
          speakChunk();
        }, Math.min(60000, budget));
        const advance = () => {
          if (done) return;
          done = true;
          clearTimeout(watchdog);
          J.fire('tts.completed', { chunk: chunk.slice(0, 40) });
          speakChunk();          // enchaîne. AUCUN message, AUCUN greeting.
        };
        utterance.onend = advance;
        utterance.onerror = advance;

        this.currentUtterance = utterance;
        try {
          window.speechSynthesis.speak(utterance);
        } catch {
          advance();
        }
      };
      speakChunk();
    }

    /* --- chemin « Piper » : WAV local généré par le serveur -------------- */
    _drainRemote(next) {
      const gen = ++this.ttsGeneration;
      const chunks = this._chunk(next.text);
      let index = 0;

      const playChunk = async () => {
        if (gen !== this.ttsGeneration) return;     // lecture annulée
        if (index >= chunks.length) {
          this.currentAudio = null;
          this._drainQueue();                       // enchaîne la file de TTS
          return;
        }
        const chunk = chunks[index++];
        try {
          const blob = await this._synthRemote(chunk);
          if (gen !== this.ttsGeneration) return;
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.volume = Number(this.settings?.volume ?? 1);
          try {
            audio.playbackRate = Math.max(0.25, Math.min(4, Number(this.settings?.speech_rate || 1)));
          } catch { /* playbackRate non supporté */ }
          this.currentAudio = audio;
          // Vrai niveau sonore (Analyseur WebAudio) pour la bouche du robot.
          const stopMeter = this._attachLevelMeter(audio);
          const finish = () => {
            stopMeter && stopMeter();
            URL.revokeObjectURL(url);
            this.currentAudio = null;
            if (gen === this.ttsGeneration) playChunk();
          };
          audio.onended = finish;
          audio.onerror = finish;
          await audio.play().catch(() => finish());
        } catch {
          if (gen === this.ttsGeneration) playChunk();
        }
      };
      playChunk();
    }

    /** Analyseur WebAudio : amplitude RÉELLE du flux Piper (niveau 0..1). */
    _attachLevelMeter(audio) {
      if (!window.AudioContext && !window.webkitAudioContext) return null;
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!this._meterCtx) this._meterCtx = new Ctx();
        const ctx = this._meterCtx;
        if (ctx.state === 'suspended') ctx.resume().catch(() => {});
        const src = ctx.createMediaElementSource(audio);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 128;
        src.connect(analyser);
        analyser.connect(ctx.destination);
        const buf = new Uint8Array(analyser.frequencyBinCount);
        let stopped = false;
        const tick = () => {
          if (stopped) return;
          analyser.getByteTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          const level = Math.min(1, Math.sqrt(sum / buf.length) * 2.2);
          window.App?.robotAudioLevel?.(level);
          // Publié sur le bus en plus de l'appel direct : l'avatar de
          // l'accueil n'est pas le robot de App, et plusieurs vues peuvent
          // vouloir réagir à la voix. L'appel ci-dessus reste, pour ne rien
          // casser de ce qui l'écoutait déjà.
          J.fire('tts.level', { level });
          this._levelRaf = requestAnimationFrame(tick);
        };
        tick();
        return () => {
          stopped = true;
          cancelAnimationFrame(this._levelRaf);
          try { src.disconnect(); analyser.disconnect(); } catch { /* ignore */ }
        };
      } catch {
        return null;
      }
    }

    _chunk(text) {
      const parts = text.match(/[^.!?…\n]+[.!?…]*\s*/g) || [text];
      const out = [];
      let buffer = '';
      for (const part of parts) {
        if ((buffer + part).length > 190) {
          if (buffer) out.push(buffer.trim());
          buffer = part;
        } else {
          buffer += part;
        }
      }
      if (buffer.trim()) out.push(buffer.trim());
      return out.filter(Boolean);
    }

    /** Fin naturelle de la parole : retour SILENCIEUX en veille. */
    _afterSpeaking() {
      this.currentUtterance = null;
      this.suppressUntil = Date.now() + 450;     // évite de capter la fin du son
      if (this.state === STATES.SPEAKING || this.state === STATES.INTERRUPTED) {
        this.setState(STATES.IDLE, 'fin de parole');
      }
      // On réactive le micro seulement si l'utilisateur écoute en continu.
      if (this.wantsListening) {
        setTimeout(() => this._startRecognition(), 500);
      }
      J.fire('voice.spoken', { text: this.lastSpokenText });
    }

    stopSpeaking(reason = 'stop') {
      this.ttsQueue = [];
      // Invalide toute lecture WAV distante en cours.
      this.ttsGeneration++;
      if (this.currentAudio) {
        try {
          this.currentAudio.onended = null;
          this.currentAudio.onerror = null;
          this.currentAudio.pause();
        } catch { /* ignore */ }
        if (this.currentAudio.src) URL.revokeObjectURL(this.currentAudio.src);
        this.currentAudio = null;
      }
      if (this.ttsAvailable) {
        try { window.speechSynthesis.cancel(); } catch { /* ignore */ }
      }
      this.ttsSpeaking = false;
      this.currentUtterance = null;
      if (this.state === STATES.SPEAKING) this.setState(STATES.INTERRUPTED, reason);
      this.setState(STATES.IDLE, reason);
      if (this.wantsListening) setTimeout(() => this._startRecognition(), 250);
    }

    _bargeInEnabled() {
      return this.settings?.interruptible_speech === true;
    }

    /* ------------------------------------------- reconnaissance serveur */
    /**
     * Repli serveur (faster-whisper) quand la Web Speech API manque.
     *
     * Firefox n'implémente pas `SpeechRecognition`, et celle de Chrome dépend
     * d'un service distant. Ici, le micro est enregistré localement puis
     * découpé sur le silence (endpointing par énergie), et chaque segment part
     * en transcription sur le serveur. Aucun texte n'est produit sans audio.
     */
    async _sttSync() {
      try {
        const res = await J.get('/api/stt/status');
        this.serverSttReady = !!(res && res.ok !== false && res.available);
        this.serverSttInfo = res || null;
      } catch {
        this.serverSttReady = false;
      }
      return this.serverSttReady;
    }

    _recorderSupported() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }

    /** True quand c'est le serveur, et non le navigateur, qui transcrit. */
    _useServerStt() {
      if (!this._recorderSupported() || !this.serverSttReady) return false;
      return this.settings?.stt_provider === 'local' || !this.available;
    }

    async _startServerRecognition() {
      if (this.recordingActive) return true;
      this.recordingActive = true;
      try {
        this.mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        });
      } catch (err) {
        this.recordingActive = false;
        this.wantsListening = false;
        J.fire('voice.error', { error: 'micro refusé' });
        toast('Micro refusé par le navigateur. Autorise-le pour parler à JARVIS.', 'err');
        return false;
      }
      J.fire('voice.mic', { active: true });
      this._runSegmentLoop();
      return true;
    }

    /** Boucle : un segment de parole → une transcription, tant qu'on écoute. */
    async _runSegmentLoop() {
      while (this.wantsListening && this.mediaStream) {
        let blob = null;
        try {
          blob = await this._recordSegment();
        } catch (err) {
          console.debug('[voice] enregistrement interrompu', err);
          break;
        }
        if (!this.wantsListening) break;
        if (blob && blob.size > 2000) {
          const text = await this._transcribeBlob(blob);
          if (text) this._handleTranscript(text);
        }
      }
      this._releaseStream();
    }

    /**
     * Enregistre jusqu'à la fin d'une prise de parole.
     * Résout avec le Blob audio, ou null si rien n'a été dit.
     */
    _recordSegment() {
      return new Promise((resolve, reject) => {
        const stream = this.mediaStream;
        if (!stream) { reject(new Error('micro fermé')); return; }
        let recorder;
        try {
          recorder = new MediaRecorder(stream, this._recorderOptions());
        } catch (err) { reject(err); return; }
        this.recorder = recorder;

        const chunks = [];
        recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
        recorder.onstop = () => {
          cleanup();
          resolve(spoke ? new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }) : null);
        };

        // Endpointing : on coupe après SILENCE_MS de silence, et jamais avant
        // d'avoir entendu au moins SPEECH_MS de parole — sinon chaque souffle
        // déclencherait un aller-retour serveur.
        const SPEECH_MS = 300;
        const SILENCE_MS = 1100;
        const MAX_MS = 15000;
        const THRESHOLD = 0.018;

        const audio = new (window.AudioContext || window.webkitAudioContext)();
        const analyser = audio.createAnalyser();
        analyser.fftSize = 1024;
        const source = audio.createMediaStreamSource(stream);
        source.connect(analyser);
        const frame = new Uint8Array(analyser.fftSize);

        let spoke = false;
        let speechMs = 0;
        let silenceMs = 0;
        const started = Date.now();
        const tick = 50;

        const timer = setInterval(() => {
          if (!this.wantsListening) { stop(); return; }
          analyser.getByteTimeDomainData(frame);
          let sum = 0;
          for (let i = 0; i < frame.length; i++) {
            const value = (frame[i] - 128) / 128;
            sum += value * value;
          }
          const rms = Math.sqrt(sum / frame.length);
          if (rms > THRESHOLD) {
            speechMs += tick;
            silenceMs = 0;
            if (!spoke && speechMs >= SPEECH_MS) {
              spoke = true;
              J.fire('voice.interim', { text: '…' });
              this._armSilenceTimer();
            }
          } else if (spoke) {
            silenceMs += tick;
            if (silenceMs >= SILENCE_MS) { stop(); return; }
          }
          if (Date.now() - started >= MAX_MS) stop();
        }, tick);

        const cleanup = () => {
          clearInterval(timer);
          try { source.disconnect(); } catch { /* ignore */ }
          try { audio.close(); } catch { /* ignore */ }
          this.recorder = null;
        };
        const stop = () => {
          if (recorder.state !== 'inactive') { try { recorder.stop(); return; } catch { /* ignore */ } }
          cleanup();
          resolve(null);
        };

        this._stopSegment = stop;
        try { recorder.start(200); } catch (err) { cleanup(); reject(err); }
      });
    }

    _recorderOptions() {
      for (const mimeType of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']) {
        if (window.MediaRecorder.isTypeSupported?.(mimeType)) return { mimeType };
      }
      return {};
    }

    async _transcribeBlob(blob) {
      const format = (blob.type || '').includes('ogg') ? 'ogg' : 'webm';
      let base64;
      try {
        base64 = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(reader.error);
          reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
          reader.readAsDataURL(blob);
        });
      } catch {
        return '';
      }
      if (!base64) return '';
      const language = (this.settings?.stt_language || 'fr-FR');
      const res = await J.post('/api/stt/transcribe', { audio: base64, format, language })
        .catch(() => null);
      if (!res || res.ok === false) {
        if (res && res.error) {
          J.fire('voice.error', { error: res.error });
          toast(res.error, 'err');
          this.wantsListening = false;     // inutile de réessayer en boucle
        }
        return '';
      }
      return String(res.text || '').trim();
    }

    _stopServerRecognition() {
      this.recordingActive = false;
      if (this._stopSegment) { try { this._stopSegment(); } catch { /* ignore */ } }
      this._releaseStream();
    }

    _releaseStream() {
      this.recordingActive = false;
      if (this.mediaStream) {
        for (const track of this.mediaStream.getTracks()) {
          try { track.stop(); } catch { /* ignore */ }
        }
        this.mediaStream = null;
        J.fire('voice.mic', { active: false });
      }
    }

    /* -------------------------------------------------------- contrôles */
    startListening() {
      const server = this._useServerStt();
      if (!this.available && !server) {
        toast(this._recorderSupported()
          ? 'Reconnaissance vocale indisponible : active la dictée locale (Réglages → Voix) '
            + 'et installe le moteur (pip install faster-whisper).'
          : 'La reconnaissance vocale n’est pas disponible dans ce navigateur.', 'err');
        return false;
      }
      this.wantsListening = true;
      this.manualStop = false;
      if (this.state === STATES.IDLE) this.setState(STATES.LISTENING, 'micro activé');
      if (server) this._startServerRecognition();
      else this._startRecognition();
      this._armSilenceTimer();
      return true;
    }

    stopListening() {
      this.wantsListening = false;
      this.conversationUntil = 0;
      this._stopRecognition({ manual: true });
      this._stopServerRecognition();
      clearTimeout(this.silenceTimer);
      if (this.state === STATES.LISTENING || this.state === STATES.WAKE) {
        this.setState(STATES.IDLE, 'micro coupé');
      }
    }

    toggleListening() {
      return this.wantsListening ? (this.stopListening(), false) : this.startListening();
    }

    /** Applique le mode d'écoute défini dans Settings → Voice. */
    _applyMode() {
      const mode = this.settings?.mode || 'wake_word';
      if (mode === 'always_listening' || mode === 'wake_word' || mode === 'conversation') {
        // L'écoute continue démarre uniquement sur geste utilisateur
        // (contrainte navigateur), d'où le bouton micro / la barre TALK.
        J.fire('voice.mode', { mode, autostart: false });
      } else {
        this.wantsListening = false;
      }
    }

    updateSettings(voiceSettings) {
      this.settings = voiceSettings;
      if (this.recognition) this.recognition.lang = voiceSettings.stt_language || 'fr-FR';
      this._applyMode();
      // Bascule sur Piper : rafraîchit l'état des voix locales.
      if (voiceSettings?.tts_provider === 'piper') this._piperSync();
      if (voiceSettings?.stt_provider === 'local' && !this.serverSttReady) this._sttSync();
    }

    /** Heartbeat : maintient la session vivante. N'a AUCUN effet vocal. */
    _startHeartbeat() {
      setInterval(() => {
        if (!this.session) return;
        J.post('/api/voice/heartbeat', { session_id: this.session.session_id }).catch(() => {});
      }, 60000);
    }

    label() { return LABELS[this.state] || this.state; }
    isBusy() { return [STATES.PROCESSING, STATES.EXECUTING].includes(this.state); }
  }

  window.VoiceManager = new AudioManager();
  window.VOICE_STATES = STATES;
  window.VOICE_LABELS = LABELS;
})();
