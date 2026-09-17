"""Wrapper sécurisé pour blender.run_script : boot puis exec du script utilisateur.

Validation AST effectuée côté serveur (jarvis/tools/blender_tools.py). Ici on
exécute le code validé dans un namespace confiné : pas de __builtins__ Python
complète, pas d'accès aux fichiers hors répertoire de travail, erreurs capturées.
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail  # noqa

    st = boot()
    progress(0.05, "Script détecté, configuration chargée", "initialisation")

    work_dir = os.path.abspath(st.get("work_dir") or st.get("output_dir") or ".")
    script_path = st.get("script")
    code = str(st.get("code") or "").strip()
    if script_path and not os.path.exists(script_path):
        raise RuntimeError(f"Script introuvable : {script_path}")
    if not script_path:
        if not code:
            raise RuntimeError("Aucun script utilisateur fourni.")
        script_path = os.path.join(work_dir, "user_script.py")
        try:
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(code)
        except OSError as exc:
            raise RuntimeError(f"Ecriture du script impossible dans le repertoire de travail : {exc}")
    if not os.path.abspath(script_path).startswith(work_dir):
        raise RuntimeError("Script hors du répertoire de travail autorisé.")

    with open(script_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    allowed_builtins = {
        "print", "len", "range", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "sum", "min", "max", "abs", "round", "enumerate",
        "zip", "sorted", "type", "isinstance", "issubclass", "hasattr", "getattr",
        "setattr", "any", "all", "reversed", "True", "False", "None", "Exception",
        "ValueError", "RuntimeError", "IndexError", "KeyError", "TypeError",
        "hash", "id", "repr", "vars", "slice", "iter", "next", "map", "filter",
    }

    namespace = {
        "bpy": __import__("bpy"),
        "print": lambda *a, **k: print(*a, **k),
        "settings": settings,
        "out": out,
        "progress": progress,
        "finish": finish,
        "__work_dir__": work_dir,
        "__builtins__": {name: getattr(__builtins__, name)
                         for name in allowed_builtins if hasattr(__builtins__, name)},
    }

    def guarded_open(path, mode="r", *args, **kwargs):
        ap = os.path.abspath(path)
        if not ap.startswith(work_dir):
            raise PermissionError("Accès fichier refusé hors du répertoire de travail.")
        return open(ap, mode, *args, **kwargs)

    namespace["open"] = guarded_open
    namespace["__builtins__"]["open"] = guarded_open

    progress(0.2, "Exécution du script…", "exécution")
    exec(compile(source, script_path, "exec"), namespace)
    progress(0.95, "Script terminé", "finalisation")

    meta = {
        "script": os.path.basename(script_path),
        "execution": "terminé",
        "files": [out(f) for f in sorted(os.listdir(out(""))) if not f.startswith(".") and f != "metadata.json"],
    }
    finish(meta, message=f"Script {os.path.basename(script_path)} exécuté avec succès")
except Exception:
    try:
        fail(f"run_wrapper : {traceback.format_exc()}")
    except Exception:
        pass
    raise