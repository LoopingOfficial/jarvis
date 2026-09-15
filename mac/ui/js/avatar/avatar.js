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
import { RoomEnvironment } from '../../vendor/RoomEnvironment.js';
import { JarvisAnimationStateController } from './animation_state.js';

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
    this.modelUrl = options.modelUrl || '/assets/avatar/jarvis_premium.glb';
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
    this.contextLost = false;
    this._visible = true;      // onglet au premier plan
    this._onScreen = true;     // canvas dans le viewport

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
    this.scene.fog = new THREE.FogExp2(0x05080f, 0.14);
    this.camera = new THREE.PerspectiveCamera(34, width / height, 0.05, 60);

    // IBL — SANS LUI, RIEN N'EST PBR.
    // Les matériaux portaient déjà `envMapIntensity`, mais `scene.environment`
    // n'était jamais défini : la peau et les yeux ne recevaient donc AUCUNE
    // réflexion spéculaire et tout paraissait en plastique mat. C'est la
    // correction la plus rentable de tout le pipeline.
    this._pmrem = new THREE.PMREMGenerator(this.renderer);
    this._pmrem.compileEquirectangularShader();
    const room = new RoomEnvironment();
    this._envRT = this._pmrem.fromScene(room, 0.035);
    this.scene.environment = this._envRT.texture;
    room.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const list = Array.isArray(o.material) ? o.material : [o.material];
      list.forEach((m) => m?.dispose?.());
    });

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

    this.rim = new THREE.DirectionalLight(0x22d3ee, 0.52);
    this.rim.position.set(-0.7, 2.2, -2.5);
    this.scene.add(this.rim);

    // Sol : reçoit l'ombre, ancre le personnage. Sans lui, il flotte.
    this.ground = new THREE.Mesh(
      new THREE.CircleGeometry(7, 56),
      new THREE.MeshStandardMaterial({
        color: 0x060b16, roughness: 0.94, metalness: 0.0,
        transparent: true, opacity: 0.9,
      }));
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = q.shadows;
    this.scene.add(this.ground);

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.47, 0.55, 64),
      new THREE.MeshBasicMaterial({
        color: 0x22d3ee, transparent: true, opacity: 0.16, side: THREE.DoubleSide,
      }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.002;
    this.scene.add(ring);
    this.ring = ring;

    // Halo lumineux au sol : ancre « premium » qui suit doucement l'avatar.
    const poolCanvas = document.createElement('canvas');
    poolCanvas.width = 256; poolCanvas.height = 256;
    const pctx = poolCanvas.getContext('2d');
    const pgrad = pctx.createRadialGradient(128, 128, 0, 128, 128, 128);
    pgrad.addColorStop(0, 'rgba(34,211,238,0.42)');
    pgrad.addColorStop(0.38, 'rgba(34,211,238,0.10)');
    pgrad.addColorStop(1, 'rgba(34,211,238,0)');
    pctx.fillStyle = pgrad;
    pctx.fillRect(0, 0, 256, 256);
    const poolTex = new THREE.CanvasTexture(poolCanvas);
    const pool = new THREE.Mesh(
      new THREE.PlaneGeometry(3.4, 3.4),
      new THREE.MeshBasicMaterial({
        map: poolTex, transparent: true, opacity: 0.9,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
    pool.rotation.x = -Math.PI / 2;
    pool.position.y = 0.001;
    this.scene.add(pool);
    this.pool = pool;

    this.avatarRoot = new THREE.Group();
    this.scene.add(this.avatarRoot);

    this.stage = new Stage();
    this.director = new CameraDirector(this.camera, this.avatarRoot);
    this.director.setMode(this.viewMode === 'FULL_BODY' ? 'FULL_BODY' : 'CALL',
      { instant: true });

    this._resizeObserver = new ResizeObserver(() => this._resize());
    this._resizeObserver.observe(canvas);
    this._bindLifecycle();
  }

  /**
   * Le GPU sert aussi Ollama/ComfyUI et l'utilisateur change d'onglet : rendre
   * un avatar que personne ne regarde est du GPU volé. On suspend le rendu
   * sans toucher à l'horloge logique, et on encaisse proprement une perte de
   * contexte WebGL (pilote qui redémarre, mise en veille) au lieu de mourir.
   */
  _bindLifecycle() {
    const canvas = this.canvas;

    this._onContextLost = (event) => {
      event.preventDefault();          // sinon le contexte n'est jamais restauré
      this.contextLost = true;
      console.warn('[avatar] contexte WebGL perdu — rendu suspendu');
    };
    this._onContextRestored = () => {
      this.contextLost = false;
      // L'IBL vit dans le contexte perdu : il faut le régénérer.
      try {
        this._envRT?.dispose();
        this._pmrem?.dispose();
        this._pmrem = new THREE.PMREMGenerator(this.renderer);
        const room = new RoomEnvironment();
        this._envRT = this._pmrem.fromScene(room, 0.035);
        this.scene.environment = this._envRT.texture;
      } catch (err) {
        console.warn('[avatar] IBL non régénéré', err);
      }
      this._clock.getDelta();          // absorbe le dt accumulé
      console.info('[avatar] contexte WebGL restauré');
    };
    canvas.addEventListener('webglcontextlost', this._onContextLost, false);
    canvas.addEventListener('webglcontextrestored', this._onContextRestored, false);

    this._onVisibility = () => {
      this._visible = document.visibilityState !== 'hidden';
      if (this._visible) this._clock.getDelta();
    };
    // L'etat initial compte : `visibilitychange` ne se declenche qu'au
    // CHANGEMENT. Construit dans un document deja masque, l'avatar se croyait
    // visible et `rendering` mentait — y compris a la sonde de performance,
    // qui rendait alors un diagnostic errone.
    this._visible = document.visibilityState !== 'hidden';
    document.addEventListener('visibilitychange', this._onVisibility);

    if (typeof IntersectionObserver === 'function') {
      this._io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          this._onScreen = e.isIntersecting;
          if (this._onScreen) this._clock.getDelta();
        }
      }, { threshold: 0.01 });
      this._io.observe(canvas);
    }
  }

  /** Le rendu ne coûte du GPU que s'il sert réellement à quelqu'un. */
  get rendering() {
    return !this.contextLost && this._visible && this._onScreen;
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

      // Une seule table etat -> animation, lisible et testable, plutot que la
      // logique eparpillee entre STATE_PROFILE, locomotion et gestes.
      this.animation = new JarvisAnimationStateController(
        this.model, this.locomotion, this.gestures, this.mixer);

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
    // Un horodatage force le retelechargement A CHAQUE rechargement et annule
    // tout cache. On conserve la version portee par l'URL quand il y en a une
    // (avatar_source.js), et on ne force que si elle est absente.
    const requested = modelUrl || this.modelUrl;
    this.modelUrl = requested.includes('?v=')
      ? requested
      : `${requested.split('?')[0]}?v=${Date.now()}`;
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
        const mname = mat.name || '';

        // Le GLB exporte TOUT en doubleSided. Sur un volume fermé c'est inutile
        // (double coût de fill) et surtout destructeur pour la peau : les faces
        // arrière s'ombrent avec une normale inversée et salissent le visage.
        mat.side = THREE.FrontSide;
        mat.envMapIntensity = 1.0;

        if (mname === 'Accent') {
          // Le rappel cyan doit rester un détail, pas une enseigne.
          mat.emissiveIntensity = 0.05;
          mat.color.setHex(0x1f6f7d);
        }
        if (/Skin/.test(mname)) {
          // Peau : la rugosité uniforme est ce qui donne l'aspect « mannequin ».
          // Sans carte de rugosité (le mesh n'a pas d'UV), on compense par un
          // spéculaire faible mais présent, nourri par l'IBL.
          mat.roughness = 0.52;
          mat.metalness = 0.0;
          mat.envMapIntensity = 1.25;
        }
        if (/Hair/.test(mname)) {
          // Les cheveux sont un volume solide : un spéculaire large et doux
          // suggère la mèche là où la géométrie ne peut pas la décrire.
          mat.roughness = 0.34;
          mat.metalness = 0.12;
          mat.envMapIntensity = 1.5;
        }
        if (/Sclera/.test(mname)) {
          mat.roughness = 0.08;
          mat.metalness = 0.0;
          mat.envMapIntensity = 2.2;
        }
        if (/Iris|Pupil|Cornea/.test(mname)) {
          // Un œil sans reflet spéculaire paraît mort : c'est le point de vie
          // le plus rentable de tout le visage.
          mat.roughness = 0.05;
          mat.metalness = 0.0;
          mat.envMapIntensity = 2.6;
        }
        if (/Brows/.test(mname)) {
          mat.roughness = 0.75;
          mat.envMapIntensity = 0.6;
        }
        mat.needsUpdate = true;
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
    // Clip de base + geste marquant : delegue au controleur d'animation.
    if (this.animation) this.animation.setState(next);

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

  /**
   * Émotion explicite, indépendante de l'état machine.
   * Un état dit ce que JARVIS FAIT ; une émotion dit sur quel TON il le fait.
   * Les deux sont volontairement dissociés : on peut penser sereinement ou
   * annoncer une erreur sans grimace.
   */
  setEmotion(emotion, { intensity = 1 } = {}) {
    if (!this.behavior) return false;
    const MOODS = ['neutral', 'friendly', 'attentive', 'thinking',
      'concerned', 'pleased', 'focused'];
    const ALIAS = { positive: 'pleased', happy: 'pleased', calm: 'neutral',
      serious: 'focused', worried: 'concerned' };
    const key = String(emotion || '').toLowerCase();
    const mood = MOODS.includes(key) ? key : ALIAS[key];
    if (!mood) return false;
    this.behavior.setMood(mood);
    this.behavior.setEnergy(clamp(0.3 + intensity * 0.45, 0, 1));
    this.emotion = mood;
    return true;
  }

  /**
   * Retour à l'état neutre : coupe la parole, relâche les gestes, efface les
   * morphs faciaux et ramène le regard caméra. Utilisé entre deux échanges et
   * par les tests — après `reset()`, aucune frame antérieure ne doit subsister.
   */
  reset() {
    if (!this.ready) return false;
    this.lipsync?.stop({ fade: 0 });
    this.gestures?.release(0.15);
    this.face?.clearMouth();
    this.face?.setBlink(0, 0);
    this.model?.clearMorphs();
    this.behavior?.setMood('neutral');
    this.behavior?.setEnergy(0.35);
    this.emotion = 'neutral';
    this.state = '';                 // force la transition dans setState
    this.setState('IDLE');
    this.locomotion?.playIdle();
    return true;
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
    if (!this.rendering) {
      // Onglet caché, avatar hors écran ou contexte perdu : on ne simule ni ne
      // rend. L'horloge est relue à la reprise pour éviter un saut de dt.
      this._clock.getDelta();
      return;
    }
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

    // 3 bis. sourcils : ils suivent browUp/browDown sans etre fusionnes
    this._updateBrows();

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

    // recouvrements conversationnels occasionnels
    if (this.animation) this.animation.update(dt);
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
      this.pool?.position.set(this.avatarRoot.position.x, 0.001, this.avatarRoot.position.z);
      const pulse = this.state === 'IDLE' || this.state === 'SLEEPING' ? 0.14 : 0.26;
      this.ring.material.opacity = pulse
        + Math.sin(performance.now() / 900) * 0.04;
    }
  }

  /**
   * Les sourcils sont un objet SEPARE, rigidement attache a l'os `head` : les
   * fusionner dans le corps (pour qu'ils heritent des morphs) produisait une
   * geometrie qui n'etait plus rendue du tout. On les fait donc suivre le
   * morph par un simple decalage de transform, en repere local de l'os.
   *
   * L'amplitude reprend celle de la shape key browUp mesuree au build
   * (4,5 mm de course) : les sourcils et l'arcade restent solidaires.
   */
  _updateBrows() {
    if (!this.model) return;
    if (this._brows === undefined) {
      this._brows = this.model.root.getObjectByName('JARVIS_Brows') || null;
      if (this._brows) this._browRest = this._brows.position.clone();
    }
    if (!this._brows) return;
    const up = this.model.getMorph('browUp');
    const down = this.model.getMorph('browDown');
    // repere glTF : Y vers le haut une fois converti par le chargeur
    this._brows.position.copy(this._browRest);
    this._brows.position.y += (up * 0.0045) - (down * 0.0040);
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
    if (this._io) this._io.disconnect();
    document.removeEventListener('visibilitychange', this._onVisibility);
    this.canvas?.removeEventListener('webglcontextlost', this._onContextLost);
    this.canvas?.removeEventListener('webglcontextrestored', this._onContextRestored);
    if (this.model) this.model.dispose();
    this._envRT?.dispose();
    this._pmrem?.dispose();
    if (this.renderer) this.renderer.dispose();
  }
}

export default JarvisAvatar;
