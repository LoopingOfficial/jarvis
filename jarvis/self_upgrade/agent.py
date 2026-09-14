"""LocalCodeAgent — boucle autonome inspect → search → read → patch → test → corriger → retest."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .ollama import OllamaClient
from .inspector import ProjectInspector
from .tools_def import (
    AgentContext,
    HANDLERS,
    TOOLS,
    tool_code_read,
    tool_code_search,
    tool_git_diff,
    tool_code_list,
)

MAX_ITERATIONS = int(__import__("os").getenv("JARVIS_SU_MAX_ITERATIONS", "10"))

CODING_SYSTEM = (
    "Tu es le LocalCodeAgent de JARVIS : tu améliores le code dans un workspace Git isolé.\n"
    "Consignes :\n"
    "1. Reste dans le workspace. Utilise toujours des chemins relatifs.\n"
    "2. Joue le rôle de développeur senior : inspecte (code.search, code.read), puis modifie "
    "(code.write / code.patch), puis teste (test.run), lis les erreurs, corrige et re-teste.\n"
    "3. N'envoie jamais tout le projet : lis uniquement les fichiers pertinents.\n"
    "4. Ne modifie JAMAIS des fichiers hors du workspace ou des chemins protégés.\n"
    "5. Quand les tests passent et que l'objectif est atteint, termine par test.run puis "
    "git.commit (message décrivant l'upgrade), puis réponds par un résumé SF sans tool call.\n"
    "6. Si un test échoue : corrige les erreurs réelles et relance, jusqu'à succès.\n"
)


def _parse_tool_calls_text(text: str) -> list[dict[str, Any]]:
    """Extrait des tool calls quand le modèle les émet en JSON dans le texte (qwen2.5-coder)."""
    import json as _json
    if not text:
        return []
    try:
        data = _json.loads(text)
        if isinstance(data, list):
            out = []
            for n, c in enumerate(data):
                if isinstance(c, dict) and c.get("name"):
                    args = c.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = _json.loads(args)
                        except Exception:
                            args = {}
                    out.append({"id": f"t{n}", "name": c["name"], "arguments": args or {}})
            return out
        if isinstance(data, dict) and data.get("name"):
            args = data.get("arguments")
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except Exception:
                    args = {}
            return [{"id": "t0", "name": data["name"], "arguments": args or {}}]
    except Exception:
        pass
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]", "{", "}"):
            continue
        try:
            obj = _json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments")
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except Exception:
                    args = {}
            out.append({"id": f"t{len(out)}", "name": obj["name"], "arguments": args or {}})
    return out


class LocalCodeAgent:
    def __init__(self, workspace: Path, project_root: Path, python: str = "python",
                 ollama_url: str = "http://127.0.0.1:11434",
                 coder_model: str = "") -> None:
        self._workspace = Path(workspace)
        self._project_root = Path(project_root)
        self._python = python
        self._client = OllamaClient(ollama_url)
        self._model = coder_model
        self._inspector = ProjectInspector(self._workspace)

    def _pick_model(self) -> str:
        if self._model:
            return self._model
        models = self._client.list_models()
        for preferred in ("qwen2.5-coder:7b-instruct-q4_K_M", "jarvis-blender:latest",
                          "qwen3:8b", "jarvis-windows:latest", "jarvis-astra:latest"):
            if preferred in models:
                return preferred
        return models[0] if models else ""

    def run(self, prompt: str, test_target: str = "") -> dict[str, Any]:
        model = self._pick_model()
        ctx = AgentContext(workspace=self._workspace, project_root=self._project_root,
                           python=self._python)
        started = time.time()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": CODING_SYSTEM},
            {"role": "user", "content": (
                f"OBJECTIF :\n{prompt}\n\n"
                "Workspace clone du projet JARVIS. Travaille ici. Ne modifie pas la version active.\n"
                + (f"Tests à exécuter : {test_target}\n" if test_target else "")
            )},
        ]
        final_banner = "Aucun message final produit par le modèle."
        iterations = 0
        while iterations < MAX_ITERATIONS:
            iterations += 1
            resp = self._client.chat(model, messages, tools=TOOLS,
                                     temperature=0.2, max_tokens=2048)
            if resp.get("error"):
                final_banner = f"Erreur modèle ({model}) : {resp['error']}"
                return self._report(ctx, model, iterations, prompt, started,
                                    ok=False, summary=final_banner)
            text = resp.get("text", "")
            calls = resp.get("tool_calls") or []
            if not calls:
                parsed = _parse_tool_calls_text(text)
                if parsed:
                    # Le modèle a émis ses tool calls en JSON dans le texte : on les exécute.
                    calls = parsed
                    text = ""
            if text:
                messages.append({"role": "assistant", "content": text})
            if not calls:
                final_banner = text or final_banner
                break
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [
                                 {"id": c["id"], "type": "function",
                                  "function": {"name": c["name"],
                                               "arguments": c.get("arguments") or {}}}
                                 for c in calls
                             ]})
            for call in calls:
                name = call.get("name", "")
                args = call.get("arguments") or {}
                handler = HANDLERS.get(name)
                if not handler:
                    output = f"Tool inconnu: {name}"
                    ok = False
                else:
                    try:
                        result = handler(ctx, args)
                        ok = bool(result.get("ok"))
                        output = str(result.get("output") or result.get("error") or "")
                    except Exception as exc:
                        ok = False
                        output = f"Exception: {exc}"
                messages.append({"role": "tool", "content": output,
                                 "tool_name": name})
        ctx.log("info", f"boucle terminée après {iterations} itérations")
        summary = final_banner if final_banner else "Terminé."
        return self._report(ctx, model, iterations, prompt, started,
                            ok=True, summary=summary)

    def _report(self, ctx: AgentContext, model: str, iterations: int,
                prompt: str, started: float, ok: bool, summary: str) -> dict[str, Any]:
        from .git_mgr import GitManager
        git = GitManager(self._workspace)
        modified = git.diff_files("HEAD")
        diff = git.diff("HEAD")
        return {
            "ok": ok,
            "summary": summary,
            "model": model,
            "iterations": iterations,
            "duration_s": round(time.time() - started, 1),
            "files_changed": [f for f in modified if not f.startswith("data_")] or [],
            "git_diff": diff[:12000],
            "tools_used": sorted(set(ctx.tools_used)),
            "logs": ctx.logs[-40:],
            "request_promotion": getattr(ctx, "request_promotion", False),
            "candidate_started": ctx.candidate_started,
            "candidate_port": ctx.candidate_port,
        }