/* ==========================================================================
   JARVIS Avatar — l'incarnation.

   Assemble le modèle, les contrôleurs et la scène, puis expose une API
   simple (window.JarvisAvatar) que la câblerie SSE de l'application pilote.

   Ordre d'une frame — il compte :
     1. mixer      : clips de base (idle / marche) + gestes additifs
     2. behavior   : respiration, poids, doigts, expression  (par-dessus)
     3. gaze       : yeux puis tête, en repère monde
     4. lip sync   : visèmes
     5. locomotion : déplacement du corps, choix du clip suivant
     6. foot IK    : les pieds ne traversent jamais le sol

   Rien n'est décoratif : chaque état vient d'un événement réel du backend.
   ========================================================================== */
import * as THREE from '../../vendor/three.module.js';
import { loadAvatar, RigControl } from './core.js';
import { AvatarFaceController } from './face_controller.js';
import { GazeController, GesturePlanner, HumanBehaviorController, LipSyncController }
  from './behavior.js';
import { CameraDirector, LocomotionController, Stage } from './locomotion.js';

const clamp = THREE.MathUtils.clamp;

/* ------------------------------------------------------------- qualité 3D */
const QUALITY = {
  low: { pixelRatio: 1.0, shadows: false, shadowSize: 512, antialias: false, targetFps: 30 },
  balanced: { pixelRatio: 1.35, shadows: true, shadowSize: 1024, antialias: true, targetFps: 55 },
  high: { pixelRatio: 1.6, shadows: true, shadowSize: 2048, antialias: true, targetFps: 58 },
  ultra: { pixelRatio: 2.0, shadows: true, shadowSize: 2048, antialias: true, targetFps: 60 },
};

/* --------------------------------------------------- états et mise en scène */
export const AVATAR_STATES = [
  'IDLE', 'LISTENING', 'UNDERSTANDING', 'THINKING', 'RECALLING', 'ACTING',
  'CODING', 'BROWSING', 'DEPLOYING', 'WALKING', 'SPEAKING', 'SUCCESS',
  'WARNING', 'ERROR', 'SLEEPING',
];

// Chaque état décrit une ATTITUDE, pas une chorégraphie : les gestes restent
// occasionnels et les regards restent plausibles.
const STATE_PROFILE = {
  IDLE: { mood: 'neutral', energy: 0.35, gaze: 'camera', attention: 0.55, idle: null },
  LISTENING: { mood: 'attentive', energy: 0.62, gaze: 'camera', attention: 0.92,
    idle: 'idle_attentive', gesture: 'acknowledge', gestureChance: 0.35, gestureEvery: 5.5 },
  UNDERSTANDING: { mood: 'attentive', energy: 0.6, gaze: 'camera', attention: 0.85 },
  THINKING: { mood: 'thinking', energy: 0.45, gaze: 'away', attention: 0.22,
    gesture: 'thinking', gestureChance: 0.5, gestureEvery: 6 },
  RECALLING: { mood: 'thinking', energy: 0.5, gaze: 'brain', attention: 0.35 },
  ACTING: { mood: 'focused', energy: 0.6, gaze: 'panel', attention: 0.5 },
  CODING: { mood: 'focused', energy: 0.55, gaze: 'desk', attention: 0.6 },
  BROWSING: { mood: 'focused', energy: 0.55, gaze: 'panel', attention: 0.55 },
  DEPLOYING: { mood: 'focused', energy: 0.65, gaze: 'panel', attention: 0.6 },
  WALKING: { mood: 'neutral', energy: 0.6, gaze: 'path', attention: 0.4 },
  SPEAKING: { mood: 'friendly', energy: 0.75, gaze: 'camera', attention: 0.8,
    gesture: 'explain', gestureChance: 0.55, gestureEvery: 3.4 },
  SUCCESS: { mood: 'pleased', energy: 0.8, gaze: 'camera', attention: 0.9,
    gesture: 'success', gestureChance: 1, gestureEvery: 99 },
  WARNING: { mood: 'concerned', energy: 0.6, gaze: 'camera', attention: 0.8,
    gesture: 'concern', gestureChance: 0.8, gestureEvery: 99 },
  ERROR: { mood: 'concerned', energy: 0.55, gaze: 'camera', attention: 0.75,
    gesture: 'concern', gestureChance: 1, gestureEvery: 99 },
  SLEEPING: { mood: 'neutral', energy: 0.08, gaze: 'down', attention: 0.1,
    idle: 'idle_relaxed' },
};

