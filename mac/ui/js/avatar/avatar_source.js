/* ==========================================================================
   Choix du modele d'avatar + versionnement des assets.

   Un seul endroit decide QUEL GLB est charge. Trois sources, par priorite
   decroissante :

     1. `?avatar=...` dans l'URL      — essai ponctuel, ne laisse aucune trace
     2. `localStorage`                — interrupteur Developer, persistant
     3. le profil par defaut          — ce qui part en production

   Cache
   -----
   Chaque entree porte un `version`. L'URL finale reçoit `?v=<version>`, donc
   publier un nouveau GLB sous le meme nom de fichier suffit a invalider le
   cache du navigateur. Les tests precedents avaient du changer de PORT pour
   contourner un module servi depuis le cache : ce contournement n'a pas sa
   place en production, et c'est ce que ce versionnement remplace.
   ========================================================================== */

export const AVATAR_BUILDS = {
  legacy: {
    url: '/assets/avatar/jarvis_premium.glb',
    version: 'v2-premium',
    label: 'Legacy (jarvis_premium)',
  },
  v23: {
    url: '/assets/avatar/jarvis_v2_3_high.glb',
    version: 'v2.3-high',
    label: 'V2.3 HIGH',
  },
  'v23-balanced': {
    url: '/assets/avatar/jarvis_v2_3_balanced.glb',
    version: 'v2.3-balanced',
    label: 'V2.3 BALANCED',
  },
};

/** Profil servi quand rien n'est demande. */
export const DEFAULT_BUILD = 'v23';

const STORAGE_KEY = 'jarvis.avatar.build';

function fromQuery() {
  try {
    const value = new URLSearchParams(location.search).get('avatar');
    return value && AVATAR_BUILDS[value] ? value : null;
  } catch {
    return null;
  }
}

function fromStorage() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    // Les anciennes installations avaient mémorisé `legacy`. Ce choix ne doit
    // plus empêcher le nouveau modèle de devenir l'avatar principal.
    if (value === 'legacy') {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return value && AVATAR_BUILDS[value] ? value : null;
  } catch {
    return null;              // navigation privee, stockage bloque
  }
}

/** Nom du profil actif, sans effet de bord. */
export function currentBuildName() {
  return fromQuery() || fromStorage() || DEFAULT_BUILD;
}

/** URL versionnee du profil actif. */
export function resolveAvatarUrl(name = null) {
  const key = name && AVATAR_BUILDS[name] ? name : currentBuildName();
  const build = AVATAR_BUILDS[key];
  return `${build.url}?v=${encodeURIComponent(build.version)}`;
}

/** URL de repli : jamais le meme fichier que celui qui vient d'echouer. */
export function fallbackAvatarUrl(name = null) {
  const key = name && AVATAR_BUILDS[name] ? name : currentBuildName();
  const target = key === DEFAULT_BUILD ? 'v23' : DEFAULT_BUILD;
  return resolveAvatarUrl(target);
}

/**
 * Interrupteur Developer. `null` efface le choix et revient au defaut.
 * Un rechargement est necessaire : le GLB est charge une seule fois au boot.
 */
export function setAvatarBuild(name) {
  try {
    if (name === null) localStorage.removeItem(STORAGE_KEY);
    else if (AVATAR_BUILDS[name]) localStorage.setItem(STORAGE_KEY, name);
    else return false;
  } catch {
    return false;
  }
  return true;
}

export function avatarBuildInfo() {
  const name = currentBuildName();
  return {
    name,
    ...AVATAR_BUILDS[name],
    resolved: resolveAvatarUrl(name),
    fallback: fallbackAvatarUrl(name),
    source: fromQuery() ? 'url' : (fromStorage() ? 'localStorage' : 'defaut'),
    available: Object.keys(AVATAR_BUILDS),
  };
}

// Interrupteur Developer minimal, depuis la console :
//   JarvisAvatarBuild.set('v23'); location.reload();
//   JarvisAvatarBuild.set(null);  location.reload();   // retour Legacy
window.JarvisAvatarBuild = {
  set: setAvatarBuild,
  info: avatarBuildInfo,
  builds: AVATAR_BUILDS,
};
