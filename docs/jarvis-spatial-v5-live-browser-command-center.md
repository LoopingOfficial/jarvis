# JARVIS SPATIAL V5 — LIVE BROWSER PREVIEW + COMMAND CENTER FINAL

## Objectif
Aperçu navigateur **réel** visible dans le Command Center Spatial V5. Chaque
pixel affiché provient d'une session Playwright headless partagée ; aucune
donnée n'est simulée. L'agent IA peut piloter cette session via de nouveaux
tools (`browser.navigate`, `browser.click`, etc.) tandis que l'utilisateur
observe l'aperçu en temps réel.

---

## 1. Architecture

### Backend
| Fichier | Rôle |
|---|---|
| `jarvis/browser_manager.py` | **BrowserManager** — thread Playwright unique, queue de commandes, events SSE. |
| `jarvis/core.py` | Injection `self.browser = BrowserManager(self)` + `set_manager(self.browser)` |
| `jarvis/events.py` | `emit(..., cache=False)` — frames haute fréquence exclues de l'historique SSE. |
| `jarvis/server.py` | Routes `/api/browser/*` (status, frame, start, action, touch, close, user-action) |
| `jarvis/self_upgrade/tools_def.py` | Tools agent : `browser.navigate`, `.click`, `.type`, `.scroll`, `.wait`, `.back`, `.pause`, `.close` |

### Frontend
| Fichier | Rôle |
|---|---|
| `ui/js/v5/spatial_browser.js` | Dock flottant : aperçu live, barre d'adresse, log d'actions, gestion gate. |
| `ui/js/v5/spatial_events.js` | Branchement SSE : `browser.frame`, `browser.navigate`, `browser.gate`, `browser.download`, `browser.error`, `browser.session.*` |
| `ui/js/v5/spatial_shell.js` | Sidebar RAIL avec labels, `.v5-rpanel` (Brain/Agent/Contexte). |
| `ui/css/v5/spatial.css` | Styles : sidebar labels, `.v5-rpanel`, `.v5-browser` dock (3 modes), responsive. |
| `ui/index.html` | Script tag `spatial_browser.js`, cache bump `JARVIS_LIVEBROWSER_1`. |

---

## 2. Événements SSE du navigateur

| Événement | Données | cache |
|---|---|---|
| `browser.session.started` | `{url}` | Oui |
| `browser.navigate` | `{url, title}` | Oui |
| `browser.action` | `{kind, target, private?}` | Oui |
| `browser.frame` | `{url, w, h, jpeg, halo?}` | **Non** (haute fréquence) |
| `browser.gate` | `{message}` | Oui |
| `browser.session.resumed` | `{url}` | Oui |
| `browser.download` | `{filename}` | Oui |
| `browser.error` | `{message, stage}` | Oui |
| `browser.session.finished` | `{}` | Oui |

---

## 3. Sécurité

- **Secrets** : les valeurs contenant `password`, `token`, `secret`, `key`,
  `cookie` ne sont **jamais** émises ; le champ affiché est « champ protégé ».
- **Pas de faux boutons** : tous les contrôles (back, forward, navigate,
  REPRENDRE) appellent l'API réelle. Si Playwright n'est pas installé, un
  message d'erreur honnête est affiché (503).
- **Gate** : en cas de CAPTCHA/action manuelle, l'automation est **mise en
  pause** (thread bloqué). Jamais de bypass.
- **Pas de données simulées** : le dock affiche uniquement les frames
  réellement reçues par SSE. Sans session active, il indique
  « SESSION FERMÉE ».

---

## 4. API routes

| Méthode | Route | Description |
|---|---|---|
| GET | `/api/browser/status` | État courant (available, active, url, state, gate) |
| GET | `/api/browser/frame` | Dernière frame JPEG en base64 (data URL) |
| POST | `/api/browser/start` | Ouvre une URL (launch Playwright si nécessaire) |
| POST | `/api/browser/action` | Exécute une action (navigate, click, type, scroll, wait, back, forward, pause, resume, close) |
| POST | `/api/browser/touch` | Maintient le stream de frames actif (appelé toutes les secondes) |
| POST | `/api/browser/close` | Ferme la session |
| POST | `/api/browser/user-action` | Reprend après gate (`action: 'pause'|'resume'`) |

---

## 5. Tools agent

