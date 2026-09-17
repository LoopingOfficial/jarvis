/* ==========================================================================
   JARVIS — holo_head.js
   Construction de la tête holographique : géométrie, squelette, peau.

   TOUT EST CALCULÉ ICI, rien n'est chargé. C'est le choix central : le dépôt
   contient treize GLB successifs et quatre documents de tentatives. Un modèle
   importé arrive avec le rig de quelqu'un d'autre, ses conventions d'axes et
   ses morphs absents ; on passe alors son temps à deviner ce qu'on a reçu.
   Une tête construite en code, on sait où est chaque os, parce qu'on l'a posé.

   Le squelette est un vrai THREE.Skeleton, pas une pile d'objets empilés :
   la mâchoire déforme le maillage par pondération de sommets, comme le ferait
   un rig d'atelier. C'est ce qui permet à la mâchoire d'entraîner la joue et
   le menton au lieu de pivoter comme un couvercle.

        racine
         └── cou
              └── tête
                   ├── mâchoire      (parole)
                   ├── œil G / œil D (regard)
                   └── sourcil G / D (expression)
   ========================================================================== */

import * as THREE from 'three';

/* Repères anatomiques, en unités de tête (1 = demi-hauteur du crâne). Ils
   servent à la fois à sculpter et à peser la peau : un seul jeu de chiffres,
   donc pas de dérive entre la forme et le rig. */
const A = {
  jawPivot: 0.02,      // hauteur de l'axe de rotation de la mâchoire
  jawReach: -0.98,     // le menton, point le plus bas influencé
  browY: 0.33,
  eyeY: 0.13,
  eyeX: 0.33,
  eyeZ: 0.70,
  mouthY: -0.44,
  mouthZ: 0.82,
};

const smooth = (t) => t * t * (3 - 2 * t);
const clamp01 = (v) => Math.max(0, Math.min(1, v));
/** Atténuation radiale autour d'un point : 1 au centre, 0 au-delà du rayon. */
const falloff = (d, radius) => (d >= radius ? 0 : smooth(1 - d / radius));

/* -------------------------------------------------------------- géométrie */
/**
 * Sculpte une sphère en tête stylisée. On travaille sur la sphère plutôt que
 * sur un maillage de visage importé pour garder une topologie régulière : les
 * poids de peau se calculent alors analytiquement, sans carte à peindre.
 */
