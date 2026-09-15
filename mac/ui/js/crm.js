/* ==========================================================================
   CRM — contacts, pipeline commercial, sociétés.

   Toutes les données viennent des routes /api/crm/*, donc de la base SQLite
   locale : le moteur (crm.py, crm_pipeline.py) existait déjà, c'est cette
   page qui manquait. Rien n'est stocké côté navigateur, et aucune donnée ne
   sort de la machine.

   Trois principes tenus ici :

   1. **On n'invente jamais une valeur.** Un champ absent s'affiche « — », pas
      zéro : un chiffre d'affaires nul et un montant non renseigné ne se
      ressemblent pas, et les confondre fausserait une décision commerciale.

   2. **Le score est toujours accompagné de son détail.** `score_detail` vient
      du moteur, qui calcule par règles explicites. Un score affiché seul
      serait un verdict opaque ; ici on peut l'auditer d'un survol.

   3. **Aucune suppression sans confirmation.** Une fiche client effacée par
      inadvertance ne se retrouve pas.
   ========================================================================== */

const CRM = {
  view: 'contacts',      // contacts | pipeline | companies
  contacts: [],
  companies: [],
  deals: [],
  pipeline: null,
  selected: null,        // fiche ouverte (contact_view complet)
  query: '',
  el: null,
};

/* Les étapes et leur ordre viennent du moteur (STAGES dans crm_pipeline.py).
   Les redéclarer ici est un doublon assumé : le navigateur doit pouvoir
   dessiner les colonnes avant toute réponse réseau. L'ordre doit rester
   identique des deux côtés, sinon le kanban mentirait sur l'avancement. */
CRM.STAGES = [
  { id: 'nouveau', label: 'Nouveau' },
  { id: 'qualifie', label: 'Qualifié' },
  { id: 'proposition', label: 'Proposition' },
  { id: 'negociation', label: 'Négociation' },
  { id: 'gagne', label: 'Gagné' },
  { id: 'perdu', label: 'Perdu' },
];

CRM.KINDS = [
  ['note', 'Note'], ['call', 'Appel'], ['email', 'E-mail'],
  ['meeting', 'Réunion'], ['task', 'Tâche'], ['other', 'Autre'],
];

/* ------------------------------------------------------------- formatage */

/** Montant lisible. `null`/absent -> « — » : voir le principe 1 ci-dessus. */
CRM.money = function (amount, currency) {
  if (amount === null || amount === undefined || amount === '') return '—';
  const n = Number(amount);
  if (!isFinite(n)) return '—';
  return n.toLocaleString('fr-FR', {
    style: 'currency', currency: currency || 'EUR', maximumFractionDigits: 0,
  });
};

/** Le score colore la pastille, mais le chiffre reste toujours lisible. */
CRM.scoreClass = function (score) {
  const n = Number(score) || 0;
  if (n >= 70) return 'ok';
  if (n >= 40) return 'warn';
  return '';
};

CRM.tagsOf = function (contact) {
  const raw = contact && contact.tags;
  if (Array.isArray(raw)) return raw;
  if (typeof raw === 'string' && raw.trim()) {
    // La table pivot stocke du JSON ; une ancienne fiche peut contenir une
    // simple liste séparée par des virgules. On accepte les deux plutôt que
    // d'afficher « [object Object] » à l'utilisateur.
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [String(parsed)];
    } catch { return raw.split(',').map((t) => t.trim()).filter(Boolean); }
  }
  return [];
};

CRM.label = function (contact) {
  const name = (contact.name || '').trim();
  const company = (contact.company || '').trim();
  return company ? `${name} — ${company}` : (name || 'Sans nom');
};

/* --------------------------------------------------------------- données */

CRM.loadContacts = async function () {
  const path = CRM.query
    ? `/api/crm/contacts?q=${encodeURIComponent(CRM.query)}`
    : '/api/crm/contacts?limit=200';
  const res = await J.get(path);
  CRM.contacts = (res && res.contacts) || [];
};

CRM.loadPipeline = async function () {
  const [deals, pipeline] = await Promise.all([
    J.get('/api/crm/deals'), J.get('/api/crm/pipeline'),
  ]);
  CRM.deals = (deals && deals.deals) || [];
  CRM.pipeline = pipeline && pipeline.stages ? pipeline : null;
};

