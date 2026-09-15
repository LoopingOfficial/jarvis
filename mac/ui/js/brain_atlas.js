/* Brain Atlas — a quiet, explorable constellation of real /api/brain data. */
import * as THREE from '../vendor/three.module.js';
import { OrbitControls } from '../vendor/OrbitControls.js';

export const BRAIN_FAMILIES = [
  'MEMORY', 'KNOWLEDGE', 'PROJECTS', 'PEOPLE', 'SERVERS', 'TOOLS',
  'WORKFLOWS', 'APPLICATIONS', 'AUTOMATIONS', 'DECISIONS', 'ERRORS',
  'SOLUTIONS', 'DOCUMENTS', 'JARVIS',
];
const FAMILY_COLORS = {
  MEMORY: 0x74c9dd, KNOWLEDGE: 0x94c7a8, PROJECTS: 0x88aef1, PEOPLE: 0xd7a1c7,
  SERVERS: 0x9caec1, TOOLS: 0xdec18b, WORKFLOWS: 0xb4a5e5, APPLICATIONS: 0x80c4b9,
  AUTOMATIONS: 0xdba885, DECISIONS: 0xd3c589, ERRORS: 0xdc929c,
  SOLUTIONS: 0x9acb9e, DOCUMENTS: 0xa4b3e3, JARVIS: 0xd8f2f5,
};
const FAMILY_NAMES = {
  MEMORY: 'Mémoire', KNOWLEDGE: 'Savoirs', PROJECTS: 'Projets', PEOPLE: 'Personnes',
  SERVERS: 'Serveurs', TOOLS: 'Outils', WORKFLOWS: 'Workflows', APPLICATIONS: 'Applications',
  AUTOMATIONS: 'Automations', DECISIONS: 'Décisions', ERRORS: 'Erreurs',
  SOLUTIONS: 'Solutions', DOCUMENTS: 'Documents', JARVIS: 'JARVIS',
};
const MAX_NODES = 320;
const colorFor = (family) => FAMILY_COLORS[family] || 0x9caec1;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

export class BrainAtlas {
  constructor({ canvas, quality = 'balanced' } = {}) {
    this.canvas = canvas;
    this.quality = quality;
    this.nodes = [];
    this.edges = [];
    this.simNodes = [];
    this.nodeMap = new Map();
    this.simMap = new Map();
    this.labelsVisible = true;
    this._selectedId = null;
    this._hovered = null;
    this._query = '';
    this._activePulses = [];
    this._reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.run();
  }

