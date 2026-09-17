/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_browser.js
   Aperçu navigateur LIVE : rien ici n'est simulé.
   Chaque frame est un JPEG produit par la session Playwright réelle du
   backend, reçu soit par SSE (`browser.frame`), soit — si le flux décroche —
   par le fallback HTTP `GET /api/browser/frame`.
   Règle : quand quelque chose ne peut pas être exécuté, on ne l'affiche pas.
   ========================================================================== */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // Diagnostic navigateur : silencieux par défaut, activable côté développeur
  //   localStorage.setItem('jarvis.debug.browser','1')
  const dbg = (...a) => {
    try {
      if (window.JARVIS_DEV || localStorage.getItem('jarvis.debug.browser') === '1') {
        console.debug('[Browser]', ...a);
      }
    } catch (_) { /* stockage indisponible */ }
  };

  // Trois tailles de dock ; on règle la variable --brow pour que le chat se
  // place à gauche sans jamais être masqué par le panneau.
  const SIZES = { full: 1, wide: 0.52, half: 0.36, compact: 0.36 };
  const MODES = {
    compact: 'COMPACT',    // ~380–450 px – aperçu permanent
    half:    'COMPACT',
    wide:    'EXPANSION',  // ~50 % – appel visuel en cours
    full:    'FULLSCREEN', // le navigateur prend la place de l'écran
  };
  const COMPACT_MIN = 380, COMPACT_MAX = 450;   // largeur réelle du dock compact
  const FRAME_STALL_MS = 1200;                  // SSE muet → on bascule en polling
  const POLL_MS = 220;                          // ~4,5 FPS max

  const Browser = {
    SIZES, MODES,
    mode: 'compact',
    docked: false,          // dock visible (compact/wide/full)
    alive: false,
    dismissed: false,       // l'utilisateur a fermé le dock à la main
    gate: false,
    lastUrl: '',
    lastFrameAt: 0,
    frames: 0,
    _touchT: null,
    _pollT: null,
    _polling: false,

    init() {
      if (this.el) { this._reparent(); return this; }   // idempotent
      // Le dock se monte dans le portail V5 s'il existe déjà ; sinon dans le
      // body, et il sera reparenté dès que la scène V5 est construite. Sans
      // ce repli, une session ouverte avant le montage du shell n'affichait
      // rien du tout : init() renvoyait null et show() abandonnait.
      const host = $('v5Portal') || document.body;
      if (!host) return null;
      const el = document.createElement('aside');
      el.className = 'v5-browser';
      el.id = 'v5Browser';
      el.innerHTML = `
        <header class="b-head">
          <b class="b-title">APERÇU LIVE</b>
          <span class="b-state" id="v5BState">—</span>
          <div class="b-size">
            <button data-size="compact" title="Aperçu permanent">▭</button>
            <button data-size="wide" title="Extension (~50 %)">▮</button>
            <button data-size="full" title="Plein écran">▣</button>
          </div>
          <button class="b-close" id="v5BClose" title="Fermer l'aperçu">✕</button>
        </header>
        <form class="b-bar" id="v5BBar">
          <button type="button" class="b-btn" id="v5BBack" title="Retour">‹</button>
          <button type="button" class="b-btn" id="v5BForward" title="Suivant">›</button>
          <input id="v5BUrl" placeholder="https://…" spellcheck="false" autocomplete="off">
          <button type="submit" class="b-go">⏎</button>
        </form>
        <div class="b-stage" id="v5BStage">
          <div class="b-splash" id="v5BSplash">SESSION FERMÉE</div>
          <img id="v5BFrame" alt="Aperçu navigateur en temps réel" hidden>
          <div class="b-halo" id="v5BHalo" hidden></div>
        </div>
        <div class="b-gate" id="v5BGate" hidden>
          <p id="v5BGateMsg">Action manuelle requise</p>
          <button id="v5BResume">REPRENDRE</button>
        </div>
        <div class="b-log" id="v5BLog" aria-label="Actions navigateur réelles"></div>
      `;
      host.appendChild(el);
      this.el = el;
      this.stage = $('v5BStage');
      this.frame = $('v5BFrame');
      this.splash = $('v5BSplash');
      this.halo = $('v5BHalo');
      this.url = $('v5BUrl');
      this.stateEl = $('v5BState');
      this.logEl = $('v5BLog');        // /!\ ne jamais nommer `log` : écrase log()
      this.gateEl = $('v5BGate');
      this.gateMsg = $('v5BGateMsg');

      this.frame.addEventListener('error', () => dbg('frame decode error'));

      el.querySelectorAll('.b-size button').forEach((b) => {
        b.addEventListener('click', () => this.resize(b.dataset.size));
      });
      $('v5BClose').addEventListener('click', () => this.close({ byUser: true }));
      $('v5BBack').addEventListener('click', () => this.act('back', {}));
      $('v5BForward').addEventListener('click', () => this.act('forward', {}));
      $('v5BBar').addEventListener('submit', (e) => {
        e.preventDefault();
        const url = this.url.value.trim();
        if (url) this.act('navigate', { url });
      });
      $('v5BResume').addEventListener('click', () => this.act('resume'));

      addEventListener('resize', () => { if (this.docked) this.setBrow(this.mode); });

      // Le stream s'arrête quand l'aperçu sort de l'écran : on le dit au
      // backend (touch) et on coupe le décodage des frames.
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) { this._haltTouch(); this._stopPoll(); }
        else if (this.docked) { this._keepAlive(); this._watchFrames(); }
      });
      dbg('dock monté dans', host.id || host.tagName.toLowerCase());
      return this;
    },

    /** Le portail V5 peut être construit après nous : on s'y raccroche. */
    _reparent() {
      const portal = $('v5Portal');
      if (portal && this.el && this.el.parentNode !== portal) portal.appendChild(this.el);
    },

    /** Notifié par browser.session.started, ou par la première frame reçue. */
    show(mode) {
      if (!this.el && !this.init()) return;
      this._reparent();
      const first = !this.docked;
      this.docked = true;
      this.el.classList.add('in');
      this.resize(mode || this.mode || 'compact');
      this._keepAlive();
      this._watchFrames();
      if (first) {
        this.splash.textContent = 'CONNEXION…';
        this.splash.hidden = false;
        dbg('dock ouvert', this.mode);
      }
    },
    close({ byUser = false } = {}) {
      // Fermer à la main est une DÉCISION, pas un état transitoire. Sans ce
      // drapeau, le filet de sécurité de browser.frame redockait le panneau à
      // l'image suivante : l'utilisateur fermait, ça rouvrait aussitôt.
      if (byUser) this.dismissed = true;
      this.docked = false;
      this.alive = false;
      if (!this.el) return;
      this.el.classList.remove('in');
      this.detach();
      this._haltTouch();
      this._stopPoll();
      document.documentElement.style.setProperty('--brow', '0px');
      // Restaure le panneau contextuel.
      const rp = document.getElementById('v5RPanel');
      if (rp) rp.style.display = '';
      dbg('dock fermé');
    },

    resize(mode) {
      if (!SIZES[mode]) mode = 'compact';
      this.mode = mode;
      const el = this.el;
      el.dataset.mode = mode;
      el.querySelectorAll('.b-size button').forEach((b) => {
        b.classList.toggle('on', b.dataset.size === mode);
      });
      this.setBrow(mode);
      // Cache le panneau contextuel en plein écran.
      const rp = document.getElementById('v5RPanel');
      if (rp) rp.style.display = mode === 'full' ? 'none' : '';
    },
    setBrow(mode) {
      const vw = innerWidth;
      let w;
      if (mode === 'full') {
        w = 0;
        this.el.classList.add('full');
        this.el.style.width = '';
      } else {
        this.el.classList.remove('full');
        w = Math.round((SIZES[mode] || 0.36) * vw);
        if (mode === 'compact' || mode === 'half') {
          w = Math.min(COMPACT_MAX, Math.max(COMPACT_MIN, w));
        }
        w = Math.min(w, Math.max(280, vw - 320));   // jamais hors viewport
        this.el.style.width = w + 'px';
      }
      // La largeur réservée au chat correspond à la largeur réelle du dock.
      document.documentElement.style.setProperty('--brow', w + 'px');
    },

    _keepAlive() {
      if (!this.docked || document.hidden) return;
      fetch('/api/browser/touch', { method: 'POST' }).catch(() => {});
      clearInterval(this._touchT);
      this._touchT = setInterval(() => {
        if (this.docked && !document.hidden) fetch('/api/browser/touch', { method: 'POST' }).catch(() => {});
      }, 1000);
    },
    _haltTouch() {
      clearInterval(this._touchT);
      this._touchT = null;
    },

    /* ------------------------------------------------- fallback de frames */
    /* Le SSE reste la voie normale. S'il ne délivre plus rien alors que la
       session est active, on va chercher la frame en HTTP (≈4,5 FPS max). */
    _watchFrames() {
      if (this._pollT) return;
      this._pollT = setInterval(() => {
        if (!this.docked || document.hidden) return;
        if (Date.now() - this.lastFrameAt < FRAME_STALL_MS) return;
        this._pullFrame();
      }, POLL_MS);
    },
    _stopPoll() {
      clearInterval(this._pollT);
      this._pollT = null;
      this._polling = false;
    },
    _pullFrame() {
      if (this._polling) return;
      this._polling = true;
      fetch('/api/browser/frame')
        .then((r) => (r.ok ? r.json() : null))
        .then((res) => {
          const d = res && (res.data || res);
          if (d && d.data_url) {
            dbg('browser.frame (http)', d.bytes, 'o seq', d.seq);
            this.paint({ data_url: d.data_url, url: d.url, w: d.w, h: d.h });
          }
        })
        .catch(() => {})
        .finally(() => { this._polling = false; });
    },

    /** Frame réelle : on peint, on ne simule pas. */
    paint(payload) {
      if (!this.docked || !this.frame) return;
      const data = payload || {};
      const raw = data.jpeg || String(data.data_url || '').split(',')[1] || '';
      // Jamais de chaîne invalide dans img.src.
      if (raw.length < 64 || !/^[A-Za-z0-9+/=]+$/.test(raw)) return;
      // Le dock a pu être dimensionné avant que la fenêtre ait sa taille
      // finale (montage précoce) : on resynchronise --brow si besoin.
      if (this._vw !== innerWidth) { this._vw = innerWidth; this.setBrow(this.mode); }
      this.frame.src = 'data:image/jpeg;base64,' + raw;
      this.frame.hidden = false;
      this.splash.hidden = true;
      this.alive = true;
      this.frames++;
      this.lastFrameAt = Date.now();
      this.stateEl.textContent = 'LIVE';
      this.stateEl.dataset.tone = 'ok';
      if (data.url) { this.lastUrl = data.url; this.url.value = data.url; }
      if (data.halo) this.paintHalo(data.halo);
    },
    paintHalo(halo) {
      const st = this.stage;
      const iw = this.frame.naturalWidth || 1280, ih = this.frame.naturalHeight || 800;
      const sw = st.offsetWidth, sh = st.offsetHeight;
      // object-fit:contain → l'image occupe un rectangle centré du stage.
      const scale = Math.min(sw / iw, sh / ih) || 1;
      const dw = iw * scale, dh = ih * scale;
      const ox = (sw - dw) / 2, oy = (sh - dh) / 2;
      this.halo.style.left = (ox + (halo.x / iw) * dw) + 'px';
      this.halo.style.top = (oy + (halo.y / ih) * dh) + 'px';
      this.halo.style.width = Math.max(4, (halo.w / iw) * dw) + 'px';
      this.halo.style.height = Math.max(4, (halo.h / ih) * dh) + 'px';
      this.halo.hidden = false;
      clearTimeout(this._haloT);
      this._haloT = setTimeout(() => { this.halo.hidden = true; }, 900);
    },

    setInfo(url, title) {
      if (!this.el) return;
      if (url) { this.lastUrl = url; this.url.value = url; }
      if (!this.alive) {
        this.stateEl.textContent = 'CHARGÉ';
        this.stateEl.dataset.tone = '';
      }
    },

    /** Événement browser.action réel (jamais un texte fictif). */
    log(text, privateField) {
      if (!this.logEl) return;
      const row = document.createElement('div');
      row.className = 'b-log-row' + (privateField ? ' priv' : '');
      row.textContent = text || '';
      this.logEl.prepend(row);
      while (this.logEl.children.length > 24) this.logEl.lastElementChild.remove();
    },

    gate(on, message) { this.setGate(on, message); },
    setGate(on, message) {
      if (!this.el) return;
      this.gate = !!on;
      this.gateEl.hidden = !on;
      this.gateMsg.textContent = message || 'Action manuelle requise';
      this.stateEl.textContent = on ? 'ATTENTE' : 'LIVE';
      this.stateEl.dataset.tone = on ? 'warn' : 'ok';
    },

    act(op, args) {
      fetch('/api/browser/action', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op, args }),
      }).then((r) => r.json()).then((res) => {
        if (res && res.ok === false) this.log('ERREUR · ' + (res.error || ''), false);
        if (op === 'resume') this.setGate(false);
      }).catch(() => this.log('ERREUR · requête impossible', false));
    },

    /** Session terminée : plus aucune frame ne doit rester en mémoire. */
    detach() {
      if (!this.frame) return;
      this.frame.hidden = true;
      this.frame.removeAttribute('src');       // libère la dernière frame
      this.alive = false;
      this.frames = 0;
      this.lastFrameAt = 0;
      this.splash.hidden = false;
      this.splash.textContent = 'SESSION FERMÉE';
      this.stateEl.textContent = '—';
      this.stateEl.dataset.tone = '';
      this._stopPoll();
    },

    /** État réel du dock — utilisé par les tests de rendu. */
    diagnostics() {
      if (!this.el) return { mounted: false };
      const cs = getComputedStyle(this.el);
      const r = this.el.getBoundingClientRect();
      return {
        mounted: true, docked: this.docked, mode: this.mode, frames: this.frames,
        parent: this.el.parentNode && (this.el.parentNode.id || this.el.parentNode.tagName),
        display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
        zIndex: cs.zIndex, overflow: cs.overflow, transform: cs.transform,
        width: Math.round(r.width), height: Math.round(r.height),
        left: Math.round(r.left), right: Math.round(r.right),
        brow: getComputedStyle(document.documentElement).getPropertyValue('--brow').trim(),
        imgComplete: this.frame.complete,
        naturalWidth: this.frame.naturalWidth, naturalHeight: this.frame.naturalHeight,
        polling: !!this._pollT, touching: !!this._touchT,
        url: this.lastUrl,
      };
    },
  };

  // Branchement SSE : ouverture automatique, frames et fin de session.
  function bind() {
    if (Browser._sse) return;
    if (typeof J === 'undefined' || typeof J.on !== 'function') {
      setTimeout(bind, 400);       // core.js pas encore prêt : on réessaie
      return;
    }
    Browser._sse = true;

    J.on('browser.session.started', (d) => {
      dbg('browser.session.started', (d && d.url) || '');
      // Une nouvelle session est une nouvelle demande : le refus précédent ne
      // vaut que pour la session qu'on a fermée.
      Browser.dismissed = false;
      Browser.show('compact');     // l'utilisateur n'a rien à cliquer
    });
    J.on('browser.navigate', (d) => {
      dbg('browser.navigate', (d && d.url) || '');
      if (!Browser.dismissed) Browser.show('compact');
      Browser.setInfo(d && d.url, d && d.title);
    });
    J.on('browser.frame', (d) => {
      if (!Browser.docked && !Browser.dismissed) Browser.show('compact');   // filet de sécurité
      dbg('browser.frame', (d && d.bytes) || 0, 'o seq', (d && d.seq) || '?');
      Browser.paint(d);
    });
    J.on('browser.error', (d) => { dbg('browser.error', (d && d.stage) || ''); });
    J.on('browser.session.finished', () => {
      dbg('browser.session.finished');
      Browser.detach();
      Browser.log('SESSION FERMÉE');
      setTimeout(() => { if (!Browser.alive) Browser.close(); }, 2500);
    });
  }

  window.JarvisBrowser = Browser;

  function boot() { Browser.init(); bind(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
