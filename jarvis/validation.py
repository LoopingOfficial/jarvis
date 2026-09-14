"""Deterministic validation between model output and JARVIS consumers."""
from __future__ import annotations

import ast
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class ValidationResult:
    ok: bool
    code: str = "OK"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def pass_(cls, message: str = "") -> "ValidationResult":
        return cls(True, "OK", message)

    @classmethod
    def fail(cls, code: str, message: str, **details: Any) -> "ValidationResult":
        return cls(False, code, message, details)


class FormatValidator:
    def validate(self, value: str, spec: dict[str, Any] | None = None) -> ValidationResult:
        spec = spec or {}
        text = value or ""
        if spec.get("json") or spec.get("format") == "json_raw":
            try: json.loads(text)
            except (TypeError, ValueError): return ValidationResult.fail("JSON_FORMAT", "expected=json_raw")
            if re.search(r"```|^\s*(json|JSON)\s*$", text, re.M): return ValidationResult.fail("JSON_FORMAT", "expected=json_raw")
        if spec.get("code_only") or spec.get("format") == "code_raw":
            if "```" in text or re.search(r"^\s*(voici|voici le code|explication)\b", text, re.I):
                return ValidationResult.fail("CODE_FORMAT", "expected=code_raw")
        max_lines = spec.get("max_lines")
        if max_lines is not None and len(text.splitlines()) > int(max_lines): return ValidationResult.fail("LINES_MAX", f"expected<={max_lines}_lines")
        if spec.get("one_sentence") and len(re.findall(r"[.!?](?:\s|$)", text)) > 1: return ValidationResult.fail("ONE_SENTENCE", "expected=one_sentence")
        return ValidationResult.pass_()


class MathValidator:
    def validate(self, value: str, spec: dict[str, Any] | None = None) -> ValidationResult:
        spec = spec or {}; expected = spec.get("expected")
        if expected is None: return ValidationResult.pass_()
        try:
            data = json.loads(value) if spec.get("json") else value.strip().replace(",", ".")
            actual = data.get(spec.get("field", "result")) if isinstance(data, dict) else float(re.search(r"[-+]?\d+(?:[.,]\d+)?", str(data)).group(0))
            if float(actual) != float(expected): return ValidationResult.fail("MATH_MISMATCH", "expected=deterministic_result", expected=expected, actual=actual)
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError): return ValidationResult.fail("MATH_FORMAT", "expected=numeric_result")
        return ValidationResult.pass_()


class CodeValidator:
    def validate(self, value: str, spec: dict[str, Any] | None = None) -> ValidationResult:
        spec = spec or {}; lang = str(spec.get("language", "")).lower(); text = value or ""
        if lang in {"python", "py"}:
            try: tree = ast.parse(text)
            except SyntaxError as exc: return ValidationResult.fail("CODE_SYNTAX", "python_syntax_error", line=exc.lineno)
            if spec.get("class") and not any(isinstance(n, ast.ClassDef) and n.name == spec["class"] for n in tree.body): return ValidationResult.fail("CODE_STRUCTURE", "expected=class")
        if spec.get("class") and not re.search(r"\bclass\s+" + re.escape(str(spec["class"])) + r"\b", text): return ValidationResult.fail("CODE_STRUCTURE", "expected=class")
        if spec.get("tests_pass") and not spec.get("test_runner_proof"): return ValidationResult.fail("TEST_PROOF", "expected=TestRunner_proof")
        return ValidationResult.pass_()


class CssValidator:
    def validate(self, value: str, spec: dict[str, Any] | None = None) -> ValidationResult:
        text = value or ""
        if re.search(r"\bdarken\s*\(|\blighten\s*\(|\$[A-Za-z_-]|&\s*[:.]|\{[^{}]*\{", text): return ValidationResult.fail("CSS_NON_NATIVE", "expected=native_css")
        for token in (spec or {}).get("required", []):
            if token not in text: return ValidationResult.fail("CSS_STRUCTURE", f"expected={token}")
        return ValidationResult.pass_()


class SecurityValidator:
    def validate(self, value: Any, spec: dict[str, Any] | None = None) -> ValidationResult:
        policy = spec or {}; text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        if policy.get("read_only") and re.search(r"\b(write|patch|delete|deploy|fs\.write|ssh\.write)\b", text, re.I): return ValidationResult.fail("READ_ONLY_WRITE", "expected=no_write")
        if policy.get("audit") and re.search(r"\b(safe|fixed|secure)\b", text, re.I) and not re.search(r"evidence|preuve|suspicion", text, re.I): return ValidationResult.fail("FINDING_PROOF", "expected=evidence_or_suspicion")
        return ValidationResult.pass_()


class IntentValidator:
    def validate(self, value: Any, spec: dict[str, Any] | None = None) -> ValidationResult:
        spec = spec or {}; calls = value if isinstance(value, list) else []
        if (spec.get("model_only") or spec.get("no_tools")) and calls: return ValidationResult.fail("MODEL_ONLY_TOOL", "expected=no_tools")
        if spec.get("read_only") and any(re.search(r"\b(write|delete|patch|deploy)\b", str(c), re.I) for c in calls): return ValidationResult.fail("READ_ONLY_WRITE", "expected=no_write")
        return ValidationResult.pass_()


class TestValidator:
    def validate(self, value: Any, spec: dict[str, Any] | None = None) -> ValidationResult:
        if (spec or {}).get("tests_required") and not (spec or {}).get("test_runner_proof"): return ValidationResult.fail("TEST_PROOF", "expected=TestRunner_proof")
        return ValidationResult.pass_()


class ValidationEngine:
    def __init__(self) -> None:
        self.validators = {"format": FormatValidator(), "math": MathValidator(), "code": CodeValidator(), "css": CssValidator(), "security": SecurityValidator(), "intent": IntentValidator(), "tests": TestValidator(), "source_grounding": self._source_grounding}

    @staticmethod
    def _source_grounding(value: Any, spec: dict[str, Any] | None = None) -> ValidationResult:
        from .source_grounding import validate_source_grounding
        return validate_source_grounding(str(value), (spec or {}).get("workbook", {}))

    def validate(self, value: Any, spec: dict[str, Any] | None = None, *, request_id: str = "") -> ValidationResult:
        spec = spec or {}; order = spec.get("validators") or ["format", "math", "code", "css", "security", "intent", "tests"]
        for name in order:
            validator = self.validators[name]
            payload = value if name != "intent" else spec.get("tool_calls", [])
            result = validator(payload, spec) if callable(validator) else validator.validate(payload, spec)
            if not result.ok: return result
        return ValidationResult.pass_()


class ValidationRetryLoop:
    def __init__(self, engine: ValidationEngine | None = None, max_corrections: int = 2) -> None:
        self.engine, self.max_corrections = engine or ValidationEngine(), max(0, int(max_corrections))

    def run(self, initial: Any, spec: dict[str, Any], correct: Callable[[str, int], Any]) -> tuple[Any, ValidationResult, int]:
        value = initial
        for attempt in range(self.max_corrections + 1):
            result = self.engine.validate(value, spec)
            if result.ok: return value, result, attempt
            if attempt == self.max_corrections: return value, result, attempt
            value = correct(f"VALIDATION_FAILED\ncode={result.code}\n{result.message}", attempt + 1)
        return value, ValidationResult.fail("VALIDATION_LOOP", "validation stopped"), self.max_corrections
