/* ==========================================================================
   Noyau frontend : état partagé, API, flux SSE, helpers UI.
   Aucune donnée n'est inventée : tout vient de /api/*.
   ========================================================================== */
const J = {
  state: {
    status: null,
    feed: [],
    agents: [],
    tasks: [],
    llm: [],
    connectors: [],
    tools: [],
    settings: null,
    conversation: null,
    messages: [],
    calendar: [],
    workflows: [],
    memoryStats: null,
    memorySeries: [],
    pendingConfirmations: [],
    page: 'command',
    connected: false,
  },
  listeners: {},
};

/* ------------------------------------------------------------------ API */
J.api = async function (path, options = {}) {
  const opts = { headers: { 'Content-Type': 'application/json' }, ...options };
  if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { ok: false, error: text.slice(0, 300) }; }
  if (!res.ok && data.ok === undefined) data.ok = false;
  return data;
};
J.get = (p) => J.api(p);
J.post = (p, body) => J.api(p, { method: 'POST', body: body || {} });
J.put = (p, body) => J.api(p, { method: 'PUT', body: body || {} });
J.del = (p) => J.api(p, { method: 'DELETE', body: {} });

/* -------------------------------------------------------------- events */
J.on = function (type, cb) {
  (J.listeners[type] = J.listeners[type] || []).push(cb);
};
J.fire = function (type, payload) {
  (J.listeners[type] || []).forEach((cb) => { try { cb(payload); } catch (e) { console.error(e); } });
  (J.listeners['*'] || []).forEach((cb) => { try { cb(type, payload); } catch (e) { console.error(e); } });
};

/* ----------------------------------------------------------------- SSE */
J.connectStream = function () {
  let source = null;
  let retry = 1000;

  const connect = () => {
    source = new EventSource('/api/events');
    source.onopen = () => {
      retry = 1000;
      J.state.connected = true;
      J.fire('stream.open');
      // IMPORTANT : une (re)connexion du flux ne déclenche AUCUN greeting.
      // Le greeting est décidé par le serveur, une seule fois par session.
    };
    source.onmessage = (evt) => {
      try {
        const event = JSON.parse(evt.data);
        J.fire(event.type, event.data || {});
        J.fire('event', event);
      } catch { /* ignoré */ }
    };
    source.onerror = () => {
      J.state.connected = false;
      J.fire('stream.close');
      source.close();
      retry = Math.min(retry * 1.6, 15000);
      setTimeout(connect, retry);
    };
  };
  connect();
};

/* --------------------------------------------------------------- utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* -------------------------------------------------------- rendu markdown */
/* Renderer markdown léger et sûr : le texte est échappé en HTML d'abord,
   puis les motifs markdown sont transformés. L'utilisateur/le modèle ne peut
   pas injecter de balise brute ; les liens sont limités à http(s) et mailto. */
function inlineMd(str) {
  return str
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\[([^\]]+)\]\((mailto:[^\s)]+)\)/g, '<a href="$2">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/__([^_]+)__/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/_([^_]+)_/g, '<em>$1</em>')
    .replace(/~~([^~]+)~~/g, '<s>$1</s>');
}

function mdToHtml(text) {
  const src = esc(String(text ?? ''));
  const out = [];
  let para = [];
  let list = null;
  let fenceLang = '';
  let codeBuf = null;

  const flushPara = () => {
    if (!para.length) return;
    out.push(`<p>${inlineMd(para.join('<br>'))}</p>`);
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`);
    list = null;
  };

  for (const raw of src.split('\n')) {
    const line = raw.trimEnd();
    const t = line.trim();

    if (codeBuf !== null) {
      if (/^```/.test(t)) {
        out.push(`<pre${fenceLang ? ` data-lang="${esc(fenceLang)}"` : ''}><code>${codeBuf.join('\n')}</code></pre>`);
        codeBuf = null;
        fenceLang = '';
      } else {
        codeBuf.push(line);
      }
      continue;
    }
    if (/^```/.test(t)) {
      flushPara(); flushList();
      fenceLang = (t.match(/^```\s*([\w+.-]*)\s*$/)?.[1] ?? '').trim();
      codeBuf = [];
      continue;
    }
    const head = t.match(/^(#{1,4})\s+(.*)$/);
    if (head) {
      flushPara(); flushList();
      const lvl = head[1].length;
      out.push(`<h${lvl}>${inlineMd(head[2])}</h${lvl}>`);
      continue;
    }
    const ol = t.match(/^(\d+)[.)]\s+(.*)$/);
    if (ol) {
      flushPara();
      if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
      list.items.push(`<li>${inlineMd(ol[2])}</li>`);
      continue;
    }
    const ul = t.match(/^[-*+]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
      list.items.push(`<li>${inlineMd(ul[1])}</li>`);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flushPara(); flushList();
      out.push('<hr>');
      continue;
    }
    const quote = t.match(/^&gt;+\s?(.*)$/);
    if (quote) {
      flushPara(); flushList();
      out.push(`<blockquote>${inlineMd(quote[1])}</blockquote>`);
      continue;
    }
    if (!t) { flushPara(); flushList(); continue; }
    para.push(line);
  }
  flushPara(); flushList();
  return out.join('\n');
}

