/* JARVIS 4 — Terminal intégré : exécute des commandes réelles sur le PC via
 * l'outil backend « terminal.run » (cmd.exe). Aucune simulation.
 * Gère la confirmation serveur : si le backend demande confirmation, un modal
 * s'affiche et le rappel repart avec confirmed:true.
 */
(function () {
  const STORE = 'jarvis.term.history';

  window.Terminal = {
    HISTORY: [],
    _busy: false,

    /* ------------------------------------------------------- entrées */
    render() {
      const input = $('#termInput');
      if (!input) return;
      this._restoreHistory();
      input.focus();
      input.scrollIntoView({ block: 'nearest' });
    },

    clear() {
      const log = $('#termLog');
      if (log) log.innerHTML = '';
    },

    addLine(kind, text) {
      const log = $('#termLog');
      if (!log) return;
      const line = document.createElement('div');
      line.className = 'term-line ' + kind;
      line.innerHTML = `<span class="term-p">${kind === 'cmd' ? esc('PS> ') : ''}</span>` +
        `<span class="term-t">${esc(text == null ? '' : String(text))}</span>`;
      log.appendChild(line);
      this._scroll(log);
    },

    type(text) {
      const cmd = String(text || '').trim();
      if (!cmd || this._busy) return;
      if (location.hash !== '#terminal') window.App?.goto('terminal');
      const input = $('#termInput');
      if (input) {
        input.value = cmd;
        input.focus();
      }
      this._submit(cmd);
    },

    /* ------------------------------------------------------- exécution */
    async _submit(command) {
      const input = $('#termInput');
      if (input) input.value = '';
      this._pushHistory(command);
      this.addLine('cmd', command);
      this.addLine('info', 'Exécution de la commande…');
      this._busy = true;

      const res = await J.post('/api/tools/terminal.run/run', {
        arguments: { command },
        confirmed: false,
      }).catch((err) => ({ error: err && err.message ? err.message : 'Requête impossible.' }));

      if (res && res.needs_confirmation) {
        this._confirm(res.needs_confirmation, res.message, command);
        return;
      }
      this._showResult(res, command);
    },

    async _runConfirmed(command) {
      this._busy = true;
      const res = await J.post('/api/tools/terminal.run/run', {
        arguments: { command },
        confirmed: true,
      }).catch((err) => ({ error: err && err.message ? err.message : 'Requête impossible.' }));
      this._showResult(res, command);
    },

    _showResult(res, command) {
      this._busy = false;
      if (!res || res.error) {
        this.addLine('err', (res && res.error) || 'Échec de l\'exécution.');
        return;
      }
      const r = res.result;
      if (r && 'ok' in r) {
        this.addLine(r.ok ? 'out' : 'err', r.output || (r.ok ? 'Terminé sans sortie.' : `Exit code ${r.data && r.data.exit_code}`));
        if (r.ok && r.data && 'exit_code' in r.data) {
          this.addLine('meta', `exit code : ${r.data.exit_code}`);
        }
        if (Array.isArray(r.artifacts) && r.artifacts.length) {
          for (const a of r.artifacts) this.addLine('meta', `artefact : ${a.type || '?'} — ${a.path || a.url || ''}`);
        }
      } else {
        this.addLine('meta', res.message || 'Terminé.');
      }
    },

    _confirm(pending, message, command) {
      this._busy = false;
      const danger = pending.risk === 'destructive';
      const m = modal({
        title: 'Confirmation requise',
        body: `<div class="risk-banner ${danger ? 'destructive' : ''}">${icon('alert', 16)}
            <div><b>${esc(pending.action || '')}</b><br>${esc(pending.reason || '')}</div></div>
          <p class="text-dim" style="font-size:11.5px;margin:0">${esc(message || '')}</p>`,
        footer: `<button class="btn" data-no>Refuser</button>
                 <button class="btn ${danger ? 'danger' : 'primary'}" data-yes>Confirmer et exécuter</button>`,
      });
      m.$('[data-yes]').onclick = () => {
        m.close();
        this.addLine('warn', 'Commande confirmée : exécution…');
        this._runConfirmed(command);
      };
      m.$('[data-no]').onclick = () => {
        m.close();
        this.addLine('err', 'Commande refusée. Aucune exécution.');
      };
    },

    /* ------------------------------------------------------- utilitaires */
    _scroll(log) {
      log.scrollTop = log.scrollHeight;
    },

    _restoreHistory() {
      try {
        const raw = sessionStorage.getItem(STORE);
        if (raw) this.HISTORY = JSON.parse(raw);
      } catch { this.HISTORY = []; }
      if (this._boundKeys) return;
      this._boundKeys = true;
      const input = $('#termInput');
      if (input) input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const cmd = input.value.trim();
          if (!cmd || this._busy) return;
          this._submit(cmd);
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault();
          if (!this.HISTORY.length) return;
          if (!this._histIdx) this._histIdx = this.HISTORY.length;
          this._histIdx += e.key === 'ArrowUp' ? -1 : 1;
          this._histIdx = Math.max(0, Math.min(this.HISTORY.length, this._histIdx));
          input.value = this.HISTORY[this._histIdx] || '';
        }
      });
    },

    _pushHistory(cmd) {
      if (this.HISTORY[this.HISTORY.length - 1] !== cmd) this.HISTORY.push(cmd);
      if (this.HISTORY.length > 100) this.HISTORY.shift();
      this._histIdx = this.HISTORY.length;
      try { sessionStorage.setItem(STORE, JSON.stringify(this.HISTORY)); } catch {}
    },
  };
})();