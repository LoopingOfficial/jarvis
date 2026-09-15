/* ==========================================================================
   JARVIS Avatar — couche « vivant ».

   Quatre contrôleurs qui se superposent sans jamais se contredire :

     HumanBehaviorController — clignement, saccades, respiration, transfert
                               de poids, micro-mouvements des doigts, variation
                               de posture. Hasard contrôlé : aucune boucle
                               perceptible.
     GazeController          — où regardent les yeux et la tête, avec latence
                               humaine (les yeux partent avant la tête).
     LipSyncController       — visèmes réels dérivés du texte prononcé, avec
                               repli sur l'amplitude audio.
     GesturePlanner          — traduit une intention (emotion/gesture/intensity)
                               en clip additif sûr. Le LLM ne touche jamais un os.

   Toutes les valeurs sont des cibles amorties : rien ne « saute ».
   ========================================================================== */
import * as THREE from '../../vendor/three.module.js';
import { smoothNoise } from './core.js';

const clamp = THREE.MathUtils.clamp;
const lerp = THREE.MathUtils.lerp;

/* ====================================================================== */
/* Comportement humain permanent                                          */
/* ====================================================================== */
export class HumanBehaviorController {
  constructor(model, rig) {
    this.model = model;
    this.rig = rig;
    this.enabled = true;
    this.time = 0;
    this.seed = Math.random() * 1000;

    // --- clignement ---
    this.blink = 0;
    this._blinkTimer = 1.5 + Math.random() * 2.5;
    this._blinkPhase = 0;
    this._doubleBlink = false;

    // --- respiration ---
    this.breathRate = 0.24;          // Hz — repos
    this.breathDepth = 1.0;
    this._breathPhase = Math.random() * Math.PI * 2;

    // --- posture ---
    this.postureTimer = 20 + Math.random() * 25;
    this.onPostureChange = null;
    this.energy = 0.5;               // 0 = très calme, 1 = engagé

    // --- expression ---
    this.expression = { smile: 0.12, browUp: 0, browDown: 0, squint: 0 };
    this._expressionTarget = { ...this.expression };

    this.fingerBones = [];
    for (const chain of Object.values(model.meta.fingerChains || {})) {
      chain.forEach((name, index) => {
        const bone = model.bone(name);
        if (bone) this.fingerBones.push({ bone, index, seed: Math.random() * 100 });
      });
    }

    this.spine = ['spine_01', 'spine_02', 'chest'].map((n) => model.bone(n)).filter(Boolean);
    this.pelvis = model.bone('pelvis');
    this.chest = model.bone('chest');
  }

  setEnergy(value) { this.energy = clamp(value, 0, 1); }

  /** États internes → cibles d'expression. Jamais de caricature. */
  setMood(mood) {
    const table = {
      neutral: { smile: 0.12, browUp: 0.0, browDown: 0.0, squint: 0.0 },
      friendly: { smile: 0.38, browUp: 0.12, browDown: 0.0, squint: 0.06 },
      attentive: { smile: 0.16, browUp: 0.18, browDown: 0.0, squint: 0.0 },
      thinking: { smile: 0.04, browUp: 0.0, browDown: 0.22, squint: 0.18 },
      concerned: { smile: 0.0, browUp: 0.30, browDown: 0.0, squint: 0.10 },
      pleased: { smile: 0.55, browUp: 0.20, browDown: 0.0, squint: 0.14 },
      focused: { smile: 0.06, browUp: 0.0, browDown: 0.16, squint: 0.12 },
    };
    this._expressionTarget = { ...(table[mood] || table.neutral) };
  }

  triggerBlink(double = false) {
    this._blinkPhase = 0.0001;
    this._doubleBlink = double;
  }

  update(dt) {
    if (!this.enabled) return;
    this.time += dt;
    this._updateBlink(dt);
    this._updateExpression(dt);
    this._updateBreathing(dt);
    this._updateWeight(dt);
    this._updateFingers(dt);
    this._updatePosture(dt);
  }