function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}
function fmtDateTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString('fr-FR',
    { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function fmtAgo(ts) {
  if (!ts) return 'jamais';
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return `il y a ${Math.round(diff)}s`;
  if (diff < 3600) return `il y a ${Math.round(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.round(diff / 3600)} h`;
  return `il y a ${Math.round(diff / 86400)} j`;
}
function fmtDuration(seconds) {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}:${String(s).padStart(2, '0')}`;
}
/** Affiche une valeur ou une mention explicite — jamais un chiffre inventé. */
function orUnavailable(value, suffix = '', fallback = 'Unavailable') {
  return (value === null || value === undefined || value === '') ? fallback : `${value}${suffix}`;
}

function toast(message, kind = '') {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 4200);
}

function modal({ title, body, footer, wide }) {
  const root = $('#modalRoot');
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `
    <div class="modal ${wide ? 'wide' : ''}">
      <div class="modal-head"><h3>${esc(title)}</h3>
        <button class="icon-btn" data-close style="width:24px;height:24px">${icon('x', 12)}</button></div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
  root.appendChild(backdrop);
  const close = () => backdrop.remove();
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  $$('[data-close]', backdrop).forEach((b) => b.addEventListener('click', close));
  return { el: backdrop, close, $: (s) => $(s, backdrop), $$: (s) => $$(s, backdrop) };
}

function confirmDialog(title, message, { danger } = {}) {
  return new Promise((resolve) => {
    const m = modal({
      title,
      body: `<div class="risk-banner ${danger ? 'destructive' : ''}">${icon('alert', 16)}
             <div>${esc(message)}</div></div>`,
      footer: `<button class="btn" data-close>Annuler</button>
               <button class="btn ${danger ? 'danger' : 'primary'}" data-yes>Confirmer</button>`,
    });
    m.$('[data-yes]').addEventListener('click', () => { m.close(); resolve(true); });
    m.el.addEventListener('click', (e) => { if (e.target === m.el) resolve(false); });
    m.$$('[data-close]').forEach((b) => b.addEventListener('click', () => resolve(false)));
  });
}

/* ------------------------------------------------ canvas : mini waveform */
function drawWave(canvas, { amplitude = 0.25, speed = 1, color = '#22d3ee', bars = 0, phase = 0 } = {}) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  if (bars) {
    const gap = w / bars;
    for (let i = 0; i < bars; i++) {
      const t = phase + i * 0.55;
      const amp = Math.abs(Math.sin(t) * Math.cos(t * 0.6 + 1)) * amplitude * h;
      const barH = Math.max(1.5, amp);
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.35 + (barH / h) * 0.65;
      ctx.fillRect(i * gap + gap * 0.2, h / 2 - barH / 2, Math.max(1.2, gap * 0.5), barH);
    }
    ctx.globalAlpha = 1;
    return;
  }
  ctx.beginPath();
  for (let x = 0; x < w; x++) {
    const y = h / 2 + Math.sin(x * 0.09 + phase * speed) * amplitude * h * 0.42
      * Math.sin(phase * 0.7 + x * 0.012);
    x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
}

/* ------------------------------------------------------- gauge circulaire */
function gaugeHtml({ label, value, sub, unit = '%' }) {
  const size = 74;
  const stroke = 5;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const offset = circumference * (1 - pct / 100);
  const color = pct >= 90 ? 'var(--danger)' : pct >= 75 ? 'var(--warn)' : 'var(--cyan)';
  return `<div class="gauge">
    <svg width="${size}" height="${size}">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
        stroke="rgba(56,142,190,.16)" stroke-width="${stroke}"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}"
        stroke-width="${stroke}" stroke-linecap="round"
        stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
        style="transition:stroke-dashoffset .6s ease"/>
    </svg>
    <div class="val">${value == null ? '—' : Math.round(value)}${value == null ? '' : `<span style="font-size:9px">${unit}</span>`}</div>
    <div class="lbl">${esc(label)}</div>
    <div class="sub">${esc(sub || '')}</div>
  </div>`;
}
