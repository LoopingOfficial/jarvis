/* ==========================================================================
   JARVIS Avatar — déplacement dans la scène et mise en scène caméra.

     Stage               — positions nommées (home, desk, brain…) + obstacles
     LocomotionController— marche réelle : accélération, virages, arrêt net,
                           vitesse ASSERVIE au cycle d'animation (donc aucun
                           glissement de pied), IK de contact au sol
     CameraDirector      — CALL / PORTRAIT / HALF_BODY / FULL_BODY / FOCUS /
                           BRAIN_VIEW avec transitions continues

   Le principe anti-« moonwalk » : on ne choisit pas une vitesse puis une
   animation ; on choisit une vitesse ET on cale la vitesse de lecture du
   cycle dessus. La foulée exportée par Blender donne le facteur exact.
   ========================================================================== */
import * as THREE from '../../vendor/three.module.js';
import { solveTwoBoneIK } from './core.js';

const clamp = THREE.MathUtils.clamp;
const lerp = THREE.MathUtils.lerp;
const _v = new THREE.Vector3();
const _v2 = new THREE.Vector3();

/* ====================================================================== */
/* Scène                                                                  */
/* ====================================================================== */
export class Stage {
  constructor() {
    // Repère : X droite, Y haut, Z vers la caméra. L'avatar regarde +Z au repos.
    this.positions = {
      home: { position: new THREE.Vector3(0, 0, 0), facing: 0 },
      call: { position: new THREE.Vector3(0, 0, 0.15), facing: 0 },
      desk: { position: new THREE.Vector3(-0.95, 0, -0.35), facing: 0.55 },
      brain: { position: new THREE.Vector3(1.15, 0, -0.75), facing: -0.75 },
      center: { position: new THREE.Vector3(0, 0, -0.5), facing: 0 },
      left_panel: { position: new THREE.Vector3(-1.35, 0, -0.9), facing: 0.9 },
      right_panel: { position: new THREE.Vector3(1.35, 0, -0.9), facing: -0.9 },
      offstage: { position: new THREE.Vector3(2.6, 0, -1.6), facing: -1.2 },
    };
    // Obstacles : cercles à contourner. Volontairement minimal — une scène
    // de bureau n'a pas besoin d'un système de navigation AAA.
    this.obstacles = [
      { center: new THREE.Vector2(1.15, -1.45), radius: 0.55 },   // Brain Atlas
      { center: new THREE.Vector2(-1.30, -1.10), radius: 0.5 },   // bureau
    ];
  }

  get(name) { return this.positions[name] || null; }

  has(name) { return !!this.positions[name]; }

  names() { return Object.keys(this.positions); }

  /** Écarte un point des obstacles connus (évite de traverser le décor). */
  avoid(point) {
    const p = new THREE.Vector2(point.x, point.z);
    for (const o of this.obstacles) {
      const d = p.distanceTo(o.center);
      if (d < o.radius && d > 1e-4) {
        p.sub(o.center).setLength(o.radius).add(o.center);
      }
    }
    return new THREE.Vector3(p.x, 0, p.y);
  }

  /** Chemin simple : un point d'évitement s'il faut contourner, sinon direct. */
  path(from, to) {
    const points = [];
    const a = new THREE.Vector2(from.x, from.z);
    const b = new THREE.Vector2(to.x, to.z);
    for (const o of this.obstacles) {
      const ab = b.clone().sub(a);
      const len = ab.length();
      if (len < 1e-4) continue;
      const t = clamp(o.center.clone().sub(a).dot(ab) / (len * len), 0, 1);
      const closest = a.clone().addScaledVector(ab, t);
      const distance = closest.distanceTo(o.center);
      if (distance < o.radius + 0.25) {
        const push = closest.clone().sub(o.center);
        if (push.lengthSq() < 1e-6) push.set(ab.y, -ab.x);
        push.setLength(o.radius + 0.35).add(o.center);
        points.push(new THREE.Vector3(push.x, 0, push.y));
      }
    }
    points.push(to.clone());
    return points;
  }
}

