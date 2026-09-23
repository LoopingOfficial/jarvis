/** Couche d'activité visuelle de VELKO.
 *
 *  Elle transforme le flux technique du moteur (db.query × 8, terminal.output,
 *  tool.failed…) en une activité HUMAINE lisible à l'écran : « Analyse des
 *  membres », « Lecture du Google Sheet », « Exécution des tests ».
 *
 *  Contrat strict : rien n'est inventé. Une activité n'existe que si un fait
 *  moteur réel l'a déclenchée ; un rafale de faits identiques est GROUPÉE (une
 *  seule affiche commentée), jamais démultipliée. La durée peut être étirée
 *  pour être lisible, mais aucune action fantôme n'est produite. */

/** États visuels. Chaque état est une activité humaine observable. */
export const VISUAL_STATES = {
  ANALYSING_DATA:  { label: 'Analyse des données',         input: 'READING', screen: 1 },
  READING_FILE:    { label: 'Lecture de fichiers',         input: 'READING', screen: 0 },
  EDITING_CODE:    { label: 'Écriture du code',            input: 'KEYBOARD', screen: 0 },
  WRITING_CONTENT: { label: 'Rédaction de contenu',        input: 'KEYBOARD', screen: 0 },
  RUNNING_COMMAND: { label: 'Exécution d’une commande',    input: 'KEYBOARD', screen: 1 },
  READING_TERMINAL:{ label: 'Lecture de la sortie',        input: 'READING', screen: 1 },
  WAITING_PROCESS: { label: 'Attente du processus',        input: 'NONE', screen: 1 },
  BROWSING:        { label: 'Navigation sur le site',      input: 'MOUSE', screen: 2 },
  USING_DISCORD:   { label: 'Discord',                     input: 'MOUSE', screen: 2 },
  COMPARING_SHEET: { label: 'Comparaison du catalogue',    input: 'READING', screen: 1 },
  REVIEWING_DIFF:  { label: 'Vérification des changements',input: 'READING', screen: 0 },
  TESTING:         { label: 'Exécution des tests',         input: 'READING', screen: 1 },
  ERROR_ANALYSIS:  { label: 'Analyse d’une erreur',        input: 'READING', screen: 1 },
  SUCCESS_REVIEW:  { label: 'Vérification des résultats',  input: 'READING', screen: 1 },
};

/** Famille technique -> activité visuelle. Priorise le fait le plus précis. */
const EVENT_BY_TYPE = [
  [/^terminal\.command$/, 'RUNNING_COMMAND'],
  [/^terminal\.(output|completed)/, 'READING_TERMINAL'],
  [/^terminal\.failed/, 'ERROR_ANALYSIS'],
  [/^process\./, 'WAITING_PROCESS'],
  [/^test\./, 'TESTING'],
  [/^browser\./, 'BROWSING'],
  [/^discord\./, 'USING_DISCORD'],
  [/^google\.|^sheet\.progress$/,'COMPARING_SHEET'],
  [/^git\./, 'REVIEWING_DIFF'],
  [/^ssh\.(command|run)/, 'RUNNING_COMMAND'],
  [/^ssh\./, 'READING_TERMINAL'],
  [/^file\.opened|^code\.file\.active/, 'READING_FILE'],
  [/^file\.(changed|created)|^code.patch.applied|^code\.file\.(modified|saving|saved)/,'EDITING_CODE'],
  [/^file\.deleted|^code\.file\.(closed|error)/,'READING_FILE'],
];

/** Famille d'outil -> activité visuelle (pour tool.started/called/completed). */
const TOOL_BY_NAME = [
  [/brainrot\.brainrots\.diff_sheet|brainrot\.brainrots\.sync_sheet/, 'COMPARING_SHEET'],
  [/brainrot\.blog\./, 'WRITING_CONTENT'],
  [/brainrot\.email\./, 'WRITING_CONTENT'],
  [/db\.query|brainrot\.analytics\.|analytics/, 'ANALYSING_DATA'],
  [/fs\.read|file\.read|read_file|ssh\.read_file/, 'READING_FILE'],
  [/fs\.write|fs\.edit|file\.write|code\.(edit|write)|ssh\.write_file/, 'EDITING_CODE'],
  [/terminal|shell|bash|test\.run|pytest|npm|pip|command|exec/, 'RUNNING_COMMAND'],
  [/browser|navigat|search|http|fetch|web/, 'BROWSING'],
  [/discord/, 'USING_DISCORD'],
  [/git\./, 'REVIEWING_DIFF'],
];

/** Geste représentatif de l'activité : utilisé uniquement à titre de défaut
 *  quand aucun geste plus précis n'accompagne le fait réel. */
