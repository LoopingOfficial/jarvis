/** Panneau de résultat premium de VELKO.
 *
 *  Après une mission, un rapport UNIQUE et structuré : titre, résumé, KPI,
 *  points à retenir, puis les actions réelles du moteur. Le Markdown brut ne
 *  touche jamais la scène. Chaque proposition exécutable devient une Action
 *  Card [Oui, fais-le] / [Non] / [Détails] qui pilote le VRAI outil. La voix
 *  et le clic sont strictement équivalents : le panneau expose une API de
 *  décision consommée aussi bien par les boutons que par les intents vocaux.
 */
import {renderMarkdown, parseMarkdown, stripMarkdown} from './markdown.js';

/** Actions réelles proposables. La détection se fait sur des phrases écrites
 *  dans le rapport (le moteur ne nomme pas toujours le tool_id), dans l'ordre
 *  d'apparition — la numération orale suit donc le texte lu. */
const ACTIONS = [
 {tool: 'brainrot.email.prepare_campaign',
  needles: ['prepare_campaign', 'préparer une campagne', 'préparer la campagne', 'campagne de relance', 'relance email', 'relance emails', 'campagne email'],
  risk: 'high', confirm: true, note: 'Envoi d’email : nécessite votre accord explicite.'},
 {tool: 'brainrot.analytics.registrations',
  needles: ['registrations', 'baisse des inscriptions', 'chute des inscriptions', 'origine des inscriptions', 'taux d’inscription'],
  risk: 'low', confirm: false, note: 'Lecture seule.'},
 {tool: 'brainrot.analytics.activity',
  needles: ['activity', 'taux d’activité', 'taux de participation', 'faible engagement', 'analyse de conversion', 'analyse de la baisse', 'analyser l’activité', 'analyse d’activité'],
  risk: 'low', confirm: false, note: 'Lecture seule.'},
 {tool: 'brainrot.blog.create_draft',
  needles: ['create_draft', 'préparer un brouillon', 'chercher un sujet', 'contenu blog', 'relancer le blog', 'brouillon pour'],
  risk: 'low', confirm: false, note: 'Brouillon : aucune publication.'},
 {tool: 'brainrot.brainrots.sync_sheet',
  needles: ['sync_sheet', 'synchroniser le catalogue', 'mettre à jour le catalogue', 'écrire dans le catalogue'],
  risk: 'medium', confirm: true, note: 'Écriture dans le catalogue Sheets : accord requis.'},
 {tool: 'brainrot.brainrots.diff_sheet',
  needles: ['diff_sheet', 'comparer le sheet', 'différence avec le sheet', 'comparaison avec le catalogue'],
  risk: 'low', confirm: false, note: 'Lecture seule : aucune écriture.'},
];

export class VelkoResultPanel {
 constructor(bus, missionClient) {
  this.bus = bus; this.missionClient = missionClient;
  this.node = document.getElementById('result-panel');
  this.open = false; this.contexts = []; this.focus = 0; this.currentMessage = '';
  this.awaiting = false; this.currentTaskId = null;   // rapport technique : id réel
  this.lastTools = new Set();                         // outils réels de la mission
  // Les outils réellement appelés sont retenus pour le rapport technique.
  bus.on('tool.real', ({name}) => { if (name) this.lastTools.add(name); });
  bus.on('task.result', ev => this.show(ev));
 }

 /** Prévisualise pendant la mission : l'avancement réel, pas un bloc brut. */
 progress(text) {
  if (!this.node || this.open) return;
  this.node.classList.add('visible', 'live');
  this.node.querySelector('.rp-head span').textContent = 'TRAVAIL EN COURS';
  this.body().replaceChildren();
  const p = document.createElement('p'); p.className = 'rp-preview'; p.textContent = text || '';
  this.body().append(p);
 }

