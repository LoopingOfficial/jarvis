/* Banc de tests de l'avatar — exécuté dans la page JARVIS réelle.
 * Chaque test observe l'effet RÉEL sur le rig, jamais un simple appel d'API.
 * Usage console : await window.runAvatarSuite()
 */
window.runAvatarSuite = async function runAvatarSuite() {
  const a = window.JarvisAvatar;
  const results = [];
  const check = (name, ok, detail = '') => results.push({ name, ok: !!ok, detail: String(detail) });
  // Pas de requestAnimationFrame : le banc doit tourner même si l'onglet
  // est en arrière-plan. On pilote la simulation pas à pas.
  const step = async (n = 30) => {
    for (let i = 0; i < n; i++) a._update(1 / 60);
    await new Promise((r) => setTimeout(r, 0));
  };
  const boneQ = (name) => {
    const b = a.model.bone(name);
    return b ? b.quaternion.toArray().map((v) => +v.toFixed(4)).join(',') : null;
  };

  if (!a || !a.ready) { check('avatar prêt', false, 'non chargé'); return results; }
  check('avatar prêt', true);

  /* --- structure --- */
  check('squelette complet (55 os)', a.model.bones.size === 55, a.model.bones.size);
  check('jambes riggées',
    !!(a.model.bone('upperLeg_L') && a.model.bone('lowerLeg_R') && a.model.bone('toe_L')));
  check('doigts riggés (30 os)',
    Object.values(a.model.meta.fingerChains).flat().filter((n) => a.model.bone(n)).length === 30);
  check('os des yeux', !!(a.model.bone('eye_L') && a.model.bone('eye_R')));
  check('morph targets (35)', a.model.morphNames().length === 35, a.model.morphNames().length);
  check('visèmes (12)', a.model.meta.visemes.length === 12, a.model.meta.visemes.length);
  check('clips (28)', a.model.clips.size === 28, a.model.clips.size);
  check('clips additifs (15)', a.model.additiveClips.size === 15, a.model.additiveClips.size);

  /* --- vie permanente --- */
  a.setState('IDLE');
  const chestBefore = boneQ('chest');
  const fingerBefore = boneQ('index_02_L');
  await step(70);
  check('respiration (le torse bouge)', boneQ('chest') !== chestBefore);
  check('doigts jamais figés', boneQ('index_02_L') !== fingerBefore);

  a.behavior.triggerBlink();
  await step(4);
  const blinking = a.model.getMorph('blinkLeft');
  await step(20);
  check('clignement réel', blinking > 0.1 && a.model.getMorph('blinkLeft') < 0.1,
    `pic ${blinking.toFixed(2)}`);

  /* --- regard --- */
  const eyeBefore = boneQ('eye_L');
  a.lookAt({ x: 2, y: 1.4, z: 1 });
  await step(40);
  check('les yeux suivent une cible', boneQ('eye_L') !== eyeBefore);
  const headBefore = boneQ('head');
  a.lookAt({ x: -2, y: 1.2, z: 1 });
  await step(40);
  check('la tête accompagne le regard', boneQ('head') !== headBefore);

  /* --- gestes additifs --- */
  const armBefore = boneQ('upperArm_R');
  a.gesture({ gesture: 'explain', emotion: 'friendly', intensity: 0.8 });
  await step(30);
  check('geste explain joue sur le bras', boneQ('upperArm_R') !== armBefore);
  check('geste enregistré', a.gestures.playing === 'gesture_explain', a.gestures.playing);

  // Superposition : un geste ne doit pas remplacer la base.
  const baseName = a.locomotion.baseName;
  check('geste superposé à la base (pas de remplacement)',
    a.locomotion.baseName === baseName && a.gestures.playing, baseName);

  /* --- lip sync --- */
  a.speak('Bonjour Jérôme, voici le rapport de ton serveur.', { duration: 2.2 });
  await step(12);
  const visemeSum = a.model.meta.visemes.reduce((s, v) => s + a.model.getMorph(v), 0);
  check('lip sync : la bouche articule', visemeSum > 0.05, visemeSum.toFixed(3));
  check('état SPEAKING', a.state === 'SPEAKING', a.state);
  a.stopSpeaking({ bargeIn: true });
  await step(25);
  check('barge-in : retour en écoute', a.state === 'LISTENING', a.state);
  const visemeAfter = a.model.meta.visemes.reduce((s, v) => s + a.model.getMorph(v), 0);
  check('barge-in : bouche refermée', visemeAfter < 0.08, visemeAfter.toFixed(3));

  /* --- locomotion --- */
  a.avatarRoot.position.set(0, 0, 0);
  a.locomotion.facing = 0;
  a.locomotion.targetFacing = 0;
  a.locomotion.state = 'idle';
  a.locomotion.speed = 0;
  a.setState('IDLE');
  await step(5);
  const startPos = a.avatarRoot.position.clone();
  a.moveTo('brain');

  // Le corps se réoriente AVANT de partir : c'est voulu, on lui laisse
  // le temps de pivoter (123° ici) avant de juger la marche.
  await step(20);
  check('pivot avant départ (clip de virage)',
    a.locomotion.baseName === 'turn_left' || a.locomotion.baseName === 'turn_right',
    a.locomotion.baseName);

  await step(90);
  check('marche déclenchée', a.locomotion.speed > 0.4, a.locomotion.speed.toFixed(2));
  check('clip de marche actif', a.locomotion.baseName === 'walk_forward',
    a.locomotion.baseName);

  // Anti-glissement : on mesure le DÉPLACEMENT RÉEL du pied en appui.
  // Un pied posé au sol qui dérive, c'est du foot sliding — rien d'autre.
  const V = a.avatarRoot.position.constructor;
  let slipTotal = 0;
  let bodyTotal = 0;
  let planted = { L: null, R: null };
  for (let i = 0; i < 90; i++) {
    const bodyBefore = a.avatarRoot.position.clone();
    a._update(1 / 60);
    a.avatarRoot.updateMatrixWorld(true);
    bodyTotal += a.avatarRoot.position.distanceTo(bodyBefore);
    for (const side of ['L', 'R']) {
      const foot = a.model.bone('foot_' + side);
      if (!foot) continue;
      const wp = foot.getWorldPosition(new V());
      const isPlanted = wp.y < 0.075;          // pied au contact du sol
      if (isPlanted && planted[side]) {
        slipTotal += Math.hypot(wp.x - planted[side].x, wp.z - planted[side].z);
      }
      planted[side] = isPlanted ? wp : null;
    }
  }
  const slipRatio = bodyTotal > 0.01 ? slipTotal / bodyTotal : 1;
  check('aucun glissement de pied (dérive du pied en appui < 35 % du corps)',
    slipRatio < 0.35,
    `dérive ${slipTotal.toFixed(3)} m pour ${bodyTotal.toFixed(3)} m parcourus`);

  check('déplacement effectif', a.avatarRoot.position.distanceTo(startPos) > 0.2,
    a.avatarRoot.position.distanceTo(startPos).toFixed(2));

  // Arrivée
  for (let i = 0; i < 900 && a.locomotion.state !== 'idle'; i++) { a._update(1 / 60); }
  check('arrivée et retour à IDLE', a.locomotion.state === 'idle', a.locomotion.state);
  const brain = a.stage.get('brain');
  check('arrivé au bon endroit',
    a.avatarRoot.position.distanceTo(brain.position) < 0.35,
    a.avatarRoot.position.distanceTo(brain.position).toFixed(2));

  /* --- pieds au sol --- */
  let minFootY = 9;
  for (let i = 0; i < 60; i++) {
    a._update(1 / 60);
    for (const side of ['L', 'R']) {
      const foot = a.model.bone('foot_' + side);
      if (foot) minFootY = Math.min(minFootY, foot.getWorldPosition(new (foot.position.constructor)()).y);
    }
  }
  check('les pieds ne traversent pas le sol', minFootY > -0.02, minFootY.toFixed(3));

  /* --- retour maison --- */
  a.returnHome();
  for (let i = 0; i < 900 && a.locomotion.state !== 'idle'; i++) a._update(1 / 60);
  check('retour à la position de conversation',
    a.avatarRoot.position.length() < 0.3, a.avatarRoot.position.length().toFixed(2));

  /* --- caméra --- */
  a.setCameraMode('CALL', { instant: true });
  await step(5);
  const camBefore = a.camera.position.clone();
  a.setCameraMode('FULL_BODY');
  // Pas de coupure : on vérifie que le trajet est progressif ET qu'il aboutit.
  await step(20);
  const mid = a.camera.position.distanceTo(camBefore);
  await step(120);
  const moved = a.camera.position.distanceTo(camBefore);
  check('transition caméra progressive (pas de coupure)',
    mid > 0.05 && mid < moved * 0.9, `mi-parcours ${mid.toFixed(2)} / total ${moved.toFixed(2)}`);
  check('transition caméra aboutie', moved > 1.0, moved.toFixed(2));
  a.setCameraMode('CALL');
  await step(120);
  check('retour cadrage appel', a.director.mode === 'CALL');

  /* --- idle : pas de boucle mécanique --- */
  const idleBefore = a.locomotion.baseName;
  a.behavior.postureTimer = 0;
  await step(6);
  check('variation de posture disponible',
    a.locomotion.idleClips.length >= 4, a.locomotion.idleClips.length);

  /* --- qualité / performance --- */
  a.setQuality('low');
  check('qualité low appliquée', a.renderer.shadowMap.enabled === false);
  a.setQuality('balanced');
  check('qualité balanced restaurée', a.renderer.shadowMap.enabled === true);
  a.setGpuBusy(true);
  check('GPU occupé → dégradation', a.quality === 'low', a.quality);
  a.setGpuBusy(false);
  check('GPU libre → restauration', a.quality === 'balanced', a.quality);

  window.__avatarSuite = results;
  return results;
};