/* ====================================================================== */
/* Locomotion                                                             */
/* ====================================================================== */
export class LocomotionController {
  constructor(model, mixer, root, stage) {
    this.model = model;
    this.mixer = mixer;
    this.root = root;                 // groupe qui porte l'avatar
    this.stage = stage;

    const meta = model.meta || {};
    this.cycleDistance = meta.walkCycleDistance || 1.2;
    this.cycleSeconds = meta.walkCycleSeconds || 1.0;
    this.naturalSpeed = this.cycleDistance / this.cycleSeconds;

    this.maxSpeed = this.naturalSpeed;
    this.speed = 0;
    this.targetSpeed = 0;
    this.acceleration = 1.9;
    this.deceleration = 3.2;
    this.turnRate = 2.6;              // rad/s

    this.facing = 0;                  // yaw courant (radians)
    this.targetFacing = 0;
    this.waypoints = [];
    this.destination = null;
    this.faceOnArrival = null;
    this.onArrive = null;
    this.state = 'idle';              // idle | walking | turning
    this.currentPlace = 'home';

    this.actions = new Map();
    this.base = null;
    this.idleClips = (meta.idleClips || []).filter((n) => model.clips.has(n));
    this.idleIndex = 0;

    for (const name of model.clips.keys()) {
      if (model.additiveClips.has(name)) continue;
      const action = mixer.clipAction(model.clips.get(name));
      action.setLoop(THREE.LoopRepeat, Infinity);
      this.actions.set(name, action);
    }
    this.legs = {
      L: ['upperLeg_L', 'lowerLeg_L', 'foot_L'].map((n) => model.bone(n)),
      R: ['upperLeg_R', 'lowerLeg_R', 'foot_R'].map((n) => model.bone(n)),
    };
    this.groundIK = true;
  }

  /* --------------------------------------------------------------- clips */
  _fade(name, duration = 0.35) {
    const action = this.actions.get(name);
    if (!action) return;
    if (this.base === action) return;
    if (this.base) this.base.fadeOut(duration);
    action.reset().setEffectiveWeight(1).fadeIn(duration).play();
    this.base = action;
    this.baseName = name;
  }

  playIdle(name = null) {
    if (!this.idleClips.length) return;
    if (name && this.model.clips.has(name)) {
      this._fade(name, 0.6);
      return;
    }
    this._fade(this.idleClips[this.idleIndex % this.idleClips.length], 0.7);
  }

  /** Variante d'idle suivante — évite toute boucle perceptible. */
  cycleIdle() {
    if (this.state !== 'idle' || !this.idleClips.length) return;
    let next = this.idleIndex;
    while (this.idleClips.length > 1 && next === this.idleIndex) {
      next = Math.floor(Math.random() * this.idleClips.length);
    }
    this.idleIndex = next;
    this.playIdle();
  }

  /* ------------------------------------------------------------- commandes */
  moveTo(target, { face = null, onArrive = null } = {}) {
    let destination;
    if (typeof target === 'string') {
      const place = this.stage.get(target);
      if (!place) return false;
      destination = place.position.clone();
      if (face === null) face = place.facing;
      this.currentPlace = target;
    } else {
      destination = new THREE.Vector3(target.x || 0, 0, target.z || 0);
      this.currentPlace = '';
    }
    destination = this.stage.avoid(destination);
    if (destination.distanceTo(this.root.position) < 0.12) {
      if (face !== null) this.turnTo(face);
      if (onArrive) onArrive();
      return true;
    }
    this.waypoints = this.stage.path(this.root.position, destination);
    this.destination = destination;
    this.faceOnArrival = face;
    this.onArrive = onArrive;
    this.state = 'walking';
    return true;
  }

  returnToDefaultPosition(onArrive = null) {
    return this.moveTo('home', { face: 0, onArrive });
  }

  /** Oriente le corps vers un angle (radians) ou un point du monde. */
  turnTo(target) {
    if (typeof target === 'number') {
      this.targetFacing = target;
    } else {
      _v.copy(target).sub(this.root.position);
      this.targetFacing = Math.atan2(_v.x, _v.z);
    }
    if (this.state === 'idle') this.state = 'turning';
  }

  stop() {
    this.waypoints = [];
    this.destination = null;
    this.targetSpeed = 0;
  }

  get moving() { return this.state === 'walking' || this.speed > 0.05; }

  /* ---------------------------------------------------------------- boucle */
  update(dt) {
    this._steer(dt);
    this._applyMotion(dt);
    this._chooseClip(dt);
  }

