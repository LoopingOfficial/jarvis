"""Generate the requested review artifacts from the saved pre-change sources."""
from pathlib import Path
import difflib
import json
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from jarvis.security_audit import AUDIT_SYSTEM

added = ["jarvis/execution_policy.py", "jarvis/security_audit.py",
         "tests/test_security_audit_frontend.js"]
existing = [p.relative_to(OUT / "before").as_posix()
            for p in (OUT / "before").rglob("*") if p.is_file()]
parts, changes = [], []
for name in sorted(set(existing + added)):
    before = OUT / "before" / name
    after = ROOT / name
    old = before.read_text(encoding="utf-8") if before.exists() else ""
    new = after.read_text(encoding="utf-8")
    if old == new:
        continue
    parts.append("diff --git a/" + name + " b/" + name + "\n")
    parts.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                 fromfile="a/" + name if before.exists() else "/dev/null",
                 tofile="b/" + name))
    changes.append({"file": name, "change": "modified" if before.exists() else "added",
                    "sha256": hashlib.sha256(after.read_bytes()).hexdigest()})
(OUT / "security-audit-readonly.patch").write_text("".join(parts), encoding="utf-8", newline="\n")
(OUT / "manifest.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
links = "\n".join(f"- [{c['file']}]({(ROOT / c['file']).as_posix()}) — {c['change']}" for c in changes)
readme = """# Correction security_audit_readonly

Les changements sont appliqués aux sources locales. Le patch joint contient tous les
hunks complets par rapport aux sources présentes au début de cette correction, y compris
les nouveaux fichiers. Ne pas le réappliquer au workspace déjà corrigé.

## Cause racine

Le détecteur has_write_intent reconnaissait « modifie » dans « ne modifie rien ».
CodeEditRouter était consulté avant le mode d'audit. Avec un document Coding ouvert,
la réponse du modèle était donc envoyée au générateur de remplacement, puis au contrôle
anti-troncature. Les anciens tests n'ouvraient pas de document Coding et rataient ce cas.

La correction précédente retournait aussi les findings seulement dans un objet API,
pas dans le texte de conversation. Une sortie invalide ou un modèle indisponible pouvait
être présenté comme zéro faille. La vérification finale pouvait afficher un hash identique
même si la relecture avait échoué.

## Fichiers exacts modifiés ou ajoutés

""" + links + """

## Patch complet

[security-audit-readonly.patch](./security-audit-readonly.patch)

[Manifest des fichiers et SHA256](./manifest.json)

## Comportement

- « affiche moi marketplace.php » : ouverture dans Coding, sans appel au LLM.
- « analyse marketplace.php ... Ne modifie rien ! » : priorité absolue à security_audit_readonly,
  avant les routeurs d'ouverture, d'édition et les réécritures de contexte.
- Le buffer Coding ouvert est analysé tel quel et verrouillé. Sinon une lecture exacte SSH
  utilise la racine du connecteur choisi. Aucun repli local ou racine distante inventée.
- La politique est liée à la requête et enregistrée dans les métadonnées du job. Un autre
  message ou un paramètre du modèle ne peut pas la transformer en autorisation d'écriture.
- Le runner refuse les outils hors liste de lecture avant les secrets et confirmations,
  y compris ssh.run/terminal.run, sauvegarde, délégation et déploiement. Le refus est journalisé
  WRITE_DENIED_READ_ONLY. Une confirmation ne contourne pas un job d'audit.
- L'éditeur reçoit seulement le contenu source et des événements de verrouillage.
  Le rapport détaillé arrive dans le texte de conversation avec la mention
  « Aucune modification n’a été appliquée ».
- Les chunks utilisent 6000 caractères par défaut et 600 caractères de recouvrement.
  Le réglage ai.audit_chunk_chars permet 1000 à 12000 caractères. Chaque appel reçoit un seul
  fragment et réserve une sortie de 1800 tokens. Adapter le budget aux limites du modèle choisi.
- L'agrégation finale est déterministe : conservation de tous les findings validés,
  déduplication, sévérité globale et résumé exécutif. Aucun appel de fusion n'envoie un
  rapport de taille non bornée au modèle. Une sortie invalide est retentée une fois puis
  signalée comme audit incomplet, jamais comme un résultat sûr.
- Pour un buffer ouvert, les hashes comparent le snapshot Coding avant/après (source_origin=coding_buffer).
  Pour une lecture distante, une seconde lecture indépendante vérifie la source (source_origin=storage).
  Les hashes portent sur le contenu UTF-8 exact ; une relecture échouée laisse le hash final inconnu.
- Une nouvelle demande explicite de correction peut déverrouiller un document après l'audit.
  Elle crée un nouveau job file_edit, transmet le rapport précédent de cette conversation au
  générateur et exige une confirmation. Avant l'écriture confirmée, le hash est recontrôlé.
  Le garde-fou anti-troncature reste actif pour les vrais remplacements.

## Tests à exécuter (PowerShell, à la racine du workspace)

```powershell
.venv\\Scripts\\python.exe -m unittest tests.test_security_analysis tests.test_pipeline tests.test_remote_paths tests.test_system
node tests/test_security_audit_frontend.js
node --check ui/js/code_env.js
```

Résultat de cette correction : 111 tests Python réussis et test JavaScript réussi.
Le runner réel est testé avec les transports SSH et le LLM simulés. Le cas avec document
déjà ouvert, la requête exacte, les tentatives d'écriture, les erreurs de modèle, la relecture
échouée, l'isolation des requêtes, les hashes, les chunks, la confirmation et l'anti-troncature
sont couverts. Le test volumineux contient exactement 153522 caractères synthétiques.
Le fichier distant marketplace.php réel et un modèle en production n'ont pas été audités.
Le test JavaScript vérifie les événements de politique, le refus de sauvegarde, la progression
et le déverrouillage sans mutation du contenu, dans une VM hors ligne.

## Règles système mises à jour

La constante AUDIT_SYSTEM de jarvis/security_audit.py est le prompt réellement utilisé :

```text
""" + AUDIT_SYSTEM + """
```

La sortie est validée comme rapport JSON puis rendue en texte dans la conversation.
La politique du runner ne dépend pas du respect du prompt.

## Mise en service

Relancer le processus JARVIS pour charger les nouveaux modules Python et recharger son
interface pour charger le JavaScript. Aucun redémarrage ou déploiement de l'instance en
cours n'a été effectué pendant cette correction.
"""
(OUT / "README.md").write_text(readme, encoding="utf-8", newline="\n")
print(f"Patch complet : {len(changes)} fichiers, {sum(len(p) for p in parts)} caractères")
