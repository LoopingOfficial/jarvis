/* JARVIS 4 — Avatar humain 3D de JARVIS (Three.js, géométrie procédurale).
 * Un vrai buste humain stylisé (gravatar 3D) : tête, visage, cheveux, cou,
 * épaules, torse en veste, bras et mains. Aucun asset externe.
 * Les états modifient réellement la posture, le regard, les sourcils, la
 * bouche (parole), le clignement, l'empiècement ORL et l'anneau lumineux au sol.
 */
import * as THREE from '../vendor/three.module.js';

export const ROBOT_STATES = [
  'IDLE', 'LISTENING', 'THINKING', 'RECALLING', 'USING_TOOL', 'CODING',
  'BROWSING', 'DEPLOYING', 'LEARNING', 'VERIFYING', 'SPEAKING',
  'SUCCESS', 'WARNING', 'ERROR', 'SLEEPING',
];

const STATE_COLORS = {
  IDLE: 0x22d3ee, LISTENING: 0x67e8f9, THINKING: 0x38bdf8, RECALLING: 0x4ade80,
  USING_TOOL: 0x60a5fa, CODING: 0x818cf8, BROWSING: 0x34d399, DEPLOYING: 0xfbbf24,
  LEARNING: 0x4ade80, VERIFYING: 0x2dd4bf, SPEAKING: 0xa78bfa,
  SUCCESS: 0x34d399, WARNING: 0xfbbf24, ERROR: 0xfb7185, SLEEPING: 0x475569,
};

const SKIN = 0xf0b99b;
const SKIN_SHADE = 0xc98270;
const HAIR = 0x6b3f2b;
const HAIR_DARK = 0x352019;
const SHIRT = 0xe9e7e1;
const JACKET = 0x718096;
const JACKET_DARK = 0x4b586b;
const TROUSERS = 0x273449;
const SHOE = 0x17202d;

export class JarvisRobot {
  constructor(options = {}) {
    this.canvas = options.canvas;
    this.quality = options.quality || 'balanced';
    this.state = 'IDLE';
    this.reason = '';
    this.speakLevel = 0;
    this.fpsCtx = this._ctxSetup();
    this.run();
  }

  _ctxSetup() {
    if (!this.canvas) return null;
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';
    this._dpr = Math.min(window.devicePixelRatio || 1, this.quality === 'ultra' ? 2 : 1.5);
    this._width = this.canvas.clientWidth || 640;
    this._height = this.canvas.clientHeight || 640;
    this._resizeObserver = new ResizeObserver(() => {
      this._width = this.canvas.clientWidth || 640;
      this._height = this.canvas.clientHeight || 640;
      if (this.camera) {
        this.camera.aspect = this._width / this._height;
        this.camera.updateProjectionMatrix();
      }
      this.renderer.setSize(this._width, this._height, false);
    });
    this._resizeObserver.observe(this.canvas);
  }