  _steer(dt) {
    if (this.state === 'walking' && this.waypoints.length) {
      const next = this.waypoints[0];
      _v.copy(next).sub(this.root.position);
      _v.y = 0;
      const distance = _v.length();

      if (distance < 0.14) {
        this.waypoints.shift();
        if (!this.waypoints.length) {
          this.state = 'stopping';
          this.targetSpeed = 0;
        }
        return;
      }
      this.targetFacing = Math.atan2(_v.x, _v.z);

      // On ralentit à l'approche, et on n'avance pas tant qu'on n'est pas
      // à peu près face à la cible : sinon le corps « dérape ».
      const angle = Math.abs(this._angleDelta(this.facing, this.targetFacing));
      const alignment = clamp(1 - angle / 1.1, 0, 1);
      const approach = clamp(distance / 0.9, 0.25, 1);
      this.targetSpeed = this.maxSpeed * alignment * approach;
    } else if (this.state === 'stopping') {
      this.targetSpeed = 0;
      if (this.speed < 0.05) {
        this.speed = 0;
        this.state = this.faceOnArrival !== null ? 'turning' : 'idle';
        if (this.faceOnArrival !== null) {
          this.targetFacing = this.faceOnArrival;
          this.faceOnArrival = null;
        }
        this.destination = null;
        if (this.onArrive) {
          const cb = this.onArrive;
          this.onArrive = null;
          cb();
        }
        this.playIdle();
      }
    } else if (this.state === 'turning') {
      this.targetSpeed = 0;
      if (Math.abs(this._angleDelta(this.facing, this.targetFacing)) < 0.05) {
        this.state = 'idle';
        this.playIdle();
      }
    }
  }

  _angleDelta(from, to) {
    let d = (to - from) % (Math.PI * 2);
    if (d > Math.PI) d -= Math.PI * 2;
    if (d < -Math.PI) d += Math.PI * 2;
    return d;
  }

  _applyMotion(dt) {
    const rate = this.targetSpeed > this.speed ? this.acceleration : this.deceleration;
    this.speed = THREE.MathUtils.damp(this.speed, this.targetSpeed, rate, dt);
    // On ne coupe la vitesse QUE pendant un ralentissement : sinon le seuil
    // annulerait la première frame d'accélération et la marche ne partirait
    // jamais (le corps resterait figé, cible atteinte ou non).
    if (this.targetSpeed <= 0 && this.speed < 0.015) this.speed = 0;

    const delta = this._angleDelta(this.facing, this.targetFacing);
    const maxTurn = this.turnRate * dt * (this.speed > 0.1 ? 1 : 1.4);
    this.facing += clamp(delta, -maxTurn, maxTurn);
    this.root.rotation.y = this.facing;

    if (this.speed > 0) {
      _v.set(Math.sin(this.facing), 0, Math.cos(this.facing))
        .multiplyScalar(this.speed * dt);
      this.root.position.add(_v);
    }
  }

  _chooseClip(dt) {
    const turning = Math.abs(this._angleDelta(this.facing, this.targetFacing));
    if (this.speed > 0.08) {
      this._fade('walk_forward', 0.28);
      const action = this.actions.get('walk_forward');
      if (action) {
        // LA règle anti-glissement : la vitesse de lecture suit la vitesse au sol.
        action.setEffectiveTimeScale(clamp(this.speed / this.naturalSpeed, 0.35, 1.7));
      }
    } else if (turning > 0.25
      && (this.state === 'turning' || this.state === 'walking')) {
      // Se réorienter avant de partir reste un mouvement : sans clip de
      // virage, le corps pivoterait en posture de repos.
      const clip = this._angleDelta(this.facing, this.targetFacing) > 0
        ? 'turn_left' : 'turn_right';
      this._fade(clip, 0.3);
    } else if (this.base && this.baseName
      && !this.idleClips.includes(this.baseName)) {
      this.playIdle();
    } else if (!this.base) {
      this.playIdle();
    }
  }