| Tool | Description |
|---|---|
| `browser.navigate` | Ouvre une URL dans la session live |
| `browser.click` | Clique sur un élément visible (texte ou sélecteur CSS) |
| `browser.type` | Saisit une valeur dans un champ (`private=true` pour secrets) |
| `browser.scroll` | Défile la page de y pixels |
| `browser.wait` | Attend ms millisecondes |
| `browser.back` | Page précédente |
| `browser.pause` | Demande une action manuelle à l'utilisateur |
| `browser.close` | Ferme la session |

---

## 6. UI — Command Center

### Sidebar (RAIL)
Labels en hover : HOME, CHAT, BRAIN, AGENTS, TOOLS, SYNC, WORKSPACE, VOICE, SETTINGS.
L'état actif est souligné en cyan avec une bordure.

### Panneau droit (.v5-rpanel)
Situé au-dessus du dock navigateur :
- **BRAIN** : état actuel du brain (IDLE/RECALLING/PROCESSING…) + nombre de concepts.
- **AGENT ACTIF** : nom de l'agent en cours + statut.
- **CONTEXTE** : information contextuelle (masquée en responsive).

### Browser Preview Dock (3 modes)
- **COMPACT (36 %)** : aperçu permanent à droite, en dessous du rpanel.
- **EXPANSION (50 %)** : appel visuel en cours, taille étendue.
- **FULLSCREEN** : le navigateur prend tout l'écran (HUD + barre d'adresse restent visibles).

Le dock contient :
1. Barre d'adresse (URL + boutons back/forward/go).
2. Zone d'aperçu : image JPEG reçue en temps réel.
3. Halo cyan sur les zones cliquées.
4. Panneau « ACTION REQUISE » + bouton REPRENDRE (gate).
5. Log des actions (types CLICK/TYPE/SCROLL… ; les secrets sont masqués).
6. État : LIVE (vert), CHARGÉ, ATTENTE (amber), — (inactif).

### Responsive
- `≤1440px` : rpanel réduit, contexte masqué.
- `≤1100px` : rpanel et mods masqués, dock prend toute la largeur restante.
- `≤860px` : rail horizontal en bas, dock plein écran par défaut.

---

## 7. Activation

Playwright reste **optionnel**. Pour activer la session navigateur :
```bash
.venv\Scripts\python.exe -m pip install playwright
.venv\Scripts\python.exe -m playwright install chromium
```

Sans Playwright, le dock affiche « Indisponible » et renvoie une erreur 503
honnete. Aucun faux aperçu n'est généré.

---

## 8. Mesures de performance

- **Frames** : ~4-5 FPS, JPEG qualité 72, via `emit(..., cache=False)`.
- **Touch** : le frontend envoie `/api/browser/touch` toutes les secondes tant
  que le dock est visible. Sans touch pendant 2.5s, le stream s'arrête.
- **CPU** : thread Playwright en daemon ; arrêt propre via queue.
- **JS** : dock invisible par défaut (`pointer-events:none`, `opacity:0`).

---

## 9. Contrôle qualité

Selftest (`await JarvisSelfTest.run()`) :
- Le module `JarvisBrowser` est présent.
- Le dock expose les 3 tailles (`SIZES.half/wide/full`).
- Le dock est fermé par défaut.
- Le dock s'affiche correctement avec tous les éléments DOM attendus.
- Le dock se ferme proprement et coupe le stream.

---

## 10. Fichiers modifiés

| Fichier | Modification |
|---|---|
| `jarvis/browser_manager.py` | **Nouveau** — BrowserManager + get_manager() |
| `jarvis/core.py` | Import + injection `self.browser` |
| `jarvis/events.py` | `emit()` — paramètre `cache` ajouté |
| `jarvis/server.py` | Routes `/api/browser/*` |
| `jarvis/self_upgrade/tools_def.py` | Tools agent browser.* |
| `ui/js/v5/spatial_browser.js` | **Nouveau** — Dock navigateur UI |
| `ui/js/v5/spatial_events.js` | Branchement SSE browser.* |
| `ui/js/v5/spatial_shell.js` | Sidebar labels + rpanel + setTask rpanel |
| `ui/js/v5/spatial_selftest.js` | Tests browser dock |
| `ui/css/v5/spatial.css` | Sidebar, rpanel, dock, responsive |
| `ui/index.html` | Script tag + cache bump `JARVIS_LIVEBROWSER_1` |
