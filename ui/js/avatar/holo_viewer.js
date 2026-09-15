/* ==========================================================================
   JARVIS — holo_viewer.js
   Le viewer de la tête holographique : rendu, comportement, parole.

   Il expose la MÊME interface que premium_viewer (gesture, setActivity, pause,
   resume, resize, attachTo, dispose) et s'y substitue sans rien changer chez
   l'appelant. Il y ajoute ce qu'un visage doit savoir faire : parler, regarder,
   cligner, réagir.

   PRINCIPE DE COMPORTEMENT. Un visage immobile est un objet ; un visage qui
   bouge en permanence est un tic. Ce qui donne le vivant, c'est l'alternance :
   des fixations longues coupées de saccades brèves, une respiration lente sous
   des micro-mouvements rapides, et surtout des mouvements qui ont une CAUSE.
   Ici chaque cause est un évènement réel de JARVIS — il parle, il réfléchit,
   un agent travaille, l'utilisateur écrit. Rien n'est joué à vide.
   ========================================================================== */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/EffectComposer.js';
import { RenderPass } from 'three/addons/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/OutputPass.js';
import { buildHoloHead } from './holo_head.js?v=JARVIS_HOLO_9';
import { buildVisemeTrack, sampleVisemeTrack, VISEMES } from './holo_visemes.js?v=JARVIS_HOLO_5';

const PIXEL_RATIO = Math.min(devicePixelRatio || 1, 1.75);

const DEFAULTS = {
  accent: 0x22d3ee,
  bloom: { strength: 0.62, radius: 0.68, threshold: 0.42 },
  exposure: 1.05,
  distance: 4.4,
};

/* États. Chacun décrit une POSTURE, pas une animation : le comportement
   interpole vers ces valeurs, ce qui évite les raccords à gérer. */
const STATES = {
  IDLE:      { brow: 0.00, lidOpen: 1.00, gazeDrift: 1.00, breath: 1.00, presence: 1.00 },
  LISTENING: { brow: 0.22, lidOpen: 1.06, gazeDrift: 0.45, breath: 0.85, presence: 1.06 },
  THINKING:  { brow: 0.34, lidOpen: 0.82, gazeDrift: 1.70, breath: 0.75, presence: 0.92 },
  SPEAKING:  { brow: 0.14, lidOpen: 1.02, gazeDrift: 0.55, breath: 1.15, presence: 1.10 },
  WORKING:   { brow: 0.10, lidOpen: 0.94, gazeDrift: 1.25, breath: 1.05, presence: 1.02 },
};

const lerp = (a, b, t) => a + (b - a) * t;
/** Approche exponentielle, indépendante de la cadence d'images : à 30 comme à
 *  144 fps le mouvement met le même temps réel à converger. */
const approach = (current, target, speed, dt) => lerp(current, target, 1 - Math.exp(-speed * dt));

