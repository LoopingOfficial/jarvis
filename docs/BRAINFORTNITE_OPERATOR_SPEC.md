Lis intégralement :

docs/BRAINFORTNITE_OPERATOR_SPEC.md

Ce document décrit l'objectif FINAL du BrainrotFortniteAgent.
Ne tente PAS d'implémenter les 37 phases en une seule fois.

Tu vas maintenant réaliser le LOT 1 concret.

OBJECTIF DU LOT 1
Construire les fondations réelles du BrainrotFortniteAgent à partir du code existant de JARVIS.

Travail demandé :

1. Inspecte d'abord réellement le projet JARVIS.
2. Identifie l'architecture actuelle :
   - router
   - agents
   - tools
   - mémoire
   - providers LLM
   - stockage
   - API
   - interface
   - système d'événements
3. Cherche tout code existant lié à :
   - brainrot-fortnite.com
   - BrainrotFortniteAgent
   - site knowledge
   - actions/tools
   - BlogPublisher
4. Réutilise l'existant. Ne crée pas un deuxième système concurrent.

Ensuite IMPLÉMENTE réellement :

A. BrainrotFortniteAgent
B. SiteKnowledgeIndex
C. BrainrotFortniteActionRegistry
D. structures de données nécessaires
E. persistance locale appropriée
F. intégration au Router JARVIS
G. tests unitaires

Pour ce premier lot :

- READ uniquement
- aucune action production
- aucun SQL arbitraire
- aucun credential
- aucun ban
- aucune publication
- aucune suppression

Le SiteKnowledgeIndex doit déjà permettre de représenter :

- module
- feature
- route
- fichiers source
- tables
- permissions
- actions possibles
- règles métier
- sources
- last_verified
- hash/version

L'ActionRegistry doit déjà supporter :

- id
- module
- description
- required_inputs
- risk_level
- permission_level
- read_or_write
- handler
- validator
- verification
- rollback_possible
- audit_event

IMPORTANT :

Je ne veux pas uniquement un plan.

Après l'inspection, COMMENCE À MODIFIER LE CODE.

Travaille directement dans le projet.

Après chaque modification importante :
- vérifie les imports
- lance les tests adaptés
- corrige les erreurs
- continue

Ne me demande pas confirmation pour les modifications locales du code.

Tu t'arrêtes uniquement si :
1. le LOT 1 est fonctionnel et testé,
ou
2. un blocage externe réel empêche de continuer.

À la fin donne seulement :

- fichiers créés
- fichiers modifiés
- architecture ajoutée
- tests exécutés
- tests PASS/FAIL
- blocages éventuels
- prochaine étape logique

BUILD :
JARVIS_BRAINFORTNITE_OPERATOR_FOUNDATION_V1

Commence maintenant par inspecter le code réel, puis implémente.