/* ==========================================================================
   AvatarLiveViewer — viewer 3D LIVE d'Avatar Studio.

   Etend le viewer GLB de l'atelier 3D avec ce qu'exige une création observée
   en direct :

     · swapTo(url)   charge la nouvelle preview EN ARRIÈRE-PLAN, puis échange
                     le modèle en conservant EXACTEMENT la caméra (position,
                     cible, zoom) — fondu de 150 ms, sans clignotement ;
     · si le nouveau GLB est invalide, l'ancien reste affiché et l'erreur
       réelle est remontée (aucune preview valide n'est jamais perdue) ;
     · cadrages caméra : Visage · Buste · Plein pied · Libre ;
     · wireframe, squelette (si rig), fond, plein écran, animations.
   ========================================================================== */
import * as THREE from 'three';
import { GLTFLoader } from '../vendor/GLTFLoader.js';
import { JarvisModelViewer } from './model_viewer.js';

const FADE_MS = 150;

export class AvatarLiveViewer extends JarvisModelViewer {
  constructor(canvas, options = {}) {
    super(canvas, options);
    this.skeletonHelper = null;
    this.showSkeleton = false;
    this.loadedVersion = 0;
    this.framing = 'free';
    this._swapToken = 0;
    this._fades = [];
    this.onSwap = options.onSwap || (() => {});
    this.onSwapError = options.onSwapError || (() => {});
  }

  /* ------------------------------------------------------ échange fluide */
  /**
   * Charge `url` en arrière-plan puis remplace le modèle sans bouger la caméra.
   * @returns {Promise<{version:number, info:object}>}
   */
  async swapTo(url, version = 0) {
    const token = ++this._swapToken;
    const gltf = await new Promise((resolve, reject) => {
      new GLTFLoader().load(
        url,
        resolve,
        undefined,
        (err) => reject(new Error((err && (err.message || err.type)) || 'GLB illisible')),
      );
    }).catch((err) => {
      // Preview invalide : on NE touche pas au modèle déjà affiché.
      this.onSwapError(err, version);
      throw err;
    });

    if (token !== this._swapToken || this.disposed) return { version, info: this.info() };

    const first = !this.root;
    const previous = this.root;
    const previousHelper = this.skeletonHelper;

    // Caméra mémorisée AVANT le swap : orientation, zoom et cible conservés.
    const camPos = this.camera.position.clone();
    const camTarget = this.controls.target.clone();

    const next = gltf.scene || gltf.scenes[0];
    this.root = next;
    this.scene.add(next);

    this.materials = [];
    next.traverse((node) => {
      if (!node.isMesh) return;
      node.castShadow = node.receiveShadow = false;
      const list = Array.isArray(node.material) ? node.material : [node.material];
      list.forEach((m) => { if (m) this.materials.push(m); });
    });
    this.setWireframe(this.wireframe);

    this.clips = gltf.animations || [];
    this.mixer = this.clips.length ? new THREE.AnimationMixer(next) : null;
    if (this.mixer && this.clips.length) this.playClip(0);

    if (first) {
      this.frame();
    } else {
      this.camera.position.copy(camPos);
      this.controls.target.copy(camTarget);
      // Surtout PAS de controls.update() ici : avec l'amortissement activé,
      // chaque appel consomme une étape du zoom résiduel et la caméra dérivait
      // de ~1,5 % à chaque preview (≈ 12 % sur huit étapes). La boucle de rendu
      // appelle déjà update() à chaque frame : l'échange reste ainsi neutre.
      this._fadeIn(next, previous, previousHelper);
    }

    this._rebuildSkeleton();
    this.loadedVersion = version || this.loadedVersion;
    const info = this.info();
    this.onSwap(info, version);
    return { version, info };
  }

  /** Fondu court du nouveau modèle, l'ancien reste visible en dessous. */
  _fadeIn(next, previous, previousHelper) {
    const affected = [];
    this.materials.forEach((m) => {
      affected.push({ material: m, transparent: m.transparent, opacity: m.opacity });
      m.transparent = true;
      m.opacity = 0;
    });
    const start = performance.now();
    const step = () => {
      if (this.disposed) return;
      const t = Math.min(1, (performance.now() - start) / FADE_MS);
      affected.forEach((entry) => { entry.material.opacity = entry.opacity * t; });
      if (t < 1) { requestAnimationFrame(step); return; }
      affected.forEach((entry) => {
        entry.material.transparent = entry.transparent;
        entry.material.opacity = entry.opacity;
      });
      if (previous) { this.scene.remove(previous); this._dispose(previous); }
      if (previousHelper) this.scene.remove(previousHelper);
    };
    requestAnimationFrame(step);
    void next;
  }