 show({result, title, status = 'completed'}) {
  if (!this.node) return;
  this.open = true; this.awaiting = false;
  this.node.classList.add('visible');
  this.node.classList.toggle('live', status !== 'completed');
  const statusText = {completed: 'MISSION TERMINÉE', failed: 'ÉCHEC DU MOTEUR', blocked: 'ACTION BLOQUÉE'}[status] || 'MISSION TERMINÉE';
  this.node.querySelector('.rp-head span').textContent = statusText;
  const doc = parseMarkdown(result || title || '');
  this.currentMessage = result || title || '';

  const body = this.body(); body.replaceChildren();
  const panel = document.createElement('div'); panel.className = 'rp-report';

  if (doc.title) { const t = document.createElement('h3'); t.textContent = doc.title; t.className = 'rp-title'; panel.append(t); }
  if (doc.summary) { const s = document.createElement('p'); s.className = 'rp-summary'; s.textContent = doc.summary; panel.append(s); }

  if (doc.kpis.length) {
   const grid = document.createElement('div'); grid.className = 'rp-kpis';
   for (const k of doc.kpis.slice(0, 5)) {
    const cell = document.createElement('div'); cell.className = 'rp-kpi';
    const v = document.createElement('b'); v.textContent = k.value;
    const l = document.createElement('span'); l.textContent = k.label;
    cell.append(v, l); grid.append(cell);
   }
   panel.append(grid);
  }

  // Points à retenir : les observations réelles du rapport.
  if (doc.observations.length || doc.sections.length) {
   const h = document.createElement('h4'); h.textContent = 'POINTS À RETENIR'; panel.append(h);
   const list = document.createElement('ul'); list.className = 'rp-list';
   // Hors section, parseMarkdown range des objets {type, item|text} : on
   // n'en garde que le texte.
   const asText = i => typeof i === 'string' ? i : (i && (i.item ?? i.text)) || '';
   const items = (doc.observations.length
    ? doc.observations.map(asText)
    : doc.sections.flatMap(s => (s.items || []).filter(i => i.type === 'list').map(i => i.item))
   ).filter(Boolean);
   for (const item of items.slice(0, 6)) {
    const li = document.createElement('li'); li.textContent = item.replace(/^•\s*/, '').replace(/\*\*(.+?)\*\*/g, '$1'); list.append(li);
   }
   if (list.children.length) panel.append(list);
  }

  // Actions réelles : si le moteur en propose plusieurs, VELKO demande par
  // laquelle commencer — par la voix ET par le clic, de façon équivalente.
  const contexts = this.buildContexts(result || '', doc);
  this.contexts = contexts; this.focus = Math.min(this.focus, Math.max(0, contexts.length - 1));
  if (contexts.length) {
   const h = document.createElement('h4'); h.textContent = 'ACTIONS PROPOSÉES'; panel.append(h);
   const cards = document.createElement('div'); cards.className = 'rp-cards';
   contexts.forEach((ctx, i) => cards.append(this.card(ctx, i)));
   panel.append(cards);
   this.emitPending(this.contexts[this.focus]);
  }

  // Rapport technique : secondaire, plié jusqu'à la demande explicite.
  const toggle = document.createElement('button'); toggle.className = 'rp-toggle'; toggle.textContent = 'Voir le rapport complet';
  const full = document.createElement('div'); full.className = 'rp-full'; full.hidden = true;
  full.innerHTML = renderMarkdown(result || '');
  const tech = document.createElement('div'); tech.className = 'rp-tech';
  if (this.currentTaskId) {
   const p = document.createElement('p'); p.textContent = 'Identifiant de tâche : ' + this.currentTaskId; tech.append(p);
  }
  const sources = this.trackedSources();
  if (sources) { const p = document.createElement('p'); p.textContent = 'Sources réelles : ' + sources; tech.append(p); }
  toggle.onclick = () => { const v = full.hidden = !full.hidden; toggle.textContent = v ? 'Voir le rapport complet' : 'Réduire le rapport'; if (v) tech.hidden = true; else tech.hidden = false; };
  panel.append(toggle, tech, full);

  body.append(panel);
 }

 /** Tableau des Context Action Pending, calculé depuis le rapport réel.
  *  Les actions sont numérotées dans l'ordre où LE RAPPORT les dit, pour que
  *  la voix (« la première », « la deuxième ») reflète le texte affiché. */
 buildContexts(result, doc) {
  const found = [];
  const hay = (result || '').toLowerCase();
  const hits = ACTIONS.map(a => {
   const needle = a.needles.find(n => hay.includes(n));
   return needle ? {action: a, at: hay.indexOf(needle)} : null;
  }).filter(Boolean).sort((x, y) => x.at - y.at);
  for (const {action} of hits) {
   const opp = (doc.actions || []).find(txt => txt && txt.toLowerCase().includes(action.needles[0].split('_')[0])) || null;
   found.push({
    action_id: action.tool, label: opp ? opp.replace(/^[-\s•]*/, '').split(' —')[0] : labelFor(action.tool),
    tool: action.tool, arguments: argumentsFor(action.tool),
    confirmation_required: action.confirm, risk: action.risk, note: action.note,
    timestamp: Date.now(), focus_index: found.length, approved: null,
   });
  }
  return found;
 }

