/* ==========================================================================
   JARVIS Avatar — noyau : chargement du modèle, carte du rig, IK.

   Tout le reste (comportement, locomotion, parole) s'appuie sur ces trois
   briques :
     AvatarModel  — le GLB chargé, ses os, ses morph targets, ses clips
     RigControl   — écriture d'orientations sur les os, en repère monde
     solveTwoBoneIK — bras et jambes atteignent une cible sans se tordre

   Le modèle est produit par assets/blender/build_avatar.py : ses métadonnées
   (clips, visèmes, chaînes de doigts, longueur de foulée) voyagent dans les
   extras glTF, donc le runtime n'a rien à deviner.
   ========================================================================== */
import * as THREE from '../../vendor/three.module.js';
import { GLTFLoader } from '../../vendor/GLTFLoader.js';

const _q = new THREE.Quaternion();
const _q2 = new THREE.Quaternion();
const _m = new THREE.Matrix4();
const _v = new THREE.Vector3();
const _v2 = new THREE.Vector3();
const _v3 = new THREE.Vector3();

/* ------------------------------------------------------------------ modèle */
export class AvatarModel {
  constructor(gltf) {
    this.gltf = gltf;
    this.root = gltf.scene;
    this.bones = new Map();
    this.rest = new Map();
    this.meshes = [];
    this.skinned = [];
    this.morphMeshes = [];
    this.meta = {};
    this.clips = new Map();
    this.additiveClips = new Set();

    this._collect();
    this._readMeta();
    this._prepareClips();
  }

  _collect() {
    this.root.traverse((obj) => {
      if (obj.isBone) {
        this.bones.set(obj.name, obj);
        this.rest.set(obj.name, {
          quaternion: obj.quaternion.clone(),
          position: obj.position.clone(),
        });
      }
      if (obj.isMesh) {
        this.meshes.push(obj);
        obj.castShadow = true;
        obj.receiveShadow = true;
        obj.frustumCulled = false;       // un skin animé déborde souvent sa box
        if (obj.isSkinnedMesh) this.skinned.push(obj);
        if (obj.morphTargetDictionary) this.morphMeshes.push(obj);
      }
    });
    this.armature = this.root.getObjectByName('JARVIS_Armature') || this.root;
  }

  _readMeta() {
    // Les extras sont posés sur l'objet armature par le script Blender.
    let raw = null;
    this.root.traverse((obj) => {
      const extras = obj.userData || {};
      if (!raw && typeof extras.jarvis === 'string') raw = extras.jarvis;
    });
    try {
      this.meta = raw ? JSON.parse(raw) : {};
    } catch {
      this.meta = {};
    }
    this.meta.additiveClips = this.meta.additiveClips || [];
    this.meta.baseClips = this.meta.baseClips || [];
    this.meta.idleClips = this.meta.idleClips || [];
    this.meta.visemes = this.meta.visemes || [];
    this.meta.fingerChains = this.meta.fingerChains || {};
  }

  _prepareClips() {
    const additive = new Set(this.meta.additiveClips);
    for (const clip of this.gltf.animations || []) {
      if (additive.has(clip.name)) {
        // Un geste doit pouvoir se superposer à la marche ou à un idle :
        // il devient additif par rapport à sa première image (= pose de repos).
        THREE.AnimationUtils.makeClipAdditive(clip);
        this.additiveClips.add(clip.name);
      }
      this.clips.set(clip.name, clip);
    }
  }

  bone(name) { return this.bones.get(name) || null; }

  restOf(name) { return this.rest.get(name) || null; }

  /** Remet un os sur sa pose de repos (avant application des couches). */
  resetBone(name) {
    const bone = this.bones.get(name);
    const rest = this.rest.get(name);
    if (bone && rest) bone.quaternion.copy(rest.quaternion);
  }

