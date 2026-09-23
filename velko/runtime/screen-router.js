/** Routeur des trois moniteurs de VELKO.
 *
 *  Nourri par les événements RÉELS du moteur : chaque famille de faits a un
 *  écran (gauche = code/fichiers, centre = terminal/analyse, droite = outil
 *  actif complémentaire). Il décide aussi de l'écran actif (activeMonitor) pour
 *  le regard, et RETIENT le dernier contenu réel de chaque famille pour ne
 *  jamais effacer un écran entre deux micro-événements.
 *
 *  Contract : une source n'existe que si un fait moteur réel l'a donnée ;
 *  aucune source n'est devinée à partir du texte de la demande. */

/** Panneau à afficher pour chaque famille d'événements réels. */
const SOURCES = [
 [/^file\.|^code\.(file|patch)\./, 'code'],
 [/^terminal\./,                   'terminal'],
 [/^process\./,                    'process'],
 [/^browser\./,                    'browser'],
 [/^ssh\./,                        'ssh'],
 [/^git\./,                        'git'],
 [/^discord\./,                    'discord'],
 // Google Sheets : analyse et comparaison du catalogue (pipeline réel).
 [/^google\.|^sheet\.progress/,     'sheet'],
 // Brainrot Fortnite : analytics, base de données, blog, email.
 [/^db\./,                         'database'],
];

/** Un outil nomme sa famille quand l'événement est générique (tool.started). */
const TOOLS = [
 [/^fs\.|^code\./,                 'code'],
 [/^terminal\.|^test\./,           'terminal'],
 [/^process\./,                    'process'],
 [/^browser\.|^web\./,             'browser'],
 [/^ssh\.|^deploy\./,              'ssh'],
 [/^git\.|^github\./,              'git'],
 [/^discord\./,                    'discord'],
 [/^google\.|sheets|^sheet\./,     'sheet'],
 // Familles métier Brainrot : l'outil est assez parlant pour nommer l'écran.
 [/^brainrot\.brainrots\./,        'sheet'],
 [/^brainrot\.analytics\.|^brainrot\.(users|registrations|activity|emails)/, 'analytics'],
 [/^brainrot\.blog\./,             'blog'],
 [/^brainrot\.email\./,            'email'],
 [/^db\./,                         'database'],
];

export const PANEL_LABELS = {
 code: 'Code · fichiers réels',
 terminal: 'Terminal · processus réels',
 process: 'Processus · sortie réelle',
 browser: 'Navigateur · session VELKO',
 ssh: 'SSH · session distante',
 git: 'Git · état réel du dépôt',
 discord: 'Discord · session VELKO',
 sheet: 'Google Sheets · données réelles',
 analytics: 'Analytics · statistiques réelles',
 blog: 'Blog · article réel',
 email: 'Email · campagnes réelles',
 database: 'Base de données · requêtes réelles',
 idle: 'Aucune source active',
};

export function sourceOf(type = '', data = {}) {
 for (const [re, kind] of SOURCES) if (re.test(type)) return kind;
 if (type === 'tool.started' || type === 'tool.called' || type === 'tool.completed' || type === 'tool.failed') {
  const name = String(data.tool || data.tool_id || data.name || '');
  for (const [re, kind] of TOOLS) if (re.test(name)) return kind;
 }
 return '';
}

export class VelkoScreenRouter {
 constructor() {
  // gauche et centre ont un rôle fixe : le code et le terminal/analyse sont les
  // surfaces que VELKO utilise dans presque toutes ses missions.
  this.panels = ['code', 'terminal', 'idle'];
  this.lastSeen = {};          // famille -> horodatage du dernier fait réel
  this.contents = {};          // famille -> dernier contenu réel (jamais effacé)
  this.visualActivity = '';    // famille technique en cours (analytics, sheet…)
  this.activeTool = '';        // dernier outil réellement appelé
  this.activeResource = '';    // dernière ressource réelle (path, sheet, id…)
  this.taskContext = '';       // dernière phase de mission réelle
  this.focus = 1;              // moniteur actuellement regardé (0/1/2)
 }

 /** Rappelle l'activité visuelle courante (famille technique) et la ressource. */
 setActivity(visual = {}) {
  const family = visual.activityFamily || this.visualActivity;
  if (family) this.visualActivity = family;
  if (visual.tool) this.activeTool = String(visual.tool);
  if (visual.resource) this.activeResource = String(visual.resource);
 }

 /** Absorbe un événement réel et renvoie l'affectation des trois écrans. */
 ingest(type, data = {}) {
  const kind = sourceOf(type, data);
  if (!kind) return this.state();
  this.lastSeen[kind] = Date.now();
  // Le contenu réel d'une source récente est retenu : un écran ne se vide jamais
  // entre deux événements de la même famille.
  if (data && (Object.keys(data).length || true)) this.contents[kind] = { type, data, at: Date.now() };
  if (type === 'velko.task.phase') this.taskContext = String(data.label || data.phase || this.taskContext);

  if (kind === 'code') this.focus = 0;
  else if (kind === 'terminal' || kind === 'process' ||
           kind === 'analytics' || kind === 'database') { this.focus = 1; this.panels[1] = kind; }
  else {
   // Toute autre source réelle (navigateur, SSH, git, Discord, Sheets, blog,
   // email) prend l'écran de droite : l'écran « outil actif complémentaire ».
   this.panels[2] = kind;
   this.focus = 2;
  }
  return this.state();
 }

 /** Écran (0/1/2) qui doit montrer cette famille, ou -1 si aucune. */
 screenFor(kind) {
  const index = this.panels.indexOf(kind);
  if (index >= 0) return index;
  if (kind === 'process' || kind === 'analytics' || kind === 'database') return 1;
  return -1;
 }

 /** Moniteur actif pour le regard (0/1/2), ou -1 si aucune famille active. */
 activeMonitor() {
  if (this.panels[2] !== 'idle') return 2;
  return this.focus;
 }

 state() {
  return {
   panels: [...this.panels],
   labels: this.panels.map(p => PANEL_LABELS[p] || p),
   focus: this.focus,
   activeMonitor: this.activeMonitor(),
   activeTool: this.activeTool,
   activeResource: this.activeResource,
   visualActivity: this.visualActivity,
   taskContext: this.taskContext,
   contents: { ...this.contents },
   seen: { ...this.lastSeen },
  };
 }
}