 /** Carte d'action : boutons réels + Détails. Le numéro aide la voix. */
 card(ctx, i) {
  const wrap = document.createElement('div'); wrap.className = 'rp-card' + (i === this.focus ? ' focused' : '');
  wrap.dataset.index = i;
  const head = document.createElement('div'); head.className = 'rp-card-head';
  const label = document.createElement('span'); label.className = 'rp-card-label';
  label.textContent = `${i + 1}. ${ctx.label}`;
  if (ctx.risk === 'high' || ctx.confirmation_required) { const badge = document.createElement('em'); badge.className = 'rp-risk'; badge.textContent = 'Confirmation requise'; label.append(badge); }
  head.append(label);
  const buttons = document.createElement('div'); buttons.className = 'rp-card-buttons';
  const yes = document.createElement('button'); yes.className = 'rp-action rp-yes'; yes.textContent = 'Oui, fais-le';
  const no = document.createElement('button'); no.className = 'rp-action rp-no'; no.textContent = 'Non';
  const det = document.createElement('button'); det.className = 'rp-action rp-details'; det.textContent = 'Détails';
  buttons.append(yes, no, det);
  wrap.append(head, buttons);
  const detail = document.createElement('div'); detail.className = 'rp-card-detail'; detail.hidden = true;
  const dnote = document.createElement('p');
  dnote.textContent = [ctx.note, `Outil réel : ${ctx.tool}`, `Niveau de risque : ${ctx.risk}`].filter(Boolean).join(' — ');
  detail.append(dnote);
  wrap.append(detail);
  det.onclick = () => this.focusCard(i) && this.toggleDetails(i);
  yes.onclick = () => this.choose(i, true);
  no.onclick = () => this.choose(i, false);
  return wrap;
 }

 focusCard(i) {
  if (i < 0 || i >= this.contexts.length) return false;
  this.focus = i;
  this.refreshFocus();
  return true;
 }

 refreshFocus() {
  const cards = this.node?.querySelectorAll('.rp-card');
  cards?.forEach((c, k) => c.classList.toggle('focused', k === this.focus));
 }

 /** Passe au contexte suivant/précédent (flèches, « la deuxième »…) */
 focusNext() { if (this.contexts.length > 1) { this.focus = (this.focus + 1) % this.contexts.length; this.refreshFocus(); this.emitPending(this.contexts[this.focus]); } }
 focusPrev() { if (this.contexts.length > 1) { this.focus = (this.focus - 1 + this.contexts.length) % this.contexts.length; this.refreshFocus(); this.emitPending(this.contexts[this.focus]); } }

 toggleDetails(i = this.focus) {
  const c = this.node?.querySelector('.rp-card[data-index="' + i + '"]');
  if (c) { const d = c.querySelector('.rp-card-detail'); if (d) d.hidden = !d.hidden; }
 }

 /** Décision unifiée clic ET voix. `approved` peut être un booléen ou «details». */
 async choose(i = this.focus, approved = true) {
  const ctx = this.contexts[i];
  if (!ctx) return;
  if (approved === 'details') { this.toggleDetails(i); this.emitPending(ctx); return; }
  ctx.approved = approved;
  const card = this.node?.querySelector('.rp-card[data-index="' + i + '"]');
  const btn = card?.querySelector('.rp-yes');
  if (approved && ctx.confirmation_required) {
   // Garde-fou : confirmation explicite, par la voix ou le clic.
   this.awaiting = true;
   this.bus.emit('action.await', {...ctx, message: `« ${ctx.label} » demande votre accord avant d’être lancée.`});
   if (btn) { btn.textContent = 'Confirmez…'; btn.classList.add('armed'); }
   return;
  }
  if (!approved) {
   if (btn) { btn.textContent = 'Annulée'; btn.classList.add('declined'); }
   if (card) card.classList.add('declined');
   this.bus.emit('action.decided', {action_id: ctx.action_id, approved: false});
   setTimeout(() => { btn.textContent = 'Oui, fais-le'; btn.classList.remove('declined'); card?.classList.remove('declined'); }, 2500);
   // Le refus passe à la carte suivante ; s'il ne reste plus rien à décider,
   // le panneau se vide et l'écoute vocale s'arrête.
   const next = this.contexts.findIndex((c, k) => k !== i && c && c.approved === null && k > i);
   const after = next >= 0 ? next : this.contexts.findIndex((c, k) => k !== i && c && c.approved === null);
   if (after >= 0) { this.focus = after; this.refreshFocus(); this.emitPending(this.contexts[after]); }
   else {
    this.hide();
    this.contexts.length = 0; this.awaiting = false;
    this.bus.emit('action.allDeclined', {taskId: this.currentTaskId});
   }
   return;
  }
  this.launch(ctx, btn);
 }

 launch(ctx, btn) {
  this.awaiting = false;
  ctx.approved = true;
  if (btn) { btn.disabled = true; btn.textContent = 'Lancement…'; }
  this.bus.emit('action.selected', {...ctx, approved: true});
  this.runAction(ctx.tool, btn);
 }

