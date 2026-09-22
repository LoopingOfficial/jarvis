import {engineFeed} from './runtime/engine-feed.js';
import {VelkoScreenRouter, PANEL_LABELS, sourceOf} from './runtime/screen-router.js';
/** Les trois moniteurs de VELKO.
 *
 *  Contrat : le flux d'événements dit QUOI regarder, le moteur fournit le
 *  CONTENU. Rien n'est reconstitué ici. Chaque fichier affiché est relu sur
 *  disque via /api/workspace/file, chaque ligne de terminal vient d'un
 *  événement `terminal.*` réel, chaque diff vient d'un vrai `git diff`.
 *
 *  Aucun sélecteur de fenêtre, aucune capture système, aucun contenu
 *  d'exemple : si une source n'existe pas, l'écran le dit. */
const params = new URLSearchParams(location.search);
const pane = params.get('pane');
document.title = {editor:'VELKO · Code', terminal:'VELKO · Terminal', application:'VELKO · Outil actif'}[pane] || 'VELKO · Atelier';
if (['editor','terminal','application'].includes(pane)) document.body.classList.add(pane + '-only');
const $ = id => document.getElementById(id);
const router = new VelkoScreenRouter();
const MAX_TERMINAL_NODES = 600;
const stamp = () => new Date().toLocaleTimeString('fr-FR');

// ---------------------------------------------------------------------------
// Écran gauche — code et fichiers réels
// ---------------------------------------------------------------------------
let activePath = '';           // fichier réellement ouvert par le moteur
let activeProject = '';        // racine de projet réelle renvoyée par le moteur
const touched = new Set();     // fichiers réellement écrits pendant la mission
let treeEntries = [];
let showingDiff = false;
let loadToken = 0;

