/* ==========================================================================
   JARVIS — Buste holographique en LIGNES DE FLUX

   Le buste (tête, cou, haut des épaules) n'est ni un maillage ni un nuage de
   points : c'est un faisceau de ~150 méridiens tracés SUR la surface du corps,
   façon lignes de champ magnétique. Ils partent tous du noyau, à la base de la
   gorge, s'écartent aux épaules et se referment à la couronne du crâne.

   Pourquoi des lignes plutôt qu'un nuage : un nuage de points rend une masse,
   une brume — il décrit un VOLUME. Une ligne décrit une DIRECTION. C'est la
   direction qui fait lire « champ d'énergie » plutôt que « statue en
   poussière », et c'est elle qui donne à l'animation de démarrage quelque chose
   à parcourir : le déploiement voyage le long de chaque ligne au lieu de tout
   allumer d'un coup.

   Deux familles, et la raison de leur séparation :
     · CRÂNE — le cou, puis un méridien de l'ellipsoïde, du menton à la couronne
     · TORSE — de la base du cou, par-dessus le deltoïde, jusqu'à la coupe basse
   Une seule famille ne peut pas faire les deux : à une hauteur donnée, le cou
   et l'épaule sont à deux rayons différents, une ligne unique devrait sauter.

       const core = await createParticleHumanoid({ canvas });
       core.triggerStartupAnimation();
       core.triggerSystemCheckAnimation();

   L'interface publique est inchangée (setState, setActivity, setAudioLevel,
   lookAt, gesture, step, pause, resume, resize, attachTo, dispose) : les deux
   surfaces qui montent ce module ne voient pas la refonte.
   ========================================================================== */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/EffectComposer.js';
import { RenderPass } from 'three/addons/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/OutputPass.js';
import { ScanAudio } from './scan_audio.js';

const PIXEL_RATIO = Math.min(globalThis.devicePixelRatio || 1, 1.75);

const DEFAULTS = {
  headLines: 60,             // méridiens du crâne
  bodyLines: 92,             // méridiens du torse
  headSamples: 74,
  bodySamples: 88,
  cyan: 0x00f0ff,            // ligne de flux, face caméra
  deep: 0x072c55,            // ligne de flux, face opposée
  hot: 0xff8800,             // noyau, crête de pulsation
  hotDeep: 0xff3300,         // noyau, creux de pulsation
  bloom: { strength: 1.05, radius: 0.80, threshold: 0.22 },
  framing: 'BUST',
  audio: true,
};

/* Cadrages : on change la distance et la hauteur visée, jamais la focale, pour
   que la perspective ne saute pas d'un cadrage à l'autre. */
const FRAMING = {
  WIDE: { distance: 5.60, lookY: -0.05, fov: 34 },
  BUST: { distance: 4.35, lookY: -0.05, fov: 32 },
  HEAD: { distance: 1.95, lookY: 0.60, fov: 30 },
};

/* Le noyau : base de la gorge, légèrement en avant du plan du torse. C'est le
   point d'où partent les lignes ET le foyer des ondes du diagnostic — les deux
   doivent coïncider, sinon le scan ne semble pas émaner de la source. */
const CORE = new THREE.Vector3(0, 0.04, 0.07);

/* Durées de la séquence de diagnostic, en secondes depuis le déclenchement. */
const CHECK = {
  droneAt: 0.55,
  waveEvery: 0.85,
  lastWaveAt: 3.40,
  validateAt: 5.40,
  endAt: 6.20,
  waveSpeed: 1.85,
  waveMax: 3.40,
};

const STARTUP_DURATION = 2.5;
const MAX_WAVES = 3;

/* -------------------------------------------------------------------------
   Anatomie

   Le torse est un loft (demi-largeur, profondeur le long de Y) ; la tête un
   ellipsoïde dont on resserre la moitié basse pour la mâchoire. Le cou fait la
   jonction, et son rayon haut est calé sur celui du bas du crâne : sans ça les
   méridiens du cou et ceux de la tête ne se raccordent pas, et le buste montre
   un décrochement net à la gorge.
   ------------------------------------------------------------------------- */

const TORSO_SECTIONS = [
  //  y      demi-largeur  profondeur
  [ 0.38,    0.165,        0.130],   // base du cou
  [ 0.30,    0.230,        0.155],   // trapèzes : la pente part d'ici
  [ 0.20,    0.330,        0.190],
  [ 0.10,    0.450,        0.220],
  [ 0.00,    0.540,        0.240],
  [-0.12,    0.575,        0.252],   // deltoïdes, point le plus large
  [-0.35,    0.525,        0.262],
  [-0.70,    0.500,        0.262],
  [-1.00,    0.465,        0.250],   // coupe basse du buste
];