 async runAction(tool, btn) {
  try {
   const args = argumentsFor(tool);
   const data = await this.missionClient.request('/api/tools/' + tool + '/run', {arguments: args, confirmed: true});
   if (data.needs_confirmation) {
    this.bus.emit('mission.confirmation', {id: data.needs_confirmation.id, action: tool, reason: data.needs_confirmation.reason, message: data.message, speech: data.needs_confirmation.speech});
   }
   btn.textContent = 'Lancé sur le moteur — voir l’écran';
   btn.classList.add('done');
   setTimeout(() => { btn.disabled = false; btn.classList.remove('done'); btn.textContent = 'Oui, fais-le'; }, 4000);
  } catch (error) {
   btn.textContent = 'Échec : ' + error.message; btn.disabled = false;
  }
 }

 /** Confirme explicitement une action verrouillée par un garde-fou (voix/clic). */
 confirmFocused() {
  const ctx = this.contexts[this.focus];
  if (ctx && ctx.approved === true && this.awaiting) {
   this.awaiting = false;
   const card = this.node?.querySelector('.rp-card[data-index="' + this.focus + '"]');
   this.launch(ctx, card?.querySelector('.rp-yes'));
   return true;
  }
  return false;
 }

 /** Intents vocaux : « oui / non / montre-moi d'abord / la première / la deuxième ».
  *  Retourne la phrase de confirmation à dire à haute voix, ou null. */
 voice(text = '') {
  const t = String(text).toLowerCase().replace(/[.!?,]/g, ' ');
  if (!this.contexts.length) {
   // Aucune action proposée : la voix n'invente pas d'action à exécuter.
   return null;
  }
  let index = this.focus;
  if (/deuxi[èe]me|la seconde/.test(t)) index = Math.min(1, this.contexts.length - 1);
  else if (/premi[èe]re/.test(t)) index = 0;
  else if (/troisi[èe]me/.test(t)) index = Math.min(2, this.contexts.length - 1);
  if (/premi[èe]re|deuxi[èe]me|troisi[èe]me/.test(t)) this.focus = index;
  this.refreshFocus();
  const ctx = this.contexts[index] || this.contexts[this.focus];
  if (/montre.*d'abord|détails|détail|explique/.test(t)) { this.toggleDetails(index); return `Voici le détail de « ${ctx.label} ».`; }
  if (/non|annule|non merci|arrête/.test(t)) { this.choose(index, false); return `D'accord, je ne lance pas « ${ctx.label} ».`; }
  if (/oui|bien|vas-y|fais|lance|d'accord|ok|valide|confirme/.test(t)) {
   this.choose(index, true);
   return ctx.confirmation_required ? `« ${ctx.label} » demande votre confirmation. Je l'exécute ?` : `Très bien, je lance « ${ctx.label} ».`;
  }
  return null;
 }

 /** L'état vocal de VELKO : un contexte prioritaire existe-t-il toujours ? */
 pendingContext() { return this.contexts.length ? this.contexts[this.focus] : null; }

 emitPending(ctx) {
  if (!ctx) return;
  this.currentTaskId = this.currentTaskId || null;
  this.bus.emit('action.pending', {...ctx, taskId: this.currentTaskId, message: this.plainAsk(ctx)});
 }

 /** Phrase « naturelle » posée oralement quand VELKO propose plusieurs actions. */
 plainAsk(ctx) {
  if (this.contexts.length > 1) return `Par quoi souhaitez-vous commencer ? Vous pouvez me dire « oui », « non », ou « ${ordinal(this.focus + 1)} » : ${this.contexts.map((c, i) => ordinal(i + 1) + ' : ' + stripMarkdown(c.label)).join(' ; ')}.`;
  return `Proposez-vous que j'exécute : ${stripMarkdown(ctx.label)} ? Dites « oui » ou « non ».`;
 }

 trackedSources() { return this.lastTools.size ? [...this.lastTools].join(', ') : null; }
 hide() { if (this.node) { this.node.classList.remove('visible', 'live'); this.open = false; this.awaiting = false; } }
 body() {
  let b = this.node.querySelector('.rp-body');
  if (!b) { b = document.createElement('div'); b.className = 'rp-body'; this.node.append(b); }
  return b;
 }
}

const ORDINALS = ['première', 'deuxième', 'troisième', 'quatrième'];
function ordinal(i) { return ORDINALS[i - 1] || (i + 'e'); }

function labelFor(tool) {
 const map = {
  'brainrot.email.prepare_campaign': 'Préparer l’email de relance',
  'brainrot.analytics.registrations': 'Analyser les inscriptions',
  'brainrot.analytics.activity': 'Analyser l’activité',
  'brainrot.blog.create_draft': 'Préparer un brouillon d’article',
  'brainrot.brainrots.sync_sheet': 'Synchroniser le catalogue',
  'brainrot.brainrots.diff_sheet': 'Comparer le Sheet',
 };
 return map[tool] || tool.split('.').pop().replace(/_/g, ' ');
}
function argumentsFor(tool) {
 const map = {
  'brainrot.analytics.registrations': {days: 30},
  'brainrot.analytics.activity': {days: 30},
 };
 return map[tool] || {};
}