  /* ------------------------------------------------------------ squelette */
  _rebuildSkeleton() {
    if (this.skeletonHelper) {
      this.scene.remove(this.skeletonHelper);
      this.skeletonHelper = null;
    }
    if (!this.root || !this.hasSkeleton()) return;
    this.skeletonHelper = new THREE.SkeletonHelper(this.root);
    this.skeletonHelper.visible = this.showSkeleton;
    this.scene.add(this.skeletonHelper);
  }

  hasSkeleton() {
    let found = false;
    this.root?.traverse((n) => { if (n.isSkinnedMesh || n.isBone) found = true; });
    return found;
  }

  setSkeleton(on) {
    this.showSkeleton = !!on;
    if (this.skeletonHelper) this.skeletonHelper.visible = this.showSkeleton;
    return this.showSkeleton && !!this.skeletonHelper;
  }

  /* --------------------------------------------------------- cadrages */
  /**
   * Boîte englobante fiable d'un avatar riggé.
   *
   * `Box3.setFromObject` sous-estime un `SkinnedMesh` (il raisonne sur la pose
   * de liaison) : sur cet avatar il renvoyait une boîte de 0,2 m au lieu de
   * 1,6 m, et la caméra atterrissait dans les pieds. On unit donc les boîtes
   * de géométrie transformées ET la position monde des os.
   */
  computeBounds() {
    const box = new THREE.Box3();
    if (!this.root) return box;
    this.root.updateMatrixWorld(true);
    const point = new THREE.Vector3();
    this.root.traverse((node) => {
      if (node.isBone) {
        box.expandByPoint(node.getWorldPosition(point));
        return;
      }
      if (!node.isMesh || !node.geometry) return;
      if (!node.geometry.boundingBox) node.geometry.computeBoundingBox();
      const local = node.geometry.boundingBox;
      if (!local) return;
      const world = local.clone();
      // Un SkinnedMesh est posé par ses os : sa matrice monde ne doit pas
      // être appliquée deux fois.
      if (!node.isSkinnedMesh) world.applyMatrix4(node.matrixWorld);
      box.union(world);
    });
    return box;
  }

  /** Recadrage complet — remplace le cadrage du viewer de base. */
  frame() {
    if (!this.root) return;
    const box = this.computeBounds();
    if (box.isEmpty()) { super.frame(); return; }
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(0.05, size.length() / 2);
    const distance = (radius / Math.tan((this.camera.fov * Math.PI) / 360)) * 1.2;
    this.camera.near = Math.max(0.001, distance / 200);
    this.camera.far = distance * 40;
    this.camera.updateProjectionMatrix();
    this.camera.position.set(center.x, center.y + size.y * 0.06, center.z + distance);
    this.controls.target.copy(center);
    this.home = { position: this.camera.position.clone(), target: center.clone() };
    this.controls.update();
    this.framing = 'free';
  }

  /** 'face' | 'upper' | 'full' | 'free' — mêmes repères que les rendus Blender. */
  setFraming(mode) {
    if (!this.root) return;
    if (mode === 'free') { this.framing = 'free'; return; }
    const box = this.computeBounds();
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const height = Math.max(0.01, size.y);
    const width = Math.max(0.01, Math.max(size.x, size.z));

    const presets = {
      face: { y: box.min.y + height * 0.92, dist: height * 0.26 + width * 0.2 },
      upper: { y: box.min.y + height * 0.80, dist: height * 0.55 + width * 0.3 },
      full: { y: box.min.y + height * 0.52, dist: height * 1.3 + width * 0.2 },
    };
    const preset = presets[mode] || presets.full;
    const distance = Math.max(0.2, preset.dist);
    const target = new THREE.Vector3(center.x, preset.y, center.z);
    this.camera.near = Math.max(0.001, distance / 200);
    this.camera.far = distance * 60;
    this.camera.updateProjectionMatrix();
    this.camera.position.set(center.x, preset.y + height * 0.03, center.z + distance);
    this.controls.target.copy(target);
    this.controls.update();
    this.framing = mode;
  }

  /** Fond du viewer : 'studio' (dégradé sombre), 'dark', 'neutral', 'transparent'. */
  setBackground(kind) {
    this.background = kind;
    if (kind === 'transparent') { this.setTransparent(true); return; }
    this.setTransparent(false);
    const colors = { studio: 0x0a0f18, dark: 0x05070a, neutral: 0x8a929c };
    this.scene.background = new THREE.Color(colors[kind] ?? colors.studio);
  }

  dispose() {
    if (this.skeletonHelper) this.scene.remove(this.skeletonHelper);
    super.dispose();
  }
}

window.AvatarLiveViewer = AvatarLiveViewer;
window.createAvatarLiveViewer = (canvas, options) => new AvatarLiveViewer(canvas, options);
window.dispatchEvent(new CustomEvent('jarvis:avatar-live-viewer-ready'));

export default AvatarLiveViewer;
