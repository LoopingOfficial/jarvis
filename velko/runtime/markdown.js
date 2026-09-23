/** Renderer Markdown pour les résultats de mission.
 *
 *  Pipeline strict : PARSE (structure) → SANITIZE (échappement + schémas sûrs)
 *  → RENDER (DOM/HTML sous contrôle). Aucun caractère `#`, `*` ou `|` ne doit
 *  rester visible, aucun HTML brut n'est adopté, les liens ne pointent que
 *  vers http(s) ou mailto. Fonctionne sans dépendance.
 */
const ESC = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'};
const esc = t => t.replace(/[&<>"]/g, c => ESC[c]);

/** Seuls les schémas sûrs survivent : un lien reste une ressource, jamais un script. */
function sanitizeUrl(url) {
  const u = String(url || '').trim();
  return /^(https?:|mailto:|#)/i.test(u) ? u : '';
}

/** Inline : le texte est échappé en premier (sanitize), puis décoré (render).
 *  L'ordre inverse exposerait du HTML brut. Les `*` et `#` ne sortent jamais. */
function inline(t) {
  const safe = esc(t);
  const linked = safe.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (all, label, target) => {
    const href = sanitizeUrl(target);
    return href ? `<a href="${href}" target="_blank" rel="noreferrer">${label}</a>` : label;
  });
  return linked
   .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
   .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
   .replace(/`([^`\n]+)`/g, '<code>$1</code>');
}

/** Découpe un rapport en sections structurées (utile au panneau résultat). */
export function parseMarkdown(text = '') {
  const lines = String(text).split(/\r?\n/);
  const doc = { title: '', summary: '', sections: [], kpis: [], observations: [], actions: [] };
  let section = null; let inTable = null; let lastWasTable = false;
  let inCode = false; let code = [];
  const newSection = (heading) => { inTable = null; section = { heading, items: [] }; doc.sections.push(section); };
  const pushItem = (item) => {
    if (section) section.items.push(item);
    else if (item.type === 'line' && item.text) {
      doc.summary = doc.summary ? doc.summary + '\n' + item.text : item.text;
    } else {
      doc.observations.push(item);
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const tr = line.trim();
    if (tr.startsWith('```')) {
      if (inCode) { pushItem({ type:'code', code: code.join('\n') }); code = []; inCode = false; }
      else { inTable = null; inCode = true; }
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const h = /^(#{1,4})\s+(.+)$/.exec(tr);
    if (h) {
      if (!doc.title && !doc.sections.length && !doc.summary) { doc.title = h[2].trim(); continue; }
      newSection(h[2].trim()); continue;
    }
    // Tableau Markdown : lignes de séparation ignorées, cellules tronquées.
    if (tr.startsWith('|')) {
      const cells = tr.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
      if (!cells.every(c => /^:?-+:?$/.test(c))) {
        inTable = { rows: (inTable && inTable.rows.length) ? inTable.rows : [] };
        inTable.rows.push(cells);
        continue;
      }
      continue;   // rangée |---|---| : séparation, jamais rendue
    }
    if (inTable) { pushItem({ type:'table', rows: inTable.rows }); inTable = null; }
    // Liste : `- ` `+ ` `* ` (exigent un espace) ou numérotée. Une graisse en
    // début de ligne (`**317** membres`) n'est PAS une puce.
    const bullet = /^\s*(?:[-+]\s+|\*\s+|\d+[.)]\s+)(?!\s*[*#])(.*)$/.exec(tr);
    if (bullet && !tr.startsWith('**')) {
      const item = bullet[1];
      pushItem({ type:'list', item });
      if (/je peux faire|action|relance|analyser|préparer|sync|compar|envoyer|publier|exploiter|approfondir/i.test(item)) doc.actions.push(item);
      continue;
    }
    if (tr) {
      pushItem({ type:'line', text: tr });
      // KPI : « Dépôts : 12 » ou « 317 membres » — donnée réelle du rapport.
      const colonKpi = /^([^:\n]+?)\s*:\s*(-?\d[\d\s]*(?:[.,]\d+)?)\s*$/.exec(tr);
      const valueKpi = /^(-?\d[\d\s]*(?:[.,]\d+)?)\s+([a-zàâäéèêëîïôöùûüç]+)/i.exec(tr);
      if (colonKpi) {
        if (!doc.kpis.some(k => k.label === colonKpi[1].trim())) doc.kpis.push({ label: colonKpi[1].trim(), value: colonKpi[2].replace(/\s/g, '') });
      } else if (valueKpi) {
        if (!doc.kpis.some(k => k.label === valueKpi[2])) doc.kpis.push({ label: valueKpi[2], value: valueKpi[1].replace(/\s/g, '') });
      }
    }
  }
  if (inCode) pushItem({ type:'code', code: code.join('\n') });
  if (inTable) pushItem({ type:'table', rows: inTable.rows });
  return doc;
}

/** Rend un document Markdown en HTML sûr (aucun caractère brut). */
export function renderMarkdown(text = '') {
  const doc = parseMarkdown(text);
  const out = [];
  if (doc.title) out.push(`<h2 class="rp-title">${inline(doc.title)}</h2>`);
  for (const s of doc.sections) {
    if (s.heading) out.push(`<h3 class="rp-heading">${inline(s.heading)}</h3>`);
    for (const item of s.items || []) {
      if (item.type === 'list') out.push(`<ul class="rp-list"><li>${inline(item.item)}</li></ul>`);
      else if (item.type === 'line') out.push(`<p class="rp-line">${inline(item.text)}</p>`);
      else if (item.type === 'code') out.push(`<pre class="rp-code">${esc(item.code)}</pre>`);
      else if (item.type === 'table') out.push(renderTable(item.rows));
    }
  }
  if (doc.summary) out.unshift(`<p class="rp-summary">${inline(doc.summary.replace(/\n/g, '<br>'))}</p>`);
  return out.join('');
}

function renderTable(rows) {
  if (!rows.length) return '';
  const [head, ...body] = rows.map(row => row.slice(0, 6));
  return `<table class="rp-full"><thead><tr>${(head || []).map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
   `<tbody>${body.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

/** Texte BRUT pour la synthèse vocale : zéro marqueur, zéro table, zéro lien. */
export function stripMarkdown(text = '') {
  return String(text || '')
   .replace(/```[\s\S]*?```/g, ' ')
   .replace(/\[([^\]]+)\]\([^)\s]+\)/g, '$1')
   .replace(/!\[([^\]]*)\]\([^)\s]+\)/g, '$1')
   .replace(/^\s{0,3}(#{1,4})\s+/gm, '')
   .replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+/gm, ' ')
   .replace(/^\s*\|.*\|.*$/gm, ' ')
   .replace(/^\s*(?:[-=]+|-{3,})\s*$/gm, ' ')
   .replace(/[*_`~]/g, '')
   .replace(/[|>]/g, ' ')
   .replace(/\s+/g, ' ')
   .trim();
}