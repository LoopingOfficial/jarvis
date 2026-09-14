/* ==========================================================================
   JarvisAvatar3D — le composant public de l'avatar.

   `JarvisAvatar` (avatar.js) est le moteur : scène, rig, IK, locomotion.
   `JarvisAvatar3D` est le CONTRAT : une surface d'API petite et stable que
   l'UI et le bus d'événements peuvent appeler sans rien savoir de Three.js.

   Trois garanties :
     1. chargement asynchrone — `mount()` rend la main immédiatement ;
     2. dégradation propre — sans WebGL, le composant existe et ne jette pas,
        chaque appel devient un no-op et le fallback DOM est affiché ;
     3. les états inconnus retombent sur IDLE plutôt que de casser la scène.

   API :
     await a.mount()          a.setState('speaking')   a.setEmotion('pleased')
     a.setAudioLevel(0.4)     a.lookAt('camera')       a.reset()
     a.destroy()
   ========================================================================== */
import { JarvisAvatar, AVATAR_STATES } from './avatar.js';
import { registerFramings, JARVIS_FRAMINGS, checkSafeFrame } from './framing.js';

// Les cadrages HOME/CHAT/VOICE sont ajoutes a la table que `CameraDirector`
// consulte deja : aucune duplication de la logique de transition.
registerFramings();

/** Un état public minuscule/majuscule tombe toujours sur un état réel. */
function normalizeState(state) {
  const key = String(state || '').toUpperCase();
  return AVATAR_STATES.includes(key) ? key : 'IDLE';
}

