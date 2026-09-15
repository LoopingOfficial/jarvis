/* ==========================================================================
   Pages secondaires : AI Core, Agents, Tasks, Calendar, Memory,
   Conversations, Knowledge Base, Tools & Skills, Workflows.
   ========================================================================== */
const Pages = {};
// Les modules secondaires (dont Avatar Studio) s'enregistrent via l'API globale.
// Sans cette exposition, leur route existe dans le code mais n'est jamais montée.
window.Pages = Pages;

/* -------------------------------------------------------------- AI CORE */
Pages.aicore = async function (el) {
  const s = J.state.status || await J.get('/api/status');
  const env = s.environment || {};
  const sec = s.security || {};
  el.innerHTML = `
    <div class="page-head">
      <div><h1>AI Core</h1><p>Cœur du système : modèles, coffre, outils, environnement.</p></div>
      <div class="page-actions">
        <button class="btn" id="coreBackup">${icon('shield', 13)} Sauvegarder la base</button>
        <button class="btn" id="coreTestAll">${icon('refresh', 13)} Tester les connecteurs</button>
      </div>
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>MODÈLES</h2></div><div class="card-body">
        <div class="list" id="coreProviders"></div></div></div>
      <div class="card"><div class="card-head"><h2>SÉCURITÉ</h2></div><div class="card-body">
        <div class="list">
          <div class="list-row"><div class="meta"><b>Coffre de secrets</b>
            <small>${esc(sec.vault_backend || '—')}</small></div>
            <span class="tag ok">${sec.secrets ?? 0} secret(s)</span></div>
          <div class="list-row"><div class="meta"><b>Journal d'audit</b>
            <small>Toutes les actions sont tracées, jamais les secrets.</small></div>
            <span class="tag cy">${sec.audit_entries ?? 0} entrées</span></div>
          <div class="list-row"><div class="meta"><b>Confirmations en attente</b>
            <small>Actions sensibles nécessitant ton accord</small></div>
            <span class="tag ${(sec.pending_confirmations || []).length ? 'warn' : ''}">${(sec.pending_confirmations || []).length}</span></div>
        </div>
        <div class="sep"></div>
        <button class="btn sm" id="openAudit">${icon('eye', 12)} Consulter le journal</button>
      </div></div>
      <div class="card"><div class="card-head"><h2>ENVIRONNEMENT</h2></div><div class="card-body">
        <div class="list">
          ${[['Plateforme', env.platform], ['Python', env.python], ['Données', env.data_dir],
             ['OpenCode', env.opencode ? 'installé' : 'absent'],
             ['Cursor', env.cursor ? 'installé' : 'absent'],
             ['Chrome', env.chrome ? 'installé' : 'absent'],
             ['Docker', env.docker ? 'installé' : 'absent'],
             ['rsync', env.rsync ? 'installé' : 'absent']]
            .map(([k, v]) => `<div class="list-row"><div class="meta"><b>${k}</b>
              <small class="mono">${esc(v ?? '—')}</small></div></div>`).join('')}
        </div></div></div>
      <div class="card"><div class="card-head"><h2>OUTILS PAR CATÉGORIE</h2></div><div class="card-body">
        <div id="coreToolCats" class="list"></div></div></div>
    </div>`;

  const providers = (s.llm || []);
  $('#coreProviders', el).innerHTML = providers.map((p) => `
    <div class="list-row"><div class="meta"><b>${esc(p.name)}</b>
      <small>${esc(p.detail)}${p.default_model ? ' · ' + esc(p.default_model) : ''}</small></div>
      <span class="tag ${p.connected ? 'ok' : ''}">${p.connected ? 'Connecté' : 'Non connecté'}</span></div>`).join('')
    || '<div class="empty">Aucun fournisseur configuré.</div>';

  const tools = await J.get('/api/tools');
  const byCat = {};
  (tools.tools || []).forEach((t) => { (byCat[t.category] = byCat[t.category] || []).push(t); });
  $('#coreToolCats', el).innerHTML = Object.entries(byCat).map(([cat, list]) => `
    <div class="list-row"><div class="meta"><b>${esc(cat)}</b>
      <small>${list.filter((t) => t.status === 'ready').length}/${list.length} prêts</small></div>
      <span class="tag cy">${list.length}</span></div>`).join('');

  $('#coreBackup', el).onclick = async () => {
    const res = await J.post('/api/system/backup');
    toast(res.ok ? 'Sauvegarde créée.' : 'Sauvegarde impossible.', res.ok ? 'ok' : 'err');
  };
  $('#coreTestAll', el).onclick = async () => {
    toast('Test de tous les connecteurs…');
    for (const c of J.state.connectors) await J.post(`/api/connectors/${c.id}/test`);
    toast('Tests terminés.', 'ok');
  };
  $('#openAudit', el).onclick = () => Pages.showAudit();
};

Pages.showAudit = async function () {
  const res = await J.get('/api/audit?limit=150');
  const rows = (res.entries || []).map((e) => `
    <div class="list-row"><div class="meta">
      <b>${esc(e.action)}</b>
      <small>${fmtDateTime(e.ts)} · ${esc(e.agent || '—')} · ${esc(e.tool || '—')}
        ${e.connector_id ? '· ' + esc(e.connector_id) : ''} · ${e.duration_ms || 0} ms</small>
    </div><span class="tag ${e.status === 'ok' ? 'ok' : e.status === 'denied' ? 'warn' : 'err'}">${esc(e.status)}</span></div>`).join('');
  modal({
    title: `Journal d'audit — ${res.total} entrées`, wide: true,
    body: `<div class="list" style="max-height:60vh">${rows || '<div class="empty">Aucune entrée.</div>'}</div>`,
    footer: '<button class="btn" data-close>Fermer</button>',
  });
};

