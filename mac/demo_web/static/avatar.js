/* Avatar holographique : buste + tête en nuage de particules (Three.js r128). */
(function () {
  const container = document.getElementById('avatar');
  const CYAN = new THREE.Color(0x3fe6ff);
  const ORANGE = new THREE.Color(0xff8a3d);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.position.set(0, 0.15, 6.2);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  // ---- géométrie du buste : échantillonnage d'une silhouette de révolution ----
  // profil (y, rayon) de la tête aux épaules
  const PROFILE = [
    [1.95, 0.08], [1.85, 0.34], [1.62, 0.52], [1.35, 0.55], [1.12, 0.46],
    [0.98, 0.22], [0.90, 0.18], [0.80, 0.30], [0.62, 0.62], [0.40, 0.92],
    [0.10, 1.10], [-0.30, 1.16], [-0.75, 1.10], [-1.20, 0.98],
  ];
  function radiusAt(y) {
    for (let i = 0; i < PROFILE.length - 1; i++) {
      const [y0, r0] = PROFILE[i], [y1, r1] = PROFILE[i + 1];
      if (y <= y0 && y >= y1) {
        const t = (y0 - y) / (y0 - y1);
        return r0 + (r1 - r0) * t;
      }
    }
    return 0;
  }

  const COUNT = 14000;
  const positions = new Float32Array(COUNT * 3);
  const colors = new Float32Array(COUNT * 3);
  const base = new Float32Array(COUNT * 3);
  const seeds = new Float32Array(COUNT);
  const yTop = PROFILE[0][0], yBottom = PROFILE[PROFILE.length - 1][0];

  for (let i = 0; i < COUNT; i++) {
    const y = yTop - Math.random() * (yTop - yBottom);
    const r = radiusAt(y) * (0.86 + Math.random() * 0.14); // coque fine
    const a = Math.random() * Math.PI * 2;
    // aplatissement avant/arrière : silhouette plus humaine
    const x = Math.cos(a) * r;
    const z = Math.sin(a) * r * 0.62;
    const k = i * 3;
    base[k] = positions[k] = x;
    base[k + 1] = positions[k + 1] = y;
    base[k + 2] = positions[k + 2] = z;
    seeds[i] = Math.random() * Math.PI * 2;

    // teinte : cyan dominant, accents orange sur les arêtes du visage
    const accent = y > 1.1 && Math.abs(x) < 0.22 && z > 0.18 ? 0.85 : Math.random() < 0.05 ? 0.6 : 0;
    const c = CYAN.clone().lerp(ORANGE, accent);
    colors[k] = c.r; colors[k + 1] = c.g; colors[k + 2] = c.b;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  const points = new THREE.Points(geom, new THREE.PointsMaterial({
    size: 0.022, vertexColors: true, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  }));
  const group = new THREE.Group();
  group.add(points);

  // anneaux lumineux autour du buste
  const rings = [];
  for (let i = 0; i < 3; i++) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(1.25 + i * 0.2, 0.004, 8, 160),
      new THREE.MeshBasicMaterial({
        color: i === 1 ? ORANGE : CYAN, transparent: true, opacity: 0.32,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -1.1 - i * 0.05;
    ring.scale.z = 0.62;
    rings.push(ring);
    group.add(ring);
  }
  scene.add(group);

  // ---- état audio ----
  let level = 0, target = 0, pulse = 0;
  const clock = new THREE.Clock();

  function resize() {
    const w = container.clientWidth || innerWidth;
    const h = container.clientHeight || innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize);
  resize();

  function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    level += (target - level) * 0.18;
    pulse *= 0.94;

    const pos = geom.attributes.position.array;
    const amp = 0.035 + level * 0.28 + pulse * 0.4;
    for (let i = 0; i < COUNT; i++) {
      const k = i * 3;
      const bx = base[k], by = base[k + 1], bz = base[k + 2];
      // onde verticale qui remonte le buste + respiration radiale
      const wave = Math.sin(by * 3.4 - t * 2.6 + seeds[i]) * amp;
      const breath = 1 + wave * 0.5 + Math.sin(t * 1.1 + seeds[i]) * 0.006;
      pos[k] = bx * breath;
      pos[k + 1] = by + wave * 0.25;
      pos[k + 2] = bz * breath;
    }
    geom.attributes.position.needsUpdate = true;

    points.material.size = 0.020 + level * 0.018 + pulse * 0.02;
    points.material.opacity = 0.78 + level * 0.22;

    rings.forEach((r, i) => {
      r.rotation.z += 0.0022 * (i + 1);
      r.material.opacity = 0.18 + level * 0.5;
      const s = 1 + level * 0.09 + Math.sin(t * 0.8 + i) * 0.01;
      r.scale.set(s, s, 0.62 * s);
    });

    group.rotation.y = Math.sin(t * 0.22) * 0.22;
    group.position.y = Math.sin(t * 0.6) * 0.02;
    renderer.render(scene, camera);
  }
  animate();

  window.JarvisAvatar = {
    /** niveau audio 0..1 envoyé par l'analyseur WebAudio */
    setLevel(v) { target = Math.max(0, Math.min(1, v)); },
    /** impulsion ponctuelle (démarrage de parole, diagnostic) */
    burst(v = 1) { pulse = v; },
    reset() { target = 0; },
  };
})();