  /* ------------------------------------------------------------ clignement */
  _updateBlink(dt) {
    if (this._blinkPhase > 0) {
      // 90 ms de fermeture, 110 ms d'ouverture : c'est la vraie dynamique.
      this._blinkPhase += dt;
      const close = 0.09;
      const open = 0.11;
      if (this._blinkPhase < close) {
        this.blink = this._blinkPhase / close;
      } else if (this._blinkPhase < close + open) {
        this.blink = 1 - (this._blinkPhase - close) / open;
      } else {
        this.blink = 0;
        this._blinkPhase = 0;
        if (this._doubleBlink) {
          this._doubleBlink = false;
          this._blinkTimer = 0.09;
        }
      }
    } else {
      this._blinkTimer -= dt;
      if (this._blinkTimer <= 0) {
        // Plus on est attentif, plus on cligne souvent.
        const base = lerp(5.2, 3.0, this.energy);
        this._blinkTimer = base * (0.55 + Math.random() * 0.9);
        this.triggerBlink(Math.random() < 0.18);
      }
    }
    const value = clamp(this.blink, 0, 1);
    this.model.setMorph('blinkLeft', value);
    this.model.setMorph('blinkRight', value);
  }

  /* ------------------------------------------------------------ expression */
  _updateExpression(dt) {
    const k = 1 - Math.exp(-4.5 * dt);
    for (const key of Object.keys(this.expression)) {
      this.expression[key] += (this._expressionTarget[key] - this.expression[key]) * k;
    }
    // Micro-asymétrie : un visage parfaitement symétrique paraît artificiel.
    const asym = smoothNoise(this.time * 0.23, this.seed) * 0.05;
    const smile = this.expression.smile;
    this.model.setMorph('smileLeft', clamp(smile + asym, 0, 1));
    this.model.setMorph('smileRight', clamp(smile - asym, 0, 1));
    const brow = this.expression.browUp
      + smoothNoise(this.time * 0.31, this.seed + 5) * 0.05 * this.energy;
    this.model.setMorph('browUp', clamp(brow, 0, 1));
    this.model.setMorph('browDown', clamp(this.expression.browDown, 0, 1));
    const squint = this.expression.squint;
    this.model.setMorph('squintLeft', clamp(squint + asym * 0.5, 0, 1));
    this.model.setMorph('squintRight', clamp(squint - asym * 0.5, 0, 1));
  }

  /* ----------------------------------------------------------- respiration */
  _updateBreathing(dt) {
    const rate = this.breathRate * lerp(0.85, 1.35, this.energy);
    this._breathPhase += dt * rate * Math.PI * 2;
    // Inspiration plus courte que l'expiration : sinus « penché ».
    const raw = Math.sin(this._breathPhase);
    const shaped = Math.sign(raw) * Math.pow(Math.abs(raw), 0.8);
    const amount = shaped * this.breathDepth;

    if (this.chest) {
      this.chest.rotation.x += amount * 0.012;
      this.chest.scale.setScalar(1 + amount * 0.006);
    }
    if (this.spine[0]) this.spine[0].rotation.x += amount * 0.004;
    if (this.spine[1]) this.spine[1].rotation.x += amount * 0.006;
  }

  /* -------------------------------------------------- transfert de poids */
  _updateWeight(dt) {
    if (!this.pelvis) return;
    const slow = smoothNoise(this.time * 0.055, this.seed + 11);
    const slower = smoothNoise(this.time * 0.031, this.seed + 23);
    this.pelvis.rotation.y += slow * 0.022;
    this.pelvis.rotation.z += slower * 0.014;
    if (this.spine[1]) {
      // Contre-rotation du buste : le corps compense toujours le bassin.
      this.spine[1].rotation.y -= slow * 0.012;
    }
  }

  /* ---------------------------------------------------------------- doigts */
  _updateFingers(dt) {
    // Les doigts d'un humain au repos ne sont ni figés ni agités.
    const amplitude = lerp(0.010, 0.026, this.energy);
    for (const { bone, index, seed } of this.fingerBones) {
      const n = smoothNoise(this.time * 0.42 + seed, seed);
      bone.rotation.x += n * amplitude * (index === 0 ? 0.7 : 1);
    }
  }

  /* --------------------------------------------------------------- posture */
  _updatePosture(dt) {
    this.postureTimer -= dt;
    if (this.postureTimer > 0) return;
    this.postureTimer = 22 + Math.random() * 38;
    if (this.onPostureChange) this.onPostureChange();
  }
}