CRM.loadCompanies = async function () {
  const res = await J.get('/api/crm/companies');
  CRM.companies = (res && res.companies) || [];
};

/* ----------------------------------------------------------------- vues */

Pages.crm = async function (el) {
  CRM.el = el;
  el.innerHTML = `
    <div class="page-head">
      <div><h1>CRM</h1><p>Contacts, opportunités et historique — base locale, rien ne sort de la machine.</p></div>
      <div class="page-actions">
        <button class="btn" id="crmRescore">${icon('refresh', 13)} Recalculer les scores</button>
        <button class="btn primary" id="crmNewContact">${icon('plus', 13)} Nouveau contact</button>
      </div>
    </div>
    <div class="crm-tabs">
      ${[['contacts', 'Contacts'], ['pipeline', 'Pipeline'], ['companies', 'Sociétés']]
        .map(([id, label]) =>
          `<button class="crm-tab${CRM.view === id ? ' active' : ''}" data-view="${id}">${label}</button>`)
        .join('')}
    </div>
    <div id="crmBody"><div class="empty">Chargement…</div></div>`;

  $('#crmNewContact', el).addEventListener('click', () => CRM.editContact(null));
  $('#crmRescore', el).addEventListener('click', CRM.rescoreAll);
  $$('.crm-tab', el).forEach((b) => b.addEventListener('click', () => {
    CRM.view = b.dataset.view;
    CRM.selected = null;
    Pages.crm(el);
  }));

  await CRM.renderBody();
};

CRM.renderBody = async function () {
  const body = $('#crmBody', CRM.el);
  if (!body) return;
  try {
    if (CRM.view === 'contacts') await CRM.renderContacts(body);
    else if (CRM.view === 'pipeline') await CRM.renderPipeline(body);
    else await CRM.renderCompanies(body);
  } catch (err) {
    // Une page blanche laisserait croire à un carnet vide. On dit ce qui s'est
    // passé et on laisse un moyen de réessayer.
    body.innerHTML = `<div class="empty">Impossible de charger le CRM : ${esc(err.message || err)}
      <br><button class="btn sm" id="crmRetry" style="margin-top:10px">Réessayer</button></div>`;
    const retry = $('#crmRetry', body);
    if (retry) retry.addEventListener('click', () => CRM.renderBody());
  }
};

/* ------------------------------------------------------------- contacts */

CRM.renderContacts = async function (body) {
  await CRM.loadContacts();
  body.innerHTML = `
    <div class="crm-split">
      <div class="card crm-listcard">
        <div class="card-head">
          <h2>CONTACTS</h2>
          <span class="tag">${CRM.contacts.length}</span>
        </div>
        <div class="card-body">
          <input class="crm-search" id="crmSearch" type="search" autocomplete="off"
                 placeholder="Nom, société ou e-mail…" value="${esc(CRM.query)}">
          <div class="list" id="crmContactList"></div>
        </div>
      </div>
      <div id="crmDetail" class="crm-detail"></div>
    </div>`;

  const search = $('#crmSearch', body);
  let timer = null;
  search.addEventListener('input', () => {
    // Anti-rebond : la recherche part sur le serveur à chaque frappe sinon,
    // et les réponses reviendraient dans le désordre.
    clearTimeout(timer);
    timer = setTimeout(async () => {
      CRM.query = search.value.trim();
      await CRM.loadContacts();
      CRM.paintContactList();
    }, 220);
  });

  CRM.paintContactList();
  if (CRM.selected) CRM.paintDetail();
  else CRM.paintDetailPlaceholder();
};

CRM.paintContactList = function () {
  const list = $('#crmContactList', CRM.el);
  if (!list) return;
  if (!CRM.contacts.length) {
    list.innerHTML = CRM.query
      ? `<div class="empty">Aucun contact ne correspond à « ${esc(CRM.query)} ».</div>`
      : `<div class="empty">Le carnet est vide. Crée un premier contact.</div>`;
    return;
  }
  list.innerHTML = CRM.contacts.map((c) => {
    const score = Number(c.score) || 0;
    return `<div class="list-row crm-row${CRM.selected && CRM.selected.contact
      && CRM.selected.contact.id === c.id ? ' active' : ''}" data-id="${esc(c.id)}">
      <div class="meta">
        <b>${esc(c.name || 'Sans nom')}</b>
        <small>${esc(c.company || '—')}${c.email ? ' · ' + esc(c.email) : ''}</small>
      </div>
      <span class="tag ${CRM.scoreClass(score)}" title="Score de lead">${score}</span>
    </div>`;
  }).join('');
  $$('.crm-row', list).forEach((row) => row.addEventListener('click',
    () => CRM.openContact(row.dataset.id)));
};

