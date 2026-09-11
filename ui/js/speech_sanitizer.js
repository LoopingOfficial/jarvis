/* JARVIS 4 — speech_sanitizer.js (miroir côté client du sanitizer serveur).
 * Garantit que la synthèse vocale (navigateur ou Piper) ne lit jamais de la
 * syntaxe brute (markdown, URLs, nombres techniques), quel que soit le modèle.
 */
(function () {
  'use strict';

  const SMALL_WORDS = {
    zéro: 0, zero: 0, un: 1, une: 1, deux: 2, trois: 3, quatre: 4, cinq: 5,
    six: 6, sept: 7, huit: 8, neuf: 9, dix: 10, onze: 11, douze: 12,
    treize: 13, quatorze: 14, quinze: 15, seize: 16, vingt: 20, trente: 30,
    quarante: 40, cinquante: 50, soixante: 60,
  };
  const TEENS = ['dix', 'onze', 'douze', 'treize', 'quatorze', 'quinze', 'seize', 'dix-sept', 'dix-huit', 'dix-neuf'];
  const TENS = ['', '', 'vingt', 'trente', 'quarante', 'cinquante', 'soixante', 'soixante-dix', 'quatre-vingts', 'quatre-vingt-dix'];

  function intWords(n) {
    if (n === 0) return 'zéro';
    const unities = ['', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf'];
    const number = Math.floor(Math.abs(n));
    let chunks = [];
    let part = number;
    while (part > 0) {
      chunks.unshift(part % 1000);
      part = Math.floor(part / 1000);
    }
    if (n < 0) chunks[-1] = null;
    const scale = ['milliard', 'million', 'mille', ''];
    const words = [];
    chunks.forEach((chunk, i) => {
      if (!chunk) return;
      const sc = scale[scale.length - chunks.length + i];
      words.push(groupWords(chunk));
      if (sc) words.push(sc);
    });
    let s = words.filter(Boolean).join(' ');
    if (s === 'un mille') s = 'mille';
    return (n < 0 ? 'moins ' : '') + s;
  }

  function groupWords(num) {
    const unities = ['', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf'];
    let s = '';
    const h = Math.floor(num / 100);
    const rest = num % 100;
    if (h) s += (h > 1 ? unities[h] + ' ' : '') + 'cent';
    if (rest) {
      if (h && rest) s += ' et ';
      if (rest === 1 && h && s) s += 'un';
      else if (rest < 10 && rest !== 1) s += unities[rest];
      else if (rest < 17) s += TEENS[rest - 10];
      else if (rest < 20) s += 'dix-' + unities[rest - 10];
      else {
        let t = Math.floor(rest / 10);
        let u = rest % 10;
        if (u === 1 && t < 8) { s += TENS[t] + ' et un'; return s; }
        if (t === 7 || t === 9) {
          s += TENS[t - 1] + '-' + (u === 1 ? 'et-onze' : u === 0 ? TEENS[0] : TEENS[u]);
          return s;
        }
        if (t > 8 && u === 0) { s += 'quatre-vingts'; return s; }
        s += TENS[t];
        if (u) s += '-' + unities[u];
      }
    }
    return s.trim();
  }

  function numberToWords(value) {
    const n = Number(value);
    if (!isFinite(n) && value) return String(value);
    if (Number.isInteger(n)) return intWords(n);
    const [i, d] = value.toString().split('.');
    let s = intWords(parseInt(i, 10) || 0);
    if (d && d.length) {
      s += ' virgule ' + d.split('').map((c) => SMALL_WORDS[c] || c).join(' ');
    }
    return s;
  }

  function readNumber(match, unsure) {
    const value = match
      .replace(/[%\s€]/g, '')
      .replace(',', '.');
    return '*' + numberToWords(value) + (match.includes('%') ? ' pour cent*' : '*');
  }

  function sanitizeText(s) {
    if (!s || typeof s !== 'string') return '';
    let out = String(s);

    // Code, JSON, moutures techniques → déduits.
    out = out.replace(/```[\s\S]*?```/g, ' Extrait technique omis. ');
    out = out.replace(/`[^`]+`/g, ' ');
    out = out.replace(/\n\s*\n+/g, '\n\n');

    // URLs → "lien".
    out = out.replace(/\bhttps?:\/\/[^\s<>"']+/gi, (m) => {
      const d = m.replace(/[),.;:\]]+$/, '');
      return '*lien vers ' + d.replace(/^\w+:\/\//, '').split('/').shift() + '*';
    });
    out = out.replace(/\bwww\.[^\s<>"']+/gi, '*lien vers ' + '$&');
    // Emails.
    out = out.replace(/\b[\w.+-]+@[\w-]+\.[\w.]+\b/g, '*adresse e-mail*');
    // Paths.
    out = out.replace(/\b([A-Za-z]:[\\/][^\s<>"']+)/g, '*chemin de fichier*');
    out = out.replace(/\b(?:\.\.?\/){1,}[^\s<>"']+/g, '*chemin*');

    // Pourcentages.
    out = out.replace(/\b\d+(?:[.,]\d+)?\s*%/g, readNumber);
    // Nombres (fréquence ≥ 2, naturels) — les identifiants restent lisibles.
    out = out.replace(/\b\d{3,}(?:[.,]\d+)?\b/g, readNumber);

    // Tableaux Markdown.
    const tableLines = out.split('\n').filter((l) => /^\s*\|/.test(l) || /^\s*:?-+\s*\|?/.test(l));
    if (tableLines.length > 1) {
      out = out.split('\n').filter((l) => !/^\s*(?:\|)?:?-+:?(\||\s)/.test(l) && !/^\s*\|/.test(l)).join('\n');
    }

    // Markdown résiduel.
    out = out.replace(/[*_~#]{1,3}/g, ' ');
    out = out.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    out = out.replace(/!\[([^\]]*)\]\([^)]+\)/g, '');
    out = out.replace(/!\[([^\]]*)\]\([^)]+\)/g, '');

    out = out.replace(/\s+/g, ' ').replace(/&/g, ' et ').trim();
    return out;
  }

  window.speechSanitize = sanitizeText;

  // Raccourci utilisé par voice.js avant toute synthèse locale.
  window.pipeSpeech = (text) => {
    if (typeof text !== 'string') return '';
    let out = sanitizeText(text);
    if (/^[\s():,.%\-—']+$/.test(out)) out = out.trim();
    return out;
  };
})();