/* ==========================================================================
   JARVIS — Pièces jointes (File & Image Analysis)
   Barre de commande : bouton 📎, drag & drop, collage presse-papiers,
   vignettes, preview et envoi des `attachment_id` avec la commande.

   Pile : files → base64 (client) → POST /api/uploads → [ids] →
   POST /api/command { text, attachments: [ids] }.
   Le fichier n'est JAMAIS exécuté côté client : seul son contenu est envoyé.
   ========================================================================== */
(function () {
  'use strict';

  const MAX_FILES = 6;

  function bytesToMb(b) { return Math.round((b / 1024 / 1024) * 10) / 10; }
  function extOf(name) { const i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i + 1).toLowerCase(); }

  const KIND_LABEL = {
    image: 'Image', pdf: 'PDF', text: 'Texte/Code', csv: 'CSV',
    xlsx: 'XLSX', docx: 'DOCX',
  };
  const ICON_BY_EXT = {
    pdf: 'doc', xlsx: 'table', docx: 'doc', csv: 'table',
  };

  const AttachmentsUI = {
    items: [],        // { id, filename, kind, mime, size, ext, url, thumb }
    form: null,
    input: null,
    chips: null,
    dropZone: null,

    async init() {
      this.form = document.getElementById('convForm');
      this.input = document.getElementById('attachInput');
      this.chips = document.getElementById('attachChips');
      this.dropZone = document.getElementById('attachDropZone');
      if (!this.form || !this.input) return;

      const attachBtn = document.getElementById('attachBtn');
      if (attachBtn) {
        attachBtn.onclick = () => this.input.click();
        this.input.addEventListener('change', () => {
          this.addFiles(Array.from(this.input.files || []));
          this.input.value = '';
        });
      }

      this.form.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (this.dropZone) this.dropZone.hidden = false;
      });
      this.form.addEventListener('dragleave', (e) => {
        if (!this.form.contains(e.relatedTarget)) this.hideDrop();
      });
      this.form.addEventListener('drop', (e) => {
        e.preventDefault();
        this.hideDrop();
        const files = Array.from(e.dataTransfer?.files || []);
        this.addFiles(files);
      });

      const input = document.getElementById('convInput');
      if (input) {
        input.addEventListener('paste', (e) => {
          const clipboardFiles = Array.from(e.clipboardData?.items || [])
            .filter((it) => it.kind === 'file')
            .map((it) => it.getAsFile())
            .filter(Boolean);
          if (!clipboardFiles.length) return;
          e.preventDefault();
          this.addFiles(clipboardFiles);
        });
      }
      this.caps = null;
      try { this.caps = await J.get('/api/uploads/capabilities'); } catch { /* offline */ }
    },

    hideDrop() { if (this.dropZone) this.dropZone.hidden = true; },

    async addFiles(files) {
      files = files.slice(0, MAX_FILES - this.items.length);
      if (!files.length) return;
      const maxBytes = this.caps?.max_upload_bytes || 25 * 1024 * 1024;
      const payload = [];
      for (const file of files) {
        const ext = extOf(file.name);
        if (!ext) {
          window.toast?.(`« ${file.name} » : format sans extension, ignoré.`, 'warn');
          continue;
        }
        if (file.size > maxBytes) {
          window.toast?.(`« ${file.name} » : ${bytesToMb(file.size)} Mo dépasse la limite (${bytesToMb(maxBytes)} Mo).`, 'warn');
          continue;
        }
        const b64 = await fileToBase64(file);
        payload.push({ filename: file.name, data_b64: b64 });
      }
      if (!payload.length) return;
      const res = await J.post('/api/uploads', { attachments: payload });
      const created = res.attachments || [];
      created.forEach((att) => this.items.push(att));
      (res.errors || []).forEach((err) =>
        window.toast?.(`${err.filename || 'Fichier'} : ${err.error}`, 'warn'));
      this.render();
    },

    remove(id) {
      this.items = this.items.filter((it) => it.id !== id);
      this.render();
    },

    clear() {
      this.items = [];
      this.render();
    },

    takeIds() {
      const ids = this.items.map((it) => it.id);
      return ids;
    },

    render() {
      if (!this.chips) return;
      this.chips.hidden = !this.items.length;
      this.chips.innerHTML = this.items.map((it) => {
        const label = KIND_LABEL[it.kind] || (it.ext || '').toUpperCase();
        const thumb = it.thumb ? `<img class="att-thumb" src="${it.thumb}" alt="">` : '';
        const icon = it.kind === 'image' ? '' :
          `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>`;
        return `<span class="att-chip ${it.kind === 'image' ? 'image' : ''}" title="${esc(it.filename)} · ${esc(label)}">
          ${thumb}${icon}<span class="att-cc-name">${esc(it.filename)}</span>
          <button type="button" class="att-remove" data-att="${esc(it.id)}" title="Retirer">×</button>
        </span>`;
      }).join('');
      this.chips.querySelectorAll('[data-att]').forEach((b) => {
        b.onclick = () => this.remove(b.dataset.att);
      });
    },
  };

  async function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(new Error('Lecture du fichier impossible'));
      r.onload = () => resolve(String(r.result).split(',')[1] || '');
      r.readAsDataURL(file);
    });
  }

  window.AttachmentsUI = AttachmentsUI;
})();