export const VISUAL_GESTURE = {
  ANALYSING_DATA:   'ReadScreen',
  READING_FILE:     'ReadScreen',
  EDITING_CODE:     'TypingNormal',
  WRITING_CONTENT:   'TypingSlow',
  RUNNING_COMMAND:   'PressEnter',
  READING_TERMINAL: 'ReadScreen',
  WAITING_PROCESS:  'ReadScreen',
  BROWSING:         'MouseClick',
  USING_DISCORD:    'MouseClick',
  COMPARING_SHEET:  'ReadScreen',
  REVIEWING_DIFF:   'ReadScreen',
  TESTING:          'ReadScreen',
  ERROR_ANALYSIS:   'ReadScreen',
  SUCCESS_REVIEW:   'ReadScreen',
};

export function activityOf(type = '', data = {}) {
  for (const [re, state] of EVENT_BY_TYPE) if (re.test(type)) return state;
  if (type === 'tool.started' || type === 'tool.called' || type === 'tool.completed' || type === 'tool.failed') {
    const name = String(data.tool || data.tool_id || data.name || '');
    for (const [re, state] of TOOL_BY_NAME) if (re.test(name)) return state;
    return 'READING_FILE';
  }
  if (type === 'task.testing' || type === 'task.retrying' || type === 'task.blocked') return 'TESTING';
  if (type === 'task.waiting_tool' || type === 'task.waiting_user' || type === 'task.waiting_confirmation') return 'WAITING_PROCESS';
  if (type === 'task.completed') return 'SUCCESS_REVIEW';
  if (type === 'task.failed') return 'ERROR_ANALYSIS';
  return 'READING_TERMINAL';
}

/** Regroupe une rafale de faits identiques en UNE affiche humaine. */
export class VelkoVisualActivityManager {
  constructor() {
    this.state = 'READING_TERMINAL';
    this.since = 0;
    this.count = 0;
    this.path = '';
    this.tool = '';
  }

  /** Consomme un fait moteur et renvoie l'activité corrigée (ou null si déjà
   *  dans le même état visuel : la rafale est absorbée, pas rejouée). */
  consume(type = '', data = {}) {
    const next = activityOf(type, data);
    const path = String(data.path || data.absolute_path || '');
    const tool = String(data.tool || data.tool_id || data.name || '');
    if (next === this.state) {
      this.count++;
      if (path) this.path = path;
      if (tool) this.tool = tool;
      return null;
    }
    this.state = next;
    this.count = 1;
    this.path = path;
    this.tool = tool;
    return this.current();
  }

  current() {
    return {
      state: this.state,
      ...VISUAL_STATES[this.state],
      label: VISUAL_STATES[this.state].label,
      gesture: VISUAL_GESTURE[this.state],
      count: this.count,
      path: this.path,
      tool: this.tool,
    };
  }
}

/** Planificateur de séquences visuelles.
 *
 *  Le moteur peut émettre dix micro-activités dans une rafale de 300 ms
 *  (ex. huit `db.query` identiques, trois `code.patch.applied` sur le même
 *  fichier). Le planificateur n'émet qu'un changement quand l'activité
 *  CHANGE réellement (après la fenêtre de regroupement) et jure que la durée
 *  minimale d'affichage est respectée — sans jamais créer d'activité. */
export class VisualActionScheduler {
  constructor({ groupWindow = 320, minDisplay = 650 } = {}) {
    this.groupWindow = groupWindow;
    this.minDisplay = minDisplay;
    this.current = null;   // {visual, at, emitted}
  }

  /** Retourne l'activité à AFFICHER maintenant, ou null (rafale absorbée). */
  push(visual) {
    if (!visual) return null;
    const now = performance.now();
    if (this.current && visual.state === this.current.visual.state && now - this.current.at < this.groupWindow) {
      this.current.at = now;
      this.current.visual.count = (this.current.visual.count || 1) + (visual.count || 1);
      if (visual.path) this.current.visual.path = visual.path;
      if (visual.tool) this.current.visual.tool = visual.tool;
      return this.current.emitted ? null : this.current.visual;
    }
    // Une activité identique qui arrive APRÈS la fenêtre est une nouvelle
    // opération de même nature : elle n'est montrée que si la précédente a
    // eu le temps d'être lisible.
    const instant = now - (this.current?.at || 0);
    if (this.current && visual.state === this.current.visual.state && instant < this.minDisplay && this.current.emitted) return null;
    this.current = { visual: { ...visual }, at: now, emitted: true };
    return this.current.visual;
  }

  /** Force la libération de l'activité en cours (fin de mission). */
  release() {
    const out = this.current?.visual || null;
    this.current = null;
    return out;
  }
}