/* ====================================================================== */
/* Regard                                                                 */
/* ====================================================================== */
export class GazeController {
  constructor(model, rig) {
    this.model = model;
    this.rig = rig;
    this.eyes = [model.bone('eye_L'), model.bone('eye_R')].filter(Boolean);
    this.head = model.bone('head');
    this.neck = model.bone('neck');
    this.chest = model.bone('chest');

    this.target = new THREE.Vector3(0, 1.6, 3);
    this._eyeTarget = new THREE.Vector3().copy(this.target);
    this._headTarget = new THREE.Vector3().copy(this.target);
    this._saccadeTimer = 0.8;
    this._saccade = new THREE.Vector3();
    this.headWeight = 0.55;         // part du regard prise en charge par la tête
    this.bodyWeight = 0.12;
    this.eyeWeight = 1.0;
    this.time = 0;
    this.seed = Math.random() * 100;
    this.attention = 0.6;           // 0 = ailleurs, 1 = fixé sur la cible
  }

  lookAt(vector) { this.target.copy(vector); }

  /** Regard « ailleurs » : réflexion, rappel mémoire, hésitation. */
  driftTo(vector, attention = 0.25) {
    this.target.copy(vector);
    this.attention = attention;
  }

  update(dt) {
    this.time += dt;

    // --- saccades : de petits sauts, pas une dérive continue ---
    this._saccadeTimer -= dt;
    if (this._saccadeTimer <= 0) {
      this._saccadeTimer = lerp(1.6, 0.35, this.attention) * (0.5 + Math.random());
      const spread = lerp(0.16, 0.035, this.attention);
      this._saccade.set(
        (Math.random() - 0.5) * spread,
        (Math.random() - 0.5) * spread * 0.6,
        0,
      );
    }

    // Les yeux atteignent la cible bien avant la tête : c'est ce décalage
    // qui rend un regard vivant plutôt que mécanique.
    const wander = new THREE.Vector3(
      smoothNoise(this.time * 0.7, this.seed) * 0.02,
      smoothNoise(this.time * 0.55, this.seed + 3) * 0.014,
      0,
    );
    const desired = this.target.clone().add(this._saccade).add(wander);
    this._eyeTarget.lerp(desired, 1 - Math.exp(-16 * dt));
    this._headTarget.lerp(this.target, 1 - Math.exp(-3.2 * dt));

    for (const eye of this.eyes) {
      this.rig.aimAt(eye, this._eyeTarget, {
        localAxis: new THREE.Vector3(0, 1, 0),
        maxAngle: 0.42,                       // limite physiologique
        weight: this.eyeWeight,
      });
    }
    if (this.head) {
      this.rig.aimAt(this.head, this._headTarget, {
        localAxis: new THREE.Vector3(0, 1, 0),
        maxAngle: 0.62,
        weight: this.headWeight,
      });
    }
    if (this.neck) {
      this.rig.aimAt(this.neck, this._headTarget, {
        localAxis: new THREE.Vector3(0, 1, 0),
        maxAngle: 0.3,
        weight: this.headWeight * 0.35,
      });
    }
  }
}

/* ====================================================================== */
/* Lip sync                                                               */
/* ====================================================================== */

// Règles graphème → visème pour le français. Volontairement compactes :
// l'objectif est une bouche crédible, pas une transcription phonétique.
const FR_RULES = [
  [/^(ou|où|oû|w)/i, 'WQ', 1],
  [/^(oi|oy)/i, 'WQ', 1],
  [/^(au|eau|ô|o)/i, 'O', 1],
  [/^(on|om)(?![aeiouy])/i, 'O', 1],
  [/^(an|am|en|em)(?![aeiouy])/i, 'A', 1],
  [/^(in|im|ain|ein|un|um)(?![aeiouy])/i, 'E', 1],
  [/^(eu|œu|œ)/i, 'U', 1],
  [/^(u|û)/i, 'U', 1],
  [/^(ch|sh)/i, 'CH', 1],
  [/^(j|g(?=[eiy]))/i, 'CH', 1],
  [/^(qu|q|k|c(?=[aouâôû])|c$|g)/i, 'CH', 1],
  [/^(th)/i, 'TH', 1],
  [/^(ph|f|v)/i, 'FV', 1],
  [/^(m|b|p)/i, 'MBP', 1],
  [/^(l)/i, 'L', 1],
  [/^(é|è|ê|ai|ei|e)/i, 'E', 1],
  [/^(i|y|î)/i, 'I', 1],
  [/^(a|à|â)/i, 'A', 1],
  [/^(r|d|t|n|s|z|x|h|c)/i, 'CH', 0.55],
];

