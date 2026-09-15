"""Mini-CRM autonome : FastAPI + MySQL (cPanel).

Volontairement séparé de `jarvis/crm.py`, qui est un carnet local SQLite
embarqué dans l'assistant. Ici la base est distante et partagée : le réseau
peut tomber, la connexion peut être coupée par le serveur, et plusieurs
clients écrivent en même temps. Ces contraintes dictent les choix du module
(pool_pre_ping, transactions courtes, clé d'API obligatoire).
"""
__version__ = "1.0.0"
