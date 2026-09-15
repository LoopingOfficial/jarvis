/* ==========================================================================
   JARVIS — Noyau holographique

   Entièrement procédural : aucun fichier à charger, rien à convertir, et donc
   aucune des misères de rig, de topologie ou de textures cartoon qui ont
   condamné les tentatives à base de GLB.

   Le parti pris est assumé : on NE sculpte PAS un visage. Un visage humain
   modelé à la main dans un shader se lit toujours comme un visage raté — il
   n'y a pas de demi-mesure, l'oeil humain est spécialisé là-dedans. Une forme
   abstraite, elle, n'a pas de « correct » à manquer : elle est exactement ce
   qu'elle prétend être. C'est aussi la représentation canonique d'une IA.

       const core = await createHoloCore({ host });
       core.setState('THINKING');   core.dispose();

   Interface identique à celle de premium_viewer, pour que la page d'accueil
   puisse passer de l'un à l'autre sans rien changer d'autre que l'import.
   ========================================================================== */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/EffectComposer.js';
import { RenderPass } from 'three/addons/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/OutputPass.js';

const PIXEL_RATIO = Math.min(devicePixelRatio || 1, 1.5);

const DEFAULTS = {
  color: 0x6fe6ff,          // cyan JARVIS
  warm: 0x4facfe,           // bleu plus profond, pour les couches lointaines
  radius: 0.62,             // rayon de la sphère de grille, en unités de scène
  rings: 14,                // parallèles
  meridians: 24,
  particles: 700,
  // Seuil haut : tout est déjà additif dans cette scène, donc presque tout
  // dépasse. À 0.18 le bloom repeignait le noyau en disque blanc.
  bloom: { strength: 0.6, radius: 0.8, threshold: 0.5 },
  exposure: 1.0,
  projector: true,          // cône de lumière et anneaux au sol
};

/* -------------------------------------------------------------------------
   Géométries de lignes

   On construit les grilles en LineSegments plutôt qu'avec `wireframe: true` :
   un maillage triangulé en fil de fer montre AUSSI ses diagonales, et la
   grille devient un tissu de triangles au lieu d'un quadrillage. Ici chaque
   ligne est posée exactement où on la veut.
   ------------------------------------------------------------------------- */

