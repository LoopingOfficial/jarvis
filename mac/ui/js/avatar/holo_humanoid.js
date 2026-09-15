/* ==========================================================================
   JARVIS — Humanoïde holographique

   Une silhouette humaine entièrement PROCÉDURALE : pas de GLB, pas de rig
   importé, pas de texture. Le corps est un squelette de groupes THREE, chaque
   os porte une capsule translucide et son propre quadrillage. C'est ce qui
   rend la chose tenable : la pose est du code, pas un fichier binaire que
   personne ne peut relire ni corriger.

   Pourquoi une silhouette et pas un personnage réaliste : un humain modélisé à
   la main tombe dans la vallée de l'étrange à la première frame. Une projection
   holographique, elle, n'a pas de « raté » possible — elle annonce ce qu'elle
   est, une IA qui se donne une forme.

       const body = await createHoloHumanoid({ canvas });
       body.setState('SPEAKING'); body.setAudioLevel(0.4); body.dispose();

   L'interface est celle de holo_core / premium_viewer (setState, setActivity,
   setAudioLevel, lookAt, gesture, pause, resume, resize, attachTo, dispose),
   pour que les surfaces appelantes n'aient rien à savoir de l'implémentation.
   ========================================================================== */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/EffectComposer.js';
import { RenderPass } from 'three/addons/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/OutputPass.js';

const PIXEL_RATIO = Math.min(devicePixelRatio || 1, 1.5);

const DEFAULTS = {
  color: 0x6fe6ff,           // cyan JARVIS, couche proche
  deep: 0x2f7fd4,            // bleu profond, couche lointaine
  height: 1.78,              // taille de la silhouette, en unités de scène
  // Seuil haut : la scène est entièrement additive, presque tout dépasse 0.2.
  bloom: { strength: 0.72, radius: 0.85, threshold: 0.42 },
  exposure: 1.0,
  projector: true,           // socle : faisceau + anneaux au sol
  framing: 'FULL',           // FULL | HALF_BODY | HEAD
};

/* Cadrages. Un humanoïde debout tient mal dans un carré : on change la
   distance ET la hauteur visée, jamais la focale, pour garder la perspective
   constante d'un cadrage à l'autre. */
const FRAMING = {
  FULL:      { distance: 3.95, lookY: 0.88, fov: 34 },
  HALF_BODY: { distance: 2.10, lookY: 1.36, fov: 34 },
  HEAD:      { distance: 1.10, lookY: 1.56, fov: 32 },
};

/* -------------------------------------------------------------------------
   Matières

   Deux familles seulement :
     · `skin`  — la masse, translucide, allumée sur la silhouette (Fresnel)
     · `wire`  — le quadrillage, qui donne l'échelle et la lisibilité

   Les deux partagent uTime / uAmp : un seul uniforme piloté par la boucle.
   ------------------------------------------------------------------------- */

function makeSkinMaterial(uTime, uAmp, tint, deep) {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    // FrontSide : en additif, la face arrière doublerait chaque pixel et le
    // corps virerait au blanc laiteux.
    side: THREE.FrontSide,
    uniforms: { uTime, uAmp, uColor: { value: tint }, uDeep: { value: deep } },
    vertexShader: `
      varying vec3 vNrm;
      varying vec3 vWorld;
      void main() {
        vNrm = normalize(normalMatrix * normal);
        vWorld = (modelMatrix * vec4(position, 1.0)).xyz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform float uTime;
      uniform float uAmp;
      uniform vec3 uColor;
      uniform vec3 uDeep;
      varying vec3 vNrm;
      varying vec3 vWorld;
      void main() {
        // vNrm est en espace vue : 1 - |z| vaut 0 face caméra, 1 au bord.
        float fres = pow(1.0 - abs(vNrm.z), 2.2);
        // Lignes de balayage horizontales, en espace MONDE : elles traversent
        // le corps d'un membre à l'autre au lieu de suivre chaque capsule.
        float scan = 0.5 + 0.5 * sin(vWorld.y * 150.0 - uTime * 3.0);
        float drift = 0.5 + 0.5 * sin(vWorld.y * 3.0 - uTime * 1.1);
        vec3 rgb = mix(uDeep, uColor, fres * 0.85 + drift * 0.15);
        rgb *= 0.12 + fres * 1.25 + scan * 0.10 + uAmp * 0.30;
        gl_FragColor = vec4(rgb, (0.05 + fres * 0.42) * (0.85 + uAmp * 0.30));
      }`,
  });
}