const HEAD_CENTER = new THREE.Vector3(0, 0.60, 0);
const HEAD_RADII = new THREE.Vector3(0.252, 0.345, 0.272);
const NECK_BOTTOM = 0.12;
const NECK_TOP = 0.38;
/* Angle polaire (mesuré depuis la couronne) où l'ellipsoïde atteint le haut du
   cou : c'est là que la famille CRÂNE quitte le cou pour le méridien. */
const HEAD_PHI_START = Math.acos((NECK_TOP - HEAD_CENTER.y) / HEAD_RADII.y);
const HEAD_PHI_END = 0.075;          // on s'arrête avant le pôle exact

function torsoAt(y) {
  const s = TORSO_SECTIONS;
  if (y >= s[0][0]) return { hw: s[0][1], d: s[0][2], slope: 0 };
  for (let i = 0; i < s.length - 1; i += 1) {
    const [y0, hw0, d0] = s[i];
    const [y1, hw1, d1] = s[i + 1];
    if (y <= y0 && y >= y1) {
      const t = (y0 - y) / (y0 - y1);
      return {
        hw: hw0 + (hw1 - hw0) * t,
        d: d0 + (d1 - d0) * t,
        // Pente de la surface : elle redresse la normale sur les épaules, là où
        // le profil change le plus vite.
        slope: (hw1 - hw0) / (y1 - y0),
      };
    }
  }
  const last = s[s.length - 1];
  return { hw: last[1], d: last[2], slope: 0 };
}

/** Resserrement de la mâchoire : d'autant plus fort qu'on descend sous l'équateur. */
function jawTaper(cosPhi) {
  return 1 - 0.42 * Math.pow(Math.max(0, -cosPhi), 1.5);
}

/** Rayon du crâne au raccord avec le cou — sert à caler le haut du cou dessus. */
const HEAD_JOIN_RADIUS = HEAD_RADII.x
  * Math.sin(HEAD_PHI_START)
  * jawTaper(Math.cos(HEAD_PHI_START));

/* -------------------------------------------------------------------------
   Tracé des lignes

   Chaque ligne est une suite d'échantillons {p, n}. Un léger vrillage (`swirl`)
   tourne l'azimut le long du parcours : sans lui les méridiens sont des
   verticales parfaites et l'ensemble lit « cage », pas « flux ».
   ------------------------------------------------------------------------- */

function headLine(theta0, swirl, samples) {
  const out = [];
  const NECK_PART = 0.30;              // part du parcours passée dans le cou
  for (let k = 0; k < samples; k += 1) {
    const s = k / (samples - 1);
    const theta = theta0 + swirl * s;
    const ct = Math.cos(theta);
    const st = Math.sin(theta);
    let p;
    let n;
    if (s < NECK_PART) {
      const u = s / NECK_PART;
      const y = NECK_BOTTOM + (NECK_TOP - NECK_BOTTOM) * u;
      // Rayon interpolé jusqu'au rayon de raccord du crâne : continuité.
      const r = 0.172 + (HEAD_JOIN_RADIUS - 0.172) * u;
      p = new THREE.Vector3(ct * r, y, st * r * 0.94);
      n = new THREE.Vector3(ct, 0.10, st).normalize();
    } else {
      const u = (s - NECK_PART) / (1 - NECK_PART);
      // On remonte le méridien : du raccord (phi grand) vers la couronne.
      const phi = HEAD_PHI_START + (HEAD_PHI_END - HEAD_PHI_START) * u;
      const sp = Math.sin(phi);
      const cp = Math.cos(phi);
      const jaw = jawTaper(cp);
      p = new THREE.Vector3(
        HEAD_CENTER.x + sp * ct * HEAD_RADII.x * jaw,
        HEAD_CENTER.y + cp * HEAD_RADII.y,
        HEAD_CENTER.z + sp * st * HEAD_RADII.z * jaw,
      );
      n = new THREE.Vector3(
        (sp * ct) / HEAD_RADII.x,
        cp / HEAD_RADII.y,
        (sp * st) / HEAD_RADII.z,
      ).normalize();
    }
    out.push({ p, n });
  }
  return out;
}

function bodyLine(theta0, swirl, samples) {
  const out = [];
  for (let k = 0; k < samples; k += 1) {
    // Biais vers le haut : les épaules portent la lecture, la coupe basse est
    // presque hors cadre et n'a pas besoin de la même densité d'échantillons.
    const s = Math.pow(k / (samples - 1), 0.85);
    const y = NECK_TOP - s * (NECK_TOP + 1.0);
    const theta = theta0 + swirl * s;
    const ct = Math.cos(theta);
    const st = Math.sin(theta);
    const { hw, d, slope } = torsoAt(y);
    out.push({
      p: new THREE.Vector3(ct * hw, y, st * d),
      n: new THREE.Vector3(ct * d, -slope * hw * 0.35, st * hw).normalize(),
    });
  }
  return out;
}

