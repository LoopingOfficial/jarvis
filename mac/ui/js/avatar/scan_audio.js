/* ==========================================================================
   JARVIS — AudioManager du module Humanoid

   Les sons du diagnostic sont SYNTHÉTISÉS, pas chargés. Raison : le dépôt ne
   contient aucun .wav de scan, et un module qui plante sur un 404 au premier
   « Check your system » ne sert à rien. La synthèse donne en plus un timing
   exact à l'échantillon — `ctx.currentTime` est la même horloge pour les
   quatre notes du carillon et pour le drone.

   Si un jour les .wav existent, `loadSound()` les enregistre et les lectures
   passent automatiquement sur l'échantillon :

       const audio = new ScanAudio();
       await audio.loadSound('chime', '../assets/sfx/check_chime.wav');  // option
       audio.playChimeSequence();

   Toutes les méthodes sont sans effet tant que le contexte est suspendu :
   les navigateurs l'exigent, et `unlock()` est appelé par la page au premier
   clic / à la première touche.
   ========================================================================== */

const NOOP_HANDLE = { stop() {} };

export class ScanAudio {
  constructor({ volume = 0.5 } = {}) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = Ctx ? new Ctx() : null;
    this.sounds = {};
    this.muted = !this.ctx;
    if (!this.ctx) return;