function sculptHead() {
  const geometry = new THREE.SphereGeometry(1, 96, 72);
  const position = geometry.attributes.position;
  const v = new THREE.Vector3();

  for (let i = 0; i < position.count; i += 1) {
    v.fromBufferAttribute(position, i);
    const { x, y, z } = v;

    // 1. Crâne. Un crâne humain est nettement PLUS HAUT QUE LARGE (environ
    //    1,4 : 1) et plus profond que large. À 0,94 / 1,00 / 0,93 on restait
    //    dans la sphère, et une sphère à laquelle on ajoute des yeux se lit
    //    comme un masque, jamais comme une tête.
    let nx = x * 0.76;
    let ny = y * 1.18;
    let nz = z * (z < 0 ? 1.04 : 1.02);

    // 1b. Tempes resserrées au-dessus de la ligne des yeux : c'est ce
    //     rétrécissement qui donne au crâne sa forme d'œuf plutôt que de bulle.
    const temple = falloff(Math.abs(y - 0.45), 0.55) * clamp01(Math.abs(x) * 1.3);
    nx *= 1 - 0.10 * temple;

    // 1c. Occiput : l'arrière du crâne déborde vers le bas, il ne se referme
    //     pas en calotte régulière.
    if (z < 0) nz -= 0.09 * falloff(Math.abs(y + 0.18), 0.75);

    // 2. Mâchoire : le bas se resserre vers le menton. Le facteur ne descend
    //    pas à zéro — un menton en pointe donne un masque, pas un visage.
    if (y < 0) {
      const t = smooth(clamp01(-y));
      const taper = 1 - 0.27 * t * t;       // quadratique : large en haut,
      nx *= taper;                          // resserré seulement près du menton
      nz *= 1 - 0.13 * t;
      // Angle de la mâchoire, vers y = -0.35 : le maxillaire ressort avant de
      // plonger vers le menton.
      const angle = falloff(Math.abs(y + 0.35), 0.30) * clamp01(Math.abs(x) * 1.6);
      nx += Math.sign(x) * 0.07 * angle;
      if (z > 0) nz += 0.06 * t * t;
    }

    // 3. Arcade sourcilière : une crête, pas une bosse. Elle donne au front
    //    son ombre et c'est elle qui rend le regard lisible de loin.
    const brow = falloff(Math.hypot(x - 0, y - A.browY) , 0.42) * clamp01(z);
    nz += 0.085 * brow;
    ny += 0.02 * brow;

    // 4. Orbites : deux creux. Sans eux, les yeux flottent devant la face.
    for (const side of [-1, 1]) {
      const d = Math.hypot(x - side * A.eyeX, (y - A.eyeY) * 1.35);
      const socket = falloff(d, 0.34) * clamp01(z * 1.4);
      nz -= 0.075 * socket;
    }

    // 5. Nez : arête fine qui s'élargit en pointe et en narines. Le facteur
    //    précédent (0,16) restait dans le plan du visage ; un nez qui ne
    //    ressort pas prive le profil de son seul repère de profondeur.
    const bridge = falloff(Math.abs(x) * 3.4, 1) * falloff(Math.abs(y - 0.06) * 1.35, 1) * clamp01(z * 1.6);
    const tip = falloff(Math.hypot(x * 2.6, (y + 0.16) * 2.2), 0.42) * clamp01(z * 1.7);
    nz += 0.17 * bridge + 0.13 * tip;
    for (const side of [-1, 1]) {                 // ailes du nez
      const d = Math.hypot((x - side * 0.11) * 2.0, (y + 0.20) * 2.6);
      nz += 0.05 * falloff(d, 0.30) * clamp01(z);
    }

    // 5b. Lèvres : un léger bourrelet au-dessus du sillon, sinon la bouche
    //     n'est qu'un trou dans une surface lisse.
    const lips = falloff(Math.hypot(x * 1.9, (y - A.mouthY + 0.02) * 3.2), 0.34) * clamp01(z);
    nz += 0.055 * lips;

    // 5c. Menton : une saillie nette. Sans elle le bas du visage fuit et le
    //     personnage paraît sans mâchoire.
    const chin = falloff(Math.hypot(x * 2.2, (y + 0.80) * 1.9), 0.42) * clamp01(z);
    nz += 0.075 * chin;
    ny -= 0.02 * chin;

    // 6. Sillon de la bouche : un creux large et peu profond. La bouche
    //    elle-même est un maillage distinct, posé dans ce creux.
    const mouth = falloff(Math.hypot(x * 1.5, (y - A.mouthY) * 2.4), 0.55) * clamp01(z);
    nz -= 0.055 * mouth;

    // 7. Pommettes : elles rattrapent le creux des orbites.
    for (const side of [-1, 1]) {
      const d = Math.hypot((x - side * 0.44) * 1.2, (y + 0.22) * 1.6);
      nz += 0.05 * falloff(d, 0.46) * clamp01(z);
    }

    position.setXYZ(i, nx, ny, nz);
  }

  position.needsUpdate = true;
  geometry.computeVertexNormals();
  return geometry;
}

/* ---------------------------------------------------------------- squelette */
function buildSkeleton() {
  const root = new THREE.Bone();
  root.position.set(0, -1.35, 0);

  const neck = new THREE.Bone();
  neck.position.set(0, 0.35, 0);
  root.add(neck);

  const head = new THREE.Bone();
  head.position.set(0, 1.0, 0);
  neck.add(head);

  const jaw = new THREE.Bone();
  jaw.position.set(0, A.jawPivot, 0.10);
  head.add(jaw);

  const bones = { root, neck, head, jaw };
  for (const [name, side] of [['eyeL', 1], ['eyeR', -1]]) {
    const bone = new THREE.Bone();
    bone.position.set(side * A.eyeX, A.eyeY, A.eyeZ - 0.22);
    head.add(bone);
    bones[name] = bone;
  }
  for (const [name, side] of [['browL', 1], ['browR', -1]]) {
    const bone = new THREE.Bone();
    bone.position.set(side * A.eyeX, A.browY, A.eyeZ - 0.06);
    head.add(bone);
    bones[name] = bone;
  }

  const order = [root, neck, head, jaw, bones.eyeL, bones.eyeR, bones.browL, bones.browR];
  return { bones, skeleton: new THREE.Skeleton(order), order };
}

/**
 * Peinture des poids, par formule.
 *
 * La mâchoire n'emporte pas un bloc : son influence monte progressivement du
 * bas du visage vers la ligne de mâchoire, et s'annule derrière les oreilles.
 * C'est cette rampe qui fait que la joue suit le menton — sans elle, on voit
 * la découpe bouger d'un coup, et la tête a l'air cassée en deux.
 */
