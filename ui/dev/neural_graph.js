/* JARVIS V5 — banc d'essai du graphe neuronal.
   Ce fichier ne contient QUE le HUD : le moteur 3D vit dans
   /js/v5/neural_graph_engine.js, monté à l'identique par la vue AI Core. */

import { createNeuralGraph } from '../js/v5/neural_graph_engine.js';
import { CLUSTERS, NODES, LINKS, METRICS, LOG_SAMPLES } from '../js/v5/neural_graph_data.js';

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const graph = createNeuralGraph({
  host: $('#stage'),
  data: { clusters: CLUSTERS, nodes: NODES, links: LINKS },
  onSelect: (n) => {
    renderDetail(n);
    $('#focusTag').textContent = n ? `FOCUS · ${n.label}` : '';
    $('#focusTag').classList.toggle('on', !!n);
  },
});
window.__graph = graph;   // réglages depuis la console, sans instrumenter le code

/* ---------------------------------------------------------------- clusters */
function renderClusters() {
  const counts = {};
  graph.nodes.forEach((n) => { counts[n.cluster] = (counts[n.cluster] || 0) + 1; });
  $('#clusters').innerHTML = Object.entries(CLUSTERS).map(([key, c]) => `
    <div class="cl" data-cluster="${key}">
      <span class="dot" style="background:${c.css};box-shadow:0 0 10px ${c.css}"></span>
      <span class="nm">${esc(c.name)}</span>
      <span class="ct">${counts[key] || 0}</span>
    </div>`).join('');
  $('#clusters').querySelectorAll('.cl').forEach((el) => {
    el.addEventListener('click', () => {
      const key = el.dataset.cluster;
      const next = !graph.isClusterVisible(key);
      graph.setClusterVisible(key, next);
      el.classList.toggle('off', !next);
      const on = Object.keys(CLUSTERS).filter((k) => graph.isClusterVisible(k)).length;
      $('#clTag').textContent = `${on} actifs`;
    });
  });
  $('#clTag').textContent = `${Object.keys(CLUSTERS).length} actifs`;
}

