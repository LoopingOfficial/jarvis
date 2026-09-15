/* ==========================================================================
   JARVIS 4 — Environnement de code dédié avec Monaco Editor.
   Monaco Engine (VS Code) pour la coloration syntaxique professionnelle.
   ========================================================================== */
(function () {
  'use strict';

  const CODE_TOOLS = ['fs.read', 'fs.write', 'fs.list', 'fs.search', 'fs.delete',
    'git.run', 'code.opencode', 'github.query',
    'ssh.read_file', 'ssh.write_file'];
  const FS_TOOLS = { 'fs.read': 'read', 'fs.write': 'write', 'fs.delete': 'delete' };
  const QUIET_SSH = ['ssh.read_file', 'ssh.write_file'];

  const CDN_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs';

  const LANG_MAP = {
    php: 'php', inc: 'php', phtml: 'php',
    js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript',
    html: 'html', htm: 'html', vue: 'html', svelte: 'html',
    css: 'css', scss: 'scss', less: 'less',
    json: 'json', jsonc: 'json',
    py: 'python', pyw: 'python',
    sql: 'sql',
    sh: 'shell', bash: 'shell', zsh: 'shell', fish: 'shell',
    yml: 'yaml', yaml: 'yaml',
    xml: 'xml', svg: 'xml',
    md: 'markdown', mdx: 'markdown',
    txt: 'plaintext', log: 'plaintext', env: 'plaintext',
    ini: 'ini', toml: 'ini', cfg: 'ini', conf: 'plaintext',
    gitignore: 'plaintext', gitattributes: 'plaintext',
    java: 'java', kt: 'kotlin', kts: 'kotlin',
    c: 'c', h: 'c',
    cpp: 'cpp', hpp: 'cpp', cc: 'cpp', cxx: 'cpp',
    cs: 'csharp',
    go: 'go',
    rb: 'ruby',
    rs: 'rust',
    lua: 'lua',
    r: 'r', R: 'r',
    dockerfile: 'dockerfile',
    makefile: 'makefile',
    cmake: 'cmake',
    graphql: 'graphql', gql: 'graphql',
    proto: 'protobuf',
    swift: 'swift',
    ex: 'elixir', exs: 'elixir',
    hs: 'haskell',
    clj: 'clojure',
    dart: 'dart',
  };

  const LANG_LABELS = {
    php: 'PHP', javascript: 'JavaScript', typescript: 'TypeScript',
    html: 'HTML', css: 'CSS', scss: 'SCSS', less: 'LESS',
    json: 'JSON', python: 'Python', sql: 'SQL', shell: 'Shell',
    yaml: 'YAML', xml: 'XML', markdown: 'Markdown', plaintext: 'Plain Text',
    java: 'Java', c: 'C', cpp: 'C++', csharp: 'C#',
    go: 'Go', ruby: 'Ruby', rust: 'Rust', lua: 'Lua',
    graphql: 'GraphQL', swift: 'Swift', dart: 'Dart',
  };

  /* ── State ────────────────────────────────────────────────── */
  let _monaco = null;
  let _editor = null;
  let _diffEditor = null;
  let _diffVisible = false;
  let _diffOriginalModel = null;
  const _models = new Map();
  let _tabs = [];
  let _activeKey = null;
  let _pendingActiveDocumentId = null;

  // Single source of truth for Coding state. Monaco models are a rendering
  // cache; they must never be the thing that decides which documents are open.
  const DocumentStore = window.DocumentStore = window.DocumentStore || {
    documents: new Map(),
    openDocumentIds: [],
    activeDocumentId: null,
    add(d) { this.documents.set(d.id, d); return d; },
    open(id) { if (!this.openDocumentIds.includes(id)) this.openDocumentIds.push(id); this.activeDocumentId = id; return this.documents.get(id); },
    setActive(id) { if (this.documents.has(id)) this.activeDocumentId = id; return this.documents.get(id); },
    get(id) { return this.documents.get(id); },
    remove(id) { this.openDocumentIds = this.openDocumentIds.filter(x => x !== id); this.documents.delete(id); if (this.activeDocumentId === id) this.activeDocumentId = this.openDocumentIds.at(-1) || null; },
  };

  function _key(source, path) { return source + '::' + path; }

  function _trace(...args) {
    try { console.log('[CODE-TRACE]', ...args); } catch { /* noop */ }
  }

  function _log(kind, message) { window.CodeEnv?._log(kind, message); }

  function _detectLang(path) {
    const ext = (path || '').split('.').pop().toLowerCase();
    return LANG_MAP[ext] || 'plaintext';
  }

  function _langLabel(lang) {
    return LANG_LABELS[lang] || lang || 'Plain Text';
  }

  function _modelName(path) {
    const parts = (path || '').split(/[/\\]/);
    return parts[parts.length - 1] || path;
  }

  function _makeUri(source, connectorId, path) {
    const safe = (path || '').replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');
    if (source === 'ssh') return 'ssh://' + encodeURIComponent(connectorId || '_') + '/' + safe.replace(/^\/+/, '');
    if (source === 'fs') return 'monaco://local/' + safe;
    if (source === 'git') return 'monaco://git/' + safe;
    return 'monaco://other/' + safe;
  }

  /* ── Monaco Loading ───────────────────────────────────────── */
  let _monacoPromise = null;

  function _loadMonaco() {
    if (window.monaco) {
      _monaco = window.monaco;
      _trace('monaco déjà chargé');
      return Promise.resolve(_monaco);
    }
    if (_monacoPromise) return _monacoPromise;
    _trace('monaco load: démarrage CDN=' + CDN_BASE);
    _monacoPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Monaco load timeout')), 25000);
      const finish = (m) => {
        clearTimeout(timer);
        _monaco = m;
        _trace('monaco load: éditeur disponible');
        try { _defineTheme(m); } catch (e) { _trace('thème échoué:', e); }
        resolve(m);
      };
      const fail = (e) => {
        clearTimeout(timer);
        reject(e instanceof Error ? e : new Error(String(e)));
      };
      const boot = () => {
        try {
          if (!window.require || !window.require.config) {
            fail(new Error('Monaco loader manquant'));
            return;
          }
          window.require.config({ paths: { vs: CDN_BASE } });
          _trace('monaco load: boot AMD → editor.main');
          window.require(['vs/editor/editor.main'],
            () => finish(window.monaco),
            (err) => fail(new Error('Monaco editor.main error: ' +
              ((err && err.message) || String(err || 'erreur inconnue')))));
        } catch (e) { fail(e); }
      };
      if (window.require && window.require.config) { boot(); return; }
      const s = document.createElement('script');
      s.src = CDN_BASE + '/loader.js';
      s.onload = boot;
      s.onerror = () => fail(new Error('Monaco CDN load failed'));
      document.head.appendChild(s);
    });
    _monacoPromise.catch((e) => { _monacoPromise = null; _trace('monaco load: ÉCHEC →', e); });
    return _monacoPromise;
  }

  function _ensureMonaco() {
    return _loadMonaco().then((m) => {
      const editor = _ensureEditor();
      _updateEmptyState();
      if (!editor) {
        _trace('monaco prêt mais éditeur introuvable (#monacoNormal manquant)');
        return m;
      }
      for (const id of DocumentStore.openDocumentIds) {
        const d = DocumentStore.get(id);
        if (d && !_models.has(id)) _getOrCreateModel(d.absolute_path, d.source_type, d.content, d.connector_id, d.language, id);
      }
      _restoreActiveDocument();
      return m;
    });
  }

  /* ── Theme: jarvis-dark ───────────────────────────────────── */
  function _defineTheme(m) {
    m.editor.defineTheme('jarvis-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: '', foreground: 'D4D4D4' },
        { token: 'comment', foreground: '6A9955', fontStyle: 'italic' },
        { token: 'comment.block', foreground: '6A9955', fontStyle: 'italic' },
        { token: 'keyword', foreground: 'C586C0' },
        { token: 'keyword.control', foreground: 'C586C0' },
        { token: 'keyword.operator', foreground: 'D4D4D4' },
        { token: 'keyword.other', foreground: 'C586C0' },
        { token: 'storage', foreground: 'C586C0' },
        { token: 'storage.type', foreground: '569CD6' },
        { token: 'string', foreground: 'CE9178' },
        { token: 'string.escape', foreground: 'D7BA7D' },
        { token: 'string.regexp', foreground: 'D16969' },
        { token: 'number', foreground: 'B5CEA8' },
        { token: 'number.hex', foreground: 'B5CEA8' },
        { token: 'type', foreground: '4EC9B0' },
        { token: 'type.identifier', foreground: '4EC9B0' },
        { token: 'class', foreground: '4EC9B0' },
        { token: 'struct', foreground: '4EC9B0' },
        { token: 'enum', foreground: '4EC9B0' },
        { token: 'interface', foreground: '4EC9B0' },
        { token: 'identifier', foreground: '9CDCFE' },
        { token: 'variable', foreground: '9CDCFE' },
        { token: 'variable.predefined', foreground: '4FC1FF' },
        { token: 'parameter', foreground: '9CDCFE' },
        { token: 'property', foreground: '9CDCFE' },
        { token: 'delimiter', foreground: 'D4D4D4' },
        { token: 'delimiter.bracket', foreground: 'FFD700' },
        { token: 'delimiter.parenthesis', foreground: 'DA70D6' },
        { token: 'delimiter.square', foreground: 'FFD700' },
        { token: 'delimiter.angle', foreground: '808080' },
        { token: 'tag', foreground: '569CD6' },
        { token: 'metatag', foreground: '569CD6' },
        { token: 'metatag.content', foreground: 'CE9178' },
        { token: 'metatag.php', foreground: 'C586C0' },
        { token: 'metatag.html', foreground: '569CD6' },
        { token: 'attribute.name', foreground: '9CDCFE' },
        { token: 'attribute.value', foreground: 'CE9178' },
        { token: 'attribute.value.html', foreground: 'CE9178' },
        { token: 'constant', foreground: '4FC1FF' },
        { token: 'constant.language', foreground: '569CD6' },
        { token: 'entity.name.function', foreground: 'DCDCAA' },
        { token: 'support.function', foreground: 'DCDCAA' },
        { token: 'support.type', foreground: '4EC9B0' },
        { token: 'support.variable', foreground: '9CDCFE' },
        { token: 'support.constant', foreground: '4FC1FF' },
        { token: 'annotation', foreground: 'DCDCAA' },
        { token: 'regexp', foreground: 'D16969' },
        { token: 'operator', foreground: 'D4D4D4' },
        { token: 'entity.name.type', foreground: '4EC9B0' },
        { token: 'entity.other.inherited-class', foreground: '4EC9B0' },
        { token: 'entity.name.tag', foreground: '569CD6' },
        { token: 'string.key.json', foreground: '9CDCFE' },
        { token: 'string.value.json', foreground: 'CE9178' },
        { token: 'keyword.json', foreground: '569CD6' },
        { token: 'keyword.other.php', foreground: 'C586C0' },
        { token: 'support.class.php', foreground: '4EC9B0' },
        { token: 'support.function.php', foreground: 'DCDCAA' },
        { token: 'variable.php', foreground: '9CDCFE' },
        { token: 'variable.other.php', foreground: '9CDCFE' },
        { token: 'constant.other.php', foreground: '4FC1FF' },
      ],
      colors: {
        'editor.background': '#0A0F1E',
        'editor.foreground': '#D4D4D4',
        'editor.lineHighlightBackground': '#141D3380',
        'editor.lineHighlightBorder': '#141D3300',
        'editor.selectionBackground': '#264F7860',
        'editor.inactiveSelectionBackground': '#264F7830',
        'editor.selectionHighlightBackground': '#264F7830',
        'editorCursor.foreground': '#22D3EE',
        'editorCursor.background': '#0A0F1E',
        'editorWhitespace.foreground': '#3B425240',
        'editorIndentGuide.background': '#1E2A4030',
        'editorIndentGuide.activeBackground': '#2A3A5560',
        'editorLineNumber.foreground': '#3A4A5E',
        'editorLineNumber.activeForeground': '#8FA5BD',
        'editorBracketMatch.background': '#0064001A',
        'editorBracketMatch.border': '#88888855',
        'editorGutter.background': '#0A0F1E',
        'editorWidget.background': '#0E1628',
        'editorWidget.border': '#1E2A40',
        'editorWidget.foreground': '#D4D4D4',
        'editorSuggestWidget.background': '#0E1628',
        'editorSuggestWidget.border': '#1E2A40',
        'editorSuggestWidget.foreground': '#D4D4D4',
        'editorSuggestWidget.selectedBackground': '#1A2844',
        'editorSuggestWidget.highlightForeground': '#22D3EE',
        'editorHoverWidget.background': '#0E1628',
        'editorHoverWidget.border': '#1E2A40',
        'minimap.background': '#0A0F1E',
        'minimap.selectionHighlight': '#264F7840',
        'minimapSlider.background': '#22D3EE18',
        'minimapSlider.hoverBackground': '#22D3EE30',
        'minimapSlider.activeBackground': '#22D3EE50',
        'scrollbarSlider.background': '#22D3EE20',
        'scrollbarSlider.hoverBackground': '#22D3EE40',
        'scrollbarSlider.activeBackground': '#22D3EE60',
        'editorOverviewRuler.border': '#0E1628',
        'editorRuler.foreground': '#1E2A4050',
        'editorError.foreground': '#F14C4C',
        'editorWarning.foreground': '#CCA700',
        'editorInfo.foreground': '#1177BB',
        'editorBracketHighlight.foreground': '#FFD700',
        'editorBracketHighlight.foreground2': '#DA70D6',
        'editorBracketHighlight.foreground3': '#22D3EE',
        'editorBracketHighlight.foreground4': '#B5CEA8',
        'editorBracketHighlight.foreground5': '#F14C4C',
        'editorBracketHighlight.foreground6': '#D7BA7D',
        'list.activeSelectionBackground': '#1A284460',
        'list.inactiveSelectionBackground': '#1A284430',
        'focusBorder': '#22D3EE80',
      },
    });
  }

  /* ── Editor Options ───────────────────────────────────────── */
  function _editorSettings() {
    return (window.J && J.state && J.state.settings && J.state.settings.editor) || {};
  }

  function _editorOptions(readOnly) {
    const s = _editorSettings();
    const fontSize = Number(s.fontSize) >= 10 && Number(s.fontSize) <= 24 ? Number(s.fontSize) : 13;
    return {
      theme: 'jarvis-dark',
      fontFamily: s.fontFamily || '"Cascadia Code", "Fira Code", "JetBrains Mono", Consolas, "Courier New", monospace',
      fontSize,
      fontLigatures: false,
      lineHeight: Math.max(16, Math.round(fontSize * 1.5)),
      letterSpacing: 0,
      minimap: { enabled: s.minimap !== false, scale: 1, showSlider: 'mouseover', renderCharacters: false, maxColumn: 80 },
      lineNumbers: 'on',
      renderLineHighlight: 'line',
      renderLineHighlightOnlyWhenFocus: false,
      bracketPairColorization: { enabled: true, independentColorPoolPerBracketType: true },
      guides: { bracketPairs: true, bracketPairsHorizontal: true, indentation: true, highlightActiveIndentation: true },
      folding: true,
      foldingStrategy: 'indentation',
      showFoldingControls: 'mouseover',
      wordWrap: s.wordWrap ? 'on' : 'off',
      scrollBeyondLastLine: false,
      smoothScrolling: true,
      cursorBlinking: 'smooth',
      cursorSmoothCaretAnimation: 'on',
      renderWhitespace: 'selection',
      automaticLayout: true,
      padding: { top: 10, bottom: 10 },
      tabSize: 4,
      insertSpaces: false,
      readOnly: !!readOnly,
      domReadOnly: !!readOnly,
      suggestOnTriggerCharacters: true,
      quickSuggestions: true,
      scrollbar: { vertical: 'auto', horizontal: 'auto', verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
      roundedSelection: true,
      copyWithSyntaxHighlighting: true,
      formatOnPaste: false,
      formatOnType: false,
      glyphMargin: false,
      fixedOverflowWidgets: true,
    };
  }

  function _applyEditorSettings() {
    const s = _editorSettings();
    const fontSize = Number(s.fontSize) >= 10 && Number(s.fontSize) <= 24 ? Number(s.fontSize) : 13;
    const opts = {
      fontSize,
      lineHeight: Math.max(16, Math.round(fontSize * 1.5)),
      fontFamily: s.fontFamily || '"Cascadia Code", "Fira Code", "JetBrains Mono", Consolas, "Courier New", monospace',
      minimap: { enabled: s.minimap !== false, scale: 1, showSlider: 'mouseover', renderCharacters: false, maxColumn: 80 },
      wordWrap: s.wordWrap ? 'on' : 'off',
    };
    if (_editor) _editor.updateOptions(opts);
    if (_diffEditor) _diffEditor.updateOptions(opts);
  }

  /* ── Editor Init ──────────────────────────────────────────── */
  function _ensureEditor() {
    if (_editor) return _editor;
    if (!_monaco) return null;
    const container = document.getElementById('monacoNormal');
    if (!container) return null;
    _editor = _monaco.editor.create(container, _editorOptions(false));
    _editor.onDidChangeCursorPosition(() => _updateStatusBar());
    _editor.onDidFocusEditorText(() => _updateStatusBar());
    return _editor;
  }

  function _restoreActiveDocument() {
    const id = _pendingActiveDocumentId || DocumentStore.activeDocumentId;
    if (!id) return;
    const doc = DocumentStore.get(id);
    if (!doc) return;
    const entry = _models.get(id) || _getOrCreateModel(doc.absolute_path, doc.source_type,
      doc.content, doc.connector_id, doc.language, id);
    if (entry) {
      _tabs = DocumentStore.openDocumentIds.slice();
      _pendingActiveDocumentId = null;
      _switchModel(doc.absolute_path, doc.source_type, id);
      // La vérification ne doit JAMAIS dépendre de requestAnimationFrame :
      // rAF est gelé quand la fenêtre est masquée/minimisée/en arrière-plan,
      // l'accusé de rendu ne partait alors jamais et JARVIS déclarait à tort
      // « affichage non confirmé » alors que le document est bien monté.
      _verifyRender(id, entry, doc, 0);
    }
  }

  function _verifyRender(id, entry, doc, attempt) {
    try { _editor.layout(); } catch { /* conteneur pas encore mesuré */ }
    const model = entry.model;
    const alive = !model.isDisposed || !model.isDisposed();
    const matches = alive
      && DocumentStore.activeDocumentId === id
      && _editor.getModel() === model
      && model.getValue() === doc.content;
    _trace('render verified', id, matches, alive ? model.getValueLength() : -1,
      'attempt', attempt, 'hidden', document.hidden);
    if (matches) {
      if (doc.request_id && doc.acknowledged !== doc.request_id) {
        doc.acknowledged = doc.request_id;
        J.post('/api/code/rendered', { document_id: id, request_id: doc.request_id,
          model_matches: true, language: model.getLanguageId() }).then(r => {
            if (!r.accepted) doc.acknowledged = null;
            _trace('render ack', id, !!r.accepted);
          }).catch((e) => { doc.acknowledged = null; console.error(e); });
      }
      return;
    }
    if (attempt < 12) setTimeout(() => _verifyRender(id, entry, doc, attempt + 1), 120);
  }

  /* ── Model Management ─────────────────────────────────────── */
  function _getModel(path, source) {
    return _models.get(_key(source, path));
  }

  function _getOrCreateModel(path, source, content, connectorId, lang, documentId) {
    const k = documentId || _key(source, path);
    let entry = _models.get(k);
    if (entry) {
      const cur = entry.model.getValue();
      if (cur !== content) {
        // `original` DOIT être posé avant setValue : onDidChangeContent part
        // pendant le setValue et comparerait sinon à l'ancienne révision, ce
        // qui laissait le document marqué « modifié » juste après une
        // écriture réussie de JARVIS.
        entry.original = content;
        entry.dirty = false;
        entry.status = 'saved';
        entry.model.setValue(content);
        const d0 = DocumentStore.get(k);
        if (d0) { d0.content = content; d0.original_content = content; d0.dirty = false; }
        _updateStatusBar();
        _renderTabs();
        window.CodeEnv?.renderFiles();
      }
      return entry;
    }
    const ext = (path || '').split('.').pop().toLowerCase();
    lang = lang || LANG_MAP[ext] || 'plaintext';
    if (lang === 'dockerfile' && _modelName(path).toLowerCase() !== 'dockerfile') lang = 'plaintext';
    if (lang === 'makefile' && _modelName(path).toLowerCase() !== 'makefile' && _modelName(path).toLowerCase() !== 'gnumakefile') lang = 'plaintext';
    const uri = _monaco.Uri.parse(_makeUri(source, connectorId, path));
    let model = _monaco.editor.getModel(uri);
    if (model) {
      model.setValue(content || '');
      _monaco.editor.setModelLanguage(model, lang);
    } else {
      model = _monaco.editor.createModel(content || '', lang, uri);
    }
    entry = {
      model, path, source, connectorId, lang,
      original: content || '',
      dirty: false,
      status: 'saved',
      readOnly: false,
      scrollTop: 0,
      cursorLine: 1,
      cursorCol: 1,
    };
    _models.set(k, entry);
    if (!DocumentStore.get(k)) DocumentStore.add({ id: k, filename: _modelName(path),
      source_type: source, absolute_path: path, connector_id: connectorId, language: lang,
      content, original_content: content, dirty: false, read_only: false,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
    _trace('monaco model created', k, 'monaco uri', uri.toString(), 'content length', String(content || '').length);
    model.onDidChangeContent(() => _onModelContentChanged(k, entry, model));
    return entry;
  }

  function _onModelContentChanged(k, entry, model) {
    try {
      const cur = model.getValue();
      const changed = cur !== entry.original;
      const doc = DocumentStore.get(k);
      if (doc) { doc.content = cur; doc.dirty = changed; doc.updated_at = new Date().toISOString(); }
      if (entry.dirty !== changed) {
        entry.dirty = changed;
        entry.status = changed ? 'modified' : 'saved';
        _updateStatusBar();
        _renderTabs();
        window.CodeEnv?.renderFiles();
      }
    } catch { /* transitoire */ }
  }

  function _switchModel(path, source, documentId) {
    if (_diffVisible) _closeDiff();
    const k = documentId || _key(source, path);
    const entry = _models.get(k);
    if (!entry || !_editor) return;
    const prev = _models.get(_activeKey);
    if (prev && prev.model === _editor.getModel()) {
      prev.scrollTop = _editor.getScrollTop();
      const p = _editor.getPosition();
      if (p) { prev.cursorLine = p.lineNumber; prev.cursorCol = p.column; }
    }
    _activeKey = k;
    DocumentStore.setActive(k);
    _editor.setModel(entry.model);
    _trace('editor.setModel', k, 'active model id', DocumentStore.activeDocumentId);
    if (entry.scrollTop) _editor.setScrollPosition({ scrollTop: entry.scrollTop });
    if (entry.cursorLine) {
      _editor.setPosition({ lineNumber: entry.cursorLine, column: entry.cursorCol || 1 });
    }
    _editor.updateOptions({ readOnly: entry.readOnly });
    _updateStatusBar();
    _renderTabs();
    window.CodeEnv?.renderFiles();
  }

  /* ── Tab Management ───────────────────────────────────────── */
  function _renderTabs() {
    const el = document.getElementById('codeTabs');
    if (!el) return;
    let html = '';
    for (const k of _tabs) {
      const m = _models.get(k);
      if (!m) continue;
      const name = _modelName(m.path);
      const active = k === _activeKey;
      const cls = ['cw-tab'];
      if (active) cls.push('active');
      if (m.dirty) cls.push('dirty');
      if (m.status === 'saving') cls.push('saving');
      if (m.status === 'error') cls.push('error');
      const srcLabel = m.source === 'ssh' ? 'SSH' : m.source === 'fs' ? 'LOCAL' : m.source.toUpperCase();
      html += `<div class="${cls.join(' ')}" data-tab-key="${esc(k)}">
        <span class="cw-tab-dot"></span>
        <span class="cw-tab-src">${esc(srcLabel)}</span>
        <span class="cw-tab-name" title="${esc(m.path)}">${esc(name)}</span>
        <span class="cw-tab-close" data-close-tab="${esc(k)}">&times;</span>
      </div>`;
    }
    el.innerHTML = html;
    el.querySelectorAll('.cw-tab').forEach((t) => {
      t.addEventListener('click', (e) => {
        if (e.target.closest('.cw-tab-close')) return;
        const parts = t.dataset.tabKey.split('::');
        _switchModel(parts.slice(1).join('::'), parts[0], t.dataset.tabKey);
      });
    });
    el.querySelectorAll('.cw-tab-close').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        _closeTab(btn.dataset.closeTab);
      });
    });
    _updateEmptyState();
  }

  function _openTab(path, source, documentId) {
    const k = documentId || _key(source, path);
    if (!_tabs.includes(k)) _tabs.push(k);
    DocumentStore.open(k);
    _switchModel(path, source, documentId);
  }

  // Retire un document côté frontend uniquement (modèle Monaco compris).
  // Un modèle n'est disposé QUE là : jamais dans un cleanup de rendu.
  function _dropDocument(k) {
    const idx = _tabs.indexOf(k);
    if (idx >= 0) _tabs.splice(idx, 1);
    const entry = _models.get(k);
    _models.delete(k);
    DocumentStore.remove(k);
    if (_activeKey === k) {
      _activeKey = null;
      if (_editor) _editor.setModel(null);
      if (_diffVisible) _closeDiff();
    }
    if (entry && entry.model) { try { entry.model.dispose(); } catch { /* déjà disposé */ } }
  }

  function _closeTab(k) {
    const idx = _tabs.indexOf(k);
    if (idx < 0) return;
    if (_models.get(k)?.dirty && !window.confirm('Fermer ce fichier et abandonner les modifications non sauvegardées ?')) return;
    _tabs.splice(idx, 1);
    const entry = _models.get(k);
    _models.delete(k);
    DocumentStore.remove(k);
    J.post('/api/code/close', {document_id: k}).catch(console.error);
    if (_activeKey === k) {
      if (_tabs.length) {
        const parts = _tabs[Math.min(idx, _tabs.length - 1)].split('::');
        _switchModel(parts.slice(1).join('::'), parts[0], _tabs[Math.min(idx, _tabs.length - 1)]);
      } else {
        _activeKey = null;
        if (_editor) _editor.setModel(null);
        if (_diffVisible) _closeDiff();
      }
    }
    if (entry && entry.model) {
      try { entry.model.dispose(); } catch { /* déjà disposé */ }
    }
    _updateEmptyState();
    _updateStatusBar();
    _renderTabs();
    // Fermer un onglet NON actif ne passait pas par _switchModel : la liste
    // « Fichiers ouverts » gardait alors une entrée fantôme.
    window.CodeEnv?.renderFiles();
  }

  function _updateEmptyState() {
    const empty = document.getElementById('codeEditorEmpty');
    const norm = document.getElementById('monacoNormal');
    const diff = document.getElementById('monacoDiff');
    const sb = document.getElementById('codeStatusBar');
    const tabs = document.getElementById('codeTabs');
    if (!empty) return;
    const hasFiles = DocumentStore.openDocumentIds.length > 0;
    empty.style.display = hasFiles ? 'none' : '';
    if (norm) norm.style.display = hasFiles && !_diffVisible ? '' : 'none';
    if (sb) sb.style.display = hasFiles ? '' : 'none';
    if (tabs) tabs.style.display = hasFiles ? '' : 'none';
    if (!hasFiles && diff) diff.style.display = 'none';
    if (!hasFiles) _diffVisible = false;
  }

  /* ── Status Bar ───────────────────────────────────────────── */
  function _updateStatusBar() {
    const lang = document.getElementById('codeStatusLang');
    const pos = document.getElementById('codeStatusPos');
    const source = document.getElementById('codeStatusSource');
    const saved = document.getElementById('codeStatusSaved');
    if (!_editor || !_activeKey) {
      if (lang) lang.textContent = '—';
      if (pos) pos.textContent = '';
      if (source) source.textContent = '—';
      if (saved) saved.textContent = '';
      return;
    }
    const entry = _models.get(_activeKey);
    if (!entry) return;
    if (lang) lang.textContent = _langLabel(entry.lang);
    const p = _editor.getPosition();
    if (pos && p) pos.textContent = 'Ln ' + p.lineNumber + ', Col ' + p.column;
    const srcLabels = { ssh: 'SSH', fs: 'LOCAL', git: 'GIT' };
    if (source) source.textContent = srcLabels[entry.source] || entry.source;
    if (saved) {
      saved.textContent = entry.status === 'modified' ? 'Modified' :
        entry.status === 'saving' ? 'Saving…' :
        entry.status === 'error' ? 'Error' :
        entry.status === 'conflict' ? 'Conflict' : 'Saved';
      saved.className = 'cw-sb cw-sb-status ' + entry.status;
    }
  }

  /* ── Save Flow ────────────────────────────────────────────── */
  async function _saveCurrent() {
    if (!_activeKey) return;
    const entry = _models.get(_activeKey);
    if (!entry || !entry.dirty) return;
    entry.status = 'saving';
    _updateStatusBar();
    _renderTabs();
    const content = entry.model.getValue();
    try {
      if (entry.source === 'ssh') {
        // GARDE-FOU ANTI-ÉCRASEMENT : le distant a pu bouger depuis l'ouverture.
        // On relit AVANT d'écrire et on compare à la révision de base (entry.original).
        // Sans ce contrôle, une modification distante était silencieusement perdue.
        const base = entry.original;
        const remote = await _readRemote(entry);
        if (remote === null) {
          entry.status = 'error';
          _updateStatusBar(); _renderTabs();
          _log('err', 'Sauvegarde annul\u00e9e : relecture distante impossible.');
          toast('Sauvegarde annul\u00e9e : serveur injoignable.', 'err');
          return;
        }
        if (remote !== base) {
          _log('warn', _modelName(entry.path) + ' : conflit distant \u2014 aucune \u00e9criture effectu\u00e9e.');
          _conflictDialog(entry, base, content, remote);
          return;
        }
        const args = { path: entry.path, content, mode: 'write' };
        if (entry.connectorId) args.connector_id = entry.connectorId;
        const r = await J.post('/api/tools/ssh.write_file/run', { arguments: args, confirmed: true });
        if (r.ok && r.result && r.result.ok) {
          if (await _verifyRemoteFile(entry, content)) {
            _log('ok', _modelName(entry.path) + ' sauvegardé et vérifié (SSH)');
            toast(_modelName(entry.path) + ' sauvegardé et vérifié.', 'ok');
          }
        } else {
          entry.status = 'error';
          _updateStatusBar();
          _renderTabs();
          const err = (r.result && r.result.output) || r.error || 'Erreur de sauvegarde.';
          _log('err', 'Sauvegarde échouée : ' + String(err).slice(0, 120));
          toast('Erreur de sauvegarde.', 'err');
        }
      } else if (entry.source === 'fs') {
        const r = await J.post('/api/tools/fs.write/run', { arguments: { path: entry.path, content } });
        if (r.ok && r.result && r.result.ok) {
          entry.original = content;
          entry.dirty = false;
          entry.status = 'saved';
          _updateStatusBar();
          _renderTabs();
          _log('ok', _modelName(entry.path) + ' sauvegardé');
          toast(_modelName(entry.path) + ' sauvegardé.', 'ok');
        } else {
          entry.status = 'error';
          _updateStatusBar();
          _renderTabs();
          _log('err', 'Sauvegarde échouée : ' + String((r.result && r.result.output) || r.error || '').slice(0, 120));
          toast('Erreur de sauvegarde.', 'err');
        }
      } else {
        entry.status = 'error';
        _updateStatusBar();
        _renderTabs();
      }
    } catch (e) {
      entry.status = 'error';
      _updateStatusBar();
      _renderTabs();
      _log('err', 'Exception sauvegarde : ' + String(e).slice(0, 100));
    }
  }

  // Relit le fichier distant. Retourne le contenu, ou null en cas d'échec.
  async function _readRemote(entry) {
    try {
      const args = { path: entry.path };
      if (entry.connectorId) args.connector_id = entry.connectorId;
      const r = await J.post('/api/tools/ssh.read_file/run', { arguments: args });
      if (r.ok && r.result && r.result.ok) {
        return r.result.data?.content ?? r.result.output ?? '';
      }
    } catch { /* réseau */ }
    return null;
  }

  // Écrit sans re-contrôler le distant (après arbitrage explicite de l'utilisateur).
  async function _forceWrite(entry, content) {
    entry.status = 'saving';
    _updateStatusBar(); _renderTabs();
    const args = { path: entry.path, content, mode: 'write' };
    if (entry.connectorId) args.connector_id = entry.connectorId;
    try {
      const r = await J.post('/api/tools/ssh.write_file/run', { arguments: args, confirmed: true });
      if (r.ok && r.result && r.result.ok) {
        if (await _verifyRemoteFile(entry, content)) {
          _log('ok', _modelName(entry.path) + ' sauvegard\u00e9 et v\u00e9rifi\u00e9 (SSH)');
          toast(_modelName(entry.path) + ' sauvegard\u00e9 et v\u00e9rifi\u00e9.', 'ok');
          return true;
        }
      } else {
        entry.status = 'error';
        _log('err', 'Sauvegarde \u00e9chou\u00e9e : ' +
          String((r.result && r.result.output) || r.error || '').slice(0, 120));
      }
    } catch (e) {
      entry.status = 'error';
      _log('err', 'Exception sauvegarde : ' + String(e).slice(0, 100));
    }
    _updateStatusBar(); _renderTabs();
    return false;
  }

  // Fusion 3-way naïve, à la ligne. Retourne { text, conflicts }.
  // Plus longue sous-séquence commune, en lignes. Base d'une fusion 3-way
  // correcte : une comparaison position par position se décale dès qu'une
  // ligne est insérée et produit des conflits fantômes.
  function _lcsOps(a, b) {
    const n = a.length, m = b.length;
    const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const ops = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { ops.push({ t: 'same', a: i, b: j }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: 'del', a: i }); i++; }
      else { ops.push({ t: 'ins', b: j }); j++; }
    }
    while (i < n) { ops.push({ t: 'del', a: i }); i++; }
    while (j < m) { ops.push({ t: 'ins', b: j }); j++; }
    return ops;
  }

  // Pour chaque ligne de la base : la liste des lignes qui la remplacent dans
  // la révision cible, plus ce qui est inséré avant elle.
  function _align(base, side) {
    const before = Array.from({ length: base.length + 1 }, () => []);
    const repl = base.map(() => null);      // null = ligne supprimée
    let pending = [];
    for (const op of _lcsOps(base, side)) {
      if (op.t === 'ins') { pending.push(side[op.b]); continue; }
      if (op.t === 'same') {
        before[op.a] = pending; pending = [];
        repl[op.a] = [base[op.a]];
        continue;
      }
      before[op.a] = pending; pending = [];   // 'del' : ligne absente de `side`
      repl[op.a] = [];
    }
    before[base.length] = pending;
    return { before, repl };
  }

  const _eq = (x, y) => JSON.stringify(x) === JSON.stringify(y);

  // Fusion 3-way par ancrage sur la base. Retourne { text, conflicts }.
  // Une région où UN SEUL côté a changé est reprise telle quelle ; une région
  // où LES DEUX ont changé différemment est marquée, jamais arbitrée en silence.
  function _merge3(base, mine, theirs) {
    const B = base.split('\n'), M = mine.split('\n'), T = theirs.split('\n');
    const am = _align(B, M), at = _align(B, T);
    const out = [];
    let conflicts = 0;
    const push = (mSide, tSide, baseSide) => {
      if (_eq(mSide, tSide)) { out.push(...mSide); return; }
      if (_eq(mSide, baseSide)) { out.push(...tSide); return; }   // seul le distant a changé
      if (_eq(tSide, baseSide)) { out.push(...mSide); return; }   // seul « moi » a changé
      conflicts++;
      out.push('<<<<<<< LOCAL', ...mSide, '=======', ...tSide, '>>>>>>> DISTANT');
    };
    for (let i = 0; i <= B.length; i++) {
      push(am.before[i], at.before[i], []);                       // insertions
      if (i < B.length) push(am.repl[i] || [], at.repl[i] || [], [B[i]]);
    }
    return { text: out.join('\n'), conflicts };
  }

  // Affiche le diff distant (gauche) vs local (droite).
  function _showConflictDiff(entry, remote) {
    if (!_monaco) return;
    if (_diffOriginalModel) { _diffOriginalModel.dispose(); _diffOriginalModel = null; }
    if (_diffEditor) { _diffEditor.dispose(); _diffEditor = null; }
    _diffOriginalModel = _monaco.editor.createModel(remote, entry.lang);
    _diffEditor = _monaco.editor.createDiffEditor(document.getElementById('monacoDiff'), {
      ..._editorOptions(false), readOnly: true, renderSideBySide: true, automaticLayout: true,
    });
    _diffEditor.setModel({ original: _diffOriginalModel, modified: entry.model });
    _diffVisible = true;
    const normEl = document.getElementById('monacoNormal');
    const diffEl = document.getElementById('monacoDiff');
    const diffBtn = document.getElementById('codeDiffBtn');
    if (normEl) normEl.style.display = 'none';
    if (diffEl) diffEl.style.display = '';
    if (diffBtn) diffBtn.classList.add('active');
  }

  // État Conflict + arbitrage explicite. Aucune écriture tant que
  // l'utilisateur n'a pas choisi : jamais d'écrasement silencieux.
  function _conflictDialog(entry, base, mine, remote) {
    entry.status = 'conflict';
    entry.conflictRemote = remote;
    _updateStatusBar(); _renderTabs();
    toast(_modelName(entry.path) + ' a chang\u00e9 sur le serveur.', 'err');
    const m = modal({
      title: 'Conflit distant \u2014 ' + _modelName(entry.path),
      body: `<div class="risk-banner destructive">${icon('alert', 16)}
        <div>Le fichier a \u00e9t\u00e9 modifi\u00e9 sur le serveur depuis son ouverture.
        <b>Aucune \u00e9criture n'a \u00e9t\u00e9 effectu\u00e9e.</b><br>
        Base ${base.length} car. \u00b7 Local ${mine.length} car. \u00b7 Distant ${remote.length} car.</div></div>`,
      footer: `<button class="btn" data-act="reload">Reload Remote</button>
               <button class="btn" data-act="compare">Compare</button>
               <button class="btn" data-act="merge">Merge</button>
               <button class="btn danger" data-act="mine">Keep Mine</button>`,
    });
    const act = (fn) => (e) => { e.preventDefault(); fn(); };
    m.$('[data-act="reload"]').addEventListener('click', act(() => {
      m.close();
      entry.model.setValue(remote);
      entry.original = remote;
      entry.dirty = false;
      entry.status = 'saved';
      const doc = [...DocumentStore.documents.values()].find(d => _models.get(d.id) === entry);
      if (doc) { doc.content = remote; doc.original_content = remote; doc.dirty = false; }
      _updateStatusBar(); _renderTabs(); window.CodeEnv?.renderFiles();
      _log('ok', _modelName(entry.path) + ' rechargé depuis le serveur.');
    }));
    m.$('[data-act="compare"]').addEventListener('click', act(() => {
      m.close();
      _showConflictDiff(entry, remote);
    }));
    m.$('[data-act="merge"]').addEventListener('click', act(() => {
      m.close();
      const { text, conflicts } = _merge3(base, mine, remote);
      entry.model.setValue(text);
      if (conflicts) {
        entry.status = 'conflict';
        _log('warn', conflicts + ' conflit(s) \u00e0 r\u00e9soudre \u00e0 la main dans ' + _modelName(entry.path));
        toast(conflicts + ' conflit(s) \u00e0 r\u00e9soudre.', 'err');
      } else {
        entry.original = remote;   // la nouvelle base est la révision distante
        entry.status = 'modified';
        _log('ok', 'Fusion automatique r\u00e9ussie \u2014 relancez Ctrl+S pour enregistrer.');
        toast('Fusion r\u00e9ussie. Ctrl+S pour enregistrer.', 'ok');
      }
      _updateStatusBar(); _renderTabs();
    }));
    m.$('[data-act="mine"]').addEventListener('click', act(async () => {
      m.close();
      _log('warn', 'Keep Mine : \u00e9crasement volontaire de la r\u00e9vision distante.');
      await _forceWrite(entry, mine);
    }));
  }

  async function _verifyRemoteFile(entry, expected) {
    try {
      const args = { path: entry.path };
      if (entry.connectorId) args.connector_id = entry.connectorId;
      const r = await J.post('/api/tools/ssh.read_file/run', { arguments: args });
      if (r.ok && r.result && r.result.ok) {
        const content = r.result.data?.content ?? r.result.output ?? '';
        if (content === expected) {
          entry.original = expected;
          entry.dirty = entry.model.getValue() !== expected;
          entry.status = entry.dirty ? 'modified' : 'saved';
          const doc = [...DocumentStore.documents.values()].find(d => _models.get(d.id) === entry);
          if (doc) { doc.original_content = expected; doc.dirty = entry.dirty; }
          _updateStatusBar(); _renderTabs(); window.CodeEnv?.renderFiles();
          return true;
        } else {
          entry.status = 'conflict';
          _updateStatusBar();
          _log('warn', _modelName(entry.path) + ' : conflit détecté');
        }
      } else { entry.status = 'error'; }
    } catch { entry.status = 'error'; }
    _updateStatusBar(); _renderTabs();
    return false;
  }

  /* ── Diff View ────────────────────────────────────────────── */
  function _closeDiff() {
    if (!_diffVisible) return;
    _diffVisible = false;
    const normEl = document.getElementById('monacoNormal');
    const diffEl = document.getElementById('monacoDiff');
    const diffBtn = document.getElementById('codeDiffBtn');
    if (_diffEditor) { _diffEditor.dispose(); _diffEditor = null; }
    if (_diffOriginalModel) { _diffOriginalModel.dispose(); _diffOriginalModel = null; }
    if (diffEl) diffEl.style.display = 'none';
    if (normEl) normEl.style.display = '';
    if (diffBtn) diffBtn.classList.remove('active');
    if (_editor) _editor.focus();
  }

  function _toggleDiff() {
    if (_diffVisible) { _closeDiff(); return; }
    if (!_activeKey || !_monaco) return;
    const entry = _models.get(_activeKey);
    if (!entry) return;
    if (entry.original === entry.model.getValue()) {
      toast('Aucune différence à afficher.', '');
      return;
    }
    if (_diffOriginalModel) { _diffOriginalModel.dispose(); _diffOriginalModel = null; }
    _diffOriginalModel = _monaco.editor.createModel(entry.original, entry.lang);
    if (_diffEditor) { _diffEditor.dispose(); _diffEditor = null; }
    _diffEditor = _monaco.editor.createDiffEditor(document.getElementById('monacoDiff'), {
      ..._editorOptions(false),
      readOnly: true,
      renderSideBySide: true,
      automaticLayout: true,
    });
    _diffEditor.setModel({ original: _diffOriginalModel, modified: entry.model });
    _diffVisible = true;
    const normEl = document.getElementById('monacoNormal');
    const diffEl = document.getElementById('monacoDiff');
    const diffBtn = document.getElementById('codeDiffBtn');
    if (normEl) normEl.style.display = 'none';
    if (diffEl) diffEl.style.display = '';
    if (diffBtn) diffBtn.classList.add('active');
  }

  /* ── Public API ───────────────────────────────────────────── */
  window.CodeEnv = {
    files: new Map(),
    log: [],
    openPath: null,
    openSource: null,
    active: false,
    _bound: false,
    _logTimer: null,
    _refreshTimer: null,
    _lastKick: 0,
    _autoOpenTimer: null,

    /* ---------------------------------------------------------- abonnement */
    bind() {
      if (this._bound) return;
      this._bound = true;
      J.on('*', (type, d) => {
        // Une (re)connexion SSE signifie potentiellement un backend redémarré :
        // le store frontend doit se réaligner sur l'état réellement persisté,
        // sinon des onglets fantômes d'une session morte restent affichés.
        if (type === 'stream.open') { setTimeout(() => this._reconcile(), 600); return; }
        if (type === 'event' || type === 'stream.close') return;
        this._onEvent(type, d || {});
      });
      setInterval(() => { if (J.state.page === 'code' || this.active) this.refresh(false); }, 15000);
      document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
          if (J.state.page === 'code' && _activeKey) { e.preventDefault(); e.stopPropagation(); _saveCurrent(); }
        }
      }, true);
      _loadMonaco().catch((e) => _trace('preload monaco échoué', e));
    },

    /* -------------------------------------------------------- page / rafraîchi */
    render() {
      this.bind();
      this._markChip();
      this.renderLog();
      this.renderFiles();
      _ensureMonaco().catch((e) => {
        console.error('[CODE-TRACE] Monaco load error:', e);
        _showMonacoError();
      });
      if (!this._rendered || this._rendered < Date.now() - 20000) {
        this._rendered = Date.now();
        this.refresh(true);
      }
    },

    async refresh(forceAudit) {
      try {
        const audit = await J.get('/api/audit?limit=100');
        if (audit.entries) {
          for (const e of audit.entries) {
            const kind = FS_TOOLS[e.tool];
            if (!kind || !e.action) continue;
            const p = this._extractPath(e.action);
            if (!p) continue;
            const cur = this.files.get(p) || { ts: 0, kinds: new Set() };
            cur.ts = Math.max(cur.ts, Number(e.ts) || 0);
            cur.kinds.add(kind);
            this.files.set(p, cur);
          }
          this._git = null;
          for (const e of audit.entries) {
            if (e.tool === 'git.run' && e.status === 'ok' && e.action && e.detail) {
              this._git = { action: e.action, detail: e.detail };
              break;
            }
          }
        }
        this.renderFiles();
        if (forceAudit && this.openPath && this.openSource === 'fs') this._readFile(this.openPath);
      } catch { /* le panneau reste silencieux si indisponible */ }
    },

    _markChip() {
      const chip = $('#codeMode');
      if (chip) chip.textContent = this.active ? 'EN TRAVAIL' : 'STANDBY';
      const dot = $('#codeLiveDot');
      if (dot) dot.classList.toggle('on', this.active);
      const proj = $('#codeProject');
      if (proj && !proj.dataset.set) {
        proj.dataset.set = '1';
        const dp = J.state.settings?.general?.default_project;
        proj.textContent = dp ? dp : '—';
      }
    },

    /* --------------------------------- réalignement sur le store backend */
    async _reconcile() {
      let snap;
      try { snap = await J.get('/api/code/documents'); } catch { return; }
      if (!snap || !Array.isArray(snap.documents)) return;
      const live = new Set(snap.documents.map((d) => d.document_id || d.id));
      for (const id of DocumentStore.openDocumentIds.slice()) {
        if (live.has(id)) continue;
        _trace('reconcile: document fantôme retiré', id);
        _dropDocument(id);
      }
      if (snap.activeDocumentId && DocumentStore.get(snap.activeDocumentId)
          && DocumentStore.activeDocumentId !== snap.activeDocumentId) {
        const doc = DocumentStore.get(snap.activeDocumentId);
        _switchModel(doc.absolute_path, doc.source_type, doc.id);
      }
      _tabs = DocumentStore.openDocumentIds.slice();
      _renderTabs();
      this.renderFiles();
      _updateEmptyState();
    },

    /* ------------------------------------------------------- événements SSE */
    _onEvent(type, d) {
      if (type === 'code.file.opened') {
        _trace('frontend event received', 'code.file.opened', d.document_id, d.filename);
        this.openRemoteFile(d);
        return;
      }
      if ((type.startsWith('agent.') || type === 'agent.idle') && d.id === 'coding') {
        this._agent(type, d);
        return;
      }
      if (['tool.called', 'tool.completed', 'tool.failed'].includes(type) && CODE_TOOLS.includes(d.tool)) {
        this._tool(type, d);
      }
    },

    _agent(type, d) {
      const label = d.name || d.id || 'Coding Agent';
      if (type === 'agent.started') {
        this.active = true;
        this._log('agent', label + ' — ' + (d.action || 'délégation de codage'));
        this.kick();
      } else if (type === 'agent.progress') {
        this._log('info', label + ' · ' + (d.action || 'en cours'));
      } else if (type === 'agent.completed') {
        this.active = false;
        this._log('ok', label + ' a terminé.');
        this.refresh(false);
      } else if (type === 'agent.failed') {
        this.active = false;
        this._log('err', label + ' en échec.');
      } else if (type === 'agent.idle') {
        this.active = false;
      }
      this._markChip();
    },

    _tool(type, d) {
      const name = d.name || d.tool || 'outil';
      const quiet = QUIET_SSH.includes(d.tool);
      const tPath = d.path || d.arguments?.path || '';
      const logKey = quiet ? 'open:ssh:' + tPath : 'tool:' + (d.tool || name) + ':' + tPath;
      if (type === 'tool.called') {
        if (quiet && d.tool === 'ssh.read_file' && tPath) {
          this._log('tool', 'Lecture de ' + this._name(tPath) + '…', logKey);
        }
        if (!quiet) this._log('tool', name + (d.agent && d.agent !== 'jarvis' ? ' (' + d.agent + ')' : '') + '…', logKey);
        if (['fs.write', 'fs.delete', 'code.opencode', 'git.run', 'github.query'].includes(d.tool)) {
          this.kick('un fichier');
        }
        if (d.tool === 'fs.write' || d.tool === 'git.run') this._scheduleRefresh();
      } else if (type === 'tool.completed') {
        if (quiet) return;
        if (d.tool === 'fs.write') {
          const p = (d.preview || '').match(/Écrit dans (.+?) \(\d+ caractères\)/);
          if (p) this._autoOpen(p[1].trim());
          this._log('ok', name + ' ✓' + (p ? ' ' + this._short(p[1]) : ''), logKey);
        } else {
          this._log('ok', name + ' ✓', logKey);
        }
        if (d.tool === 'git.run' || d.tool === 'fs.write') this._scheduleRefresh();
      } else if (type === 'tool.failed') {
        if (!quiet) this._log('err', name + ' ✗', logKey);
        this._scheduleRefresh();
      }
      this._markChip();
    },

    /* ------------------------------------------- bascule automatique */
    kick(reason) {
      if (J.state.page === 'code') { this.render(); return; }
      const now = Date.now();
      if (now - this._lastKick > 5000) {
        this._lastKick = now;
        window.App?.goto('code');
        toast('Codage — JARVIS travaille' + (reason ? ' sur ' + reason : '') + '.', 'ok');
      }
    },

    _scheduleRefresh() {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = setTimeout(() => this.refresh(false), 900);
    },

    _autoOpen(path) {
      if (location.hash !== '#code') return;
      clearTimeout(this._autoOpenTimer);
      this._autoOpenTimer = setTimeout(() => this.openFile(path), 1100);
    },

    /* ------------------------------------------------------- journal live */
    _log(kind, text, key) {
      if (key) {
        const prev = this.log.findIndex((e) => e.key === key);
        if (prev >= 0) this.log.splice(prev, 1);
      }
      this.log.push({ t: Date.now(), k: kind, m: text, key });
      if (this.log.length > 140) this.log.shift();
      clearTimeout(this._logTimer);
      this._logTimer = setTimeout(() => this.renderLog(), 260);
    },

    renderLog() {
      const host = $('#codeLog');
      if (!host) return;
      const atBottom = host.scrollTop >= host.scrollHeight - host.clientHeight - 20;
      const frag = document.createDocumentFragment();
      for (const e of this.log) {
        const div = document.createElement('div');
        div.className = 'cw-line ' + e.k;
        const st = document.createElement('i');
        st.className = 'cw-st';
        const span = document.createElement('span');
        span.textContent = e.m;
        div.appendChild(st);
        div.appendChild(span);
        frag.appendChild(div);
      }
      host.innerHTML = '';
      host.appendChild(frag);
      if (atBottom) host.scrollTop = host.scrollHeight;
      const empty = $('#codeLogEmpty');
      if (empty) empty.hidden = this.log.length > 0;
    },

    /* ----------------------------------------------------- listes fichiers */
    renderFiles() {
      const host = $('#codeFiles');
      if (!host) return;
      host.innerHTML = DocumentStore.openDocumentIds.map(id => {
        const d = DocumentStore.get(id);
        return `<button class="cw-file-row${id === DocumentStore.activeDocumentId ? ' active' : ''}" data-document-id="${esc(id)}">${esc(d.filename)}${d.dirty ? ' ●' : ''}</button>`;
      }).join('') || '<p class="cw-empty">Aucun fichier ouvert.</p>';
      host.querySelectorAll('[data-document-id]').forEach(row => { row.onclick = () => {
        const d = DocumentStore.get(row.dataset.documentId);
        _switchModel(d.absolute_path, d.source_type, d.id);
      }; });
      const modified = document.getElementById('codeModifiedFiles');
      if (modified) modified.textContent = [...DocumentStore.documents.values()].filter(d => d.dirty).map(d => d.filename).join(', ') || 'Aucun fichier modifié.';
    },

    /* ---------------------------------------------------------- contenu */
    openFile(path) {
      this.openPath = path;
      this.openSource = 'fs';
      this._readFile(path);
      this.renderFiles();
    },

    openRemoteFile(d) {
      const path = d.path || d.remote_path;
      if (!path) { _trace('openRemoteFile: path vide', d); return; }
      const source = d.source_type || d.source || 'ssh';
      const id = d.document_id || _key(source, path);
      const content = d.content === undefined || d.content === null ? '' : String(d.content);
      const lang = LANG_MAP[String(d.language || '').toLowerCase()] || _detectLang(path);
      // Replaying an SSE snapshot must not overwrite unsaved editor changes.
      const previous = DocumentStore.get(id);
      if (previous && (d.restored || previous.request_id === d.request_id && d.request_id)) return;
      DocumentStore.add({ id, filename: d.filename || _modelName(path), language: lang,
        source_type: source, connector_id: d.connector_id || '', absolute_path: path,
        content, original_content: content, dirty: false, read_only: !!d.read_only,
        request_id: d.request_id,
        created_at: d.created_at || new Date().toISOString(), updated_at: new Date().toISOString() });
      DocumentStore.open(id);
      _tabs = DocumentStore.openDocumentIds.slice();
      _pendingActiveDocumentId = id;
      _trace('document created', d.filename || _modelName(path), 'document id', id, 'content length', content.length);
      _trace('document added to store', id, 'activeDocumentId', DocumentStore.activeDocumentId);
      if (!_editor) {
        _trace('openRemoteFile: éditeur pas prêt → pending', path);
        window.App?.goto('code');
        _ensureMonaco().catch((e) => {
          console.error('[CODE-TRACE] openRemoteFile: Monaco init échoué:', e);
          _showMonacoError();
        });
        return;
      }
      _trace('openRemoteFile', { path, source, lang, len: content.length });
      this.openPath = path;
      this.openSource = source;
      this.active = true;
      const cur = this.files.get(path) || { ts: Date.now() / 1000, kinds: new Set() };
      cur.kinds.add('read');
      this.files.set(path, cur);
      _getOrCreateModel(path, source, content, d.connector_id || '', lang, id);
      _openTab(path, source, id);
      _restoreActiveDocument();
      this.renderFiles();
      this._markChip();
      this._log('ok', _modelName(path) + ' chargé depuis ' + source.toUpperCase(),
        'open:' + source + ':' + path);
      if (location.hash !== '#code') window.App?.goto('code');
    },

    openGit() {
      if (!this._git) return;
      this.openPath = null;
      this.openSource = 'git';
      const content = this._git.detail || 'Aucune sortie.';
      if (!_editor) return;
      _getOrCreateModel('git-output', 'git', content, '', 'plaintext');
      _openTab('git-output', 'git');
      this.renderFiles();
    },

    async _readFile(path) {
      const r = await J.post('/api/tools/fs.read/run', { arguments: { path, max_lines: 10000 } });
      if (r.result && r.result.ok) {
        const content = r.result.output || '';
        const lang = _detectLang(path);
        this.openRemoteFile({path, source: 'fs', content, language: lang});
      } else {
        if (_editor) {
          _getOrCreateModel(path, 'fs', '// ' + String((r.error || r.result?.output || 'Lecture impossible.')).slice(0, 500), '', _detectLang(path));
          _openTab(path, 'fs');
        }
      }
    },

    renderView(text, head) {
      const hud = $('#codeFileHead');
      if (hud) hud.textContent = head || '';
      if (_editor && _activeKey) {
        const entry = _models.get(_activeKey);
        if (entry && entry.model.getValue() !== text) {
          entry.model.setValue(text || '');
          entry.original = text || '';
          entry.dirty = false;
          entry.status = 'saved';
          _updateStatusBar();
          _renderTabs();
        }
      }
    },

    /* -------------------------------------------------------- utilitaires */
    _extractPath(action) {
      const m = /:\s*(.+)$/.exec(String(action || ''));
      if (!m) return null;
      let p = m[1].trim().replace(/^['"]|['"]$/g, '');
      if (/^[A-Za-z]:[\\/]|^~[\\/]|^[\\/]|^\.(?:\.)?[\\/]/.test(p)) return p;
      return null;
    },

    _short(path, max = 48) {
      const s = String(path || '');
      return s.length > max ? '…' + s.slice(-(max - 1)) : s;
    },

    _name(path) {
      const s = String(path || '');
      const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
      return i >= 0 ? s.slice(i + 1) : s;
    },

    applyEditorSettings: _applyEditorSettings,
    inspect() {
      const d = DocumentStore.get(DocumentStore.activeDocumentId);
      const m = _models.get(DocumentStore.activeDocumentId)?.model;
      return { build_id: 'CODING_MERGE3_20260911_H', documents: DocumentStore.openDocumentIds.map(id => ({id, filename: DocumentStore.get(id).filename})),
        activeDocumentId: DocumentStore.activeDocumentId, language: m?.getLanguageId(), contentLength: m?.getValueLength(),
        modelMatches: !!m && _editor?.getModel() === m, dirty: d?.dirty, uri: m?.uri.toString() };
    },
  };

  /* ── Diff Button ──────────────────────────────────────────── */
  function _bindDiffButton() {
    const btn = document.getElementById('codeDiffBtn');
    if (btn) btn.addEventListener('click', (e) => { e.preventDefault(); _toggleDiff(); });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _bindDiffButton);
  } else {
    _bindDiffButton();
  }

  /* ── Monaco Error Fallback ────────────────────────────────── */
  function _showMonacoError() {
    const empty = document.getElementById('codeEditorEmpty');
    if (empty) {
      empty.innerHTML = `<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" style="color:var(--danger);opacity:.5"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>
        <p style="color:var(--danger)">Monaco Editor n'a pas pu être chargé</p>
        <p class="sub">Vérifie ta connexion Internet ou recharge la page.</p>`;
    }
  }
})();