const KEYWORDS = /^(?:import|from|export|default|const|let|var|function|class|extends|return|if|else|elif|for|while|try|catch|except|finally|throw|raise|new|await|async|yield|def|in|is|not|and|or|None|True|False|self|this|null|undefined|true|false|typeof|instanceof|with|as|pass|lambda|global)$/;
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;'};
const esc = t => t.replace(/[&<>]/g, c => ESC[c]);
// Un seul passage, alternatives mutuellement exclusives : le texte affiché
// reste EXACTEMENT celui du fichier, seule la couleur est ajoutée. (Une passe
// à base de marqueurs corrompait les lignes : le marqueur se faisait recolorer.)
const SPANS = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(#.*$|\/\/.*$)/g;
const CODE = /([A-Za-z_$][\w$]*)(\s*\()?|(\d+(?:\.\d+)?)/g;

function highlightCode(part) {
 return part.replace(CODE, (whole, word, call, num) => {
  if (num !== undefined) return `<span class="tok-num">${num}</span>`;
  const tag = KEYWORDS.test(word) ? 'tok-key' : call ? 'tok-fn' : '';
  const body = tag ? `<span class="${tag}">${esc(word)}</span>` : esc(word);
  return body + esc(call || '');
 });
}

/** Coloration minimale, sans dépendance réseau : le texte reste le vrai texte. */
function highlight(line) {
 let out = '', last = 0, m;
 SPANS.lastIndex = 0;
 while ((m = SPANS.exec(line)) !== null) {
  out += highlightCode(line.slice(last, m.index));
  out += `<span class="tok-${m[1] !== undefined ? 'str' : 'com'}">${esc(m[0])}</span>`;
  last = SPANS.lastIndex;
 }
 return out + highlightCode(line.slice(last));
}

function renderCode(doc, changedLines = []) {
 const changed = new Set(changedLines);
 const table = document.createElement('table');
 const body = document.createElement('tbody');
 doc.content.split('\n').forEach((line, i) => {
  const tr = document.createElement('tr');
  if (changed.has(i + 1)) tr.className = 'changed';
  const ln = document.createElement('td'); ln.className = 'ln'; ln.textContent = String(i + 1);
  const src = document.createElement('td'); src.className = 'src'; src.innerHTML = highlight(line) || '&nbsp;';
  tr.append(ln, src); body.append(tr);
 });
 table.append(body);
 $('code-view').replaceChildren(table);
}

function codeMessage(text) {
 const p = document.createElement('p'); p.className = 'empty'; p.textContent = text;
 $('code-view').replaceChildren(p);
}

/** Charge le VRAI fichier désigné par un événement. Jamais de contenu inventé. */
async function openFile(path, {changed = false} = {}) {
 if (!path) return;
 activePath = path;
 if (changed) touched.add(path);
 $('filename').textContent = path;
 const token = ++loadToken;
 try {
  const r = await fetch('/api/workspace/file?path=' + encodeURIComponent(path));
  const d = await r.json();
  if (token !== loadToken) return;                 // un fichier plus récent a pris la main
  if (!d.ok) { codeMessage(d.error || 'Fichier indisponible.'); $('file-meta').textContent = ''; return; }
  activeProject = d.project || activeProject;
  $('filename').textContent = d.relative || d.path;
  $('file-meta').textContent = `${d.language} · ${d.lines} lignes`;
  $('diff-toggle').disabled = false;
  const hunks = changed ? await changedLines(path) : [];
  renderCode(d, hunks);
  if (showingDiff) loadDiff(path);
  loadTree(path);
  markTree();
 } catch (error) {
  if (token === loadToken) codeMessage('Contenu indisponible : ' + error.message);
 }
}

/** Lignes réellement modifiées, d'après le vrai git diff. Vide hors dépôt. */
async function changedLines(path) {
 try {
  const r = await fetch('/api/workspace/diff?path=' + encodeURIComponent(path));
  const d = await r.json();
  if (!d.ok || !d.diff) return [];
  const lines = [];
  let cursor = 0;
  for (const raw of d.diff.split('\n')) {
   const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(raw);
   if (hunk) { cursor = Number(hunk[1]); continue; }
   if (!cursor) continue;
   if (raw.startsWith('+') && !raw.startsWith('+++')) { lines.push(cursor); cursor++; }
   else if (!raw.startsWith('-')) cursor++;
  }
  return lines;
 } catch { return []; }
}

async function loadDiff(path) {
 const r = await fetch('/api/workspace/diff?path=' + encodeURIComponent(path));
 const d = await r.json().catch(() => ({}));
 const view = $('diff-view');
 view.replaceChildren();
 if (!d.ok) { view.textContent = d.error || 'Diff indisponible.'; return; }
 if (!d.diff) { view.textContent = d.detail || 'Aucune modification non validée.'; return; }
 for (const line of d.diff.split('\n')) {
  const span = document.createElement('span');
  span.className = line.startsWith('+') && !line.startsWith('+++') ? 'add'
   : line.startsWith('-') && !line.startsWith('---') ? 'del'
   : line.startsWith('@@') ? 'hunk' : '';
  span.textContent = line + '\n';
  view.append(span);
 }
}

$('diff-toggle').onclick = () => {
 showingDiff = !showingDiff;
 $('diff-toggle').classList.toggle('on', showingDiff);
 $('diff-view').hidden = !showingDiff;
 $('code-view').hidden = showingDiff;
 if (showingDiff && activePath) loadDiff(activePath);
};

/** Arborescence RÉELLE du projet qui contient le fichier ouvert. */
async function loadTree(path) {
 try {
  const r = await fetch('/api/workspace/tree?path=' + encodeURIComponent(path));
  const d = await r.json();
  if (!d.ok || d.project === activeProject && treeEntries.length) return;
  activeProject = d.project;
  treeEntries = d.entries || [];
  const nav = $('files');
  nav.replaceChildren();
  const head = document.createElement('p'); head.className = 'project'; head.textContent = d.project_name || '';
  nav.append(head);
  for (const entry of treeEntries) {
   const b = document.createElement('button');
   b.textContent = '  '.repeat(entry.depth) + (entry.dir ? '▸ ' : '') + entry.name;
   b.dataset.path = entry.path; b.title = entry.path;
   if (entry.dir) b.className = 'dir';
   else b.onclick = () => openFile(entry.path);
   nav.append(b);
  }
  markTree();
 } catch { /* l'arborescence est un confort : son absence ne masque pas le code */ }
}

function markTree() {
 for (const b of $('files').querySelectorAll('button')) {
  b.classList.toggle('active', b.dataset.path === activePath);
  b.classList.toggle('touched', touched.has(b.dataset.path));
 }
}

// ---------------------------------------------------------------------------
// Écran central — terminal réel
// ---------------------------------------------------------------------------
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;
function term(text, cls = '') {
 if (!text) return;
 const node = $('terminal');
 node.querySelector('.empty')?.remove();
 const span = document.createElement('span');
 if (cls) span.className = cls;
 span.textContent = String(text).replace(ANSI, '');   // séquences ANSI retirées, texte réel conservé
 node.append(span);
 while (node.childNodes.length > MAX_TERMINAL_NODES) node.firstChild.remove();
 node.scrollTop = node.scrollHeight;
}

// ---------------------------------------------------------------------------
// Écran droit — outil actif réel (navigateur, SSH, git, Discord…)
// ---------------------------------------------------------------------------
function setRight(kind, state, detail = '', {badge = ''} = {}) {
 if (kind !== rightPanel) rightToken++;
 rightPanel = kind;
 $('application-title').textContent = PANEL_LABELS[kind] || kind;
 $('application-state').replaceChildren(document.createTextNode(state));
 if (badge) {
  const b = document.createElement('span');
  b.className = 'badge ' + (badge === 'on' ? 'on' : 'off');
  b.textContent = badge === 'on' ? 'CONNECTÉ' : 'NON CONNECTÉ';
  $('application-state').append(b);
 }
 $('application-detail').textContent = detail;
}

function rightBody(node) { $('application-body').replaceChildren(node || document.createTextNode('')); }

/** Vue LIVE de la session navigateur de VELKO.
 *
 *  L'image vient des frames réellement capturées par la session Playwright que
 *  le moteur pilote (`browser.frame`). Ce n'est ni une fenêtre choisie par
 *  l'utilisateur, ni une capture de son Mac : ce navigateur appartient à VELKO.
 *  Le moteur ne diffuse des frames que si un écran les regarde — d'où le
 *  « touch » périodique tant que ce panneau est affiché. */
let browserFrame = null;
let touchTimer = 0;
let rightPanel = 'idle';
let rightToken = 0;      // une réponse lente n'écrase jamais un panneau plus récent

function keepStreamAlive(on) {
 clearInterval(touchTimer);
 if (!on) return;
 const touch = () => fetch('/api/browser/touch', {method: 'POST'}).catch(() => {});
 touch();
 touchTimer = setInterval(touch, 1500);
}

/** `src` : soit le base64 brut du flux SSE, soit la data-url du fallback HTTP. */
function showFrame(src, url) {
 if (!src) return;
 // Les frames continuent d'arriver quelques instants après un changement de
 // source : elles ne doivent pas se réinstaller sous un autre panneau.
 if (rightPanel !== 'browser') return;
 if (!browserFrame) {
  browserFrame = document.createElement('img');
  browserFrame.alt = 'Session navigateur réelle pilotée par VELKO';
 }
 browserFrame.src = src.startsWith('data:') ? src : 'data:image/jpeg;base64,' + src;
 if (browserFrame.parentElement !== $('application-body')) rightBody(browserFrame);
 if (url) $('application-detail').textContent = url;
}

async function refreshBrowser() {
 const token = rightToken, wasPanel = rightPanel;
 let d;
 try { d = await (await fetch('/api/browser/status')).json(); }
 catch { setRight('browser', 'État du navigateur indisponible.', '', {badge: 'off'}); return; }
 // Une autre source réelle (Discord, git, SSH) occupe l'écran : on n'y
 // repose pas une frame de navigateur. Le flux d'événements décide, pas
 // l'ordre d'arrivée des réponses HTTP.
 if (rightPanel !== 'browser' && rightPanel !== 'idle') return;
 void token; void wasPanel;
 const s = d.status || d;
 if (!s.available) {
  keepStreamAlive(false);
  setRight('browser', 'Navigateur intégré indisponible : Playwright n’est pas installé.',
           'Installation : ./.venv/bin/python -m playwright install chromium', {badge: 'off'});
  rightBody(null); browserFrame = null; return;
 }
 if (!s.active) {
  keepStreamAlive(false);
  setRight('browser', 'Aucune session navigateur ouverte par VELKO.', '', {badge: 'off'});
  rightBody(null); browserFrame = null; return;
 }
 keepStreamAlive(true);
 if (s.gate) {
  // Authentification / captcha : VELKO attend l'utilisateur, il n'invente rien.
  setRight('browser', 'ACTION REQUISE — ' + (s.gate_message || 'intervention nécessaire sur la page.'),
           s.url || '', {badge: 'off'});
 } else {
  setRight('browser', (s.title || 'Session navigateur') + ' — pilotée par VELKO',
           s.url || '', {badge: 'on'});
 }
 // Frame immédiate le temps que le flux SSE en pousse une nouvelle.
 try {
  const f = await (await fetch('/api/browser/frame')).json();
  showFrame(f.data_url || f.jpeg, s.url);
 } catch { /* pas encore de frame : le flux en fournira une */ }
}

/** Discord RÉEL uniquement.
 *
 *  Aucune interface Discord n'est reconstituée : soit VELKO parle vraiment au
 *  serveur (bot authentifié ou session navigateur discord.com), soit l'écran
 *  affiche « DISCORD — NON CONNECTÉ » et la raison. */
async function refreshDiscord(type = '', data = {}) {
 setRight('discord', 'Discord · lecture de l’état réel…', '');
 const token = rightToken;
 let s = {};
 try { s = await (await fetch('/api/discord/status')).json(); } catch { /* moteur injoignable */ }
 if (rightToken !== token) return;
 const live = Boolean(s.connected ?? s.status?.connected);
 if (!live) {
  setRight('discord', 'DISCORD — NON CONNECTÉ',
           s.error || s.detail || 'Aucune session Discord authentifiée. VELKO ne simule pas cette interface.',
           {badge: 'off'});
  rightBody(null);
  return;
 }
 const who = s.user || s.bot || s.name || 'bot authentifié';
 const guilds = (s.guilds || []).map(g => g.name || g).join(', ');
 setRight('discord', 'Discord · ' + (type ? type.split('.').slice(1).join('.') : 'session active'),
          [who, guilds, data.channel ? 'salon ' + data.channel : ''].filter(Boolean).join(' · '),
          {badge: 'on'});
 // On quitte la vue navigateur : sa frame ne doit pas rester sous un autre titre.
 browserFrame = null;
 keepStreamAlive(false);
 // Seules de vraies données d'API Discord sont rendues ici.
 const payload = data.messages || data.channels || data.preview || data.result || data.detail;
 const pre = document.createElement('pre');
 pre.textContent = payload
  ? (typeof payload === 'string' ? payload : JSON.stringify(payload, null, 1))
  : 'Session Discord active. Les données apparaissent dès que VELKO lit ou écrit réellement.';
 rightBody(pre);
}

async function refreshGit() {
 if (!activePath) { setRight('git', 'Aucun fichier suivi pour le moment.'); return; }
 const r = await fetch('/api/workspace/diff?path=' + encodeURIComponent(activePath));
 const d = await r.json().catch(() => ({}));
 setRight('git', d.repo ? 'Dépôt : ' + d.repo : 'Hors dépôt git.', d.detail || '');
 const pre = document.createElement('pre');
 pre.textContent = d.diff || d.detail || d.error || '';
 rightBody(pre);
}

// ---------------------------------------------------------------------------
// Flux réel
// ---------------------------------------------------------------------------
engineFeed().subscribe(frame => {
 const type = frame.type || '', data = frame.data || {};
 if (type === 'feed.state') {
  $('status').textContent = data.online ? 'Moteur connecté' : 'Flux interrompu — reconnexion';
  return;
 }
 router.ingest(type, data);

 // --- fichiers réels -------------------------------------------------------
 if (type === 'file.opened' || type === 'code.file.active') { openFile(data.path || data.absolute_path); return; }
 if (type === 'file.changed' || type === 'file.created' || type === 'code.patch.applied') {
  openFile(data.path || data.absolute_path, {changed: true});
  $('result').textContent = `${type === 'file.created' ? 'Créé' : 'Modifié'} : ${data.path || ''}`;
  return;
 }
 if (type === 'file.deleted') {
  const path = data.path || '';
  touched.delete(path);
  if (activePath === path) { activePath = ''; codeMessage('Fichier supprimé par VELKO : ' + path); $('filename').textContent = path; }
  $('result').textContent = 'Supprimé : ' + path + (data.trash_path ? ' → ' + data.trash_path : '');
  return;
 }
 if (type === 'code.file.error') { $('saved').textContent = 'Erreur sur ' + (data.filename || data.path || '') + ' : ' + (data.error || ''); return; }

 // --- terminal réel --------------------------------------------------------
 if (type === 'terminal.command') {
  $('terminal-cwd').textContent = data.cwd || '';
  term(`\n[${stamp()}] $ `, 'meta');
  term(data.command || '', 'cmd');
  if (data.cwd) term(`   (${data.cwd})`, 'cwd');
  term('\n');
  return;
 }
 if (type === 'terminal.output') { term(data.text || '', data.stream === 'stderr' ? 'err' : ''); return; }
 if (type === 'terminal.completed') {
  const ok = Number(data.exit_code) === 0;
  term(`[${stamp()}] exit ${data.exit_code}`, ok ? 'exit-ok' : 'exit-ko');
  term(` · ${data.duration_ms || 0} ms\n`, 'meta');
  return;
 }
 if (type === 'terminal.failed') { term(`\n[${stamp()}] terminal en échec : ${data.error || ''}\n`, 'err'); return; }

 // --- processus réels ------------------------------------------------------
 if (type.startsWith('process.')) {
  const what = type.split('.')[1];
  term(`\n[${stamp()}] processus ${what} : ${data.name || data.process_id || ''}\n`, 'meta');
  if (data.output || data.logs) term(String(data.output || data.logs) + '\n');
  return;
 }

 // --- écran droit : sources réelles uniquement -----------------------------
 if (type === 'browser.frame') {            // flux live de la session de VELKO
  if (data.jpeg) showFrame(data.jpeg, data.url);
  return;
 }
 if (type === 'browser.gate') {
  setRight('browser', 'ACTION REQUISE — ' + (data.message || 'intervention nécessaire sur la page.'),
           'VELKO attend votre intervention dans sa session.', {badge: 'off'});
  return;
 }
 if (type === 'browser.error') {
  $('application-detail').textContent = 'Erreur navigateur : ' + (data.message || '');
  return;
 }
 if (type.startsWith('browser.')) { refreshBrowser(); return; }
 if (type.startsWith('git.')) { refreshGit(); return; }
 if (type.startsWith('ssh.')) {
  setRight('ssh', 'Session SSH : ' + (data.host || data.connector_id || 'hôte non nommé'),
           data.command ? '$ ' + data.command : '', {badge: 'on'});
  if (data.output) { const pre = document.createElement('pre'); pre.textContent = data.output; rightBody(pre); }
  return;
 }
 if (type.startsWith('discord.')) { refreshDiscord(type, data); return; }
 if (type.startsWith('connector.')) {
  const id = String(data.connector_id || data.name || '');
  if (id === rightPanel) setRight(rightPanel, id + ' · ' + type.split('.')[1], data.detail || '',
                                  {badge: data.connected ? 'on' : 'off'});
  return;
 }

 // Un appel d'outil nomme lui aussi une source réelle : `tool.started
 // discord.list_channels` doit amener l'écran droit sur Discord, même si
 // l'outil n'émet pas d'événement `discord.*` de son côté.
 if (type === 'tool.started' || type === 'tool.completed') {
  const kind = sourceOf(type, data);
  if (kind === 'discord') { refreshDiscord('', data); return; }
  if (kind === 'browser') { refreshBrowser(); return; }
  if (kind === 'git') { refreshGit(); return; }
 }

 // --- fil de mission -------------------------------------------------------
 if (type === 'velko.task.phase') { $('result').textContent = data.label || data.phase || ''; return; }
 if (type === 'jarvis.activity' || type === 'activity.trace' || type === 'system.warning') {
  const text = data.detail || data.message || data.text;
  if (text) $('result').textContent = text;
 }
});

// État de départ honnête : ce qui est réellement disponible, rien de plus.
if (pane === 'application') {
 setRight('idle', 'Aucune source réelle active. Cet écran suit le navigateur, SSH, git ou Discord de VELKO.');
 refreshBrowser();
}
fetch('/api/code/documents').then(r => r.json()).then(s => {
 const doc = (s.documents || []).find(d => d.document_id === s.activeDocumentId) || (s.documents || [])[0];
 if (doc) openFile(doc.absolute_path || doc.path || doc.filename);
}).catch(() => { /* aucun document ouvert : l'écran reste honnêtement vide */ });