/* -------------------------------------------------------------- inspecteur */
function renderDetail(n) {
  const body = $('#detailBody');
  if (!n) {
    body.className = 'empty';
    body.innerHTML = 'Aucun nœud sélectionné.<br>Cliquez une sphère pour focaliser la caméra et révéler ses connexions.';
    $('#dTag').textContent = '—';
    return;
  }
  const c = CLUSTERS[n.cluster];
  const neigh = [...n.neighbors].map((id) => graph.byId.get(id)).filter(Boolean);
  body.className = '';
  body.innerHTML = `
    <h2 style="color:${c.css};text-shadow:0 0 18px ${c.css}55">${esc(n.label)}</h2>
    <div class="sub">${esc(c.name)} · ${esc(n.id)}</div>
    <p>${esc(n.desc)}</p>
    <div class="kv">${Object.entries(n.meta || {}).map(([k, v]) =>
      `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join('')}</div>
    <div class="sub">Connexions (${neigh.length})</div>
    <div class="links">${neigh.map((m) =>
      `<span class="chip" data-goto="${esc(m.id)}">${esc(m.label)}</span>`).join('')}</div>`;
  body.querySelectorAll('[data-goto]').forEach((el) => {
    el.addEventListener('click', () => graph.select(el.dataset.goto));
  });
  $('#dTag').textContent = c.name.toUpperCase();
}

/* ------------------------------------------- radar 2D : cercles + balayage */
const radar = $('#radar').getContext('2d');
function drawRadar(t) {
  const W = 372, R = W / 2;
  radar.clearRect(0, 0, W, W);
  radar.save();
  radar.translate(R, R);
  for (let i = 1; i <= 4; i++) {
    radar.beginPath();
    radar.arc(0, 0, (R - 14) * (i / 4), 0, Math.PI * 2);
    radar.strokeStyle = `rgba(34,211,238,${0.06 + i * 0.03})`;
    radar.lineWidth = 1.5;
    radar.stroke();
  }
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    radar.beginPath();
    radar.moveTo(Math.cos(a) * 26, Math.sin(a) * 26);
    radar.lineTo(Math.cos(a) * (R - 14), Math.sin(a) * (R - 14));
    radar.strokeStyle = 'rgba(34,211,238,.07)';
    radar.stroke();
  }
  radar.rotate(t * 0.00042);
  radar.beginPath();
  radar.arc(0, 0, R - 30, 0, Math.PI * 0.8);
  radar.strokeStyle = 'rgba(52,211,153,.55)';
  radar.lineWidth = 2;
  radar.stroke();
  radar.restore();

  radar.save();
  radar.translate(R, R);
  const sweep = t * 0.0013;
  if (radar.createConicGradient) {
    const g = radar.createConicGradient(sweep, 0, 0);
    g.addColorStop(0, 'rgba(34,211,238,.34)');
    g.addColorStop(0.12, 'rgba(34,211,238,0)');
    g.addColorStop(1, 'rgba(34,211,238,0)');
    radar.fillStyle = g;
    radar.beginPath(); radar.arc(0, 0, R - 14, 0, Math.PI * 2); radar.fill();
  }
  graph.nodes.forEach((n, i) => {
    if (n.dim && !n.hub) return;
    const a = (i / graph.nodes.length) * Math.PI * 2;
    const rr = 34 + ((n.val || 1) / 4.2) * (R - 70);
    const near = Math.abs(((sweep % (Math.PI * 2)) - ((a + Math.PI * 2) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2));
    radar.beginPath();
    radar.arc(Math.cos(a) * rr, Math.sin(a) * rr, n === graph.selected ? 6 : 3.4, 0, Math.PI * 2);
    radar.fillStyle = CLUSTERS[n.cluster].css;
    radar.globalAlpha = near < 0.5 ? 1 : 0.3;
    radar.fill();
    radar.globalAlpha = 1;
  });
  radar.beginPath(); radar.arc(0, 0, 5, 0, Math.PI * 2);
  radar.fillStyle = '#e2f6ff'; radar.fill();
  radar.restore();
}

/* ------------------------------------------------ métriques + sparkline */
const history = Array.from({ length: 60 }, () => 40 + Math.random() * 30);
function renderMetrics() {
  $('#metrics').innerHTML = METRICS.map((m) => `
    <div class="metric">
      <div class="row"><span>${esc(m.key)}</span><b>${m.value.toLocaleString('fr-FR')}${m.unit}</b></div>
      <div class="bar"><i style="width:${clamp(m.value / m.max * 100, 2, 100)}%;background:${m.css};box-shadow:0 0 10px ${m.css}"></i></div>
    </div>`).join('') + '<canvas id="spark" width="520" height="120"></canvas>';
}
function drawSpark() {
  const cv = $('#spark');
  if (!cv) return;
  const c = cv.getContext('2d'), W = cv.width, H = cv.height;
  c.clearRect(0, 0, W, H);
  c.beginPath();
  history.forEach((v, i) => {
    const x = (i / (history.length - 1)) * W, y = H - (v / 100) * H;
    i ? c.lineTo(x, y) : c.moveTo(x, y);
  });
  c.strokeStyle = '#22d3ee'; c.lineWidth = 2; c.stroke();
  c.lineTo(W, H); c.lineTo(0, H); c.closePath();
  const g = c.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, 'rgba(34,211,238,.26)');
  g.addColorStop(1, 'rgba(34,211,238,0)');
  c.fillStyle = g; c.fill();
}

/* ------------------------------- flux de logs : SSE réel, sinon simulation */
const logList = $('#logList');
function pushLog(level, source, msg) {
  graph.pulse(source);                    // le nœud concerné s'allume brièvement
  const el = document.createElement('div');
  el.className = 'log';
  el.innerHTML = `<time>${new Date().toLocaleTimeString('fr-FR')}</time>` +
    `<span class="lv ${level}">${level.toUpperCase()}</span>` +
    `<span class="msg"><b style="color:${CLUSTERS[graph.byId.get(source)?.cluster]?.css || '#7c93b5'}">${esc(source)}</b> · ${esc(msg)}</span>`;
  logList.appendChild(el);
  while (logList.children.length > 60) logList.removeChild(logList.firstChild);
  logList.scrollTop = logList.scrollHeight;
}

/* Bascule SSE <-> SIM : /api/events est retenté en permanence (3 s -> 30 s),
   donc un redémarrage du backend repasse le badge en SSE tout seul. */
let es = null, retryTimer = null, retryDelay = 3000, simTimer = null;
function connectSSE() {
  if (!('EventSource' in window)) return simulateLogs();
  clearTimeout(retryTimer);
  try { es = new EventSource('/api/events'); } catch { return scheduleRetry(); }
  const opening = setTimeout(() => { if (es && es.readyState !== 1) dropSSE(); }, 2500);
  es.onopen = () => { clearTimeout(opening); retryDelay = 3000; stopSim(); $('#sseTag').textContent = 'SSE'; };
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { d = { message: ev.data }; }
    pushLog(d.level || 'inf', d.source || 'jarvis', d.message || ev.data);
  };
  es.onerror = () => { clearTimeout(opening); dropSSE(); };
}
function dropSSE() { if (es) { es.close(); es = null; } simulateLogs(); scheduleRetry(); }
function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connectSSE, retryDelay);
  retryDelay = Math.min(30000, retryDelay * 1.6);
}
function simulateLogs() {
  if (simTimer) return;
  $('#sseTag').textContent = 'SIM';
  const emit = () => {
    const [lv, src, msg] = LOG_SAMPLES[Math.floor(Math.random() * LOG_SAMPLES.length)];
    pushLog(lv, src, msg);
    simTimer = setTimeout(emit, 1400 + Math.random() * 2600);
  };
  emit();
}
function stopSim() { clearTimeout(simTimer); simTimer = null; }

/* -------------------------------------------------------- barre du haut */
$('#sNodes').textContent = graph.nodes.length;
$('#sLinks').textContent = graph.links.length;
let mrr = 18420, req = 128;
setInterval(() => {
  mrr += Math.round((Math.random() - 0.35) * 90);
  req = clamp(req + Math.round((Math.random() - 0.5) * 22), 40, 320);
  $('#sMrr').textContent = mrr.toLocaleString('fr-FR') + ' €';
  $('#sReq').textContent = req;
  history.push(clamp(history[history.length - 1] + (Math.random() - 0.5) * 14, 8, 96));
  history.shift();
  drawSpark();
}, 1600);
setInterval(() => {
  $('#clock').textContent = new Date().toLocaleTimeString('fr-FR');
  $('#sFps').textContent = graph.fps || '—';
}, 1000);

$('#q').addEventListener('input', (e) => graph.setQuery(e.target.value));
addEventListener('keydown', (e) => {
  if (e.key === 'Escape') graph.home();
  if (e.key.toLowerCase() === 'l' && e.target.tagName !== 'INPUT') graph.toggleLabels();
});

/* radar à ~30 fps, indépendant de la boucle 3D */
setInterval(() => drawRadar(performance.now()), 33);

renderClusters();
renderMetrics();
drawSpark();
renderDetail(null);
connectSSE();
setTimeout(() => $('#boot').classList.add('gone'), 420);
