# CRM API

Mini-CRM autonome (FastAPI + MySQL), distinct de `jarvis/crm.py` qui reste le
carnet local SQLite de l'assistant. Ici la base est distante et partagée.

## Démarrage

```bash
pip install -r crm_api/requirements.txt
cp crm_api/.env.example .env   # puis renseigner DB_* et CRM_API_KEY
uvicorn crm_api.main:app --reload
```

Les tables manquantes sont créées au démarrage ; aucune table existante n'est
modifiée. Documentation interactive sur `/docs`.

## Authentification

Toutes les routes métier exigent l'en-tête `X-API-Key`. Seul `/health` est
public — il sert de sonde à l'hébergeur et ne renvoie aucune donnée client.

```bash
curl -H "X-API-Key: $CRM_API_KEY" http://localhost:8000/contacts?q=martin
```

## Routes

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | État du service **et** de la base (`degraded` si MySQL injoignable) |
| GET | `/contacts` | Recherche paginée (`q`, `status`, `limit`, `offset`) ; renvoie le total |
| POST | `/contacts` | Création ; **409** si l'e-mail existe déjà |
| GET | `/contacts/{id}` | Fiche + fil des interactions |
| PATCH | `/contacts/{id}` | Mise à jour partielle (champ absent = inchangé) |
| DELETE | `/contacts/{id}` | Suppression, interactions comprises |
| POST | `/contacts/{id}/interactions` | Ajout au fil (`occurred_at` optionnel, daté côté serveur) |
| GET | `/contacts/{id}/interactions` | Fil d'un contact |
| GET | `/interactions` | Fil transverse, filtrable par `kind` |

## Tests

```bash
pytest tests/test_crm_api.py
```

Ils tournent sur SQLite en mémoire : ils valident le contrat HTTP, pas le
dialecte MySQL.