/* ====================================================================== */
export class JarvisAvatar {
  constructor(options = {}) {
    this.canvas = options.canvas;
    this.quality = QUALITY[options.quality] ? options.quality : 'balanced';
    this.modelUrl = options.modelUrl || '/assets/avatar/jarvis_avatar.glb';
    this.viewMode = options.viewMode || 'CALL';   // CALL | FULL_BODY
    this.state = 'IDLE';
    this.reason = '';
    this.ready = false;
    this.onReady = options.onReady || null;
    this.onError = options.onError || null;
    this.onStateChange = options.onStateChange || null;

    this._clock = new THREE.Clock();
    this._destroyed = false;
    this._gestureTimer = 3;
    this._fpsSamples = [];
    this.fps = 0;
    this.gpuBusy = false;

    this._initScene();
    this._load();
  }

  /* --------------------------------------------------------------- scène */
  _initScene() {
    const q = QUALITY[this.quality];
    const canvas = this.canvas;
    const width = canvas.clientWidth || 640;
    const height = canvas.clientHeight || 640;

    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: q.antialias, alpha: true, powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, q.pixelRatio));
    this.renderer.setSize(width, height, false);
    this.renderer.shadowMap.enabled = q.shadows;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(34, width / height, 0.05, 60);

    // Éclairage portrait : clé chaude, remplissage froid, contre-jour cyan.
    const hemi = new THREE.HemisphereLight(0x9fbce8, 0x0a0f18, 0.72);
    this.scene.add(hemi);

    this.key = new THREE.DirectionalLight(0xfff2e2, 2.35);
    this.key.position.set(1.5, 2.7, 2.3);
    this.key.castShadow = q.shadows;
    this.key.shadow.mapSize.set(q.shadowSize, q.shadowSize);
    this.key.shadow.camera.near = 0.5;
    this.key.shadow.camera.far = 12;
    this.key.shadow.camera.left = -2;
    this.key.shadow.camera.right = 2;
    this.key.shadow.camera.top = 3;
    this.key.shadow.camera.bottom = -0.5;
    this.key.shadow.bias = -0.0016;
    this.key.shadow.normalBias = 0.022;
    this.scene.add(this.key);

    this.fill = new THREE.DirectionalLight(0x6ba6ea, 0.75);
    this.fill.position.set(-2.3, 1.5, 1.4);
    this.scene.add(this.fill);

    this.rim = new THREE.DirectionalLight(0x22d3ee, 0.42);
    this.rim.position.set(-0.7, 2.2, -2.5);
    this.scene.add(this.rim);

    // Sol : reçoit l'ombre, ancre le personnage. Sans lui, il flotte.
    this.ground = new THREE.Mesh(
      new THREE.CircleGeometry(7, 56),
      new THREE.MeshStandardMaterial({
        color: 0x0a1322, roughness: 0.94, metalness: 0.0,
        transparent: true, opacity: 0.92,
      }));
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = q.shadows;
    this.scene.add(this.ground);

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.42, 0.47, 64),
      new THREE.MeshBasicMaterial({
        color: 0x22d3ee, transparent: true, opacity: 0.20, side: THREE.DoubleSide,
      }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.002;
    this.scene.add(ring);
    this.ring = ring;

    this.avatarRoot = new THREE.Group();
    this.scene.add(this.avatarRoot);

    this.stage = new Stage();
    this.director = new CameraDirector(this.camera, this.avatarRoot);
    this.director.setMode(this.viewMode === 'FULL_BODY' ? 'FULL_BODY' : 'CALL',
      { instant: true });

    this._resizeObserver = new ResizeObserver(() => this._resize());
    this._resizeObserver.observe(canvas);
  }

  _resize() {
    const width = this.canvas.clientWidth || 640;
    const height = this.canvas.clientHeight || 640;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  /* ------------------------------------------------------------ chargement */
  async _load() {
    try {
      this.model = await loadAvatar(this.modelUrl);
      if (this._destroyed) return;
      this.avatarRoot.add(this.model.root);

      this._tuneMaterials();
      this.rig = new RigControl(this.model);
      this.face = new AvatarFaceController(this.model);
      this.mixer = new THREE.AnimationMixer(this.model.root);
      this.behavior = new HumanBehaviorController(this.model, this.rig);
      this.gaze = new GazeController(this.model, this.rig);
      this.lipsync = new LipSyncController(this.model);
      this.gestures = new GesturePlanner(this.model, this.mixer, this.behavior);
      this.locomotion = new LocomotionController(
        this.model, this.mixer, this.avatarRoot, this.stage);
      this.locomotion.bindRig(this.rig);

      this.behavior.onPostureChange = () => this.locomotion.cycleIdle();
      this.locomotion.playIdle();
      this.setState('IDLE');
      this.ready = true;
      this._animate();
      if (this.onReady) this.onReady(this);
    } catch (err) {
      console.error('[avatar] chargement impossible', err);
      if (this.onError) this.onError(err);
    }
  }

  /**
   * Recharge le GLB de l'avatar (après adoption d'une révision Avatar Studio).
   * Cache-bust obligatoire : sans lui le navigateur resservirait l'ancien modèle.
   */
  async reload(modelUrl = '') {
    if (this.model?.root) {
      this.avatarRoot.remove(this.model.root);
      this.model.root.traverse((node) => {
        if (node.geometry) node.geometry.dispose();
        const list = Array.isArray(node.material) ? node.material : [node.material];
        list.forEach((m) => m?.dispose?.());
      });
    }
    this.model = null;
    this.ready = false;
    const base = (modelUrl || this.modelUrl).split('?')[0];
    this.modelUrl = `${base}?v=${Date.now()}`;
    await this._load();
  }

  /**
   * Réglages fins du modèle une fois chargé.
   * Le visage doit rester LISIBLE : c'est lui qu'on regarde pendant un appel.
   */
  _tuneMaterials() {
    for (const mesh of this.model.meshes) {
      const name = mesh.name || '';
      // Auto-ombrage : sur un maillage organique dense, il ne produit que du
      // moutonnement noir sur le visage et le torse. Le corps PROJETTE son
      // ombre au sol (elle ancre le personnage) mais n'en reçoit pas.
      mesh.receiveShadow = false;
      mesh.castShadow = !/Head|Hair|Eyes|Iris|Pupils|Brows|Accent/.test(name);
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        if (!mat) continue;
        if (mat.name === 'Accent') {
          // Le rappel cyan doit rester un détail, pas une enseigne.
          // Un « rappel JARVIS » doit se deviner, pas s'annoncer.
          mat.emissiveIntensity = 0.05;
          mat.color.setHex(0x1f6f7d);
        }
        if (/Skin/.test(mat.name || '')) {
          mat.roughness = 0.58;
          mat.envMapIntensity = 1.15;
        }
        if (/Sclera|Iris|Pupil/.test(mat.name || '')) {
          // Un œil sans reflet spéculaire paraît mort.
          mat.roughness = 0.12;
          mat.metalness = 0.0;
        }
      }
    }
    // Frustum d'ombre serré autour du personnage : bien plus de texels utiles
    // pour la même taille de shadow map.
    this.key.shadow.camera.left = -1.2;
    this.key.shadow.camera.right = 1.2;
    this.key.shadow.camera.top = 2.2;
    this.key.shadow.camera.bottom = -0.2;
    this.key.shadow.camera.updateProjectionMatrix();
    this.key.shadow.normalBias = 0.05;
    this.key.shadow.radius = 2.5;

    // Lumière d'appoint solidaire de la caméra : c'est ce qui donne
    // l'impression d'un visage éclairé par un écran, comme en visio.
    this.faceLight = new THREE.DirectionalLight(0xfff4e8, 0.85);
    this.scene.add(this.faceLight);
    this.scene.add(this.faceLight.target);
  }

  /* ------------------------------------------------------------- API états */
  setState(state, extra = {}) {
    const next = AVATAR_STATES.includes(state) ? state : 'IDLE';
    this.reason = extra.reason || '';
    if (next === this.state) return;
    this.state = next;
    const profile = STATE_PROFILE[next] || STATE_PROFILE.IDLE;

    if (this.behavior) {
      this.behavior.setMood(profile.mood);
      this.behavior.setEnergy(profile.energy);
    }
    if (this.gaze) this.gaze.attention = profile.attention ?? 0.6;
    if (this.locomotion && profile.idle) this.locomotion.playIdle(profile.idle);

    // Un geste marquant n'est joué qu'à l'entrée dans l'état, jamais en boucle.
    if (profile.gesture && profile.gestureEvery >= 99 && this.gestures) {
      this.gestures.play('gesture_' + profile.gesture, 0.75);
    }
    this._gestureTimer = profile.gestureEvery || 6;

    if (next === 'SPEAKING' && this.behavior) this.behavior.triggerBlink();
    if (this.onStateChange) this.onStateChange(next, extra);
  }

  /* -------------------------------------------------------------- parole */
  speak(text, options = {}) {
    if (!this.lipsync) return;
    this.lipsync.speak(text, options);
    this.setState('SPEAKING');
  }

  resyncSpeech(charIndex, totalChars) {
    if (this.lipsync) this.lipsync.resync(charIndex, totalChars);
  }

  stopSpeaking({ bargeIn = false } = {}) {
    if (this.lipsync) this.lipsync.stop({ fade: bargeIn ? 0.12 : 0.2 });
    if (bargeIn && this.gestures) this.gestures.release(0.2);
    if (this.state === 'SPEAKING') this.setState(bargeIn ? 'LISTENING' : 'IDLE');
  }

  setAudioLevel(level) {
    if (this.lipsync) this.lipsync.setAmplitude(level);
  }

  /** API publique stable pour les expressions/visèmes externes (TTS/UI). */
  setFace(values) {
    if (!this.face) return false;
    this.face.setExpression(values);
    return true;
  }

  setBlink(left = 0, right = left) {
    if (!this.face) return false;
    this.face.setBlink(left, right);
    return true;
  }

  setViseme(name, value = 0) {
    if (!this.face) return false;
    this.face.setMouth(name, value);
    return true;
  }

  /* -------------------------------------------------------------- gestes */
  gesture(plan) {
    if (!this.gestures) return false;
    return this.gestures.request(typeof plan === 'string' ? { gesture: plan } : plan);
  }

  /* --------------------------------------------------------- déplacements */
  moveTo(place, options = {}) {
    if (!this.locomotion) return false;
    const ok = this.locomotion.moveTo(place, {
      ...options,
      onArrive: () => {
        if (this.state === 'WALKING') this.setState('IDLE');
        if (options.onArrive) options.onArrive();
      },
    });
    if (ok && this.locomotion.state === 'walking') this.setState('WALKING');
    return ok;
  }

  returnHome(onArrive = null) {
    return this.moveTo('home', { face: 0, onArrive });
  }

  turnTo(target) { if (this.locomotion) this.locomotion.turnTo(target); }

  lookAt(target) {
    if (!this.gaze) return;
    if (typeof target === 'string') {
      const place = this.stage.get(target);
      if (place) {
        this.gaze.lookAt(place.position.clone().setY(1.35));
        return;
      }
    }
    this.gaze.lookAt(target);
  }

  /* ------------------------------------------------------------- caméra */
  setCameraMode(mode, options = {}) {
    return this.director ? this.director.setMode(mode, options) : false;
  }

  setViewMode(mode) {
    this.viewMode = mode === 'FULL_BODY' ? 'FULL_BODY' : 'CALL';
    this.setCameraMode(this.viewMode === 'FULL_BODY' ? 'FULL_BODY' : 'CALL');
  }

  /* ------------------------------------------------------------ qualité */
  setQuality(name) {
    const q = QUALITY[name];
    if (!q) return;
    this.quality = name;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, q.pixelRatio));
    this.renderer.shadowMap.enabled = q.shadows;
    this.key.castShadow = q.shadows;
    this.ground.receiveShadow = q.shadows;
    if (q.shadows) this.key.shadow.mapSize.set(q.shadowSize, q.shadowSize);
    this.renderer.shadowMap.needsUpdate = true;
  }

  /** Le GPU sert aussi Ollama ou ComfyUI : on lui laisse de la place. */
  setGpuBusy(busy) {
    if (this.gpuBusy === !!busy) return;
    this.gpuBusy = !!busy;
    if (this.gpuBusy) {
      this._savedQuality = this.quality;
      this.setQuality('low');
    } else if (this._savedQuality) {
      this.setQuality(this._savedQuality);
      this._savedQuality = null;
    }
  }

  /* -------------------------------------------------------------- boucle */
  _animate() {
    if (this._destroyed) return;
    requestAnimationFrame(() => this._animate());
    const dt = Math.min(this._clock.getDelta(), 0.06);
    this._update(dt);
    this.renderer.render(this.scene, this.camera);
    this._measureFps(dt);
  }

  _update(dt) {
    if (!this.ready) return;

    // 1. animations (base + additif)
    this.mixer.update(dt);

    // 2. couche « vivant » — s'ajoute aux clips sans les remplacer
    this.behavior.update(dt);

    // 3. regard : nécessite des matrices monde à jour
    this.avatarRoot.updateMatrixWorld(true);
    this._updateGazeTarget(dt);
    this.gaze.update(dt);

    // 4. parole
    this.lipsync.update(dt);
    if (this.lipsync.speaking) {
      // La tête accompagne l'intonation, très légèrement.
      const head = this.model.bone('head');
      if (head) head.rotation.x += Math.sin(performance.now() / 260) * 0.012 * this.lipsync.jaw;
    }

    // 5. déplacement
    this.locomotion.update(dt);
    // Marcher est un fait, pas une attitude : on n'écrase jamais une écoute
    // ni une prise de parole en cours (JARVIS peut parler en marchant).
    const CONVERSATIONAL = ['SPEAKING', 'LISTENING', 'UNDERSTANDING', 'WALKING'];
    if (this.locomotion.moving && !CONVERSATIONAL.includes(this.state)) {
      this.setState('WALKING');
    }

    // 6. contact au sol
    this.avatarRoot.updateMatrixWorld(true);
    this.locomotion.applyFootIK();

    // gestes conversationnels occasionnels
    this._updateAmbientGesture(dt);
    this.gestures.update(dt);

    // 7. caméra : elle suit le corps, jamais l'inverse
    this.director.update(dt);
    if (this.faceLight) {
      this.faceLight.position.copy(this.camera.position).add(new THREE.Vector3(0.25, 0.35, 0));
      this.faceLight.target.position.copy(this.avatarRoot.position).setY(1.45);
      this.faceLight.target.updateMatrixWorld();
    }

    if (this.ring) {
      this.ring.position.set(this.avatarRoot.position.x, 0.002, this.avatarRoot.position.z);
      const pulse = this.state === 'IDLE' || this.state === 'SLEEPING' ? 0.14 : 0.26;
      this.ring.material.opacity = pulse
        + Math.sin(performance.now() / 900) * 0.04;
    }
  }

  _updateGazeTarget(dt) {
    const profile = STATE_PROFILE[this.state] || STATE_PROFILE.IDLE;
    const target = new THREE.Vector3();
    switch (profile.gaze) {
      case 'brain': {
        const place = this.stage.get('brain');
        target.copy(place.position).setY(1.35);
        break;
      }
      case 'desk': {
        const place = this.stage.get('desk');
        target.copy(place.position).setY(1.05);
        break;
      }
      case 'panel':
        target.copy(this.avatarRoot.position).add(new THREE.Vector3(0.75, 1.42, 0.55));
        break;
      case 'away':
        target.copy(this.camera.position).add(new THREE.Vector3(-0.55, 0.42, -0.3));
        break;
      case 'down':
        target.copy(this.avatarRoot.position).add(new THREE.Vector3(0, 0.9, 0.8));
        break;
      case 'path': {
        const facing = this.locomotion ? this.locomotion.facing : 0;
        target.copy(this.avatarRoot.position)
          .add(new THREE.Vector3(Math.sin(facing) * 2.2, 1.55, Math.cos(facing) * 2.2));
        break;
      }
      default:
        this.director.eyeTarget(target);
    }
    this.gaze.lookAt(target);
  }

  _updateAmbientGesture(dt) {
    const profile = STATE_PROFILE[this.state];
    if (!profile || !profile.gesture || profile.gestureEvery >= 99) return;
    this._gestureTimer -= dt;
    if (this._gestureTimer > 0) return;
    this._gestureTimer = profile.gestureEvery * (0.7 + Math.random() * 0.9);
    if (Math.random() > (profile.gestureChance ?? 0.4)) return;
    // Pendant la parole, on varie : toujours le même geste se remarquerait.
    const pool = this.state === 'SPEAKING'
      ? ['explain', 'open_hand', 'small_point', 'reassure', 'neutral']
      : [profile.gesture];
    const pick = pool[Math.floor(Math.random() * pool.length)];
    this.gestures.play('gesture_' + pick, 0.35 + Math.random() * 0.35);
  }

  _measureFps(dt) {
    if (dt <= 0) return;
    this._fpsSamples.push(1 / dt);
    if (this._fpsSamples.length < 60) return;
    const avg = this._fpsSamples.reduce((a, b) => a + b, 0) / this._fpsSamples.length;
    this._fpsSamples.length = 0;
    this.fps = Math.round(avg);
    // Dégradation automatique : mieux vaut moins d'ombres qu'un avatar saccadé.
    const target = QUALITY[this.quality].targetFps;
    if (this.fps < target * 0.62 && this.quality !== 'low' && !this.gpuBusy) {
      const order = ['ultra', 'high', 'balanced', 'low'];
      const next = order[Math.min(order.indexOf(this.quality) + 1, order.length - 1)];
      if (next !== this.quality) {
        console.info(`[avatar] ${this.fps} FPS → qualité ${next}`);
        this.setQuality(next);
      }
    }
  }

  dispose() {
    this._destroyed = true;
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this.model) this.model.dispose();
    if (this.renderer) this.renderer.dispose();
  }
}

export default JarvisAvatar;