function makeWireMaterial(tint) {
  return new THREE.LineBasicMaterial({
    color: tint, transparent: true, opacity: 0.30,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
}

/** Anneaux horizontaux le long d'une capsule : la « tranche » du volume. */
function ringsGeometry(radius, length, count, segments = 28) {
  const points = [];
  for (let i = 0; i < count; i++) {
    // On évite les extrémités, où la capsule se referme et où l'anneau
    // dépasserait de la matière.
    const t = (i + 1) / (count + 1);
    const y = length * (0.5 - t);
    // Section réelle de la capsule à cette hauteur : sinon les anneaux
    // flottent autour des épaules et des chevilles.
    const k = Math.sin(Math.PI * t);
    const r = radius * (0.55 + 0.45 * k);
    for (let s = 0; s < segments; s++) {
      const a = (s / segments) * Math.PI * 2;
      const b = ((s + 1) / segments) * Math.PI * 2;
      points.push(Math.cos(a) * r, y, Math.sin(a) * r);
      points.push(Math.cos(b) * r, y, Math.sin(b) * r);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

/** Halo radial. Un SpriteMaterial sans `map` est un carré plein : il faut
    fournir la décroissance nous-mêmes, sinon le coeur s'affiche en cube. */
function sparkTexture(size = 128) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0.00, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.25, 'rgba(255,255,255,0.55)');
  gradient.addColorStop(0.60, 'rgba(255,255,255,0.12)');
  gradient.addColorStop(1.00, 'rgba(255,255,255,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Cercle unitaire dans le plan XZ, réutilisé pour le sol et le balayage. */
function circleGeometry(segments = 96) {
  const points = [];
  for (let s = 0; s <= segments; s++) {
    const a = (s / segments) * Math.PI * 2;
    points.push(Math.cos(a), 0, Math.sin(a));
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

export async function createHoloHumanoid(options = {}) {
  const opts = {
    ...DEFAULTS, ...options,
    bloom: { ...DEFAULTS.bloom, ...(options.bloom || {}) },
  };
  const host = opts.host || null;
  const canvas = opts.canvas || null;
  if (!host && !canvas) {
    throw new Error('createHoloHumanoid : `host` ou `canvas` est requis.');
  }

  const tint = new THREE.Color(opts.color);
  const deep = new THREE.Color(opts.deep);
  const H = opts.height;

  /* ------------------------------------------------------------- rendu */
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.01, 60);

  const renderer = new THREE.WebGLRenderer({
    canvas: canvas || undefined,
    antialias: true, alpha: true, powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(PIXEL_RATIO);
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = opts.exposure;
  if (!canvas) {
    renderer.domElement.style.cssText = 'display:block;width:100%;height:100%';
    host.appendChild(renderer.domElement);
  }

  // Aucune lumière : tout est additif et non éclairé. Pas de PBR, pas d'ombre,
  // pas d'environnement à générer — c'est ce qui rend ce rendu dix fois plus
  // léger qu'un personnage skinné.
  const uTime = { value: 0 };
  const uAmp = { value: 0 };
  const disposables = [];
  const keep = (x) => { disposables.push(x); return x; };

  const skin = keep(makeSkinMaterial(uTime, uAmp, tint, deep));
  const wire = keep(makeWireMaterial(tint));

  const root = new THREE.Group();
  scene.add(root);

  /* ---------------------------------------------------------- squelette

     Chaque os est un Group placé à son articulation : on tourne le GROUPE, la
     capsule pend dessous. Une rotation d'épaule emmène donc l'avant-bras et la
     main sans le moindre calcul — c'est toute la raison d'être de la hiérarchie.
  */
  function bone(parent, { length, radius, rings = 3, y = 0, x = 0, z = 0 }) {
    const joint = new THREE.Group();
    joint.position.set(x, y, z);
    parent.add(joint);
    if (length > 0) {
      const geometry = keep(new THREE.CapsuleGeometry(radius, Math.max(0.001, length - radius * 2), 6, 18));
      const mesh = new THREE.Mesh(geometry, skin);
      mesh.position.y = -length / 2;     // l'os pend sous son articulation
      joint.add(mesh);
      if (rings > 0) {
        const lines = new THREE.LineSegments(keep(ringsGeometry(radius, length, rings)), wire);
        lines.position.y = -length / 2;
        joint.add(lines);
      }
    }
    return joint;
  }

  /* Proportions canoniques (têtes) ramenées à la hauteur demandée. Les valeurs
     sont des fractions de H : changer `height` ne casse donc jamais la pose. */
  const P = {
    hipY: H * 0.52, spine: H * 0.16, chest: H * 0.14, neck: H * 0.05,
    head: H * 0.13, upperArm: H * 0.165, forearm: H * 0.155, hand: H * 0.055,
    thigh: H * 0.245, shin: H * 0.235, foot: H * 0.055,
    shoulder: H * 0.105, hipHalf: H * 0.055,
  };

  const hips = bone(root, { length: 0, radius: 0, y: P.hipY });
  const pelvis = new THREE.Mesh(
    keep(new THREE.CapsuleGeometry(H * 0.062, H * 0.045, 6, 20)), skin);
  pelvis.scale.set(1.15, 1.0, 0.80);
  pelvis.position.y = -H * 0.02;
  hips.add(pelvis);

  const spine = bone(hips, { length: 0, radius: 0 });
  const spineMesh = new THREE.Mesh(
    keep(new THREE.CapsuleGeometry(H * 0.047, P.spine * 0.85, 6, 20)), skin);
  spineMesh.scale.set(1.08, 1.0, 0.78);
  spineMesh.position.y = P.spine / 2;
  spine.add(spineMesh);

  const chest = bone(spine, { length: 0, radius: 0, y: P.spine });
  const chestMesh = new THREE.Mesh(
    keep(new THREE.CapsuleGeometry(H * 0.085, P.chest * 0.62, 6, 22)), skin);
  // Epaules larges, cage peu profonde : c'est ce rapport, plus que le detail,
  // qui fait lire une silhouette comme humaine et non comme un tonneau.
  chestMesh.scale.set(1.30, 1.0, 0.78);
  chestMesh.position.y = P.chest * 0.42;
  chest.add(chestMesh);
  chest.add(new THREE.LineSegments(
    keep(ringsGeometry(H * 0.095, P.chest * 1.25, 4, 32)), wire));
  chestMesh.position.y = P.chest * 0.42;

  // Le coeur : la seule source « chaude » du corps. C'est lui qui traduit la
  // parole et l'activité — un point qui bat se lit de loin, un membre qui
  // s'agite non.
  const heartMaterial = keep(new THREE.SpriteMaterial({
    map: keep(sparkTexture()),
    color: tint, transparent: true, opacity: 0.85,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const heart = new THREE.Sprite(heartMaterial);
  heart.position.set(0, P.chest * 0.55, H * 0.045);
  heart.scale.setScalar(H * 0.085);
  chest.add(heart);

  const neck = bone(chest, { length: 0, radius: 0, y: P.chest });
  const neckMesh = new THREE.Mesh(
    keep(new THREE.CapsuleGeometry(H * 0.028, P.neck, 6, 14)), skin);
  neckMesh.position.y = P.neck / 2;
  neck.add(neckMesh);

  /* ------------------------------------------------------------- tête

     Une tête ovoïde, sans visage sculpté : deux yeux-barres et une bande de
     bouche qui ne bouge que quand JARVIS parle. Tout le reste du « vivant »
     vient du regard et des micro-mouvements, pas de la géométrie. */
  const headJoint = bone(neck, { length: 0, radius: 0, y: P.neck });
  const headMesh = new THREE.Mesh(
    keep(new THREE.SphereGeometry(P.head * 0.5, 28, 22)), skin);
  headMesh.scale.set(0.90, 1.04, 0.92);
  headMesh.position.y = P.head * 0.5;
  headJoint.add(headMesh);
  const headWire = new THREE.LineSegments(
    keep(ringsGeometry(P.head * 0.46, P.head, 4, 30)), wire);
  headWire.position.y = P.head * 0.5;
  headJoint.add(headWire);

  const glowMaterial = keep(new THREE.MeshBasicMaterial({
    color: 0xbdf3ff, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const eyes = [-1, 1].map((side) => {
    const eye = new THREE.Mesh(
      keep(new THREE.PlaneGeometry(P.head * 0.20, P.head * 0.045)), glowMaterial);
    eye.position.set(side * P.head * 0.17, P.head * 0.56, P.head * 0.41);
    headJoint.add(eye);
    return eye;
  });
  const mouth = new THREE.Mesh(
    keep(new THREE.PlaneGeometry(P.head * 0.24, P.head * 0.03)), glowMaterial.clone());
  mouth.material.opacity = 0.0;
  keep(mouth.material);
  mouth.position.set(0, P.head * 0.30, P.head * 0.40);
  headJoint.add(mouth);

  /* ------------------------------------------------------- bras, jambes */
  function arm(side) {
    const shoulder = bone(chest, {
      length: P.upperArm, radius: H * 0.024, rings: 2,
      x: side * P.shoulder, y: P.chest * 0.88,
    });
    const elbow = bone(shoulder, {
      length: P.forearm, radius: H * 0.020, rings: 2, y: -P.upperArm,
    });
    const hand = bone(elbow, {
      length: P.hand, radius: H * 0.017, rings: 0, y: -P.forearm,
    });
    return { shoulder, elbow, hand };
  }
  function leg(side) {
    const hip = bone(hips, {
      length: P.thigh, radius: H * 0.034, rings: 3, x: side * P.hipHalf, y: -H * 0.03,
    });
    const knee = bone(hip, {
      length: P.shin, radius: H * 0.026, rings: 3, y: -P.thigh,
    });
    const foot = bone(knee, { length: 0, radius: 0, y: -P.shin });
    const footMesh = new THREE.Mesh(
      keep(new THREE.CapsuleGeometry(H * 0.022, P.foot, 5, 12)), skin);
    footMesh.rotation.x = Math.PI / 2;
    footMesh.position.set(0, -H * 0.012, P.foot * 0.45);
    foot.add(footMesh);
    return { hip, knee, foot };
  }
  const arms = { left: arm(-1), right: arm(1) };
  const legs = { left: leg(-1), right: leg(1) };

  // Pose de repos. Bras strictement verticaux = mannequin de vitrine : on
  // écarte légèrement et on fléchit le coude, c'est ce décalage qui fait la
  // différence entre « debout » et « posé ».
  const REST = {
    shoulderZ: 0.10, elbowX: -0.08, hipX: 0.02, kneeX: 0.04,
  };
  arms.left.shoulder.rotation.z = REST.shoulderZ;
  arms.right.shoulder.rotation.z = -REST.shoulderZ;
  arms.left.elbow.rotation.x = REST.elbowX;
  arms.right.elbow.rotation.x = REST.elbowX;

  /* ------------------------------------------------------------ socle */
  const ground = new THREE.Group();
  root.add(ground);
  const ringMaterial = keep(new THREE.LineBasicMaterial({
    color: tint, transparent: true, opacity: 0.35,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const groundRings = [];
  if (opts.projector) {
    for (const [radius, opacity] of [[0.34, 0.45], [0.46, 0.26], [0.60, 0.15]]) {
      const ring = new THREE.LineLoop(keep(circleGeometry()), ringMaterial.clone());
      keep(ring.material);
      ring.material.opacity = opacity;
      ring.scale.set(radius, 1, radius);
      ring.position.y = 0.002;
      ring.userData.base = opacity;
      ring.userData.speed = 0.12 + radius * 0.5;
      ground.add(ring);
      groundRings.push(ring);
    }
    // Le faisceau : un cône ouvert vers le haut, très faible. Il explique d'où
    // vient la projection ; sans lui la silhouette a l'air posée sur rien.
    const beam = new THREE.Mesh(
      keep(new THREE.ConeGeometry(0.34, H * 0.62, 40, 1, true)),
      keep(new THREE.MeshBasicMaterial({
        color: deep, transparent: true, opacity: 0.020, side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })),
    );
    beam.rotation.x = Math.PI;          // pointe au sol, evasement vers le haut
    beam.position.y = H * 0.30;
    ground.add(beam);
  }

  // Balayage : un anneau qui remonte le corps. C'est la signature « projection
  // en cours » — et accessoirement ce qui donne son épaisseur à la silhouette.
  const scanMaterial = keep(new THREE.LineBasicMaterial({
    color: 0xc9f6ff, transparent: true, opacity: 0.30,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const scan = new THREE.LineLoop(keep(circleGeometry()), scanMaterial);
  root.add(scan);

  /* -------------------------------------------------------- composition */
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(1, 1), opts.bloom.strength, opts.bloom.radius, opts.bloom.threshold);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  let framing = FRAMING[opts.framing] || FRAMING.FULL;

  let lastW = 0, lastH = 0;

  function measure() {
    // On mesure le CANVAS lui-meme, pas son parent : dans une mise en page ou
    // le conteneur est dimensionne apres coup, le parent vaut encore 0 au
    // montage et le rendu restait fige sur une bande de 1 pixel de large.
    const el = canvas || host;
    const box = el.getBoundingClientRect();
    let w = Math.round(box.width || el.clientWidth || 0);
    let h = Math.round(box.height || el.clientHeight || 0);
    if (w < 2 || h < 2) {
      const parent = el.parentElement;
      const pb = parent ? parent.getBoundingClientRect() : null;
      if (pb) { w = Math.round(pb.width) || w; h = Math.round(pb.height) || h; }
    }
    return [Math.max(1, w), Math.max(1, h)];
  }

  function resize() {
    const [w, h] = measure();
    lastW = w; lastH = h;
    renderer.setSize(w, h, !canvas);
    composer.setSize(w, h);
    bloom.setSize(w, h);
    camera.aspect = w / h;
    camera.fov = framing.fov;
    // Un cadre étroit coupe les épaules : on recule à mesure que le ratio
    // descend sous 1, plutôt que d'élargir la focale (ce qui déformerait).
    const narrow = Math.min(1, camera.aspect / 0.85);
    camera.position.set(0, framing.lookY + H * 0.06, framing.distance / Math.max(0.55, narrow));
    camera.lookAt(0, framing.lookY, 0);
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas || host);
  if (canvas && canvas.parentElement) observer.observe(canvas.parentElement);
  resize();

  /* ------------------------------------------------------------- états */
  const STATES = {
    IDLE:       { amp: 0.00, motion: 1.00, color: opts.color },
    LISTENING:  { amp: 0.30, motion: 0.70, color: opts.color },   // il se fige pour écouter
    THINKING:   { amp: 0.55, motion: 1.45, color: 0x8fd8ff },
    SPEAKING:   { amp: 0.75, motion: 1.15, color: opts.color },
    WORKING:    { amp: 1.00, motion: 1.80, color: 0x9be8ff },
    ACTING:     { amp: 0.85, motion: 1.60, color: 0x9be8ff },
    SUCCESS:    { amp: 0.70, motion: 1.20, color: 0x7dffc4 },
    WARNING:    { amp: 0.65, motion: 1.30, color: 0xffd479 },
    ERROR:      { amp: 0.60, motion: 1.40, color: 0xff8a9b },
    SLEEPING:   { amp: 0.00, motion: 0.35, color: 0x3f7fae },
  };
  let state = STATES.IDLE;
  let stateName = 'IDLE';
  let amp = 0, motion = 1, pulse = 0;
  let speakLevel = 0, speaking = false;
  const look = { x: 0, y: 0, cx: 0, cy: 0 };   // cible et valeur courante
  let blink = 0, nextBlink = 2.5;

  const clock = new THREE.Clock();
  let raf = 0, running = true, fps = 0, acc = 0, frames = 0;

  function loop() {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(clock.getDelta(), 0.1);   // un onglet réveillé saute des secondes
    const t = clock.elapsedTime;
    uTime.value = t;

    // L'activité monte vite et retombe lentement : se mettre au travail doit
    // se voir tout de suite, s'apaiser peut prendre son temps.
    amp += (state.amp - amp) * Math.min(1, dt * (state.amp > amp ? 4.0 : 0.9));
    motion += (state.motion - motion) * Math.min(1, dt * 2.0);
    pulse *= Math.max(0, 1 - dt * 1.9);
    speakLevel *= Math.max(0, 1 - dt * 6.0);
    const activity = Math.min(1, amp + pulse);
    uAmp.value = activity;

    /* Respiration. Une seule source, la cage thoracique, propagée vers le haut
       par la hiérarchie : les épaules montent parce que le torse monte. */
    const breathRate = 0.55 + activity * 0.45;
    const breath = Math.sin(t * breathRate * Math.PI);
    chest.scale.set(1 + breath * 0.016, 1 + breath * 0.012, 1 + breath * 0.020);
    spine.position.y = breath * H * 0.004;

    /* Report d'appui. Le poids passe d'une jambe à l'autre très lentement —
       c'est ce qui distingue une personne debout d'un mannequin. */
    const shift = Math.sin(t * 0.21) * motion;
    hips.position.x = shift * H * 0.012;
    hips.rotation.z = -shift * 0.035;
    hips.rotation.y = Math.sin(t * 0.17) * 0.07 * motion;
    legs.left.hip.rotation.x = REST.hipX + shift * 0.045;
    legs.right.hip.rotation.x = REST.hipX - shift * 0.045;
    legs.left.knee.rotation.x = REST.kneeX + Math.max(0, shift) * 0.06;
    legs.right.knee.rotation.x = REST.kneeX + Math.max(0, -shift) * 0.06;

    /* Bras. Balancement lent, déphasé entre les deux côtés ; l'activité ouvre
       légèrement les coudes, comme quelqu'un qui s'apprête à agir. */
    const swing = Math.sin(t * 0.42) * motion;
    arms.left.shoulder.rotation.x = 0.05 + swing * 0.10 - activity * 0.14;
    arms.right.shoulder.rotation.x = 0.05 - swing * 0.10 - activity * 0.14;
    arms.left.shoulder.rotation.z = REST.shoulderZ + activity * 0.04 + swing * 0.02;
    arms.right.shoulder.rotation.z = -REST.shoulderZ - activity * 0.04 + swing * 0.02;
    arms.left.elbow.rotation.x = REST.elbowX - activity * 0.30 - Math.max(0, swing) * 0.08;
    arms.right.elbow.rotation.x = REST.elbowX - activity * 0.30 - Math.max(0, -swing) * 0.08;

    /* Regard. Saccade puis fixation : on interpole vite (le mouvement oculaire
       est bref) mais la cible, elle, change rarement. */
    const drift = stateName === 'THINKING' ? 1.6 : 0.5;
    const tx = look.x + Math.sin(t * 0.63) * 0.05 * drift;
    const ty = look.y + Math.sin(t * 0.47) * 0.03 * drift;
    look.cx += (tx - look.cx) * Math.min(1, dt * 7);
    look.cy += (ty - look.cy) * Math.min(1, dt * 7);
    headJoint.rotation.y = look.cx * 0.55;
    headJoint.rotation.x = -look.cy * 0.35;
    chest.rotation.y = look.cx * 0.16;          // le buste suit, en retrait

    /* Clignement. Pas un cycle régulier — un intervalle aléatoire, sinon l'oeil
       le perçoit comme un tic mécanique. */
    nextBlink -= dt;
    if (nextBlink <= 0) { blink = 1; nextBlink = 2.2 + Math.random() * 3.6; }
    blink = Math.max(0, blink - dt * 9);
    const lid = 1 - blink;
    for (const eye of eyes) eye.scale.y = Math.max(0.05, lid);
    glowMaterial.opacity = 0.65 + activity * 0.30;

    /* Parole. La bande de bouche ne vit QUE sur un niveau audio réel : rien
       n'est joué à vide, donc un silence se voit immédiatement. */
    const talk = speaking ? Math.max(speakLevel, 0.12) : speakLevel;
    mouth.material.opacity = Math.min(0.95, talk * 1.6);
    mouth.scale.set(1 + talk * 0.35, Math.max(0.2, talk * 4.0), 1);

    // Coeur : battement de fond + parole.
    heartMaterial.opacity = 0.35 + activity * 0.35 + Math.sin(t * 2.4) * 0.06 + talk * 0.25;
    heart.scale.setScalar(H * (0.075 + activity * 0.025 + talk * 0.02));

    // Balayage vertical, avec un rayon qui suit la largeur du corps.
    const y = ((t * (0.22 + activity * 0.30)) % 1) * H;
    const width = 0.055 + 0.085 * Math.sin(Math.min(1, y / H) * Math.PI);
    scan.position.y = y;
    scan.scale.set(width * 1.6, 1, width * 1.6);
    scanMaterial.opacity = 0.10 + activity * 0.22;

    for (const ring of groundRings) {
      ring.rotation.y += dt * ring.userData.speed * motion;
      ring.material.opacity = ring.userData.base * (0.7 + activity * 0.5);
    }

    composer.render();

    acc += dt; frames += 1;
    if (acc >= 0.5) {
      fps = Math.round(frames / acc); acc = 0; frames = 0;
      const [w, h] = measure();
      if (Math.abs(w - lastW) > 1 || Math.abs(h - lastH) > 1) resize();
    }
  }
  raf = requestAnimationFrame(loop);

  function applyColor(hex) {
    const color = new THREE.Color(hex);
    tint.copy(color);
    wire.color.copy(color);
    ringMaterial.color.copy(color);
    for (const ring of groundRings) ring.material.color.copy(color);
    heartMaterial.color.copy(color);
  }

  function dispose() {
    running = false;
    cancelAnimationFrame(raf);
    observer.disconnect();
    for (const item of disposables) item.dispose?.();
    bloom.dispose();
    composer.renderTarget1?.dispose();
    composer.renderTarget2?.dispose();
    renderer.dispose();
    renderer.forceContextLoss?.();
    if (!canvas) renderer.domElement.remove();
  }

  return {
    scene, camera, renderer, composer, root, canvas: renderer.domElement,
    get fps() { return fps; },
    get state() { return stateName; },

    /** États du bus JARVIS : voix, chat, agents. */
    setState(name) {
      const key = String(name || '').toUpperCase();
      const next = STATES[key];
      if (!next) return;
      if (next !== state) pulse = 0.5;       // le changement lui-même se voit
      state = next;
      stateName = key;
      speaking = key === 'SPEAKING';
      if (!speaking) speakLevel = 0;
      applyColor(next.color);
    },
    /** Pilotage continu, 0 = calme, 1 = actif. */
    setActivity(level) {
      const value = Math.max(0, Math.min(1, Number(level) || 0));
      state = { ...state, amp: value, motion: 1 + value * 0.9 };
    },
    /** Amplitude TTS réelle, 0..1 — c'est elle qui anime la bouche. */
    setAudioLevel(level) {
      speakLevel = Math.max(0, Math.min(1, Number(level) || 0));
      if (speakLevel > 0.02) speaking = true;
    },
    speak(_text, _options) { speaking = true; },
    stopSpeaking() { speaking = false; speakLevel = 0; },
    /** Cible du regard en coordonnées normalisées (-1..1). */
    lookAt(point) {
      if (!point) { look.x = 0; look.y = 0; return; }
      look.x = Math.max(-1, Math.min(1, Number(point.x) || 0));
      look.y = Math.max(-1, Math.min(1, Number(point.y) || 0));
    },
    /** Réaction ponctuelle : une impulsion qui retombe seule. */
    gesture() { pulse = 1; return true; },
    /** FULL | HALF_BODY | HEAD */
    setViewMode(mode) {
      const next = FRAMING[String(mode || '').toUpperCase()];
      if (!next) return false;
      framing = next;
      resize();
      return true;
    },
    setCameraMode(mode) { return this.setViewMode(mode); },
    setBloom({ strength, radius, threshold } = {}) {
      if (strength !== undefined) bloom.strength = strength;
      if (radius !== undefined) bloom.radius = radius;
      if (threshold !== undefined) bloom.threshold = threshold;
    },
    // Présents pour rester interchangeable avec les viewers GLB, dont la page
    // d'accueil appelle l'interface complète.
    setQuality() {},
    setAutoRotate() {},
    recenter() { look.x = 0; look.y = 0; },
    moveTo() { return false; },
    pause() { running = false; cancelAnimationFrame(raf); },
    resume() { if (!running) { running = true; clock.getDelta(); raf = requestAnimationFrame(loop); } },
    resize,
    attachTo(el) {
      if (!el || canvas) return;
      if (renderer.domElement.parentElement !== el) el.appendChild(renderer.domElement);
      observer.disconnect();
      observer.observe(el);
      observer.observe(renderer.domElement);
      resize();
    },
    dispose,
  };
}

export default createHoloHumanoid;