export async function createHoloViewer(options = {}) {
  const config = { ...DEFAULTS, ...options };
  const host = config.host;
  if (!host) throw new Error('holo_viewer : hôte manquant');

  /* ------------------------------------------------------------- rendu */
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  // Le sujet va désormais du sommet du crâne (+1,2) à la coupe du buste
  // (-1,9). Viser y = 0,05 comme pour une tête seule laissait les épaules hors
  // champ : on abaisse la visée et on recule d'autant.
  camera.position.set(0, -0.22, config.distance * 1.34);
  camera.lookAt(0, -0.30, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(PIXEL_RATIO);
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = config.exposure;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  Object.assign(renderer.domElement.style, { width: '100%', height: '100%', display: 'block' });

  // Un hologramme émet, mais le matériau de base reste standard : sans un peu
  // de lumière, les faces non rasantes tombent au noir et le visage se réduit
  // à une silhouette. Ces deux sources ne servent qu'à ça.
  scene.add(new THREE.AmbientLight(config.accent, 0.30));
  const key = new THREE.DirectionalLight(0x9fe8ff, 1.45);
  key.position.set(1.4, 1.8, 2.4);
  scene.add(key);

  const head = buildHoloHead({ accent: config.accent });
  scene.add(head.group);

  const composer = new EffectComposer(renderer);
  // NOTE : ne pas mettre `renderPass.clearAlpha = 0` en croyant obtenir un
  // fond transparent. Essayé et mesuré : la cible devient transparente, mais
  // le halo du bloom s'y accumule et remplit tout le cadre d'un voile cyan —
  // plus visible que le noir qu'on voulait supprimer. Le fond reste opaque et
  // c'est le masque CSS, côté page, qui fond les bords.
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1),
    config.bloom.strength, config.bloom.radius, config.bloom.threshold);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  /* --------------------------------------------------------- dimensions */
  function resize() {
    const width = host.clientWidth || 1;
    const height = host.clientHeight || 1;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    composer.setSize(width * PIXEL_RATIO, height * PIXEL_RATIO);
    bloom.setSize(width * PIXEL_RATIO, height * PIXEL_RATIO);
  }
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  /* ------------------------------------------------------------- état */
  const S = {
    state: 'IDLE',
    activity: 0,
    // regard
    gaze: new THREE.Vector2(0, 0),
    gazeTarget: new THREE.Vector2(0, 0),
    nextSaccade: 0,
    pointer: null,            // dernière position du curseur sur la scène
    pointerUntil: 0,
    // paupières
    blink: 0,
    nextBlink: 1.5,
    blinkPhase: 0,
    // tête
    tilt: new THREE.Vector2(0, 0),
    tiltTarget: new THREE.Vector2(0, 0),
    nextPose: 0,
    nod: 0,
    // expression
    brow: 0,
    browTarget: 0,
    // parole
    track: null,
    speechStart: 0,
    speechEnd: 0,
    shape: { ...VISEMES.SIL },
    jaw: 0,
  };

  const clock = new THREE.Clock();
  let running = true;
  let raf = 0;
  let fps = 0;

  /* ------------------------------------------------------------ regard */
  // Le curseur est une cause légitime de mouvement : quelqu'un est là. La tête
  // le suit, mais avec retard et amplitude limitée — un regard collé au curseur
  // donne un pantin de vitrine.
  function onPointer(event) {
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    S.pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -(((event.clientY - rect.top) / rect.height) * 2 - 1),
    );
    S.pointerUntil = clock.elapsedTime + 2.6;
  }
  host.addEventListener('pointermove', onPointer);

  function updateGaze(t, dt, profile) {
    if (S.pointer && t < S.pointerUntil) {
      // On vise le curseur, atténué : la tête tourne peu, l'œil fait le reste.
      S.gazeTarget.set(S.pointer.x * 0.55, S.pointer.y * 0.42);
      S.nextSaccade = t + 0.6;
    } else if (t > S.nextSaccade) {
      // Saccade : nouvelle cible, puis fixation. Les durées viennent de
      // l'observation du regard humain — fixations de 0,4 à 2 s en conversation,
      // plus longues et plus hautes quand on cherche une idée.
      const drift = profile.gazeDrift;
      const up = S.state === 'THINKING' ? 0.35 : 0;
      S.gazeTarget.set(
        (Math.random() * 2 - 1) * 0.42 * drift,
        up + (Math.random() * 2 - 1) * 0.26 * drift,
      );
      S.nextSaccade = t + 0.45 + Math.random() * (S.state === 'SPEAKING' ? 1.1 : 2.0);
    }
    // La saccade est le mouvement le plus rapide du corps humain : on converge
    // vite (30), là où la tête suit lentement (2,5). Ce décalage est ce qui
    // rend le regard vivant.
    S.gaze.x = approach(S.gaze.x, S.gazeTarget.x, 30, dt);
    S.gaze.y = approach(S.gaze.y, S.gazeTarget.y, 30, dt);

    for (const [eye, sign] of [[head.eyes.left, 1], [head.eyes.right, 1]]) {
      eye.rotation.y = S.gaze.x * 0.5 * sign;
      eye.rotation.x = -S.gaze.y * 0.4;
    }
  }

  /* --------------------------------------------------------- paupières */
  function updateBlink(t, dt, profile) {
    if (t > S.nextBlink && S.blinkPhase === 0) {
      S.blinkPhase = 1;
      // Les clignements vont par salves : un isolé toutes les 3 à 7 s, parfois
      // deux coup sur coup. Une cadence régulière se repère immédiatement.
      S.nextBlink = t + (Math.random() < 0.18 ? 0.22 : 3 + Math.random() * 4);
    }
    if (S.blinkPhase > 0) {
      // 120 ms de fermeture, 180 ms d'ouverture — durées réelles d'un clignement.
      S.blink += dt / (S.blinkPhase === 1 ? 0.12 : -0.18);
      if (S.blink >= 1) { S.blink = 1; S.blinkPhase = 2; }
      if (S.blink <= 0) { S.blink = 0; S.blinkPhase = 0; }
    }
    const open = profile.lidOpen * (1 - S.blink);
    for (const eye of [head.eyes.left, head.eyes.right]) {
      // La paupière descend ; à 0 elle couvre entièrement l'œil.
      eye.userData.lid.position.y = lerp(0.055, 0.345, Math.max(0, Math.min(1, open)));
    }
  }

  /* -------------------------------------------------- posture et gestes */
  function updatePosture(t, dt, profile) {
    if (t > S.nextPose) {
      // Un léger report de poids, comme quelqu'un qui se réinstalle.
      S.tiltTarget.set((Math.random() * 2 - 1) * 0.10, (Math.random() * 2 - 1) * 0.07);
      S.nextPose = t + 2.5 + Math.random() * 4;
    }
    if (S.pointer && t < S.pointerUntil) S.tiltTarget.x = S.pointer.x * 0.22;

    S.tilt.x = approach(S.tilt.x, S.tiltTarget.x, 2.5, dt);
    S.tilt.y = approach(S.tilt.y, S.tiltTarget.y, 2.5, dt);

    // Respiration : lente, amplifiée par l'activité réelle.
    const breath = Math.sin(t * 1.15) * 0.018 * profile.breath * (1 + S.activity * 0.5);
    const micro = Math.sin(t * 5.7) * 0.0022 + Math.sin(t * 3.1) * 0.0031;

    S.nod = approach(S.nod, 0, 3.2, dt);

    head.bones.neck.rotation.x = S.tilt.y * 0.45 + breath * 0.6 + S.nod;
    head.bones.neck.rotation.y = S.tilt.x * 0.55;
    head.bones.head.rotation.x = S.gaze.y * -0.20 + micro + S.nod * 0.6;
    head.bones.head.rotation.y = S.gaze.x * 0.34 + S.tilt.x * 0.25;
    head.bones.head.rotation.z = S.tilt.x * -0.16;
    head.group.position.y = breath * 1.2;
  }

  /* -------------------------------------------------------- expression */
  function updateBrows(dt, profile) {
    S.brow = approach(S.brow, profile.brow + S.browTarget, 6, dt);
    head.bones.browL.position.y = head.anchor.browY + S.brow * 0.055;
    head.bones.browR.position.y = head.anchor.browY + S.brow * 0.055;
    // Les sourcils s'inclinent aussi : levés ET rapprochés, c'est de la
    // concentration ; levés et écartés, de la surprise.
    const pinch = S.state === 'THINKING' ? -0.10 : 0.06;
    head.bones.browL.rotation.z = -S.brow * pinch;
    head.bones.browR.rotation.z = S.brow * pinch;
  }

  /* ------------------------------------------------------------ parole */
  function updateSpeech(t, dt) {
    let shape = VISEMES.SIL;
    if (S.track && t < S.speechEnd) {
      shape = sampleVisemeTrack(S.track, t - S.speechStart);
    } else if (S.track && t >= S.speechEnd) {
      S.track = null;
      if (S.state === 'SPEAKING') setState('IDLE');
    }

    // Lissage : la bouche a une inertie. Sans elle, les consonnes claquent.
    for (const k of ['open', 'wide', 'round', 'press']) {
      S.shape[k] = approach(S.shape[k], shape[k], 26, dt);
    }

    // La mâchoire porte l'ouverture ; elle entraîne la joue par la peau.
    S.jaw = approach(S.jaw, S.shape.open, 22, dt);
    head.bones.jaw.rotation.x = S.jaw * 0.30;

    // La bouche porte la FORME. Les trois axes sont indépendants, c'est ce qui
    // distingue un « ou » d'un « a » ouverts de la même hauteur.
    const mouth = head.mouth;
    const height = 0.07 + S.shape.open * 0.95 - S.shape.press * 0.05;
    const width = 1.25 + S.shape.wide * 0.42 - S.shape.round * 0.72 - S.shape.press * 0.10;
    mouth.scale.set(Math.max(0.35, width), Math.max(0.05, height), 1);
    mouth.position.z = head.anchor.mouthZ + S.shape.round * 0.045;
    // Le liseré s'allume à l'ouverture, la cavité se creuse : les deux
    // ensemble font qu'on lit la bouche de loin, fermée comme grande ouverte.
    const aperture = Math.max(S.shape.open, S.shape.press * 0.6);
    mouth.userData.rim.material.opacity = 0.72 + 0.28 * aperture;
    mouth.userData.cavity.material.opacity = 0.55 + 0.40 * aperture;

    // Prosodie : la tête accompagne la parole. Un hochement par syllabe
    // accentuée, pas un balancement continu.
    if (S.track && S.shape.open > 0.7 && Math.random() < 0.035) S.nod = -0.022;
  }

  /* ----------------------------------------------------------- boucle */
  let frames = 0, fpsClock = 0;
  function loop() {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.05, clock.getDelta());
    const t = clock.elapsedTime;
    const profile = STATES[S.state] || STATES.IDLE;

    updateGaze(t, dt, profile);
    updateBlink(t, dt, profile);
    updateSpeech(t, dt);
    updatePosture(t, dt, profile);
    updateBrows(dt, profile);

    const presence = profile.presence * (0.94 + S.activity * 0.1);
    head.tick(t, presence);
    head.emitter.userData.disc.rotation.z = t * 0.12;
    bloom.strength = config.bloom.strength * (0.9 + S.activity * 0.35 + S.shape.open * 0.2);

    composer.render();

    frames += 1; fpsClock += dt;
    if (fpsClock >= 0.5) { fps = Math.round(frames / fpsClock); frames = 0; fpsClock = 0; }
  }

  function setState(name) {
    if (!STATES[name] || S.state === name) return;
    S.state = name;
    S.nextSaccade = 0;                 // le changement d'état provoque un regard
  }

  clock.start();
  loop();

  /* -------------------------------------------------------------- API */
  return {
    scene, camera, renderer, composer, head,
    get fps() { return fps; },
    get state() { return S.state; },

    /** Fait parler la tête. `text` est le texte RÉELLEMENT prononcé et
     *  `duration` la durée estimée par le moteur vocal — les deux arrivent
     *  ensemble dans l'évènement `tts.started`. */
    speak(text, duration) {
      const track = buildVisemeTrack(text, duration);
      if (!track.length) return false;
      S.track = track;
      S.speechStart = clock.elapsedTime;
      S.speechEnd = S.speechStart + track[track.length - 1].until;
      setState('SPEAKING');
      return true;
    },

    /** Recale la partition sur l'avancée RÉELLE du moteur vocal. Le navigateur
     *  émet `onboundary` à chaque mot : on sait donc où il en est vraiment, et
     *  on corrige la dérive de l'estimation au lieu de la laisser filer. */
    resyncSpeech(charIndex, totalChars) {
      if (!S.track || !totalChars) return;
      const span = S.track[S.track.length - 1].until;
      const expected = (charIndex / totalChars) * span;
      const actual = clock.elapsedTime - S.speechStart;
      const drift = actual - expected;
      // On ne rattrape qu'un tiers de l'écart par évènement : une correction
      // sèche ferait sauter la bouche à chaque mot.
      if (Math.abs(drift) > 0.06) S.speechStart += drift * 0.34;
    },

    stopSpeaking() {
      S.track = null;
      if (S.state === 'SPEAKING') setState('IDLE');
    },

    setState,

    /** Réaction visible quand JARVIS agit réellement : un hochement bref. */
    gesture() {
      S.nod = -0.055;
      S.browTarget = 0.30;
      setTimeout(() => { S.browTarget = 0; }, 520);
      S.nextSaccade = 0;
      return true;
    },

    /** 0 = calme, 1 = actif. Amplifie respiration, présence et halo. */
    setActivity(level) {
      S.activity = Math.max(0, Math.min(1, Number(level) || 0));
    },

    /** Regard dirigé, en coordonnées normalisées (-1..1). */
    lookAt(x, y) {
      S.gazeTarget.set(Math.max(-1, Math.min(1, x)) * 0.6, Math.max(-1, Math.min(1, y)) * 0.45);
      S.nextSaccade = clock.elapsedTime + 1.2;
    },

    setBloom({ strength, radius, threshold }) {
      if (strength !== undefined) config.bloom.strength = strength;
      if (radius !== undefined) bloom.radius = radius;
      if (threshold !== undefined) bloom.threshold = threshold;
    },
    setAutoRotate() { /* sans objet : la tête regarde, elle ne tourne pas sur socle */ },
    recenter() { resize(); },
    resize,

    pause() { running = false; cancelAnimationFrame(raf); },
    resume() { if (!running) { running = true; clock.getDelta(); loop(); } },

    attachTo(el) {
      if (!el || renderer.domElement.parentElement === el) return;
      el.appendChild(renderer.domElement);
      observer.disconnect();
      observer.observe(el);
      resize();
    },

    dispose() {
      running = false;
      cancelAnimationFrame(raf);
      observer.disconnect();
      host.removeEventListener('pointermove', onPointer);
      head.dispose();
      composer.dispose?.();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

export default createHoloViewer;
