/* ==========================================================================
   JarvisModelViewer — viewer GLB/GLTF réel (Three.js r160).

   Charge un vrai fichier exporté par l'atelier Blender de JARVIS et l'affiche
   avec : rotation, zoom, pan, reset caméra, plein écran, wireframe, fond
   transparent, lecture/pause des animations et sélecteur d'animation.

   Rien n'est codé en dur : la géométrie, les matériaux et les clips viennent
   du GLB. Si le fichier ne charge pas, le viewer affiche l'erreur réelle.
   ========================================================================== */
import * as THREE from 'three';
import { OrbitControls } from '../vendor/OrbitControls.js';
import { GLTFLoader } from '../vendor/GLTFLoader.js';
import { RoomEnvironment } from '../vendor/RoomEnvironment.js';

export class JarvisModelViewer {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.disposed = false;
    this.wireframe = false;
    this.transparent = !!options.transparent;
    this.clips = [];
    this.action = null;
    this.mixer = null;
    this.playing = true;
    this.root = null;
    this.onReady = options.onReady || (() => {});
    this.onError = options.onError || (() => {});

    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true, preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;

    this.scene = new THREE.Scene();
    this.setTransparent(this.transparent);

    // Éclairage d'environnement : le modèle est lisible sans dépendre du GLB.
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(3, 5, 4);
    this.scene.add(key);
    this.scene.add(new THREE.AmbientLight(0x9fb6c8, 0.5));

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
    this.camera.position.set(2.2, 1.6, 2.6);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.screenSpacePanning = true;

    this.clock = new THREE.Clock();
    this._resize = () => this.resize();
    window.addEventListener('resize', this._resize);
    this._observer = new ResizeObserver(() => this.resize());
    try { this._observer.observe(canvas.parentElement || canvas); } catch { /* ignoré */ }
    this.resize();
    this._loop();
  }

  /* ------------------------------------------------------------ chargement */
  async load(url) {
    return new Promise((resolve, reject) => {
      new GLTFLoader().load(
        url,
        (gltf) => {
          try {
            this._install(gltf);
            this.onReady(this.info());
            resolve(this.info());
          } catch (err) { this.onError(err); reject(err); }
        },
        undefined,
        (err) => {
          const message = (err && (err.message || err.type)) || 'chargement impossible';
          this.onError(new Error(message));
          reject(new Error(message));
        },
      );
    });
  }

  _install(gltf) {
    if (this.root) {
      this.scene.remove(this.root);
      this._dispose(this.root);
    }
    this.root = gltf.scene || gltf.scenes[0];
    this.scene.add(this.root);

    this.materials = [];
    this.root.traverse((node) => {
      if (!node.isMesh) return;
      node.castShadow = node.receiveShadow = false;
      const list = Array.isArray(node.material) ? node.material : [node.material];
      list.forEach((m) => { if (m) this.materials.push(m); });
    });

    this.clips = gltf.animations || [];
    this.mixer = this.clips.length ? new THREE.AnimationMixer(this.root) : null;
    if (this.mixer && this.clips.length) this.playClip(0);

    this.frame();
  }

  info() {
    let meshes = 0;
    let triangles = 0;
    this.root?.traverse((node) => {
      if (!node.isMesh || !node.geometry) return;
      meshes += 1;
      const geometry = node.geometry;
      triangles += geometry.index
        ? geometry.index.count / 3
        : (geometry.attributes.position?.count || 0) / 3;
    });
    return {
      meshes,
      triangles: Math.round(triangles),
      materials: new Set(this.materials.map((m) => m.name || m.uuid)).size,
      animations: this.clips.map((c) => c.name),
      skinned: !!this.root && this._hasSkin(),
    };
  }

  _hasSkin() {
    let found = false;
    this.root.traverse((n) => { if (n.isSkinnedMesh) found = true; });
    return found;
  }

  /* -------------------------------------------------------------- caméra */
  frame() {
    if (!this.root) return;
    const box = new THREE.Box3().setFromObject(this.root);
    if (!box.isEmpty()) {
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(0.05, size.length() / 2);
      const distance = radius / Math.tan((this.camera.fov * Math.PI) / 360) * 1.35;
      this.camera.near = Math.max(0.001, distance / 200);
      this.camera.far = distance * 40;
      this.camera.updateProjectionMatrix();
      this.camera.position.set(
        center.x + distance * 0.72,
        center.y + size.y * 0.28 + distance * 0.28,
        center.z + distance * 0.78,
      );
      this.controls.target.copy(center);
      this.home = { position: this.camera.position.clone(), target: center.clone() };
    }
    this.controls.update();
  }

  resetCamera() {
    if (!this.home) { this.frame(); return; }
    this.camera.position.copy(this.home.position);
    this.controls.target.copy(this.home.target);
    this.controls.update();
  }

  /* -------------------------------------------------------------- options */
  setWireframe(on) {
    this.wireframe = !!on;
    (this.materials || []).forEach((m) => { m.wireframe = this.wireframe; });
  }

  setTransparent(on) {
    this.transparent = !!on;
    this.renderer.setClearAlpha(this.transparent ? 0 : 1);
    this.scene.background = this.transparent ? null : new THREE.Color(0x0b0e14);
  }

  fullscreen() {
    const target = this.canvas.parentElement || this.canvas;
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    target.requestFullscreen?.();
  }

  /* ----------------------------------------------------------- animations */
  playClip(index) {
    if (!this.mixer || !this.clips[index]) return '';
    this.mixer.stopAllAction();
    this.action = this.mixer.clipAction(this.clips[index]);
    this.action.reset().play();
    this.playing = true;
    return this.clips[index].name;
  }

  setPlaying(on) {
    this.playing = !!on;
    if (this.action) this.action.paused = !this.playing;
  }

  togglePlay() { this.setPlaying(!this.playing); return this.playing; }

  /* --------------------------------------------------------------- boucle */
  resize() {
    const host = this.canvas.parentElement || this.canvas;
    const width = Math.max(1, host.clientWidth || this.canvas.clientWidth);
    const height = Math.max(1, host.clientHeight || this.canvas.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  _loop() {
    if (this.disposed) return;
    requestAnimationFrame(() => this._loop());
    const delta = this.clock.getDelta();
    if (this.mixer && this.playing) this.mixer.update(delta);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  _dispose(object) {
    object.traverse((node) => {
      if (node.geometry) node.geometry.dispose();
      const list = Array.isArray(node.material) ? node.material : [node.material];
      list.forEach((m) => {
        if (!m) return;
        Object.values(m).forEach((v) => { if (v && v.isTexture) v.dispose(); });
        m.dispose();
      });
    });
  }

  dispose() {
    this.disposed = true;
    window.removeEventListener('resize', this._resize);
    try { this._observer.disconnect(); } catch { /* ignoré */ }
    if (this.root) this._dispose(this.root);
    this.controls.dispose();
    this.renderer.dispose();
  }
}

/** Fabrique exposée aux scripts classiques (model_message.js). */
window.JarvisModelViewer = JarvisModelViewer;
window.createModelViewer = (canvas, options) => new JarvisModelViewer(canvas, options);
window.dispatchEvent(new CustomEvent('jarvis:model-viewer-ready'));

export default JarvisModelViewer;
