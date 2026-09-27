"""Evidence-based mission verification."""

from __future__ import annotations

from typing import Any, Dict

from .mission import Mission
from .tools import ToolRegistry


class MissionVerifier:
    def verify(self, mission: Mission, tools: ToolRegistry) -> Dict[str, Any]:
        test_artifacts = [artifact for artifact in mission.artifacts if artifact.type == "test_result"]
        failed_tests = [artifact for artifact in test_artifacts if (artifact.content or {}).get("returncode") != 0]
        evidence = {
            "git_status": tools.execute_tool("git_status", {}),
            "git_diff": tools.execute_tool("git_diff", {}),
            "test_results": len(test_artifacts),
        }
        if failed_tests:
            return {"status": "FAIL", "reason": "tests_failed", "evidence": evidence}
        if not test_artifacts:
            return {"status": "NEEDS_FIX", "reason": "no_test_result_artifact", "evidence": evidence}
        return {"status": "PASS", "evidence": evidence}