function skinHead(geometry, order) {
  const index = { root: 0, neck: 1, head: 2, jaw: 3 };
  const position = geometry.attributes.position;
  const skinIndices = [];
  const skinWeights = [];
  const v = new THREE.Vector3();

  for (let i = 0; i < position.count; i += 1) {
    v.fromBufferAttribute(position, i);

    // Rampe verticale : 0 au niveau de l'axe, 1 au menton.
    let jaw = clamp01((A.jawPivot - v.y) / (A.jawPivot - A.jawReach));
    jaw = smooth(jaw);
    // La nuque ne s'ouvre pas quand la bouche parle.
    jaw *= clamp01((v.z + 0.35) / 0.75);
    // Et l'articulation est sur les côtés : plein profil, l'influence tombe.
    jaw *= 0.35 + 0.65 * clamp01((0.95 - Math.abs(v.x)) / 0.95);

    const neck = smooth(clamp01((-0.55 - v.y) / 0.6)) * (1 - jaw) * 0.45;
    const head = Math.max(0, 1 - jaw - neck);

    skinIndices.push(index.jaw, index.head, index.neck, 0);
    skinWeights.push(jaw, head, neck, 0);
  }

  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(skinIndices, 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(skinWeights, 4));
  return order;
}

/* --------------------------------------------------------------- matériaux */
/**
 * Un hologramme n'est pas un objet éclairé : il est vu parce qu'il émet. La
 * base reste un matériau standard — three gère alors la peau et les ombres —
 * et le Fresnel, les lignes de balayage et le tremblement sont injectés dans
 * son shader. Réécrire le shader entièrement coûterait le support du skinning.
 */
function holoMaterial(accent, { wireframe = false, opacity = 0.5 } = {}) {
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(accent).multiplyScalar(0.18),
    emissive: new THREE.Color(accent),
    emissiveIntensity: 0.30,
    transparent: true,
    opacity,
    roughness: 0.35,
    metalness: 0.1,
    wireframe,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });

  material.userData.uniforms = { uTime: { value: 0 }, uPresence: { value: 1 } };

  material.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = material.userData.uniforms.uTime;
    shader.uniforms.uPresence = material.userData.uniforms.uPresence;

    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        varying vec3 vViewDir;
        varying vec3 vWorldNormal;
        varying float vHeight;`)
      .replace('#include <worldpos_vertex>', `#include <worldpos_vertex>
        vec4 holoWorld = modelMatrix * vec4( transformed, 1.0 );
        vViewDir = normalize( cameraPosition - holoWorld.xyz );
        vWorldNormal = normalize( mat3( modelMatrix ) * objectNormal );
        vHeight = holoWorld.y;`);

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform float uTime;
        uniform float uPresence;
        varying vec3 vViewDir;
        varying vec3 vWorldNormal;
        varying float vHeight;`)
      .replace('#include <dithering_fragment>', `#include <dithering_fragment>
        // Fresnel : les bords rasants s'allument, le centre reste translucide.
        // C'est ce qui donne le volume sans avoir à éclairer quoi que ce soit.
        float facing = abs( dot( normalize( vWorldNormal ), normalize( vViewDir ) ) );
        // Exposant élevé : la lueur se concentre sur les bords rasants. À 2.4
        // le centre du visage brillait autant que la silhouette et l'ensemble
        // se lisait comme une masse, pas comme une coque creuse.
        float fres = pow( 1.0 - facing, 3.4 );

        // Balayage : lignes fines qui montent. La fréquence est volontairement
        // élevée — trop lâche, on lit un store vénitien, pas une projection.
        float scan = 0.5 + 0.5 * sin( vHeight * 78.0 - uTime * 2.6 );
        scan = 0.82 + 0.18 * scan;

        // Tremblement : deux sinusoïdes non harmoniques, sinon l'œil entend
        // la boucle au bout de trois secondes.
        float flicker = 0.955 + 0.045 * sin( uTime * 41.0 ) * sin( uTime * 7.3 );

        // Courbes de niveau : des anneaux qui épousent la courbure. C'est ce
        // qui donne à un hologramme sa lecture de VOLUME — sans elles, la
        // surface reste plate quelle que soit la géométrie dessous.
        float contour = smoothstep( 0.72, 0.98, abs( sin( facing * 21.0 ) ) ) * 0.42;

        float glow = ( 0.09 + 1.75 * fres + contour ) * scan * flicker * uPresence;
        gl_FragColor.rgb *= glow;
        gl_FragColor.a *= clamp( 0.10 + 1.15 * fres + contour * 0.7, 0.0, 1.0 ) * uPresence;`);
  };

  return material;
}

