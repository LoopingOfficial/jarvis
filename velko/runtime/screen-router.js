/** VelkoScreenRouter — décide SEUL ce que montre chaque moniteur.
 *
 *  L'utilisateur ne choisit jamais de fenêtre : il n'existe ni sélecteur, ni
 *  capture d'écran système, ni partage manuel. Les moniteurs appartiennent à
 *  VELKO et suivent ses propres actions.
 *
 *  La décision vient EXCLUSIVEMENT des événements réels du moteur. Tant qu'un
 *  événement n'a pas nommé une source, l'écran annonce honnêtement qu'il n'a
 *  rien à montrer — il n'invente jamais de contenu de remplissage.
 *
 *  Écrans : 0 = gauche, 1 = centre, 2 = droite.
 */

/** Panneau à afficher pour chaque famille d'événements réels. */
const SOURCES = [
 [/^file\.|^code\.(file|patch)\./, 'code'],
 [/^terminal\./,                   'terminal'],
 [/^process\./,                    'process'],
 [/^browser\./,                    'browser'],
 [/^ssh\./,                        'ssh'],
 [/^git\./,                        'git'],
 [/^discord\./,                    'discord'],
];

/** Un outil nomme sa famille quand l'événement est générique (tool.started). */
const TOOLS = [
 [/^fs\.|^code\./,        'code'],
 [/^terminal\.|^test\./,  'terminal'],
 [/^process\./,           'process'],
 [/^browser\.|^web\./,    'browser'],
 [/^ssh\.|^deploy\./,     'ssh'],
 [/^git\.|^github\./,     'git'],
 [/^discord\./,           'discord'],
];

export const PANEL_LABELS = {
 code: 'Code · fichiers réels',
 terminal: 'Terminal · processus réels',
 process: 'Processus · sortie réelle',
 browser: 'Navigateur · session VELKO',
 ssh: 'SSH · session distante',
 git: 'Git · état réel du dépôt',
 discord: 'Discord · session VELKO',
 idle: 'Aucune source active',
};

export function sourceOf(type = '', data = {}) {
 for (const [re, kind] of SOURCES) if (re.test(type)) return kind;
 if (type === 'tool.started' || type === 'tool.called' || type === 'tool.completed') {
  const name = String(data.tool || data.name || data.tool_id || '');
  for (const [re, kind] of TOOLS) if (re.test(name)) return kind;
 }
 return '';
}

export class VelkoScreenRouter {
 constructor() {
  // gauche et centre ont un rôle fixe : le code et le terminal sont les deux
  // surfaces que VELKO utilise dans presque toutes ses missions.
  this.panels = ['code', 'terminal', 'idle'];
  this.lastSeen = {};       // famille -> horodatage du dernier fait réel
  this.focus = 1;
 }

 /** Absorbe un événement réel et renvoie l'affectation des trois écrans. */
 ingest(type, data = {}) {
  const kind = sourceOf(type, data);
  if (!kind) return this.state();
  this.lastSeen[kind] = Date.now();

  if (kind === 'code') this.focus = 0;
  else if (kind === 'terminal' || kind === 'process') this.focus = 1;
  else {
   // Toute autre source réelle (navigateur, SSH, git, Discord) prend l'écran
   // de droite : c'est l'écran « outil actif complémentaire ».
   this.panels[2] = kind;
   this.focus = 2;
  }
  // Un vrai processus long occupe le centre sans déloger le terminal : les
  // deux partagent la même surface de sortie.
  return this.state();
 }

 /** Écran (0/1/2) qui doit montrer cette famille, ou -1 si aucune. */
 screenFor(kind) {
  const index = this.panels.indexOf(kind);
  if (index >= 0) return index;
  if (kind === 'process') return 1;
  return -1;
 }

 state() {
  return {
   panels: [...this.panels],
   labels: this.panels.map(p => PANEL_LABELS[p] || p),
   focus: this.focus,
   seen: { ...this.lastSeen },
  };
 }
}
