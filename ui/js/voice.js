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
    async submit(text, { silent = false, source = silent ? 'text' : 'voice' } = {}) {
      if (!text || !text.trim()) return null;
      // Nouvelle commande utilisateur : on arrête proprement la parole en cours.
      if (this.state === STATES.SPEAKING) this.stopSpeaking('nouvelle commande');
      this.setState(STATES.PROCESSING, 'commande');
      J.fire('jarvis.user', { text });

      const payload = { text, source };
      if (J.state.conversation) payload.conversation_id = J.state.conversation;
      console.debug('[CHAT-UI] API request started', payload.conversation_id);
      const res = await J.post('/api/command', payload);
      console.debug('[CHAT-UI] API response received', res.ok);

      if (res.conversation_id) J.state.conversation = res.conversation_id;
      const response = res.response || (res.ok ? 'Terminé.' : (res.error || 'Échec.'));
      J.fire('jarvis.reply', { text: response, result: res });

      if (res.needs_confirmation) {
        this.setState(STATES.SPEAKING, 'demande de confirmation');
        this.speak(response, { kind: 'confirmation' });
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
      return this.settings?.tts_provider === 'piper' && this.piperAvailable;
    }

    /** Récupère l'état des voix françaises Piper auprès du serveur. */
    async _piperSync() {
      try {
        const res = await J.get('/api/tts/voices');
        if (res.ok) {
          this.piperVoices = res.voices || [];
          this.piperAvailable = !!(res.engine?.available)
            && (this.piperVoices || []).some((v) => v.installed);
        } else {
          this.piperVoices = [];
          this.piperAvailable = false;
        }
      } catch {
        this.piperAvailable = false;
      }
    }

    _pickPiperVoice() {
      const wanted = this.settings?.voice;
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
          if (window.JarvisAvatar && typeof ev.charIndex === 'number') {
            window.JarvisAvatar.resyncSpeech(ev.charIndex, chunk.length);
          }
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

    /* -------------------------------------------------------- contrôles */
    startListening() {
      if (!this.available) {
        toast('La reconnaissance vocale n’est pas disponible dans ce navigateur.', 'err');
        return false;
      }
      this.wantsListening = true;
      this.manualStop = false;
      if (this.state === STATES.IDLE) this.setState(STATES.LISTENING, 'micro activé');
      this._startRecognition();
      this._armSilenceTimer();
      return true;
    }

    stopListening() {
      this.wantsListening = false;
      this.conversationUntil = 0;
      this._stopRecognition({ manual: true });
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