  /** Applique une valeur de morph target sur toutes les meshes qui la portent. */
  setMorph(name, value) {
    for (const mesh of this.morphMeshes) {
      const index = mesh.morphTargetDictionary[name];
      if (index === undefined) continue;
      mesh.morphTargetInfluences[index] = value;
    }
  }

  getMorph(name) {
    for (const mesh of this.morphMeshes) {
      const index = mesh.morphTargetDictionary[name];
      if (index !== undefined) return mesh.morphTargetInfluences[index];
    }
    return 0;
  }

  clearMorphs(except = null) {
    for (const mesh of this.morphMeshes) {
      const dict = mesh.morphTargetDictionary;
      for (const name in dict) {
        if (except && except.has(name)) continue;
        mesh.morphTargetInfluences[dict[name]] = 0;
      }
    }
  }

  morphNames() {
    const names = new Set();
    for (const mesh of this.morphMeshes) {
      for (const name in mesh.morphTargetDictionary) names.add(name);
    }
    return [...names];
  }

  dispose() {
    this.root.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        mats.forEach((m) => m.dispose());
      }
    });
  }
}

/* --------------------------------------------------------------- chargement */
export function loadAvatar(url, { onProgress } = {}) {
  const loader = new GLTFLoader();
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (gltf) => {
        try {
          resolve(new AvatarModel(gltf));
        } catch (err) {
          reject(err);
        }
      },
      (event) => {
        if (onProgress && event.total) onProgress(event.loaded / event.total);
      },
      (err) => reject(err),
    );
  });
}

/* ------------------------------------------------------------------- rig */
export class RigControl {
  constructor(model) {
    this.model = model;
    this._parentInverse = new THREE.Quaternion();
    this._world = new THREE.Quaternion();
  }

  /** Oriente un os dans le repère MONDE, quel que soit son roll d'origine. */
  setWorldQuaternion(bone, worldQuat) {
    if (!bone) return;
    if (bone.parent) {
      bone.parent.getWorldQuaternion(this._parentInverse).invert();
      bone.quaternion.copy(this._parentInverse).multiply(worldQuat);
    } else {
      bone.quaternion.copy(worldQuat);
    }
  }

  /** Ajoute une rotation (repère monde) par-dessus l'orientation courante. */
  addWorldRotation(bone, axis, angle) {
    if (!bone || !angle) return;
    bone.getWorldQuaternion(this._world);
    _q.setFromAxisAngle(axis, angle);
    _q.multiply(this._world);
    this.setWorldQuaternion(bone, _q);
  }

  /**
   * Fait pointer l'axe local `localAxis` de l'os vers `targetWorld`,
   * en limitant l'écart à `maxAngle` — base du regard et du look-at de tête.
   */
  aimAt(bone, targetWorld, { localAxis = new THREE.Vector3(0, 1, 0),
    maxAngle = Math.PI, weight = 1 } = {}) {
    if (!bone) return;
    bone.getWorldPosition(_v);
    _v2.copy(targetWorld).sub(_v).normalize();
    bone.getWorldQuaternion(this._world);
    _v3.copy(localAxis).applyQuaternion(this._world).normalize();

    const dot = THREE.MathUtils.clamp(_v3.dot(_v2), -1, 1);
    let angle = Math.acos(dot);
    if (angle < 1e-4) return;
    angle = Math.min(angle, maxAngle) * weight;
    _v3.cross(_v2);
    if (_v3.lengthSq() < 1e-8) return;
    _v3.normalize();
    _q.setFromAxisAngle(_v3, angle);
    _q.multiply(this._world);
    this.setWorldQuaternion(bone, _q);
  }

  /** Rotation locale additive (pitch/yaw/roll en radians) sur la pose courante. */
  addLocalEuler(bone, x, y, z) {
    if (!bone) return;
    if (!x && !y && !z) return;
    _q.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
    bone.quaternion.multiply(_q);
  }
}

