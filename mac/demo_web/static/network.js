/* Graphe de nœuds d'IA en arrière-plan (canvas 2D). */
(function () {
  const canvas = document.getElementById('network');
  const ctx = canvas.getContext('2d');
  const LABELS = ['Chief of staff', 'Ops', 'Engineering', 'Research', 'Finance',
                  'Security', 'Comms', 'Scheduling', 'Memory', 'Vision'];
  let W = 0, H = 0, nodes = [], core = { x: 0, y: 0 }, t = 0;

  function build() {
    W = canvas.width = innerWidth * devicePixelRatio;
    H = canvas.height = innerHeight * devicePixelRatio;
    canvas.style.width = innerWidth + 'px';
    canvas.style.height = innerHeight + 'px';
    core = { x: W / 2, y: H / 2 };
    const R = Math.min(W, H) * 0.40;
    nodes = LABELS.map((label, i) => {
      const a = (i / LABELS.length) * Math.PI * 2 - Math.PI / 2;
      return {
        label, a, r: R * (0.78 + (i % 3) * 0.12),
        x: 0, y: 0, phase: Math.random() * Math.PI * 2,
        active: 0, accent: i % 4 === 0,
      };
    });
  }
  build();
  addEventListener('resize', build);

  function draw() {
    requestAnimationFrame(draw);
    t += 0.008;
    ctx.clearRect(0, 0, W, H);
    const dpr = devicePixelRatio;

    nodes.forEach((n) => {
      n.active *= 0.97;
      const wob = Math.sin(t * 1.4 + n.phase) * 8 * dpr;
      n.x = core.x + Math.cos(n.a + t * 0.05) * (n.r + wob);
      n.y = core.y + Math.sin(n.a + t * 0.05) * (n.r + wob) * 0.72;
    });

    // liens vers le noyau
    nodes.forEach((n) => {
      const glow = 0.10 + n.active * 0.55;
      const g = ctx.createLinearGradient(core.x, core.y, n.x, n.y);
      g.addColorStop(0, `rgba(63,230,255,${glow + 0.12})`);
      g.addColorStop(1, n.accent ? `rgba(255,138,61,${glow})` : `rgba(63,230,255,${glow})`);
      ctx.strokeStyle = g;
      ctx.lineWidth = (1 + n.active * 2) * dpr;
      ctx.beginPath();
      ctx.moveTo(core.x, core.y);
      ctx.quadraticCurveTo((core.x + n.x) / 2, (core.y + n.y) / 2 - 40 * dpr, n.x, n.y);
      ctx.stroke();

      // paquet de données circulant sur le lien
      const p = ((t * 0.35 + n.phase) % 1);
      const px = core.x + (n.x - core.x) * p;
      const py = core.y + (n.y - core.y) * p - Math.sin(Math.PI * p) * 40 * dpr;
      ctx.fillStyle = n.accent ? '#ff8a3d' : '#3fe6ff';
      ctx.beginPath(); ctx.arc(px, py, 2.2 * dpr, 0, Math.PI * 2); ctx.fill();

      // nœud
      const rad = (5 + n.active * 6) * dpr;
      ctx.shadowBlur = 18 * dpr;
      ctx.shadowColor = n.accent ? '#ff8a3d' : '#3fe6ff';
      ctx.fillStyle = n.accent ? '#ffb27d' : '#8ff2ff';
      ctx.beginPath(); ctx.arc(n.x, n.y, rad, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;

      ctx.fillStyle = `rgba(190,238,252,${0.45 + n.active * 0.5})`;
      ctx.font = `${11 * dpr}px "Segoe UI",sans-serif`;
      ctx.textAlign = n.x < core.x ? 'right' : 'left';
      ctx.fillText(n.label, n.x + (n.x < core.x ? -12 : 12) * dpr, n.y + 4 * dpr);
    });

    // noyau central
    const pulse = 1 + Math.sin(t * 2.2) * 0.12;
    const cg = ctx.createRadialGradient(core.x, core.y, 0, core.x, core.y, 90 * dpr * pulse);
    cg.addColorStop(0, 'rgba(63,230,255,.55)');
    cg.addColorStop(0.5, 'rgba(63,230,255,.10)');
    cg.addColorStop(1, 'rgba(63,230,255,0)');
    ctx.fillStyle = cg;
    ctx.beginPath(); ctx.arc(core.x, core.y, 90 * dpr * pulse, 0, Math.PI * 2); ctx.fill();
  }
  draw();

  window.JarvisNetwork = {
    /** réinitialise puis rallume les nœuds en cascade */
    reset() {
      nodes.forEach((n) => { n.active = 0; });
      nodes.forEach((n, i) => setTimeout(() => { n.active = 1; }, 90 * i));
    },
    /** fait vibrer le graphe au rythme de la voix */
    pulse(level) { nodes.forEach((n) => { n.active = Math.max(n.active, level); }); },
  };
})();