  run() {
    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x040a14, 7, 15);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 60);
    camera.position.set(0, 1.22, 4.25);
    camera.lookAt(0, 1.12, 0);

    const renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: this.quality !== 'low',
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(this._dpr);
    renderer.setSize(this._width, this._height, false);
    renderer.shadowMap.enabled = this.quality !== 'low';
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;

    this.renderer = renderer;
    this.scene = scene;
    this.camera = camera;

    // ------- éclairage (studio portrait) -------
    const hemi = new THREE.HemisphereLight(0x9db8e6, 0x0a0d14, 0.75);
    scene.add(hemi);
    const key = new THREE.DirectionalLight(0xfff1df, 1.5);
    key.position.set(1.6, 3.2, 3.4);
    key.castShadow = true;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x5aa2e8, 0.5);
    fill.position.set(-2.4, 1.2, -1.6);
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0x22d3ee, 0.55);
    rim.position.set(-0.4, 2.6, -2.8);
    scene.add(rim);

    // ------- construction de l'avatar humain -------
    this.group = new THREE.Group();
    scene.add(this.group);
    this._build();
    this._buildPlatform();
    // CALL mode is a human portrait. The cyan hardware accents remain
    // available to the state machine but are not drawn over the character.
    this.core?.traverse((o) => { o.visible = false; });
    this.coreRing?.traverse((o) => { o.visible = false; });
    this.earL.visible = false;
    this.earR.visible = false;
    this.platform.visible = false;
    this.platform.userData.avatarAccent = true;
    this.underlight.material.opacity = 0.025;

    this.envDots = this._buildEnvDots();

    const clock = new THREE.Clock();
    const animate = () => {
      if (this._destroyed) return;
      requestAnimationFrame(animate);
      const dt = Math.min(clock.getDelta(), 0.05);
      this._t = (this._t || 0) + dt;
      this._animate(dt);
      renderer.render(scene, camera);
    };
    animate();
  }

  // ------------------------------------------------------------ matériaux
  _mat(color, opts = {}) {
    return new THREE.MeshStandardMaterial({
      color,
      roughness: opts.roughness ?? 0.85,
      metalness: opts.metalness ?? 0.02,
      emissive: opts.emissive ?? 0x000000,
      emissiveIntensity: opts.emissiveIntensity ?? 1,
      flatShading: opts.flat ?? false,
    });
  }

  _skin() { return this._mat(SKIN, { roughness: 0.7 }); }
  _skinShade() { return this._mat(SKIN_SHADE, { roughness: 0.7 }); }
  _hair() { return this._mat(HAIR, { roughness: 0.9 }); }
  _jacket() { return this._mat(JACKET, { roughness: 0.75, metalness: 0.05 }); }
  _shirt() { return this._mat(SHIRT, { roughness: 0.9 }); }
  _glow(color) { return this._mat(0x0c1424, { emissive: color, emissiveIntensity: 1.5, roughness: 0.3, metalness: 0.1 }); }

  _build() {
    const skin = this._skin();
    const shade = this._skinShade();
    const hair = this._hair();
    const jacket = this._jacket();
    const jacketDark = this._mat(JACKET_DARK, { roughness: 0.8 });
    const shirt = this._shirt();
    const trousers = this._mat(TROUSERS, { roughness: 0.82 });
    const shoe = this._mat(SHOE, { roughness: 0.55, metalness: 0.08 });
    const white = this._mat(0xf2f4f8, { roughness: 0.25 });

    // Full body rig hierarchy. These named groups are intentionally stable so
    // future GLB/IK controllers can target the same joints without changing
    // the public avatar API.
    this.rig = new THREE.Group();
    this.rig.name = 'JARVIS_Humanoid_Rig';
    this.joints = {};
    const joint = (name, parent, y = 0) => {
      const j = new THREE.Group(); j.name = name; j.position.y = y; parent.add(j);
      this.joints[name] = j; return j;
    };
    this.root = joint('root', this.rig);
    this.pelvis = joint('pelvis', this.root, 0.26);
    this.spine = joint('spine', this.pelvis, 0.26);
    this.chestJoint = joint('chest', this.spine, 0.45);
    this.rig.add(this.pelvis);

    // ============================ TORSE / VESTE ============================
    this.torso = new THREE.Group(); this.torso.name = 'torso';
    const chest = new THREE.Mesh(new THREE.CapsuleGeometry(0.47, 0.56, 7, 16), jacket);
    chest.scale.x = 0.92;
    chest.scale.z = 0.66;
    chest.scale.y = 1.08;
    chest.position.y = 0.82;
    chest.castShadow = true;
    this.torso.add(chest);

    const chestLower = new THREE.Mesh(new THREE.CapsuleGeometry(0.36, 0.28, 6, 14), jacketDark);
    chestLower.scale.z = 0.8;
    chestLower.position.y = 0.45;
    this.torso.add(chestLower);

    // Chemise + col.
    const shirtV = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.16, 0.24, 8), shirt);
    shirtV.position.set(0, 0.94, 0.4);
    shirtV.rotation.x = 0.3;
    this.torso.add(shirtV);
    const collarL = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.11, 0.05), shirt);
    collarL.position.set(-0.07, 1.18, 0.4);
    collarL.rotation.z = 0.5;
    collarL.rotation.x = 0.25;
    this.torso.add(collarL);
    const collarR = collarL.clone();
    collarR.position.x = 0.07;
    collarR.rotation.z = -0.5;
    this.torso.add(collarR);
    const pocket = new THREE.Mesh(new THREE.BoxGeometry(0.17, 0.16, 0.025), jacketDark);
    pocket.position.set(0.27, 0.78, 0.34);
    this.torso.add(pocket);
    for (const sx of [-0.11, 0.11]) {
      const button = new THREE.Mesh(new THREE.SphereGeometry(0.014, 8, 6), jacketDark);
      button.position.set(sx, 0.78, 0.38); this.torso.add(button);
    }

    // Revers de veste (lapels).
    const lapelL = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.34, 0.05), jacketDark);
    lapelL.position.set(-0.16, 1.02, 0.4);
    lapelL.rotation.z = 0.14;
    lapelL.rotation.x = 0.12;
    this.torso.add(lapelL);
    const lapelR = lapelL.clone();
    lapelR.position.x = 0.16;
    lapelR.rotation.z = -0.14;
    this.torso.add(lapelR);

    // Ceinture/assise du buste.
    const belt = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.37, 0.1, 14), jacketDark);
    belt.position.y = 0.28;
    this.torso.add(belt);

    // ====================== EMBLÈME CORE (états réels) ======================
    this.core = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12), this._glow(0x22d3ee));
    this.core.position.set(0.19, 1.03, 0.49);
    // Deliberately keep the state emitter off the human mesh; state color is
    // reflected by lighting and the surrounding UI instead.
    this.coreRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.055, 0.004, 8, 24),
      this._glow(0x22d3ee),
    );
    this.coreRing.material.transparent = true;
    this.coreRing.material.opacity = 0.28;
    // The identity cue stays in the ear lights and platform; the chest badge
    // remains available for state events but is hidden in the human CALL view.
    this.core.visible = false;
    this.coreRing.visible = false;
    this.coreRing.position.set(0.19, 1.03, 0.49);
    this.coreRing.rotation.y = 0.35;

    // ============================ ÉPAULES / BRAS ============================
    const shoulderL = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), jacket);
    shoulderL.position.set(-0.6, 0.98, 0.02);
    const shoulderR = shoulderL.clone();
    shoulderR.position.x = 0.6;
    this.torso.add(shoulderL, shoulderR);
    this.shoulderL = shoulderL;
    this.shoulderR = shoulderR;

    this.armL = this._arm(-1, jacketDark, skin);
    this.armR = this._arm(1, jacketDark, skin);
    this.torso.add(this.armL, this.armR);

    // Hips, legs and shoes keep the avatar ready for a later full-body scene.
    this.legL = this._leg(-1, trousers, shoe);
    this.legR = this._leg(1, trousers, shoe);
    this.torso.add(this.legL, this.legR);

    // ================================ COU ================================
    this.neck = new THREE.Group();
    const neckMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.12, 0.16, 12), skin);
    neckMesh.position.y = 0.08;
    this.neck.add(neckMesh);
    this.neck.position.y = 1.42;
    this.torso.add(this.neck);

    // ================================ TÊTE ================================
    this.head = new THREE.Group();
    this.head.position.y = 0.22;
    this.neck.add(this.head);

    // Crâne.
    const skull = new THREE.Mesh(new THREE.SphereGeometry(0.3, 32, 24), skin);
    skull.scale.set(1, 1.05, 0.97);
    skull.position.y = 0.02;
    skull.castShadow = true;
    this.head.add(skull);

    // Mâchoire / menton : élargit la base du visage.
    const jaw = new THREE.Mesh(new THREE.SphereGeometry(0.15, 20, 14), skin);
    jaw.scale.set(1.15, 0.85, 1);
    jaw.position.set(0, -0.22, 0.13);
    jaw.castShadow = true;
    this.head.add(jaw);

    // Pommettes légères.
    for (const sx of [-1, 1]) {
      const cheek = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 10), shade);
      cheek.scale.set(1, 0.9, 0.7);
      cheek.position.set(sx * 0.24, -0.02, 0.12);
      this.head.add(cheek);
    }

    // Oreilles.
    for (const sx of [-1, 1]) {
      const ear = new THREE.Mesh(new THREE.SphereGeometry(0.058, 12, 10), skin);
      ear.scale.set(0.45, 1, 0.55);
      ear.position.set(sx * 0.305, 0.02, 0);
      this.head.add(ear);
    }

    // ============================== YEUX ==============================
    const SCLERA = 0.068;
    const IRIS = 0.034;
    this.eyeL = new THREE.Group();
    this.eyeL.position.set(-0.11, 0.12, 0.27);
    const scleraL = new THREE.Mesh(new THREE.SphereGeometry(SCLERA, 20, 16), white);
    this.eyeL.add(scleraL);
    this.irisL = this._eyeIris();
    this.eyeL.add(this.irisL);
    this.head.add(this.eyeL);

    this.eyeR = new THREE.Group();
    this.eyeR.position.set(0.11, 0.12, 0.27);
    const scleraR = new THREE.Mesh(new THREE.SphereGeometry(SCLERA, 20, 16), white);
    this.eyeR.add(scleraR);
    this.irisR = this._eyeIris();
    this.eyeR.add(this.irisR);
    this.head.add(this.eyeR);

    // Sourcils.
    const browGeo = new THREE.BoxGeometry(0.17, 0.018, 0.035);
    this.browL = new THREE.Mesh(browGeo, hair);
    this.browL.position.set(-0.105, 0.205, 0.275);
    this.head.add(this.browL);
    this.browR = new THREE.Mesh(browGeo, hair);
    this.browR.position.set(0.105, 0.205, 0.275);
    this.head.add(this.browR);

    // Nez.
    const nose = new THREE.Mesh(new THREE.SphereGeometry(0.05, 14, 10), shade);
    nose.scale.set(0.62, 1.05, 0.9);
    nose.position.set(0, -0.01, 0.32);
    this.head.add(nose);

    // =============================== BOUCHE ===============================
    // Intérieur sombre lumineux (la « bouche » animée à la parole).
    this.mouth = new THREE.Mesh(
      new THREE.BoxGeometry(0.09, 0.03, 0.12),
      this._glow(0x22d3ee),
    );
    this.mouth.position.set(0, -0.145, 0.3);
    this.mouth.scale.y = 0.1;
    this.head.add(this.mouth);

    // Lèvres (statiques, s'écartent légèrement à la parole).
    const lipGeo = new THREE.BoxGeometry(0.15, 0.045, 0.11);
    this.lipU = new THREE.Mesh(lipGeo, shade);
    this.lipU.position.set(0, -0.125, 0.29);
    this.lipU.rotation.x = -0.14;
    this.head.add(this.lipU);
    this.lipL = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.035, 0.1), shade);
    this.lipL.position.set(0, -0.165, 0.29);
    this.lipL.rotation.x = 0.06;
    this.head.add(this.lipL);

    // =============================== CHEVEUX ===============================
    const hairCap = new THREE.Mesh(new THREE.SphereGeometry(0.318, 24, 18), hair);
    hairCap.scale.set(1.03, 0.82, 0.99);
    hairCap.position.set(0, 0.16, -0.045);
    this.head.add(hairCap);
    // Frange avant (2 mèches) pour casser le rond du crâne.
    for (const sx of [-0.07, 0.07]) {
      const fringe = new THREE.Mesh(new THREE.SphereGeometry(0.055, 12, 9), hair);
      fringe.scale.set(1.4, 0.8, 0.8);
      fringe.position.set(sx, 0.14, 0.23);
      this.head.add(fringe);
    }
    // Voluminous swept quiff inspired by the reference portrait.
    for (let i = 0; i < 7; i++) {
      const tuft = new THREE.Mesh(new THREE.SphereGeometry(0.075, 14, 10), hair);
      tuft.scale.set(1.7, 0.58, 0.78);
      tuft.position.set(-0.18 + i * 0.06, 0.28 + Math.sin(i * 0.65) * 0.025, 0.12 - Math.abs(i - 3) * 0.012);
      tuft.rotation.z = -0.2 + i * 0.06;
      tuft.rotation.y = -0.18 + i * 0.055;
      this.head.add(tuft);
    }
    // Sideburns.
    for (const sx of [-1, 1]) {
      const burn = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.12, 0.08), hair);
      burn.position.set(sx * 0.24, -0.02, 0.015);
      this.head.add(burn);
    }

    // Écouteur / implants ORL : la seule « lumière d'état » sur la tête.
    for (const sx of [-1, 1]) {
      const mic = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.03, 0.05, 8), this._glow(0x22d3ee));
      mic.rotation.x = Math.PI / 2;
      if (sx < 0) { this.earL = mic; } else { this.earR = mic; }
    }
    this.earL.position.set(-0.33, 0.03, 0.13);
    this.earR.position.set(0.33, 0.03, 0.13);
    this.head.add(this.earL, this.earR);

    this.group.add(this.rig, this.torso);
    this.rig.add(this.torso);
    this._buildFingerRig(this.armL, -1, skin);
    this._buildFingerRig(this.armR, 1, skin);
  }

  _leg(side, trousers, shoe) {
    const g = new THREE.Group(); g.name = side < 0 ? 'leg_L' : 'leg_R';
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.13, 0.34, 5, 12), trousers);
    upper.position.set(side * 0.18, -0.05, 0); upper.castShadow = true; g.add(upper);
    const lower = new THREE.Mesh(new THREE.CapsuleGeometry(0.105, 0.30, 5, 12), trousers);
    lower.position.set(side * 0.18, -0.43, 0); lower.castShadow = true; g.add(lower);
    const foot = new THREE.Mesh(new THREE.CapsuleGeometry(0.12, 0.20, 5, 12), shoe);
    foot.scale.z = 1.45; foot.position.set(side * 0.18, -0.72, 0.08); foot.castShadow = true; g.add(foot);
    g.position.y = 0.34; g.userData.side = side;
    this.joints[side < 0 ? 'upperLeg_L' : 'upperLeg_R'] = g;
    return g;
  }

  _buildFingerRig(arm, side, skin) {
    const hand = arm.children[2];
    hand.name = side < 0 ? 'hand_L' : 'hand_R';
    const fingers = [];
    for (let i = 0; i < 4; i++) {
      const f = new THREE.Mesh(new THREE.CapsuleGeometry(0.012, 0.055, 3, 6), skin);
      f.position.set(side * (0.035 + i * 0.018), -0.07, 0.055);
      f.rotation.x = Math.PI / 2; f.name = `${side < 0 ? 'finger_L' : 'finger_R'}_${i}`;
      hand.add(f); fingers.push(f);
    }
    const thumb = new THREE.Mesh(new THREE.CapsuleGeometry(0.014, 0.06, 3, 6), skin);
    thumb.position.set(side * 0.09, -0.01, 0.035); thumb.rotation.z = side * 0.8; hand.add(thumb);
    fingers.push(thumb);
    if (side < 0) this.fingersL = fingers; else this.fingersR = fingers;
  }

  _eyeIris() {
    const g = new THREE.Group();
    const iris = new THREE.Mesh(
      new THREE.SphereGeometry(0.034, 16, 12),
      this._mat(0x9a5a22, { roughness: 0.3 }),
    );
    iris.scale.set(1, 1, 0.42);
    iris.position.z = 0.018;
    g.add(iris);
    const pupil = new THREE.Mesh(
      new THREE.SphereGeometry(0.014, 10, 8),
      this._mat(0x0a0f16, { roughness: 0.2 }),
    );
    pupil.position.z = 0.036;
    g.add(pupil);
    return g;
  }

  _arm(side, sleeve, skin) {
    const g = new THREE.Group();
    // Bras haut (manche).
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.08, 0.3, 4, 10), sleeve);
    upper.position.set(0, -0.28, 0);
    upper.rotation.z = side * -0.16;
    upper.castShadow = true;
    g.add(upper);
    // Avant-bras (manche) + main.
    const fore = new THREE.Mesh(new THREE.CapsuleGeometry(0.065, 0.24, 4, 10), sleeve);
    fore.position.set(side * 0.05, -0.56, 0);
    fore.rotation.z = side * -0.22;
    fore.castShadow = true;
    g.add(fore);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.075, 12, 10), skin);
    hand.scale.set(0.85, 1.15, 0.75);
    hand.position.set(side * 0.09, -0.7, 0.02);
    g.add(hand);

    g.position.set(side * 0.58, 0.95, 0.03);
    g.userData.side = side;
    return g;
  }

  _buildPlatform() {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.98, 1.06, 48),
      new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.3, side: THREE.DoubleSide }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.001;
    this.platform = ring;
    this.scene.add(ring);
    const ring2 = new THREE.Mesh(
      new THREE.RingGeometry(1.2, 1.23, 48),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.14, side: THREE.DoubleSide }),
    );
    ring2.rotation.x = -Math.PI / 2;
    ring2.position.y = 0;
    ring2.visible = false;
    this.platformOuter = ring2;
    this.scene.add(ring2);
    // Halo d'état doux au sol.
    this.underlight = new THREE.Mesh(
      new THREE.RingGeometry(0.4, 1.0, 40),
      new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.12, side: THREE.DoubleSide }),
    );
    this.underlight.rotation.x = -Math.PI / 2;
    this.underlight.position.y = 0.002;
    this.scene.add(this.underlight);
  }

  _buildEnvDots() {
    const n = this.quality === 'ultra' ? 140 : 80;
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const r = 2.4 + ((i * 37) % 10) / 6;
      const y = -0.3 + Math.sin(i * 13.7) * 0.7;
      pos[i * 3] = Math.cos(a) * r;
      pos[i * 3 + 1] = y;
      pos[i * 3 + 2] = Math.sin(a) * r;
    }
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const mat = new THREE.PointsMaterial({
      color: 0x38bdf8, size: 0.014, transparent: true, opacity: 0.5,
    });
    const dots = new THREE.Points(geo, mat);
    this.scene.add(dots);
    return dots;
  }

  // ------------------------------------------------------------ API publique
  setState(state, extra = {}) {
    const st = (state || 'IDLE').toUpperCase();
    if (ROBOT_STATES.includes(st)) {
      this.state = st;
      this.reason = extra.reason || '';
      this._stateSince = this._t || 0;
      this._applyStateColor();
      if (this.onStateChange) this.onStateChange({ state: st, reason: this.reason });
    }
    return this;
  }

  /** High-level hooks for future scene choreography and real backend events. */
  setMode(mode) {
    this.mode = String(mode || 'CALL').toUpperCase();
    const full = this.mode === 'FULL_BODY';
    this.camera.position.z = full ? 6.6 : 4.25;
    this.camera.lookAt(0, full ? 0.72 : 1.12, 0);
    return this;
  }

  moveTo(position, duration = 900) {
    const p = typeof position === 'string' ? ({ home: [0, 0], brain: [0.35, 0], center: [0, 0], left_panel: [-0.45, 0], right_panel: [0.45, 0] }[position] || [0, 0]) : [position?.x || 0, position?.z || 0];
    this._moveFrom = { x: this.group.position.x, z: this.group.position.z };
    this._moveTo = { x: p[0], z: p[1], t: 0, duration: Math.max(160, duration) / 1000 };
    this.setState('WALKING', { reason: 'moving' });
    return this;
  }

  stop() { this._moveTo = null; if (this.state === 'WALKING') this.setState('IDLE'); return this; }
  returnToDefaultPosition() { return this.moveTo('home'); }
  lookAt(target = { x: 0, y: 1, z: 0 }) { this._lookTarget = target; return this; }
  gesture(name = 'neutral', intensity = 0.25) { this._gesture = { name: String(name), intensity: Math.max(0, Math.min(1, intensity)), t: 0 }; return this; }

  setReason(reason) {
    this.reason = reason || '';
  }

  setSpeakingLevel(level) {
    this.speakLevel = Math.max(0, Math.min(1, level || 0));
  }

  setQuality(q) {
    this.quality = q;
  }

  setVisible(v) {
    if (this.renderer) {
      this.renderer.domElement.style.visibility = v ? 'visible' : 'hidden';
    }
  }

  destroy() {
    this._destroyed = true;
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this.renderer) this.renderer.dispose();
  }

  _applyStateColor() {
    const c = STATE_COLORS[this.state] || STATE_COLORS.IDLE;
    const color = new THREE.Color(c);
    this.core.material.emissive.copy(color);
    this.coreRing.material.emissive.copy(color);
    this.mouth.material.emissive.copy(color);
    this.earL.material.emissive.copy(color);
    this.earR.material.emissive.copy(color);
    this.underlight.material.color.copy(color);
    // Intensité : ERROR = rouge très vif, SPEAKING = violet, sinon calme.
    this._targetEye = this.state === 'ERROR' ? 2.1 : this.state === 'SPEAKING' ? 1.7 : 1.0;
    this._underGlow = this.state === 'ERROR' ? 0.28 : this.state === 'SPEAKING' ? 0.2 : this.state === 'LISTENING' ? 0.16 : 0.1;
  }

  // ------------------------------------------------------------ animation
  _animate(dt) {
    const t = this._t;
    const st = this.state;
    // Keep the identity accent deliberately understated in the human-facing
    // call view even while state transitions update its internal color.
    if (this.core) this.core.visible = false;
    if (this.coreRing) this.coreRing.visible = false;

    if (this._moveTo) {
      this._moveTo.t = Math.min(this._moveTo.duration, this._moveTo.t + dt);
      const p = Math.min(1, this._moveTo.t / this._moveTo.duration);
      const ease = p * p * (3 - 2 * p);
      this.group.position.x = this._moveFrom.x + (this._moveTo.x - this._moveFrom.x) * ease;
      this.group.position.z = this._moveFrom.z + (this._moveTo.z - this._moveFrom.z) * ease;
      if (p >= 1) this.stop();
    }

    // Respiration naturelle (poitrine + épaules).
    const breath = Math.sin(t * 1.4) * 0.012 + Math.sin(t * 2.9) * 0.004;
    this.torso.scale.y = 1 + breath;
    this.torso.scale.x = 1 + breath * 0.35;
    this.armL.position.x = -0.58 + breath * 0.01;
    this.armR.position.x = 0.58 + breath * 0.01;

    // Emblème : pulsation selon l'état.
    const corePulse = st === 'SPEAKING'
      ? 1 + this.speakLevel * 0.4
      : st === 'THINKING' || st === 'RECALLING' ? 1 + Math.sin(t * 5.5) * 0.18 : 1 + Math.sin(t * 1.8) * 0.07;
    this.core.scale.setScalar(0.52 * corePulse);
    this.coreRing.scale.setScalar(0.62 + Math.abs(Math.sin(t * 2.2)) * (st === 'THINKING' ? 0.14 : 0.05));
    this.coreRing.rotation.z += dt * (st === 'THINKING' || st === 'RECALLING' ? 2.2 : 0.5);

    // Éclat : fondu.
    this.eyeGlow = this.eyeGlow || 1.0;
    const target = this._targetEye || 1.0;
    this.eyeGlow += (target - this.eyeGlow) * Math.min(1, dt * 5);
    this.earL.material.emissiveIntensity = this.eyeGlow;
    this.earR.material.emissiveIntensity = this.eyeGlow;
    this.core.material.emissiveIntensity = this.eyeGlow;
    this.coreRing.material.emissiveIntensity = this.eyeGlow * 0.35;
    this.mouth.material.emissiveIntensity = st === 'SPEAKING' ? this.speakLevel * 1.6 + 0.2 : 0.15;
    this.underlight.material.opacity += ((this._underGlow || 0.1) - this.underlight.material.opacity) * Math.min(1, dt * 5);
    this.underlight.scale.setScalar(1 + Math.sin(t * (st === 'SPEAKING' ? 5 : 1.3)) * 0.05);

    this._headPose(dt);

    // Bras vivants.
    const armSwing = Math.sin(t * 1.4) * 0.015;
    this.armL.rotation.z = -0.16 + armSwing;
    this.armR.rotation.z = 0.16 - armSwing;
    const walking = st === 'WALKING';
    const step = walking ? Math.sin(t * 7.2) * 0.12 : Math.sin(t * 0.65) * 0.008;
    if (this.legL && this.legR) {
      this.legL.rotation.x = step;
      this.legR.rotation.x = -step;
      this.armL.rotation.x = walking ? -step * 0.7 : 0;
      this.armR.rotation.x = walking ? step * 0.7 : 0;
    }
    const fingerWave = Math.sin(t * 1.7) * 0.025;
    for (const [i, f] of (this.fingersL || []).entries()) f.rotation.z = fingerWave * (i + 1) / 5;
    for (const [i, f] of (this.fingersR || []).entries()) f.rotation.z = -fingerWave * (i + 1) / 5;
    if (this._gesture) {
      this._gesture.t += dt;
      const g = this._gesture;
      const amount = Math.sin(Math.min(1, g.t / 0.45) * Math.PI) * g.intensity;
      if (g.name === 'explain' || g.name === 'open_hand') this.armR.rotation.x -= amount * 0.35;
      if (g.name === 'acknowledge' || g.name === 'agree') this.head.rotation.x += amount * 0.08;
      if (g.t > 1.2) this._gesture = null;
    }

    // Plateforme + environ.
    this.platform.rotation.z = -t * 0.04;
    this.envDots.rotation.y = t * 0.02;

    // Hochement très léger d'ensemble (idle).
    this.group.position.y = Math.sin(t * 1.4) * 0.006;
  }

  _headPose(dt) {
    const t = this._t;
    const st = this.state;
    let headY = 0, headX = 0, headZ = 0;
    switch (st) {
      case 'LISTENING':
        headY = 0.05 * Math.sin(t * 1.1);
        headX = -0.055;
        headZ = 0.035 * Math.sin(t * 0.6);
        break;
      case 'THINKING':
        headY = 0.05 * Math.sin(t * 0.75);
        headX = -0.14;
        break;
      case 'RECALLING':
        headY = 0.1 * Math.sin(t * 0.5);
        headX = -0.06;
        break;
      case 'SPEAKING':
        headY = 0.035 * Math.sin(t * 1.8);
        headX = -0.025 + Math.sin(t * 3.2) * 0.018;
        headZ = 0.018 * Math.sin(t * 2.1);
        break;
      case 'USING_TOOL':
      case 'LEARNING':
      case 'VERIFYING':
        headY = 0.03 * Math.sin(t * 1.1);
        headX = -0.075;
        headZ = 0.03 * Math.sin(t * 0.85);
        break;
      case 'CODING':
      case 'DEPLOYING':
        headX = -0.09;
        headY = 0.02 * Math.sin(t * 1.05);
        break;
      case 'BROWSING':
        headY = 0.04 * Math.sin(t * 1.4);
        headX = -0.035;
        break;
      case 'WARNING':
        headX = -0.09;
        headY = 0.025 * Math.sin(t * 2.3);
        break;
      case 'SUCCESS':
        headX = -0.015;
        headY = 0.04 * Math.sin(t * 1.6) + 0.055;
        break;
      case 'ERROR':
        headX = -0.11;
        headY = -0.035;
        break;
      case 'SLEEPING':
        headX = -0.32;
        headZ = -0.02;
        break;
      default:
        headY = 0.024 * Math.sin(t * 0.5);
        headX = 0.018 * Math.sin(t * 0.32);
    }
    const prev = this._headRot || { x: 0, y: 0, z: 0 };
    const k = Math.min(1, dt * 5);
    this._headRot = {
      x: prev.x + (headX - prev.x) * k,
      y: prev.y + (headY - prev.y) * k,
      z: prev.z + (headZ - prev.z) * k,
    };
    this.head.rotation.x = this._headRot.x;
    this.head.rotation.y = this._headRot.y;
    this.head.rotation.z = this._headRot.z;

    // Bouche : amplitude réelle → ouverture (glow interne + lèvres).
    const mouthTarget = st === 'SPEAKING' ? 0.45 + this.speakLevel * 1.3 : 0.08;
    this.mouth.scale.y += (mouthTarget - this.mouth.scale.y) * Math.min(1, dt * 12);
    const lipOpen = st === 'SPEAKING' ? this.speakLevel * 0.02 : 0;
    this.lipU.position.y -= (this.lipU.position.y - (-0.125 + lipOpen)) * Math.min(1, dt * 10);
    this.lipL.position.y -= (this.lipL.position.y - (-0.165 - lipOpen)) * Math.min(1, dt * 10);

    // Clignement (n'écrase pas l'iris du chat : on scale le groupe œil).
    if (!this._nextBlink) this._nextBlink = t + 2 + Math.random() * 4;
    if (t > this._nextBlink) {
      const p = (t - this._nextBlink) / 0.13;
      const eyeScale = p > 1 ? 1 : 1 - Math.sin(p * Math.PI) * 0.95;
      this.eyeL.scale.y = eyeScale;
      this.eyeR.scale.y = eyeScale;
      if (p > 1) this._nextBlink = t + 1.8 + Math.random() * 4.5;
    } else {
      this.eyeL.scale.y += (1 - this.eyeL.scale.y) * Math.min(1, dt * 8);
      this.eyeR.scale.y += (1 - this.eyeR.scale.y) * Math.min(1, dt * 8);
    }

    // Regard : haut-bas selon l'état (learning/thinking regarde « par le haut »).
    const lookY = st === 'THINKING' ? 0.02 : st === 'ERROR' ? -0.015 : 0;
    this.irisL.position.y += (lookY - this.irisL.position.y) * Math.min(1, dt * 4);
    this.irisR.position.y += (lookY - this.irisR.position.y) * Math.min(1, dt * 4);

    // Sourcils selon l'état.
    let browTarget = 0;
    if (st === 'THINKING' || st === 'RECALLING') browTarget = 0.035;
    else if (st === 'ERROR' || st === 'WARNING') browTarget = 0.06;
    else if (st === 'SUCCESS') browTarget = -0.02;
    this.browL.rotation.z += (browTarget - this.browL.rotation.z) * Math.min(1, dt * 4);
    this.browR.rotation.z += (-browTarget - this.browR.rotation.z) * Math.min(1, dt * 4);
    this.browL.position.y += ((st === 'ERROR' || st === 'WARNING' ? 0.006 : 0) - this.browL.position.y) * Math.min(1, dt * 4);
    this.browR.position.y += ((st === 'ERROR' || st === 'WARNING' ? 0.006 : 0) - this.browR.position.y) * Math.min(1, dt * 4);
  }
}