/**
 * Convertit les lignes en LineSegments. Chaque échantillon intérieur est
 * dupliqué : c'est le prix d'un LineSegments, mais il rend chaque segment
 * indépendant, donc coupable proprement par le fondu de la coupe basse.
 */
function buildGeometry(opt) {
  const lines = [];

  for (let i = 0; i < opt.headLines; i += 1) {
    const theta = (i / opt.headLines) * Math.PI * 2 + (Math.random() - 0.5) * 0.03;
    lines.push({
      pts: headLine(theta, 0.38 + Math.random() * 0.16, opt.headSamples),
      seed: Math.random(),
    });
  }
  for (let i = 0; i < opt.bodyLines; i += 1) {
    const theta = (i / opt.bodyLines) * Math.PI * 2 + (Math.random() - 0.5) * 0.03;
    lines.push({
      pts: bodyLine(theta, -0.62 - Math.random() * 0.26, opt.bodySamples),
      seed: Math.random(),
    });
  }

  let segments = 0;
  for (const line of lines) segments += line.pts.length - 1;
  const count = segments * 2;

  const position = new Float32Array(count * 3);
  const normal = new Float32Array(count * 3);
  const along = new Float32Array(count);     // 0 = racine (noyau), 1 = extrémité
  const seed = new Float32Array(count);
  let v = 0;

  const push = (sample, t, s) => {
    position[v * 3] = sample.p.x;
    position[v * 3 + 1] = sample.p.y;
    position[v * 3 + 2] = sample.p.z;
    normal[v * 3] = sample.n.x;
    normal[v * 3 + 1] = sample.n.y;
    normal[v * 3 + 2] = sample.n.z;
    along[v] = t;
    seed[v] = s;
    v += 1;
  };

  for (const line of lines) {
    const n = line.pts.length;
    for (let k = 0; k < n - 1; k += 1) {
      push(line.pts[k], k / (n - 1), line.seed);
      push(line.pts[k + 1], (k + 1) / (n - 1), line.seed);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
  geometry.setAttribute('aNormal', new THREE.BufferAttribute(normal, 3));
  geometry.setAttribute('aAlong', new THREE.BufferAttribute(along, 1));
  geometry.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 5);
  return { geometry, segments };
}

/* -------------------------------------------------------------------------
   Shaders
   ------------------------------------------------------------------------- */

/* Bruit de Perlin (gradient) 3D.

   Honnêteté sur le nom : le champ de déplacement ci-dessous échantillonne ce
   bruit trois fois à des offsets décorrélés — ce n'est PAS un curl noise à
   divergence nulle. Un vrai rotationnel demande un potentiel vectoriel dérivé
   par différences finies, soit ~18 évaluations de bruit par sommet ; sur
   24 000 sommets c'est le poste le plus cher du frame, pour une oscillation
   lente que l'œil ne distingue pas du champ ci-dessous. */
const NOISE_GLSL = /* glsl */`
  vec3 hash3(vec3 p) {
    p = vec3(dot(p, vec3(127.1, 311.7, 74.7)),
             dot(p, vec3(269.5, 183.3, 246.1)),
             dot(p, vec3(113.5, 271.9, 124.6)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
  }

  float perlin(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    vec3 u = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(dot(hash3(i + vec3(0.0, 0.0, 0.0)), f - vec3(0.0, 0.0, 0.0)),
              dot(hash3(i + vec3(1.0, 0.0, 0.0)), f - vec3(1.0, 0.0, 0.0)), u.x),
          mix(dot(hash3(i + vec3(0.0, 1.0, 0.0)), f - vec3(0.0, 1.0, 0.0)),
              dot(hash3(i + vec3(1.0, 1.0, 0.0)), f - vec3(1.0, 1.0, 0.0)), u.x), u.y),
      mix(mix(dot(hash3(i + vec3(0.0, 0.0, 1.0)), f - vec3(0.0, 0.0, 1.0)),
              dot(hash3(i + vec3(1.0, 0.0, 1.0)), f - vec3(1.0, 0.0, 1.0)), u.x),
          mix(dot(hash3(i + vec3(0.0, 1.0, 1.0)), f - vec3(0.0, 1.0, 1.0)),
              dot(hash3(i + vec3(1.0, 1.0, 1.0)), f - vec3(1.0, 1.0, 1.0)), u.x), u.y),
      u.z);
  }

  vec3 flowField(vec3 p, float t) {
    vec3 q = p * 2.4 + vec3(0.0, t * 0.22, 0.0);
    return vec3(
      perlin(q),
      perlin(q + vec3(41.3, 17.9, 63.1)),
      perlin(q + vec3(93.7, 55.1, 28.4))
    );
  }
`;

const VERTEX = /* glsl */`
  attribute vec3 aNormal;
  attribute float aAlong;
  attribute float aSeed;

  uniform float uTime;
  uniform float uForm;
  uniform float uAmp;
  uniform float uPulse;
  uniform vec3  uCore;
  uniform vec3  uWaveR;
  uniform vec3  uWaveS;

  varying float vRim;
  varying float vAlong;
  varying float vHit;
  varying float vLead;
  varying float vFade;
  varying float vGrow;

  ${NOISE_GLSL}

  float band(float d, float radius, float strength) {
    if (strength <= 0.001) return 0.0;
    float x = (d - radius) / 0.13;
    return exp(-x * x) * strength;
  }

  void main() {
    // Déploiement : la tête de croissance remonte la ligne. Le retard de 0.55
    // fait que les racines sont déjà sorties quand les extrémités démarrent —
    // sinon toutes les lignes s'allument ensemble et il n'y a plus de geste.
    float grow = clamp(uForm * 1.55 - aAlong * 0.55, 0.0, 1.0);
    grow = smoothstep(0.0, 1.0, grow);

    // Le jaillissement suit un ARC, pas un rayon : Bézier quadratique dont le
    // point de contrôle est poussé vers le haut. C'est ce qui donne « en arc de
    // cercle vers le haut et les côtés » plutôt qu'une explosion radiale.
    vec3 d = position - uCore;
    vec3 ctrl = uCore + d * 0.45 + vec3(0.0, 0.42, 0.0);
    float g = grow;
    float ig = 1.0 - g;
    vec3 p = ig * ig * uCore + 2.0 * ig * g * ctrl + g * g * position;

    // Respiration du champ. L'amplitude suit grow : une ligne pas encore
    // déployée n'a pas à frémir.
    float amp = (0.012 + uAmp * 0.020 + uPulse * 0.022) * grow;
    p += flowField(position + aSeed * 7.0, uTime) * amp;

    // Souffle lent, sur le torse seulement.
    float breath = sin(uTime * 0.85 + aSeed) * 0.007 * grow;
    p.xz *= 1.0 + breath * smoothstep(0.35, -0.6, position.y);

    float dist = distance(p, uCore);
    float hit = band(dist, uWaveR.x, uWaveS.x)
              + band(dist, uWaveR.y, uWaveS.y)
              + band(dist, uWaveR.z, uWaveS.z);
    hit = min(hit, 1.0);
    p += aNormal * hit * 0.05;

    vec3 vn = normalize(normalMatrix * aNormal);
    // Une ligne tournée vers la caméra doit être la plus lisible. L'ancienne
    // formule faisait l'inverse: le front du buste devenait bleu sombre et
    // seuls les contours saturaient en cyan.
    vRim = pow(abs(vn.z), 0.72);
    vAlong = aAlong;
    vHit = hit;
    vGrow = grow;
    // Front de croissance : un éclat qui passe et retombe, propre à chaque
    // sommet au moment où il arrive à sa place.
    vLead = smoothstep(0.0, 0.18, grow) * (1.0 - smoothstep(0.18, 0.62, grow));
    vFade = smoothstep(-1.02, -0.62, position.y);

    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const FRAGMENT = /* glsl */`
  precision highp float;

  uniform vec3 uCyan;
  uniform vec3 uDeep;
  uniform vec3 uHot;
  uniform float uScan;

  varying float vRim;
  varying float vAlong;
  varying float vHit;
  varying float vLead;
  varying float vFade;
  varying float vGrow;

  void main() {
    // Profondeur : la face opposée vire au bleu sombre. C'est le seul indice de
    // volume dont on dispose, les lignes n'ayant ni ombre ni épaisseur.
    vec3 color = mix(uDeep, uCyan, clamp(vRim * 1.6, 0.0, 1.0));

    // Les lignes s'éteignent vers leurs extrémités : elles semblent alimentées
    // par le noyau au lieu de flotter librement.
    float feed = 1.0 - smoothstep(0.60, 1.0, vAlong) * 0.40;

    // Front de déploiement : blanc, mais teinté par la couleur du noyau qu'il
    // vient de quitter.
    color = mix(color, mix(vec3(1.0), uHot, 0.35), vLead * 0.85);
    // Passage d'onde : blanc franc.
    color = mix(color, vec3(1.0), clamp(vHit * 0.75, 0.0, 0.9));

    float alpha = (0.22 + vRim * 0.70) * feed * vFade * vGrow;
    alpha += vLead * 0.55 + vHit * 0.5;
    alpha *= 0.80 + uScan * 0.20;

    gl_FragColor = vec4(color * (1.0 + vHit * 0.6 + vLead * 0.5),
                        clamp(alpha, 0.0, 1.0));
  }
`;

/* Noyau : une sphère additive au centre saturé et au bord transparent, doublée
   d'un halo creux plus large. Deux sphères valent mieux qu'un sprite : elles
   gardent leur place dans la profondeur quand le buste pivote. */
const CORE_VERTEX = /* glsl */`
  varying vec3 vNrm;
  void main() {
    vNrm = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const HALO_FRAGMENT = /* glsl */`
  precision highp float;
  uniform vec3 uHot;
  uniform vec3 uHotDeep;
  uniform float uBeat;
  uniform float uOpacity;
  varying vec2 vUv;
  void main() {
    // Decroissance gaussienne : le halo n'a pas de bord, donc pas de disque.
    float r = length(vUv * 2.0 - 1.0);
    float a = exp(-r * r * 5.5) * uOpacity;
    vec3 color = mix(uHotDeep, uHot, uBeat);
    gl_FragColor = vec4(color, a);
  }
`;

const CORE_FRAGMENT = /* glsl */`
  precision highp float;
  uniform vec3 uHot;
  uniform vec3 uHotDeep;
  uniform float uBeat;
  uniform float uOpacity;
  uniform float uEdge;      // 0 = orbe pleine, 1 = halo creux
  varying vec3 vNrm;
  void main() {
    float face = abs(vNrm.z);                 // 1 au centre du disque apparent
    float core = pow(face, 2.2);
    float halo = pow(1.0 - face, 2.0);
    float shape = mix(core, halo, uEdge);
    vec3 color = mix(uHotDeep, uHot, uBeat);
    gl_FragColor = vec4(color * (1.0 + core * uBeat), shape * uOpacity);
  }
`;

const RING_VERTEX = /* glsl */`
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const RING_FRAGMENT = /* glsl */`
  precision highp float;
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uWidth;
  varying vec2 vUv;
  void main() {
    float r = length(vUv * 2.0 - 1.0);
    if (r > 1.2) discard;
    float x = (r - 1.0) / uWidth;
    float band = exp(-x * x);
    // Traîne interne : l'onde laisse un sillage derrière son front.
    float trail = smoothstep(1.0, 0.55, r) * 0.07 * step(r, 1.0);
    gl_FragColor = vec4(uColor * (1.0 + band), (band + trail) * uOpacity);
  }
`;

/* ========================================================================== */

export async function createParticleHumanoid(options = {}) {
  const opt = {
    ...DEFAULTS,
    ...options,
    bloom: { ...DEFAULTS.bloom, ...(options.bloom || {}) },
  };
  const canvas = opt.canvas || null;
  const host = opt.container || canvas?.parentElement || document.body;

  const renderer = new THREE.WebGLRenderer({
    canvas: canvas || undefined,
    antialias: true,             // des lignes de 1 px : l'AA change tout ici
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(PIXEL_RATIO);
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  if (!canvas) host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  let framing = FRAMING[String(opt.framing).toUpperCase()] || FRAMING.BUST;
  const camera = new THREE.PerspectiveCamera(framing.fov, 1, 0.1, 100);

  const root = new THREE.Group();
  scene.add(root);

  /* --- Lignes de flux ----------------------------------------------------- */
  const { geometry, segments } = buildGeometry(opt);
  const uniforms = {
    uTime: { value: 0 },
    uForm: { value: 0 },
    uAmp: { value: 0.15 },
    uPulse: { value: 0 },
    uCore: { value: CORE.clone() },
    uWaveR: { value: new THREE.Vector3(0, 0, 0) },
    uWaveS: { value: new THREE.Vector3(0, 0, 0) },
    uCyan: { value: new THREE.Color(opt.cyan) },
    uDeep: { value: new THREE.Color(opt.deep) },
    uHot: { value: new THREE.Color(opt.hot) },
    uScan: { value: 0 },
  };

  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    toneMapped: false,
  });

  const flow = new THREE.LineSegments(geometry, material);
  flow.frustumCulled = false;
  root.add(flow);

  /* --- Noyau -------------------------------------------------------------- */
  const beat = { value: 0.5 };
  const coreUniforms = {
    uHot: { value: new THREE.Color(opt.hot) },
    uHotDeep: { value: new THREE.Color(opt.hotDeep) },
    uBeat: beat,
    uOpacity: { value: 0 },
    uEdge: { value: 0 },
  };
  const haloUniforms = {
    uHot: { value: new THREE.Color(opt.hot) },
    uHotDeep: { value: new THREE.Color(opt.hotDeep) },
    uBeat: beat,
    uOpacity: { value: 0 },
  };

  const orbGeometry = new THREE.SphereGeometry(0.075, 24, 18);
  // Le halo est un PLAN face caméra, pas une sphère : le bord géométrique d'une
  // sphère se découpe net et l'orbe lit comme un disque collé sur l'image.
  const haloGeometry = new THREE.PlaneGeometry(1.05, 1.05);
  const makeCoreMaterial = (u, fragment, vertex) => new THREE.ShaderMaterial({
    uniforms: u,
    vertexShader: vertex,
    fragmentShader: fragment,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    toneMapped: false,
  });
  const orb = new THREE.Mesh(orbGeometry, makeCoreMaterial(coreUniforms, CORE_FRAGMENT, CORE_VERTEX));
  const halo = new THREE.Mesh(haloGeometry, makeCoreMaterial(haloUniforms, HALO_FRAGMENT, RING_VERTEX));
  orb.position.copy(CORE);
  halo.position.copy(CORE);
  orb.frustumCulled = false;
  halo.frustumCulled = false;
  root.add(orb, halo);

  /* --- Anneaux sonar ------------------------------------------------------ */
  const ringGeometry = new THREE.PlaneGeometry(2, 2);
  const rings = [];
  for (let k = 0; k < MAX_WAVES; k += 1) {
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(opt.cyan) },
        uOpacity: { value: 0 },
        uWidth: { value: 0.03 },
      },
      vertexShader: RING_VERTEX,
      fragmentShader: RING_FRAGMENT,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      toneMapped: false,
    });
    const mesh = new THREE.Mesh(ringGeometry, mat);
    mesh.position.copy(CORE);
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.renderOrder = -1;
    root.add(mesh);
    rings.push({ mesh, mat, active: false, radius: 0 });
  }

  /* --- Post-traitement ---------------------------------------------------- */
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(1, 1),
    opt.bloom.strength, opt.bloom.radius, opt.bloom.threshold,
  );
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const audio = opt.audioManager || (opt.audio ? new ScanAudio() : null);
  const ownsAudio = !opt.audioManager && Boolean(audio);

  /* --- Dimensions --------------------------------------------------------- */
  function measure() {
    const el = canvas || renderer.domElement;
    const box = (el.parentElement || el).getBoundingClientRect();
    return [Math.max(1, Math.round(box.width)), Math.max(1, Math.round(box.height))];
  }

  let lastW = 0;
  let lastH = 0;

  function resize() {
    const [w, h] = measure();
    lastW = w; lastH = h;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    bloom.setSize(w, h);
    camera.aspect = w / h;
    camera.fov = framing.fov;
    // Sur une fenêtre étroite, on recule pour ne pas décapiter le buste.
    const pull = camera.aspect < 1 ? (1 / camera.aspect) * 0.55 : 0;
    camera.position.set(0, framing.lookY + 0.08, framing.distance + pull);
    camera.lookAt(0, framing.lookY, 0);
    camera.updateProjectionMatrix();
  }

  const observer = new ResizeObserver(() => resize());
  observer.observe((canvas || renderer.domElement).parentElement || renderer.domElement);
  resize();

  /* --- État --------------------------------------------------------------- */
  const clock = new THREE.Clock();
  let raf = 0;
  let running = true;
  let fps = 0;
  let acc = 0;
  let frames = 0;

  let manualTime = 0;           // horloge de `step()` piloté à la main
  let form = 0;                 // 0..1, position dans l'animation de démarrage
  let formTarget = 0;
  let startupT = -1;            // < 0 = pas de démarrage en cours
  let checkT = -1;              // < 0 = pas de diagnostic en cours
  let nextWaveAt = 0;
  let dronePlayed = false;
  let validatePlayed = false;
  let droneHandle = null;
  let scanLevel = 0;
  let pulse = 0;
  let activity = 0.15;
  let speakLevel = 0;
  let status = 'STANDBY';

  const look = { x: 0, y: 0 };
  const onStatus = typeof opt.onStatus === 'function' ? opt.onStatus : null;

  function setStatus(next) {
    if (next === status) return;
    status = next;
    onStatus?.(next);
  }

  function emitWave() {
    // On recycle le plus vieil anneau si les trois sont pris : la cadence
    // d'émission est plus rapide que la durée de vie d'une onde.
    let slot = rings.find((r) => !r.active);
    if (!slot) slot = rings.reduce((a, b) => (a.radius > b.radius ? a : b));
    slot.active = true;
    slot.radius = 0.12;
    slot.mesh.visible = true;
  }

  function stopDrone() {
    droneHandle?.stop?.();
    droneHandle = null;
  }

  /* --- Une image ----------------------------------------------------------
     Séparé de la boucle rAF exprès : toute la chronologie (démarrage, ondes,
     hooks audio) devient pilotable au pas, donc testable — et rejouable à
     l'identique quand l'onglet a été bridé.
     ----------------------------------------------------------------------- */
  function step(dt, t) {
    uniforms.uTime.value = t;

    if (startupT >= 0) {
      startupT += dt;
      const p = Math.min(1, startupT / STARTUP_DURATION);
      // Palier : le noyau seul vacille ~0.5 s, puis les lignes jaillissent.
      formTarget = p < 0.2 ? p * 0.05 : 0.010 + Math.pow((p - 0.2) / 0.8, 0.82) * 0.990;
      if (p >= 1) {
        startupT = -1;
        formTarget = 1;
        pulse = 0.8;
        setStatus('IDLE');
      }
    }
    form += (formTarget - form) * Math.min(1, dt * 14);
    uniforms.uForm.value = form;

    if (checkT >= 0) {
      const prev = checkT;
      checkT += dt;
      if (prev < CHECK.droneAt && checkT >= CHECK.droneAt && !dronePlayed) {
        dronePlayed = true;
        droneHandle = audio?.playDroneScan({
          duration: CHECK.validateAt - CHECK.droneAt + 0.3,
        }) || null;
      }
      if (checkT >= nextWaveAt && nextWaveAt <= CHECK.lastWaveAt) {
        emitWave();
        nextWaveAt += CHECK.waveEvery;
      }
      if (prev < CHECK.validateAt && checkT >= CHECK.validateAt && !validatePlayed) {
        validatePlayed = true;
        stopDrone();
        audio?.playValidationTone();
        pulse = 1;
        setStatus('ALL_CLEAR');
      }
      // +1.2 s : la dernière onde met ce temps-là à finir sa course, et sans ça
      // les lignes redeviennent froides pendant qu'elle les traverse.
      const target = checkT < CHECK.lastWaveAt + 1.2 ? 1 : 0;
      scanLevel += (target - scanLevel) * Math.min(1, dt * 3.2);
      if (checkT >= CHECK.endAt) {
        checkT = -1;
        stopDrone();
        setStatus(form > 0.9 ? 'IDLE' : 'STANDBY');
      }
    } else {
      scanLevel += (0 - scanLevel) * Math.min(1, dt * 2.5);
    }
    uniforms.uScan.value = scanLevel;

    const wr = uniforms.uWaveR.value;
    const ws = uniforms.uWaveS.value;
    wr.set(0, 0, 0); ws.set(0, 0, 0);
    rings.forEach((ring, k) => {
      if (!ring.active) { ring.mesh.visible = false; return; }
      ring.radius += CHECK.waveSpeed * dt;
      if (ring.radius > CHECK.waveMax) {
        ring.active = false;
        ring.mesh.visible = false;
        ring.mat.uniforms.uOpacity.value = 0;
        return;
      }
      // L'onde s'éteint en s'éloignant : sans ça les trois anneaux ont le même
      // poids visuel et la profondeur du balayage disparaît.
      const strength = Math.pow(1 - ring.radius / CHECK.waveMax, 1.4);
      if (k === 0) { wr.x = ring.radius; ws.x = strength; }
      else if (k === 1) { wr.y = ring.radius; ws.y = strength; }
      else { wr.z = ring.radius; ws.z = strength; }

      ring.mesh.scale.setScalar(ring.radius);
      ring.mesh.quaternion.copy(camera.quaternion);   // billboard
      ring.mat.uniforms.uOpacity.value = strength * 0.30;
      ring.mat.uniforms.uWidth.value = 0.022 + 0.030 * (ring.radius / CHECK.waveMax);
    });

    pulse = Math.max(0, pulse - dt * 1.6);
    uniforms.uPulse.value = pulse;
    uniforms.uAmp.value = Math.max(activity, speakLevel);

    // Le noyau PRÉCÈDE le corps : au tout début du démarrage il vacille seul,
    // d'où l'opacité qui monte bien avant `form`.
    const ignite = Math.min(1, Math.max(form * 6.0, startupT >= 0 ? 0.55 : 0));
    const flicker = startupT >= 0 && form < 0.12
      ? 0.55 + 0.45 * Math.sin(t * 34.0) * Math.sin(t * 11.0)
      : 1;
    beat.value = Math.min(1.4, 0.5 + 0.5 * Math.sin(t * 2.3) + pulse * 0.3);
    coreUniforms.uOpacity.value = 0.95 * ignite * flicker * (1 + scanLevel * 0.3);
    haloUniforms.uOpacity.value = 0.42 * ignite * flicker * (1 + scanLevel * 0.5);
    orb.scale.setScalar(1 + pulse * 0.35 + scanLevel * 0.12);
    halo.scale.setScalar(1 + pulse * 0.20 + scanLevel * 0.25);
    halo.quaternion.copy(camera.quaternion);

    // Léger balancement + suivi du regard : un buste parfaitement immobile se
    // lit comme une maquette.
    root.rotation.y += ((look.x * 0.28 + Math.sin(t * 0.32) * 0.05) - root.rotation.y)
      * Math.min(1, dt * 2.4);
    root.rotation.x += ((-look.y * 0.16 + Math.sin(t * 0.27) * 0.02) - root.rotation.x)
      * Math.min(1, dt * 2.4);

    bloom.strength = opt.bloom.strength + scanLevel * 0.22 + pulse * 0.18;

    composer.render();
  }

  function loop() {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.05, clock.getDelta());
    step(dt, clock.elapsedTime);

    acc += dt; frames += 1;
    if (acc >= 0.5) {
      fps = Math.round(frames / acc); acc = 0; frames = 0;
      const [w, h] = measure();
      if (Math.abs(w - lastW) > 1 || Math.abs(h - lastH) > 1) resize();
    }
  }
  raf = requestAnimationFrame(loop);

  function dispose() {
    running = false;
    cancelAnimationFrame(raf);
    observer.disconnect();
    stopDrone();
    if (ownsAudio) audio?.dispose();
    geometry.dispose();
    material.dispose();
    orbGeometry.dispose();
    haloGeometry.dispose();
    orb.material.dispose();
    halo.material.dispose();
    ringGeometry.dispose();
    for (const ring of rings) ring.mat.dispose();
    bloom.dispose();
    composer.renderTarget1?.dispose();
    composer.renderTarget2?.dispose();
    renderer.dispose();
    renderer.forceContextLoss?.();
    if (!canvas) renderer.domElement.remove();
  }

  return {
    scene, camera, renderer, composer, root, audio,
    flow, orb, halo,
    points: flow,                 // alias : le nom d'avant, pour ne rien casser
    canvas: renderer.domElement,
    get fps() { return fps; },
    get status() { return status; },
    get formed() { return form > 0.9; },
    get segmentCount() { return segments; },

    /** Séquence 1 — « Open humanoid ». Rejouable à tout moment. */
    triggerStartupAnimation() {
      audio?.unlock();
      startupT = 0;
      form = 0;
      formTarget = 0;
      pulse = 0;
      setStatus('OPENING');
      return STARTUP_DURATION;
    },

    /**
     * Séquence 2 — « Check your system ». Matérialise d'abord le buste s'il
     * n'est pas encore là : un scan sur un écran vide n'a pas de sens.
     */
    triggerSystemCheckAnimation() {
      audio?.unlock();
      if (form < 0.9 && startupT < 0) this.triggerStartupAnimation();
      stopDrone();
      checkT = 0;
      nextWaveAt = 0;
      dronePlayed = false;
      validatePlayed = false;
      audio?.playChimeSequence();
      pulse = 0.9;
      setStatus('SCANNING');
      return CHECK.endAt;
    },

    /** Matérialise sans rejouer l'animation (reprise d'onglet, tests). */
    showInstantly() { startupT = -1; form = 1; formTarget = 1; setStatus('IDLE'); },
    hide() { startupT = -1; checkT = -1; form = 0; formTarget = 0; setStatus('STANDBY'); },

    /* --- Interface commune aux viewers JARVIS --- */
    setActivity(level) { activity = Math.max(0, Math.min(1, Number(level) || 0)); },
    setAudioLevel(level) { speakLevel = Math.max(0, Math.min(1, Number(level) || 0)); },
    setState(name) {
      const key = String(name || '').toUpperCase();
      if (key === 'SPEAKING') activity = Math.max(activity, 0.5);
      if (key === 'IDLE') activity = 0.15;
      pulse = Math.max(pulse, 0.45);
    },
    gesture() { pulse = 1; return true; },
    lookAt(point) {
      if (!point) { look.x = 0; look.y = 0; return; }
      look.x = Math.max(-1, Math.min(1, Number(point.x) || 0));
      look.y = Math.max(-1, Math.min(1, Number(point.y) || 0));
    },
    setViewMode(mode) {
      const next = FRAMING[String(mode || '').toUpperCase()];
      if (!next) return false;
      framing = next;
      resize();
      return true;
    },
    setCameraMode(mode) { return this.setViewMode(mode); },
    setBloom({ strength, radius, threshold } = {}) {
      if (strength !== undefined) opt.bloom.strength = strength;
      if (radius !== undefined) bloom.radius = radius;
      if (threshold !== undefined) bloom.threshold = threshold;
    },
    setQuality() {},
    setAutoRotate() {},
    recenter() { look.x = 0; look.y = 0; },
    pause() { running = false; cancelAnimationFrame(raf); },
    /* Inconditionnel, et non « seulement si en pause » : un onglet masqué peut
       laisser `running` à true alors que la chaîne rAF est morte. */
    resume() {
      running = true;
      cancelAnimationFrame(raf);
      clock.getDelta();           // absorbe le temps passé hors écran
      raf = requestAnimationFrame(loop);
    },
    resize,
    /** Avance d'une image sans passer par rAF (tests, onglet bridé, capture). */
    step(dt = 1 / 60) { manualTime += dt; step(Math.min(0.05, dt), manualTime); },
    attachTo(el) {
      if (!el || canvas) return;
      if (renderer.domElement.parentElement !== el) el.appendChild(renderer.domElement);
      observer.disconnect();
      observer.observe(el);
      resize();
    },
    dispose,
  };
}

export default createParticleHumanoid;