export class JarvisAvatar3D {
  /**
   * @param {HTMLCanvasElement} canvas  cible de rendu
   * @param {object} options            quality, modelUrl, viewMode, fallbackEl
   */
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = options;
    this.fallbackEl = options.fallbackEl || null;
    this.engine = null;
    this.available = false;
    this.error = null;
    this._pending = null;
    this._triedFallback = false;
  }

  /** True si WebGL est réellement utilisable ici (et pas seulement déclaré). */
  static webglSupported() {
    try {
      const c = document.createElement('canvas');
      return !!(window.WebGL2RenderingContext && c.getContext('webgl2'))
        || !!(window.WebGLRenderingContext
          && (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch {
      return false;
    }
  }

  /**
   * Instancie le moteur et résout quand l'avatar est réellement affichable.
   * Ne rejette jamais : un échec bascule sur le fallback et laisse l'UI vivre.
   */
  mount() {
    if (this._pending) return this._pending;
    this._pending = new Promise((resolve) => {
      if (!this.canvas || !JarvisAvatar3D.webglSupported()) {
        this.error = new Error('WebGL indisponible');
        this._showFallback(true);
        resolve(false);
        return;
      }
      try {
        this.engine = new JarvisAvatar({
          canvas: this.canvas,
          quality: this.options.quality,
          modelUrl: this.options.modelUrl,
          viewMode: this.options.viewMode,
          onReady: (engine) => {
            this.available = true;
            this._showFallback(false);
            if (this.options.onReady) this.options.onReady(engine);
            resolve(true);
          },
          onError: (err) => {
            // Repli : une variante allegee qui echoue ne doit jamais laisser
            // un avatar vide. On retente UNE fois sur le modele de secours,
            // puis seulement on bascule sur le fallback DOM.
            if (this.options.fallbackUrl && !this._triedFallback) {
              this._triedFallback = true;
              console.warn('[avatar] modele indisponible, repli sur',
                this.options.fallbackUrl, err);
              this.engine?.reload(this.options.fallbackUrl)
                .then(() => {
                  this.available = true;
                  this._showFallback(false);
                  resolve(true);
                })
                .catch((err2) => {
                  this.error = err2;
                  this._showFallback(true);
                  resolve(false);
                });
              return;
            }
            this.error = err;
            this._showFallback(true);
            if (this.options.onError) this.options.onError(err);
            resolve(false);
          },
        });
      } catch (err) {
        this.error = err;
        this._showFallback(true);
        resolve(false);
      }
    });
    return this._pending;
  }

  _showFallback(show) {
    if (!this.fallbackEl) return;
    this.fallbackEl.hidden = !show;
  }

  /* ------------------------------------------------------------------ API */

  /** IDLE | LISTENING | THINKING | SPEAKING | SUCCESS | WARNING | ERROR … */
  setState(state, extra = {}) {
    if (!this.available) return false;
    this.engine.setState(normalizeState(state), extra);
    return true;
  }

  get state() { return this.engine?.state || 'IDLE'; }

  setEmotion(emotion, options = {}) {
    return this.available ? this.engine.setEmotion(emotion, options) : false;
  }

  /** Amplitude audio 0→1 : pilote jaw/mouthOpen quand aucun phonème n'existe. */
  setAudioLevel(level) {
    if (!this.available) return false;
    const v = Number(level);
    this.engine.setAudioLevel(Number.isFinite(v) ? Math.min(Math.max(v, 0), 1) : 0);
    return true;
  }

  /** Cible nommée de la scène ('camera', 'brain', 'desk') ou Vector3. */
  lookAt(target) {
    if (!this.available) return false;
    this.engine.lookAt(target);
    return true;
  }

  speak(text, options = {}) {
    return this.available ? (this.engine.speak(text, options), true) : false;
  }

  stopSpeaking(options = {}) {
    return this.available ? (this.engine.stopSpeaking(options), true) : false;
  }

  reset() {
    return this.available ? this.engine.reset() : false;
  }

  setQuality(q) { if (this.available) this.engine.setQuality(q); }

  setViewMode(mode) { if (this.available) this.engine.setViewMode(mode); }

  /** HOME | CHAT | VOICE — cadrages JARVIS, transitions gerees par le director. */
  setFraming(name, options = {}) {
    const key = String(name || '').toUpperCase();
    if (!JARVIS_FRAMINGS[key] || !this.available) return false;
    return this.engine.setCameraMode(key, options);
  }

  /** Marges reelles du cadrage courant — sert a verifier qu'on ne rogne rien. */
  safeFrame(name = 'HOME') {
    const key = String(name || '').toUpperCase();
    const spec = JARVIS_FRAMINGS[key];
    if (!spec) return null;
    const canvas = this.canvas;
    const aspect = canvas && canvas.clientHeight
      ? canvas.clientWidth / canvas.clientHeight : 1;
    return { framing: key, note: spec.note, ...checkSafeFrame(spec, aspect) };
  }

  /**
   * Sonde de performance REELLE, a lancer dans l'application visible.
   *
   * Elle ne peut rien mesurer dans un onglet masque : le rendu y est suspendu
   * et `requestAnimationFrame` bride. Elle renvoie donc `null` plutot qu'un
   * chiffre faux, et indique pourquoi.
   *
   *   await JarvisAvatar3DInstance.profile(3000, 'SPEAKING')
   */
  async profile(durationMs = 3000, state = null) {
    if (!this.available) return { error: 'avatar indisponible' };
    if (!this.engine.rendering) {
      return { error: 'rendu suspendu (onglet masque ou avatar hors ecran) : '
        + 'aucune mesure fiable possible' };
    }
    if (state) this.setState(state);
    const frames = [];
    let last = performance.now();
    const started = last;
    // Garde-fou : si l'onglet passe en arriere-plan pendant la mesure,
    // `requestAnimationFrame` cesse d'etre appele et la promesse ne se
    // resoudrait JAMAIS. On borne donc l'attente par un minuteur.
    await new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      const tick = () => {
        const now = performance.now();
        frames.push(now - last);
        last = now;
        if (now - started >= durationMs) finish();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      setTimeout(finish, durationMs * 2 + 500);
    });
    const times = frames.slice(1);                 // la 1re mesure est biaisee
    if (times.length < 5) {
      return { error: 'frames insuffisantes (' + times.length + ') : '
        + 'requestAnimationFrame bride, fenetre au premier plan requise' };
    }
    const sorted = [...times].sort((a, b) => a - b);
    const mean = times.reduce((a, b) => a + b, 0) / times.length;
    const info = this.engine.renderer.info;
    const mem = performance.memory
      ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null;
    return {
      state: this.state,
      frames: times.length,
      fpsAvg: +(1000 / mean).toFixed(1),
      fpsMin: +(1000 / sorted[sorted.length - 1]).toFixed(1),
      frameMsAvg: +mean.toFixed(2),
      frameMsP95: +sorted[Math.floor(sorted.length * 0.95)].toFixed(2),
      frameMsMax: +sorted[sorted.length - 1].toFixed(2),
      drawCalls: info.render.calls,
      triangles: info.render.triangles,
      programs: info.programs ? info.programs.length : null,
      geometries: info.memory.geometries,
      textures: info.memory.textures,
      jsHeapMB: mem,
      quality: this.engine.quality,
    };
  }

  /** Diagnostic : ce que les tests et le HUD de perf lisent. */
  stats() {
    return {
      available: this.available,
      state: this.state,
      emotion: this.engine?.emotion || 'neutral',
      fps: this.engine?.fps || 0,
      quality: this.engine?.quality || null,
      rendering: this.engine?.rendering ?? false,
      contextLost: this.engine?.contextLost ?? false,
      error: this.error ? String(this.error.message || this.error) : null,
    };
  }

  destroy() {
    this.engine?.dispose();
    this.engine = null;
    this.available = false;
    this._pending = null;
  }
}

window.JarvisAvatar3D = JarvisAvatar3D;
export default JarvisAvatar3D;