CRM.paintDetailPlaceholder = function () {
  const box = $('#crmDetail', CRM.el);
  if (box) box.innerHTML = `<div class="card"><div class="card-body">
    <div class="empty">Sélectionne un contact pour voir sa fiche, son historique et ses opportunités.</div>
  </div></div>`;
};

CRM.openContact = async function (contactId) {
  const res = await J.get(`/api/crm/contacts/${encodeURIComponent(contactId)}`);
  if (!res || res.ok === false || !res.contact) {
    toast('Contact introuvable.', 'error');
    return;
  }
  CRM.selected = res;
  CRM.paintContactList();
  CRM.paintDetail();
};

CRM.paintDetail = function () {
  const box = $('#crmDetail', CRM.el);
  if (!box || !CRM.selected) return;
  const { contact, company, deals, timeline } = CRM.selected;
  const tags = CRM.tagsOf(contact);
  const auto = contact.auto_tags || [];
  const detail = contact.score_detail || {};
  const score = Number(contact.score) || 0;

  box.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>${esc(contact.name || 'Sans nom')}</h2>
        <div class="crm-head-actions">
          <button class="btn sm" data-act="edit">${icon('edit', 12)} Modifier</button>
          <button class="btn sm danger" data-act="delete">${icon('trash', 12)} Supprimer</button>
        </div>
      </div>
      <div class="card-body">
        <div class="crm-facts">
          ${[['Société', company ? company.name : contact.company],
             ['E-mail', contact.email], ['Téléphone', contact.phone],
             ['Statut', contact.status], ['Rôle', contact.role],
             ['Origine', contact.source], ['N° TVA', contact.vat_number]]
            .map(([k, v]) => `<div class="crm-fact"><span>${k}</span><b>${esc(v || '—')}</b></div>`)
            .join('')}
        </div>

        <div class="crm-score">
          <div class="crm-score-val ${CRM.scoreClass(score)}">${score}</div>
          <div class="crm-score-body">
            <b>Score de lead</b>
            ${CRM.scoreDetailHtml(detail)}
          </div>
        </div>

        ${tags.length || auto.length ? `<div class="crm-tags">
          ${tags.map((t) => `<span class="tag cy">${esc(t)}</span>`).join('')}
          ${auto.map((t) => `<span class="tag" title="Déduit automatiquement">${esc(t)}</span>`).join('')}
        </div>` : ''}

        ${contact.notes ? `<div class="crm-notes">${esc(contact.notes)}</div>` : ''}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>OPPORTUNITÉS</h2>
        <button class="btn sm" data-act="new-deal">${icon('plus', 12)} Ajouter</button></div>
      <div class="card-body">
        ${(deals && deals.length) ? `<div class="list">${deals.map((d) => `
          <div class="list-row crm-deal-row" data-deal="${esc(d.id)}">
            <div class="meta"><b>${esc(d.title)}</b>
              <small>${esc(CRM.stageLabel(d.stage))} · ${d.probability || 0}% de probabilité</small></div>
            <span class="tag ${d.status === 'won' ? 'ok' : d.status === 'lost' ? 'warn' : 'cy'}">${
              CRM.money(d.amount, d.currency)}</span>
          </div>`).join('')}</div>`
          : '<div class="empty">Aucune opportunité pour ce contact.</div>'}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>HISTORIQUE</h2>
        <button class="btn sm" data-act="new-interaction">${icon('plus', 12)} Journaliser</button></div>
      <div class="card-body">
        ${(timeline && timeline.length) ? `<div class="crm-timeline">${timeline.map((i) => `
          <div class="crm-ti" data-interaction="${esc(i.id)}">
            <span class="crm-ti-kind k-${esc(i.kind || 'note')}">${esc(CRM.kindLabel(i.kind))}</span>
            <div class="crm-ti-body">
              <b>${esc(i.subject || '(sans objet)')}</b>
              ${i.summary ? `<small>${esc(i.summary)}</small>` : ''}
              <em>${esc(fmtAgo(i.occurred_at))}${i.agent ? ' · ' + esc(i.agent) : ''}${
                // L'intention est déduite par règles, avec une confiance. On
                // affiche les deux : une intention sans sa confiance se lirait
                // comme une certitude.
                i.intent ? ` · intention « ${esc(i.intent)} » (${
                  Math.round((Number(i.intent_confidence) || 0) * 100)}%)` : ''}</em>
            </div>
            <button class="icon-btn crm-ti-del" data-del-interaction="${esc(i.id)}"
                    title="Supprimer cet échange">${icon('trash', 11)}</button>
          </div>`).join('')}</div>`
          : '<div class="empty">Aucun échange enregistré.</div>'}
      </div>
    </div>`;

  const act = (name, fn) => {
    const b = box.querySelector(`[data-act="${name}"]`);
    if (b) b.addEventListener('click', fn);
  };
  act('edit', () => CRM.editContact(contact));
  act('delete', () => CRM.deleteContact(contact));
  act('new-deal', () => CRM.editDeal(null, contact.id));
  act('new-interaction', () => CRM.addInteraction(contact.id));
  $$('.crm-deal-row', box).forEach((row) => row.addEventListener('click', () => {
    const deal = (deals || []).find((d) => d.id === row.dataset.deal);
    if (deal) CRM.editDeal(deal, contact.id);
  }));
  $$('[data-del-interaction]', box).forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const entry = (timeline || []).find((i) => i.id === b.dataset.delInteraction);
    if (!await confirmDialog('Supprimer cet échange',
      `« ${(entry && entry.subject) || 'Cet échange'} » sera retiré de l'historique et le score recalculé.`,
      { danger: true })) return;
    await J.del(`/api/crm/interactions/${encodeURIComponent(b.dataset.delInteraction)}`);
    toast('Échange supprimé.');
    await CRM.openContact(contact.id);
  }));
};

