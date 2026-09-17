/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_neural_graph.js
   Vue « Réseau neuronal » montée en tête de la page AI Core, selon le même
   esprit que spatial_modules.js : une enveloppe V5 est posée au-dessus du DOM
   hérité. À la différence des vues migrées, la page n'est PAS marquée
   `v5-migrated` — les cartes MODÈLES / SÉCURITÉ / ENVIRONNEMENT restent
   visibles sous le graphe, qui les complète au lieu de les remplacer.

   Aucune donnée n'est inventée : les nœuds viennent de /api/status (modèles),
   /api/connectors, /api/tools et /api/agents. Si ces appels échouent ou ne
   renvoient rien, on bascule sur le jeu d'exemple et on l'ANNONCE dans le
   bandeau (badge EXEMPLE) — jamais de fausse télémétrie présentée comme réelle.

   Les impulsions lumineuses suivent les événements réels du bus (tool.*,
   agent.*, connector.*) : un nœud ne s'allume que si quelque chose s'est
   effectivement passé.
   ========================================================================== */

import { createNeuralGraph } from './neural_graph_engine.js';
import { CLUSTERS, NODES as SAMPLE_NODES, LINKS as SAMPLE_LINKS } from './neural_graph_data.js';

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const slug = (v) => String(v ?? '').toLowerCase().normalize('NFD')
  .replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'x';
