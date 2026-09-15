"""Générateur TOTP (RFC 6238) — codes 2FA à la volée pour les connexions autonomes.

Implémenté sur la bibliothèque standard (`hmac`, `hashlib`, `base64`) : aucune
dépendance nouvelle, donc rien à installer pour que l'authentification à deux
facteurs fonctionne hors-ligne.

Le secret TOTP est un secret comme un autre : il est stocké chiffré par
`SecretVault` et n'est déchiffré qu'au moment de générer le code. Ce module ne
lit jamais la base — il reçoit le secret en clair de l'appelant et le rend
aussitôt inutile en ne le conservant nulle part.

Sécurité : `verify()` compare en temps constant (`hmac.compare_digest`) et
accepte une fenêtre de dérive (`window`), parce qu'une horloge locale décalée
de quelques secondes ne doit pas faire échouer une connexion.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import urllib.parse

DIGITS = 6
PERIOD = 30
ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class TotpError(ValueError):
    """Secret TOTP inexploitable (base32 invalide, algorithme inconnu)."""


def normalize_secret(secret: str) -> str:
    """Nettoie un secret recopié depuis un site : espaces, tirets, minuscules.

    Les fournisseurs affichent souvent « jbsw y3dp ehpk 3pxp » : recopié tel
    quel, le base32 échouerait. On retire aussi le padding pour le recalculer.
    """
    cleaned = "".join(str(secret or "").split()).replace("-", "").upper()
    return cleaned.rstrip("=")


def _decode_secret(secret: str) -> bytes:
    cleaned = normalize_secret(secret)
    if not cleaned:
        raise TotpError("Secret TOTP vide.")
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding, casefold=True)
    except Exception as exc:  # binascii.Error
        raise TotpError("Secret TOTP invalide : ce n'est pas du base32.") from exc


def hotp(secret: str, counter: int, *, digits: int = DIGITS, algorithm: str = "SHA1") -> str:
    """HOTP (RFC 4226) — brique de base du TOTP."""
    digest_mod = ALGORITHMS.get(str(algorithm or "SHA1").upper())
    if digest_mod is None:
        raise TotpError(f"Algorithme TOTP non supporté : {algorithm}")
    mac = hmac.new(_decode_secret(secret), struct.pack(">Q", int(counter)), digest_mod).digest()
    offset = mac[-1] & 0x0F                       # troncature dynamique RFC 4226
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def generate(secret: str, *, at: float | None = None, digits: int = DIGITS,
             period: int = PERIOD, algorithm: str = "SHA1") -> str:
    """Code à 6 chiffres valable pour la fenêtre courante."""
    now = time.time() if at is None else at
    return hotp(secret, int(now // max(1, period)), digits=digits, algorithm=algorithm)


def seconds_remaining(*, at: float | None = None, period: int = PERIOD) -> int:
    """Secondes avant expiration du code courant.

    Utile à l'agent : sous ~3 s, mieux vaut attendre le code suivant plutôt que
    de le saisir et de se le faire refuser au milieu du formulaire.
    """
    now = time.time() if at is None else at
    return int(max(1, period) - (now % max(1, period)))


def generate_safe(secret: str, *, min_validity: int = 3, **kwargs) -> tuple[str, int]:
    """Renvoie (code, secondes_restantes) en évitant les codes expirants.

    Si le code courant expire dans moins de `min_validity` secondes, on attend
    la fenêtre suivante : un code saisi puis refusé déclenche souvent un
    verrouillage temporaire côté fournisseur.
    """
    period = int(kwargs.get("period", PERIOD))
    remaining = seconds_remaining(period=period)
    if remaining < max(0, min_validity):
        time.sleep(remaining)
        remaining = period
    return generate(secret, **kwargs), remaining


def verify(secret: str, code: str, *, at: float | None = None, window: int = 1,
           digits: int = DIGITS, period: int = PERIOD, algorithm: str = "SHA1") -> bool:
    """Vérifie un code en tolérant `window` fenêtres de dérive d'horloge."""
    candidate = "".join(str(code or "").split())
    if not candidate.isdigit():
        return False
    now = time.time() if at is None else at
    counter = int(now // max(1, period))
    for drift in range(-abs(window), abs(window) + 1):
        expected = hotp(secret, counter + drift, digits=digits, algorithm=algorithm)
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def parse_uri(uri: str) -> dict[str, object]:
    """Lit une URI `otpauth://totp/...` (contenu d'un QR code).

    Permet de coller directement ce que le site propose en « clé manuelle » ou
    ce qu'un lecteur de QR code a produit, plutôt que d'extraire le secret à la
    main et de se tromper de paramètres (certains fournisseurs utilisent 8
    chiffres ou SHA256).
    """
    parsed = urllib.parse.urlparse(str(uri or "").strip())
    if parsed.scheme != "otpauth" or parsed.netloc.lower() != "totp":
        raise TotpError("URI otpauth invalide (attendu : otpauth://totp/...).")
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    secret = params.get("secret", "")
    if not secret:
        raise TotpError("URI otpauth sans paramètre `secret`.")
    _decode_secret(secret)                        # échoue tout de suite si invalide
    label = urllib.parse.unquote(parsed.path.lstrip("/"))
    issuer = params.get("issuer", "")
    if not issuer and ":" in label:
        issuer = label.split(":", 1)[0]
    return {
        "secret": normalize_secret(secret),
        "label": label,
        "issuer": issuer,
        "digits": int(params.get("digits", DIGITS)),
        "period": int(params.get("period", PERIOD)),
        "algorithm": str(params.get("algorithm", "SHA1")).upper(),
    }