/* Libellés des critères de score. Le moteur les nomme sans accent (clés de
   dictionnaire) ; les afficher tels quels donnerait « recence » à l'écran. */
CRM.SCORE_LABELS = {
  recence: 'Récence', volume: 'Volume', reciprocite: 'Réciprocité',
  intention: 'Intention', deals: 'Opportunités',
};

/** Le détail du score, tel que le moteur l'a calculé. Rien n'est recalculé
    ici : réinterpréter le score côté navigateur le rendrait incohérent avec
    celui que les agents utilisent.

    La structure est imbriquée — `{recence: {jours, points}, …, total}` — et
    c'est `points` qui nous intéresse, critère par critère. Un score affiché
    seul serait un verdict opaque ; le détail le rend auditable. */
CRM.scoreDetailHtml = function (detail) {
  const parts = Object.entries(detail || {})
    .filter(([key, v]) => key !== 'total' && v && typeof v === 'object')
    .map(([key, v]) => {
      const points = Number(v.points) || 0;
      const label = CRM.SCORE_LABELS[key] || key;
      // Le motif qui a produit les points (jours écoulés, nombre d'échanges,
      // intention détectée) est mis en infobulle : il explique le « pourquoi ».
      const why = Object.entries(v)
        .filter(([k2]) => k2 !== 'points')
        .map(([k2, v2]) => `${k2} : ${v2}`)
        .join(' · ');
      return `<span class="crm-score-part"${why ? ` title="${esc(why)}"` : ''}>${esc(label)}
        <em>${points > 0 ? '+' : ''}${points}</em></span>`;
    });
  if (!parts.length) return '<small>Pas encore de signal mesuré pour ce contact.</small>';
  return `<div class="crm-score-parts">${parts.join('')}</div>`;
};

CRM.stageLabel = function (stage) {
  const found = CRM.STAGES.find((s) => s.id === stage);
  return found ? found.label : (stage || '—');
};

CRM.kindLabel = function (kind) {
  const found = CRM.KINDS.find(([id]) => id === kind);
  return found ? found[1] : (kind || 'Note');
};

/* -------------------------------------------------------------- pipeline */

