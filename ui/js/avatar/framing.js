/* ==========================================================================
   Cadrages JARVIS — HOME / CHAT / VOICE.

   Ces trois vues sont ENREGISTREES dans la table `CAMERA_MODES` existante
   plutot que codees ailleurs : `CameraDirector` continue donc de gerer seul
   les transitions, et rien n'est duplique.

   Choix de focale
   ---------------
   Toutes les vues restent entre 26 et 30 degres de champ vertical, soit
   l'equivalent d'un 85–100 mm en 24x36. En dessous de ~35 degres le nez
   cesse de grossir et les oreilles de reculer : c'est la plage ou un visage
   se lit comme une photo de portrait et non comme une webcam.

   `offset` et `look` sont exprimes par rapport au pied de l'avatar (metres),
   pour un personnage de 1,78 m dont les yeux sont a 1,68 m.
   ========================================================================== */
import * as THREE from '../../vendor/three.module.js';
import { CAMERA_MODES } from './locomotion.js';

/** Marges minimales, en fraction de la hauteur de l'image. */
export const SAFE_FRAME = {
  // Au-dessus des cheveux, sous le menton, autour des epaules : en deca de ces
  // marges un redimensionnement de fenetre rogne la tete.
  top: 0.08,
  bottom: 0.06,
  sides: 0.07,
};

export const JARVIS_FRAMINGS = {
  // Cohabite avec le Brain : l'avatar occupe le tiers gauche, pas l'ecran.
  HOME: {
    offset: new THREE.Vector3(0.10, 1.40, 2.25),
    look: new THREE.Vector3(0, 1.26, 0),
    fov: 30,
    note: 'buste moyen, epaules completes, respiration autour du sujet',
  },
  // Conversation : plus present, mais la moitie droite reste au texte.
  CHAT: {
    offset: new THREE.Vector3(0.06, 1.46, 1.72),
    look: new THREE.Vector3(0, 1.38, 0),
    fov: 29,
    note: 'buste rapproche, clavicules visibles',
  },
  // Voix : le lipsync doit etre lisible, donc le visage domine le cadre.
  VOICE: {
    offset: new THREE.Vector3(0.04, 1.56, 1.26),
    look: new THREE.Vector3(0, 1.52, 0),
    fov: 26,
    note: 'portrait, bouche lisible, epaules encore dans le champ',
  },
};

/** Enregistre les cadrages dans la table que `CameraDirector` consulte. */
export function registerFramings() {
  for (const [name, spec] of Object.entries(JARVIS_FRAMINGS)) {
    CAMERA_MODES[name] = {
      offset: spec.offset.clone(),
      look: spec.look.clone(),
      fov: spec.fov,
    };
  }
  return Object.keys(JARVIS_FRAMINGS);
}

/**
 * Verifie qu'un cadrage respecte la zone sure pour un ratio donne.
 * Renvoie les marges reelles (fraction de l'image) au sommet du crane et
 * sous les epaules : negatif = element coupe.
 */
export function checkSafeFrame(spec, aspect, { headTop = 1.78,
  shoulders = 1.42 } = {}) {
  const fovRad = (spec.fov * Math.PI) / 180;
  const dist = spec.offset.z;
  const halfHeight = Math.tan(fovRad / 2) * dist;
  const centre = spec.look.y;
  const topEdge = centre + halfHeight;
  const bottomEdge = centre - halfHeight;
  return {
    aspect,
    halfHeight: +halfHeight.toFixed(4),
    marginTop: +((topEdge - headTop) / (2 * halfHeight)).toFixed(4),
    marginBottom: +((shoulders - bottomEdge) / (2 * halfHeight)).toFixed(4),
    okTop: topEdge - headTop > 0,
    okBottom: bottomEdge < shoulders,
  };
}

export default JARVIS_FRAMINGS;