  run() {
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    this.camera.position.set(0, 0, 10);
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas, antialias: true, alpha: true, powerPreference: 'low-power',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.quality === 'ultra' ? 2 : 1.5));
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 2;
    this.controls.maxDistance = 35;
    this.controls.autoRotate = false;
    this.controls.autoRotateSpeed = 0.35;
    this.controls.enablePan = true;
    this.nodeGroup = new THREE.Group();
    this.effectGroup = new THREE.Group();
    this.brainVisual = new THREE.Group();
    this.scene.add(this.brainVisual, this.nodeGroup, this.effectGroup);
    this._dummy = new THREE.Object3D();
    this._color = new THREE.Color();
    this._projected = new THREE.Vector3();
    this._ray = new THREE.Raycaster();
    this._pointer = new THREE.Vector2();
    this._haloTexture = this._makeHaloTexture();
    this._buildBrainVisual();
    this._buildLabels();
    this._buildSelectionRing();
    this._listeners = {};
    let down = null;
    this._listen('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    this._listen('pointermove', (e) => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) {
        down.dragged = true;
        this._setHover(null);
      } else if (!down) this._setHover(this._hit(e)?.node.id || null);
    });
    this._listen('pointerup', (e) => {
      if (down && !down.dragged) {
        const node = this._hit(e)?.node;
        if (node) this.onNodeClick?.(node);
        else this.deselect();
      }
      down = null;
    });
    this._listen('pointerleave', () => { down = null; this._setHover(null); });
    this._listen('pointercancel', () => { down = null; this._setHover(null); });
    this._resizeObserver = new ResizeObserver(() => this._resize());
    this._resizeObserver.observe(this.canvas);
    this._resize();
    this.clock = new THREE.Clock();
    this._t = 0;
    const loop = () => {
      if (this._destroyed) return;
      this._frame = requestAnimationFrame(loop);
      const dt = Math.min(this.clock.getDelta(), 0.05);
      if (!this.canvas.clientHeight || !this.canvas.getClientRects().length) return;
      this._t += dt;
      this.controls.update();
      this._writeInstances();
      this._pulseTick(dt);
      this._animateBrain(dt);
      this._updateSelectionRing();
      this.renderer.render(this.scene, this.camera);
      this._updateLabels();
    };
    loop();
  }

  /* A recognizable cortex behind the live memory graph. It is deliberately
   * built from primitives so it stays local, fast and works without assets. */
  _buildBrainVisual() {
    this._clearGroup(this.brainVisual);
    const shellMat = new THREE.MeshBasicMaterial({
      color: 0x55d8e8, transparent: true, opacity: 0.105,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    });
    const rimMat = new THREE.MeshBasicMaterial({
      color: 0x8beaf4, transparent: true, opacity: 0.42,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    // Extruded, lobulated silhouette: a readable brain shape from the front,
    // with real depth so it still feels like a 3D object when orbiting.
    const outline = [[0.02, 1.44], [0.34, 1.6], [0.7, 1.52], [0.92, 1.65],
      [1.28, 1.47], [1.45, 1.22], [1.7, 1.08], [1.58, 0.78], [1.76, 0.5],
      [1.58, 0.2], [1.72, -0.08], [1.55, -0.42], [1.62, -0.72],
      [1.38, -0.98], [1.25, -1.28], [0.92, -1.3], [0.64, -1.5], [0.28, -1.43], [0, -1.58]];
    [-1, 1].forEach((side) => {
      const shape = new THREE.Shape();
      outline.forEach(([x, y], i) => (i ? shape.lineTo(side * x, y) : shape.moveTo(side * x, y)));
      shape.closePath();
      const geo = new THREE.ExtrudeGeometry(shape, { depth: 0.62, bevelEnabled: true, bevelSegments: 3, bevelSize: 0.07, bevelThickness: 0.08, curveSegments: 3 });
      geo.translate(0, 0, -0.31);
      const hemi = new THREE.Mesh(geo, shellMat.clone());
      hemi.material.opacity = 0.16;
      this.brainVisual.add(hemi);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo), rimMat.clone());
      edge.material.opacity = 0.46;
      this.brainVisual.add(edge);
    });
    const ridge = (points, opacity = 0.22, width = 0.018) => {
      const curve = new THREE.CatmullRomCurve3(points);
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 18, width, 5, false), rimMat.clone());
      tube.material.opacity = opacity;
      this.brainVisual.add(tube);
    };
    const ys = [-1.12, -0.72, -0.28, 0.2, 0.68, 1.08];
    [-1, 1].forEach((side) => {
      ys.forEach((y, row) => {
        const width = 1.18 - Math.abs(y) * 0.2;
        const z = 0.58 + Math.sin(row * 1.7) * 0.09;
        ridge([
          new THREE.Vector3(side * 0.22, y - 0.2, z),
          new THREE.Vector3(side * (0.58 + width * 0.18), y + 0.02, z + 0.08),
          new THREE.Vector3(side * (0.72 + width * 0.22), y + 0.22, z - 0.02),
          new THREE.Vector3(side * 1.56, y + 0.05, z - 0.06),
        ], 0.18 + row * 0.012, 0.015);
      });
      [-0.75, 0.05, 0.82].forEach((y, i) => ridge([
        new THREE.Vector3(side * 0.38, y - 0.28, 0.66),
        new THREE.Vector3(side * (0.72 + i * 0.04), y, 0.72),
        new THREE.Vector3(side * 1.42, y + 0.28, 0.42),
      ], 0.14, 0.012));
    });
    // Deep inter-hemispheric fissure and a soft neural core.
    ridge([new THREE.Vector3(0, -1.58, 0.7), new THREE.Vector3(0.02, -0.5, 0.8), new THREE.Vector3(-0.02, 0.62, 0.8), new THREE.Vector3(0, 1.55, 0.68)], 0.58, 0.026);
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.22, 20, 14), new THREE.MeshBasicMaterial({ color: 0xd9fbff, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.brainVisual.add(core);
    this.brainVisual.scale.setScalar(1.12);
    this._brainCore = core;
    this._brainRim = rimMat;
  }

  _animateBrain(dt) {
    if (!this.brainVisual) return;
    const breathe = this._reducedMotion ? 0 : Math.sin(this._t * 1.5) * 0.018;
    this.brainVisual.scale.set(1.12 + breathe, 1.12 + breathe, 1.12 + breathe);
    this.brainVisual.rotation.y = Math.sin(this._t * 0.24) * 0.045;
    if (this._brainCore) {
      const glow = 0.72 + Math.sin(this._t * 2.8) * 0.18;
      this._brainCore.material.opacity = glow;
      this._brainCore.scale.setScalar(1 + Math.sin(this._t * 2.8) * 0.12);
    }
  }

  _listen(event, fn) {
    this._listeners[event] = fn;
    this.canvas.addEventListener(event, fn);
  }

  _resize() {
    this._w = this.canvas.clientWidth || 640;
    this._h = this.canvas.clientHeight || 320;
    this.camera.aspect = this._w / this._h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this._w, this._h, false);
    if (this.simNodes.length) this.discover(false);
  }

  loadBrain(brain) {
    const previousSelection = this._selectedId;
    const raw = brain || {};
    this.nodes = (raw.nodes || []).slice(0, MAX_NODES).map((n) => ({
      ...n,
      family: String(n.family || 'KNOWLEDGE').toUpperCase(),
      score: Number(n.score ?? n.confidence ?? n.meta?.confidence ?? 0.6),
      summary: n.summary || n.meta?.summary || '',
    }));
    this.nodeMap = new Map(this.nodes.map((n) => [n.id, n]));
    this.edges = (raw.edges || [])
      .map((e) => ({ a: e.a ?? e.source, b: e.b ?? e.target, kind: e.kind }))
      .filter((e) => this.nodeMap.has(e.a) && this.nodeMap.has(e.b)).slice(0, 900);
    const families = [...new Set(this.nodes.map((n) => n.family))]
      .sort((a, b) => BRAIN_FAMILIES.indexOf(a) - BRAIN_FAMILIES.indexOf(b));
    const outer = families.filter((f) => f !== 'JARVIS');
    const degree = new Map();
    this.edges.forEach((e) => { degree.set(e.a, (degree.get(e.a) || 0) + 1); degree.set(e.b, (degree.get(e.b) || 0) + 1); });
    this.simNodes = [];
    // Stable family islands keep the graph readable, even with hundreds of links.
    // Every point and every connection still corresponds to a real record.
    for (const family of families) {
      const members = this.nodes.filter((n) => n.family === family)
        .sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || String(a.id).localeCompare(String(b.id)));
      const fi = outer.indexOf(family);
      const angle = fi / Math.max(1, outer.length) * Math.PI * 2 - 0.96;
      // Pack records into a cortex-like volume instead of a flat constellation.
      const hemisphere = family === 'JARVIS' ? 0 : (fi % 2 ? 1 : -1);
      const ax = family === 'JARVIS' ? 0 : hemisphere * (0.5 + (fi % 4) * 0.28);
      const ay = family === 'JARVIS' ? 0 : Math.sin(angle) * 1.15;
      members.forEach((node, i) => {
        const spread = i ? Math.min(1.05, 0.28 + Math.sqrt(members.length) * 0.11) * Math.sqrt(i / Math.max(1, members.length - 1)) : 0;
        const phi = i * 2.399963;
        const x = ax + Math.cos(phi) * spread;
        const y = ay + Math.sin(phi) * spread * 0.8;
        const z = i ? 0.35 + Math.sin(i * 1.7 + fi) * spread * 0.55 : 0.48;
        this.simNodes.push({ node, x, y, z, pos3: new THREE.Vector3(x, y, z),
          representative: i === 0, degree: degree.get(node.id) || 0, dim: false, isHit: false,
          size: family === 'JARVIS' ? 1.6 : i === 0 ? 1.05 : 0.42 + Math.min(0.38, (degree.get(node.id) || 0) * 0.025),
        });
      });
    }
    this.simMap = new Map(this.simNodes.map((s) => [s.node.id, s]));
    this._clearGroup(this.nodeGroup);
    if (this.line) { this.scene.remove(this.line); this.line.geometry.dispose(); this.line.material.dispose(); }
    this._activePulses.forEach((p) => this._disposePulse(p));
    this._activePulses = [];
    const count = this.simNodes.length;
    this.mesh = null;
    this.halo = null;
    if (count) {
      this.mesh = new THREE.InstancedMesh(new THREE.SphereGeometry(0.07, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffffff }), count);
      this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.mesh.frustumCulled = false;
      this.nodeGroup.add(this.mesh);
      {
        this.halo = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.6, 0.6), new THREE.MeshBasicMaterial({
          color: 0xffffff, map: this._haloTexture, transparent: true, opacity: 0.5,
          blending: THREE.AdditiveBlending, depthWrite: false,
        }), count);
        this.halo.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        this.halo.frustumCulled = false;
        this.halo.visible = this.quality !== 'reduced';
        this.nodeGroup.add(this.halo);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(this.edges.length * 6), 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(this.edges.length * 6), 3));
    this.line = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.52, depthWrite: false,
    }));
    this.scene.add(this.line);
    this._selectedId = this.nodeMap.has(previousSelection) ? previousSelection : null;
    this._hovered = null;
    this.highlight(this._query);
    this._renderLegend(families);
    this._hasFramed = false;
    this.discover(false);
    const empty = document.getElementById('brainEmpty');
    if (empty) empty.hidden = count > 0;
    this.onGraphLoaded?.({ nodes: count, edges: this.edges.length });
    return { nodes: count, edges: this.edges.length };
  }

  _writeInstances() {
    if (!this.mesh) return;
    const d = this._dummy;
    const c = this._color;
    this.simNodes.forEach((s, i) => {
      const focused = s.node.id === this._selectedId || s.node.id === this._hovered;
      const connected = !this._selectedId || s.node.id === this._selectedId || this.edges.some((e) =>
        (e.a === this._selectedId && e.b === s.node.id) || (e.b === this._selectedId && e.a === s.node.id));
      c.setHex(colorFor(s.node.family));
      if (s.dim || !connected) c.multiplyScalar(0.17);
      else if (focused || s.isHit) c.lerp(new THREE.Color(0xffffff), 0.45);
      d.position.copy(s.pos3);
      d.quaternion.identity();
      const size = s.size * (focused ? 1.25 : 1);
      d.scale.setScalar(size);
      d.updateMatrix();
      this.mesh.setMatrixAt(i, d.matrix);
      this.mesh.setColorAt(i, c);
      if (this.halo) {
        d.quaternion.copy(this.camera.quaternion);
        d.scale.setScalar(size * (focused ? 1.3 : 1));
        d.updateMatrix();
        this.halo.setMatrixAt(i, d.matrix);
        this.halo.setColorAt(i, c);
      }
    });
    this.mesh.instanceMatrix.needsUpdate = true;
    this.mesh.instanceColor.needsUpdate = true;
    if (this.halo) { this.halo.instanceMatrix.needsUpdate = true; this.halo.instanceColor.needsUpdate = true; }
  }

  _updateLinePositions() {
    if (!this.line) return;
    const pos = this.line.geometry.attributes.position.array;
    const col = this.line.geometry.attributes.color.array;
    this.edges.forEach((e, i) => {
      const a = this.simMap.get(e.a), b = this.simMap.get(e.b);
      const selected = this._selectedId && (e.a === this._selectedId || e.b === this._selectedId);
      const dim = a.dim || b.dim || (this._selectedId && !selected);
      const strength = dim ? 0.035 : selected ? 1 : a.node.family === b.node.family ? 0.44 : 0.19;
      [a, b].forEach((s, j) => {
        const at = i * 6 + j * 3;
        pos[at] = s.x; pos[at + 1] = s.y; pos[at + 2] = s.z;
        const c = this._color.setHex(selected ? 0xcdebf0 : colorFor(s.node.family)).multiplyScalar(strength);
        col[at] = c.r; col[at + 1] = c.g; col[at + 2] = c.b;
      });
    });
    this.line.geometry.attributes.position.needsUpdate = true;
    this.line.geometry.attributes.color.needsUpdate = true;
    this.line.geometry.computeBoundingSphere();
  }

  // Compatibility with the dashboard's data-refresh API.
  scaleSizes() { this._writeInstances(); }
  _setHover(id) {
    if (this._hovered === id) return;
    this._hovered = id;
    this.canvas.style.cursor = id ? 'pointer' : 'grab';
  }
  _hit(e) {
    const r = this.canvas.getBoundingClientRect();
    this._pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this._ray.setFromCamera(this._pointer, this.camera);
    let best = null, nearest = Infinity;
    for (const s of this.simNodes) {
      if (s.dim) continue;
      const distance = this._ray.ray.distanceSqToPoint(s.pos3);
      const radius = Math.max(0.09, s.size * 0.12);
      if (distance < radius * radius && distance < nearest) { best = s; nearest = distance; }
    }
    return best;
  }
  selectNode(id) {
    if (!this.nodeMap.has(id)) return;
    this._selectedId = id;
    this.controls.autoRotate = false;
    this._updateLinePositions();
  }
  deselect() { this._selectedId = null; this._updateLinePositions(); }

  _buildSelectionRing() {
    this._ring = new THREE.Mesh(new THREE.RingGeometry(0.115, 0.125, 48), new THREE.MeshBasicMaterial({
      color: 0xd7f1f5, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false,
    }));
    this._ring.visible = false;
    this.effectGroup.add(this._ring);
  }
  _updateSelectionRing() {
    const s = this.simMap.get(this._selectedId);
    this._ring.visible = !!s;
    if (!s) return;
    this._ring.position.copy(s.pos3);
    this._ring.quaternion.copy(this.camera.quaternion);
    this._ring.scale.setScalar(s.size * (this._reducedMotion ? 1 : 1.12 + Math.sin(this._t * 2) * 0.06));
  }

  _buildLabels() {
    this._labelsHost = document.createElement('div');
    this._labelsHost.className = 'brain-labels';
    this.canvas.parentElement.appendChild(this._labelsHost);
    this._labelPool = Array.from({ length: 24 }, () => {
      const el = document.createElement('div');
      el.className = 'brain-label';
      el.hidden = true;
      this._labelsHost.appendChild(el);
      return el;
    });
  }
  toggleLabels() { this.labelsVisible = !this.labelsVisible; return this.labelsVisible; }
  _updateLabels() {
    this._labelPool.forEach((el) => { el.hidden = true; });
    const emphasis = [this._hovered, this._selectedId].filter(Boolean);
    const queue = this.simNodes.filter((s) => !s.dim &&
      (emphasis.includes(s.node.id) || (this.labelsVisible && (this._query ? s.isHit : s.representative))));
    queue.sort((a, b) => Number(emphasis.includes(b.node.id)) - Number(emphasis.includes(a.node.id)) || b.degree - a.degree);
    const used = [];
    const budget = Math.min(this._labelPool.length, this._w < 450 ? 9 : 15);
    let count = 0;
    for (const s of queue) {
      if (count >= budget) break;
      const p = this._projected.copy(s.pos3).project(this.camera);
      if (p.z > 1 || p.z < -1) continue;
      const isFamily = s.representative && !emphasis.includes(s.node.id) && !this._query;
      const label = (isFamily ? FAMILY_NAMES[s.node.family] || s.node.family : s.node.label || s.node.id).slice(0, 32);
      const width = Math.min(190, label.length * 5.7 + 22);
      const x = (p.x * 0.5 + 0.5) * this._w;
      const y = (-p.y * 0.5 + 0.5) * this._h + 17;
      const box = { left: x - width / 2, right: x + width / 2, top: y - 10, bottom: y + 10 };
      if (box.left < 9 || box.right > this._w - 9 || box.top < 15 || box.bottom > this._h - 37) continue;
      if (used.some((b) => box.left < b.right + 8 && box.right > b.left - 8 && box.top < b.bottom + 6 && box.bottom > b.top - 6)) continue;
      used.push(box);
      const el = this._labelPool[count++];
      el.textContent = label;
      el.classList.toggle('hover', emphasis.includes(s.node.id));
      el.classList.toggle('family', isFamily);
      el.style.setProperty('--ld', this._familyCss(s.node.family));
      el.style.left = x + 'px';
      el.style.top = y + 'px';
      el.hidden = false;
    }
  }
  _familyCss(family) { return '#' + colorFor(family).toString(16).padStart(6, '0'); }
  _renderLegend(families) {
    const host = document.getElementById('brainLegend');
    if (!host) return;
    host.innerHTML = families.map((family) => {
      const count = this.nodes.filter((n) => n.family === family).length;
      return `<button type="button" class="legend-item" data-family="${esc(family)}" style="--ld:${this._familyCss(family)}" aria-pressed="false" title="Explorer ${esc(FAMILY_NAMES[family] || family)}"><i class="legend-dot"></i>${esc(FAMILY_NAMES[family] || family)}<span>${count}</span></button>`;
    }).join('');
    host.querySelectorAll('button').forEach((button) => {
      button.onclick = () => {
        const query = this._query === button.dataset.family.toLowerCase() ? '' : button.dataset.family;
        const input = document.getElementById('brainSearch');
        if (input) input.value = query;
        this.highlight(query);
      };
    });
    this._updateSearchStatus();
  }
  highlight(query) {
    this._query = String(query || '').toLowerCase().trim();
    for (const s of this.simNodes) {
      const n = s.node;
      const hit = !this._query || [n.label, n.family, n.summary, FAMILY_NAMES[n.family]].some((v) => String(v || '').toLowerCase().includes(this._query));
      s.dim = !!this._query && !hit;
      s.isHit = !!this._query && hit;
    }
    this._updateLinePositions();
    this._updateSearchStatus();
  }
  _updateSearchStatus() {
    const status = document.getElementById('brainSearchStatus');
    if (status) {
      const count = this.simNodes.filter((s) => !s.dim).length;
      status.textContent = this._query ? `${count} résultat${count === 1 ? '' : 's'}` : 'VUE D’ENSEMBLE';
      status.classList.toggle('filtered', !!this._query);
    }
    document.querySelectorAll('#brainLegend button').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.family.toLowerCase() === this._query));
    });
  }

  pulse(ids, color) {
    if (this._reducedMotion) return;
    const origin = new THREE.Vector3(0, 0, 0.82);
    for (const [index, id] of (ids || []).slice(0, 12).entries()) {
      const s = this.simMap.get(id);
      if (!s) continue;
      const tint = color || colorFor(s.node.family);
      const traveler = new THREE.Mesh(new THREE.SphereGeometry(0.075, 12, 8), new THREE.MeshBasicMaterial({
        color: tint, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      traveler.position.copy(origin);
      const trailGeo = new THREE.BufferGeometry().setFromPoints([origin, origin]);
      const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({
        color: tint, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      this.effectGroup.add(trail, traveler);
      this._activePulses.push({ traveler, trail, origin: origin.clone(), target: s.pos3.clone(),
        node: s, color: tint, t: -index * 0.13, duration: 0.72, arrived: false });
    }
  }
  pulseFamily(family, labelFilter = '') {
    const needle = String(labelFilter).toLowerCase();
    const matches = this.simNodes.filter((s) => s.node.family === String(family).toUpperCase() &&
      (!needle || [s.node.label, s.node.meta?.tool_id, s.node.meta?.connector_id].some((v) => String(v || '').toLowerCase().includes(needle)))).slice(0, 10);
    this.pulse(matches.map((s) => s.node.id));
    return matches.length;
  }
  _pulseTick(dt) {
    this._activePulses = this._activePulses.filter((p) => {
      p.t += dt;
      if (p.t < 0) return true;
      if (!p.arrived) {
        const progress = Math.min(1, p.t / p.duration);
        const eased = 1 - Math.pow(1 - progress, 3);
        p.traveler.position.lerpVectors(p.origin, p.target, eased);
        p.traveler.scale.setScalar(1 + Math.sin(progress * Math.PI) * 1.6);
        p.trail.geometry.setFromPoints([p.origin, p.traveler.position]);
        p.trail.material.opacity = 0.72 * (1 - progress * 0.4);
        if (progress < 1) return true;
        this.effectGroup.remove(p.traveler, p.trail);
        p.traveler.geometry.dispose(); p.traveler.material.dispose();
        p.trail.geometry.dispose(); p.trail.material.dispose();
        p.mesh = new THREE.Mesh(new THREE.RingGeometry(0.1, 0.125, 40), new THREE.MeshBasicMaterial({
          color: p.color, transparent: true, opacity: 0.95, side: THREE.DoubleSide, depthWrite: false,
          blending: THREE.AdditiveBlending,
        }));
        p.mesh.position.copy(p.target);
        this.effectGroup.add(p.mesh);
        p.arrived = true;
        p.t = 0;
        return true;
      }
      p.mesh.quaternion.copy(this.camera.quaternion);
      p.mesh.scale.setScalar(p.node.size * (1.15 + p.t * 1.6));
      p.mesh.material.opacity = Math.max(0, 0.95 * (1 - p.t / 1.55));
      if (p.t < 1.55) return true;
      this._disposePulse(p);
      return false;
    });
  }
  _disposePulse(p) {
    if (p.traveler) { this.effectGroup.remove(p.traveler); p.traveler.geometry.dispose(); p.traveler.material.dispose(); }
    if (p.trail) { this.effectGroup.remove(p.trail); p.trail.geometry.dispose(); p.trail.material.dispose(); }
    if (p.mesh) { this.effectGroup.remove(p.mesh); p.mesh.geometry.dispose(); p.mesh.material.dispose(); }
  }
  _makeHaloTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    gradient.addColorStop(0, 'rgba(255,255,255,0.7)');
    gradient.addColorStop(0.22, 'rgba(255,255,255,0.22)');
    gradient.addColorStop(0.6, 'rgba(255,255,255,0.04)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(canvas);
  }
  discover(clearSelection = true) {
    if (clearSelection) this.deselect();
    this.controls.autoRotate = false;
    this.controls.target.set(0, 0, 0);
    const halfHeight = Math.max(3.15, 4.65 / this.camera.aspect);
    this.camera.position.set(0, 0, halfHeight / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    this.camera.lookAt(0, 0, 0);
    this.controls.update();
    this._hasFramed = !!this.simNodes.length;
  }
  setQuality(q) {
    this.quality = q;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, q === 'ultra' ? 2 : q === 'reduced' ? 1 : 1.5));
    if (this.halo) this.halo.visible = q !== 'reduced';
  }
  _clearGroup(group) {
    while (group.children.length) {
      const object = group.children[0];
      group.remove(object);
      object.geometry?.dispose();
      object.material?.dispose();
    }
  }
  destroy() {
    this._destroyed = true;
    cancelAnimationFrame(this._frame);
    Object.entries(this._listeners).forEach(([event, fn]) => this.canvas.removeEventListener(event, fn));
    this._resizeObserver.disconnect();
    this.controls.dispose();
    this._clearGroup(this.nodeGroup);
    this._clearGroup(this.effectGroup);
    this.line?.geometry.dispose();
    this.line?.material.dispose();
    this._haloTexture.dispose();
    this._labelsHost.remove();
    this.renderer.dispose();
  }
}