const API = () => (typeof J !== 'undefined' ? J : window.J);
/** Les détails de statut du backend peuvent faire plusieurs lignes (traces d'erreur). */
const trim = (v, n = 140) => {
  const s = String(v ?? '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
};
function fmtUptime(s) {
  if (!s && s !== 0) return '—';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}j ${h}h` : h ? `${h}h ${m}min` : `${m}min`;
}

/* ======================================================================
   1. Adaptation des données réelles -> modèle du graphe
   ====================================================================== */

/** Classement d'un connecteur/outil réel dans une famille du graphe. */
function clusterOf(name, category) {
  const n = (slug(name) + ' ' + slug(category)).replace(/_/g, ' ');
  if (/linkedin|twitter| x |youtube|instagram|tiktok|facebook|social|reseau/.test(' ' + n + ' ')) return 'social';
  if (/stripe|paypal|qonto|bank|banque|invoice|factur|devis|compta|finance|payment/.test(n)) return 'finance';
  return 'tool';
}

const HUBS = {
  brain:   { id: 'hub_brain',   label: 'CERVEAUX LLM',   desc: 'Routeur de modèles : choisit le cerveau selon le coût, la latence et le type de tâche.' },
  tool:    { id: 'hub_tool',    label: 'CAPACITÉS',      desc: 'Registre des outils et connecteurs déclarés au planificateur.' },
  social:  { id: 'hub_social',  label: 'RÉSEAUX',        desc: 'Comptes sociaux connectés : diffusion et veille.' },
  finance: { id: 'hub_finance', label: 'FINANCE',        desc: 'Encaissements, trésorerie et facturation.' },
  agent:   { id: 'hub_agent',   label: 'AGENTS MÉTIERS', desc: 'Agents autonomes déclarés par le backend.' },
};

/** Construit nœuds + liens depuis les API réelles. Renvoie null si vide. */
async function buildLiveData() {
  const J = API();
  if (!J || typeof J.get !== 'function') return null;

  const [status, connectors, tools, agents] = await Promise.all([
    J.get('/api/status').catch(() => null),
    J.get('/api/connectors').catch(() => null),
    J.get('/api/tools').catch(() => null),
    J.get('/api/agents').catch(() => null),
  ]);

  const providers = (status?.llm || []).filter((p) => p && p.name);
  const conns = (connectors?.connectors || []).filter((c) => c && (c.name || c.id));
  const toolList = (tools?.tools || []).filter((t) => t && t.name);
  const agentList = (agents?.agents || []).filter((a) => a && a.name);

  // Sans modèle ni agent ni connecteur, il n'y a rien de réel à montrer.
  if (!providers.length && !conns.length && !agentList.length) return null;

  // L'agent « jarvis » EST le noyau : on le fusionne au lieu d'afficher deux fois JARVIS.
  const coreAgent = agentList.find((a) => slug(a.id) === 'jarvis');
  const core = status?.core || {};
  const nodes = [{
    id: 'jarvis', label: 'NOYAU JARVIS', cluster: 'core', val: 4.2, hub: true,
    desc: coreAgent?.role
      ? `${coreAgent.role} : routage des intentions, mémoire et supervision des agents.`
      : 'Orchestrateur central : routage des intentions, mémoire et supervision des agents.',
    meta: {
      // Uniquement des champs réellement renvoyés par /api/status.
      Version: status?.version || '—',
      Uptime: fmtUptime(status?.uptime_s),
      Statut: coreAgent?.status || core.agents?.status || '—',
      Exécutions: coreAgent?.runs != null ? String(coreAgent.runs) : '—',
      Système: core.system?.detail || '—',
      Modèles: core.llms?.detail || String(providers.length),
    },
  }];
  const links = [];
  const used = new Set(['jarvis']);
  const addNode = (n) => { if (used.has(n.id)) return false; used.add(n.id); nodes.push(n); return true; };
  const hubFor = (cluster) => {
    const h = HUBS[cluster];
    if (addNode({ id: h.id, label: h.label, cluster, val: 2.8, hub: true, desc: h.desc, meta: {} })) {
      links.push([ 'jarvis', h.id, 3 ]);
    }
    return h.id;
  };

  /* --- modèles ------------------------------------------------------- */
  const providerSlugs = new Set();
  providers.forEach((p) => {
    const id = 'llm_' + slug(p.id || p.name);
    providerSlugs.add(slug(p.name));
    if (!addNode({
      id, label: p.name, cluster: 'brain', val: p.connected ? 1.9 : 1.3,
      desc: p.detail || 'Fournisseur de modèles déclaré dans les paramètres.',
      meta: {
        Statut: p.connected ? 'Connecté' : 'Non connecté',
        'Modèle par défaut': p.default_model || '—',
        Modèles: p.models ? String(p.models.length) : '—',
        Outils: p.supports_tools ? 'oui' : 'non',
      },
    })) return;
    links.push([hubFor('brain'), id, p.connected ? 3 : 1]);
  });

  /* --- connecteurs (capacités, réseaux, finance) --------------------- */
  conns.forEach((c) => {
    // Un connecteur « provider_of: llm » est déjà représenté par son modèle.
    if (c.provider_of === 'llm' && providerSlugs.has(slug(c.name))) return;
    const cluster = clusterOf(c.name || c.id, c.category || c.type);
    const id = 'conn_' + slug(c.id || c.name);
    const ok = c.status === 'connected';
    if (!addNode({
      id, label: c.name || c.id, cluster, val: ok ? 1.7 : 1.2,
      desc: trim(c.status_detail) || c.label || `Connecteur ${c.type || ''}`.trim(),
      meta: {
        Statut: ok ? 'Connecté' : (c.status || '—'),
        Catégorie: c.category || '—',
        Type: c.type || '—',
        Permissions: (c.permissions || []).join(', ') || '—',
      },
    })) return;
    links.push([hubFor(cluster), id, ok ? 3 : 1]);
  });

  /* --- catégories d'outils : une seule entrée par catégorie ---------- */
  const byCat = {};
  toolList.forEach((t) => { (byCat[t.category || 'Divers'] = byCat[t.category || 'Divers'] || []).push(t); });
  Object.entries(byCat).forEach(([cat, list]) => {
    const id = 'cat_' + slug(cat);
    // `status` n'est pas toujours renvoyé : on retombe sur `enabled`.
    const ready = list.filter((t) => (t.status ? t.status === 'ready' : t.enabled !== false)).length;
    if (!addNode({
      id, label: cat, cluster: clusterOf(cat, cat), val: 1.4,
      desc: `Catégorie d'outils exposée au planificateur (${list.length} outils).`,
      meta: { Outils: String(list.length), Actifs: String(ready) },
    })) return;
    links.push([hubFor('tool'), id, ready ? 2 : 1]);
  });

  /* --- agents (le noyau a déjà absorbé l'agent « jarvis ») ----------- */
  agentList.filter((a) => a !== coreAgent).forEach((a) => {
    const id = 'agent_' + slug(a.id || a.name);
    if (!addNode({
      id, label: a.name, cluster: 'agent', val: 2.0,
      desc: a.role || a.description || 'Agent déclaré par le backend.',
      meta: {
        Rôle: a.role || '—',
        Statut: a.status || '—',
        Exécutions: a.runs != null ? String(a.runs) : '—',
        Modèle: a.model || a.model_role || '—',
        ...(a.current_action ? { 'En cours': trim(a.current_action, 60) } : {}),
        ...(a.last_error ? { Erreur: trim(a.last_error, 60) } : {}),
      },
    })) return;
    links.push([hubFor('agent'), id, a.status === 'active' ? 3 : 2]);

    // Rattachement au modèle seulement si le backend nomme un vrai fournisseur
    // (model_role vaut souvent un alias de rôle comme « default » : on ignore).
    const model = slug(a.model || '');
    if (model) {
      const target = nodes.find((n) => n.cluster === 'brain' && (n.id === 'llm_' + model || slug(n.label) === model));
      if (target) links.push([id, target.id, 2]);
    }
  });

  return { clusters: CLUSTERS, nodes, links };
}