/* --------------------------------------------------------- pièces du visage */
/** La bouche est un maillage distinct : une fente lumineuse posée dans le
 *  sillon. Elle porte les visèmes en la déformant (hauteur, largeur, avancée),
 *  pendant que la mâchoire, elle, ouvre réellement le bas du visage. Les deux
 *  ensemble donnent une parole ; l'une sans l'autre donne un poisson. */
function buildMouth(accent) {
  const group = new THREE.Group();

  // La bouche était additive comme le reste du visage : elle AJOUTAIT de la
  // lumière à une surface déjà lumineuse, donc elle ne se voyait pas. Une
  // bouche, c'est un trou — on la rend sombre, et c'est le liseré qui brille.
  const cavity = new THREE.Mesh(
    new THREE.CircleGeometry(0.26, 48),
    new THREE.MeshBasicMaterial({
      color: 0x03141c, transparent: true, opacity: 0.88,
      blending: THREE.NormalBlending, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  cavity.renderOrder = 3;
  group.add(cavity);

  const rim = new THREE.Mesh(
    new THREE.RingGeometry(0.245, 0.30, 48),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent), transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  rim.renderOrder = 4;
  group.add(rim);

  group.position.set(0, A.mouthY, A.mouthZ);
  group.rotation.x = -0.13;
  group.scale.set(1.10, 0.115, 1);      // au repos : un trait, pas un trou
  group.userData = { cavity, rim };
  return group;
}

function buildEye(accent, side) {
  const group = new THREE.Group();

  const sclera = new THREE.Mesh(
    new THREE.SphereGeometry(0.105, 32, 24),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent).multiplyScalar(0.55),
      transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }),
  );
  group.add(sclera);

  // L'iris est un anneau : un disque plein fait un œil de poupée. L'anneau,
  // lui, lit comme une optique — c'est ce qu'on attend d'une IA.
  const iris = new THREE.Mesh(
    new THREE.RingGeometry(0.038, 0.072, 40),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent),
      transparent: true, opacity: 1,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  iris.position.z = 0.098;
  group.add(iris);

  const pupil = new THREE.Mesh(
    new THREE.CircleGeometry(0.022, 24),
    new THREE.MeshBasicMaterial({
      color: 0xffffff, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }),
  );
  pupil.position.z = 0.104;
  group.add(pupil);

  // Paupière : un disque opaque au noir qui descend. En additif, « opaque »
  // n'existe pas — on la fait donc soustractive en masquant l'émission.
  const lid = new THREE.Mesh(
    new THREE.CircleGeometry(0.125, 28),
    new THREE.MeshBasicMaterial({
      color: 0x000000, transparent: true, opacity: 1,
      blending: THREE.NormalBlending, depthWrite: false,
    }),
  );
  lid.position.set(0, 0.245, 0.115);
  lid.renderOrder = 4;
  group.add(lid);

  group.position.set(side * A.eyeX, A.eyeY, A.eyeZ - 0.22);
  group.userData = { iris, pupil, lid, sclera };
  return group;
}

function buildBrow(accent, side) {
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-0.17 * side, -0.012, -0.02),   // côté nez, légèrement bas
    new THREE.Vector3(0, 0.026, 0.02),
    new THREE.Vector3(0.19 * side, 0.012, -0.03),     // côté tempe, relevé
  ]);
  const mesh = new THREE.Mesh(
    new THREE.TubeGeometry(curve, 24, 0.017, 8, false),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent), transparent: true, opacity: 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }),
  );
  mesh.position.set(side * A.eyeX, A.browY, A.eyeZ - 0.04);
  return mesh;
}

/** Halo de projection au sol : la tête doit venir de quelque part. */
function buildEmitter(accent) {
  const group = new THREE.Group();
  const disc = new THREE.Mesh(
    new THREE.RingGeometry(0.55, 1.35, 64),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent), transparent: true, opacity: 0.16,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  disc.rotation.x = -Math.PI / 2;
  disc.position.y = -2.05;   // sous les épaules, pas au milieu du buste
  group.add(disc);

  const cone = new THREE.Mesh(
    new THREE.ConeGeometry(1.15, 1.5, 48, 1, true),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(accent), transparent: true, opacity: 0.045,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    }),
  );
  cone.position.y = -1.45;
  group.add(cone);
  group.userData = { disc, cone };
  return group;
}


