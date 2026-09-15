/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — neural_graph_engine.js
   Moteur du graphe neuronal 3D, sans aucun HUD : il ne connaît que son
   conteneur et son jeu de données. Le prototype (ui/dev/neural_graph.html) et
   la vue V5 d'AI Core montent tous deux ce même moteur avec leur propre
   habillage.

   Rendu : Three.js r160 vendorisé, halos additifs (pas de post-processing),
   layout de force pré-calculé hors de la boucle d'animation.

   createNeuralGraph({ host, data, onSelect, onHover }) -> instance
   ========================================================================== */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/* --- textures procédurales : aucun fichier externe --------------------- */
let GLOW = null;
function glowTexture() {
  if (GLOW) return GLOW;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0.00, 'rgba(255,255,255,1)');
  g.addColorStop(0.18, 'rgba(255,255,255,.72)');
  g.addColorStop(0.45, 'rgba(255,255,255,.18)');
  g.addColorStop(1.00, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  GLOW = new THREE.CanvasTexture(c);
  GLOW.colorSpace = THREE.SRGBColorSpace;
  return GLOW;
}

function labelTexture(text, css) {
  const pad = 12, font = '600 34px "JetBrains Mono","Fira Code",ui-monospace,monospace';
  const m = document.createElement('canvas').getContext('2d');
  m.font = font;
  const w = Math.ceil(m.measureText(text).width) + pad * 2;
  const h = 56;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.font = font;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(0,0,0,.95)';
  ctx.shadowBlur = 10;
  ctx.fillStyle = css;
  ctx.fillText(text, w / 2, h / 2);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return { tex: t, w, h };
}

