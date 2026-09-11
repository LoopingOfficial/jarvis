"""Client SSH : paramiko si présent, sinon binaire `ssh` (clé uniquement).

Les secrets arrivent ici sous forme de dict déjà déchiffré par le Secure Tool
Runner. Ils ne sont jamais journalisés ni renvoyés dans les sorties.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:  # pragma: no cover
    import paramiko

    HAVE_PARAMIKO = True
except Exception:  # pragma: no cover
    paramiko = None  # type: ignore
    HAVE_PARAMIKO = False


def _expand(path: str) -> str:
    return str(Path(path).expanduser()) if path else ""


def _debug(message: str) -> None:
    if os.getenv("JARVIS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}:
        print(f"[ssh_client] {message}", flush=True)


def ssh_exec(
    config: dict[str, Any], secrets: dict[str, str], command: str, timeout: int = 90
) -> tuple[bool, str]:
    host = str(config.get("host") or "").strip()
    user = str(config.get("username") or config.get("user") or "").strip()
    port = int(config.get("port") or 22)
    if not host or not user:
        return False, "Configuration SSH incomplète (hôte / utilisateur)."
    workdir = str(config.get("working_directory") or "").strip()
    if workdir:
        command = f"cd {shell_quote(workdir)} && ({command})"

    password = secrets.get("password") or ""
    private_key = secrets.get("private_key") or ""
    passphrase = secrets.get("passphrase") or ""
    key_path = _expand(str(config.get("key_path") or ""))

    _debug(f"SSH CONNECT START host={host} port={port} user={user} (method="
           f"{'paramiko' if HAVE_PARAMIKO else 'binaire'})")
    if HAVE_PARAMIKO:
        return _paramiko_exec(host, port, user, password, private_key, passphrase, key_path, command, timeout)
    return _binary_exec(host, port, user, key_path, command, timeout, bool(password))


def _load_pkey(private_key: str, key_path: str, passphrase: str):
    loaders = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)
    if private_key.strip():
        for loader in loaders:
            try:
                return loader.from_private_key(io.StringIO(private_key), password=passphrase or None)
            except Exception:
                continue
    if key_path and Path(key_path).is_file():
        for loader in loaders:
            try:
                return loader.from_private_key_file(key_path, password=passphrase or None)
            except Exception:
                continue
    return None


def _paramiko_exec(host, port, user, password, private_key, passphrase, key_path, command, timeout):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pkey = _load_pkey(private_key, key_path, passphrase)
        client.connect(
            hostname=host, port=port, username=user,
            password=password or None, pkey=pkey,
            key_filename=key_path if (not pkey and key_path and Path(key_path).is_file()) else None,
            timeout=15, auth_timeout=20, banner_timeout=20,
            look_for_keys=not (pkey or password), allow_agent=not password,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        text = (out + ("\n" + err if err.strip() else "")).strip()
        _debug(f"SSH CONNECTED · SSH COMMAND START\nSSH COMMAND COMPLETED exit_code={code}")
        return code == 0, text or ("Commande exécutée sans sortie." if code == 0 else f"Code de sortie {code}.")
    except Exception as exc:
        _debug(f"SSH CONNECT FAILED ({exc.__class__.__name__})")
        return False, f"SSH: {exc}"
    finally:
        try:
            client.close()
        except Exception:
            pass


def _binary_exec(host, port, user, key_path, command, timeout, had_password):
    if had_password and not shutil.which("sshpass"):
        return False, (
            "Authentification par mot de passe indisponible : installez paramiko "
            "(pip install paramiko) ou utilisez une clé SSH."
        )
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
            "-o", "StrictHostKeyChecking=accept-new", "-p", str(port)]
    if key_path:
        argv += ["-i", key_path]
    argv += [f"{user}@{host}", command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        text = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return proc.returncode == 0, text or "Commande exécutée sans sortie."
    except subprocess.TimeoutExpired:
        return False, "Délai SSH dépassé."
    except FileNotFoundError:
        return False, "Le client ssh est introuvable sur cette machine."
    except Exception as exc:
        return False, f"SSH: {exc}"


def ssh_deploy(
    config: dict[str, Any], secrets: dict[str, str], local_path: str, remote_path: str = "", timeout: int = 1800
) -> tuple[bool, str]:
    host = str(config.get("host") or "")
    user = str(config.get("username") or "")
    port = int(config.get("port") or 22)
    remote = (remote_path or str(config.get("remote_path") or "")).strip()
    if not remote:
        return False, "Aucun chemin distant défini pour ce serveur."
    src = Path(local_path).expanduser().resolve()
    if not src.is_dir():
        return False, f"Dossier local introuvable: {src}"
    key_path = _expand(str(config.get("key_path") or ""))
    password = secrets.get("password") or ""
    private_key = secrets.get("private_key") or ""

    tmp_key = None
    if private_key.strip() and not key_path:
        fd, tmp_key = tempfile.mkstemp(prefix="jarvis-key-")
        with os.fdopen(fd, "w") as fh:
            fh.write(private_key if private_key.endswith("\n") else private_key + "\n")
        os.chmod(tmp_key, 0o600)
        key_path = tmp_key

    try:
        if password and not key_path and HAVE_PARAMIKO:
            return _sftp_upload(host, port, user, password, private_key, secrets.get("passphrase", ""),
                                key_path, src, remote)
        rsync = shutil.which("rsync")
        ssh_parts = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
                     "-o", "StrictHostKeyChecking=accept-new", "-p", str(port)]
        if key_path:
            ssh_parts += ["-i", key_path]
        dest = f"{user}@{host}:{remote.rstrip('/')}/"
        if rsync:
            argv = [rsync, "-az", "--delete-after", "--exclude", ".git", "--exclude", ".venv",
                    "--exclude", "node_modules", "--exclude", "__pycache__",
                    "-e", " ".join(ssh_parts), str(src) + "/", dest]
        else:
            argv = ["scp", "-r", "-P", str(port), "-o", "BatchMode=yes"]
            if key_path:
                argv += ["-i", key_path]
            argv += [str(src) + "/", dest]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        text = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        if proc.returncode != 0:
            return False, f"Déploiement échoué.\n{text[:2000]}"
        return True, f"Fichiers synchronisés vers {user}@{host}:{remote}\n{text[-2000:]}"
    except subprocess.TimeoutExpired:
        return False, "Le déploiement a dépassé le délai."
    except Exception as exc:
        return False, f"Déploiement impossible: {exc}"
    finally:
        if tmp_key:
            try:
                os.unlink(tmp_key)
            except OSError:
                pass


def _sftp_upload(host, port, user, password, private_key, passphrase, key_path, src: Path, remote: str):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sent = 0
    try:
        pkey = _load_pkey(private_key, key_path, passphrase)
        client.connect(hostname=host, port=port, username=user, password=password or None,
                       pkey=pkey, timeout=20)
        sftp = client.open_sftp()
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in rel.parts):
                continue
            target = f"{remote.rstrip('/')}/{rel.as_posix()}"
            if path.is_dir():
                try:
                    sftp.mkdir(target)
                except OSError:
                    pass
            else:
                sftp.put(str(path), target)
                sent += 1
        sftp.close()
        return True, f"{sent} fichier(s) envoyés vers {user}@{host}:{remote}"
    except Exception as exc:
        return False, f"SFTP: {exc}"
    finally:
        try:
            client.close()
        except Exception:
            pass


def shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _sftp_client(config: dict[str, Any], secrets: dict[str, str]):
    """Connecte un SFTP paramiko en réutilisant les mêmes secrets que ssh_exec."""
    import io as _io

    host = str(config.get("host") or "").strip()
    user = str(config.get("username") or config.get("user") or "").strip()
    port = int(config.get("port") or 22)
    if not host or not user:
        raise ValueError("Configuration SSH incomplète (hôte / utilisateur).")
    password = secrets.get("password") or ""
    private_key = secrets.get("private_key") or ""
    passphrase = secrets.get("passphrase") or ""
    key_path = _expand(str(config.get("key_path") or ""))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = _load_pkey(private_key, key_path, passphrase)
    client.connect(
        hostname=host, port=port, username=user, password=password or None, pkey=pkey,
        key_filename=key_path if (not pkey and key_path and Path(key_path).is_file()) else None,
        timeout=15, auth_timeout=20, banner_timeout=20,
        look_for_keys=not (pkey or password), allow_agent=not password,
    )
    return client, client.open_sftp()


def ssh_upload(
    config: dict[str, Any], secrets: dict[str, str], local_path: str, remote_path: str,
    timeout: int = 300,
) -> tuple[bool, str]:
    _debug("SFTP UPLOAD START")
    local = Path(local_path).expanduser().resolve()
    if not local.is_file():
        return False, f"Fichier local introuvable: {local}"
    try:
        client, sftp = _sftp_client(config, secrets)
    except Exception as exc:
        return False, f"SFTP: {exc}"
    try:
        sftp.put(str(local), remote_path)
        _debug("SFTP UPLOAD COMPLETED")
        return True, f"{local.name} envoyé dans {remote_path}"
    except Exception as exc:
        return False, f"SFTP upload: {exc}"
    finally:
        try:
            sftp.close()
            client.close()
        except Exception:
            pass


def ssh_download(
    config: dict[str, Any], secrets: dict[str, str], remote_path: str, local_path: str,
    timeout: int = 300,
) -> tuple[bool, str]:
    _debug("SFTP DOWNLOAD START")
    local_dir = Path(local_path).expanduser().resolve()
    try:
        local_dir.mkdir(parents=True, exist_ok=True) if local_dir.is_dir() or local_dir.suffix == "" \
            else local_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        client, sftp = _sftp_client(config, secrets)
    except Exception as exc:
        return False, f"SFTP: {exc}"
    try:
        name = Path(remote_path).name
        dest = Path(local_path).expanduser()
        if dest.is_dir() or (dest.suffix == "" and not dest.exists()):
            dest = dest / name
        sftp.get(remote_path, str(dest))
        _debug("SFTP DOWNLOAD COMPLETED")
        return True, f"{remote_path} téléchargé dans {dest}"
    except Exception as exc:
        return False, f"SFTP download: {exc}"
    finally:
        try:
            sftp.close()
            client.close()
        except Exception:
            pass
