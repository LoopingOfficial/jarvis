# Anti-loop local model

- Après le succès d'un outil, considère son résultat comme acquis.
- Ne répète jamais le même outil avec exactement les mêmes paramètres.
- Après un read_file/read_files réussi, analyse immédiatement le résultat.
- Ne relis pas le même fichier sauf si le fichier a changé ou si l'utilisateur le demande explicitement.
- Si une action échoue, change de stratégie au lieu de répéter exactement le même appel.
- Ne transforme jamais un outil Cline en pseudo-code Bash, JSON ou texte.
- Quand le contenu nécessaire est déjà disponible, réponds directement sans nouvel appel d'outil.

- Pour toute affirmation sur une classe, fonction, méthode ou import Python :
  vérifie d'abord factuellement avec AST, grep ou le contenu exact du fichier.
- Ne confonds jamais un symbole importé avec un symbole défini localement.
- Ne déduis jamais l'existence d'une fonction à partir de son nom attendu.
- Si un résultat d'outil contredit une réponse précédente, le résultat d'outil prévaut.
- Ne considère jamais une modification comme réussie sans vérifier le fichier après écriture.
