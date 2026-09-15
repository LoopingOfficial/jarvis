"""Routes HTTP. Un module par ressource pour que le préfixe et la
dépendance de sécurité soient déclarés une seule fois, au montage."""
from . import contacts, interactions  # noqa: F401
