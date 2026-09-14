/* ==========================================================================
   JARVIS_CINEMATIC_UI_V4 — jarvis_core.js
   AI CORE : noyau holographique rendu en canvas 2D (DPR-aware).

   - anneaux concentriques à rotations indépendantes
   - segments HUD qui s'activent selon l'état
   - particules orbitales / convergentes / flux de données
   - halo, profondeur simulée, respiration

   L'état vient TOUJOURS du réel (App.setRobot est la source unique côté app).
   Aucun état n'est simulé : si rien ne bouge côté backend, le core respire.
   ========================================================================== */
(function () {
  'use strict';

  const TAU = Math.PI * 2;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  /* Palette V4 — cohérente avec les tokens CSS. */
  const PALETTE = {
    cyan: [0, 217, 255],
    ice: [41, 236, 255],
    blue: [20, 140, 255],
    success: [54, 245, 162],
    warning: [255, 184, 77],
    error: [255, 83, 100],
    violet: [167, 139, 250],
  };

  /* Profils d'état : chaque état réel de JARVIS a une signature visuelle. */
  const PROFILES = {
    IDLE: { color: 'cyan', spin: 0.10, breath: 0.055, breathHz: 0.28, segments: 0.18, particles: 0.22, converge: 0, stream: 0, glow: 0.55 },
    SLEEPING: { color: 'blue', spin: 0.04, breath: 0.03, breathHz: 0.14, segments: 0.08, particles: 0.10, converge: 0, stream: 0, glow: 0.30 },
    LISTENING: { color: 'ice', spin: 0.22, breath: 0.10, breathHz: 0.95, segments: 0.45, particles: 0.55, converge: 0, stream: 0, glow: 0.85 },
    THINKING: { color: 'cyan', spin: 0.85, breath: 0.07, breathHz: 0.55, segments: 0.95, particles: 0.85, converge: 0.9, stream: 0.15, glow: 0.95 },
    RECALLING: { color: 'violet', spin: 0.55, breath: 0.07, breathHz: 0.5, segments: 0.7, particles: 0.7, converge: 0.55, stream: 0.45, glow: 0.85 },
    USING_TOOL: { color: 'blue', spin: 0.5, breath: 0.08, breathHz: 0.7, segments: 0.8, particles: 0.6, converge: 0.2, stream: 0.85, glow: 0.9 },
    CODING: { color: 'ice', spin: 0.45, breath: 0.07, breathHz: 0.7, segments: 0.75, particles: 0.5, converge: 0.15, stream: 0.9, glow: 0.85 },
    BROWSING: { color: 'blue', spin: 0.6, breath: 0.07, breathHz: 0.8, segments: 0.7, particles: 0.6, converge: 0.2, stream: 0.7, glow: 0.85 },
    DEPLOYING: { color: 'warning', spin: 0.7, breath: 0.09, breathHz: 0.9, segments: 0.9, particles: 0.7, converge: 0.3, stream: 0.8, glow: 0.95 },
    READING_FILE: { color: 'ice', spin: 0.35, breath: 0.06, breathHz: 0.6, segments: 0.6, particles: 0.45, converge: 0.2, stream: 1.0, glow: 0.85 },
    LEARNING: { color: 'violet', spin: 0.45, breath: 0.08, breathHz: 0.6, segments: 0.7, particles: 0.8, converge: 0.7, stream: 0.4, glow: 0.9 },
    VERIFYING: { color: 'ice', spin: 0.4, breath: 0.07, breathHz: 0.7, segments: 0.85, particles: 0.4, converge: 0.25, stream: 0.5, glow: 0.85 },
    SYNCING: { color: 'warning', spin: 0.95, breath: 0.10, breathHz: 1.0, segments: 1.0, particles: 0.8, converge: 0.4, stream: 1.0, glow: 1.0 },
    SPEAKING: { color: 'ice', spin: 0.3, breath: 0.14, breathHz: 1.25, segments: 0.55, particles: 0.6, converge: 0, stream: 0.2, glow: 0.95 },
    SUCCESS: { color: 'success', spin: 0.3, breath: 0.12, breathHz: 0.9, segments: 0.9, particles: 0.7, converge: 0, stream: 0.2, glow: 1.0 },
    WARNING: { color: 'warning', spin: 0.3, breath: 0.11, breathHz: 1.0, segments: 0.7, particles: 0.5, converge: 0, stream: 0.1, glow: 0.9 },
    ERROR: { color: 'error', spin: 0.18, breath: 0.16, breathHz: 1.5, segments: 0.6, particles: 0.35, converge: 0, stream: 0, glow: 1.0 },
  };

  const rgba = (name, a) => {
    const c = PALETTE[name] || PALETTE.cyan;
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  };
  const mixColor = (from, to, t) => {
    const a = PALETTE[from] || PALETTE.cyan;
    const b = PALETTE[to] || PALETTE.cyan;
    return [Math.round(lerp(a[0], b[0], t)), Math.round(lerp(a[1], b[1], t)), Math.round(lerp(a[2], b[2], t))];
  };

  class CoreInstance {
    constructor(canvas, options) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.opts = Object.assign({ detail: 1 }, options || {});
      this.state = 'IDLE';
      this.profile = PROFILES.IDLE;
      this.blend = { ...PROFILES.IDLE };
      this.colorFrom = 'cyan';
      this.colorTo = 'cyan';
      this.colorT = 1;
      this.t = 0;
      this.level = 0;          // amplitude audio réelle (TTS)
      this.ringPhase = [0, 0, 0, 0];
      this.particles = [];
      this.streams = [];
      this.w = 0; this.h = 0; this.dpr = 1;
      this._seedParticles();
    }

    _seedParticles() {
      const n = Math.round(46 * this.opts.detail);
      this.particles = Array.from({ length: n }, () => ({
        a: Math.random() * TAU,
        r: 0.35 + Math.random() * 0.62,
        sp: (0.12 + Math.random() * 0.45) * (Math.random() < 0.35 ? -1 : 1),
        s: 0.6 + Math.random() * 1.5,
        o: 0.18 + Math.random() * 0.5,
        z: Math.random(),
        conv: Math.random(),
      }));
      this.streams = Array.from({ length: Math.round(10 * this.opts.detail) }, () => ({
        a: Math.random() * TAU,
        p: Math.random(),
        sp: 0.25 + Math.random() * 0.5,
        len: 0.12 + Math.random() * 0.2,
      }));
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
      const w = Math.max(1, Math.round(rect.width));
      const h = Math.max(1, Math.round(rect.height));
      if (w === this.w && h === this.h && dpr === this.dpr) return;
      this.w = w; this.h = h; this.dpr = dpr;
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }

    setState(state) {
      const next = PROFILES[state] ? state : 'IDLE';
      if (next === this.state) return;
      this.state = next;
      const p = PROFILES[next];
      this.colorFrom = this.currentColorName();
      this.colorTo = p.color;
      this.colorT = 0;
      this.profile = p;
    }

    currentColorName() {
      return this.colorT >= 1 ? this.colorTo : this.colorFrom;
    }

    setLevel(v) { this.level = clamp(v || 0, 0, 1); }

    /** Interpolation douce des paramètres : aucune rupture visuelle. */
    _ease(dt) {
      const k = 1 - Math.pow(0.001, dt);
      for (const key of ['spin', 'breath', 'breathHz', 'segments', 'particles', 'converge', 'stream', 'glow']) {
        this.blend[key] = lerp(this.blend[key], this.profile[key], k);
      }
      this.colorT = clamp(this.colorT + dt * 1.6, 0, 1);
    }

    frame(dt, reduced) {
      this.resize();
      if (!this.w || !this.h) return;
      this._ease(dt);
      this.t += dt;

      const ctx = this.ctx;
      const b = this.blend;
      const cx = (this.w / 2) * this.dpr;
      const cy = (this.h / 2) * this.dpr;
      const R = Math.min(this.w, this.h) * 0.5 * this.dpr;

      const col = mixColor(this.colorFrom, this.colorTo, this.colorT);
      const C = (a) => `rgba(${col[0]},${col[1]},${col[2]},${a})`;

      const breath = reduced ? 0 : Math.sin(this.t * TAU * b.breathHz) * b.breath;
      const audio = this.level * 0.16;
      const scale = 1 + breath + audio;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.globalCompositeOperation = 'lighter';

      /* ---- halo profond ---------------------------------------------- */
      const haloR = R * 0.96 * scale;
      const halo = ctx.createRadialGradient(0, 0, R * 0.04, 0, 0, haloR);
      halo.addColorStop(0, C(0.30 * b.glow));
      halo.addColorStop(0.32, C(0.10 * b.glow));
      halo.addColorStop(1, C(0));
      ctx.fillStyle = halo;
      ctx.beginPath(); ctx.arc(0, 0, haloR, 0, TAU); ctx.fill();

      /* ---- anneaux concentriques (rotations indépendantes) ------------ */
      const rings = [
        { r: 0.92, w: 0.9, dash: [R * 0.02, R * 0.055], dir: 1, sp: 0.30, a: 0.38 },
        { r: 0.76, w: 1.3, dash: [R * 0.18, R * 0.09], dir: -1, sp: 0.55, a: 0.5 },
        { r: 0.58, w: 1.0, dash: [R * 0.012, R * 0.03], dir: 1, sp: 0.95, a: 0.42 },
        { r: 0.42, w: 1.8, dash: [R * 0.30, R * 0.42], dir: -1, sp: 1.4, a: 0.62 },
      ];
      rings.forEach((ring, i) => {
        if (!reduced) this.ringPhase[i] += dt * ring.sp * b.spin * ring.dir * 2.2;
        ctx.save();
        ctx.rotate(this.ringPhase[i]);
        ctx.beginPath();
        ctx.arc(0, 0, R * ring.r * scale, 0, TAU);
        ctx.setLineDash(ring.dash);
        ctx.lineWidth = ring.w * this.dpr;
        ctx.strokeStyle = C(ring.a * (0.45 + b.glow * 0.55));
        ctx.stroke();
        ctx.restore();
      });
      ctx.setLineDash([]);

      /* ---- segments HUD (activité) ------------------------------------ */
      const segCount = 18;
      const active = b.segments;
      for (let i = 0; i < segCount; i++) {
        const phase = (this.t * 0.9 + i / segCount) % 1;
        const lit = phase < active;
        const a0 = (i / segCount) * TAU + this.ringPhase[1] * 0.4;
        const a1 = a0 + TAU / segCount * 0.58;
        ctx.beginPath();
        ctx.arc(0, 0, R * 0.985 * scale, a0, a1);
        ctx.lineWidth = (lit ? 2.6 : 1.1) * this.dpr;
        ctx.strokeStyle = C(lit ? 0.55 * (0.4 + b.glow * 0.6) : 0.12);
        ctx.stroke();
      }

      /* ---- particules orbitales / convergentes ------------------------ */
      for (const p of this.particles) {
        if (!reduced) p.a += dt * p.sp * (0.35 + b.spin * 0.9);
        let rr = p.r;
        if (b.converge > 0.02) {
          const pull = ((this.t * 0.55 + p.conv) % 1);
          rr = lerp(p.r, 0.22, b.converge * (1 - pull));
        }
        const x = Math.cos(p.a) * R * rr * scale;
        const y = Math.sin(p.a) * R * rr * scale * 0.86; // profondeur simulée
        const size = p.s * this.dpr * (0.6 + p.z * 0.8);
        ctx.beginPath();
        ctx.arc(x, y, size, 0, TAU);
        ctx.fillStyle = C(p.o * b.particles * (0.5 + b.glow * 0.5));
        ctx.fill();
      }

      /* ---- flux de données (outils / lecture fichier / sync) ---------- */
      if (b.stream > 0.03) {
        for (const s of this.streams) {
          if (!reduced) s.p = (s.p + dt * s.sp) % 1;
          const rIn = lerp(1.05, 0.3, s.p);
          const a = s.a + this.ringPhase[2] * 0.2;
          const x0 = Math.cos(a) * R * rIn * scale;
          const y0 = Math.sin(a) * R * rIn * scale * 0.86;
          const x1 = Math.cos(a) * R * (rIn - s.len) * scale;
          const y1 = Math.sin(a) * R * (rIn - s.len) * scale * 0.86;
          ctx.beginPath();
          ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
          ctx.lineWidth = 1.4 * this.dpr;
          ctx.strokeStyle = C(0.45 * b.stream * (1 - Math.abs(s.p - 0.5) * 0.8));
          ctx.stroke();
        }
      }

      /* ---- noyau ------------------------------------------------------ */
      const nR = R * 0.2 * scale * (1 + this.level * 0.25);
      const nucleus = ctx.createRadialGradient(0, 0, 0, 0, 0, nR * 2.4);
      nucleus.addColorStop(0, `rgba(238,250,255,${0.85 * (0.5 + b.glow * 0.5)})`);
      nucleus.addColorStop(0.35, C(0.55));
      nucleus.addColorStop(1, C(0));
      ctx.fillStyle = nucleus;
      ctx.beginPath(); ctx.arc(0, 0, nR * 2.4, 0, TAU); ctx.fill();

      ctx.beginPath();
      ctx.arc(0, 0, nR, 0, TAU);
      ctx.fillStyle = `rgba(238,250,255,${0.16 + b.glow * 0.2})`;
      ctx.fill();

      ctx.restore();
    }
  }

  /* ------------------------------------------------------------ manager */
  const JarvisCore = {
    instances: [],
    state: 'IDLE',
    reason: '',
    _raf: null,
    _last: 0,
    _visible: true,
    _flashToken: 0,

    reduced() {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    },

    mount(canvas, options) {
      if (!canvas) return null;
      const inst = new CoreInstance(canvas, options);
      inst.setState(this.state);
      this.instances.push(inst);
      this.start();
      return inst;
    },

    unmount(canvas) {
      this.instances = this.instances.filter((i) => i.canvas !== canvas);
    },

    /** État réel de JARVIS → signature visuelle du core. */
    setState(state, extra) {
      const next = PROFILES[state] ? state : 'IDLE';
      this.state = next;
      this.reason = (extra && extra.reason) || '';
      this.instances.forEach((i) => i.setState(next));
      document.documentElement.setAttribute('data-core-state', next);
      window.dispatchEvent(new CustomEvent('jarvis:core-state', { detail: { state: next, reason: this.reason } }));
    },

    /** Flash court (SUCCESS / ERROR) puis retour à l'état précédent. */
    flash(state, ms, extra) {
      const token = ++this._flashToken;
      const prev = this.state;
      this.setState(state, extra);
      setTimeout(() => {
        if (token === this._flashToken && this.state === state) this.setState(prev === state ? 'IDLE' : prev);
      }, ms || 1600);
    },

    setLevel(v) { this.instances.forEach((i) => i.setLevel(v)); },

    start() {
      if (this._raf) return;
      this._last = performance.now();
      const loop = (now) => {
        this._raf = requestAnimationFrame(loop);
        const dt = clamp((now - this._last) / 1000, 0, 0.1);
        this._last = now;
        if (!this._visible) return;                    // pas de calcul hors écran
        const reduced = this.reduced();
        for (const inst of this.instances) {
          if (!inst.canvas.isConnected) continue;
          if (inst.canvas.offsetParent === null) continue; // panneau masqué → pause
          inst.frame(dt, reduced);
        }
      };
      this._raf = requestAnimationFrame(loop);
    },

    stop() {
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = null;
    },
  };

  document.addEventListener('visibilitychange', () => {
    JarvisCore._visible = !document.hidden;
    JarvisCore._last = performance.now();
  });

  window.JarvisCore = JarvisCore;
})();
