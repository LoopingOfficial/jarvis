/* ==========================================================================
   JarvisAnimationStateController — une seule table etat -> animation.

   Ce que ce fichier NE fait pas
   -----------------------------
   Il ne remplace ni `LocomotionController` ni `GesturePlanner` : ceux-ci
   fonctionnent et restent la seule autorite sur le mixer. Cette couche se
   contente de rendre EXPLICITE et testable la correspondance entre les sept
   etats publics de JARVIS et ce qui doit jouer.

   Ce qu'il faut savoir sur le mixer existant
   -----------------------------------------
   `LocomotionController` cree une action pour CHACUN des clips non additifs
   et `GesturePlanner` pour chacun des additifs : 41 actions sont instanciees
   en permanence. Instancier n'est pas jouer, et `getEffectiveWeight()` renvoie
   le poids par defaut (1) meme pour une action JAMAIS demarree : compter les
   actions « a poids 1 » donnait donc 41, chiffre faux. La seule mesure juste
   est `action.isRunning() && action.getEffectiveWeight() > 0`.

   Mesure faite, il restait tout de meme 3 actions reellement evaluees en
   permanence au lieu d'une : deux idles superposes et un geste additif
   termine. C'est un vrai defaut, corrige par `_reap()` plus bas.
   ========================================================================== */

/** Etat public -> (clip de base, recouvrement additif occasionnel). */
export const STATE_ANIMATION = {
  IDLE: { base: 'idle_neutral', fade: 0.45, overlay: null },
  LISTENING: { base: 'idle_attentive', fade: 0.30, overlay: 'gesture_acknowledge',
    overlayChance: 0.35, overlayEvery: 5.5, intensity: 0.25 },
  THINKING: { base: 'idle_neutral', fade: 0.35, overlay: 'gesture_thinking',
    overlayChance: 0.5, overlayEvery: 6.0, intensity: 0.4 },
  SPEAKING: { base: 'idle_attentive', fade: 0.25, overlay: 'gesture_explain',
    overlayChance: 0.55, overlayEvery: 3.4, intensity: 0.45,
    // Repeter le meme geste en parlant se remarque tres vite.
    overlayPool: ['gesture_explain', 'gesture_open_hand', 'gesture_small_point',
      'gesture_reassure', 'gesture_neutral'] },
  SUCCESS: { base: 'idle_attentive', fade: 0.30, overlay: 'gesture_success',
    once: true, intensity: 0.75 },
  WARNING: { base: 'idle_neutral', fade: 0.35, overlay: 'gesture_concern',
    once: true, intensity: 0.6 },
  ERROR: { base: 'idle_neutral', fade: 0.40, overlay: 'gesture_concern',
    once: true, intensity: 0.7 },
};

// Etats internes plus fins de l'avatar, ramenes a l'un des sept publics.
const ALIASES = {
  UNDERSTANDING: 'LISTENING', RECALLING: 'THINKING', ACTING: 'THINKING',
  CODING: 'THINKING', BROWSING: 'THINKING', DEPLOYING: 'THINKING',
  SLEEPING: 'IDLE', WALKING: 'IDLE',
};

export class JarvisAnimationStateController {
  /**
   * @param {object} model       AvatarModel (clips + metadonnees)
   * @param {object} locomotion  LocomotionController (couche de base)
   * @param {object} gestures    GesturePlanner (couche additive)
   */
  constructor(model, locomotion, gestures, mixer) {
    this.model = model;
    this.locomotion = locomotion;
    this.gestures = gestures;
    this.mixer = mixer;
    this.state = 'IDLE';
    this._timer = 0;
    this._reapTimer = 0.5;
    this._retiring = new Map();     // action -> secondes restantes avant stop
    this.lastBase = null;
  }

  /**
   * Relance une base en forcant le passage dans `_fade()`.
   *
   * `LocomotionController._fade()` sort immediatement si l'action demandee est
   * deja `this.base` — y compris lorsque cette action a ete ARRETEE entre
   * temps. Sans remise a zero de la reference, l'avatar restait sans aucune
   * animation de base apres un cycle d'etats.
   */
  _forceBase(name) {
    if (!this.locomotion) return;
    this.locomotion.base = null;
    this.locomotion.playIdle(this.model.clips.has(name) ? name : undefined);
    this.lastBase = name;
  }

  static normalize(state) {
    const key = String(state || '').toUpperCase();
    if (STATE_ANIMATION[key]) return key;
    return ALIASES[key] || 'IDLE';
  }

