/* ==========================================================================
   Tests frontend de l'AudioManager (exécutés avec Node, DOM simulé).
   Vérifient que le navigateur ne peut pas rejouer le message d'accueil et
   que JARVIS ne coupe jamais sa propre voix.
       node tests/test_voice_frontend.js
   ========================================================================== */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); passed++; }
  catch (e) { console.log(`  ✗ ${name}\n      ${e.message}`); failed++; }
}

/* --------------------------- environnement simulé ------------------------ */
function buildSandbox({ greetResponse } = {}) {
  const calls = { greeting: 0, session: 0, state: [], heartbeat: 0, command: 0 };
  const spoken = [];
  let cancelCount = 0;
  const recognitionInstances = [];

  class FakeRecognition {
    constructor() {
      this.started = 0;
      this.stopped = 0;
      recognitionInstances.push(this);
    }
    start() { this.started++; if (this.onstart) this.onstart(); }
    stop() { this.stopped++; if (this.onend) this.onend(); }
  }

  const utterances = [];
  class FakeUtterance {
    constructor(text) { this.text = text; utterances.push(this); }
  }

  const speechSynthesis = {
    speak(u) { spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 0); },
    cancel() { cancelCount++; },
    getVoices: () => [{ name: 'Thomas', lang: 'fr-FR' }],
  };

  const listeners = {};
  const J = {
    state: { conversation: null },
    on(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
    fire(type, payload) { (listeners[type] || []).forEach((cb) => cb(payload)); },
    async post(url, body) {
      if (url === '/api/voice/session') {
        calls.session++;
        return { ok: true, session: { session_id: 'sess-1', client_id: 'c1',
          greeted: calls.session > 1, resumed: calls.session > 1 } };
      }
      if (url === '/api/voice/greeting') {
        calls.greeting++;
        const speak = greetResponse !== undefined ? greetResponse : calls.greeting === 1;
        return { ok: true, speak, text: speak ? 'Bonjour Jérôme.' : '' };
      }
      if (url === '/api/voice/state') { calls.state.push(body.state); return { ok: true }; }
      if (url === '/api/voice/heartbeat') { calls.heartbeat++; return { ok: true }; }
      if (url === '/api/command') {
        calls.command++;
        return { ok: true, response: 'Le serveur fonctionne correctement.', conversation_id: 'conv-1' };
      }
      return { ok: true };
    },
  };

  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval, Date,
    J,
    toast: () => {},
    localStorage: {
      _d: {},
      getItem(k) { return this._d[k] || null; },
      setItem(k, v) { this._d[k] = v; },
    },
    window: {
      SpeechRecognition: FakeRecognition,
      speechSynthesis,
      SpeechSynthesisUtterance: FakeUtterance,
    },
    _probe: { calls, spoken, utterances, recognitionInstances,
      cancels: () => cancelCount, listeners },
  };
  sandbox.window.localStorage = sandbox.localStorage;
  sandbox.SpeechSynthesisUtterance = FakeUtterance;
  sandbox.global = sandbox;
  vm.createContext(sandbox);
  const code = fs.readFileSync(path.join(__dirname, '..', 'ui', 'js', 'voice.js'), 'utf8');
  vm.runInContext(code, sandbox);
  return sandbox;
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/* --------------------------------- tests -------------------------------- */
(async function run() {
  console.log('\nTests frontend — AudioManager\n');

  // 1. instance unique
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    test('une seule instance d\'AudioManager (pas de listeners concurrents)', () => {
      assert.ok(vmgr, 'VoiceManager absent');
      assert.strictEqual(sb._probe.recognitionInstances.length, 0,
        'aucune reconnaissance ne doit être créée avant init()');
    });
  }

  // 2. greeting demandé une seule fois
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'wake_word', speak_responses: true, wake_word: 'jarvis' });
    await wait(30);
    test('TEST 1 · le greeting est demandé exactement une fois au démarrage', () => {
      assert.strictEqual(sb._probe.calls.greeting, 1);
      assert.strictEqual(sb._probe.spoken.length, 1);
      assert.match(sb._probe.spoken[0], /Bonjour/);
    });

    // Simule 5 minutes : heartbeats, redémarrages STT, timeouts VAD.
    for (let i = 0; i < 30; i++) {
      vmgr.setState('LISTENING', 'micro');
      vmgr.setState('IDLE', 'timeout vad');
      const rec = sb._probe.recognitionInstances[0];
      if (rec && rec.onend) rec.onend();
    }
    await wait(30);
    test('TEST 1 · aucun nouveau greeting après 5 minutes d\'activité simulée', () => {
      assert.strictEqual(sb._probe.calls.greeting, 1,
        'l\'endpoint greeting a été rappelé : régression');
      assert.strictEqual(sb._probe.spoken.length, 1);
    });

    test('TEST 6 · le redémarrage STT ne prononce rien', () => {
      assert.strictEqual(sb._probe.spoken.length, 1);
    });
    test('TEST 7 · le timeout VAD ramène en IDLE sans parler', () => {
      assert.strictEqual(vmgr.state, 'IDLE');
      assert.strictEqual(sb._probe.spoken.length, 1);
    });
  }

  // 3. rechargement de page = session reprise = pas de greeting
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'wake_word' });
    await wait(20);
    const before = sb._probe.spoken.length;
    // second init = rechargement/reconnexion
    vmgr.greetingRequested = false;
    await vmgr.init({ mode: 'wake_word' });
    await wait(20);
    test('TEST 5 · reconnexion/rechargement : session reprise, aucun greeting', () => {
      assert.strictEqual(sb._probe.spoken.length, before,
        'un greeting a été rejoué après reconnexion');
    });
  }

  // 4. la réponse n'est jamais coupée par un greeting ni par un timer
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'wake_word', speak_responses: true });
    await wait(20);
    const cancelsBefore = sb._probe.cancels();
    vmgr.setState('PROCESSING', 'commande');
    vmgr.speak('Voici une réponse longue. Elle contient plusieurs phrases. '
      + 'Elle doit être prononcée intégralement, sans interruption.', { kind: 'reply' });
    // pendant la parole : heartbeats et timers
    for (let i = 0; i < 10; i++) {
      await J_noop();
      vmgr._armSilenceTimer?.();
    }
    await wait(60);
    test('TEST 2 · la réponse est prononcée en entier, sans cancel() intempestif', () => {
      assert.strictEqual(sb._probe.cancels(), cancelsBefore,
        'speechSynthesis.cancel() a été appelé pendant la parole');
      const joined = sb._probe.spoken.join(' ');
      assert.match(joined, /réponse longue/);
      assert.match(joined, /sans interruption/);
    });
    test('TEST 3 · après la parole, retour silencieux en IDLE', () => {
      assert.strictEqual(vmgr.state, 'IDLE');
      assert.strictEqual(sb._probe.calls.greeting, 1);
    });
  }

  // 5. JARVIS ne s'entend pas lui-même
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'always_listening', interruptible_speech: false, speak_responses: true });
    await wait(20);
    vmgr.startListening();
    const rec = sb._probe.recognitionInstances[0];
    vmgr.setState('PROCESSING');
    vmgr.speak('Le conteneur nginx a été redémarré.', { kind: 'reply' });
    const commandsBefore = sb._probe.calls.command;
    // Le micro « entend » la voix de JARVIS pendant SPEAKING
    if (rec.onresult) {
      rec.onresult({ resultIndex: 0,
        results: [Object.assign(['Le conteneur nginx a été redémarré'].map((t) => ({ transcript: t })),
          { isFinal: true, 0: { transcript: 'Le conteneur nginx a été redémarré' }, length: 1 })] });
    }
    test('TEST · la voix de JARVIS n\'est jamais prise pour une commande', () => {
      assert.strictEqual(sb._probe.calls.command, commandsBefore,
        'JARVIS s\'est auto-déclenché sur sa propre voix');
    });
    test('TEST 4 · réactiver le micro n\'entraîne aucun bonjour', () => {
      vmgr.stopListening();
      vmgr.startListening();
      assert.strictEqual(sb._probe.calls.greeting, 1);
    });
  }

  // 6. wake word : accusé bref, jamais le greeting complet
  {
    const sb = buildSandbox({ greetResponse: false });
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'wake_word', wake_word: 'jarvis', wake_ack: 'Oui ?',
      wake_ack_enabled: true, speak_responses: true });
    await wait(20);
    sb._probe.spoken.length = 0;
    vmgr._handleTranscript('Jarvis');
    await wait(20);
    test('TEST · « Jarvis » seul déclenche « Oui ? », jamais le message d\'accueil', () => {
      assert.strictEqual(sb._probe.spoken.length, 1);
      assert.strictEqual(sb._probe.spoken[0], 'Oui ?');
      assert.ok(!sb._probe.spoken[0].includes('Bonjour'));
    });
    test('TEST · le mot d\'éveil est retiré de la commande', () => {
      const stripped = vmgr._stripWakeWord('jarvis vérifie mon serveur', 'Jarvis vérifie mon serveur');
      assert.strictEqual(stripped, 'vérifie mon serveur');
    });
  }

  // 7. transitions interdites refusées
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'push_to_talk' });
    vmgr.state = 'LISTENING';
    test('TEST · transitions non autorisées refusées', () => {
      assert.strictEqual(vmgr.setState('EXECUTING'), false);
      assert.strictEqual(vmgr.state, 'LISTENING');
      assert.strictEqual(vmgr.setState('PROCESSING'), true);
    });
  }

  // 8. mot d'arrêt
  {
    const sb = buildSandbox();
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'always_listening', stop_words: ['stop', 'arrête'], speak_responses: true });
    await wait(20);
    vmgr.setState('PROCESSING');
    vmgr.speak('Une très longue explication qui pourrait durer.', { kind: 'reply' });
    const cancelsBefore = sb._probe.cancels();
    vmgr._handleTranscript('stop');
    test('TEST · seul un mot d\'arrêt explicite interrompt la parole', () => {
      assert.strictEqual(sb._probe.cancels(), cancelsBefore + 1);
      assert.strictEqual(vmgr.state, 'IDLE');
    });
  }


  // 9. chien de garde TTS : jamais bloqué en SPEAKING
  {
    const sb = buildSandbox({ greetResponse: false });
    // moteur TTS défaillant : ne déclenche jamais onend
    sb.window.speechSynthesis.speak = (u) => { sb._probe.spoken.push(u.text); };
    const vmgr = sb.window.VoiceManager;
    await vmgr.init({ mode: 'push_to_talk', speak_responses: true, speech_rate: 1 });
    await wait(20);
    vmgr.setState('PROCESSING');
    vmgr.speak('Ok.', { kind: 'reply' });
    assert.strictEqual(vmgr.state, 'SPEAKING');
    await wait(3200);
    test('TEST · un moteur TTS muet ne bloque pas JARVIS en SPEAKING', () => {
      assert.strictEqual(vmgr.state, 'IDLE',
        'le chien de garde n\'a pas débloqué la machine à états');
    });
  }

  console.log(`\n${passed} réussis, ${failed} échoués\n`);
  process.exit(failed ? 1 : 0);
})();

function J_noop() { return new Promise((r) => setTimeout(r, 1)); }