CRM.renderPipeline = async function (body) {
  await CRM.loadPipeline();
  const stages = (CRM.pipeline && CRM.pipeline.stages) || {};
  const weighted = CRM.pipeline ? CRM.pipeline.weighted_open : null;

  body.innerHTML = `
    <div class="crm-pipe-head">
      <div class="crm-pipe-stat">
        <span>VALEUR PONDÉRÉE (en cours)</span>
        <b>${CRM.money(weighted, 'EUR')}</b>
        <small>Montants des opportunités ouvertes, pondérés par leur probabilité.</small>
      </div>
      <button class="btn primary" id="crmNewDeal">${icon('plus', 13)} Nouvelle opportunité</button>
    </div>
    <div class="crm-kanban">
      ${CRM.STAGES.map((s) => {
        const bucket = stages[s.id] || { count: 0, amount: 0 };
        const items = CRM.deals.filter((d) => d.stage === s.id);
        return `<div class="crm-col" data-stage="${s.id}">
          <div class="crm-col-head">
            <b>${s.label}</b>
            <span class="tag">${bucket.count || 0}</span>
            <small>${bucket.count ? CRM.money(bucket.amount, 'EUR') : '—'}</small>
          </div>
          <div class="crm-col-body">
            ${items.length ? items.map((d) => `
              <div class="crm-card-deal" data-deal="${esc(d.id)}">
                <b>${esc(d.title)}</b>
                <span class="crm-deal-amount">${CRM.money(d.amount, d.currency)}</span>
                <small>${d.probability || 0}%${d.source ? ' · ' + esc(d.source) : ''}</small>
              </div>`).join('')
              : '<div class="crm-col-empty">—</div>'}
          </div>
        </div>`;
      }).join('')}
    </div>`;

  $('#crmNewDeal', body).addEventListener('click', () => CRM.editDeal(null, ''));
  $$('.crm-card-deal', body).forEach((card) => card.addEventListener('click', () => {
    const deal = CRM.deals.find((d) => d.id === card.dataset.deal);
    if (deal) CRM.editDeal(deal, deal.contact_id || '');
  }));
};

/* -------------------------------------------------------------- sociétés */

