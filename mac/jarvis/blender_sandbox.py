"""Sandbox logique pour blender.run_script.

Un script bpy tourne dans le meme processus que Blender : il peut donc, en
theorie, tout faire sur la machine. Ce module refuse AVANT execution tout
script qui sort de l'atelier 3D :

  - imports systeme (os.system, subprocess, socket, shutil, ctypes, winreg...)
  - execution dynamique (eval, exec, compile, __import__)
  - acces reseau (urllib, requests, http.client)
  - ecriture hors du workspace autorise (data/generated/3d)

L'analyse est faite sur l'AST : une chaine obfusquee ne passe pas, car les
appels dangereux restent des appels identifiables. Le filtre est volontairement
conservateur : en cas de doute, il refuse et l'explique.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# Modules interdits, meme importes indirectement.
FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket", "ctypes", "winreg", "_winreg",
    "importlib", "pickle", "marshal", "multiprocessing", "threading", "signal",
    "urllib", "urllib2", "urllib3", "requests", "http", "httplib", "ftplib",
    "smtplib", "telnetlib", "paramiko", "pty", "platform", "webbrowser",
    "tempfile", "glob", "pathlib", "sqlite3", "asyncio",
}
# `os.path` seul reste utile et inoffensif : il est autorise explicitement.
ALLOWED_MODULE_PREFIXES = ("bpy", "bmesh", "mathutils", "math", "json", "random",
                           "colorsys", "itertools", "functools", "time", "re",
                           "builder", "materials", "modifiers", "lighting", "camera",
                           "rigging", "animation", "optimize", "textures",
                           "blueprints", "exporters", "sceneio", "common", "pipeline")

FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
}
# Operateurs Blender qui touchent au systeme de fichiers ou lancent du code.
FORBIDDEN_OPS = re.compile(
    r"bpy\.ops\.(?:wm\.(?:quit_blender|open_mainfile|save_mainfile|url_open|"
    r"console_|path_open)|script\.|preferences\.|text\.run_script)",
    re.IGNORECASE)
FORBIDDEN_ATTRS = {"__subclasses__", "__globals__", "__builtins__", "__class__",
                   "__bases__", "__mro__", "__code__", "__loader__", "__reduce__"}


class ScriptRejected(Exception):
    pass


def _module_allowed(name: str) -> bool:
    root = str(name or "").split(".")[0]
    if name == "os.path" or name.startswith("os.path."):
        return True
    if root in FORBIDDEN_MODULES:
        return False
    return root in ALLOWED_MODULE_PREFIXES or root in {"typing", "dataclasses"}


def inspect_script(code: str) -> list:
    """Retourne la liste des motifs refuses. Vide = script acceptable."""
    problems: list[str] = []
    text = str(code or "")
    if not text.strip():
        return ["Script vide."]
    if len(text) > 200_000:
        return ["Script trop volumineux (limite 200 000 caracteres)."]
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return ["Erreur de syntaxe Python ligne %s : %s" % (exc.lineno, exc.msg)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    problems.append("import interdit : %s (ligne %d)"
                                    % (alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if not _module_allowed(node.module or ""):
                problems.append("import interdit : %s (ligne %d)"
                                % (node.module, node.lineno))
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in FORBIDDEN_CALLS:
                problems.append("appel interdit : %s() (ligne %d)" % (name, node.lineno))
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                problems.append("acces interdit : %s (ligne %d)"
                                % (node.attr, node.lineno))

    for match in FORBIDDEN_OPS.finditer(text):
        problems.append("operateur Blender interdit : %s" % match.group(0))
    return problems


def workspace_violations(code: str, workspace: Path) -> list:
    """Detecte les chemins absolus ecrits hors du workspace autorise."""
    root = str(Path(workspace).resolve()).lower().replace("\\", "/")
    problems = []
    for raw in re.findall(r"['\"]([A-Za-z]:[\\/][^'\"]{2,240}|/[^'\"]{2,240})['\"]", code):
        normalised = raw.lower().replace("\\", "/")
        if normalised.startswith(root):
            continue
        # Un chemin de LECTURE (texture, HDRI, modele importe) reste autorise :
        # seules les ecritures explicites hors workspace sont refusees.
        if re.search(r"(?:save|write|export|render\.filepath)", code, re.IGNORECASE):
            problems.append("chemin hors workspace : %s" % raw[:120])
    return problems


def validate(code: str, workspace: Path) -> str:
    """Leve ScriptRejected si le script sort de l'atelier 3D."""
    problems = inspect_script(code) + workspace_violations(code, workspace)
    if not problems:
        return code
    raise ScriptRejected(
        "Script refuse par la sandbox 3D (il doit rester dans l'atelier Blender) :\n- "
        + "\n- ".join(dict.fromkeys(problems))[:1200])


def is_safe(code: str, workspace: Path) -> bool:
    try:
        validate(code, workspace)
        return True
    except ScriptRejected:
        return False
