#!/usr/bin/env python3
"""Evidence-aware verifier for tool-using coding-agent trajectories."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala",
}
TEST_HINTS = ("test", "pytest", "unittest", "jest", "vitest", "mocha", "rspec",
              "go test", "cargo test", "mvn test", "gradle test", "npm test",
              "pnpm test", "yarn test")
BUILD_HINTS = ("build", "lint", "typecheck", "tsc", "mypy", "ruff", "eslint",
               "flake8", "cargo check", "cargo clippy")
FAIL_PATTERNS = (
    r"\bfailed\b", r"\bfailures?\b", r"\berrors?\b", r"traceback",
    r"syntaxerror", r"assertionerror", r"segmentation fault", r"command not found",
    r"no such file or directory", r"compilation (?:failed|error)",
)
STACK_TRACE_RE = re.compile(r"(traceback|stack trace|exception|syntaxerror|typeerror|valueerror|assertionerror)", re.IGNORECASE)
SKIPPED_TEST_RE = re.compile(r"\b(skipped|xfailed|xfail|pending)\b", re.IGNORECASE)
TRUNCATED_LOG_RE = re.compile(r"\b(truncated|omitted|ellipsized|too long|output hidden)\b", re.IGNORECASE)
SPOOFED_OUTPUT_RE = re.compile(r"\b(echo|printf)\b.*\b(passed|success|ok|done)\b", re.IGNORECASE)
PASS_PATTERNS = (
    r"\bpassed\b", r"\bok\b", r"\b0 failed\b", r"\b0 errors\b",
    r"tests? pass(?:ed)?", r"build successful", r"successfully built",
)
SUCCESS_CLAIM_RE = re.compile(
    r"\b(done|fixed|implemented|complete|completed|success|successful|passes|passed|working)\b",
    re.IGNORECASE,
)
VERSION = "0.1.0"

DEFAULT_CONFIG = {
    "profile": "default",
    "verifier_version": VERSION,
    "test_commands": ["pytest", "npm test", "pnpm test", "yarn test", "cargo test", "go test"],
    "build_commands": list(BUILD_HINTS),
    "source_extensions": sorted(SOURCE_EXTS),
    "test_path_keywords": ["test", "spec"],
    "risk_patterns": [],
    "missing_tests_penalty": 0.12,
    "tests_only_change_penalty": 0.15,
    "close_case_threshold": 0.10,
}

BUILTIN_PROFILES = {
    "default": {},
    "python": {
        "test_commands": ["pytest", "python -m pytest", "unittest", "tox", "nox"],
        "build_commands": ["ruff", "mypy", "pyright", "python -m compileall", "flake8"],
        "source_extensions": [".py"],
        "test_path_keywords": ["test", "tests"],
        "risk_patterns": [r"subprocess\.", r"os\.system", r"eval\(", r"exec\("],
    },
    "node": {
        "test_commands": ["npm test", "pnpm test", "yarn test", "jest", "vitest", "mocha"],
        "build_commands": ["npm run build", "pnpm build", "yarn build", "npm run lint", "eslint", "tsc"],
        "source_extensions": [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
        "test_path_keywords": ["test", "spec", "__tests__"],
        "risk_patterns": [r"eval\(", r"child_process", r"dangerouslySetInnerHTML", r"innerHTML\s*="],
    },
    "webapp": {
        "test_commands": ["npm test", "pnpm test", "yarn test", "playwright test", "vitest", "jest"],
        "build_commands": ["npm run build", "pnpm build", "yarn build", "npm run lint", "eslint", "tsc"],
        "source_extensions": [".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".vue", ".svelte"],
        "test_path_keywords": ["test", "spec", "__tests__", "e2e"],
        "risk_patterns": [r"console error", r"pageerror", r"network.*failed", r"hydration", r"dangerouslySetInnerHTML"],
    },
    "security-sensitive": {
        "test_commands": ["pytest", "npm test", "pnpm test", "cargo test", "go test"],
        "build_commands": ["npm audit", "pip-audit", "cargo audit", "gosec", "bandit", "semgrep", "ruff", "eslint"],
        "risk_patterns": [
            r"password\s*=", r"api[_-]?key\s*=", r"secret\s*=", r"token\s*=",
            r"eval\(", r"exec\(", r"os\.system", r"child_process",
            r"SELECT .* \+", r"dangerouslySetInnerHTML",
        ],
    },
    "browser-agent": {
        "test_commands": ["playwright test", "npm test", "pnpm test", "yarn test"],
        "build_commands": ["npm run build", "pnpm build", "yarn build"],
        "source_extensions": [".js", ".jsx", ".ts", ".tsx", ".html", ".css"],
        "test_path_keywords": ["test", "spec", "e2e"],
        "risk_patterns": [r"console error", r"pageerror", r"requestfailed", r"timeout", r"selector.*not found"],
    },
}


@dataclass
class Event:
    event_id: str
    event_type: str
    timestamp: Optional[str]
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    candidate_id: str
    trajectory: List[Event]


@dataclass
class Task:
    task_id: str
    task_description: str
    candidates: List[Candidate]


@dataclass
class EvidenceItem:
    evidence_id: str
    candidate_id: str
    event_ids: List[str]
    kind: str
    summary: str
    direction: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateAnalysis:
    candidate: Candidate
    evidence: List[EvidenceItem]
    checks: Dict[str, Any]
    unsupported_claims: List[Dict[str, Any]]
    failure_modes: List[str]
    missing_evidence: List[str]
    risk_factors: List[str]
    uncertainty_reasons: List[str]
    score: float
    confidence: str
    verdict: str
    summary: str
    semantic_certificate: Dict[str, Any] = field(default_factory=dict)


class SemanticJudge:
    """Stub interface for optional LLM/task-specific judgment.

    The MVP is deterministic. Implementations may return an adjustment in
    [-0.15, 0.15] plus cited evidence IDs.
    """

    def judge(self, task: Task, candidate: Candidate, evidence: List[EvidenceItem]) -> Dict[str, Any]:
        return {"adjustment": 0.0, "evidence_ids": [], "reason": "deterministic MVP"}


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(config_path) if config_path else Path.cwd() / ".verifier" / "verifier.toml"
    file_config: Dict[str, Any] = {}
    if not path.exists():
        return merge_profile_config({})
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("["):
            continue
        key, value = line.split("=", 1)
        file_config[key.strip()] = _parse_scalar(value)
    return merge_profile_config(file_config)


def merge_profile_config(file_config: Dict[str, Any]) -> Dict[str, Any]:
    profile = str(file_config.get("profile") or DEFAULT_CONFIG["profile"])
    if profile not in BUILTIN_PROFILES:
        profile = "default"
    config = dict(DEFAULT_CONFIG)
    config.update(BUILTIN_PROFILES.get(profile, {}))
    config.update(file_config)
    config["profile"] = profile
    return config


def write_default_config(path: Path, profile: str = "default") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = merge_profile_config({"profile": profile})
    lines = [
        "# Repo-local trajectory verifier configuration.",
        "# Commit this file when project-specific scoring and detection policy matters.",
        "# Profiles encode reusable policy inspired by pk-skills1: default, python, node, webapp, security-sensitive, browser-agent.",
        f"verifier_version = '{VERSION}'",
        f"profile = '{config['profile']}'",
        "",
        "# Commands treated as test commands when seen in collected trajectories.",
        f"test_commands = {toml_array(config['test_commands'])}",
        "",
        "# Commands treated as build/lint/static-check commands.",
        f"build_commands = {toml_array(config['build_commands'])}",
        "",
        "# File extensions treated as source changes.",
        f"source_extensions = {toml_array(config['source_extensions'])}",
        "",
        "# Path keywords treated as test/spec files.",
        f"test_path_keywords = {toml_array(config['test_path_keywords'])}",
        "",
        "# Regexes that mark suspicious or security-sensitive behavior in logs/diffs.",
        f"risk_patterns = {toml_array(config['risk_patterns'])}",
        "",
        "missing_tests_penalty = 0.12",
        "tests_only_change_penalty = 0.15",
        "close_case_threshold = 0.10",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def toml_array(values: List[Any]) -> str:
    return "[" + ", ".join("'" + str(v).replace("'", "\\'") + "'" for v in values) + "]"


def parse_task(payload: Dict[str, Any]) -> Task:
    candidates = []
    for c in payload.get("candidates", []):
        events = []
        for idx, e in enumerate(c.get("trajectory", [])):
            events.append(Event(
                event_id=str(e.get("event_id") or f"event_{idx}"),
                event_type=str(e.get("event_type") or ""),
                timestamp=e.get("timestamp"),
                content=str(e.get("content") or ""),
                metadata=dict(e.get("metadata") or {}),
            ))
        candidates.append(Candidate(str(c.get("candidate_id")), events))
    return Task(
        task_id=str(payload.get("task_id")),
        task_description=str(payload.get("task_description") or ""),
        candidates=candidates,
    )


def _text(event: Event) -> str:
    meta = event.metadata or {}
    return "\n".join(str(x or "") for x in (
        event.content, meta.get("command"), meta.get("stdout"), meta.get("stderr"), meta.get("diff")
    ))


def _is_test_command(command: str, config: Optional[Dict[str, Any]] = None) -> bool:
    lc = command.lower()
    hints = tuple((config or {}).get("test_commands") or TEST_HINTS)
    return any(str(h).lower() in lc for h in hints)


def _is_build_or_lint(command: str, config: Optional[Dict[str, Any]] = None) -> bool:
    lc = command.lower()
    hints = tuple((config or {}).get("build_commands") or BUILD_HINTS)
    return any(str(h).lower() in lc for h in hints)


def _matches_profile_risk(text: str, config: Optional[Dict[str, Any]] = None) -> bool:
    return any(re.search(str(pattern), text, re.IGNORECASE) for pattern in (config or {}).get("risk_patterns", []))


def _has_failure(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in FAIL_PATTERNS)


def _has_pass(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in PASS_PATTERNS)


def _diff_stats(diff: str) -> Tuple[int, int]:
    added = removed = 0
    for line in (diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _path_from_diff(diff: str) -> Optional[str]:
    m = re.search(r"^\+\+\+\s+b/(.+)$", diff or "", re.MULTILINE)
    return m.group(1).strip() if m else None


def _is_test_path(path: str, config: Optional[Dict[str, Any]] = None) -> bool:
    p = (path or "").lower().replace("\\", "/")
    keywords = tuple((config or {}).get("test_path_keywords") or ("test", "spec"))
    return any(str(k).lower() in p for k in keywords)


def _is_source_path(path: str, config: Optional[Dict[str, Any]] = None) -> bool:
    exts = set((config or {}).get("source_extensions") or SOURCE_EXTS)
    return Path(path or "").suffix.lower() in exts and not _is_test_path(path, config)


def _task_asks_for_test_changes(task_description: str) -> bool:
    text = task_description.lower()
    if re.search(r"\brun\b.{0,20}\btests?\b", text) and not re.search(r"\b(add|write|create|update|modify)\b.{0,40}\b(tests?|specs?)\b", text):
        return False
    return bool(re.search(r"\b(add|write|create|update|modify|fix)\b.{0,40}\b(tests?|specs?)\b", text))


def extract_evidence(candidate: Candidate, config: Optional[Dict[str, Any]] = None) -> List[EvidenceItem]:
    evidence: List[EvidenceItem] = []

    def add(event: Event, kind: str, summary: str, direction: str, **data: Any) -> None:
        evidence.append(EvidenceItem(
            evidence_id=f"{candidate.candidate_id}:E{len(evidence) + 1}",
            candidate_id=candidate.candidate_id,
            event_ids=[event.event_id],
            kind=kind,
            summary=summary,
            direction=direction,
            data=data,
        ))

    for event in candidate.trajectory:
        meta = event.metadata or {}
        command = str(meta.get("command") or "")
        exit_code = meta.get("exit_code")
        combined = _text(event)

        if event.event_type == "command" or command:
            direction = "negative" if isinstance(exit_code, int) and exit_code != 0 else "uncertainty"
            add(event, "command", f"Ran command: {command or event.content[:120]}", direction,
                command=command, exit_code=exit_code)
            if isinstance(exit_code, int) and exit_code == 0:
                add(event, "zero_exit", f"Command exited successfully: {command}", "positive",
                    command=command, exit_code=exit_code)
            if SPOOFED_OUTPUT_RE.search(command):
                add(event, "suspicious_behavior", f"Command may spoof success-looking output: {command}", "negative",
                    command=command)
            if _is_test_command(command, config):
                passed = exit_code == 0 and not _has_failure(combined)
                failed = (isinstance(exit_code, int) and exit_code != 0) or _has_failure(combined)
                if passed:
                    add(event, "test_result", f"Test command passed: {command}", "positive",
                        command=command, exit_code=exit_code)
                elif failed:
                    add(event, "test_result", f"Test command failed or showed failures: {command}", "negative",
                        command=command, exit_code=exit_code)
                else:
                    add(event, "test_result", f"Test command outcome is unclear: {command}", "uncertainty",
                        command=command, exit_code=exit_code)
            if _is_build_or_lint(command, config) and ((isinstance(exit_code, int) and exit_code != 0) or _has_failure(combined)):
                add(event, "build_lint_failure", f"Build/lint/syntax command failed: {command}", "negative",
                    command=command, exit_code=exit_code)
            elif _is_build_or_lint(command, config) and isinstance(exit_code, int) and exit_code == 0 and not _has_failure(combined):
                add(event, "build_lint_success", f"Build/lint/syntax command succeeded: {command}", "positive",
                    command=command, exit_code=exit_code)

        if isinstance(exit_code, int) and exit_code != 0:
            add(event, "nonzero_exit", f"Command exited nonzero: {exit_code}", "negative",
                command=command, exit_code=exit_code)

        diff = str(meta.get("diff") or "")
        if event.event_type == "file_diff" or diff:
            path = str(meta.get("file_path") or _path_from_diff(diff) or "")
            added, removed = _diff_stats(diff or event.content)
            direction = "positive" if added + removed >= 3 else "uncertainty"
            add(event, "file_diff", f"Changed {path or 'unknown file'} (+{added}/-{removed})", direction,
                file_path=path, added=added, removed=removed)

        if event.event_type == "artifact":
            add(event, "artifact", f"Created or modified artifact: {meta.get('file_path') or event.content[:120]}",
                "positive", file_path=meta.get("file_path"))

        log_like_event = event.event_type in {"command", "command_output", "test_result", "tool_call", "error"}
        if event.event_type == "error" or (log_like_event and _has_failure(combined)):
            add(event, "error", f"Visible error/failure signal in {event.event_id}", "negative")
        if log_like_event and STACK_TRACE_RE.search(combined):
            add(event, "stack_trace", f"Visible stack trace or exception signal in {event.event_id}", "negative")
        if log_like_event and SKIPPED_TEST_RE.search(combined):
            add(event, "suspicious_behavior", f"Test output includes skipped or expected-failure markers in {event.event_id}", "negative")
        if log_like_event and TRUNCATED_LOG_RE.search(combined):
            add(event, "suspicious_behavior", f"Log output appears truncated or hidden in {event.event_id}", "negative")
        if _matches_profile_risk(combined, config):
            add(event, "suspicious_behavior", f"Profile-specific risk pattern matched in {event.event_id}", "negative")

        if event.event_type in ("assistant_message", "final_answer") and SUCCESS_CLAIM_RE.search(event.content):
            add(event, "success_claim", event.content.strip()[:180], "uncertainty")

    return evidence


def run_checks(task: Task, candidate: Candidate, evidence: List[EvidenceItem], config: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str], List[str], List[str], List[str]]:
    kinds = [e.kind for e in evidence]
    test_results = [e for e in evidence if e.kind == "test_result"]
    commands = [e for e in evidence if e.kind == "command"]
    diffs = [e for e in evidence if e.kind == "file_diff"]
    source_diffs = [e for e in diffs if _is_source_path(str(e.data.get("file_path") or ""), config)]
    test_diffs = [e for e in diffs if _is_test_path(str(e.data.get("file_path") or ""), config)]
    final_claims = [e for e in evidence if e.kind == "success_claim"]
    negative = [e for e in evidence if e.direction == "negative"]
    suspicious = [e for e in evidence if e.kind == "suspicious_behavior"]

    passed_tests = [e for e in test_results if e.direction == "positive"]
    failed_tests = [e for e in test_results if e.direction == "negative"]
    last_passing_test_idx = max((evidence.index(e) for e in passed_tests), default=-1)
    last_test = test_results[-1] if test_results else None
    unresolved_failed_tests = [last_test] if last_test and last_test.direction == "negative" else []
    unresolved_nonzero = [
        e for e in evidence
        if e.kind == "nonzero_exit" and evidence.index(e) > last_passing_test_idx
    ]
    unresolved_build_lint = [
        e for e in evidence
        if e.kind == "build_lint_failure" and evidence.index(e) > last_passing_test_idx
    ]
    meaningful_diffs = [e for e in diffs if int(e.data.get("added") or 0) + int(e.data.get("removed") or 0) >= 3]
    suspicious_small_diffs = [e for e in diffs if int(e.data.get("added") or 0) + int(e.data.get("removed") or 0) < 3]

    checks = {
        "has_commands": bool(commands),
        "passed_tests": bool(passed_tests),
        "failed_tests": bool(unresolved_failed_tests),
        "missing_tests": not test_results,
        "nonzero_exits": [e.evidence_id for e in unresolved_nonzero],
        "build_lint_failures": [e.evidence_id for e in unresolved_build_lint],
        "build_lint_succeeded": any(e.kind == "build_lint_success" for e in evidence),
        "stack_traces": [e.evidence_id for e in evidence if e.kind == "stack_trace"],
        "suspicious_behaviors": [e.evidence_id for e in suspicious],
        "has_meaningful_code_change": bool(meaningful_diffs),
        "suspiciously_small_diffs": [e.evidence_id for e in suspicious_small_diffs],
        "tests_only_change": bool(test_diffs) and not source_diffs,
        "made_no_meaningful_code_change": not meaningful_diffs,
        "success_claims": [e.evidence_id for e in final_claims],
        "retry_loop": len([e for e in test_results if e.direction == "negative"]) >= 3,
    }

    unsupported_claims = []
    if final_claims and (unresolved_failed_tests or checks["nonzero_exits"] or checks["build_lint_failures"]):
        unsupported_claims.append({
            "claim": "Candidate claimed success despite contradictory logs.",
            "reason": "Success claim conflicts with failed tests, nonzero exits, or build/lint failures.",
            "related_evidence_ids": [e.evidence_id for e in final_claims + negative],
        })
    elif final_claims and not passed_tests:
        unsupported_claims.append({
            "claim": "Candidate claimed success without passing test evidence.",
            "reason": "No passing test result was observed.",
            "related_evidence_ids": [e.evidence_id for e in final_claims],
        })

    failure_modes = []
    if unresolved_failed_tests:
        failure_modes.append("failing_tests")
    if checks["nonzero_exits"]:
        failure_modes.append("nonzero_command_exit")
    if checks["build_lint_failures"]:
        failure_modes.append("build_lint_or_syntax_failure")
    if checks["stack_traces"]:
        failure_modes.append("unresolved_exception_or_stack_trace")
    if checks["made_no_meaningful_code_change"]:
        failure_modes.append("no_meaningful_code_change")
    task_asks_for_tests = _task_asks_for_test_changes(task.task_description)
    if checks["tests_only_change"] and not task_asks_for_tests:
        failure_modes.append("changed_tests_only")
    if unsupported_claims:
        failure_modes.append("unsupported_or_contradicted_success_claim")

    risk_factors = []
    if checks["tests_only_change"] and not task_asks_for_tests:
        risk_factors.append("test_tampering_or_tests_only_change")
    if checks["suspiciously_small_diffs"]:
        risk_factors.append("empty_or_suspiciously_small_diff")
    if checks["suspicious_behaviors"]:
        risk_factors.append("suspicious_or_reward_hacking_signal")
    if checks["retry_loop"]:
        risk_factors.append("repetitive_failing_retry_loop")
    if final_claims and not passed_tests:
        risk_factors.append("fake_or_unsupported_completion_language")
    if checks["passed_tests"] and not checks["build_lint_succeeded"]:
        risk_factors.append("no_separate_build_or_lint_success_observed")

    missing_evidence = []
    if not test_results:
        missing_evidence.append("No test command or test result was observed.")
    if not diffs:
        missing_evidence.append("No file diff or patch summary was observed.")
    if not commands:
        missing_evidence.append("No shell command evidence was observed.")

    uncertainty_reasons = list(missing_evidence)
    if checks["passed_tests"]:
        uncertainty_reasons.append("Hidden tests or untested integration paths may still fail.")
    if checks["passed_tests"] and len(passed_tests) == 1:
        uncertainty_reasons.append("Only one passing test command was observed.")
    if not checks["build_lint_succeeded"]:
        uncertainty_reasons.append("No independent build/lint/syntax success evidence was observed.")
    if checks["suspicious_behaviors"]:
        uncertainty_reasons.append("Suspicious trajectory behavior reduces trust in observed outputs.")

    return checks, unsupported_claims, failure_modes, missing_evidence, risk_factors, uncertainty_reasons


def score_candidate(task: Task, candidate: Candidate, judge: Optional[SemanticJudge] = None, config: Optional[Dict[str, Any]] = None) -> CandidateAnalysis:
    config = config or DEFAULT_CONFIG
    evidence = extract_evidence(candidate, config)
    checks, unsupported_claims, failure_modes, missing_evidence, risk_factors, uncertainty_reasons = run_checks(task, candidate, evidence, config)
    score = 0.50

    if checks["passed_tests"]:
        score += 0.25
    if checks["has_meaningful_code_change"]:
        score += 0.15
    if checks["failed_tests"]:
        score -= 0.30
    if checks["nonzero_exits"]:
        score -= min(0.20, 0.05 * len(checks["nonzero_exits"]))
    if checks["build_lint_failures"]:
        score -= 0.20
    if checks["stack_traces"]:
        score -= 0.18
    if checks["missing_tests"]:
        score -= float(config.get("missing_tests_penalty", 0.12))
    if checks["made_no_meaningful_code_change"]:
        score -= 0.18
    if checks["tests_only_change"] and not _task_asks_for_test_changes(task.task_description):
        score -= float(config.get("tests_only_change_penalty", 0.15))
    if unsupported_claims:
        score -= 0.10
    if checks["suspiciously_small_diffs"]:
        score -= 0.05
    if checks["suspicious_behaviors"]:
        score -= min(0.20, 0.08 * len(checks["suspicious_behaviors"]))
    if checks["retry_loop"]:
        score -= 0.10

    judge = judge or SemanticJudge()
    semantic = judge.judge(task, candidate, evidence)
    score += max(-0.15, min(0.15, float(semantic.get("adjustment", 0.0))))
    score = max(0.0, min(1.0, round(score, 3)))

    objective_count = sum(1 for e in evidence if e.kind in ("test_result", "file_diff", "nonzero_exit", "build_lint_failure", "build_lint_success", "error", "stack_trace"))
    if objective_count >= 2 and not missing_evidence and not risk_factors:
        confidence = "high"
    elif objective_count >= 1 or len(missing_evidence) <= 1:
        confidence = "medium"
    else:
        confidence = "low"

    verdict = score_to_verdict(score)
    summary = summarize_candidate(candidate.candidate_id, score, checks, failure_modes, missing_evidence)
    analysis = CandidateAnalysis(candidate, evidence, checks, unsupported_claims, failure_modes, missing_evidence,
                                 risk_factors, uncertainty_reasons,
                                 score, confidence, verdict, summary)
    analysis.semantic_certificate = build_semantic_certificate(analysis)
    return analysis


def score_to_verdict(score: float) -> str:
    if score >= 0.90:
        return "strong_pass"
    if score >= 0.70:
        return "likely_pass"
    if score >= 0.50:
        return "mixed"
    if score >= 0.30:
        return "likely_fail"
    return "strong_fail"


def summarize_candidate(candidate_id: str, score: float, checks: Dict[str, Any], failure_modes: List[str], missing: List[str]) -> str:
    if failure_modes:
        return f"{candidate_id} scored {score:.2f}; main issues: {', '.join(failure_modes)}."
    if checks["passed_tests"] and checks["has_meaningful_code_change"]:
        return f"{candidate_id} scored {score:.2f}; observed meaningful changes and passing tests."
    return f"{candidate_id} scored {score:.2f}; evidence is incomplete or mixed."


def build_semantic_certificate(analysis: CandidateAnalysis) -> Dict[str, Any]:
    positive_ids = [e.evidence_id for e in analysis.evidence if e.direction == "positive"]
    negative_ids = [e.evidence_id for e in analysis.evidence if e.direction == "negative"]
    verified = []
    inferred = []
    unknown = list(analysis.uncertainty_reasons)
    if analysis.checks.get("passed_tests"):
        verified.append({"claim": "At least one collected test command passed.", "evidence_ids": positive_ids[:3]})
    if analysis.checks.get("has_meaningful_code_change"):
        verified.append({"claim": "The trajectory includes a meaningful source or artifact diff.", "evidence_ids": positive_ids[:3]})
    if analysis.failure_modes:
        verified.append({"claim": "Failure or risk signals were observed in the trajectory.", "evidence_ids": negative_ids[:5]})
    if analysis.score >= 0.70 and analysis.risk_factors:
        inferred.append({"claim": "Candidate may still be incomplete despite positive execution evidence.", "evidence_ids": positive_ids[:3] + negative_ids[:3]})
    if analysis.score < 0.70 and analysis.checks.get("passed_tests"):
        inferred.append({"claim": "Passing tests do not outweigh contradictory or suspicious evidence.", "evidence_ids": positive_ids[:3] + negative_ids[:3]})
    return {
        "verified": verified,
        "inferred": inferred,
        "unknown": unknown,
        "counterexamples_considered": [
            "Final assistant claim may be unsupported.",
            "Observed tests may not cover hidden or integration behavior.",
            "Patch may change tests or unrelated files instead of source behavior.",
        ],
        "semantic_adjustment": 0.0,
    }


def key_evidence(analysis: CandidateAnalysis) -> List[Dict[str, str]]:
    selected: List[EvidenceItem] = []
    priority = ("test_result", "build_lint_success", "file_diff", "artifact", "zero_exit", "build_lint_failure", "nonzero_exit", "error", "stack_trace", "suspicious_behavior", "success_claim")
    for kind in priority:
        selected.extend([e for e in analysis.evidence if e.kind == kind and e.direction != "negative"])
        if len(selected) >= 6:
            break
    return [{
        "evidence_id": e.evidence_id,
        "claim": e.summary,
        "supports": e.direction,
        "evidence_type": e.kind,
        "source_event_ids": e.event_ids,
    } for e in selected[:6]]


def negative_evidence(analysis: CandidateAnalysis) -> List[Dict[str, Any]]:
    selected = [e for e in analysis.evidence if e.direction == "negative"]
    return [{
        "evidence_id": e.evidence_id,
        "claim": e.summary,
        "supports": "negative",
        "evidence_type": e.kind,
        "source_event_ids": e.event_ids,
    } for e in selected[:8]]


def pairwise_close_cases(analyses: List[CandidateAnalysis], config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cases = []
    threshold = float((config or {}).get("close_case_threshold", 0.10))
    for i, a in enumerate(analyses):
        for b in analyses[i + 1:]:
            if abs(a.score - b.score) >= threshold:
                continue
            winner = tie_break(a, b)
            reason, evidence_ids = tie_reason(winner, b if winner is a else a)
            cases.append({
                "candidate_a": a.candidate.candidate_id,
                "candidate_b": b.candidate.candidate_id,
                "winner": winner.candidate.candidate_id,
                "reason": reason,
                "evidence_ids": evidence_ids,
            })
    return cases


def tie_break(a: CandidateAnalysis, b: CandidateAnalysis) -> CandidateAnalysis:
    weights = [
        ("passed_tests", 3),
        ("has_meaningful_code_change", 2),
        ("failed_tests", -3),
        ("build_lint_failures", -3),
        ("nonzero_exits", -1),
        ("tests_only_change", -2),
        ("made_no_meaningful_code_change", -2),
    ]

    def s(x: CandidateAnalysis) -> int:
        total = 0
        for key, weight in weights:
            val = x.checks.get(key)
            present = bool(val)
            total += weight if present else 0
        total -= len(x.missing_evidence)
        return total

    return a if (s(a), a.score, a.candidate.candidate_id) >= (s(b), b.score, b.candidate.candidate_id) else b


def tie_reason(winner: CandidateAnalysis, loser: CandidateAnalysis) -> Tuple[str, List[str]]:
    ids = [e["evidence_id"] for e in key_evidence(winner)[:3]]
    if winner.checks["passed_tests"] and not loser.checks["passed_tests"]:
        return "Close-score tie broken by observed passing tests.", ids
    if winner.checks["has_meaningful_code_change"] and loser.checks["made_no_meaningful_code_change"]:
        return "Close-score tie broken by meaningful code-change evidence.", ids
    return "Close-score tie broken by stronger deterministic evidence and fewer missing/negative signals.", ids


def verify(payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or load_config()
    task = parse_task(payload)
    analyses = [score_candidate(task, c, config=config) for c in task.candidates]
    close = pairwise_close_cases(analyses, config)
    winner_by_close = {(c["candidate_a"], c["candidate_b"]): c["winner"] for c in close}

    def rank_key(a: CandidateAnalysis) -> Tuple[float, int, str]:
        close_bonus = sum(1 for c in close if c["winner"] == a.candidate.candidate_id)
        return (a.score, close_bonus, a.candidate.candidate_id)

    ranked = sorted(analyses, key=rank_key, reverse=True)
    output_candidates = []
    for idx, analysis in enumerate(ranked, start=1):
        output_candidates.append({
            "candidate_id": analysis.candidate.candidate_id,
            "rank": idx,
            "score": analysis.score,
            "confidence": analysis.confidence,
            "verdict": analysis.verdict,
            "summary": analysis.summary,
            "key_evidence": key_evidence(analysis),
            "negative_evidence": negative_evidence(analysis),
            "unsupported_claims": analysis.unsupported_claims,
            "failure_modes": analysis.failure_modes,
            "missing_evidence": analysis.missing_evidence,
            "risk_factors": analysis.risk_factors,
            "uncertainty_reasons": analysis.uncertainty_reasons,
            "semantic_certificate": analysis.semantic_certificate,
        })
    return {
        "task_id": task.task_id,
        "config_profile": config.get("profile", "default"),
        "ranked_candidates": output_candidates,
        "pairwise_comparisons": close,
    }


def validate_citations(result: Dict[str, Any]) -> Tuple[int, int]:
    total = valid = 0
    known = set()
    for cand in result.get("ranked_candidates", []):
        for ev in cand.get("key_evidence", []) + cand.get("negative_evidence", []):
            known.add(ev.get("evidence_id"))
    for cand in result.get("ranked_candidates", []):
        for ev in cand.get("key_evidence", []) + cand.get("negative_evidence", []):
            total += 1
            if ev.get("evidence_id") in known and ev.get("claim") and ev.get("supports") in {"positive", "negative", "uncertainty"}:
                valid += 1
        for claim in cand.get("unsupported_claims", []):
            for eid in claim.get("related_evidence_ids", []):
                total += 1
                if eid:
                    valid += 1
    for case in result.get("pairwise_comparisons", []):
        for eid in case.get("evidence_ids", []):
            total += 1
            if eid:
                valid += 1
    return valid, total


def evaluate_dataset(payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or load_config()
    tasks = payload.get("tasks", payload if isinstance(payload, list) else [])
    if not isinstance(tasks, list):
        raise ValueError("labeled dataset must be a list or {'tasks': [...]}")

    top1_correct = pair_correct = total_pairs = false_pos = false_neg = positives = negatives = 0
    unsupported_success_cases = unsupported_success_detected = 0
    bins: Dict[str, Dict[str, float]] = {}
    citation_valid = citation_total = 0

    for item in tasks:
        labels = item.get("labels", {})
        success_claim_by_candidate = {}
        for raw_candidate in item.get("candidates", []):
            cid = raw_candidate.get("candidate_id")
            success_claim_by_candidate[cid] = any(
                event.get("event_type") in ("assistant_message", "final_answer")
                and SUCCESS_CLAIM_RE.search(str(event.get("content") or ""))
                for event in raw_candidate.get("trajectory", [])
            )
        result = verify(item, config)
        valid, total = validate_citations(result)
        citation_valid += valid
        citation_total += total
        ranked = result["ranked_candidates"]
        if ranked:
            top = ranked[0]["candidate_id"]
            if labels.get(top) in (1, True, "pass", "passed"):
                top1_correct += 1
        for cand in ranked:
            label = labels.get(cand["candidate_id"])
            passed = label in (1, True, "pass", "passed")
            predicted = cand["score"] >= 0.70
            if passed:
                positives += 1
                if not predicted:
                    false_neg += 1
            else:
                negatives += 1
                if predicted:
                    false_pos += 1
            if not passed and success_claim_by_candidate.get(cand["candidate_id"]):
                unsupported_success_cases += 1
                if cand.get("unsupported_claims"):
                    unsupported_success_detected += 1
            lo = math.floor(cand["score"] * 10) / 10
            key = f"{lo:.1f}-{min(lo + 0.1, 1.0):.1f}"
            bins.setdefault(key, {"count": 0, "avg_score": 0.0, "empirical_pass_rate": 0.0})
            bins[key]["count"] += 1
            bins[key]["avg_score"] += cand["score"]
            bins[key]["empirical_pass_rate"] += 1.0 if passed else 0.0

        for i, a in enumerate(ranked):
            for b in ranked[i + 1:]:
                la = labels.get(a["candidate_id"]) in (1, True, "pass", "passed")
                lb = labels.get(b["candidate_id"]) in (1, True, "pass", "passed")
                if la != lb:
                    total_pairs += 1
                    if la and not lb:
                        pair_correct += 1

    calibration_error = 0.0
    total_calibration_count = sum(row["count"] for row in bins.values())
    for row in bins.values():
        count = row["count"]
        row["avg_score"] = round(row["avg_score"] / count, 3)
        row["empirical_pass_rate"] = round(row["empirical_pass_rate"] / count, 3)
        if total_calibration_count:
            calibration_error += (count / total_calibration_count) * abs(row["avg_score"] - row["empirical_pass_rate"])

    n_tasks = len(tasks)
    return {
        "num_tasks": n_tasks,
        "top1_selection_accuracy": round(top1_correct / n_tasks, 3) if n_tasks else 0.0,
        "pairwise_ranking_accuracy": round(pair_correct / total_pairs, 3) if total_pairs else 0.0,
        "calibration_error": round(calibration_error, 3),
        "calibration_table": dict(sorted(bins.items())),
        "false_positive_rate": round(false_pos / negatives, 3) if negatives else 0.0,
        "false_negative_rate": round(false_neg / positives, 3) if positives else 0.0,
        "evidence_citation_validity_rate": round(citation_valid / citation_total, 3) if citation_total else 1.0,
        "unsupported_success_detection_rate": round(unsupported_success_detected / unsupported_success_cases, 3) if unsupported_success_cases else 1.0,
    }


def verify_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="verify-trajectories")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default=None, help="defaults to .verifier/verifier.toml in the current repo")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as f:
        payload = json.load(f)
    result = verify(payload.get("input", payload), load_config(args.config))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return 0


def evaluate_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluate-verifier")
    parser.add_argument("labeled_dataset")
    parser.add_argument("--out")
    parser.add_argument("--config", default=None, help="defaults to .verifier/verifier.toml in the current repo")
    args = parser.parse_args(argv)
    with open(args.labeled_dataset, encoding="utf-8") as f:
        payload = json.load(f)
    result = evaluate_dataset(payload, load_config(args.config))
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_record(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"trajectory file does not exist: {path}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def _save_record(path: str, payload: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _event_id(candidate: Dict[str, Any]) -> str:
    return f"e{len(candidate.setdefault('trajectory', [])) + 1}"


def _single_candidate(payload: Dict[str, Any], candidate_id: Optional[str] = None) -> Dict[str, Any]:
    candidates = payload.setdefault("candidates", [])
    if not candidates:
        if not candidate_id:
            candidate_id = "candidate"
        candidates.append({"candidate_id": candidate_id, "trajectory": []})
    if candidate_id:
        for candidate in candidates:
            if candidate.get("candidate_id") == candidate_id:
                return candidate
        candidate = {"candidate_id": candidate_id, "trajectory": []}
        candidates.append(candidate)
        return candidate
    return candidates[0]


def collect_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="verifier-collect")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="create a trajectory JSON file")
    init.add_argument("--task-id", required=True)
    init.add_argument("--task-description", required=True)
    init.add_argument("--candidate-id", required=True)
    init.add_argument("--out", default=".verifier/trajectory.json")
    init.add_argument("--config", default=".verifier/verifier.toml")
    init.add_argument("--profile", default="default", choices=sorted(BUILTIN_PROFILES.keys()))

    run = sub.add_parser("run", help="run a command and append stdout/stderr/exit code")
    run.add_argument("--file", default=".verifier/trajectory.json")
    run.add_argument("--candidate-id")
    run.add_argument("command", nargs=argparse.REMAINDER)

    diff = sub.add_parser("diff", help="append current git diff")
    diff.add_argument("--file", default=".verifier/trajectory.json")
    diff.add_argument("--candidate-id")
    diff.add_argument("--path", default=None, help="optional pathspec passed to git diff")

    final = sub.add_parser("final", help="append a final answer / success claim")
    final.add_argument("--file", default=".verifier/trajectory.json")
    final.add_argument("--candidate-id")
    final.add_argument("content")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        config_path = Path(args.config)
        if not config_path.exists():
            write_default_config(config_path, args.profile)
        payload = {
            "task_id": args.task_id,
            "task_description": args.task_description,
            "candidates": [{"candidate_id": args.candidate_id, "trajectory": []}],
        }
        _save_record(args.out, payload)
        return 0

    payload = _load_record(args.file)
    candidate = _single_candidate(payload, getattr(args, "candidate_id", None))

    if args.cmd == "run":
        if not args.command:
            raise SystemExit("verifier-collect run requires a command after --")
        command_parts = args.command[1:] if args.command and args.command[0] == "--" else args.command
        command = " ".join(command_parts)
        completed = subprocess.run(command_parts, text=True, capture_output=True, shell=False)
        candidate["trajectory"].append({
            "event_id": _event_id(candidate),
            "event_type": "command",
            "timestamp": _utc_now(),
            "content": "",
            "metadata": {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "file_path": None,
                "diff": None,
            },
        })
        _save_record(args.file, payload)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="")
        return completed.returncode

    if args.cmd == "diff":
        command = ["git", "diff", "--", args.path] if args.path else ["git", "diff"]
        completed = subprocess.run(command, text=True, capture_output=True, shell=False)
        candidate["trajectory"].append({
            "event_id": _event_id(candidate),
            "event_type": "file_diff",
            "timestamp": _utc_now(),
            "content": "",
            "metadata": {
                "command": " ".join(command),
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "file_path": args.path,
                "diff": completed.stdout,
            },
        })
        _save_record(args.file, payload)
        if completed.stderr:
            print(completed.stderr, end="")
        return completed.returncode

    if args.cmd == "final":
        candidate["trajectory"].append({
            "event_id": _event_id(candidate),
            "event_type": "final_answer",
            "timestamp": _utc_now(),
            "content": args.content,
            "metadata": {},
        })
        _save_record(args.file, payload)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(verify_cli())