/** Sphère en quadrillage latitude / longitude. */
function latLongGeometry(radius, rings, meridians, segments = 96) {
  const points = [];
  const push = (a, b) => { points.push(a.x, a.y, a.z, b.x, b.y, b.z); };
  const at = (lat, lon) => new THREE.Vector3(
    radius * Math.cos(lat) * Math.sin(lon),
    radius * Math.sin(lat),
    radius * Math.cos(lat) * Math.cos(lon),
  );

  // Parallèles : on saute les pôles, où le cercle dégénère en un point.
  for (let i = 1; i < rings; i++) {
    const lat = -Math.PI / 2 + (Math.PI * i) / rings;
    for (let s = 0; s < segments; s++) {
      push(at(lat, (s / segments) * Math.PI * 2), at(lat, ((s + 1) / segments) * Math.PI * 2));
    }
  }
  // Méridiens : d'un pôle à l'autre.
  for (let m = 0; m < meridians; m++) {
    const lon = (m / meridians) * Math.PI * 2;
    for (let s = 0; s < segments / 2; s++) {
      const a = -Math.PI / 2 + (Math.PI * s) / (segments / 2);
      const b = -Math.PI / 2 + (Math.PI * (s + 1)) / (segments / 2);
      push(at(a, lon), at(b, lon));
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

/** Cercle en ligne fermée, rayon 1 : on le met à l'échelle à l'usage. */
function circleGeometry(segments = 128) {
  const points = [];
  for (let i = 0; i <= segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    points.push(Math.cos(a), 0, Math.sin(a));
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

/** Texture d'un point lumineux : un dégradé radial, pas un carré. */
function sparkTexture() {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const grd = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.28, 'rgba(255,255,255,.55)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = grd;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export async function createHoloCore(options = {}) {
  const opts = { ...DEFAULTS, ...options, bloom: { ...DEFAULTS.bloom, ...(options.bloom || {}) } };
  const host = opts.host;
  if (!host) throw new Error('createHoloCore : option `host` manquante.');

  const tint = new THREE.Color(opts.color);
  const deep = new THREE.Color(opts.warm);
  const R = opts.radius;

  /* ---------------------------------------------------------------- scène */
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 50);
  // Rayon réellement occupé : l'anneau gyroscopique le plus large (1.80 R) et
  // le nuage de particules (1.90 R). Cadrer sur la sphère seule faisait sortir
  // les anneaux du cadre — invisible tant que le masque CSS rognait les bords.
  // Le projecteur compte dans le cadrage : sans lui, ses anneaux tombaient
  // sous le bord inférieur et le faisceau semblait sortir de nulle part.
  const OUTER = opts.radius * 2.25;
  const LOOK_Y = -opts.radius * 0.34;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(PIXEL_RATIO);
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = opts.exposure;
  renderer.domElement.style.cssText = 'display:block;width:100%;height:100%';
  host.appendChild(renderer.domElement);

  // Tout est additif et sans éclairage : aucune lumière dans cette scène, donc
  // aucune ombre, aucun PBR, aucun environnement à générer. C'est ce qui rend
  // ce rendu bien plus léger qu'un personnage skinné.
  const core = new THREE.Group();
  scene.add(core);

  // Temps partagé : un seul uniforme mis à jour par frame pour tous les shaders.
  const uTime = { value: 0 };
  const uAmp = { value: 0 };        // 0 = repos, 1 = activité maximale
  const disposables = [];

  /* ------------------------------------------------------------ coeur plein */
  // Une sphère déformée par une onde, translucide, dont seule la silhouette
  // s'allume (Fresnel). C'est la masse qui donne au noyau son volume ; sans
  // elle, les grilles flottent dans le vide et rien ne paraît solide.
  const innerMaterial = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    // FrontSide et non DoubleSide : en additif, dessiner aussi la face arrière
    // double la contribution de CHAQUE pixel et le noyau vire à la boule
    // blanche. La face avant seule suffit à donner le volume.
    side: THREE.FrontSide,
    uniforms: { uTime, uAmp, uColor: { value: tint }, uDeep: { value: deep } },
    vertexShader: `
      uniform float uTime;
      uniform float uAmp;
      varying vec3 vNrm;
      varying vec3 vPos;
      // Trois sinusoïdes croisées plutôt qu'un vrai bruit : à cette échelle la
      // différence est invisible, et cela évite d'embarquer une fonction de
      // bruit de cent lignes pour une ondulation de trois centimètres.
      float ripple(vec3 p, float t) {
        return sin(p.x * 5.5 + t * 1.30)
             * sin(p.y * 4.8 - t * 1.05)
             * sin(p.z * 6.10 + t * 0.85);
      }
      void main() {
        float amount = 0.020 + uAmp * 0.055;
        vec3 displaced = position + normal * ripple(position, uTime) * amount;
        vNrm = normalize(normalMatrix * normal);
        vPos = displaced;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
      }`,
    fragmentShader: `
      uniform float uTime;
      uniform float uAmp;
      uniform vec3 uColor;
      uniform vec3 uDeep;
      varying vec3 vNrm;
      varying vec3 vPos;
      void main() {
        // vNrm est en espace vue : sa composante z donne l'incidence, donc
        // 1 - |z| vaut 0 face à la caméra et 1 sur le bord de la silhouette.
        float fres = pow(1.0 - abs(vNrm.z), 2.4);
        float bands = 0.5 + 0.5 * sin(vPos.y * 54.0 - uTime * 2.2);
        vec3 rgb = mix(uDeep, uColor, fres) * (0.10 + fres * 1.15 + bands * 0.07 + uAmp * 0.22);
        gl_FragColor = vec4(rgb, (0.045 + fres * 0.45) * (0.85 + uAmp * 0.25));
      }`,
  });
  const innerGeometry = new THREE.IcosahedronGeometry(R * 0.68, 4);
  const inner = new THREE.Mesh(innerGeometry, innerMaterial);
  core.add(inner);
  disposables.push(innerGeometry, innerMaterial);

  /* ------------------------------------------------------- grille sphérique */
  const gridGeometry = latLongGeometry(R, opts.rings, opts.meridians);
  const gridMaterial = new THREE.LineBasicMaterial({
    color: tint, transparent: true, opacity: 0.32,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const grid = new THREE.LineSegments(gridGeometry, gridMaterial);
  core.add(grid);
  disposables.push(gridGeometry, gridMaterial);

  // Seconde coque, facettée et plus large, tournant en sens inverse : c'est ce
  // décalage entre deux couches qui donne la profondeur. Une seule sphère,
  // même animée, se lit comme un dessin plat.
  // Détail 1 et non 0 : à 0 les vingt faces de l'icosaèdre donnent des arêtes
  // si longues qu'elles barrent le noyau de part en part. Et opacité très
  // basse : cette coque est un contexte, pas un sujet — plus lisible qu'elle,
  // elle avalait les anneaux gyroscopiques.
  const shellGeometry = new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(R * 1.16, 1));
  const shellMaterial = new THREE.LineBasicMaterial({
    color: deep, transparent: true, opacity: 0.09,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const shell = new THREE.LineSegments(shellGeometry, shellMaterial);
  core.add(shell);
  disposables.push(shellGeometry, shellMaterial);

  /* ----------------------------------------------------- anneaux gyroscopiques */
  const circle = circleGeometry(160);
  disposables.push(circle);
  const gyros = [];
  const GYRO_SETUP = [
    { scale: R * 1.42, tilt: [0.32, 0, 0.12], speed: 0.38, opacity: 0.55 },
    { scale: R * 1.62, tilt: [-0.95, 0.4, 0], speed: -0.26, opacity: 0.38 },
    { scale: R * 1.80, tilt: [1.35, 0, 0.6], speed: 0.17, opacity: 0.26 },
  ];
  for (const setup of GYRO_SETUP) {
    const material = new THREE.LineBasicMaterial({
      color: tint, transparent: true, opacity: setup.opacity,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    const ring = new THREE.Line(circle, material);
    ring.scale.setScalar(setup.scale);
    ring.rotation.set(setup.tilt[0], setup.tilt[1], setup.tilt[2]);
    ring.userData.speed = setup.speed;
    core.add(ring);
    gyros.push(ring);
    disposables.push(material);
  }

  /* ------------------------------------------------------- anneau de balayage */
  // Un cercle qui monte et descend le long du noyau, en épousant la section de
  // la sphère à sa hauteur : r = sqrt(R² - y²). Sans cette correction il
  // traverserait la sphère au lieu de glisser dessus.
  const scanMaterial = new THREE.LineBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.5,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const scan = new THREE.Line(circle, scanMaterial);
  core.add(scan);
  disposables.push(scanMaterial);

  /* ------------------------------------------------------------- particules */
  const spark = sparkTexture();
  const count = opts.particles;
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count * 3);   // rayon, vitesse, phase
  for (let i = 0; i < count; i++) {
    // Distribution uniforme sur une sphère. Tirer theta et phi au hasard
    // agglutinerait les points aux pôles : c'est l'arccos qui corrige ça.
    const u = Math.random() * 2 - 1;
    const theta = Math.random() * Math.PI * 2;
    const rho = Math.sqrt(1 - u * u);
    const radius = R * (1.05 + Math.random() * 0.85);
    positions[i * 3] = rho * Math.cos(theta) * radius;
    positions[i * 3 + 1] = u * radius;
    positions[i * 3 + 2] = rho * Math.sin(theta) * radius;
    seeds[i * 3] = radius;
    seeds[i * 3 + 1] = 0.15 + Math.random() * 0.5;
    seeds[i * 3 + 2] = Math.random() * Math.PI * 2;
  }
  const dustGeometry = new THREE.BufferGeometry();
  dustGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  dustGeometry.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 3));
  const dustMaterial = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime, uAmp, uColor: { value: tint }, uMap: { value: spark }, uScale: { value: 1 } },
    vertexShader: `
      uniform float uTime;
      uniform float uAmp;
      uniform float uScale;
      attribute vec3 aSeed;
      varying float vFade;
      void main() {
        // Chaque grain tourne autour de l'axe vertical à SA vitesse : une
        // rotation commune ferait tourner un bloc rigide, pas un nuage.
        float angle = uTime * aSeed.y * 0.35 + aSeed.z;
        float c = cos(angle), s = sin(angle);
        vec3 p = vec3(position.x * c - position.z * s, position.y, position.x * s + position.z * c);
        p.y += sin(uTime * aSeed.y + aSeed.z) * 0.04;
        vFade = 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * 1.6 + aSeed.z * 3.0));
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        // Atténuation par la distance : sans division par -mv.z, les grains du
        // fond auraient la même taille que ceux du premier plan.
        gl_PointSize = (3.2 + uAmp * 2.6) * uScale / max(-mv.z, 0.001);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      uniform vec3 uColor;
      uniform sampler2D uMap;
      varying float vFade;
      void main() {
        vec4 tex = texture2D(uMap, gl_PointCoord);
        gl_FragColor = vec4(uColor * (0.7 + vFade * 0.9), tex.a * vFade * 0.75);
      }`,
  });
  const dust = new THREE.Points(dustGeometry, dustMaterial);
  core.add(dust);
  disposables.push(dustGeometry, dustMaterial, spark);

  /* --------------------------------------------------------- projecteur au sol */
  const base = new THREE.Group();
  if (opts.projector) {
    base.position.y = -R * 1.72;
    scene.add(base);

    // Cône de lumière. Ouvert aux deux bouts, rendu des deux côtés, et dégradé
    // par la hauteur : c'est le dégradé qui fait le faisceau, la géométrie
    // seule donnerait un abat-jour.
    const coneMaterial = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      uniforms: { uTime, uColor: { value: tint } },
      vertexShader: `
        varying float vH;
        void main() {
          vH = uv.y;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        uniform float uTime;
        uniform vec3 uColor;
        varying float vH;
        void main() {
          // L'émetteur est EN BAS, là où le cône est étroit : le faisceau doit
          // donc s'éteindre en montant. Le dégradé était inversé, ce qui
          // peignait une dalle cyan en travers de la scène.
          float fade = pow(1.0 - vH, 2.2);
          float flicker = 0.9 + 0.1 * sin(uTime * 7.3);
          gl_FragColor = vec4(uColor * 0.55, fade * 0.075 * flicker);
        }`,
    });
    const coneGeometry = new THREE.CylinderGeometry(R * 1.5, R * 0.12, R * 2.0, 56, 1, true);
    const cone = new THREE.Mesh(coneGeometry, coneMaterial);
    cone.position.y = R * 1.0;
    base.add(cone);
    disposables.push(coneGeometry, coneMaterial);

    // Anneaux de l'émetteur.
    for (const [scale, opacity] of [[R * 0.55, 0.75], [R * 0.85, 0.4], [R * 1.25, 0.2]]) {
      const material = new THREE.LineBasicMaterial({
        color: tint, transparent: true, opacity,
        blending: THREE.AdditiveBlending, depthWrite: false,
      });
      const ring = new THREE.Line(circle, material);
      ring.scale.setScalar(scale);
      base.add(ring);
      disposables.push(material);
    }
  }

  /* ----------------------------------------------------- post-traitement */
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(1, 1), opts.bloom.strength, opts.bloom.radius, opts.bloom.threshold);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  /* ------------------------------------------------------ redimensionnement */
  function resize() {
    const box = renderer.domElement.parentElement || host;
    const w = box.clientWidth || 1;
    const h = box.clientHeight || 1;
    if (!w || !h) return;
    renderer.setPixelRatio(PIXEL_RATIO);
    renderer.setSize(w, h, false);
    composer.setPixelRatio(PIXEL_RATIO);
    composer.setSize(w, h);
    bloom.setSize(Math.max(2, Math.round(w / 2)), Math.max(2, Math.round(h / 2)));
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Distance calculée sur LES DEUX champs de vision : ne considérer que le
    // vertical laisse le sujet déborder par les côtés dès que le conteneur
    // devient large — et l'inverse dès qu'il devient étroit.
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
    const distance = Math.max(OUTER / Math.sin(vFov / 2), OUTER / Math.sin(hFov / 2)) * 1.06;
    camera.position.set(0, LOOK_Y + opts.radius * 0.2, distance);
    camera.lookAt(0, LOOK_Y, 0);
    // La taille des points est en PIXELS : sans ce facteur, le nuage paraîtrait
    // deux fois plus dense sur un petit conteneur que sur un grand.
    dustMaterial.uniforms.uScale.value = h * PIXEL_RATIO * 0.0016;
  }
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  /* --------------------------------------------------------------- états */
  const STATES = {
    IDLE: { amp: 0, spin: 1, color: opts.color },
    LISTENING: { amp: 0.35, spin: 1.4, color: opts.color },
    THINKING: { amp: 0.6, spin: 2.6, color: opts.color },
    SPEAKING: { amp: 0.8, spin: 1.8, color: opts.color },
    WORKING: { amp: 1, spin: 3.2, color: 0x9be8ff },
  };
  let state = STATES.IDLE;
  let amp = 0, spin = 1;
  let pulse = 0;                     // impulsion ponctuelle, retombe seule

  const clock = new THREE.Clock();
  let raf = 0, running = true, fps = 0, acc = 0, frames = 0;

  function loop() {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(clock.getDelta(), 0.1);   // un onglet réveillé saute parfois plusieurs secondes
    const t = clock.elapsedTime;
    uTime.value = t;

    // L'activité monte vite et redescend lentement : une IA qui se met au
    // travail doit se voir immédiatement, l'apaisement peut prendre son temps.
    const rise = state.amp > amp ? 4.0 : 0.9;
    amp += (state.amp - amp) * Math.min(1, dt * rise);
    spin += (state.spin - spin) * Math.min(1, dt * 2.2);
    pulse *= Math.max(0, 1 - dt * 1.8);
    uAmp.value = Math.min(1, amp + pulse);

    // Rotations : trois vitesses différentes, aucune multiple d'une autre,
    // sinon le motif se répète visiblement toutes les quelques secondes.
    grid.rotation.y += dt * 0.09 * spin;
    grid.rotation.x = Math.sin(t * 0.13) * 0.09;
    shell.rotation.y -= dt * 0.055 * spin;
    shell.rotation.z += dt * 0.021 * spin;
    inner.rotation.y += dt * 0.14 * spin;
    for (const ring of gyros) {
      ring.rotation.z += dt * ring.userData.speed * spin;
      ring.rotation.y += dt * ring.userData.speed * 0.4 * spin;
    }

    // Balayage : va-et-vient sur la hauteur, rayon suivant la section.
    const y = Math.sin(t * (0.35 + amp * 0.5)) * R * 0.96;
    const r = Math.sqrt(Math.max(0, R * R - y * y));
    scan.position.y = y;
    scan.scale.set(r * 1.02, 1, r * 1.02);
    scanMaterial.opacity = 0.18 + 0.4 * amp + 0.1 * Math.sin(t * 3.1);

    // Respiration d'ensemble : très légère, mais c'est elle qui empêche le
    // noyau de paraître figé quand rien ne se passe.
    const breath = 1 + Math.sin(t * 0.8) * 0.012 + uAmp.value * 0.03;
    core.scale.setScalar(breath);

    composer.render();

    acc += dt; frames += 1;
    if (acc >= 0.5) { fps = Math.round(frames / acc); acc = 0; frames = 0; }
  }
  raf = requestAnimationFrame(loop);

  /* ---------------------------------------------------------- libération */
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
    renderer.domElement.remove();
  }

  return {
    scene, camera, renderer, composer, core,
    get fps() { return fps; },
    /** États du bus : voix, chat, agents. */
    setState(name) {
      const next = STATES[String(name || '').toUpperCase()];
      if (!next) return;
      if (next !== state) pulse = 0.5;   // le changement lui-même se voit
      state = next;
      const color = new THREE.Color(next.color);
      tint.copy(color);
      gridMaterial.color.copy(color);
    },
    /** 0 = calme, 1 = actif, pour un pilotage continu. */
    setActivity(level) {
      const value = Math.max(0, Math.min(1, Number(level) || 0));
      state = { ...state, amp: value, spin: 1 + value * 2.2 };
    },
    /** Réaction ponctuelle : une impulsion qui retombe d'elle-même. */
    gesture() { pulse = 1; return true; },
    setBloom({ strength, radius, threshold }) {
      if (strength !== undefined) bloom.strength = strength;
      if (radius !== undefined) bloom.radius = radius;
      if (threshold !== undefined) bloom.threshold = threshold;
    },
    // Présents pour rester interchangeable avec premium_viewer, dont la page
    // d'accueil appelle l'interface complète.
    setAutoRotate() {},
    recenter() {},
    pause() { running = false; cancelAnimationFrame(raf); },
    resume() { if (!running) { running = true; clock.getDelta(); raf = requestAnimationFrame(loop); } },
    resize,
    attachTo(el) {
      if (!el) return;
      if (renderer.domElement.parentElement !== el) el.appendChild(renderer.domElement);
      observer.disconnect();
      observer.observe(el);
      resize();
    },
    dispose,
  };
}

export default createHoloCore;