export function createNeuralGraph({ host, data, onSelect = null, onHover = null, stars = true } = {}) {
  const CLUSTERS = data.clusters;
  const GRAPH_R = 150;

  /* ====================================================================
     1. Modèle + simulation de force 3D
     ==================================================================== */
  const nodes = data.nodes.map((n) => ({
    ...n,
    pos: new THREE.Vector3(),
    vel: new THREE.Vector3(),
    neighbors: new Set(),
    flash: 0,
  }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = data.links
    .map((l) => (Array.isArray(l)
      ? { a: byId.get(l[0]), b: byId.get(l[1]), flux: l[2] || 1 }
      : { a: byId.get(l.source), b: byId.get(l.target), flux: l.flux || 1 }))
    .filter((l) => l.a && l.b);
  links.forEach((l) => { l.a.neighbors.add(l.b.id); l.b.neighbors.add(l.a.id); });

  // Ancres de cluster : spirale de Fibonacci, 1 hub par famille, équidistants.
  const satellites = Object.keys(CLUSTERS).filter((k) => k !== 'core');
  const anchors = new Map([['core', new THREE.Vector3(0, 0, 0)]]);
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  satellites.forEach((key, i) => {
    const y = 1 - ((i + 0.5) / satellites.length) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = GOLDEN * i;
    anchors.set(key, new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r).multiplyScalar(GRAPH_R));
  });

  nodes.forEach((n, i) => {
    const a = anchors.get(n.cluster) || new THREE.Vector3();
    n.pos.set(a.x + (Math.random() - .5) * 40, a.y + (Math.random() - .5) * 40, a.z + (Math.random() - .5) * 40);
    if (n.hub) n.pos.copy(a);        // noyau et hubs épinglés : charpente du graphe
    n.fixed = !!n.hub;
    n.index = i;
  });

  /** Une itération : répulsion O(n²), ressorts, ancres de cluster, gravité. */
  function tick(alpha) {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = b.pos.x - a.pos.x, dy = b.pos.y - a.pos.y, dz = b.pos.z - a.pos.z;
        const d2 = Math.max(36, dx * dx + dy * dy + dz * dz);
        const f = (24000 / d2) * alpha;
        const d = Math.sqrt(d2);
        const ux = dx / d, uy = dy / d, uz = dz / d;
        a.vel.x -= ux * f; a.vel.y -= uy * f; a.vel.z -= uz * f;
        b.vel.x += ux * f; b.vel.y += uy * f; b.vel.z += uz * f;
      }
    }
    for (const l of links) {
      const rest = 52 - l.flux * 5;
      const dx = l.b.pos.x - l.a.pos.x, dy = l.b.pos.y - l.a.pos.y, dz = l.b.pos.z - l.a.pos.z;
      const d = Math.max(1, Math.hypot(dx, dy, dz));
      const f = ((d - rest) * 0.035) * alpha;
      const ux = dx / d, uy = dy / d, uz = dz / d;
      l.a.vel.x += ux * f; l.a.vel.y += uy * f; l.a.vel.z += uz * f;
      l.b.vel.x -= ux * f; l.b.vel.y -= uy * f; l.b.vel.z -= uz * f;
    }
    for (const n of nodes) {
      const anchor = anchors.get(n.cluster);
      if (anchor) {
        n.vel.x += (anchor.x - n.pos.x) * 0.05 * alpha;
        n.vel.y += (anchor.y - n.pos.y) * 0.05 * alpha;
        n.vel.z += (anchor.z - n.pos.z) * 0.05 * alpha;
      }
      n.vel.addScaledVector(n.pos, -0.012 * alpha);   // gravité : compense la répulsion
      n.vel.multiplyScalar(0.72);
      if (!n.fixed) n.pos.add(n.vel);
    }
  }
  for (let i = 0, alpha = 1; i < 420; i++, alpha *= 0.992) tick(alpha);

  // Rayon ramené au 90e centile (et non au max, qu'un seul nœud excentré
  // suffirait à dicter) : cadrage caméra, brouillard et fondu des labels
  // restent valables quel que soit le jeu de données.
  const radii = nodes.map((n) => n.pos.length()).sort((a, b) => a - b);
  const p90 = Math.max(1, radii[Math.floor(radii.length * 0.9)]);
  nodes.forEach((n) => n.pos.multiplyScalar(GRAPH_R / p90));

  /* ====================================================================
     2. Scène
     ==================================================================== */
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x050811, 0.0015);

  const camera = new THREE.PerspectiveCamera(52, 1, 0.5, 2200);
  const HOME_DIST = GRAPH_R * 2.6;
  camera.position.set(0, GRAPH_R * 0.42, HOME_DIST);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.style.cssText = 'display:block;width:100%;height:100%';
  host.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.07;
  controls.rotateSpeed = 0.5;
  controls.minDistance = 40;
  controls.maxDistance = GRAPH_R * 5;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.42;

  scene.add(new THREE.AmbientLight(0xffffff, 1));

  const TEX = glowTexture();
  const disposables = [];

  /* --- champ de particules de fond ------------------------------------ */
  if (stars) {
    const N = 900;
    const sp = new Float32Array(N * 3), sc = new Float32Array(N * 3);
    const tint = new THREE.Color();
    for (let i = 0; i < N; i++) {
      const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
      const r = 420 + Math.random() * 520, s = Math.sqrt(1 - u * u);
      sp.set([Math.cos(th) * s * r, u * r * 0.7, Math.sin(th) * s * r], i * 3);
      tint.setHSL(0.5 + Math.random() * 0.12, 0.8, 0.4 + Math.random() * 0.35);
      sc.set([tint.r, tint.g, tint.b], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(sp, 3));
    g.setAttribute('color', new THREE.BufferAttribute(sc, 3));
    // fog:false — à cette distance le brouillard les effacerait entièrement.
    const m = new THREE.PointsMaterial({
      size: 3.2, map: TEX, vertexColors: true, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true, fog: false,
    });
    scene.add(new THREE.Points(g, m));
    disposables.push(g, m);
  }

  /* --- sphères, halos, labels ----------------------------------------- */
  const graph = new THREE.Group();
  scene.add(graph);
  const sphereGeo = new THREE.SphereGeometry(1, 20, 16);
  disposables.push(sphereGeo);
  const pickables = [];

  for (const n of nodes) {
    const color = new THREE.Color(CLUSTERS[n.cluster].color);
    const radius = (3.1 + (n.val || 1) * 2.2) * (n.hub ? 1.25 : 1);

    const core = new THREE.Mesh(sphereGeo, new THREE.MeshBasicMaterial({
      color: color.clone().lerp(new THREE.Color(0xffffff), n.hub ? 0.6 : 0.42),
      transparent: true, opacity: 0.96,
    }));
    core.scale.setScalar(radius);
    core.position.copy(n.pos);
    core.userData.node = n;

    // Halo additif : la lueur néon sans passe de post-processing.
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: TEX, color, transparent: true, opacity: n.hub ? 1 : 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    halo.scale.setScalar(radius * (n.hub ? 9 : 7.5));
    halo.position.copy(n.pos);

    let aura = null;
    if (n.hub) {                                   // second halo : lisible de loin
      aura = new THREE.Sprite(new THREE.SpriteMaterial({
        map: TEX, color: color.clone().lerp(new THREE.Color(0xffffff), 0.3),
        transparent: true, opacity: 0.3,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      aura.scale.setScalar(radius * 17);
      aura.position.copy(n.pos);
      graph.add(aura);
      disposables.push(aura.material);
    }

    const { tex, w, h } = labelTexture(n.label, n.hub ? '#ffffff' : '#dff3ff');
    const label = new THREE.Sprite(new THREE.SpriteMaterial({
      map: tex, transparent: true, opacity: 0.9, depthWrite: false, depthTest: false,
    }));
    const lh = n.hub ? 10.5 : 7.5;
    label.scale.set((w / h) * lh, lh, 1);
    label.position.copy(n.pos).y += radius + 7;
    label.renderOrder = 3;

    graph.add(halo, core, label);
    disposables.push(core.material, halo.material, label.material, tex);
    n.mesh = core; n.halo = halo; n.aura = aura; n.labelSprite = label; n.radius = radius;
    pickables.push(core);
  }

  /* --- liens ----------------------------------------------------------- */
  const linkPos = new Float32Array(links.length * 6);
  const linkCol = new Float32Array(links.length * 6);
  links.forEach((l, i) => {
    linkPos.set([l.a.pos.x, l.a.pos.y, l.a.pos.z, l.b.pos.x, l.b.pos.y, l.b.pos.z], i * 6);
    const ca = new THREE.Color(CLUSTERS[l.a.cluster].color);
    const cb = new THREE.Color(CLUSTERS[l.b.cluster].color);
    linkCol.set([ca.r, ca.g, ca.b, cb.r, cb.g, cb.b], i * 6);
  });
  const linkGeo = new THREE.BufferGeometry();
  linkGeo.setAttribute('position', new THREE.BufferAttribute(linkPos, 3));
  linkGeo.setAttribute('color', new THREE.BufferAttribute(linkCol, 3));
  const linkMat = new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.2,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  graph.add(new THREE.LineSegments(linkGeo, linkMat));
  disposables.push(linkGeo, linkMat);

  /** Répercute n.pos sur les objets 3D et le buffer des liens (après un drag). */
  function syncPositions() {
    for (const n of nodes) {
      n.mesh.position.copy(n.pos);
      n.halo.position.copy(n.pos);
      if (n.aura) n.aura.position.copy(n.pos);
      n.labelSprite.position.copy(n.pos).y += n.radius + 7;
    }
    for (let i = 0; i < links.length; i++) {
      const l = links[i];
      linkPos.set([l.a.pos.x, l.a.pos.y, l.a.pos.z, l.b.pos.x, l.b.pos.y, l.b.pos.z], i * 6);
    }
    linkGeo.attributes.position.needsUpdate = true;
  }

  /* --- particules directionnelles (flux de données) -------------------- */
  const particles = [];
  links.forEach((l) => {
    const count = 1 + l.flux;                      // densité pilotée par le flux
    for (let k = 0; k < count; k++) {
      particles.push({ link: l, t: k / count, speed: 0.0016 + 0.0011 * l.flux + Math.random() * 0.0006 });
    }
  });
  const pPos = new Float32Array(particles.length * 3);
  const pCol = new Float32Array(particles.length * 3);
  const pGeo = new THREE.BufferGeometry();
  pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
  pGeo.setAttribute('color', new THREE.BufferAttribute(pCol, 3));
  const pMat = new THREE.PointsMaterial({
    size: 3.4, map: TEX, vertexColors: true, transparent: true, opacity: 0.95,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  });
  const points = new THREE.Points(pGeo, pMat);
  points.frustumCulled = false;
  graph.add(points);
  disposables.push(pGeo, pMat);

  const tmpA = new THREE.Color(), tmpB = new THREE.Color(), tmpC = new THREE.Color();
  function updateParticles(dt) {
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.t = (p.t + p.speed * dt) % 1;
      const { a, b } = p.link;
      pPos[i * 3 + 0] = a.pos.x + (b.pos.x - a.pos.x) * p.t;
      pPos[i * 3 + 1] = a.pos.y + (b.pos.y - a.pos.y) * p.t;
      pPos[i * 3 + 2] = a.pos.z + (b.pos.z - a.pos.z) * p.t;
      tmpA.set(CLUSTERS[a.cluster].color);
      tmpB.set(CLUSTERS[b.cluster].color);
      tmpC.copy(tmpA).lerp(tmpB, p.t).multiplyScalar(p.link.dim ? 0.12 : 1);
      pCol[i * 3 + 0] = tmpC.r; pCol[i * 3 + 1] = tmpC.g; pCol[i * 3 + 2] = tmpC.b;
    }
    pGeo.attributes.position.needsUpdate = true;
    pGeo.attributes.color.needsUpdate = true;
  }

  /* ====================================================================
     3. Interactions
     ==================================================================== */
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let hovered = null, selected = null, query = '', pointerInside = false, labelsOn = true;
  const hiddenClusters = new Set();

  function matches(n) {
    if (hiddenClusters.has(n.cluster)) return false;
    if (!query) return true;
    return (n.label + ' ' + n.id + ' ' + CLUSTERS[n.cluster].name).toLowerCase().includes(query);
  }

  function applyEmphasis() {
    const focusId = selected?.id || hovered?.id || null;
    for (const n of nodes) {
      const shown = matches(n);
      const related = !focusId || n.id === focusId || n.neighbors.has(focusId);
      const dim = !shown || !related;
      const k = dim ? (shown ? 0.16 : 0.05) : 1;
      n.mesh.material.opacity = 0.96 * k;
      n.halo.material.opacity = (n.hub ? 1 : 0.85) * k * (n.id === focusId ? 1.5 : 1);
      if (n.aura) n.aura.material.opacity = 0.3 * k;
      n.labelBase = labelsOn ? (dim ? 0.1 : 0.92) : 0;
      n.dim = dim;
    }
    const anyFocus = !!focusId;
    for (const l of links) {
      const shown = matches(l.a) && matches(l.b);
      l.dim = !shown || !(!focusId || l.a.id === focusId || l.b.id === focusId);
    }
    linkMat.opacity = anyFocus ? 0.1 : 0.2;
    for (let i = 0; i < links.length; i++) {
      const l = links[i], f = l.dim ? 0.08 : (anyFocus ? 1.6 : 1);
      tmpA.set(CLUSTERS[l.a.cluster].color).multiplyScalar(f);
      tmpB.set(CLUSTERS[l.b.cluster].color).multiplyScalar(f);
      linkCol.set([tmpA.r, tmpA.g, tmpA.b, tmpB.r, tmpB.g, tmpB.b], i * 6);
    }
    linkGeo.attributes.color.needsUpdate = true;
  }

  /* --- vol de caméra --------------------------------------------------- */
  let fly = null;
  function flyTo(target, distance, ms = 900) {
    const dir = camera.position.clone().sub(controls.target).normalize();
    fly = {
      t0: performance.now(), ms,
      fromCam: camera.position.clone(), fromTgt: controls.target.clone(),
      toCam: target.clone().add(dir.multiplyScalar(distance)), toTgt: target.clone(),
    };
  }
  function stepFly(now) {
    if (!fly) return;
    const k = clamp((now - fly.t0) / fly.ms, 0, 1), e = easeInOut(k);
    camera.position.lerpVectors(fly.fromCam, fly.toCam, e);
    controls.target.lerpVectors(fly.fromTgt, fly.toTgt, e);
    if (k >= 1) fly = null;
  }

  function select(node) {
    selected = node || null;
    controls.autoRotate = !selected;
    if (selected) flyTo(selected.pos, 140 + selected.radius * 7);
    else flyTo(new THREE.Vector3(0, 0, 0), HOME_DIST);
    applyEmphasis();
    if (onSelect) onSelect(selected);
  }

  /** Le pointeur peut n'avoir jamais bougé avant un clic (tablette, clic direct). */
  function setPointer(e) {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  }
  function nodeUnderPointer() {
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    const node = hit ? hit.object.userData.node : null;
    return node && matches(node) ? node : null;
  }

  let lastPick = 0;
  function pick() {
    const now = performance.now();
    if (!pointerInside || now - lastPick < 60) return;   // throttle : pas un raycast par frame
    lastPick = now;
    const next = nodeUnderPointer();
    if (next !== hovered) {
      hovered = next;
      renderer.domElement.style.cursor = next ? 'pointer' : 'default';
      if (!selected) applyEmphasis();
      if (onHover) onHover(hovered);
    }
  }

  /* --- glisser-déposer : le nœud suit le curseur dans le plan caméra --- */
  const dragPlane = new THREE.Plane();
  const dragPoint = new THREE.Vector3();
  const dragOffset = new THREE.Vector3();
  const camDir = new THREE.Vector3();
  let dragged = null, downXY = null, movedWhileDown = false;

  const onPointerMove = (e) => {
    setPointer(e);
    pointerInside = true;
    if (downXY && Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]) > 4) movedWhileDown = true;
    if (!dragged) return;
    raycaster.setFromCamera(pointer, camera);
    if (raycaster.ray.intersectPlane(dragPlane, dragPoint)) {
      dragged.pos.copy(dragPoint).add(dragOffset);
      syncPositions();
    }
  };
  const onPointerDown = (e) => {
    setPointer(e);
    downXY = [e.clientX, e.clientY];
    movedWhileDown = false;
    const node = nodeUnderPointer();
    if (!node) return;                          // sinon OrbitControls orbite normalement
    dragged = node;
    controls.enabled = false;
    camera.getWorldDirection(camDir);
    dragPlane.setFromNormalAndCoplanarPoint(camDir, node.pos);
    raycaster.setFromCamera(pointer, camera);
    if (raycaster.ray.intersectPlane(dragPlane, dragPoint)) dragOffset.copy(node.pos).sub(dragPoint);
    try { renderer.domElement.setPointerCapture(e.pointerId); } catch { /* non capturable */ }
    renderer.domElement.style.cursor = 'grabbing';
  };
  const onPointerUp = (e) => {
    if (dragged) {
      try { renderer.domElement.releasePointerCapture(e.pointerId); } catch { /* déjà relâché */ }
      dragged = null;
      controls.enabled = true;
      renderer.domElement.style.cursor = hovered ? 'pointer' : 'default';
    }
    downXY = null;
  };
  const onLeave = () => { pointerInside = false; if (hovered) { hovered = null; applyEmphasis(); if (onHover) onHover(null); } };
  const onClick = () => {
    if (movedWhileDown) return;                 // un déplacement n'est pas un clic
    const node = nodeUnderPointer();
    if (node) select(node === selected ? null : node);
    else if (selected) select(null);
  };

  const el = renderer.domElement;
  el.addEventListener('pointermove', onPointerMove);
  el.addEventListener('pointerdown', onPointerDown);
  el.addEventListener('pointerup', onPointerUp);
  el.addEventListener('pointercancel', onPointerUp);
  el.addEventListener('pointerleave', onLeave);
  el.addEventListener('click', onClick);

  /* ====================================================================
     4. Boucle de rendu
     ==================================================================== */
  // Le conteneur peut changer : la page hôte est susceptible de réécrire son
  // DOM et d'emporter l'enveloppe. On se réfère donc toujours au parent réel
  // du canvas, jamais au `host` capturé au montage.
  function container() { return renderer.domElement.parentElement || host; }
  function resize() {
    const el = container();
    const w = el?.clientWidth || 0, h = el?.clientHeight || 0;
    if (!w || !h) return;        // pas de mise en page : ne pas figer un buffer 1×1
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  /** Réinstalle le canvas dans un nouveau conteneur et y réobserve la taille. */
  function attachTo(el) {
    if (!el) return;
    if (renderer.domElement.parentElement !== el) el.appendChild(renderer.domElement);
    ro.disconnect();
    ro.observe(el);
    resize();
  }

  let raf = 0, last = performance.now(), fpsAcc = 0, fpsN = 0, fps = 0, running = true;
  function loop(now) {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(64, now - last);
    last = now;

    stepFly(now);
    pick();
    updateParticles(dt);

    const focus = selected || hovered;
    for (const n of nodes) {
      n.flash *= 0.94;
      const puls = 1 + 0.05 * Math.sin(now * 0.0016 + n.index) + n.flash;
      const s = n.radius * (n.hub ? 9 : 7.5) * puls;
      n.halo.scale.setScalar(n === focus ? s * 1.3 : s);
      if (n.aura) n.aura.scale.setScalar(n.radius * 17 * puls);
      // Labels des feuilles : visibles seulement à portée ; les hubs, toujours.
      const near = n.hub || n === focus || (focus && n.neighbors.has(focus.id))
        ? 1
        : clamp((GRAPH_R * 2.3 - camera.position.distanceTo(n.pos)) / 70, 0, 1);
      n.labelSprite.material.opacity = n.labelBase * near;
    }

    controls.autoRotate = !selected && !hovered && !fly && !dragged;
    controls.update();
    renderer.render(scene, camera);

    fpsAcc += 1000 / Math.max(1, dt); fpsN++;
    if (fpsN >= 30) { fps = Math.round(fpsAcc / fpsN); fpsAcc = 0; fpsN = 0; }
  }
  applyEmphasis();
  raf = requestAnimationFrame(loop);

  /* ====================================================================
     5. API publique
     ==================================================================== */
  return {
    nodes, links, byId, clusters: CLUSTERS,
    canvas: renderer.domElement,
    get selected() { return selected; },
    get hovered() { return hovered; },
    get fps() { return fps; },
    select(id) { select(typeof id === 'string' ? byId.get(id) : id); },
    home() { select(null); },
    /** Éclat bref sur un nœud : appelé sur événement réel, jamais en décoratif. */
    pulse(id) { const n = byId.get(id); if (n) n.flash = 0.9; return !!n; },
    setQuery(q) { query = String(q || '').trim().toLowerCase(); applyEmphasis(); },
    setClusterVisible(key, visible) {
      if (visible) hiddenClusters.delete(key); else hiddenClusters.add(key);
      if (selected && hiddenClusters.has(selected.cluster)) select(null);
      else applyEmphasis();
    },
    isClusterVisible(key) { return !hiddenClusters.has(key); },
    toggleLabels() { labelsOn = !labelsOn; applyEmphasis(); return labelsOn; },
    resize, attachTo,
    pause() { running = false; cancelAnimationFrame(raf); },
    resume() { if (running) return; running = true; last = performance.now(); raf = requestAnimationFrame(loop); },
    dispose() {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      el.removeEventListener('pointermove', onPointerMove);
      el.removeEventListener('pointerdown', onPointerDown);
      el.removeEventListener('pointerup', onPointerUp);
      el.removeEventListener('pointercancel', onPointerUp);
      el.removeEventListener('pointerleave', onLeave);
      el.removeEventListener('click', onClick);
      disposables.forEach((d) => { try { d.dispose(); } catch { /* déjà libéré */ } });
      renderer.dispose();
      el.remove();
    },
  };
}
