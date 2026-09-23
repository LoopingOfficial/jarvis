# Migration §15 — `users.last_login_at` + unification des sessions

Date : 2026-09-23 · Projet : brainrot-fortnite.com · État : PLAN (non déployé)

## Constat (mesuré en base, read-only)

| Élément | Valeur mesurée |
|---|---|
| `users` | 317 lignes ; **aucune colonne `last_login_at`** (seuls `created_at`, `email_verified_at`) |
| Sessions web | PHP natives (`session_start()` dans `config/config.php`), cookie `br_remember` (TTL 30 j, `config.php`), **aucune table de session** |
| Sessions mobile | table `mobile_auth_tokens` : 16 jetons, **0 actif** (tous expirés/révoqués), seuls 3 users distincts ont eu un jeton, MAX `last_used_at` = 2026-07-17 |
| Présence | `activity_presence` : 54 users distincts, `last_seen_at` touchée par `activity_presence_touch()` (newsfeed-status / `site_auto_touch_activity_presence`) |
| Événements | `activity_events` (audit, `event_type`, `aggregate_key`), `admin_member_events` (actions admin) |

Conséquence opérationnelle : « last login » n'existe pas → la rétention réelle (≥3 jours libres
d'affluence) ne peut pas être mesurée ; l'analytique `brainrot.analytics.*` a dû se passer de ce
signal (activité infime mesurée par d'autres biais).

## Choix d'architecture

1. **Colonne additive unique, nullable** : `users.last_login_at DATETIME NULL` (jamais de rewrite,
   supporte le dry-run, rollback simple). Pas de table de sessions centralisée : le site vit déjà
   en sessions PHP natives + `mobile_auth_tokens` ; unifier forcerait une migration de l'ensemble
   de l'auth pour un bénéfice nul. On ajoute juste le point de référence agrégé.
2. **Mise à jour au login** : hook dans `set_user_session()` (`ajax/login.php:89`) — un point
   unique couvre login mot de passe, login epic, remember-me, OAuth discord. `UPDATE users SET
   last_login_at = NOW() WHERE id = ?` en une requête, jamais bloquante (le login ne dépend pas
   de son succès).
3. **Mise à jour au jeton mobile** : étendre `mobile_jwt.php` « validate » (l.76 met déjà à jour
   `mobile_auth_tokens.last_used_at`) pour refléter la même valeur sur `users.last_login_at` la
   première fois de la journée (éviter 1 UPDATE par requête API : seulement si
   `last_login_at < DATE(NOW())`).
4. **Backfill séparé, hors migration** (règle du repo : une migration ne réécrit jamais de
   contenu utilisateur — cf. migration 074) : script one-shot qui prend
   `MAX(mobile_auth_tokens.last_used_at)` puis `MAX(activity_presence.last_seen_at)` par user.
   Honnêteté des données : ~3 users auront une vraie date mobile, ~54 une date de présence,
   les ~260 restants resteront NULL (cobaye légitime : ces users n'ont simplement jamais laissé
   de trace).

## Mécanique de la migration (conforme à `sql/migrations/`)

- Fichier : `sql/migrations/2026-09-XX_075_users_last_login_at.php`, classe
  `Migration_2026_09_XX_075_UsersLastLoginAt extends AbstractMigration`.
- `supportsTransaction(): true`, `supportsRollback(): true`, `supportsDryRun(): true`.
- Preflight : version serveur + table `users` présente + colonne absente (`columnExists`).
- `up()` : `ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL AFTER email_verified_at`
  (DDL exécuté sous `execGuarded`/`MigrationSqlGuard`), puis `KEY idx_users_last_login`
  si pertinent pour les tris admin rétention.
- `down()` : `DROP COLUMN last_login_at` (annule tout, safe : nullable, données perdues = une
  date de login).
- Backfill : `sql/backfill/075_last_login_from_mobile_presence.php` (script séparé,
  dry-run + `--apply`).

## Téléchargements/deploy

- Patch local via `brainrot.webdev.propose_patch` (sandbox, lint php), puis plan via
  `brainrot.webdev.deploy_plan` — upload seulement après backup `deployment-backups/` et
  confirmation humaine. Console : jamais directement.

## Critères de validation

1. Migration en dry-run côté local : dependencies satisfaites, DDL vérifié, non exécuté.
2. Apply réel : colonne créée, 317 users = NULL (avant backfill), migration `applied`.
3. Backfill appliqué : ~3 users datés depuis mobile, ~54 depuis présence, 0 perte.
4. Login test (sable local) : `set_user_session` écrit `last_login_at = NOW()`.
5. Vérif analytics : `brainrot.analytics.members` peut afficher la vraie date de dernière
   activité (fini « pas de last_login »).