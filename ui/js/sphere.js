/* Sphère centrale animée — rendu canvas léger (aucune bibliothèque). */
(function () {
  const canvas = document.getElementById('sphere');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let w = 0;
  let h = 0;
  let t = 0;
  let dpr = Math.min(window.devicePixelRatio || 1, 2);
  let intensity = 0.25;        // 0 = repos, 1 = pleine activité
  let target = 0.25;

  const POINTS = [];
  const RINGS = 13;
  for (let i = 0; i < RINGS; i++) {
    const lat = -Math.PI / 2 + (Math.PI * (i + 1)) / (RINGS + 1);
    const count = Math.max(10, Math.round(Math.cos(lat) * 30));
    for (let k = 0; k < count; k++) {
      POINTS.push({ lat, lon: (2 * Math.PI * k) / count });
    }
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    w = Math.max(120, rect.width);
    h = Math.max(120, rect.height);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();

  function frame() {
    intensity += (target - intensity) * 0.04;
    t += 0.0045 + intensity * 0.008;
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w * 0.62, h) * 0.38;

    // halo
    const glow = ctx.createRadialGradient(cx, cy, radius * 0.15, cx, cy, radius * 2.1);
    glow.addColorStop(0, `rgba(34,211,238,${0.14 + intensity * 0.16})`);
    glow.addColorStop(0.45, 'rgba(23,90,140,.07)');
    glow.addColorStop(1, 'rgba(3,8,20,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    // anneaux orbitaux
    for (let i = 0; i < 3; i++) {
      const rr = radius * (1.35 + i * 0.26);
      const tilt = 0.34 + i * 0.1;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(Math.sin(t * (0.3 + i * 0.12)) * 0.22 + i * 0.5);
      ctx.scale(1, tilt);
      ctx.beginPath();
      ctx.arc(0, 0, rr, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(34,211,238,${0.1 + i * 0.03})`;
      ctx.lineWidth = 1;
      ctx.stroke();
      // satellite
      const a = t * (0.9 + i * 0.35) + i * 2;
      ctx.beginPath();
      ctx.arc(Math.cos(a) * rr, Math.sin(a) * rr, 1.9, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(103,232,249,${0.55 + intensity * 0.4})`;
      ctx.fill();
      ctx.restore();
    }

    // grille sphérique
    const projected = [];
    for (const p of POINTS) {
      const lon = p.lon + t * 0.55;
      const x = Math.cos(p.lat) * Math.sin(lon);
      const y = Math.sin(p.lat);
      const z = Math.cos(p.lat) * Math.cos(lon);
      const scale = 0.72 + (z + 1) * 0.19;
      projected.push({
        x: cx + x * radius * scale,
        y: cy + y * radius * scale * 0.98,
        z,
        alpha: (z + 1) / 2,
      });
    }
    // liaisons
    ctx.lineWidth = 0.6;
    for (let i = 0; i < projected.length; i += 3) {
      const a = projected[i];
      const b = projected[(i + 7) % projected.length];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (dist < radius * 0.55 && a.z > -0.2 && b.z > -0.2) {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(34,211,238,${0.035 + a.alpha * 0.07 * (0.6 + intensity)})`;
        ctx.stroke();
      }
    }
    for (const p of projected) {
      const size = 0.8 + p.alpha * 1.7;
      ctx.beginPath();
      ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${140 + p.alpha * 80},${228 + p.alpha * 20},255,${0.2 + p.alpha * (0.62 + intensity * 0.3)})`;
      ctx.fill();
    }

    // cœur
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 0.62);
    core.addColorStop(0, `rgba(190,250,255,${0.5 + intensity * 0.3})`);
    core.addColorStop(0.35, `rgba(34,211,238,${0.22 + intensity * 0.2})`);
    core.addColorStop(1, 'rgba(8,30,60,0)');
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.62, 0, Math.PI * 2);
    ctx.fillStyle = core;
    ctx.fill();

    // arc de balayage
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.12, t * 1.5, t * 1.5 + 0.5 + intensity * 0.5);
    ctx.strokeStyle = `rgba(103,232,249,${0.25 + intensity * 0.4})`;
    ctx.lineWidth = 1.6;
    ctx.stroke();

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  /** Intensité pilotée par l'état réel (voix + agents actifs). */
  window.setSphereIntensity = (value) => { target = Math.max(0.12, Math.min(1, value)); };
})();
