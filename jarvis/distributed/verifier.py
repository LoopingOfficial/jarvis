"""Evidence-based mission verification."""

from __future__ import annotations

from typing import Any, Dict

from .mission import Mission
from .tools import ToolRegistry


class MissionVerifier:
    def verify(self, mission: Mission, tools: ToolRegistry) -> Dict[str, Any]:
        test_artifacts = [artifact for artifact in mission.artifacts if artifact.type == "test_result"]
        failed_tests = [artifact for artifact in test_artifacts if (artifact.content or {}).get("returncode") != 0]
        change_artifacts = [artifact for artifact in mission.artifacts if artifact.type == "change_set"]
        review_artifacts = [artifact for artifact in mission.artifacts if artifact.type == "review"]
        evidence = {
            "git_status": tools.execute_tool("git_status", {}),
            "git_diff": tools.execute_tool("git_diff", {}),
            "test_results": len(test_artifacts),
            "change_sets": len(change_artifacts),
            "reviews": len(review_artifacts),
        }
        if failed_tests:
            return {"status": "FAIL", "reason": "tests_failed", "evidence": evidence}
        if not test_artifacts:
            return {"status": "NEEDS_FIX", "reason": "no_test_result_artifact", "evidence": evidence}
        is_correction = any(word in mission.user_request.lower() for word in ("corrige", "fix", "répare", "correct"))
        if is_correction and not change_artifacts:
            return {"status": "NEEDS_FIX", "reason": "no_structured_change_set", "evidence": evidence}
        if is_correction and not evidence["git_diff"]:
            return {"status": "NEEDS_FIX", "reason": "no_workspace_diff", "evidence": evidence}
        if is_correction and not review_artifacts:
            return {"status": "NEEDS_FIX", "reason": "no_review_artifact", "evidence": evidence}
        if is_correction and any((artifact.content or {}).get("decision") != "PASS" for artifact in review_artifacts):
            return {"status": "NEEDS_FIX", "reason": "review_rejected_change", "evidence": evidence}
        return {"status": "PASS", "evidence": evidence}