    // Bus maître : un seul gain à couper, et un compresseur pour que le drone
    // et le carillon ne s'additionnent pas en saturation.
    this.master = this.ctx.createGain();
    this.master.gain.value = volume;
    this.comp = this.ctx.createDynamicsCompressor();
    this.comp.threshold.value = -12;
    this.comp.ratio.value = 6;
    this.master.connect(this.comp).connect(this.ctx.destination);
  }

  /** À appeler depuis un vrai geste utilisateur (clic, touche). */
  unlock() {
    if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    return this;
  }

  setVolume(value) {
    if (this.master) this.master.gain.value = Math.max(0, Math.min(1, Number(value) || 0));
  }

  setMuted(flag) { this.muted = Boolean(flag) || !this.ctx; }

  /** Optionnel : un échantillon remplace la synthèse pour ce nom. */
  async loadSound(name, url) {
    if (!this.ctx) return null;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    const bytes = await response.arrayBuffer();
    this.sounds[name] = await this.ctx.decodeAudioData(bytes);
    return this.sounds[name];
  }

  /** Lecture d'un échantillon chargé. Renvoie false si absent. */
  playSound(name, { when = 0, gain = 1 } = {}) {
    if (!this.ctx || this.muted || !this.sounds[name]) return false;
    const src = this.ctx.createBufferSource();
    src.buffer = this.sounds[name];
    const g = this.ctx.createGain();
    g.gain.value = gain;
    src.connect(g).connect(this.master);
    src.start(this.ctx.currentTime + when);
    return { stop: () => { try { src.stop(); } catch (err) { /* déjà terminé */ } } };
  }

  /* --- Briques de synthèse ---------------------------------------------- */

  _blip(freq, at, duration, { type = 'sine', peak = 0.25, detune = 0 } = {}) {
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, at);
    osc.detune.value = detune;
    // Attaque quasi instantanée, décroissance exponentielle : c'est ce profil
    // qui fait « synthétique HUD » plutôt que « cloche ».
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, at + duration);
    osc.connect(g).connect(this.master);
    osc.start(at);
    osc.stop(at + duration + 0.05);
    return osc;
  }

  _noiseBuffer(seconds) {
    const n = Math.floor(this.ctx.sampleRate * seconds);
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < n; i += 1) data[i] = Math.random() * 2 - 1;
    return buf;
  }

  /* --- Les trois événements du diagnostic -------------------------------- */

  /** 1. Carillon ascendant : quatre notes, début de l'émission des ondes. */
  playChimeSequence() {
    if (!this.ctx || this.muted) return NOOP_HANDLE;
    const sample = this.playSound('chime');
    if (sample) return sample;
    const t0 = this.ctx.currentTime + 0.01;
    const notes = [880, 1174.66, 1567.98, 2093];   // La5 → Ré6 → Sol6 → Do7
    notes.forEach((freq, i) => {
      const at = t0 + i * 0.115;
      this._blip(freq, at, 0.42, { type: 'triangle', peak: 0.22 });
      // Une quinte très en retrait : la note seule sonne creuse.
      this._blip(freq * 1.5, at, 0.26, { type: 'sine', peak: 0.07 });
    });
    return NOOP_HANDLE;
  }

  /**
   * 2. Balayage + drone grave, maintenu pendant l'analyse.
   * @returns {{stop: Function}} handle — l'animation peut écourter le drone.
   */
  playDroneScan({ duration = 5.2 } = {}) {
    if (!this.ctx || this.muted) return NOOP_HANDLE;
    const sample = this.playSound('drone');
    if (sample) return sample;

    const t0 = this.ctx.currentTime + 0.01;
    const end = t0 + duration;
    const bus = this.ctx.createGain();
    bus.gain.setValueAtTime(0.0001, t0);
    bus.gain.exponentialRampToValueAtTime(0.5, t0 + 0.6);
    bus.gain.setValueAtTime(0.5, end - 0.8);
    bus.gain.exponentialRampToValueAtTime(0.0001, end);
    bus.connect(this.master);

    // Basse : deux dents de scie légèrement désaccordées derrière un passe-bas
    // qui s'ouvre — le battement entre elles fait la « respiration » du drone.
    const lp = this.ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(180, t0);
    lp.frequency.linearRampToValueAtTime(620, t0 + duration * 0.6);
    lp.frequency.linearRampToValueAtTime(260, end);
    lp.Q.value = 6;
    lp.connect(bus);

    const voices = [];
    for (const [freq, detune] of [[55, -6], [55, 7], [82.5, 3]]) {
      const osc = this.ctx.createOscillator();
      osc.type = 'sawtooth';
      osc.frequency.value = freq;
      osc.detune.value = detune;
      osc.connect(lp);
      osc.start(t0);
      osc.stop(end + 0.1);
      voices.push(osc);
    }

    // Swoosh : bruit blanc dans un passe-bande qui monte puis retombe.
    const noise = this.ctx.createBufferSource();
    noise.buffer = this._noiseBuffer(Math.max(1.4, duration));
    noise.loop = true;
    const bp = this.ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.Q.value = 3.5;
    bp.frequency.setValueAtTime(400, t0);
    bp.frequency.exponentialRampToValueAtTime(5200, t0 + 0.55);
    bp.frequency.exponentialRampToValueAtTime(700, t0 + 1.5);
    const noiseGain = this.ctx.createGain();
    noiseGain.gain.setValueAtTime(0.0001, t0);
    noiseGain.gain.exponentialRampToValueAtTime(0.16, t0 + 0.2);
    noiseGain.gain.exponentialRampToValueAtTime(0.02, t0 + 1.6);
    noiseGain.gain.setValueAtTime(0.02, end - 0.5);
    noiseGain.gain.exponentialRampToValueAtTime(0.0001, end);
    noise.connect(bp).connect(noiseGain).connect(bus);
    noise.start(t0);
    noise.stop(end + 0.1);
    voices.push(noise);

    return {
      stop: () => {
        const now = this.ctx.currentTime;
        bus.gain.cancelScheduledValues(now);
        bus.gain.setValueAtTime(Math.max(0.0001, bus.gain.value), now);
        bus.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        for (const v of voices) {
          try { v.stop(now + 0.4); } catch (err) { /* déjà terminé */ }
        }
      },
    };
  }

  /** 3. Tonalité de validation : « All clear ». */
  playValidationTone() {
    if (!this.ctx || this.muted) return NOOP_HANDLE;
    const sample = this.playSound('validation');
    if (sample) return sample;
    const t0 = this.ctx.currentTime + 0.01;
    this._blip(1046.5, t0, 0.5, { type: 'sine', peak: 0.26 });          // Do6
    this._blip(1567.98, t0 + 0.09, 0.9, { type: 'sine', peak: 0.2 });   // quinte
    this._blip(2093, t0 + 0.09, 0.7, { type: 'triangle', peak: 0.08 });
    return NOOP_HANDLE;
  }

  dispose() {
    try { this.ctx?.close(); } catch (err) { /* déjà fermé */ }
    this.ctx = null;
    this.sounds = {};
  }
}

export default ScanAudio;