  /** Transition d'etat : crossfade de la base, geste marquant si `once`. */
  setState(state) {
    const key = JarvisAnimationStateController.normalize(state);
    const profile = STATE_ANIMATION[key];
    this.state = key;

    // La marche est un FAIT, pas une attitude : on ne lui vole pas sa base.
    if (this.locomotion && this.locomotion.state === 'idle') {
      const base = this.model.clips.has(profile.base) ? profile.base : null;
      // On relance aussi quand l'action de base a ete ARRETEE entre-temps :
      // comparer au seul `lastBase` laissait l'avatar sans aucune base apres
      // un cycle d'etats, donc fige sur sa pose de repos.
      const stale = !this.locomotion.base || !this.locomotion.base.isRunning();
      if (base && (base !== this.lastBase || stale)) {
        if (stale) this._forceBase(base);
        else {
          this.locomotion.playIdle(base);
          this.lastBase = base;
        }
      }
    }

    if (profile.once && profile.overlay && this.gestures) {
      this.gestures.play(profile.overlay, profile.intensity ?? 0.6);
      this._timer = Number.POSITIVE_INFINITY;   // pas de repetition
    } else {
      this._timer = profile.overlayEvery || Number.POSITIVE_INFINITY;
    }
    return key;
  }

  /**
   * Arrete les actions devenues inutiles.
   *
   * `LocomotionController._fade()` enchaine `previous.fadeOut()` puis, sur la
   * NOUVELLE action, `reset().setEffectiveWeight(1).fadeIn()`. Or `reset()`
   * annule un fondu en cours : quand `cycleIdle()` relance une base pendant
   * qu'un crossfade est en vol, l'ancienne action reste demarree A POIDS 1
   * pour toujours. Deux idles se superposaient donc en permanence, et un geste
   * additif termine restait a son poids. three.js ne stoppe jamais une action
   * tout seul : il faut le faire explicitement.
   */
  _reap(dt) {
    if (!this.mixer) return;
    for (const [action, left] of [...this._retiring]) {
      const remaining = left - dt;
      if (remaining <= 0) {
        action.stop();
        this._retiring.delete(action);
      } else {
        this._retiring.set(action, remaining);
      }
    }
    this._reapTimer -= dt;
    if (this._reapTimer > 0) return;
    this._reapTimer = 0.4;

    const currentBase = this.locomotion ? this.locomotion.base : null;
    const playingGesture = this.gestures && this.gestures.playing
      ? this.gestures.actions.get(this.gestures.playing) : null;

    for (const action of this.mixer._actions || []) {
      if (!action.isRunning() || this._retiring.has(action)) continue;
      if (action.getEffectiveWeight() <= 0.0001) {
        action.stop();
        continue;
      }
      const isCurrent = action === currentBase || action === playingGesture;
      if (isCurrent) continue;
      action.fadeOut(0.30);
      this._retiring.set(action, 0.35);
    }

    // Filet de securite : il doit TOUJOURS rester une base qui tourne.
    const hasBase = (this.mixer._actions || []).some(
      (a) => a.isRunning() && a.getEffectiveWeight() > 0.0001
        && !this._retiring.has(a) && a.blendMode !== 2501);
    if (!hasBase && this.locomotion) {
      this._forceBase(STATE_ANIMATION[this.state].base);
    }
  }

  /** Recouvrements occasionnels — jamais en boucle, jamais deux a la fois. */
  update(dt) {
    this._reap(dt);
    const profile = STATE_ANIMATION[this.state];
    if (!profile || !profile.overlay || !Number.isFinite(this._timer)) return;
    this._timer -= dt;
    if (this._timer > 0) return;
    this._timer = profile.overlayEvery * (0.7 + Math.random() * 0.9);
    if (Math.random() > (profile.overlayChance ?? 0.4)) return;
    const pool = profile.overlayPool || [profile.overlay];
    const pick = pool[Math.floor(Math.random() * pool.length)];
    if (this.gestures) this.gestures.play(pick, profile.intensity ?? 0.5);
  }

  /**
   * Etat reel du mixer. `isRunning()` est indispensable : sans lui on compte
   * les 41 actions instanciees au lieu des 1 a 2 reellement evaluees.
   */
  inspect(mixer) {
    const active = [];
    for (const action of mixer._actions || []) {
      if (!action.isRunning()) continue;
      const weight = action.getEffectiveWeight();
      if (weight <= 0.0001) continue;
      active.push({
        clip: action.getClip().name,
        weight: Math.round(weight * 1000) / 1000,
        additive: action.blendMode !== undefined
          && action.blendMode !== 2500,        // NormalAnimationBlendMode
      });
    }
    return {
      state: this.state,
      created: (mixer._actions || []).length,
      active,
      activeCount: active.length,
    };
  }
}

export default JarvisAnimationStateController;
