/* ==========================================================================
   JARVIS — holo_visemes.js
   Traduction d'un texte français en suite de VISÈMES, c'est-à-dire les formes
   que prend une bouche pendant qu'elle parle.

   POURQUOI PAS UNE ONDULATION. L'API Web Speech ne donne accès ni au signal
   audio ni aux phonèmes : `tts.audio_level` n'est jamais émis sur ce chemin
   (vérifié — seul avatar_bridge l'écoute, et rien ne le déclenche). Faire
   onduler la mâchoire au hasard pendant que JARVIS parle, c'est inventer une
   donnée. Le texte réellement prononcé, lui, est connu : `tts.started` le
   transporte. On articule donc CE texte.

   Ce que le module produit est une estimation assumée, pas une mesure : la
   durée de chaque visème vient d'un modèle de débit, recalé en continu sur les
   évènements `onboundary` du moteur vocal (voir resync dans holo_viewer).

   Sept formes suffisent à lire une bouche. Au-delà, l'œil ne distingue plus.
   ========================================================================== */

/** Les sept formes. `open` = ouverture mâchoire, `wide` = étirement des
 *  commissures, `round` = projection des lèvres, `press` = lèvres pressées. */
export const VISEMES = {
  SIL: { open: 0.00, wide: 0.00, round: 0.00, press: 0.00 },  // silence
  AA:  { open: 1.00, wide: 0.22, round: 0.00, press: 0.00 },  // a, â
  EE:  { open: 0.34, wide: 1.00, round: 0.00, press: 0.00 },  // i, é, è
  OO:  { open: 0.52, wide: 0.00, round: 1.00, press: 0.00 },  // o, ou, u
  MM:  { open: 0.02, wide: 0.10, round: 0.12, press: 1.00 },  // m, b, p
  FF:  { open: 0.16, wide: 0.42, round: 0.00, press: 0.62 },  // f, v
  CC:  { open: 0.30, wide: 0.55, round: 0.10, press: 0.10 },  // consonnes
};

/* Le français s'écrit rarement comme il se prononce. Ces digrammes sont traités
   AVANT les lettres seules, sinon « ou » sortirait en O puis U et « on » en O
   puis N — deux mouvements de bouche là où il n'y en a qu'un. */
const DIGRAPHS = [
  ['eau', 'OO'], ['oin', 'OO'], ['ain', 'EE'], ['ein', 'EE'],
  ['ou', 'OO'], ['on', 'OO'], ['om', 'OO'], ['au', 'OO'], ['eu', 'OO'],
  ['oi', 'OO'], ['un', 'OO'],
  ['an', 'AA'], ['am', 'AA'], ['en', 'AA'], ['em', 'AA'], ['ai', 'EE'],
  ['in', 'EE'], ['im', 'EE'], ['ei', 'EE'],
  ['ch', 'CC'], ['ph', 'FF'], ['qu', 'CC'], ['gn', 'CC'], ['th', 'CC'],
];

const LETTERS = {
  a: 'AA', à: 'AA', â: 'AA',
  e: 'EE', é: 'EE', è: 'EE', ê: 'EE', ë: 'EE', i: 'EE', î: 'EE', ï: 'EE', y: 'EE',
  o: 'OO', ô: 'OO', u: 'OO', ù: 'OO', û: 'OO', w: 'OO',
  m: 'MM', b: 'MM', p: 'MM',
  f: 'FF', v: 'FF',
};

/* Une voyelle tient la bouche ouverte plus longtemps qu'une consonne : sans
   cette différence, l'articulation part en mitraillette régulière. */
const WEIGHT = { SIL: 1.4, AA: 1.25, EE: 1.1, OO: 1.2, MM: 0.7, FF: 0.8, CC: 0.75 };

/** Découpe un mot en visèmes, digrammes d'abord. */
function wordToVisemes(word) {
  const out = [];
  let i = 0;
  while (i < word.length) {
    const pair = DIGRAPHS.find(([seq]) => word.startsWith(seq, i));
    if (pair) { out.push(pair[1]); i += pair[0].length; continue; }
    const ch = word[i];
    // Une lettre doublée ne se prononce qu'une fois (« elle », « pomme »).
    if (out.length && word[i - 1] === ch) { i += 1; continue; }
    if (/[a-zà-öø-ÿ]/.test(ch)) out.push(LETTERS[ch] || 'CC');
    i += 1;
  }
  return out;
}

/**
 * Construit la partition de la bouche.
 *
 * @param {string} text      le texte RÉELLEMENT prononcé
 * @param {number} duration  durée estimée de l'énoncé, en secondes
 * @returns {{at:number, until:number, viseme:string, shape:object}[]}
 *          suite ordonnée, `at` et `until` en secondes depuis le début.
 */
export function buildVisemeTrack(text, duration) {
  const clean = String(text || '').toLowerCase().trim();
  if (!clean) return [];

  const units = [];
  for (const token of clean.split(/(\s+|[,.;:!?…]+)/)) {
    if (!token) continue;
    if (/^[,.;:!?…]+$/.test(token)) {
      // La ponctuation est un vrai silence : la bouche se ferme. C'est ce qui
      // distingue une phrase articulée d'un bavardage continu.
      units.push('SIL');
      if (/[.!?…]/.test(token)) units.push('SIL');
      continue;
    }
    if (/^\s+$/.test(token)) continue;
    units.push(...wordToVisemes(token));
  }
  if (!units.length) return [];

  const total = units.reduce((sum, v) => sum + (WEIGHT[v] || 1), 0);
  const span = Math.max(0.3, Number(duration) || units.length * 0.075);
  const track = [];
  let cursor = 0;
  for (const viseme of units) {
    const length = ((WEIGHT[viseme] || 1) / total) * span;
    track.push({ at: cursor, until: cursor + length, viseme, shape: VISEMES[viseme] });
    cursor += length;
  }
  return track;
}

/** Forme de bouche à l'instant `t`, interpolée entre le visème courant et le
 *  suivant. La transition occupe les 45 derniers pour cent du visème : une
 *  bouche qui saute d'une forme à l'autre donne un pantin, pas une parole. */
export function sampleVisemeTrack(track, t) {
  if (!track || !track.length) return VISEMES.SIL;
  if (t <= 0) return track[0].shape;
  const last = track[track.length - 1];
  if (t >= last.until) return VISEMES.SIL;

  let index = 0;
  while (index < track.length - 1 && track[index].until < t) index += 1;
  const current = track[index];
  const next = track[index + 1];
  if (!next) return current.shape;

  const length = Math.max(1e-4, current.until - current.at);
  const progress = (t - current.at) / length;
  const blend = progress < 0.55 ? 0 : (progress - 0.55) / 0.45;
  const ease = blend * blend * (3 - 2 * blend);          // smoothstep

  const a = current.shape, b = next.shape;
  return {
    open:  a.open  + (b.open  - a.open)  * ease,
    wide:  a.wide  + (b.wide  - a.wide)  * ease,
    round: a.round + (b.round - a.round) * ease,
    press: a.press + (b.press - a.press) * ease,
  };
}

export default { VISEMES, buildVisemeTrack, sampleVisemeTrack };
