/* ==========================================================================
   JARVIS — Premium Avatar Viewer
   Viewer 3D autonome : cadrage automatique sur boîte englobante réelle,
   éclairage studio, matériaux PBR, bloom officiel (UnrealBloomPass) et
   libération complète de la mémoire.

   Il ne dépend QUE de three r160 et des addons vendorisés dans /ui/vendor.
   Il ne touche pas au pipeline avatar existant (behavior, face_controller,
   locomotion) : c'est un composant de rendu, montable n'importe où.

       const viewer = await createAvatarViewer({ host, url });
       viewer.dispose();
   ========================================================================== */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { GLTFLoader } from 'three/addons/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/DRACOLoader.js';
import { RoomEnvironment } from 'three/addons/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/EffectComposer.js';
import { RenderPass } from 'three/addons/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/OutputPass.js';

const DEFAULTS = {
  url: '/assets/avatar/rp_manuel_dancing.glb',
  // Cadrage : marge autour du sujet, et hauteur visée du regard (0 = pieds,
  // 1 = sommet du crâne). 0.62 place la caméra à hauteur de poitrine, ce qui
  // évite la contre-plongée disgracieuse d'une caméra centrée sur le nombril.
  fitMargin: 1.25,
  lookAtRatio: 0.62,
  // Seuil haut volontairement : à 0.85 un vêtement blanc passe au-dessus et
  // le personnage entier se met à rayonner. Le bloom doit souligner les accents
  // néon de la scène, pas repeindre le sujet.
  bloom: { strength: 0.38, radius: 0.55, threshold: 0.92 },
  accent: 0x22d3ee,        // cyan JARVIS
  rim: 0x3b82f6,           // bleu froid pour le contre-jour
  // Exposition volontairement basse : un sujet clair (sweat blanc) sature
  // très vite en ACESFilmic, et la lumière écrase alors tout le modelé du
  // visage et des plis du vêtement.
  exposure: 0.72,
  shadows: true,
  autoRotate: false,
  platform: true,
  // Ancre la racine : l'avatar danse sur place au lieu de traverser la scène.
  anchorRoot: true,
  // Voir le commentaire dans le chargement des animations : indispensable
  // pour les rigs FBX exportés depuis Maya/3ds Max.
  stripScaleTracks: true,
};

/* -------------------------------------------------------------------------
   Cadrage : la seule façon fiable de centrer est de MESURER.
   ------------------------------------------------------------------------- */
function measure(object3d) {
  const box = new THREE.Box3();
  // Les maillages skinnés ont une boîte de repos trompeuse une fois animés :
  // on force la mise à jour des matrices avant de mesurer, sinon le centre
  // calculé correspond à une pose que le modèle n'occupe jamais.
  object3d.updateWorldMatrix(true, true);
  box.setFromObject(object3d, true);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  return { box, size, center, height: size.y, radius: size.length() / 2 };
}

/**
 * Distance à laquelle un sujet de rayon `radius` tient entièrement dans le
 * cadre, POUR LES DEUX AXES. Ne considérer que le FOV vertical fait sortir le
 * sujet par les côtés dès que la fenêtre devient étroite — c'est l'erreur de
 * cadrage la plus courante, et elle ne se voit qu'en redimensionnant.
 */
function fitDistance(camera, radius, margin) {
  const vFov = THREE.MathUtils.degToRad(camera.fov);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  const forVertical = radius / Math.sin(vFov / 2);
  const forHorizontal = radius / Math.sin(hFov / 2);
  return Math.max(forVertical, forHorizontal) * margin;
}

/* -------------------------------------------------------------------------
   Poses figées et vie du personnage
   ------------------------------------------------------------------------- */

/** Bones de déformation utiles, par motif. Un rig Rigify exporté en glTF ne
 *  conserve QUE les os `DEF-` comme os de skinning : les contrôleurs FK/IK
 *  sortent inertes, les animer n'aurait aucun effet visible. */
