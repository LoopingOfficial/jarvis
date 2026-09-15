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

import { createAvatarViewer } from '../avatar/premium_viewer.js?v=JARVIS_HOLO_12';
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
      <div class="jh-inner">
        <div class="jh-stage" data-stage>
          <div class="jh-loading" data-loading></div>
        </div>

        <h1 class="jh-hello" data-hello>Bonsoir.</h1>
        <p class="jh-sub" data-sub>Connexion aux systèmes…</p>

        <div class="jh-suggest" data-suggest></div>

        <div class="jh-strip">
          <span class="jh-chip"><span class="jh-dot" data-sys-dot></span><b data-sys>—</b></span>
          <span class="jh-chip">Processeur <b data-cpu>—</b></span>
          <span class="jh-chip">Mémoire <b data-ram>—</b></span>
          <span class="jh-chip">Modèles <b data-llm-tag>—</b></span>
          <span class="jh-chip">Outils <b data-tools>—</b></span>
          <span class="jh-chip">Agents <b data-agents-tag>—</b></span>
          <span class="jh-chip jh-chip-mute" data-version>—</span>
        </div>

        <div class="jh-activity" data-activity hidden>
          <span class="jh-dot"></span><span data-activity-text></span>
        </div>
      </div>`;
    wrap.classList.add('jh-layer');
    target.appendChild(wrap);
    this.host = wrap;
    return wrap;
  },

  q(sel) { return this.host ? this.host.querySelector(sel) : null; },

  /* Suggestions : elles ne « font » rien toutes seules, elles PRÉPARENT la
     demande dans la vraie barre de commande (#convInput) et lui donnent le
     focus. L'utilisateur garde la main sur l'envoi — proposer n'est pas agir. */
  SUGGESTIONS: [
    { t: 'Résumer mes derniers mails', p: 'Résume mes mails non lus et classe-les par urgence.' },
    { t: 'Analyser un document', p: "Analyse le document que je vais te joindre et donne-moi l'essentiel." },
    { t: 'Chercher sur le web', p: 'Cherche sur le web : ', send: false },
    { t: 'Faire le point', p: 'Fais le point sur mes tâches en cours et ce qui bloque.' },
  ],

  renderSuggestions() {
    const box = this.q('[data-suggest]');
    if (!box || box.dataset.ready) return;
    box.dataset.ready = '1';
    box.innerHTML = this.SUGGESTIONS.map((s, i) =>
      `<button class="jh-sugg" type="button" data-i="${i}">${esc(s.t)}</button>`).join('');
    box.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-i]');
      if (!btn) return;
      const input = document.getElementById('convInput');
      if (!input) return;
      const suggestion = this.SUGGESTIONS[Number(btn.dataset.i)];
      input.value = suggestion.p;
      input.focus();
      input.dispatchEvent(new Event('input', { bubbles: true }));   // laisse l'app réagir
      try { input.setSelectionRange(input.value.length, input.value.length); } catch (_) {}

      // Les suggestions à compléter (« Cherche sur le web : ») attendent la
      // suite : on prépare et on rend la main. Les autres sont des demandes
      // complètes — on les ENVOIE. Se contenter de remplir un champ que
      // l'utilisateur ne regarde pas donne l'impression d'un bouton mort.
      if (suggestion.send === false) return;
      const form = document.getElementById('convForm');
      if (!form) return;
      // requestSubmit() passe par la validation et les écouteurs du formulaire,
      // contrairement à form.submit() qui les court-circuite.
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
      else form.querySelector('button[type=submit]')?.click();
    });
  },

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
      // Plus aucun GLB à charger : la tête est construite en code, donc elle
      // apparaît en une frame au lieu des 4,6 Mo qu'il fallait télécharger.
      // Un vrai maillage de tête, pas une sphère sculptée : une tête humaine
      // ne s'obtient pas en déplaçant les sommets d'une sphère avec quelques
      // fonctions d'atténuation. Le voile holographique (Fresnel sur la
      // silhouette, balayage en espace monde, scintillement) est injecté dans
      // les matériaux du modèle.
      this.viewer = await createAvatarViewer({
        host: stage,
        url: '/assets/avatar/cartoon_boy.glb',
        hologram: true,
        portrait: true,              // plan tête-épaules
        autoRotate: false,
        platform: false,             // l'anneau est dessiné en CSS sous la scène
        bloom: { strength: 0.42, radius: 0.72, threshold: 0.45 },
        exposure: 0.85,
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
    const set = (sel, text) => { const el = this.q(sel); if (el) el.textContent = text; };

    set('[data-cpu]', sys.cpu === null ? '—' : `${Math.round(sys.cpu)} %`);
    set('[data-ram]', sys.ram === null ? '—' : `${Math.round(sys.ram)} %`);
    set('[data-tools]', status.tools?.total != null ? String(status.tools.total) : '—');
    set('[data-version]', status.version ? `v${status.version}` : '—');

    const health = core.system?.status || '';
    set('[data-sys]', health === 'optimal' ? 'Systèmes opérationnels'
      : health ? `Systèmes · ${health}` : 'État inconnu');
    const dot = this.q('[data-sys-dot]');
    if (dot) dot.className = 'jh-dot' + (health === 'optimal' ? '' : health ? ' warn' : ' off');

    const providers = status.llm || [];
    const connected = providers.filter((p) => p.connected);
    set('[data-llm-tag]', providers.length ? `${connected.length}/${providers.length}` : '—');

    // Salutation : l'heure et le prénom viennent du backend, jamais d'un défaut
    // inventé. Sans nom connu, on salue sans nommer.
    const hour = new Date().getHours();
    const moment = hour < 6 ? 'Bonne nuit' : hour < 18 ? 'Bonjour' : 'Bonsoir';
    const name = String(status.user_name || '').trim();
    set('[data-hello]', name ? `${moment}, ${name}.` : `${moment}.`);

    const assistant = String(status.assistant_name || 'JARVIS').trim();
    const bits = [];
    if (connected.length) bits.push(`${connected[0].name} connecté`);
    if (status.tools?.total) bits.push(`${status.tools.total} outils`);
    set('[data-sub]', bits.length
      ? `${assistant} est prêt · ${bits.join(' · ')}`
      : `${assistant} démarre…`);
  },

  renderAgents(agents) {
    const actifs = agents.filter((a) => a.status === 'active' || a.status === 'running').length;
    const tag = this.q('[data-agents-tag]');
    if (tag) tag.textContent = agents.length ? `${actifs}/${agents.length}` : '—';
  },

  /* ---------------------------------------------------- flux d'activité */
  /* Une page d'assistant n'a pas à dérouler un journal : on annonce ce qui se
     passe MAINTENANT, en une ligne, et la ligne disparaît quand c'est fini. */
  pushLine(text, kind = '') {
    const box = this.q('[data-activity]');
    const label = this.q('[data-activity-text]');
    if (!box || !label) return;
    label.textContent = String(text || kind).slice(0, 90);
    box.hidden = false;
    clearTimeout(this._activityTimer);
    this._activityTimer = setTimeout(() => { box.hidden = true; }, 6000);
  },

  bind() {
    if (this.bound) return;
    this.bound = true;

    window.addEventListener('jarvis:page', (e) => {
      const page = e.detail?.page;
      if (page === 'command') setTimeout(() => this.enter(), 160);
      else this.leave();
    });

    // L'accueil ne suivait que la PAGE. Or le shell change de CONTEXTE sans
    // changer de page : ouvrir une conversation fait passer data-context de
    // « home » à « chat » et allume #v5Chat, qui est un calque plein écran
    // au-dessus de l'accueil. Les deux se peignaient donc ensemble — le fil de
    // discussion tombait en plein sur l'avatar, sur la salutation et sur les
    // suggestions. On s'accroche à l'attribut que setContext() écrit déjà,
    // plutôt que d'ajouter un événement au shell : l'accueil s'efface dès que
    // la conversation prend la main et revient quand elle la rend.
    const syncContext = () => {
      const ctx = document.documentElement.getAttribute('data-context') || 'home';
      if (ctx === 'home') { if (this.host?.style.display === 'none' || !this.host) this.enter(); }
      else if (this.host && this.host.style.display !== 'none') this.leave();
    };
    new MutationObserver(syncContext).observe(document.documentElement,
      { attributes: true, attributeFilter: ['data-context'] });

    const J = API();
    if (J && typeof J.on === 'function') {
      // ---- La parole. La bouche articule le texte RÉELLEMENT prononcé.
      J.on('tts.started', (d) => this.viewer?.speak(d?.text || '', Number(d?.duration) || 0));
      J.on('tts.boundary', (d) => this.viewer?.resyncSpeech(Number(d?.charIndex) || 0, Number(d?.total) || 0));
      J.on('tts.completed', () => this.viewer?.stopSpeaking());
      // ---- Les états. Chacun correspond à une situation vraie, pas à une
      //      humeur décidée au hasard : le micro écoute, le modèle réfléchit,
      //      un agent travaille.
      J.on('voice.listening', () => this.viewer?.setState('LISTENING'));
      J.on('voice.stopped', () => this.viewer?.setState('IDLE'));
      J.on('chat.thinking', () => this.viewer?.setState('THINKING'));
      J.on('agent.started', () => this.viewer?.setState('WORKING'));
      J.on('agent.completed', () => this.viewer?.setState('IDLE'));

      // Le flux reprend les ÉVÉNEMENTS RÉELS du bus : rien n'est simulé.
      J.on('*', (type, data) => {
        if (!this.host || !/^(tool|agent|task|vault|crm|discord|memory|image)\./.test(type)) return;
        // Le personnage réagit quand JARVIS agit vraiment : un geste par
        // rafale d'événements, pas un par événement — sinon il gesticule en
        // permanence dès qu'un agent envoie sa progression.
        const now = Date.now();
        if (now - (this._lastGesture || 0) > 6000) {
          this._lastGesture = now;
          this.viewer?.gesture();
        }
        const label = data?.name || data?.tool || data?.title || data?.id || '';
        this.pushLine(String(label).slice(0, 80) || type, type);
      });
    }
  },

  async enter() {
    this.mount();
    if (this.host) this.host.style.display = '';
    // Le calque recouvre tout le décor du shell : le laisser peindre ses
    // étoiles, ses traînées, le cerveau et l'ancien avatar coûtait 4,8 Mpx par
    // frame pour des pixels que personne ne voit.
    window.JarvisSpatial?.setQuiet?.(true);
    // Les données d'abord, l'avatar ensuite et EN PARALLÈLE : attendre le
    // chargement du GLB (4,6 Mo, plusieurs secondes) laissait les jauges et les
    // agents à « — » pendant tout ce temps. Un asset 3D ne doit jamais retarder
    // l'affichage de l'état du système.
    this.renderSuggestions();
    this.refresh();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.refresh(), 5000);
    this.mountViewer();
  },

  leave() {
    window.JarvisSpatial?.setQuiet?.(false);
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
  // Le délai fixe de 400 ms laissait l'accueil du shell V5 (salutation, décor,
  // cerveau) seul à l'écran le temps qu'il s'écoule : on voyait défiler un
  // accueil précédent avant celui-ci. On attend l'état RÉEL du shell
  // (data-v5-ready), pas une durée supposée, avec une sortie de secours si le
  // shell échoue — dans ce cas l'accueil se monte quand même.
  const shellReady = () => document.documentElement.getAttribute('data-v5-ready') === '1';
  // Une conversation restaurée place le shell en contexte « chat » dès le
  // démarrage : monter l'accueil visible par-dessus reproduirait exactement la
  // superposition qu'on corrige.
  const enterIfHome = () => {
    if ((document.documentElement.getAttribute('data-context') || 'home') === 'home') Home.enter();
  };
  if (shellReady()) enterIfHome();
  else {
    const started = Date.now();
    const wait = setInterval(() => {
      if (shellReady() || Date.now() - started > 3000) { clearInterval(wait); enterIfHome(); }
    }, 32);
  }
}

export default Home;