  /* -------------------------------------------------- contact au sol (IK) */
  applyFootIK() {
    if (!this.groundIK) return;
    for (const side of ['L', 'R']) {
      const [upper, lower, foot] = this.legs[side];
      if (!upper || !lower || !foot) continue;
      foot.getWorldPosition(_v);
      const groundY = this.root.position.y;
      if (_v.y >= groundY + 0.005) continue;
      // Le pied traverse le sol : on le repose, hanche et genou suivent.
      _v2.copy(_v);
      _v2.y = groundY + 0.005;
      const pole = new THREE.Vector3();
      upper.getWorldPosition(pole);
      pole.z += 1.0;                       // genou vers l'avant
      solveTwoBoneIK(this._rig, upper, lower, foot, _v2, pole, { weight: 0.9 });
    }
  }

  bindRig(rig) { this._rig = rig; }
}

/* ====================================================================== */
/* Caméra                                                                 */
/* ====================================================================== */
export const CAMERA_MODES = {
  // Cadrages calés sur un canevas portrait : la tête ne doit jamais être
  // rognée, et les mains doivent pouvoir entrer dans le champ.
  CALL: { offset: new THREE.Vector3(0.05, 1.44, 1.62), look: new THREE.Vector3(0, 1.38, 0), fov: 32 },
  PORTRAIT: { offset: new THREE.Vector3(0.04, 1.60, 1.02), look: new THREE.Vector3(0, 1.60, 0), fov: 28 },
  HALF_BODY: { offset: new THREE.Vector3(0.1, 1.32, 2.30), look: new THREE.Vector3(0, 1.15, 0), fov: 34 },
  FULL_BODY: { offset: new THREE.Vector3(0.35, 1.30, 3.30), look: new THREE.Vector3(0, 0.98, 0), fov: 38 },
  FOCUS: { offset: new THREE.Vector3(-0.35, 1.52, 1.05), look: new THREE.Vector3(0, 1.48, 0), fov: 32 },
  BRAIN_VIEW: { offset: new THREE.Vector3(-1.0, 1.55, 2.6), look: new THREE.Vector3(0.5, 1.2, -0.6), fov: 40 },
};

export class CameraDirector {
  constructor(camera, avatarRoot) {
    this.camera = camera;
    this.avatar = avatarRoot;
    this.mode = 'CALL';
    this.position = new THREE.Vector3();
    this.lookAt = new THREE.Vector3();
    this.fov = CAMERA_MODES.CALL.fov;
    this.transition = 0;
    this.speed = 1.6;
    this.handheld = true;             // micro-mouvement type webcam
    this.time = 0;
    this._apply(CAMERA_MODES.CALL, true);
  }

  setMode(mode, { instant = false } = {}) {
    const spec = CAMERA_MODES[mode];
    if (!spec) return false;
    this.mode = mode;
    this._target = spec;
    if (instant) this._apply(spec, true);
    return true;
  }

  _apply(spec, instant) {
    const base = this.avatar ? this.avatar.position : new THREE.Vector3();
    const goal = base.clone().add(spec.offset);
    const look = base.clone().add(spec.look);
    if (instant) {
      this.position.copy(goal);
      this.lookAt.copy(look);
      this.fov = spec.fov;
    }
    this._goal = goal;
    this._look = look;
    this._fovGoal = spec.fov;
  }

  update(dt) {
    this.time += dt;
    this._apply(this._target || CAMERA_MODES[this.mode], false);
    const k = 1 - Math.exp(-this.speed * dt);
    this.position.lerp(this._goal, k);
    this.lookAt.lerp(this._look, k);
    this.fov = lerp(this.fov, this._fovGoal, k);

    if (this.handheld) {
      // Une webcam premium n'est jamais parfaitement immobile.
      const sway = 0.004;
      this.camera.position.set(
        this.position.x + Math.sin(this.time * 0.37) * sway,
        this.position.y + Math.sin(this.time * 0.29 + 1.2) * sway * 0.8,
        this.position.z + Math.sin(this.time * 0.23 + 2.4) * sway * 0.5,
      );
    } else {
      this.camera.position.copy(this.position);
    }
    this.camera.lookAt(this.lookAt);
    if (Math.abs(this.camera.fov - this.fov) > 0.01) {
      this.camera.fov = this.fov;
      this.camera.updateProjectionMatrix();
    }
  }

  /** Point que l'avatar doit regarder pour « regarder la caméra ». */
  eyeTarget(out = new THREE.Vector3()) {
    return out.copy(this.camera.position);
  }
}