/* ------------------------------------------------------------------ buste */
/**
 * Cou et épaules. Une tête seule flotte : c'est la silhouette des épaules qui
 * fait lire une présence plutôt qu'un objet en lévitation.
 *
 * Profil tourné (LatheGeometry) plutôt que maillage sculpté : le buste n'a
 * aucune animation propre, une révolution suffit et coûte quelques centaines
 * de triangles. Il est attaché au GROUPE et non au cou — des épaules qui
 * suivraient la rotation de la tête seraient l'erreur la plus voyante.
 */
function buildBust(accent) {
  // Coupé à la naissance du torse : au-delà, il faudrait un buste crédible que
  // personne ne regarde, et la tête perdrait sa place dans le cadre.
  const profile = [
    new THREE.Vector2(0.001, -0.74),   // sous le menton, le cou commence
    new THREE.Vector2(0.28, -0.82),
    new THREE.Vector2(0.30, -1.10),
    new THREE.Vector2(0.34, -1.30),    // base du cou
    new THREE.Vector2(0.56, -1.44),    // trapèzes
    new THREE.Vector2(0.92, -1.58),
    new THREE.Vector2(1.16, -1.72),    // épaules
    new THREE.Vector2(1.24, -1.88),
  ];
  const geometry = new THREE.LatheGeometry(profile, 64);
  // Épaules plus larges que profondes : une révolution pure donne un tronc
  // cylindrique, jamais des épaules.
  geometry.scale(1, 1, 0.62);

  const material = holoMaterial(accent, { opacity: 0.30 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = 1;

  const wireMaterial = holoMaterial(accent, { wireframe: true, opacity: 0.14 });
  const wire = new THREE.Mesh(geometry, wireMaterial);
  wire.renderOrder = 2;

  const group = new THREE.Group();
  group.add(mesh, wire);
  group.userData = { materials: [material, wireMaterial] };
  return group;
}

/* ------------------------------------------------------------------ export */
/**
 * Assemble la tête complète.
 * @returns {{group, skinned, bones, mouth, eyes, brows, emitter, materials, dispose}}
 */
export function buildHoloHead({ accent = 0x22d3ee } = {}) {
  const group = new THREE.Group();

  const geometry = sculptHead();
  const { bones, skeleton, order } = buildSkeleton();
  skinHead(geometry, order);

  const shellMaterial = holoMaterial(accent, { opacity: 0.42 });
  const skinned = new THREE.SkinnedMesh(geometry, shellMaterial);
  skinned.add(bones.root);
  skinned.bind(skeleton);
  skinned.frustumCulled = false;
  skinned.renderOrder = 1;
  group.add(skinned);

  // Le filaire partage la MÊME géométrie et le MÊME squelette : il suit donc
  // la mâchoire sans un octet de plus et sans risque de désynchronisation.
  const wireMaterial = holoMaterial(accent, { wireframe: true, opacity: 0.22 });
  const wire = new THREE.SkinnedMesh(geometry, wireMaterial);
  wire.bind(skeleton, skinned.bindMatrix);
  wire.frustumCulled = false;
  wire.renderOrder = 2;
  group.add(wire);

  const mouth = buildMouth(accent);
  bones.jaw.add(mouth);

  const eyes = { left: buildEye(accent, 1), right: buildEye(accent, -1) };
  bones.eyeL.add(eyes.left);  eyes.left.position.set(0, 0, 0);
  bones.eyeR.add(eyes.right); eyes.right.position.set(0, 0, 0);

  const brows = { left: buildBrow(accent, 1), right: buildBrow(accent, -1) };
  bones.browL.add(brows.left);  brows.left.position.set(0, 0, 0);
  bones.browR.add(brows.right); brows.right.position.set(0, 0, 0);

  const bust = buildBust(accent);
  group.add(bust);

  const emitter = buildEmitter(accent);
  group.add(emitter);

  // Les matériaux du buste rejoignent la liste : sans cela ils resteraient
  // figés pendant que la tête respire, et le raccord sauterait aux yeux.
  const materials = [shellMaterial, wireMaterial, ...bust.userData.materials];

  return {
    group, skinned, wire, bones, skeleton, mouth, eyes, brows, emitter, bust, materials,
    anchor: A,
    /** Avance les shaders. Appelé une fois par frame par le viewer. */
    tick(elapsed, presence = 1) {
      for (const material of materials) {
        material.userData.uniforms.uTime.value = elapsed;
        material.userData.uniforms.uPresence.value = presence;
      }
    },
    dispose() {
      geometry.dispose();
      for (const material of materials) material.dispose();
      group.traverse((node) => {
        if (node.isMesh && node.geometry !== geometry) node.geometry?.dispose();
        if (node.isMesh && !materials.includes(node.material)) node.material?.dispose();
      });
    },
  };
}

export default buildHoloHead;
