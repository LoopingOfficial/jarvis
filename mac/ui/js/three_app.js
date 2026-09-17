/* JARVIS — Bootstrap 3D : instancie l'avatar humain + le Brain Atlas sur leurs
 * canvases et expose l'API globale (window.JarvisAvatar / window.JarvisBrain).
 * La câblerie d'événements vit dans app.js (une seule source de vérité SSE).
 *
 * `window.JarvisRobot` reste disponible : c'est un adaptateur vers l'avatar,
 * pour que le code existant (badges d'état, qualité 3D) continue de marcher.
 */
import { createHoloHumanoid } from './avatar/holo_humanoid.js?v=HOLO_HUMANOID_1';
import { BrainAtlas } from './brain_atlas.js';

const settings = (() => {
  try { return JSON.parse(sessionStorage.getItem('jarvis.settings') || '{}'); }
  catch { return {}; }
})();
const qualityMap = ['low', 'balanced', 'high', 'ultra'];
const quality = qualityMap.includes(settings.appearance?.quality)
  ? settings.appearance.quality : 'balanced';

export function boot() {
  const out = { avatar: null, brain: null, ready: false };

  const stageCanvas = document.getElementById('robotStage');
  if (stageCanvas) {
    // Avatar 100 % procedural : aucun GLB a charger, donc aucun echec de
    // reseau, de rig ou de cache a gerer. Le rendu s'attache directement au
    // canvas deja present dans la page.
    createHoloHumanoid({ canvas: stageCanvas, framing: 'FULL' })
      .then((body) => {
        out.avatar = body;
        out.component = body;
        window.JarvisAvatar = body;
        window.JarvisAvatar3DInstance = body;
        window.JarvisRobot = makeRobotAdapter(body);
        window.dispatchEvent(new CustomEvent('jarvis-avatar-ready', { detail: body }));
      })
      .catch((err) => {
        console.warn('Avatar holographique indisponible :', err);
        showFallback('robotFallback');
      });
  }

  const brainCanvas = document.getElementById('brainStage');
  if (brainCanvas && window.WebGLRenderingContext) {
    try {
      out.brain = new BrainAtlas({ canvas: brainCanvas, quality });
      window.JarvisBrain = out.brain;
      out.brain.loadBrain({ nodes: [], edges: [] });
    } catch (err) {
      console.warn('Brain Atlas 3D indisponible :', err);
      showFallback('brainFallback');
    }
  } else {
    showFallback('brainFallback');
  }

  out.ready = !!(out.component || out.brain);
  window.Jarvis3D = out;

  if (out.brain) {
    window.fetch('/api/brain')
      .then((r) => r.json())
      .then((data) => {
        out.brain.loadBrain(data);
        const hudN = document.getElementById('brainNodeCount');
        const hudE = document.getElementById('brainEdgeCount');
        if (hudN) hudN.textContent = (data.nodes || []).length;
        if (hudE) hudE.textContent = (data.edges || []).length;
        const list = document.getElementById('brainFallbackList');
        if (list && !(data.nodes || []).length) {
          for (const n of (data.nodes || []).slice(0, 40)) {
            const d = document.createElement('div');
            d.className = 'bf-item';
            d.innerHTML = `<i class="bf-dot" style="background:${familyHex(n.family)}"></i>`
              + `<span>${esc(n.label || n.id)}</span><small>${esc(n.family || '')}</small>`;
            list.appendChild(d);
          }
        }
      })
      .catch(() => {});
  }
  return out;
}

/**
 * Adaptateur de compatibilité : l'ancienne API `JarvisRobot` continue
 * d'exister, mais elle pilote désormais un véritable humain 3D.
 */
function makeRobotAdapter(avatar) {
  const STATE_MAP = {
    IDLE: 'IDLE', LISTENING: 'LISTENING', THINKING: 'THINKING', RECALLING: 'RECALLING',
    USING_TOOL: 'ACTING', CODING: 'CODING', BROWSING: 'BROWSING', DEPLOYING: 'DEPLOYING',
    LEARNING: 'RECALLING', VERIFYING: 'ACTING', SPEAKING: 'SPEAKING',
    SUCCESS: 'SUCCESS', WARNING: 'WARNING', ERROR: 'ERROR', SLEEPING: 'SLEEPING',
    WALKING: 'WALKING', UNDERSTANDING: 'UNDERSTANDING', ACTING: 'ACTING',
  };
  return {
    get state() { return avatar.state; },
    setState(state, extra = {}) {
      avatar.setState(STATE_MAP[String(state || '').toUpperCase()] || 'IDLE', extra);
    },
    setQuality(q) { avatar.setQuality?.(q === 'reduced' ? 'low' : q); },
    setMode(mode) {
      avatar.setViewMode?.(mode);
      // Le choix est mémorisé côté serveur : il survit à un rechargement.
      fetch('/api/settings/avatar', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ view: mode }),
      }).catch(() => {});
    },
    moveTo(place, options) { return avatar.moveTo?.(place, options); },
    gesture(plan) { return avatar.gesture?.(plan); },
    lookAt(target) { avatar.lookAt(target); },
    setCameraMode(mode) { return avatar.setCameraMode?.(mode); },
    setSpeakLevel(level) { avatar.setAudioLevel(level); },
    // Nom utilisé par l'UI principale pour relayer le niveau TTS réel.
    setSpeakingLevel(level) { avatar.setAudioLevel(level); },
    setVisible(visible) {
      const canvas = avatar.canvas || document.getElementById('robotStage');
      if (canvas) canvas.style.visibility = visible ? 'visible' : 'hidden';
    },
    avatar,
  };
}

function showFallback(id) {
  const el = document.getElementById(id);
  if (el) el.hidden = false;
}

function familyHex(family) {
  const map = {
    MEMORY: '#22d3ee', KNOWLEDGE: '#4ade80', PROJECTS: '#60a5fa', PEOPLE: '#f472b6',
    SERVERS: '#94a3b8', TOOLS: '#fbbf24', WORKFLOWS: '#a78bfa', APPLICATIONS: '#34d399',
    AUTOMATIONS: '#fb923c', DECISIONS: '#fbbf24', ERRORS: '#fb7185',
    SOLUTIONS: '#4ade80', DOCUMENTS: '#818cf8', JARVIS: '#22d3ee',
  };
  return map[String(family || '').toUpperCase()] || '#22d3ee';
}

function esc(s) {
  const d = document.createElement('span');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

export default boot;
if (document.getElementById('robotStage')) {
  boot();
}