CRM.renderCompanies = async function (body) {
  await CRM.loadCompanies();
  body.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>SOCIÉTÉS</h2>
        <button class="btn sm" id="crmNewCompany">${icon('plus', 12)} Ajouter</button></div>
      <div class="card-body">
        ${CRM.companies.length ? `<div class="list">${CRM.companies.map((c) => `
          <div class="list-row">
            <div class="meta"><b>${esc(c.name)}</b>
              <small>${esc(c.domain || '—')}${c.industry ? ' · ' + esc(c.industry) : ''}</small></div>
            <div class="crm-head-actions">
              <button class="btn sm" data-edit="${esc(c.id)}">${icon('edit', 11)}</button>
              <button class="btn sm danger" data-del="${esc(c.id)}">${icon('trash', 11)}</button>
            </div>
          </div>`).join('')}</div>`
          : '<div class="empty">Aucune société enregistrée.</div>'}
      </div>
    </div>`;

  $('#crmNewCompany', body).addEventListener('click', () => CRM.editCompany(null));
  $$('[data-edit]', body).forEach((b) => b.addEventListener('click', () => {
    const c = CRM.companies.find((x) => x.id === b.dataset.edit);
    if (c) CRM.editCompany(c);
  }));
  $$('[data-del]', body).forEach((b) => b.addEventListener('click', async () => {
    const c = CRM.companies.find((x) => x.id === b.dataset.del);
    if (!c) return;
    if (!await confirmDialog('Supprimer la société',
      `« ${c.name} » sera retirée définitivement.`, { danger: true })) return;
    await J.del(`/api/crm/companies/${encodeURIComponent(c.id)}`);
    toast('Société supprimée.');
    CRM.renderBody();
  }));
};

/* ------------------------------------------------------------ formulaires */

CRM.field = function (name, label, value, opts) {
  const o = opts || {};
  if (o.options) {
    return `<label class="field"><span>${label}</span><select name="${name}">
      ${o.options.map(([v, l]) =>
        `<option value="${esc(v)}"${String(value || '') === String(v) ? ' selected' : ''}>${esc(l)}</option>`)
        .join('')}</select></label>`;
  }
  if (o.textarea) {
    return `<label class="field"><span>${label}</span>
      <textarea name="${name}" rows="3">${esc(value || '')}</textarea></label>`;
  }
  return `<label class="field"><span>${label}</span>
    <input name="${name}" type="${o.type || 'text'}" value="${esc(value === 0 ? '0' : (value || ''))}"
      ${o.placeholder ? `placeholder="${esc(o.placeholder)}"` : ''}></label>`;
};

CRM.readForm = function (root) {
  const out = {};
  $$('[name]', root).forEach((input) => { out[input.name] = input.value.trim(); });
  return out;
};

CRM.editContact = function (contact) {
  const c = contact || {};
  const m = modal({
    title: c.id ? 'Modifier le contact' : 'Nouveau contact',
    wide: true,
    body: `<div class="crm-form">
      ${CRM.field('name', 'Nom complet', c.name)}
      ${CRM.field('company', 'Société', c.company)}
      ${CRM.field('email', 'E-mail', c.email, { type: 'email' })}
      ${CRM.field('phone', 'Téléphone', c.phone)}
      ${CRM.field('address', 'Adresse', c.address)}
      ${CRM.field('vat_number', 'N° TVA', c.vat_number)}
      ${CRM.field('role', 'Rôle', c.role, { placeholder: 'Dirigeant, acheteur…' })}
      ${CRM.field('source', 'Origine', c.source, { placeholder: 'Salon, site, recommandation…' })}
      ${CRM.field('notes', 'Notes', c.notes, { textarea: true })}
    </div>`,
    footer: `<button class="btn" data-close>Annuler</button>
             <button class="btn primary" data-save>Enregistrer</button>`,
  });
  m.$('[data-save]').addEventListener('click', async () => {
    const payload = CRM.readForm(m.el);
    if (!payload.name) { toast('Le nom est obligatoire.', 'error'); return; }
    if (c.id) payload.id = c.id;
    const res = await J.post('/api/crm/contacts', payload);
    if (!res || res.ok === false) { toast(res && res.error || 'Échec de l\'enregistrement.', 'error'); return; }
    m.close();
    toast('Contact enregistré.');
    // On rouvre la fiche pour que le score et les tags recalculés soient visibles.
    if (res.contact) CRM.selected = res;
    await CRM.renderBody();
  });
};

CRM.deleteContact = async function (contact) {
  const ok = await confirmDialog('Supprimer le contact',
    `La fiche de ${contact.name || 'ce contact'} et son historique seront effacés définitivement.`,
    { danger: true });
  if (!ok) return;
  await J.del(`/api/crm/contacts/${encodeURIComponent(contact.id)}`);
  CRM.selected = null;
  toast('Contact supprimé.');
  CRM.renderBody();
};

CRM.editDeal = function (deal, contactId) {
  const d = deal || {};
  const contactOptions = [['', '— Aucun —']]
    .concat(CRM.contacts.map((c) => [c.id, CRM.label(c)]));
  const m = modal({
    title: d.id ? 'Modifier l\'opportunité' : 'Nouvelle opportunité',
    wide: true,
    body: `<div class="crm-form">
      ${CRM.field('title', 'Titre', d.title, { placeholder: 'Ex. Terrasse bois — 40 m²' })}
      ${CRM.field('contact_id', 'Contact', d.contact_id || contactId, { options: contactOptions })}
      ${CRM.field('stage', 'Étape', d.stage || 'nouveau',
        { options: CRM.STAGES.map((s) => [s.id, s.label]) })}
      ${CRM.field('amount', 'Montant', d.amount, { type: 'number' })}
      ${CRM.field('currency', 'Devise', d.currency || 'EUR')}
      ${CRM.field('probability', 'Probabilité (%)', d.probability, { type: 'number' })}
      ${CRM.field('source', 'Origine', d.source)}
      ${CRM.field('notes', 'Notes', d.notes, { textarea: true })}
    </div>
    <p class="hint">Passer une opportunité en « Gagné » ou « Perdu » la clôt automatiquement
       et met à jour le score du contact.</p>`,
    footer: `${d.id ? '<button class="btn danger" data-del>Supprimer</button>' : ''}
             <button class="btn" data-close>Annuler</button>
             <button class="btn primary" data-save>Enregistrer</button>`,
  });

  m.$('[data-save]').addEventListener('click', async () => {
    const payload = CRM.readForm(m.el);
    if (!payload.title) { toast('Le titre est obligatoire.', 'error'); return; }
    if (d.id) payload.id = d.id;
    payload.amount = Number(payload.amount || 0);
    payload.probability = Number(payload.probability || 0);
    const res = await J.post('/api/crm/deals', payload);
    if (!res || res.ok === false) { toast(res && res.error || 'Échec.', 'error'); return; }
    m.close();
    toast('Opportunité enregistrée.');
    if (CRM.selected && CRM.selected.contact) await CRM.openContact(CRM.selected.contact.id);
    await CRM.renderBody();
  });

  const del = m.$('[data-del]');
  if (del) del.addEventListener('click', async () => {
    if (!await confirmDialog('Supprimer l\'opportunité',
      `« ${d.title} » sera retirée définitivement.`, { danger: true })) return;
    await J.del(`/api/crm/deals/${encodeURIComponent(d.id)}`);
    m.close();
    toast('Opportunité supprimée.');
    if (CRM.selected && CRM.selected.contact) await CRM.openContact(CRM.selected.contact.id);
    await CRM.renderBody();
  });
};

CRM.editCompany = function (company) {
  const c = company || {};
  const m = modal({
    title: c.id ? 'Modifier la société' : 'Nouvelle société',
    body: `<div class="crm-form">
      ${CRM.field('name', 'Nom', c.name)}
      ${CRM.field('domain', 'Domaine', c.domain, { placeholder: 'exemple.fr' })}
      ${CRM.field('industry', 'Secteur', c.industry)}
      ${CRM.field('size', 'Taille', c.size, { placeholder: 'Ex. 10-50' })}
      ${CRM.field('notes', 'Notes', c.notes, { textarea: true })}
    </div>`,
    footer: `<button class="btn" data-close>Annuler</button>
             <button class="btn primary" data-save>Enregistrer</button>`,
  });
  m.$('[data-save]').addEventListener('click', async () => {
    const payload = CRM.readForm(m.el);
    if (!payload.name) { toast('Le nom est obligatoire.', 'error'); return; }
    if (c.id) payload.id = c.id;
    const res = await J.post('/api/crm/companies', payload);
    if (!res || res.ok === false) { toast(res && res.error || 'Échec.', 'error'); return; }
    m.close();
    toast('Société enregistrée.');
    CRM.renderBody();
  });
};

CRM.addInteraction = function (contactId) {
  const m = modal({
    title: 'Journaliser un échange',
    body: `<div class="crm-form">
      ${CRM.field('kind', 'Type', 'note', { options: CRM.KINDS })}
      ${CRM.field('subject', 'Objet', '')}
      ${/* Le champ s'appelle `summary` côté moteur. L'envoyer sous un autre
            nom le ferait accepter en silence puis disparaître. */''}
      ${CRM.field('summary', 'Contenu', '', { textarea: true })}
    </div>
    <p class="hint">L'échange est daté à l'instant de l'enregistrement et le score du
       contact est recalculé dans la foulée.</p>`,
    footer: `<button class="btn" data-close>Annuler</button>
             <button class="btn primary" data-save>Enregistrer</button>`,
  });
  m.$('[data-save]').addEventListener('click', async () => {
    const payload = CRM.readForm(m.el);
    if (!payload.subject && !payload.summary) {
      toast('Indique au moins un objet ou un contenu.', 'error');
      return;
    }
    payload.contact_id = contactId;
    payload.agent = 'utilisateur';   // saisi à la main, pas produit par un agent
    const res = await J.post('/api/crm/interactions', payload);
    if (!res || res.ok === false) { toast(res && res.error || 'Échec.', 'error'); return; }
    m.close();
    toast('Échange journalisé.');
    await CRM.openContact(contactId);
  });
};

CRM.rescoreAll = async function () {
  const res = await J.post('/api/crm/rescore', {});
  if (!res || res.ok === false) { toast('Recalcul impossible.', 'error'); return; }
  toast(`${res.rescored ?? 0} contact(s) recalculé(s).`);
  if (CRM.selected && CRM.selected.contact) await CRM.openContact(CRM.selected.contact.id);
  else CRM.renderBody();
};

window.CRM = CRM;