export function textToVisemes(text) {
  const out = [];
  let rest = String(text || '')
    .replace(/[^\p{L}\s'’-]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  let guard = 0;
  while (rest.length && guard++ < 4000) {
    if (/^[\s'’-]/.test(rest)) {
      out.push({ viseme: 'REST', weight: 0.5 });
      rest = rest.slice(1);
      continue;
    }
    let matched = false;
    for (const [re, viseme, weight] of FR_RULES) {
      const m = rest.match(re);
      if (m && m[0].length) {
        out.push({ viseme, weight });
        rest = rest.slice(m[0].length);
        matched = true;
        break;
      }
    }
    if (!matched) rest = rest.slice(1);
  }
  return out;
}

export class LipSyncController {
  constructor(model) {
    this.model = model;
    this.available = new Set(model.meta.visemes || []);
    this.current = 'viseme_REST';
    this.weights = new Map();
    this.timeline = null;
    this.startedAt = 0;
    this.duration = 0;
    this.amplitude = 0;
    this.active = false;
    this.jaw = 0;
    this._decay = 0;
  }

  /** Prononciation d'un texte : timeline de visèmes calée sur la durée réelle. */
  speak(text, { duration = 0, rate = 1 } = {}) {
    const visemes = textToVisemes(text);
    if (!visemes.length) return;
    // Durée estimée si le moteur TTS ne la donne pas (≈ 13 visèmes / seconde).
    const estimated = visemes.length / (13 * rate);
    this.duration = duration > 0.2 ? duration : estimated;
    this.timeline = visemes;
    this.startedAt = performance.now() / 1000;
    this.active = true;
  }

  /** Recalage sur un événement réel du moteur TTS (frontière de mot). */
  resync(charIndex, totalChars) {
    if (!this.active || !this.timeline || !totalChars) return;
    const ratio = clamp(charIndex / totalChars, 0, 1);
    const expected = this.startedAt + ratio * this.duration;
    const now = performance.now() / 1000;
    // Correction douce : un saut brutal se verrait sur la bouche.
    this.startedAt += (now - expected) * 0.4;
  }

  stop({ fade = 0.18 } = {}) {
    this.active = false;
    this.timeline = null;
    this._decay = fade;
  }

  /** Repli : sans timeline, la bouche suit l'énergie du signal audio. */
  setAmplitude(level) {
    this.amplitude = clamp(level, 0, 1);
  }

  update(dt) {
    const targets = new Map();
    if (this.active && this.timeline) {
      const t = performance.now() / 1000 - this.startedAt;
      if (t > this.duration + 0.25) {
        this.stop();
      } else {
        const pos = clamp(t / Math.max(this.duration, 0.001), 0, 1)
          * (this.timeline.length - 1);
        const i = Math.floor(pos);
        const f = pos - i;
        const a = this.timeline[i];
        const b = this.timeline[Math.min(i + 1, this.timeline.length - 1)];
        if (a) targets.set('viseme_' + a.viseme, (1 - f) * (a.weight ?? 1));
        if (b) {
          const key = 'viseme_' + b.viseme;
          targets.set(key, (targets.get(key) || 0) + f * (b.weight ?? 1));
        }
      }
    } else if (this.amplitude > 0.02) {
      // Bouche ouverte proportionnellement au volume : jamais figée.
      targets.set('viseme_A', this.amplitude * 0.55);
      targets.set('viseme_E', this.amplitude * 0.25);
    }

    // Lissage : chaque visème rejoint sa cible, les autres retombent.
    const k = 1 - Math.exp(-22 * dt);
    const names = new Set([...this.weights.keys(), ...targets.keys()]);
    let jawSum = 0;
    for (const name of names) {
      const goal = targets.get(name) || 0;
      const value = lerp(this.weights.get(name) || 0, goal, k);
      if (value < 0.002 && goal === 0) {
        this.weights.delete(name);
        if (this.available.has(name)) this.model.setMorph(name, 0);
        continue;
      }
      this.weights.set(name, value);
      if (this.available.has(name)) this.model.setMorph(name, value);
      if (name === 'viseme_A' || name === 'viseme_O') jawSum += value;
    }
    this.jaw = jawSum;
  }

  get speaking() { return this.active; }
}

/* ====================================================================== */
/* Planificateur de gestes                                                */
/* ====================================================================== */
const GESTURE_ALIASES = {
  explain: 'gesture_explain',
  open: 'gesture_open_hand',
  open_hand: 'gesture_open_hand',
  point: 'gesture_small_point',
  acknowledge: 'gesture_acknowledge',
  agree: 'gesture_agree',
  yes: 'gesture_agree',
  disagree: 'gesture_disagree',
  no: 'gesture_disagree',
  thinking: 'gesture_thinking',
  success: 'gesture_success',
  concern: 'gesture_concern',
  welcome: 'gesture_welcome',
  question: 'gesture_question',
  wait: 'gesture_wait',
  reassure: 'gesture_reassure',
  neutral: 'gesture_neutral',
  arms_cross: 'gesture_arms_cross',
};

const EMOTION_MOOD = {
  friendly: 'friendly',
  neutral: 'neutral',
  focused: 'focused',
  thinking: 'thinking',
  concerned: 'concerned',
  positive: 'pleased',
  pleased: 'pleased',
  attentive: 'attentive',
};

export class GesturePlanner {
  constructor(model, mixer, behavior) {
    this.model = model;
    this.mixer = mixer;
    this.behavior = behavior;
    this.actions = new Map();
    this.playing = null;
    this.cooldown = 0;
    this.minGap = 1.6;

    for (const name of model.additiveClips) {
      const clip = model.clips.get(name);
      if (!clip) continue;
      const action = mixer.clipAction(clip);
      action.blendMode = THREE.AdditiveAnimationBlendMode;
      action.setLoop(THREE.LoopOnce, 1);
      action.clampWhenFinished = false;
      this.actions.set(name, action);
    }
  }

  /**
   * Entrée unique du backend : {gesture, emotion, intensity}.
   * Toute valeur inconnue est ignorée — le modèle ne peut rien casser.
   */
  request(plan = {}) {
    const name = GESTURE_ALIASES[String(plan.gesture || '').toLowerCase()];
    const mood = EMOTION_MOOD[String(plan.emotion || '').toLowerCase()];
    if (mood && this.behavior) this.behavior.setMood(mood);
    if (!name) return false;
    return this.play(name, clamp(Number(plan.intensity ?? 0.6) || 0.6, 0.05, 1));
  }

  play(name, intensity = 0.6) {
    const action = this.actions.get(name);
    if (!action) return false;
    if (this.cooldown > 0 && this.playing !== name) return false;

    if (this.playing && this.playing !== name) {
      const previous = this.actions.get(this.playing);
      if (previous) previous.fadeOut(0.3);
    }
    action.reset();
    action.setEffectiveWeight(0);
    action.fadeIn(0.25);
    action.setEffectiveTimeScale(lerp(1.25, 0.85, intensity));
    action.play();
    // L'intensité pilote l'amplitude : un même geste peut être discret ou franc.
    action.setEffectiveWeight(intensity);
    this.playing = name;
    this.cooldown = this.minGap;
    this._duration = action.getClip().duration / action.getEffectiveTimeScale();
    this._elapsed = 0;
    return true;
  }

  /** Termine proprement le geste courant (barge-in). */
  release(fade = 0.25) {
    if (!this.playing) return;
    const action = this.actions.get(this.playing);
    if (action) action.fadeOut(fade);
    this.playing = null;
  }

  update(dt) {
    if (this.cooldown > 0) this.cooldown -= dt;
    if (!this.playing) return;
    this._elapsed += dt;
    if (this._elapsed >= this._duration) {
      const action = this.actions.get(this.playing);
      if (action) action.fadeOut(0.3);
      this.playing = null;
    }
  }
}