/* ---------------------------------------------------------------- IK 2 os */
const _origin = new THREE.Vector3();
const _target = new THREE.Vector3();
const _pole = new THREE.Vector3();
const _axis = new THREE.Vector3();
const _dir = new THREE.Vector3();

/**
 * IK analytique à deux os (bras ou jambe).
 * `poleWorld` fixe le plan de flexion : c'est ce qui évite qu'un coude parte
 * dans le torse ou qu'un genou se retourne.
 */
export function solveTwoBoneIK(rig, upper, lower, end, targetWorld, poleWorld,
  { weight = 1 } = {}) {
  if (!upper || !lower || !end || weight <= 0) return false;

  upper.getWorldPosition(_origin);
  const lenUpper = lower.getWorldPosition(_v).distanceTo(_origin);
  const lenLower = end.getWorldPosition(_v2).distanceTo(_v);
  const total = lenUpper + lenLower;
  if (total < 1e-5) return false;

  _target.copy(targetWorld);
  let distance = _origin.distanceTo(_target);
  const maxReach = total * 0.999;
  if (distance > maxReach) {
    // Cible hors de portée : on la ramène, sinon le membre se disloque.
    _target.sub(_origin).setLength(maxReach).add(_origin);
    distance = maxReach;
  }
  distance = Math.max(distance, 1e-4);

  // Angles du triangle (loi des cosinus).
  const cosUpper = THREE.MathUtils.clamp(
    (lenUpper * lenUpper + distance * distance - lenLower * lenLower)
    / (2 * lenUpper * distance), -1, 1);
  const cosJoint = THREE.MathUtils.clamp(
    (lenUpper * lenUpper + lenLower * lenLower - distance * distance)
    / (2 * lenUpper * lenLower), -1, 1);
  const angleUpper = Math.acos(cosUpper);
  const angleJoint = Math.PI - Math.acos(cosJoint);

  // Plan de flexion défini par le pôle.
  _dir.copy(_target).sub(_origin).normalize();
  _pole.copy(poleWorld).sub(_origin);
  _pole.addScaledVector(_dir, -_pole.dot(_dir));
  if (_pole.lengthSq() < 1e-8) _pole.set(0, 0, 1);
  _pole.normalize();
  _axis.copy(_dir).cross(_pole).normalize();

  const before = new THREE.Quaternion();
  // 1) aligner l'os supérieur sur la cible…
  upper.getWorldQuaternion(before);
  _v.set(0, 1, 0).applyQuaternion(before).normalize();
  _q.setFromUnitVectors(_v, _dir);
  _q.multiply(before);
  // 2) …puis l'écarter de l'angle du triangle.
  _q2.setFromAxisAngle(_axis, -angleUpper);
  _q.premultiply(_q2);
  if (weight < 1) {
    upper.getWorldQuaternion(before);
    _q.slerp(before, 1 - weight);
  }
  rig.setWorldQuaternion(upper, _q);
  upper.updateMatrixWorld(true);

  // Flexion de l'articulation.
  lower.getWorldQuaternion(before);
  _q.copy(before);
  _q2.setFromAxisAngle(_axis, angleJoint * weight);
  _q.premultiply(_q2);
  rig.setWorldQuaternion(lower, _q);
  lower.updateMatrixWorld(true);
  return true;
}

/* -------------------------------------------------------------- utilitaires */
export function damp(current, target, lambda, dt) {
  return THREE.MathUtils.damp(current, target, lambda, dt);
}

export function noise1D(seed) {
  const s = Math.sin(seed * 127.1) * 43758.5453;
  return s - Math.floor(s);
}

/** Bruit lisse 1D — sert aux micro-mouvements non répétitifs. */
export function smoothNoise(t, seed = 0) {
  const i = Math.floor(t);
  const f = t - i;
  const a = noise1D(i + seed);
  const b = noise1D(i + 1 + seed);
  const u = f * f * (3 - 2 * f);
  return (a + (b - a) * u) * 2 - 1;
}
