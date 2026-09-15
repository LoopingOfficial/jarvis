"""Empêche une action planifiée de dépasser le risque de l'objectif utilisateur."""
class RiskEscalationGuard:
    READ_ONLY_TOOLS = {"ssh.run", "ssh.list", "ssh.read_file", "ssh.logs", "ssh.status", "fs.search", "fs.read"}

    def check(self, goal: str, tool_id: str, arguments: dict) -> tuple[bool, str]:
        if goal in {"read_file", "search_file", "get_file_contents", "list_directory"}:
            if tool_id == "ssh.service" or tool_id in {"ssh.write_file", "ssh.delete"}:
                return False, f"Risk escalation blocked: {tool_id} incompatible avec un objectif lecture."
            if tool_id == "ssh.run":
                command = str(arguments.get("command", "")).casefold()
                if any(x in command for x in ("systemctl stop", "systemctl restart", "rm ", "kill ", "pkill ", "shutdown", "reboot", "truncate", "drop ", "delete ")):
                    return False, "Commande destructive bloquée pour un objectif lecture."
        return True, ""