function findBones(model) {
  let skeleton = null;
  model.traverse((n) => { if (!skeleton && n.isSkinnedMesh) skeleton = n.skeleton; });
  if (!skeleton) return {};
  const by = (re) => skeleton.bones.find((b) => re.test(b.name)) || null;
  return {
    skeleton,
    hips: by(/^(DEF-)?(pelvis|hips)$/i) || by(/pelvis|hips/i),
    spine: by(/^DEF-spine$/i) || by(/^spine$/i),
    chest: by(/^DEF-spine00[23]$/i) || by(/chest/i),
    head: by(/^DEF-spine006$/i) || by(/^DEF-head$/i) || by(/head/i),
    armL: by(/^DEF-upper_arm.*L$/i) || by(/upper_?arm.*l$/i),
    armR: by(/^DEF-upper_arm.*R$/i) || by(/upper_?arm.*r$/i),
    handL: by(/^DEF-handL$/i), handR: by(/^DEF-handR$/i),
  };
}

/** Pose la plus « au repos » : mains les plus basses par rapport au bassin. */
function pickRestPose(model, mixer, clips) {
  const bones = findBones(model);
  if (!bones.hips || !bones.handL || !bones.handR) return null;
  const tmp = new THREE.Vector3();
  let best = null, bestScore = Infinity;
  for (const candidate of clips) {
    mixer.stopAllAction();
    mixer.clipAction(candidate).play();
    mixer.setTime(candidate.duration || 0.01);
    model.updateWorldMatrix(true, true);
    const hipY = bones.hips.getWorldPosition(tmp).y;
    const score = (bones.handL.getWorldPosition(new THREE.Vector3()).y - hipY)
                + (bones.handR.getWorldPosition(new THREE.Vector3()).y - hipY);
    if (score < bestScore) { bestScore = score; best = candidate; }
  }
  mixer.stopAllAction();
  return best;
}