/* ======================================================================
   2. Montage de la vue
   ====================================================================== */

const View = {
  graph: null, host: null, info: null, live: false, bound: false,

  /** Enveloppe V5 posée en tête de page, au-dessus du DOM hérité d'AI Core. */
  mountHost() {
    const page = document.getElementById('page-aicore');
    if (!page) return null;
    let host = page.querySelector(':scope > .v5-neural');
    if (!host) {
      host = document.createElement('div');
      host.className = 'v5-neural';
      host.innerHTML = `
        <div class="v5-neural-stage"></div>
        <div class="v5-neural-top">
          <span class="v5-neural-title">RÉSEAU NEURONAL</span>
          <span class="v5-neural-src">—</span>
          <span class="v5-neural-legend"></span>
        </div>
        <div class="v5-neural-info"></div>
        <div class="v5-neural-foot">
          <input class="v5-neural-search" placeholder="Filtrer un nœud…" spellcheck="false" />
          <span class="v5-neural-hint">clic : focus · glisser un nœud : déplacer · Échap : vue globale</span>
        </div>`;
      page.prepend(host);
    }
    // Volontairement PAS de classe `v5-migrated` : celle-ci masque tout le DOM
    // hérité de la page (spatial_phase2.css). Ici la vue COMPLÈTE AI Core — les
    // cartes MODÈLES / SÉCURITÉ / ENVIRONNEMENT restent lisibles sous le graphe.
    page.classList.remove('v5-migrated');
    return host;
  },

  async render() {
    const wrap = this.mountHost();
    if (!wrap) return;
    const stage = wrap.querySelector('.v5-neural-stage');
    this.info = wrap.querySelector('.v5-neural-info');

    // Pages.aicore réécrit tout le innerHTML de la page à chaque navigation :
    // notre enveloppe (canvas compris) peut avoir été emportée. On réinstalle
    // le canvas existant plutôt que d'ouvrir un second contexte WebGL.
    if (this.graph) {
      this.graph.attachTo(stage);
      this.wireHud(wrap);
      this.graph.resume();
      this.showInfo(this.graph.selected);
      return;
    }

    let data = null;
    try { data = await buildLiveData(); } catch { data = null; }
    this.live = !!data;
    if (!data) data = { clusters: CLUSTERS, nodes: SAMPLE_NODES, links: SAMPLE_LINKS };

    this.graph = createNeuralGraph({
      host: stage,
      data,
      onSelect: (n) => this.showInfo(n),
    });

    this.wireHud(wrap);
    window.JarvisNeuralGraph = this.graph;      // accès depuis la console
  },

  /** (Re)câble bandeau, légende et recherche — l'enveloppe peut être neuve. */
  wireHud(wrap) {
    if (wrap.dataset.wired === '1') return;
    wrap.dataset.wired = '1';

    const badge = wrap.querySelector('.v5-neural-src');
    const count = this.graph.nodes.length;
    badge.textContent = this.live ? `${count} nœuds · données réelles` : `${count} nœuds · exemple`;
    badge.classList.toggle('sample', !this.live);
    badge.title = this.live
      ? 'Construit depuis /api/status, /api/connectors, /api/tools et /api/agents.'
      : "Les API n'ont rien renvoyé : jeu d'exemple affiché.";

    const box = wrap.querySelector('.v5-neural-legend');
    const present = new Set(this.graph.nodes.map((n) => n.cluster));
    box.innerHTML = Object.entries(this.graph.clusters)
      .filter(([key]) => present.has(key) && key !== 'core')
      .map(([key, c]) => `<button class="v5-neural-chip${this.graph.isClusterVisible(key) ? '' : ' off'}"
        data-cluster="${key}" type="button">
        <i style="background:${c.css};box-shadow:0 0 8px ${c.css}"></i>${esc(c.name)}</button>`).join('');
    box.querySelectorAll('[data-cluster]').forEach((b) => {
      b.addEventListener('click', () => {
        const key = b.dataset.cluster;
        const next = !this.graph.isClusterVisible(key);
        this.graph.setClusterVisible(key, next);
        b.classList.toggle('off', !next);
      });
    });

    wrap.querySelector('.v5-neural-search')
      .addEventListener('input', (e) => this.graph.setQuery(e.target.value));
  },

  showInfo(n) {
    if (!this.info) return;
    if (!n) { this.info.classList.remove('on'); this.info.innerHTML = ''; return; }
    const c = this.graph.clusters[n.cluster];
    const rel = [...n.neighbors].map((id) => this.graph.byId.get(id)).filter(Boolean);
    this.info.innerHTML = `
      <h3 style="color:${c.css};text-shadow:0 0 16px ${c.css}55">${esc(n.label)}</h3>
      <div class="sub">${esc(c.name)} · ${esc(n.id)}</div>
      ${n.desc ? `<p>${esc(n.desc)}</p>` : ''}
      <div class="v5-neural-kv">${Object.entries(n.meta || {})
        .map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>
      <div class="sub">Connexions (${rel.length})</div>
      <div class="v5-neural-rel">${rel.map((m) =>
        `<button type="button" data-goto="${esc(m.id)}">${esc(m.label)}</button>`).join('')}</div>`;
    this.info.classList.add('on');
    this.info.querySelectorAll('[data-goto]').forEach((b) => {
      b.addEventListener('click', () => this.graph.select(b.dataset.goto));
    });
  },

  /** Retrouve le nœud correspondant à un nom d'outil / connecteur / agent réel. */
  resolve(name) {
    if (!this.graph || !name) return null;
    const s = slug(name);
    if (!s) return null;
    for (const pre of ['agent_', 'conn_', 'llm_', 'cat_', '']) {
      const n = this.graph.byId.get(pre + s);
      if (n) return n;
    }
    return this.graph.nodes.find((n) => slug(n.label) === s)
        || this.graph.nodes.find((n) => s.includes(slug(n.label)) || slug(n.label).includes(s))
        || null;
  },

  /** Impulsion sur événement RÉEL uniquement (jamais de pulsation décorative). */
  pulse(name) {
    const n = this.resolve(name);
    if (n) this.graph.pulse(n.id);
  },

  bind() {
    if (this.bound) return;
    this.bound = true;

    // Échap ne revient à la vue globale que s'il y a bien une sélection à
    // annuler : sinon on laisserait le shell gérer sa propre touche Échap.
    window.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !this.graph || !this.graph.selected) return;
      if (!document.getElementById('page-aicore')?.classList.contains('active')) return;
      this.graph.home();
    });

    window.addEventListener('jarvis:page', (e) => {
      const page = e.detail?.page;
      if (page === 'aicore') setTimeout(() => this.render(), 160);   // laisse la page héritée finir
      else if (this.graph) this.graph.pause();                        // pas de rAF orphelin
    });

    const J = API();
    if (!J || typeof J.on !== 'function') return;
    J.on('*', (type, d) => {
      if (!this.graph) return;
      if (/^tool\./.test(type)) this.pulse(d?.name || d?.tool);
      else if (/^agent\./.test(type)) this.pulse(d?.id || d?.name || d?.agent);
      else if (/^connector\./.test(type)) this.pulse(d?.id || d?.connector || d?.name);
      else if (/^(memory|brain)\./.test(type)) this.graph.pulse('jarvis');
    });
  },
};

View.bind();
// La page AI Core peut déjà être affichée au chargement du module.
if (document.getElementById('page-aicore')?.classList.contains('active')
  || (typeof J !== 'undefined' && J.state?.page === 'aicore')) {
  setTimeout(() => View.render(), 300);
}

export default View;
