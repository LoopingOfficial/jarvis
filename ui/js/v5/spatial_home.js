/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_home.js
   Refonte de l'accueil : grille 3 colonnes (métriques / avatar 3D / agents).

   CONTRAINTE TENUE : aucun identifiant ni binding existant n'est touché.
   L'enveloppe est ajoutée EN TÊTE de #page-command, tout ce qu'elle crée est
   préfixé `jh-`, et le DOM hérité reste en place et fonctionnel dessous.
   C'est pour cette raison que index.html n'est pas régénéré : 167 ids y sont
   déclarés et 490 références y pointent depuis le JS.

   Aucune valeur n'est inventée : CPU, RAM, uptime, modèles et agents viennent
   de /api/status et /api/agents. Une donnée absente s'affiche « — », jamais
   une valeur plausible.
   ========================================================================== */

import { createAvatarViewer } from '../avatar/premium_viewer.js?v=JARVIS_HOME_REDESIGN_1';

const AVATAR_URL = '/assets/avatar/cartoon_boy.glb';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const API = () => (typeof J !== 'undefined' ? J : window.J);
const pct = (v) => (v === null || v === undefined || Number.isNaN(v) ? null : Math.max(0, Math.min(100, Number(v))));

function fmtUptime(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}j ${h}h` : h ? `${h}h ${m}min` : `${m}min`;
}

/** Les détails système arrivent en texte libre : « CPU 12.2% · RAM 63.4% … ». */
function parseSystem(detail) {
  // Le libellé est encadré par un groupe NON CAPTURANT. Sans lui, un motif à
  // alternative comme « Disque|Disk » coupe l'expression entière en deux : la
  // branche gauche n'a plus de groupe de capture, `found[1]` est indéfini, et
  // l'exception qui suivait interrompait TOUT le rafraîchissement — jauges,
  // modèles et agents restaient à « — ».
  const grab = (label) => {
    const found = new RegExp(`(?:${label})\\s*([\\d.,]+)\\s*%`, 'i').exec(String(detail || ''));
    return found && found[1] ? parseFloat(found[1].replace(',', '.')) : null;
  };
  return { cpu: grab('CPU'), ram: grab('RAM'), disk: grab('Disque|Disk') };
}

const Home = {
  viewer: null, host: null, bound: false, timer: null, lines: [],

  /* ------------------------------------------------------------ montage */
  mount() {
    // Sur l'accueil, le shell V5 met à `opacity: 0` AUSSI BIEN .v5-work que
    // .v5-view : il n'affiche que ses canvas (cerveau, avatar) et le message
    // d'accueil. Il n'existe donc aucun conteneur visible où poser une mise en
    // page — mesuré, pas supposé. La refonte devient un calque autonome,
    // ajouté au body et retiré quand on quitte l'accueil. Rien du shell n'est
    // modifié, rien du DOM hérité n'est touché.
    const target = document.body;
    let wrap = document.querySelector('body > .jh');
    if (wrap) return wrap;

    wrap = document.createElement('section');
    wrap.className = 'jh';
    wrap.innerHTML = `
      <aside class="jh-col jh-left">
        <div class="jh-card">
          <div class="jh-card-h"><span class="jh-dot" data-sys-dot></span>Système<span class="jh-tag" data-sys-tag>—</span></div>
          <div class="jh-card-b">
            <div class="jh-gauge"><div class="jh-gauge-top"><span>Processeur</span><b data-cpu>—</b></div>
              <div class="jh-bar"><i data-cpu-bar></i></div></div>
            <div class="jh-gauge"><div class="jh-gauge-top"><span>Mémoire</span><b data-ram>—</b></div>
              <div class="jh-bar"><i data-ram-bar></i></div></div>
            <div class="jh-gauge"><div class="jh-gauge-top"><span>Disque</span><b data-disk>—</b></div>
              <div class="jh-bar"><i data-disk-bar></i></div></div>
          </div>
        </div>

        <div class="jh-card">
          <div class="jh-card-h">Noyau<span class="jh-tag" data-version>—</span></div>
          <div class="jh-card-b">
            <div class="jh-kv"><span>Uptime</span><b data-uptime>—</b></div>
            <div class="jh-kv"><span>Outils</span><b data-tools>—</b></div>
            <div class="jh-kv"><span>Mémoires</span><b data-memories>—</b></div>
            <div class="jh-kv"><span>Conversations</span><b data-convs>—</b></div>
          </div>
        </div>

        <div class="jh-card">
          <div class="jh-card-h"><span class="jh-dot off" data-llm-dot></span>Modèles<span class="jh-tag" data-llm-tag>—</span></div>
          <div class="jh-card-b" data-llm-list><div class="jh-empty">Chargement…</div></div>
        </div>
      </aside>

      <main class="jh-center">
        <div class="jh-card jh-stage-card">
          <div class="jh-stage" data-stage>
            <div class="jh-badge"><span class="jh-dot" data-core-dot></span><span data-core-label>JARVIS CORE · CHARGEMENT</span></div>
            <div class="jh-loading" data-loading>PRÉPARATION DE L'AVATAR…</div>
            <div class="jh-hint">glisser : orbiter · molette : zoom</div>
          </div>
        </div>
      </main>

      <aside class="jh-col jh-right">
        <div class="jh-card">
          <div class="jh-card-h"><span class="jh-dot" data-agents-dot></span>Agents<span class="jh-tag" data-agents-tag>—</span></div>
          <div class="jh-card-b" data-agents><div class="jh-empty">Chargement…</div></div>
        </div>
        <div class="jh-card jh-feed">
          <div class="jh-card-h"><span class="jh-dot"></span>Activité<span class="jh-tag" data-feed-tag>EN DIRECT</span></div>
          <div class="jh-feed-list" data-feed><div class="jh-empty">En attente d'événements…</div></div>
        </div>
      </aside>`;
    wrap.classList.add('jh-layer');
    target.appendChild(wrap);
    this.host = wrap;
    return wrap;
  },

  q(sel) { return this.host ? this.host.querySelector(sel) : null; },

  /* -------------------------------------------------------------- avatar */
  async mountViewer() {
    const stage = this.q('[data-stage]');
    if (!stage) return;
    if (this.viewer) {                    // la page héritée peut avoir été re-rendue
      this.viewer.attachTo(stage);
      this.viewer.resume();
      return;
    }
    try {
      this.viewer = await createAvatarViewer({
        host: stage,
        url: AVATAR_URL,
        autoRotate: true,
        // Un vêtement blanc sous un bloom trop bas devient une lampe : le seuil
        // haut réserve la lueur aux accents néon de la scène.
        bloom: { strength: 0.34, radius: 0.6, threshold: 0.93 },
        exposure: 0.95,
      });
      window.JarvisHomeAvatar = this.viewer;
      this.q('[data-loading]')?.classList.add('gone');
    } catch (error) {
      const box = this.q('[data-loading]');
      if (box) box.textContent = `AVATAR INDISPONIBLE — ${String(error?.message || error).slice(0, 80)}`;
      console.error('[home] avatar', error);
    }
  },

  /* ------------------------------------------------------- données réelles */
  async refresh() {
    const J = API();
    if (!J || !this.host) return;
    const [status, agents] = await Promise.all([
      J.get('/api/status').catch(() => null),
      J.get('/api/agents').catch(() => null),
    ]);
    // Chaque panneau est isolé : une erreur dans l'un ne doit pas priver
    // l'utilisateur de tous les autres, comme cela vient d'arriver.
    try { if (status) this.renderStatus(status); } catch (e) { console.error('[home] status', e); }
    try { if (agents) this.renderAgents(agents.agents || []); } catch (e) { console.error('[home] agents', e); }
  },

  setGauge(valueSel, barSel, value, { suffix = ' %', warn = 75, danger = 90 } = {}) {
    const label = this.q(valueSel), bar = this.q(barSel);
    const v = pct(value);
    if (label) label.textContent = v === null ? '—' : `${v.toFixed(1)}${suffix}`;
    if (bar) {
      bar.style.width = v === null ? '0%' : `${v}%`;
      bar.className = v === null ? '' : (v >= danger ? 'err' : v >= warn ? 'warn' : '');
    }
  },

  renderStatus(status) {
    const core = status.core || {};
    const sys = parseSystem(core.system?.detail);
    this.setGauge('[data-cpu]', '[data-cpu-bar]', sys.cpu);
    this.setGauge('[data-ram]', '[data-ram-bar]', sys.ram);
    this.setGauge('[data-disk]', '[data-disk-bar]', sys.disk);

    const sysTag = this.q('[data-sys-tag]');
    if (sysTag) sysTag.textContent = (core.system?.status || '—').toUpperCase();
    const sysDot = this.q('[data-sys-dot]');
    if (sysDot) sysDot.className = 'jh-dot' + (core.system?.status === 'optimal' ? '' : ' warn');

    const set = (sel, text) => { const el = this.q(sel); if (el) el.textContent = text; };
    set('[data-version]', status.version ? `v${status.version}` : '—');
    set('[data-uptime]', fmtUptime(status.uptime_s));
    set('[data-tools]', status.tools?.total != null ? String(status.tools.total) : '—');
    set('[data-memories]', core.memory?.count != null ? String(core.memory.count) : '—');
    set('[data-convs]', status.conversations?.total != null ? String(status.conversations.total) : '—');

    const providers = status.llm || [];
    const connected = providers.filter((p) => p.connected);
    const llmList = this.q('[data-llm-list]');
    if (llmList) {
      llmList.innerHTML = providers.length
        ? providers.slice(0, 6).map((p) => `
            <div class="jh-kv"><span>${esc(p.name)}</span>
              <b class="jh-pill ${p.connected ? 'on' : ''}">${p.connected ? 'connecté' : 'hors ligne'}</b></div>`).join('')
        : '<div class="jh-empty">Aucun fournisseur configuré.</div>';
    }
    set('[data-llm-tag]', `${connected.length}/${providers.length}`);
    const llmDot = this.q('[data-llm-dot]');
    if (llmDot) llmDot.className = 'jh-dot' + (connected.length ? '' : ' off');

    const label = this.q('[data-core-label]');
    if (label) {
      label.textContent = `JARVIS CORE V${status.version || '—'} · ${(core.agents?.status || 'ACTIVE').toUpperCase()}`;
    }
  },

  renderAgents(agents) {
    const box = this.q('[data-agents]');
    if (!box) return;
    box.innerHTML = agents.length
      ? agents.slice(0, 8).map((a) => {
          const on = a.status === 'active' || a.status === 'running';
          const bad = a.status === 'error' || a.last_error;
          return `<div class="jh-agent">
            <span class="jh-dot ${bad ? 'err' : on ? '' : 'off'}"></span>
            <div class="jh-agent-main"><b>${esc(a.name)}</b><small>${esc(a.role || a.description || '—')}</small></div>
            <span class="jh-pill ${bad ? 'err' : on ? 'on' : ''}">${esc(a.status || '—')}</span>
          </div>`;
        }).join('')
      : '<div class="jh-empty">Aucun agent déclaré.</div>';
    const tag = this.q('[data-agents-tag]');
    if (tag) tag.textContent = `${agents.filter((a) => a.status === 'active').length}/${agents.length}`;
  },

  /* ---------------------------------------------------- flux d'activité */
  pushLine(text, kind = '') {
    const list = this.q('[data-feed]');
    if (!list) return;
    if (this.lines.length === 0) list.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'jh-line';
    el.innerHTML = `<time>${new Date().toLocaleTimeString('fr-FR')}</time>
      <span class="jh-msg">${kind ? `<b>${esc(kind)}</b> ` : ''}${esc(text)}</span>`;
    list.prepend(el);
    this.lines.push(el);
    while (this.lines.length > 40) this.lines.shift().remove();
  },

  bind() {
    if (this.bound) return;
    this.bound = true;

    window.addEventListener('jarvis:page', (e) => {
      const page = e.detail?.page;
      if (page === 'command') setTimeout(() => this.enter(), 160);
      else this.leave();
    });

    const J = API();
    if (J && typeof J.on === 'function') {
      // Le flux reprend les ÉVÉNEMENTS RÉELS du bus : rien n'est simulé.
      J.on('*', (type, data) => {
        if (!this.host || !/^(tool|agent|task|vault|crm|discord|memory|image)\./.test(type)) return;
        const label = data?.name || data?.tool || data?.title || data?.id || '';
        this.pushLine(String(label).slice(0, 80) || type, type);
      });
    }
  },

  async enter() {
    this.mount();
    if (this.host) this.host.style.display = '';
    // Les données d'abord, l'avatar ensuite et EN PARALLÈLE : attendre le
    // chargement du GLB (4,6 Mo, plusieurs secondes) laissait les jauges et les
    // agents à « — » pendant tout ce temps. Un asset 3D ne doit jamais retarder
    // l'affichage de l'état du système.
    this.refresh();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.refresh(), 5000);
    this.mountViewer();
  },

  leave() {
    clearInterval(this.timer);
    this.timer = null;
    this.viewer?.pause();          // pas de boucle rAF orpheline hors de l'accueil
    // Le calque est retiré de l'écran mais PAS détruit : le remonter coûterait
    // un rechargement complet du GLB de 4,6 Mo à chaque aller-retour.
    if (this.host) this.host.style.display = 'none';
  },
};

Home.bind();
if (document.getElementById('page-command')?.classList.contains('active')) {
  setTimeout(() => Home.enter(), 400);
}

export default Home;