/* --------------------------------------------------------------- AGENTS */
Pages.agents = async function (el) {
  const res = await J.get('/api/agents');
  const agents = res.agents || [];
  el.innerHTML = `
    <div class="page-head"><div><h1>Agents</h1>
      <p>JARVIS Core orchestre ; les agents spécialisés exécutent.</p></div></div>
    <div class="grid-3">
      ${agents.map((a) => `
        <div class="card"><div class="card-head">
          <h2>${esc(a.name.toUpperCase())}</h2>
          <span class="tag ${a.status === 'active' ? 'ok' : a.status === 'error' ? 'err' : ''}">${esc(a.status)}</span>
        </div><div class="card-body">
          <p class="text-dim" style="margin:0 0 8px;font-size:11px">${esc(a.role)}</p>
          <div class="list" style="gap:4px">
            <div class="list-row" style="padding:6px 8px"><div class="meta">
              <small>Dernière activité</small><b style="font-size:11px">${fmtAgo(a.last_activity_at)}</b></div></div>
            <div class="list-row" style="padding:6px 8px"><div class="meta">
              <small>Exécutions</small><b style="font-size:11px">${a.runs || 0}</b></div></div>
            ${a.current_action ? `<div class="list-row" style="padding:6px 8px"><div class="meta">
              <small>En cours</small><b style="font-size:11px">${esc(a.current_action)}</b></div></div>` : ''}
            ${a.last_error ? `<div class="list-row" style="padding:6px 8px;border-color:rgba(251,113,133,.3)">
              <div class="meta"><small>Dernière erreur</small>
              <b style="font-size:10px;color:var(--danger)">${esc(a.last_error.slice(0, 90))}</b></div></div>` : ''}
          </div>
          <div class="sep"></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${a.id !== 'jarvis' ? `<button class="btn sm" data-run-agent="${esc(a.id)}">${icon('play', 11)} Lancer</button>` : ''}
            <button class="btn sm" data-toggle-agent="${esc(a.id)}">${a.enabled ? 'Désactiver' : 'Activer'}</button>
          </div>
          <div class="text-faint" style="font-size:9px;margin-top:7px">
            Outils : ${(a.tool_prefixes || []).join(', ') || 'tous'}</div>
        </div></div>`).join('')}
    </div>`;

  $$('[data-run-agent]', el).forEach((b) => b.onclick = () => {
    const id = b.dataset.runAgent;
    const m = modal({
      title: `Lancer ${id}`,
      body: `<div class="field"><label>Instruction</label>
             <textarea id="agentInstruction" placeholder="Ex. vérifie l'état du serveur de production"></textarea></div>`,
      footer: `<button class="btn" data-close>Annuler</button>
               <button class="btn primary" data-go>Lancer</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      const instruction = m.$('#agentInstruction').value.trim();
      if (!instruction) return;
      m.close();
      toast(`${id} en cours…`);
      const res2 = await J.post(`/api/agents/${id}/run`, { instruction });
      App.pushMessage('jarvis', res2.output || res2.error || 'Terminé.');
      App.openConsole();
      Pages.render('agents');
    };
  });
  $$('[data-toggle-agent]', el).forEach((b) => b.onclick = async () => {
    const id = b.dataset.toggleAgent;
    const agent = agents.find((a) => a.id === id);
    await J.post(`/api/agents/${id}/toggle`, { enabled: !agent.enabled });
    Pages.render('agents');
  });
};

/* ---------------------------------------------------------------- TASKS */
Pages.tasks = async function (el) {
  const res = await J.get('/api/tasks?limit=80');
  const tasks = res.tasks || [];
  const stats = res.stats || {};
  const statusTag = { completed: 'ok', running: 'cy', planning: 'cy', queued: '',
    failed: 'err', cancelled: '', waiting_confirmation: 'warn' };
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Tasks</h1><p>${stats.active || 0} active(s) · ${stats.completed || 0} terminée(s) · ${stats.failed || 0} en échec</p></div>
      <div class="page-actions"><button class="btn primary" id="newTask">${icon('plus', 13)} Nouvelle tâche</button></div>
    </div>
    <div class="card"><div class="card-body">
      <div class="list">${tasks.map((t) => `
        <div class="list-row" data-task="${esc(t.id)}" style="cursor:pointer">
          <div class="meta">
            <b>${esc(t.name)}</b>
            <small>${esc(t.agent)} · ${fmtAgo(t.created_at)} ${t.tools?.length ? '· ' + t.tools.length + ' outil(s)' : ''}</small>
            ${['running', 'planning'].includes(t.status)
              ? `<div class="progress"><i style="width:${Math.round((t.progress || 0) * 100)}%"></i></div>` : ''}
          </div>
          <span class="tag ${statusTag[t.status] || ''}">${esc(t.status)}</span>
          <div class="acts">
            ${['running', 'planning', 'queued'].includes(t.status)
              ? `<button class="btn sm danger" data-cancel="${esc(t.id)}">Annuler</button>` : ''}
          </div>
        </div>`).join('') || '<div class="empty"><b>Aucune tâche</b>Demande quelque chose à JARVIS.</div>'}
      </div></div></div>`;

  $$('[data-task]', el).forEach((row) => row.onclick = (e) => {
    if (e.target.closest('[data-cancel]')) return;
    Pages.showTask(row.dataset.task);
  });
  $$('[data-cancel]', el).forEach((b) => b.onclick = async (e) => {
    e.stopPropagation();
    await J.post(`/api/tasks/${b.dataset.cancel}/cancel`);
    Pages.render('tasks');
  });
  $('#newTask', el).onclick = () => App.newTaskDialog();
};

/** Execution Panel : plan, agents, outils, logs, résultat. */
Pages.showTask = async function (taskId) {
  const res = await J.get(`/api/tasks/${taskId}`);
  if (!res.ok) return toast('Tâche introuvable.', 'err');
  const t = res.task;
  const logs = (t.logs || []).map((l) => `
    <div class="exec-step ${l.level === 'tool' ? 'tool' : l.level === 'error' ? 'error' : l.level === 'thought' ? 'thought' : ''}">
      <div class="k">${fmtTime(l.ts)} · ${esc(l.level)}</div>
      <div class="v">${esc(l.message)}</div>
    </div>`).join('');
  modal({
    title: t.name, wide: true,
    body: `
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <span class="tag ${t.status === 'completed' ? 'ok' : t.status === 'failed' ? 'err' : 'cy'}">${esc(t.status)}</span>
        <span class="tag">Agent ${esc(t.agent)}</span>
        <span class="tag">${Math.round((t.progress || 0) * 100)}%</span>
        ${(t.tools || []).map((x) => `<span class="tag cy">${esc(x)}</span>`).join('')}
      </div>
      ${t.result ? `<div class="field"><label>RÉSULTAT</label>
        <div class="mono" style="white-space:pre-wrap;padding:9px;border:1px solid var(--line-soft);
        border-radius:7px;max-height:180px;overflow:auto">${esc(t.result)}</div></div>` : ''}
      ${t.error ? `<div class="risk-banner destructive">${icon('alert', 15)}<div>${esc(t.error)}</div></div>` : ''}
      <div class="field"><label>ACTIVITÉ (${(t.logs || []).length})</label>
        <div class="exec-steps" style="max-height:300px;overflow:auto">${logs || '<div class="empty">Aucun log.</div>'}</div>
      </div>`,
    footer: '<button class="btn" data-close>Fermer</button>',
  });
};

/* ------------------------------------------------------------- CALENDAR */
Pages.calendar = async function (el) {
  const res = await J.get('/api/calendar?days=30');
  const events = res.events || [];
  const byDay = {};
  events.forEach((e) => {
    const key = new Date(e.start_at * 1000).toLocaleDateString('fr-FR',
      { weekday: 'long', day: 'numeric', month: 'long' });
    (byDay[key] = byDay[key] || []).push(e);
  });
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Calendar</h1><p>${events.length} événement(s) à venir · agenda local + Google si connecté</p></div>
      <div class="page-actions">
        <button class="btn" id="syncGoogle">${icon('google', 13)} Synchroniser Google</button>
        <button class="btn primary" id="addEvent">${icon('plus', 13)} Ajouter</button>
      </div>
    </div>
    ${Object.keys(byDay).length ? Object.entries(byDay).map(([day, list]) => `
      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>${esc(day.toUpperCase())}</h2></div>
      <div class="card-body"><div class="list">
        ${list.map((e) => `<div class="list-row"><div class="meta">
          <b>${esc(e.title)}</b><small>${fmtTime(e.start_at)}${e.end_at ? ' – ' + fmtTime(e.end_at) : ''}
          · ${esc(e.source)}</small></div>
          <div class="acts"><button class="btn sm danger" data-del-event="${esc(e.id)}">${icon('trash', 11)}</button></div>
        </div>`).join('')}
      </div></div></div>`).join('')
      : '<div class="card"><div class="card-body"><div class="empty"><b>Agenda vide</b>Ajoute un événement ou connecte Google Agenda.</div></div></div>'}`;

  $('#addEvent', el).onclick = () => {
    const m = modal({
      title: 'Nouvel événement',
      body: `<div class="field"><label>Titre</label><input id="evTitle"/></div>
             <div class="field"><label>Quand</label><input id="evWhen" placeholder="demain 14h · lundi 9h30 · 2026-09-15 08:00"/></div>
             <div class="field"><label>Durée (min)</label><input id="evDur" type="number" value="60"/></div>`,
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Ajouter</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      const r = await J.post('/api/calendar', {
        title: m.$('#evTitle').value, start: m.$('#evWhen').value,
        duration_min: Number(m.$('#evDur').value || 60),
      });
      if (r.ok) { m.close(); Pages.render('calendar'); toast('Événement ajouté.', 'ok'); }
      else toast(r.error || 'Date non comprise.', 'err');
    };
  };
  $('#syncGoogle', el).onclick = async () => {
    const r = await J.post('/api/tools/google.calendar/run', { arguments: { days: 30 } });
    const result = r.result || {};
    toast(result.ok ? 'Agenda Google synchronisé.' : (result.output || 'Google non connecté.'),
      result.ok ? 'ok' : 'err');
    if (result.ok) Pages.render('calendar');
  };
  $$('[data-del-event]', el).forEach((b) => b.onclick = async () => {
    await J.del(`/api/calendar/${b.dataset.delEvent}`);
    Pages.render('calendar');
  });
};

/* --------------------------------------------------------------- MEMORY */
Pages.memory = async function (el) {
  const res = await J.get('/api/memory?limit=200');
  const stats = res.stats || {};
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Memory</h1><p>${stats.total || 0} souvenirs · ${stats.pinned || 0} épinglés ·
        ${stats.knowledge || 0} fiches · ${stats.embedded || 0} vectorisés</p></div>
      <div class="page-actions">
        <button class="btn" id="memGraph">${icon('core', 13)} Memory Map</button>
        <button class="btn primary" id="memAdd">${icon('plus', 13)} Nouveau souvenir</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:11px"><div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
      <input id="memSearch" placeholder="Rechercher dans la mémoire…" style="flex:1;min-width:200px;
        padding:8px 11px;border-radius:7px;border:1px solid var(--line);background:rgba(5,14,28,.8);outline:none"/>
      <select id="memScope" style="padding:8px 11px;border-radius:7px;border:1px solid var(--line);
        background:rgba(5,14,28,.8);outline:none">
        <option value="">Toutes les portées</option>
        <option value="user">User</option><option value="project">Project</option>
        <option value="conversation">Conversation</option><option value="task">Task</option>
      </select>
    </div></div>
    <div class="card"><div class="card-body"><div class="list" id="memList"></div></div></div>`;

  const draw = (items) => {
    $('#memList', el).innerHTML = items.map((m) => `
      <div class="list-row">
        <div class="meta">
          <b>${esc(m.content)}</b>
          <small>${esc(m.scope)} · importance ${m.importance} · ${esc(m.source || 'manuel')} ·
            ${fmtAgo(m.created_at)}${m.tags?.length ? ' · ' + m.tags.map(esc).join(', ') : ''}</small>
        </div>
        <div class="acts">
          <button class="btn sm" data-pin="${esc(m.id)}" title="Épingler">${m.pinned ? '📌' : icon('pin', 11)}</button>
          <button class="btn sm danger" data-del="${esc(m.id)}">${icon('trash', 11)}</button>
        </div>
      </div>`).join('') || '<div class="empty"><b>Mémoire vide</b>Dis « retiens que… » à JARVIS.</div>';
    $$('[data-del]', el).forEach((b) => b.onclick = async () => {
      await J.del(`/api/memory/${b.dataset.del}`);
      Pages.render('memory');
    });
    $$('[data-pin]', el).forEach((b) => b.onclick = async () => {
      const item = items.find((x) => x.id === b.dataset.pin);
      await J.put(`/api/memory/${b.dataset.pin}`, { pinned: !item.pinned });
      Pages.render('memory');
    });
  };
  draw(res.memories || []);

  const search = async () => {
    const r = await J.get(`/api/memory?limit=200&search=${encodeURIComponent($('#memSearch', el).value)}`
      + `&scope=${$('#memScope', el).value}`);
    draw(r.memories || []);
  };
  let timer;
  $('#memSearch', el).oninput = () => { clearTimeout(timer); timer = setTimeout(search, 260); };
  $('#memScope', el).onchange = search;
  $('#memAdd', el).onclick = () => {
    const m = modal({
      title: 'Nouveau souvenir',
      body: `<div class="field"><label>Contenu</label><textarea id="mContent"></textarea></div>
             <div class="field"><label>Portée</label><select id="mScope">
               <option value="user">User</option><option value="project">Project</option>
               <option value="conversation">Conversation</option><option value="task">Task</option></select></div>
             <div class="field"><label>Importance (1-5)</label><input id="mImp" type="number" min="1" max="5" value="3"/></div>`,
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Enregistrer</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      await J.post('/api/memory', { content: m.$('#mContent').value,
        scope: m.$('#mScope').value, importance: Number(m.$('#mImp').value) });
      m.close(); Pages.render('memory');
    };
  };
  $('#memGraph', el).onclick = async () => {
    const g = await J.get('/api/memory/graph');
    const nodes = g.graph?.nodes || [];
    modal({
      title: `Memory Map — ${nodes.length} nœuds, ${(g.graph?.edges || []).length} relations`, wide: true,
      body: `<canvas id="graphCanvas" width="820" height="440" style="width:100%;border:1px solid var(--line-soft);border-radius:8px"></canvas>`,
      footer: '<button class="btn" data-close>Fermer</button>',
    });
    Pages.drawGraph(g.graph);
  };
};

Pages.drawGraph = function (graph) {
  const canvas = $('#graphCanvas');
  if (!canvas || !graph) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const nodes = graph.nodes.map((n, i) => {
    const angle = (i / Math.max(1, graph.nodes.length)) * Math.PI * 2;
    const radius = 60 + (i % 5) * 34;
    return { ...n, x: w / 2 + Math.cos(angle) * radius * 1.6, y: h / 2 + Math.sin(angle) * radius };
  });
  const index = Object.fromEntries(nodes.map((n) => [n.id, n]));
  ctx.clearRect(0, 0, w, h);
  graph.edges.forEach((e) => {
    const a = index[e.source];
    const b = index[e.target];
    if (!a || !b) return;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.strokeStyle = 'rgba(34,211,238,.16)';
    ctx.stroke();
  });
  const colors = { user: '#22d3ee', project: '#4ade80', conversation: '#a78bfa', task: '#fbbf24' };
  nodes.forEach((n) => {
    const r = 3 + n.importance * 1.5;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle = colors[n.scope] || '#22d3ee';
    ctx.globalAlpha = n.pinned ? 1 : 0.7;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = 'rgba(190,220,240,.6)';
    ctx.font = '9px system-ui';
    ctx.fillText(n.label.slice(0, 26), n.x + r + 3, n.y + 3);
  });
};

/* -------------------------------------------------------- CONVERSATIONS */
Pages.conversations = async function (el) {
  const res = await J.get('/api/conversations?limit=60');
  const list = res.conversations || [];
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Conversations</h1><p>${list.length} conversation(s) — contexte, tâches et souvenirs conservés</p></div>
      <div class="page-actions"><button class="btn primary" id="newConv">${icon('plus', 13)} Nouvelle</button></div>
    </div>
    <div class="card"><div class="card-body"><div class="list">
      ${list.map((c) => `
        <div class="list-row" data-conv="${esc(c.id)}" style="cursor:pointer">
          <div class="meta"><b>${esc(c.title)}</b>
            <small>${c.message_count} message(s) · ${c.task_count} tâche(s) · ${c.memory_count} souvenir(s)
              · ${fmtAgo(c.updated_at)}</small>
            ${c.preview ? `<small class="text-faint">${esc(c.preview)}</small>` : ''}</div>
          ${c.id === res.current ? '<span class="tag cy">Active</span>' : ''}
          <div class="acts">
            <button class="btn sm" data-continue="${esc(c.id)}">Continuer</button>
            <button class="btn sm" data-rename="${esc(c.id)}">${icon('edit', 11)}</button>
            <button class="btn sm danger" data-del-conv="${esc(c.id)}">${icon('trash', 11)}</button>
          </div>
        </div>`).join('') || '<div class="empty"><b>Aucune conversation</b>Parle à JARVIS pour commencer.</div>'}
    </div></div></div>`;

  $$('[data-conv]', el).forEach((row) => row.onclick = (e) => {
    if (e.target.closest('button')) return;
    Pages.showConversation(row.dataset.conv);
  });
  $$('[data-continue]', el).forEach((b) => b.onclick = async () => {
    await J.put(`/api/conversations/${b.dataset.continue}`, { current: true });
    J.state.conversation = b.dataset.continue;
    await App.loadConversation();
    App.openConsole();
    toast('Conversation reprise.', 'ok');
  });
  $$('[data-rename]', el).forEach((b) => b.onclick = async () => {
    const title = prompt('Nouveau titre :');
    if (!title) return;
    await J.put(`/api/conversations/${b.dataset.rename}`, { title });
    Pages.render('conversations');
  });
  $$('[data-del-conv]', el).forEach((b) => b.onclick = async () => {
    if (!await confirmDialog('Supprimer', 'Cette conversation et ses messages seront supprimés.', { danger: true })) return;
    await J.del(`/api/conversations/${b.dataset.delConv}`);
    Pages.render('conversations');
  });
  $('#newConv', el).onclick = async () => {
    const r = await J.post('/api/conversations');
    J.state.conversation = r.conversation.id;
    $('#consoleLog').innerHTML = '';
    App.openConsole();
    Pages.render('conversations');
  };
};

Pages.showConversation = async function (cid) {
  const res = await J.get(`/api/conversations/${cid}?limit=200`);
  const conv = res.conversation;
  const messages = (conv.messages || []).map((m) => `
    <div class="msg ${m.role}"><div class="who">${m.role === 'user' ? 'VOUS' : 'JARVIS'} · ${fmtTime(m.created_at)}</div>
      <div class="bubble">${esc(m.content)}</div></div>`).join('');
  modal({
    title: conv.title, wide: true,
    body: `<div style="display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap">
        <span class="tag">${conv.message_count} messages</span>
        <span class="tag cy">${conv.task_count} tâches</span>
        <span class="tag">${conv.memory_count} souvenirs</span></div>
      <div style="max-height:56vh;overflow:auto;display:flex;flex-direction:column;gap:10px">${messages}</div>`,
    footer: '<button class="btn" data-close>Fermer</button>',
  });
};

/* ------------------------------------------------------- KNOWLEDGE BASE */
Pages.knowledge = async function (el) {
  const res = await J.get('/api/knowledge');
  const items = res.items || [];
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Knowledge Base</h1><p>${items.length} fiche(s) — procédures, documentation, notes durables</p></div>
      <div class="page-actions"><button class="btn primary" id="kbAdd">${icon('plus', 13)} Nouvelle fiche</button></div>
    </div>
    <div class="card" style="margin-bottom:11px"><div class="card-body">
      <input id="kbSearch" placeholder="Rechercher…" style="width:100%;padding:8px 11px;border-radius:7px;
        border:1px solid var(--line);background:rgba(5,14,28,.8);outline:none"/></div></div>
    <div class="grid-2" id="kbGrid"></div>`;

  const draw = (list) => {
    $('#kbGrid', el).innerHTML = list.map((k) => `
      <div class="card"><div class="card-head"><h2>${esc(k.title.toUpperCase())}</h2>
        <span class="tag">${esc(k.kind)}</span></div>
      <div class="card-body">
        <p style="margin:0;font-size:11.5px;color:var(--text-dim);max-height:110px;overflow:hidden">${esc(k.content.slice(0, 380))}</p>
        <div class="sep"></div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <small class="text-faint">${fmtAgo(k.updated_at)}${k.project ? ' · ' + esc(k.project) : ''}</small>
          <div style="display:flex;gap:5px">
            <button class="btn sm" data-kb-view="${esc(k.id)}">Ouvrir</button>
            <button class="btn sm danger" data-kb-del="${esc(k.id)}">${icon('trash', 11)}</button>
          </div>
        </div>
      </div></div>`).join('')
      || '<div class="card"><div class="card-body"><div class="empty"><b>Base vide</b>Ajoute une procédure ou demande à JARVIS de documenter quelque chose.</div></div></div>';
    $$('[data-kb-del]', el).forEach((b) => b.onclick = async () => {
      await J.del(`/api/knowledge/${b.dataset.kbDel}`);
      Pages.render('knowledge');
    });
    $$('[data-kb-view]', el).forEach((b) => b.onclick = () => {
      const k = list.find((x) => x.id === b.dataset.kbView);
      modal({ title: k.title, wide: true,
        body: `<div style="white-space:pre-wrap;font-size:12px;line-height:1.6;max-height:60vh;overflow:auto">${esc(k.content)}</div>`,
        footer: '<button class="btn" data-close>Fermer</button>' });
    });
  };
  draw(items);

  let timer;
  $('#kbSearch', el).oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const r = await J.get(`/api/knowledge?search=${encodeURIComponent($('#kbSearch', el).value)}`);
      draw(r.items || []);
    }, 260);
  };
  $('#kbAdd', el).onclick = () => {
    const m = modal({
      title: 'Nouvelle fiche', wide: true,
      body: `<div class="field"><label>Titre</label><input id="kTitle"/></div>
             <div class="field"><label>Type</label><input id="kKind" value="note" placeholder="note · procédure · doc"/></div>
             <div class="field"><label>Contenu</label><textarea id="kContent" style="min-height:180px"></textarea></div>`,
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Enregistrer</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      await J.post('/api/knowledge', { title: m.$('#kTitle').value,
        content: m.$('#kContent').value, kind: m.$('#kKind').value });
      m.close(); Pages.render('knowledge');
    };
  };
};

/* -------------------------------------------------------- TOOLS & SKILLS */
Pages.tools = async function (el) {
  const res = await J.get('/api/tools');
  const tools = res.tools || [];
  const byCat = {};
  tools.forEach((t) => { (byCat[t.category] = byCat[t.category] || []).push(t); });
  const riskTag = { read_only: '', safe_write: 'cy', sensitive: 'warn', destructive: 'err' };
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Tools &amp; Skills</h1>
        <p>${tools.length} outils · ${tools.filter((t) => t.status === 'ready').length} prêts ·
           ${tools.filter((t) => t.status === 'not_configured').length} en attente de connecteur</p></div>
    </div>
    ${Object.entries(byCat).map(([cat, list]) => `
      <div class="card" style="margin-bottom:11px"><div class="card-head"><h2>${esc(cat.toUpperCase())}</h2>
        <span class="tools">${list.length}</span></div>
      <div class="card-body"><div class="list">
        ${list.map((t) => `<div class="list-row">
          <div class="meta"><b>${esc(t.name)} <span class="mono text-faint">${esc(t.id)}</span></b>
            <small>${esc(t.description)}</small>
            <small class="text-faint">${t.connector_type ? 'connecteur : ' + esc(t.connector_type) + ' · ' : ''}
              permissions : ${(t.permissions || []).join(', ')}</small></div>
          <span class="tag ${riskTag[t.risk] || ''}">${esc(t.risk_label)}</span>
          <span class="tag ${t.status === 'ready' ? 'ok' : t.status === 'not_configured' ? 'warn' : ''}">
            ${t.status === 'ready' ? 'Prêt' : t.status === 'not_configured' ? 'Non configuré' : 'Désactivé'}</span>
          <div class="acts">
            <button class="btn sm" data-tool-run="${esc(t.id)}">${icon('play', 11)}</button>
            <button class="btn sm" data-tool-toggle="${esc(t.id)}">${t.enabled ? 'Off' : 'On'}</button>
          </div></div>`).join('')}
      </div></div></div>`).join('')}`;

  $$('[data-tool-toggle]', el).forEach((b) => b.onclick = async () => {
    const tool = tools.find((t) => t.id === b.dataset.toolToggle);
    await J.post(`/api/tools/${tool.id}/toggle`, { enabled: !tool.enabled });
    Pages.render('tools');
  });
  $$('[data-tool-run]', el).forEach((b) => b.onclick = () => {
    const tool = tools.find((t) => t.id === b.dataset.toolRun);
    const props = tool.input_schema?.properties || {};
    const fields = Object.entries(props).map(([key, spec]) => `
      <div class="field"><label>${esc(key)}${(tool.input_schema.required || []).includes(key) ? ' *' : ''}</label>
        ${spec.enum ? `<select data-arg="${esc(key)}">${spec.enum.map((o) => `<option>${esc(o)}</option>`).join('')}</select>`
        : `<input data-arg="${esc(key)}" placeholder="${esc(spec.description || spec.type || '')}"/>`}
        ${spec.description ? `<div class="hint">${esc(spec.description)}</div>` : ''}</div>`).join('');
    const m = modal({
      title: `${tool.name} — ${tool.risk_label}`,
      body: (fields || '<p class="text-dim">Cet outil ne prend aucun paramètre.</p>')
        + (tool.connector_type ? `<div class="hint">Connecteurs disponibles : ${
          J.state.connectors.filter((c) => c.type === tool.connector_type).map((c) => c.id).join(', ') || 'aucun'}</div>` : ''),
      footer: `<button class="btn" data-close>Annuler</button><button class="btn primary" data-go>Exécuter</button>`,
    });
    m.$('[data-go]').onclick = async () => {
      const args = {};
      m.$$('[data-arg]').forEach((input) => {
        if (input.value.trim()) {
          const spec = props[input.dataset.arg] || {};
          args[input.dataset.arg] = spec.type === 'integer' ? Number(input.value) : input.value;
        }
      });
      m.close();
      const r = await J.post(`/api/tools/${tool.id}/run`, { arguments: args });
      if (r.needs_confirmation) return App.showConfirmation({ ...r.needs_confirmation, message: r.message });
      const result = r.result || {};
      App.pushMessage('jarvis', result.output || 'Terminé.');
      App.openConsole();
    };
  });
};

/* ------------------------------------------------------------ WORKFLOWS */
Pages.workflows = async function (el) {
  const res = await J.get('/api/workflows');
  const workflows = res.workflows || [];
  const runs = res.runs || [];
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Workflows</h1><p>${workflows.length} automatisation(s) ·
        ${workflows.filter((w) => w.enabled).length} active(s)</p></div>
      <div class="page-actions"><button class="btn primary" id="wfAdd">${icon('plus', 13)} Nouvelle automatisation</button></div>
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>AUTOMATISATIONS</h2></div><div class="card-body">
        <div class="list">${workflows.map((w) => `
          <div class="list-row"><div class="meta">
            <b>${esc(w.name)}</b>
            <small>${esc(w.description || (w.steps?.[0]?.instruction || '').slice(0, 90))}</small>
            <small class="text-faint">${esc(Pages.triggerLabel(w.trigger))}
              ${w.next_run_at ? '· prochaine : ' + fmtDateTime(w.next_run_at) : ''}
              ${w.run_count ? '· ' + w.run_count + ' exécution(s)' : ''}</small>
          </div>
          <span class="tag ${w.last_status === 'success' ? 'ok' : w.last_status === 'failed' ? 'err' : ''}">
            ${w.enabled ? 'Actif' : 'Inactif'}</span>
          <div class="acts">
            <button class="btn sm" data-wf-run="${esc(w.id)}">${icon('play', 11)}</button>
            <button class="btn sm" data-wf-toggle="${esc(w.id)}">${w.enabled ? 'Off' : 'On'}</button>
            <button class="btn sm danger" data-wf-del="${esc(w.id)}">${icon('trash', 11)}</button>
          </div></div>`).join('')
          || '<div class="empty"><b>Aucune automatisation</b>Dis à JARVIS : « vérifie mon site chaque matin ».</div>'}
        </div></div></div>
      <div class="card"><div class="card-head"><h2>DERNIÈRES EXÉCUTIONS</h2></div><div class="card-body">
        <div class="list">${runs.map((r) => `
          <div class="list-row"><div class="meta">
            <b>${esc((workflows.find((w) => w.id === r.workflow_id) || {}).name || r.workflow_id)}</b>
            <small>${fmtDateTime(r.started_at)}${r.finished_at ? ' · ' + Math.round(r.finished_at - r.started_at) + 's' : ''}</small>
          </div><span class="tag ${r.status === 'success' ? 'ok' : r.status === 'failed' ? 'err' : 'cy'}">${esc(r.status)}</span>
          </div>`).join('') || '<div class="empty">Aucune exécution.</div>'}
        </div></div></div>
    </div>`;

  $$('[data-wf-run]', el).forEach((b) => b.onclick = async () => {
    toast('Exécution en cours…');
    const r = await J.post(`/api/workflows/${b.dataset.wfRun}/run`);
    toast(r.ok ? 'Automatisation terminée.' : 'Échec.', r.ok ? 'ok' : 'err');
    Pages.render('workflows');
  });
  $$('[data-wf-toggle]', el).forEach((b) => b.onclick = async () => {
    const w = workflows.find((x) => x.id === b.dataset.wfToggle);
    await J.post(`/api/workflows/${w.id}/toggle`, { enabled: !w.enabled });
    Pages.render('workflows');
  });
  $$('[data-wf-del]', el).forEach((b) => b.onclick = async () => {
    if (!await confirmDialog('Supprimer', 'Cette automatisation sera supprimée.', { danger: true })) return;
    await J.del(`/api/workflows/${b.dataset.wfDel}`);
    Pages.render('workflows');
  });
  $('#wfAdd', el).onclick = () => App.newWorkflowDialog();
};

Pages.triggerLabel = function (trigger) {
  if (!trigger) return 'manuel';
  const t = trigger.type;
  if (t === 'daily') return `chaque jour à ${String(trigger.hour ?? 8).padStart(2, '0')}h${String(trigger.minute ?? 0).padStart(2, '0')}`;
  if (t === 'weekly') {
    const days = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];
    return `chaque ${days[trigger.weekday ?? 0]} à ${String(trigger.hour ?? 8).padStart(2, '0')}h`;
  }
  if (t === 'interval') {
    const s = trigger.seconds || 3600;
    return s < 3600 ? `toutes les ${Math.round(s / 60)} min` : `toutes les ${Math.round(s / 3600)} h`;
  }
  if (t === 'cron') return `cron ${trigger.expression}`;
  if (t === 'webhook') return 'sur webhook';
  if (t === 'event') return `sur événement ${trigger.event}`;
  if (t === 'condition') return `si ${trigger.check}`;
  return 'manuel';
};

Pages.learning = async function (el) {
  const res = await J.get('/api/learning');
  const x = res.learning || {};
  const cur = x.current || {};
  const usage = x.tool_usage || [];
  const queue = x.queue || [];
  const sessions = x.sessions || [];
  const pending = queue.length;
  const currentResult = cur.result || {};
  const safeRes = (r) => { try { return JSON.parse(r || '{}'); } catch (e) { return {}; } };
  const compLabel = {
    documentation: 'Documentation', knowledge: 'Savoir validé', usage: 'Usage réussi',
    error_recovery: 'Recovery erreurs', freshness: 'Fraîcheur', workflows: 'Workflows testés'
  };
  const kindTag = (k) => k === 'error_fix' ? 'warn' : (k === 'documentation' ? 'cy' : 'cy');
  const expTone = (v) => v >= 70 ? 'ok' : (v >= 40 ? 'cy' : 'warn');

  const cards = `
    <div class="card"><div class="card-kicker">ÉTAT</div><strong>${esc(x.state)}</strong>
      <p>${x.idle_s || 0}s d'inactivité · <span style="opacity:.55">dernière activité ${fmtAgo(x.last_activity_at)}</span></p></div>
    <div class="card"><div class="card-kicker">SESSION COURANTE</div>
      <strong>${cur.topic ? esc(cur.topic) : 'Aucune'}</strong>
      <p>${cur.tool_id ? `<span class="mono">${esc(cur.tool_id)}</span> · ` : ''}${cur.topic ? Math.round((cur.progress || 0) * 100) + '%' : ''}</p>
      ${cur.url ? `<p class="sub">Source : ${cur.url.startsWith('registry:') ? 'registre interne' : esc(cur.url)}</p>` : ''}
      ${cur.progress ? `<div class="progress"><i style="width:${Math.round((cur.progress || 0) * 100)}%"></i></div>` : ''}</div>
      ${currentResult && cur.progress >= 1 ? `<div class="card"><div class="card-kicker">RÉSULTAT</div>
        <strong>${currentResult.knowledge_created || 0} knowledge créée(s)</strong>
        <p>${currentResult.knowledge_updated || 0} mise(s) à jour · ${currentResult.tests_passed || 0}/${currentResult.tests_run || 0} test(s) validé(s)</p>
        <p class="sub">Expertise : ${currentResult.expertise_before || 0}% → ${currentResult.expertise_after || 0}%</p></div>` : ''}
    <div class="card"><div class="card-kicker">OUTILS SUIVIS</div><strong>${usage.length}</strong>
      <p>priorités calculées sur l'usage réel</p></div>
    <div class="card"><div class="card-kicker">FILE D'ATTENTE</div><strong>${pending}</strong>
      <p>sujets programmés avant le curriculum</p></div>`;

  const rows = usage.map((u, i) => {
    const aliases = (u.aliases || []).filter(a => a && a !== u.tool_id);
    const parts = [];
    for (const name of Object.keys(compLabel)) {
      const c = (u.expertise_components || {})[name] || { score: 0, weight: 0, weighted: 0 };
      parts.push(`<tr class="sub detail-row"><td>${esc(compLabel[name])}</td><td>${c.score}</td><td>${Math.round((c.weight || 0) * 100)}%</td><td>+${c.weighted}</td></tr>`);
    }
    const knowledge = (u.knowledge || []).map(k => `<li><b>${esc(k.title || k.kind || 'Knowledge')}</b>
      <span class="sub">${esc(k.kind || '')} · ${k.verification_method ? 'validée' : 'non vérifiée'} · confiance ${Math.round((k.confidence_score || 0) * 100)}%</span></li>`).join('');
    const errors = Object.entries(u.error_categories || {}).map(([k, v]) => `${esc(k)} (${v})`).join(' · ') || 'Aucune erreur connue';
    const detail = parts.join('') + `<tr class="sub"><td>Erreurs connues</td><td colspan="3">${errors}</td></tr>
      <tr class="sub"><td>Sources / connaissances</td><td colspan="3"><ul class="sub" style="margin:0;padding-left:18px">${knowledge || '<li>Aucune knowledge liée</li>'}</ul></td></tr>`;
    const lastErr = u.last_failure_error ? `<span class="tag err">${esc(String(u.last_failure_error).slice(0, 42))}</span>` : '';
    const errCell = u.failed_calls > 0
      ? `<span class="tag warn">${u.failed_calls} échec${u.failed_calls > 1 ? 's' : ''}</span>${lastErr}`
      : '—';
    return `<tr>
      <td><div><b>${esc(u.display_name || u.tool_id)}</b></div>
        <div class="sub mono">${esc(u.tool_id)}</div>
        ${aliases.map(a => `<span class="tag" style="font-size:9.5px">alias&nbsp;${esc(a)}</span>`).join(' ')}</td>
      <td>${u.total_calls}<div class="sub">${Math.round((u.success_rate || 0) * 100)}% succès</div>
        <div class="progress" style="width:64px;margin-top:4px"><i style="width:${Math.round((u.success_rate || 0) * 100)}%"></i></div></td>
      <td>${errCell}${u.last_failure_at ? `<div class="sub">${fmtAgo(u.last_failure_at)}</div>` : ''}</td>
      <td>${u.knowledge_count || 0}<div class="sub">${(u.knowledge || []).length} fiches</div></td>
      <td>${u.coverage_mastered || 0}/${u.coverage_total || 0}<div class="progress" style="width:64px;margin-top:4px"><i style="width:${u.coverage_total ? Math.round(100 * (u.coverage_mastered || 0) / u.coverage_total) : 0}%"></i></div></td>
      <td><a href="#" class="exp-link" data-i="${i}" data-open="0" style="text-decoration:none">
        <span class="tag ${expTone(u.expertise)}" style="font-size:12px;font-weight:700">${u.expertise}%</span></a>
        <div class="sub">priorité ${Math.round(u.priority || 0)}</div></td>
      <td>${u.last_learning_at ? `<div>${fmtAgo(u.last_learning_at)}</div><div class="sub">docs ${u.docs_version === 'latest' ? 'à jour' : (u.docs_checked_at ? fmtAgo(u.docs_checked_at) : 'jamais')}</div>` : '<div class="sub">jamais appris</div>'}</td>
    </tr>
    <tr class="exp-detail" data-row="${i}" style="display:none"><td colspan="7"><table class="inner"><tbody>${detail}</tbody></table></td></tr>`;
  });
  const tableOrEmpty = rows.length
    ? `<tbody>${rows.join('')}</tbody>`
    : `<tbody><tr><td colspan="7"><p class="sub">Les statistiques apparaîtront après l'utilisation des outils.</p></td></tr></tbody>`;

  const queueRows = queue.length
    ? queue.map(q => `<tr><td><span class="tag ${kindTag(q.kind)}">${esc(q.kind)}</span></td>
        <td>${esc(q.topic)}</td><td class="mono">${esc(q.tool_id || '—')}</td>
        <td>${Math.round(q.priority || 0)}</td></tr>`).join('')
    : `<tr><td colspan="4"><p class="sub">Aucun sujet en attente — l'ordre est tiré de l'usage réel.</p></td></tr>`;

  const histRows = sessions.length
    ? sessions.map(s => {
        const r = safeRes(s.result);
        const noNew = r.no_new_knowledge;
        return `<tr>
          <td><span class="tag ${s.error ? 'err' : 'ok'}">${esc(s.status)}</span></td>
          <td class="mono">${esc(s.tool_id || '—')}</td>
          <td>${esc(s.topic || '')}</td>
          <td>${r.knowledge_created || 0}<div class="sub">+${r.knowledge_updated || 0} maj</div></td>
          <td>${s.tests_run || 0}<div class="sub">${s.tests_passed || 0} réussi</div></td>
          <td>${s.expertise_before || 0} → ${s.expertise_after || 0}%</td>
          <td>${s.knowledge_validated || 0}<div class="sub">${s.duplicates_merged || 0} fusion</div></td>
          <td style="max-width:240px"><span class="sub">${esc(r.note || (noNew ? 'No new validated knowledge' : (s.error || 'session terminée')))}</span></td>
          <td>${fmtAgo(s.finished_at || s.started_at)}</td></tr>`;
      }).join('')
    : `<tr><td colspan="9"><p class="sub">Aucune session d'apprentissage pour l'instant.</p></td></tr>`;

  el.innerHTML = `
    <div class="zone-head">
      <div class="zone-title"><span class="zone-kicker">APPRENTISSAGE AUTONOME</span><h2>Idle Learning · mémoire d'expertise</h2></div>
      <div class="term-actions">
        <button class="btn" id="learnRun">Lancer une session</button>
        <button class="btn" id="learnPause">${x.state === 'PAUSED' ? 'Reprendre' : 'Pause'}</button>
        <button class="btn" id="learnMigrate">Fusionner les doublons</button>
      </div>
    </div>
    <div class="cards-grid">${cards}</div>
    <div class="panel"><div class="panel-head"><h3>Outils suivis — expertise réelle expliquée</h3>
      <span class="sub">un seul outil par identité, doublons fusionnés · cliquez sur le score pour le détail</span></div>
      <div class="table-wrap"><table><thead><tr>
        <th>Outil</th><th>Appels</th><th>Échecs</th><th>Connaissances</th><th>Couverture</th><th>Expertise</th><th>Apprentissage</th>
      </tr></thead>${tableOrEmpty}</table></div></div>
    <div class="panel"><div class="panel-head"><h3>File d'apprentissage</h3></div>
      <div class="table-wrap"><table><thead><tr><th>Type</th><th>Sujet</th><th>Outil</th><th>Priorité</th></tr></thead>
      <tbody>${queueRows}</tbody></table></div></div>
    <div class="panel"><div class="panel-head"><h3>Historique des sessions</h3></div>
      <div class="table-wrap"><table><thead><tr>
        <th>État</th><th>Outil</th><th>Sujet</th><th>Créées</th><th>Tests</th><th>Expertise</th><th>Validées</th><th>Note</th><th>Quand</th>
      </tr></thead><tbody>${histRows}</tbody></table></div></div>`;

  el.querySelector('#learnRun').onclick = async () => {
    const b = el.querySelector('#learnRun'); b.disabled = true; b.textContent = 'Session en cours…';
    try { const r = await J.post('/api/learning/run', {}); if (!r.ok) throw new Error(r.error || 'Échec de la session'); }
    catch (e) { window.alert(`Apprentissage : ${e.message}`); }
    await Pages.render('learning');
  };
  el.querySelector('#learnPause').onclick = async () => {
    try {
      const r = await J.post(`/api/learning/${x.state === 'PAUSED' ? 'resume' : 'pause'}`, {});
      if (!r.ok) throw new Error(r.error || 'échec');
    } catch (e) { window.alert(`Pause : ${e.message}`); }
    await Pages.render('learning');
  };
  el.querySelector('#learnMigrate').onclick = async () => {
    const b = el.querySelector('#learnMigrate'); b.disabled = true; b.textContent = 'Fusion…';
    try { const r = await J.post('/api/learning/migrate', {}); if (!r.ok) throw new Error(r.error || 'Échec de la fusion'); }
    catch (e) { window.alert(`Migration : ${e.message}`); }
    await Pages.render('learning');
  };
  el.querySelectorAll('.exp-link').forEach(a => {
    a.onclick = (ev) => {
      ev.preventDefault();
      const i = a.dataset.i;
      const open = a.dataset.open === '1';
      a.dataset.open = open ? '0' : '1';
      const row = el.querySelector(`.exp-detail[data-row="${i}"]`);
      row.style.display = open ? 'none' : '';
    };
  });
  if (x.state === 'LEARNING') setTimeout(() => { if (J.state.page === 'learning') Pages.render('learning'); }, 2000);
};

/* --------------------------------------------------------------- routeur */
Pages.render = async function (name) {
  const el = document.getElementById('page-' + name);
  if (!el) return;
  if (name === 'settings') return Settings.render(el);
  const fn = Pages[name];
  if (typeof fn === 'function') {
    el.innerHTML = '<div class="empty">Chargement…</div>';
    await fn(el);
  }
};