export async function createAvatarViewer(options = {}) {
  const opts = { ...DEFAULTS, ...options, bloom: { ...DEFAULTS.bloom, ...(options.bloom || {}) } };
  const host = opts.host;
  if (!host) throw new Error('createAvatarViewer : option `host` manquante.');

  /* ---------------------------------------------------------------- scène */
  const scene = new THREE.Scene();
  scene.background = null;

  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 200);
  camera.position.set(0, 1.5, 4);

  const renderer = new THREE.WebGLRenderer({
    antialias: true, alpha: true, powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));   // au-delà de 2 : coût x2 pour rien
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;           // OutputPass la relit
  renderer.toneMappingExposure = opts.exposure;
  if (opts.shadows) {
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  }
  renderer.domElement.style.cssText = 'display:block;width:100%;height:100%';
  host.appendChild(renderer.domElement);

  // Environnement PBR procédural : donne aux matériaux métalliques quelque
  // chose à réfléchir. Sans lui, `metalness: 0.8` rend un gris plat — c'est la
  // cause la plus fréquente d'un rendu « en plastique ».
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
  scene.environment = envRT.texture;

  /* ------------------------------------------------------------ éclairage */
  const lights = new THREE.Group();
  scene.add(lights);

  // Ambiante faible et légèrement bleutée : elle sert à décoller les noirs,
  // pas à éclairer. Trop forte, elle aplatit le sujet en aplat blanc.
  const ambient = new THREE.AmbientLight(0xcfe4ff, 0.16);
  lights.add(ambient);

  // Clé tiède plutôt que blanc pur, et deux fois moins intense : le blanc pur
  // à 2.2 cramait les vêtements clairs bien avant le bloom.
  const key = new THREE.DirectionalLight(0xfff2e2, 1.05);       // lumière principale
  key.position.set(2.6, 4.2, 3.2);
  if (opts.shadows) {
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0008;                                  // supprime le moiré d'ombre
    key.shadow.normalBias = 0.02;
    const c = key.shadow.camera;
    c.near = 0.5; c.far = 18; c.left = -3; c.right = 3; c.top = 4; c.bottom = -2;
  }
  lights.add(key);

  const fill = new THREE.DirectionalLight(0x9fc6ff, 0.32);      // déboucheur froid
  fill.position.set(-3.5, 2.0, 1.5);
  lights.add(fill);

  const rim = new THREE.PointLight(opts.rim, 12, 14, 2);        // contre-jour bleu
  rim.position.set(-1.8, 2.4, -2.6);
  lights.add(rim);

  const accent = new THREE.PointLight(opts.accent, 9, 10, 2);   // accent cyan au sol
  accent.position.set(1.6, 0.35, 1.8);
  lights.add(accent);

  /* ------------------------------------------------ plateforme HUD au sol */
  const floorGroup = new THREE.Group();
  if (opts.platform) {
    const shadowCatcher = new THREE.Mesh(
      new THREE.PlaneGeometry(14, 14),
      new THREE.ShadowMaterial({ opacity: 0.45 }),               // invisible sauf l'ombre
    );
    shadowCatcher.rotation.x = -Math.PI / 2;
    shadowCatcher.receiveShadow = true;
    floorGroup.add(shadowCatcher);

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.92, 1.0, 96),
      new THREE.MeshBasicMaterial({
        color: opts.accent, transparent: true, opacity: 0.55,
        side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false,
      }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.002;                                     // évite le z-fighting avec le sol
    floorGroup.add(ring);

    const halo = new THREE.Mesh(
      new THREE.CircleGeometry(1.9, 64),
      new THREE.MeshBasicMaterial({
        color: opts.accent, transparent: true, opacity: 0.07,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }),
    );
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = 0.001;
    floorGroup.add(halo);
    scene.add(floorGroup);
  }

  /* ---------------------------------------------------------- chargement */
  const loader = new GLTFLoader();
  const draco = new DRACOLoader();
  draco.setDecoderPath('/vendor/draco/');                        // requis : le GLB est compressé Draco
  loader.setDRACOLoader(draco);

  const gltf = await new Promise((resolve, reject) => {
    loader.load(opts.url, resolve, opts.onProgress || undefined, reject);
  });

  const model = gltf.scene;
  model.traverse((node) => {
    if (!node.isMesh) return;
    node.castShadow = true;
    node.receiveShadow = true;
    // Un maillage skinné animé sort de sa boîte de repos : sans cela il
    // disparaît par intermittence quand la pose s'en éloigne.
    node.frustumCulled = false;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    for (const material of materials) {
      if (!material) continue;
      // L'environnement PMREM ajoute sa propre lumière diffuse : à 0.9 il
      // s'additionnait aux sources et participait à la saturation.
      material.envMapIntensity = 0.45;
      if (material.map) material.map.anisotropy = 8;
      // La peau n'est ni métallique ni miroir : forcer metalness 0.8 partout,
      // comme le font beaucoup d'exemples, donnerait un mannequin en étain.
      if (material.isMeshStandardMaterial) {
        material.roughness = Math.min(material.roughness ?? 1, 0.72);
        material.metalness = Math.min(material.metalness ?? 0, 0.08);
      }
      material.needsUpdate = true;
    }
  });
  scene.add(model);

  /* ---------------------------------------------------------- animations */
  const mixer = new THREE.AnimationMixer(model);
  let action = null;
  let clip = null;
  let poses = [];            // bibliothèque de poses figées, si c'en est une
  if (gltf.animations?.length) {
    // Un FBX importé se fragmente en un clip par chaîne d'os : ici 19 clips,
    // dont 18 ne portent que 3 canaux sur des os terminaux. Le vrai mouvement
    // est celui qui pilote le plus de canaux — on ne prend donc pas [0].
    // Une T-pose ou une A-pose est une pose de RÉFÉRENCE, pas une animation.
    const usable = gltf.animations.filter((c) => !/t[-_ ]?pose|a[-_ ]?pose|bind/i.test(c.name || ''));
    const pool = usable.length ? usable : gltf.animations;

    // Certains modèles n'embarquent que des POSES FIGÉES (durée quasi nulle) :
    // en jouer une laisse le personnage bloqué dedans pour toujours. On choisit
    // alors la plus reposante — celle dont les mains sont le plus bas par
    // rapport au bassin — au lieu de prendre la première venue, qui ici était
    // une main posée sur la tête.
    const isPoseLibrary = pool.every((c) => (c.duration || 0) < 0.5);
    clip = isPoseLibrary
      ? pickRestPose(model, mixer, pool) || pool[0]
      : pool.reduce((best, c) => (c.tracks.length > best.tracks.length ? c : best));
    poses = isPoseLibrary ? pool : [];

    // Pistes d'échelle : à retirer sur ce type de rig.
    //
    // Les FBX issus de Maya/3ds portent une piste `.scale` par os (69 ici). En
    // sortie glTF elles se combinent aux échelles déjà présentes sur les nœuds,
    // et le haut du corps explose : mesuré 1,91 × 2,43 × 2,47 m, bras étirés à
    // plusieurs mètres. Sans elles : 1,86 × 0,98 × 0,41 m — les proportions
    // d'un humain. Aucune perte : le mouvement est porté par les rotations.
    //
    // À l'inverse, NE PAS filtrer la translation de l'os racine : sur ce rig
    // elle porte la mise à l'échelle du squelette, et la retirer fait
    // s'effondrer le personnage à 4 cm de haut. L'ancrage se fait par
    // contre-translation du groupe, plus bas.
    if (opts.stripScaleTracks) {
      const filtered = clip.tracks.filter((t) => !t.name.endsWith('.scale'));
      if (filtered.length !== clip.tracks.length) {
        clip = clip.clone();
        clip.tracks = filtered;
      }
    }
    action = mixer.clipAction(clip);
    action.play();
    mixer.update(0);
  }

  // Ancrage par contre-translation : on laisse l'animation faire ce qu'elle
  // veut, puis on ramène le groupe pour que l'os racine reste au même point au
  // sol. Rien n'est retiré du clip, donc rien ne peut s'effondrer.
  const rootBone = (() => { let b = null; model.traverse((n) => { if (!b && n.isSkinnedMesh) b = n.skeleton?.bones?.[0] || null; }); return b; })();
  const rootWorld = new THREE.Vector3();
  let anchorXZ = null;

  function applyAnchor() {
    if (!opts.anchorRoot || !rootBone) return;
    model.position.set(0, model.position.y, 0);
    model.updateWorldMatrix(true, true);
    rootBone.getWorldPosition(rootWorld);
    if (!anchorXZ) anchorXZ = { x: rootWorld.x, z: rootWorld.z };
    model.position.x = anchorXZ.x - rootWorld.x;
    model.position.z = anchorXZ.z - rootWorld.z;
    model.updateWorldMatrix(true, true);
  }

  function findRootBone(object3d) {
    let bone = null;
    object3d.traverse((n) => { if (!bone && n.isSkinnedMesh) bone = n.skeleton?.bones?.[0] || null; });
    return bone;
  }

  /**
   * Boîte englobante de TOUTE l'animation, pas d'une seule pose.
   *
   * Mesurer la frame 0 donnait ici 1,32 m de haut (pose basse) alors que le
   * mouvement culmine à 2,64 m : la caméra se retrouvait à l'intérieur du
   * personnage dès la deuxième seconde. On échantillonne le clip et on prend
   * l'union — coût ponctuel, cadrage valable pour toute la boucle.
   */
  function measureAnimated(samples = 24) {
    if (!action || !clip) return measure(model);
    const union = new THREE.Box3();
    const previous = mixer.time;
    for (let i = 0; i < samples; i++) {
      mixer.setTime((clip.duration * i) / samples);
      applyAnchor();
      union.union(new THREE.Box3().setFromObject(model, true));
    }
    mixer.setTime(previous);
    model.updateWorldMatrix(true, true);
    if (union.isEmpty()) return measure(model);
    const size = union.getSize(new THREE.Vector3());
    const center = union.getCenter(new THREE.Vector3());
    const sampled = { box: union, size, center, height: size.y, radius: size.length() / 2 };
    // Garde-fou : au tout premier appel, la hiérarchie du glTF n'est pas
    // toujours à jour et l'échantillonnage renvoie une boîte quasi nulle
    // (observé : 0,009 m pour un personnage d'1,74 m), ce qui colle la caméra
    // au modèle. On préfère alors la mesure statique, et `frame()` réessaiera.
    if (sampled.height < 0.05) {
      const fallback = measure(model);
      if (fallback.height > sampled.height) return fallback;
    }
    return sampled;
  }

  /* ------------------------------------------------------------- cadrage */
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.enablePan = false;                                    // un avatar ne se déplace pas hors cadre
  controls.autoRotate = opts.autoRotate;
  controls.autoRotateSpeed = 0.6;

  let framing = null;

  /** Recentre et recadre sur la boîte englobante ACTUELLE du modèle. */
  function frame({ animate = false } = {}) {
    const m = measureAnimated();
    framing = m;

    // On ne déplace pas le modèle : on déplace la cible. Recentrer en
    // translatant l'objet casserait les animations qui portent une translation
    // racine (ici, la danse se déplace légèrement).
    const target = new THREE.Vector3(m.center.x, m.box.min.y + m.height * opts.lookAtRatio, m.center.z);
    const distance = fitDistance(camera, m.radius, opts.fitMargin);

    // Direction conservée si l'utilisateur a déjà orbité, sinon 3/4 face.
    const dir = animate && controls.target.distanceTo(target) > 0
      ? camera.position.clone().sub(controls.target).normalize()
      : new THREE.Vector3(0.35, 0.16, 1).normalize();

    camera.position.copy(target).addScaledVector(dir, distance);
    controls.target.copy(target);
    controls.minDistance = distance * 0.35;
    controls.maxDistance = distance * 3.2;
    camera.near = Math.max(0.01, distance * 0.01);
    camera.far = distance * 12;
    camera.updateProjectionMatrix();
    controls.update();

    // La plateforme se cale sous les pieds, quelle que soit l'origine du modèle.
    floorGroup.position.set(m.center.x, m.box.min.y, m.center.z);
    if (opts.shadows) {
      key.target.position.copy(target);
      key.target.updateMatrixWorld();
      scene.add(key.target);
    }
    return m;
  }
  frame();

  /* -------------------------------------------------- post-processing */
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(1, 1), opts.bloom.strength, opts.bloom.radius, opts.bloom.threshold);
  composer.addPass(bloom);
  // OutputPass applique le tone mapping et la conversion sRGB EN FIN de chaîne.
  // Sans lui, avec un composer, l'image ressort délavée et trop claire.
  composer.addPass(new OutputPass());

  /* ------------------------------------------------------ redimensionnement */
  function resize() {
    const box = renderer.domElement.parentElement || host;
    const w = box.clientWidth || 1;
    const h = box.clientHeight || 1;
    if (!w || !h) return;                       // conteneur non encore disposé : ne rien figer
    const ratio = Math.min(devicePixelRatio || 1, 2);
    renderer.setPixelRatio(ratio);
    renderer.setSize(w, h, false);
    composer.setPixelRatio(ratio);
    composer.setSize(w, h);
    bloom.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Le cadrage dépend de l'aspect : sans ce recadrage, passer en fenêtre
    // étroite coupe les épaules du sujet.
    if (framing) {
      const distance = fitDistance(camera, framing.radius, opts.fitMargin);
      const dir = camera.position.clone().sub(controls.target).normalize();
      camera.position.copy(controls.target).addScaledVector(dir, distance);
      controls.update();
    }
  }
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  /* ------------------------------------------------------ boucle de rendu */
  /* --------------------------------------------------- vie du personnage */
  // Le modèle n'embarque que des poses figées : sans cela il resterait
  // strictement immobile, ce qui se remarque tout de suite et rend l'avatar
  // mort. On superpose donc un mouvement PROCÉDURAL aux rotations posées par
  // le mixer — respiration, report d'appui, micro-mouvements de tête.
  const rig = findBones(model);
  const idleState = { activity: 0, target: 0, gestureUntil: 0, nextLook: 0, look: { x: 0, y: 0 } };
  const qTmp = new THREE.Quaternion();
  const eTmp = new THREE.Euler();

  // Rotation de repos de chaque os, capturée une fois. INDISPENSABLE : les
  // clips de ce modèle durent 0,23 s ; passé ce délai le mixer cesse d'écrire
  // les os, et une rotation simplement multipliée à chaque frame se compose à
  // l'infini — le cou se vrillait complètement en deux secondes.
  const baseRotations = new Map();

  /** Compose depuis la pose de repos (jamais depuis la frame précédente). */
  function addRotation(bone, x, y, z) {
    if (!bone) return;
    let base = baseRotations.get(bone);
    if (!base) { base = bone.quaternion.clone(); baseRotations.set(bone, base); }
    eTmp.set(x, y, z);
    qTmp.setFromEuler(eTmp);
    bone.quaternion.copy(base).multiply(qTmp);
  }

  /** À appeler quand la pose de fond change : les repères doivent suivre. */
  function resetBaseRotations() { baseRotations.clear(); }

  function animateIdle(t, dt) {
    if (!rig.skeleton) return;
    // L'activité monte vite et redescend lentement : un agent qui travaille
    // doit se voir tout de suite, et l'apaisement doit rester naturel.
    const speed = idleState.target > idleState.activity ? 3.5 : 0.6;
    idleState.activity += (idleState.target - idleState.activity) * Math.min(1, dt * speed);
    if (t > idleState.gestureUntil) idleState.target = 0;
    const amp = 1 + idleState.activity * 1.6;

    // Respiration : le thorax se soulève, le ventre suit avec un retard.
    const breath = Math.sin(t * (0.85 + idleState.activity * 0.5));
    addRotation(rig.chest, breath * 0.013 * amp, 0, 0);
    addRotation(rig.spine, Math.sin(t * 0.85 - 0.6) * 0.006 * amp, 0, 0);

    // Report d'appui : lent, asymétrique, jamais un balancier régulier.
    const sway = Math.sin(t * 0.31) * 0.6 + Math.sin(t * 0.17 + 1.1) * 0.4;
    addRotation(rig.hips, 0, sway * 0.02, sway * 0.012);
    addRotation(rig.spine, 0, sway * -0.01, sway * -0.008);

    // Bras : léger ballant, décalé entre les deux côtés.
    addRotation(rig.armL, 0, 0, Math.sin(t * 0.43) * 0.02 * amp);
    addRotation(rig.armR, 0, 0, Math.sin(t * 0.43 + 2.2) * -0.02 * amp);

    // Tête : elle vise un point qui change de temps en temps, et y va en
    // douceur. Une tête parfaitement immobile est ce qui trahit le plus un
    // personnage sans vie.
    if (t > idleState.nextLook) {
      idleState.look.x = (Math.random() - 0.5) * 0.18;
      idleState.look.y = (Math.random() - 0.5) * 0.34;
      idleState.nextLook = t + 2.5 + Math.random() * 4;
    }
    addRotation(rig.head,
      idleState.look.x + Math.sin(t * 0.7) * 0.012,
      idleState.look.y + Math.sin(t * 0.53) * 0.016,
      Math.sin(t * 0.29) * 0.01);
  }

  /** Enchaîne une pose de la bibliothèque, puis revient à la pose de repos. */
  function playGesture(seconds = 2.4) {
    idleState.target = 1;
    idleState.gestureUntil = clock.elapsedTime + seconds;
    if (poses.length < 2 || !action) return false;
    const others = poses.filter((c) => c !== clip);
    if (!others.length) return false;
    const next = others[Math.floor(Math.random() * others.length)];
    resetBaseRotations();
    const gesture = mixer.clipAction(next);
    gesture.reset();
    gesture.setLoop(THREE.LoopOnce, 1);
    gesture.clampWhenFinished = true;
    gesture.play();
    action.crossFadeTo(gesture, 0.45, false);
    setTimeout(() => {
      // Retour au repos : sans ce retour, chaque geste laisserait le
      // personnage dans sa dernière pose, exactement le défaut d'origine.
      action.reset().play();
      gesture.crossFadeTo(action, 0.6, false);
      setTimeout(resetBaseRotations, 700);   // après le fondu, pas pendant
    }, seconds * 1000);
    return true;
  }

  const clock = new THREE.Clock();
  let raf = 0, running = true, fps = 0, acc = 0, frames = 0;

  function loop() {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = clock.getDelta();
    mixer.update(dt);
    // APRÈS le mixer : on ajoute nos rotations à la pose qu'il vient d'écrire.
    animateIdle(clock.elapsedTime, dt);
    applyAnchor();

    // Respiration lumineuse : discrète, et surtout pas sur le sujet lui-même.
    const t = clock.elapsedTime;
    accent.intensity = 9 + Math.sin(t * 1.6) * 2;
    rim.intensity = 12 + Math.sin(t * 1.1 + 1.2) * 1.8;

    controls.update();
    composer.render();

    acc += dt; frames += 1;
    if (acc >= 0.5) { fps = Math.round(frames / acc); acc = 0; frames = 0; }
  }
  raf = requestAnimationFrame(loop);

  // Recadrage tant que la mesure reste dégénérée, pendant quelques frames.
  // Observé à plusieurs reprises : au premier appel la boîte englobante sort à
  // 0,009 m pour un personnage d'1,74 m — la hiérarchie du glTF n'est pas
  // encore à jour — et la caméra se retrouve collée au modèle. Une seule frame
  // de délai ne suffit pas toujours ; on réessaie jusqu'à obtenir une mesure
  // plausible, dix frames au maximum.
  let settleTries = 10;
  (function settleFraming() {
    if (!running || settleTries-- <= 0) return;
    if (!framing || framing.height < 0.05) {
      frame();
      requestAnimationFrame(settleFraming);
    }
  })();

  /* ---------------------------------------------------------- libération */
  function dispose() {
    running = false;
    cancelAnimationFrame(raf);
    observer.disconnect();
    controls.dispose();
    mixer.stopAllAction();
    mixer.uncacheRoot(model);

    // Géométries, matériaux ET textures : oublier les textures est la fuite la
    // plus coûteuse ici (une diffuse 2048² occupe ~16 Mo en VRAM).
    const seen = new Set();
    scene.traverse((node) => {
      if (node.geometry && !seen.has(node.geometry)) { seen.add(node.geometry); node.geometry.dispose(); }
      const materials = Array.isArray(node.material) ? node.material : (node.material ? [node.material] : []);
      for (const material of materials) {
        if (!material || seen.has(material)) continue;
        seen.add(material);
        for (const value of Object.values(material)) {
          if (value && value.isTexture && !seen.has(value)) { seen.add(value); value.dispose(); }
        }
        material.dispose();
      }
    });
    envRT.dispose();
    pmrem.dispose();
    bloom.dispose();
    composer.renderTarget1?.dispose();
    composer.renderTarget2?.dispose();
    draco.dispose();
    renderer.dispose();
    renderer.forceContextLoss?.();
    renderer.domElement.remove();
  }

  return {
    scene, camera, renderer, composer, controls, model, mixer, action,
    animations: gltf.animations || [],
    get fps() { return fps; },
    get framing() { return framing; },
    /** À appeler après un changement de pose/modèle : remesure et recadre. */
    recenter: (o) => frame(o),
    setBloom({ strength, radius, threshold }) {
      if (strength !== undefined) bloom.strength = strength;
      if (radius !== undefined) bloom.radius = radius;
      if (threshold !== undefined) bloom.threshold = threshold;
    },
    setAutoRotate(on) { controls.autoRotate = !!on; },
    /** Réaction visible quand JARVIS agit réellement (événement du bus). */
    gesture(seconds) { return playGesture(seconds); },
    /** 0 = calme, 1 = actif : amplifie la respiration et le ballant. */
    setActivity(level) {
      idleState.target = Math.max(0, Math.min(1, Number(level) || 0));
      idleState.gestureUntil = clock.elapsedTime + 3;
    },
    get poses() { return poses.map((c) => c.name); },
    pause() { running = false; cancelAnimationFrame(raf); },
    resume() { if (!running) { running = true; clock.getDelta(); raf = requestAnimationFrame(loop); } },
    resize,
    /** Réinstalle le canvas dans un nouveau conteneur (page hôte re-rendue). */
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

export default createAvatarViewer;
