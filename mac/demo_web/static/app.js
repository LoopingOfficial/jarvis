/* Pilotage : /speak (edge-tts) + analyse audio + /check-system. */
(function () {
  const $ = (id) => document.getElementById(id);
  const promptEl = $('prompt'), speakBtn = $('speak'), checkBtn = $('check');
  const statusEl = $('status'), scan = $('scanline');
  const report = $('report'), reportSummary = $('reportSummary'), reportList = $('reportList');

  let audioCtx = null, analyser = null, freq = null, rafId = 0;

  function setStatus(text) { statusEl.textContent = text; }

  function ensureAudioGraph(el) {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.75;
      freq = new Uint8Array(analyser.frequencyBinCount);
      analyser.connect(audioCtx.destination);
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const src = audioCtx.createMediaElementSource(el);
    src.connect(analyser);
  }

  function startMeter() {
    cancelAnimationFrame(rafId);
    const tick = () => {
      analyser.getByteFrequencyData(freq);
      // on privilégie la bande vocale (~100 Hz – 3 kHz)
      let sum = 0, n = 0;
      for (let i = 2; i < 64; i++) { sum += freq[i]; n++; }
      const level = Math.min(1, (sum / n / 255) * 1.7);
      window.JarvisAvatar.setLevel(level);
      window.JarvisNetwork.pulse(level * 0.8);
      rafId = requestAnimationFrame(tick);
    };
    tick();
  }

  function stopMeter() {
    cancelAnimationFrame(rafId);
    window.JarvisAvatar.reset();
  }

  async function speak(text) {
    speakBtn.disabled = true;
    setStatus('synthèse vocale…');
    try {
      const res = await fetch('/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: 'fr-FR-HenriNeural', rate: '-4%', pitch: '-2Hz' }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      audio.crossOrigin = 'anonymous';
      ensureAudioGraph(audio);
      audio.onplay = () => { setStatus('jarvis speaking'); window.JarvisAvatar.burst(1); startMeter(); };
      audio.onended = audio.onerror = () => {
        stopMeter(); setStatus('system idle'); URL.revokeObjectURL(url);
      };
      await audio.play();
    } catch (err) {
      setStatus('erreur tts : ' + err.message);
    } finally {
      speakBtn.disabled = false;
    }
  }

  /** Diagnostic holographique complet. */
  async function trigger_system_check() {
    checkBtn.disabled = true;
    report.classList.remove('show');
    scan.classList.remove('run');
    void scan.offsetWidth;          // relance l'animation
    scan.classList.add('run');
    window.JarvisAvatar.burst(1.2);
    window.JarvisNetwork.reset();
    setStatus('running diagnostics…');

    try {
      const data = await (await fetch('/check-system')).json();
      reportSummary.textContent = data.summary;
      reportList.innerHTML = '';
      data.checks.forEach((c) => {
        const li = document.createElement('li');
        if (!c.ok) li.classList.add('bad');
        li.innerHTML = `<span class="dot"></span><span>${c.name}</span>` +
                       `<span class="detail">${c.detail || (c.ok ? 'ok' : 'échec')}</span>`;
        reportList.appendChild(li);
      });
      setTimeout(() => report.classList.add('show'), 900);
      setStatus(data.ok ? 'all systems nominal' : 'attention required');
      setTimeout(() => speak(data.ok
        ? `Diagnostic terminé. ${data.passed} contrôles sur ${data.total} validés. Tous les systèmes sont opérationnels.`
        : `Diagnostic terminé. ${data.passed} contrôles sur ${data.total} validés. Une anomalie a été détectée.`), 1100);
    } catch (err) {
      setStatus('diagnostic indisponible : ' + err.message);
    } finally {
      checkBtn.disabled = false;
    }
  }
  window.trigger_system_check = trigger_system_check;

  speakBtn.addEventListener('click', () => promptEl.value.trim() && speak(promptEl.value.trim()));
  promptEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') speakBtn.click(); });
  checkBtn.addEventListener('click', trigger_system_check);
  report.addEventListener('click', () => report.classList.remove('show'));
})();
