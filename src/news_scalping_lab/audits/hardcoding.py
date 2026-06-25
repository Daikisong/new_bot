"""Production source scan for domain hardcoding risks."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

TICKER_LITERAL_RE = re.compile(r"[\"'][0-9]{6}[\"']")
HANGUL_RE = re.compile(r"[가-힣]")
DOMAIN_TOKENS = {
    "beneficiary",
    "beneficiaries",
    "candidate",
    "candidates",
    "companies",
    "company",
    "event",
    "leader",
    "leaders",
    "policies",
    "policy",
    "region",
    "regions",
    "sector",
    "sectors",
    "stock",
    "stocks",
    "theme",
    "themes",
    "ticker",
    "tickers",
}
CONTAINER_TOKENS = {
    "allowlist",
    "blacklist",
    "catalog",
    "dict",
    "denylist",
    "list",
    "map",
    "mapping",
    "registry",
    "table",
    "whitelist",
}
COLLECTION_CONSTRUCTORS = {
    "defaultdict",
    "dict",
    "frozenset",
    "list",
    "mappingproxytype",
    "ordereddict",
    "set",
    "tuple",
}
MIN_SIX_DIGIT_TICKER = 100_000
MAX_SIX_DIGIT_TICKER = 999_999
SCORE_NAME_TOKENS = {"score", "scores", "scoring"}
SCORE_QUALIFIER_TOKENS = {
    "event",
    "expression",
    "fixed",
    "keyword",
    "mou",
    "news",
    "policy",
    "region",
    "sector",
    "static",
    "theme",
}
TEXT_FIELD_NAMES = {"article", "body", "content", "headline", "news", "snippet", "summary", "text", "title"}
TEXT_DOMAIN_COLLECTION_RE = re.compile(
    r"\b(?:theme|sector|region|policy|beneficiary|ticker|stock|candidate)"
    r"[a-z0-9_\- ]{0,32}"
    r"(?:map|mapping|list|whitelist|allowlist|table)\b\s*[:=]\s*(?:\[|\{)",
    re.IGNORECASE,
)
TEXT_HANGUL_COLLECTION_RE = re.compile(r"[\uac00-\ud7a3].*[:=]\s*(?:\[|\{)")
TEXT_POLICY_FILE_SUFFIXES = {".md", ".txt"}
SYMBOLIC_PLACEHOLDER_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
MIN_KNOWN_COMPANY_LITERAL_CHARS = 3
IGNORED_ENTITY_LITERAL_VALUES = {"n/a", "none", "null", "unknown", "미상", "없음"}


def audit_hardcoding(root: Path) -> dict[str, object]:
    src = root / "src" / "news_scalping_lab"
    findings: list[dict[str, object]] = []
    known_company_literals = _known_company_literals(root)
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if TICKER_LITERAL_RE.search(line):
                findings.append(_finding(root, path, line_number, "quoted_six_digit_ticker", line))
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            findings.append(
                _finding(root, path, exc.lineno or 1, "python_parse_error", _line_at(lines, exc.lineno or 1))
            )
            continue
        findings.extend(_ast_findings(root, path, tree, lines))
        findings.extend(
            _known_company_literal_findings(
                root,
                path,
                tree,
                lines,
                known_company_literals,
            )
        )
    for path in _iter_text_policy_files(root):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if TICKER_LITERAL_RE.search(line):
                findings.append(
                    _finding(root, path, line_number, "guidance_six_digit_ticker", line)
                )
            if TEXT_DOMAIN_COLLECTION_RE.search(line):
                findings.append(
                    _finding(root, path, line_number, "guidance_domain_collection", line)
                )
            if TEXT_HANGUL_COLLECTION_RE.search(line):
                findings.append(
                    _finding(
                        root,
                        path,
                        line_number,
                        "guidance_hangul_domain_collection",
                        line,
                    )
                )
    return {"passed": not findings, "findings": findings}


def _known_company_literals(root: Path) -> list[str]:
    values: set[str] = set()
    for payload in _iter_json_objects(root / "memory" / "company_memory"):
        _add_company_literal(values, payload.get("company_name"))
        for alias in _sequence_items(payload.get("aliases")):
            _add_company_literal(values, alias)
    for payload in _iter_json_objects(root / "research" / "accepted"):
        for candidate in _sequence_items(payload.get("blind_predictions")):
            if isinstance(candidate, dict):
                _add_company_literal(values, candidate.get("company_name"))
        for edge in _sequence_items(payload.get("event_ticker_edges")):
            if isinstance(edge, dict):
                _add_company_literal(values, edge.get("company_name"))
    return sorted(values, key=lambda value: (-len(value), value))


def _iter_json_objects(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _sequence_items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _add_company_literal(values: set[str], value: object) -> None:
    if not isinstance(value, str):
        return
    normalized = " ".join(value.split())
    if len(normalized) < MIN_KNOWN_COMPANY_LITERAL_CHARS:
        return
    if normalized.lower() in IGNORED_ENTITY_LITERAL_VALUES:
        return
    if SYMBOLIC_PLACEHOLDER_RE.fullmatch(normalized):
        return
    values.add(normalized)


def _known_company_literal_findings(
    root: Path,
    path: Path,
    tree: ast.AST,
    lines: list[str],
    known_company_literals: list[str],
) -> list[dict[str, object]]:
    if not known_company_literals:
        return []
    findings: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        matched = _matched_known_company_literal(node.value, known_company_literals)
        if matched is None:
            continue
        findings.append(
            _finding(
                root,
                path,
                node.lineno,
                "known_company_name_literal",
                _line_at(lines, node.lineno),
                match=matched,
            )
        )
    return findings


def _matched_known_company_literal(value: str, known_company_literals: list[str]) -> str | None:
    normalized = " ".join(value.split())
    for literal in known_company_literals:
        if literal in normalized:
            return literal
    return None


def _iter_text_policy_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in (root / "AGENTS.md",):
        if path.exists():
            candidates.append(path)
    for directory in (root / "prompts", root / ".agents" / "skills"):
        if not directory.exists():
            continue
        candidates.extend(
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.lower() in TEXT_POLICY_FILE_SUFFIXES
        )
    return sorted(candidates)


def _ast_findings(root: Path, path: Path, tree: ast.AST, lines: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            if value is None:
                continue
            for target in _assignment_targets(node):
                target_name = _target_name(target)
                if target_name and _is_forbidden_domain_assignment(target_name, value):
                    findings.append(
                        _finding(
                            root,
                            path,
                            node.lineno,
                            "domain_hardcoding_collection",
                            _line_at(lines, node.lineno),
                        )
                    )
                    break
                if target_name and _is_hangul_domain_collection(target_name, value):
                    findings.append(
                        _finding(
                            root,
                            path,
                            node.lineno,
                            "hangul_domain_literal_collection",
                            _line_at(lines, node.lineno),
                        )
                    )
                    break
                if target_name and _is_numeric_ticker_domain_collection(target_name, value):
                    findings.append(
                        _finding(
                            root,
                            path,
                            node.lineno,
                            "numeric_six_digit_ticker",
                            _line_at(lines, node.lineno),
                        )
                    )
                    break
        if isinstance(node, ast.If) and _has_literal_condition_scoring(node):
            findings.append(
                _finding(root, path, node.lineno, "fixed_expression_score", _line_at(lines, node.lineno))
            )
    return findings


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    return [node.target]


def _target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_forbidden_domain_assignment(name: str, value: ast.AST) -> bool:
    tokens = set(_identifier_tokens(name))
    if _is_score_table_name(tokens):
        return True
    if not _is_collection_expression(value):
        return False
    if tokens & DOMAIN_TOKENS and tokens & CONTAINER_TOKENS:
        return True
    domain_token_count = len(tokens & DOMAIN_TOKENS)
    return domain_token_count >= 2 and "to" in tokens


def _is_hangul_domain_collection(name: str, value: ast.AST) -> bool:
    tokens = set(_identifier_tokens(name))
    if not _is_collection_expression(value) or not tokens & DOMAIN_TOKENS:
        return False
    return _contains_hangul_domain_literal(value)


def _contains_hangul_domain_literal(value: ast.AST) -> bool:
    for child in ast.walk(value):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        if HANGUL_RE.search(child.value) and "가-힣" not in child.value:
            return True
    return False


def _is_score_table_name(tokens: set[str]) -> bool:
    return bool(tokens & SCORE_NAME_TOKENS and tokens & SCORE_QUALIFIER_TOKENS)


def _is_numeric_ticker_domain_collection(name: str, value: ast.AST) -> bool:
    tokens = set(_identifier_tokens(name))
    return bool(
        tokens & DOMAIN_TOKENS
        and _is_collection_expression(value)
        and _contains_numeric_ticker_literal(value)
    )


def _contains_numeric_ticker_literal(value: ast.AST) -> bool:
    for child in ast.walk(value):
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, int)
            and not isinstance(child.value, bool)
            and MIN_SIX_DIGIT_TICKER <= child.value <= MAX_SIX_DIGIT_TICKER
        ):
            return True
    return False


def _is_collection_expression(value: ast.AST) -> bool:
    if isinstance(value, ast.Dict | ast.List | ast.Tuple | ast.Set):
        return True
    if isinstance(value, ast.Call):
        call_name = _call_name(value.func)
        return call_name is not None and call_name.lower() in COLLECTION_CONSTRUCTORS
    return False


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _identifier_tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def _has_literal_condition_scoring(node: ast.If) -> bool:
    return _test_contains_string_trigger(node.test) and any(_contains_score_mutation(child) for child in node.body)


def _test_contains_string_trigger(test: ast.AST) -> bool:
    has_string_literal = any(
        isinstance(child, ast.Constant) and isinstance(child.value, str) and len(child.value.strip()) >= 2
        for child in ast.walk(test)
    )
    if not has_string_literal:
        return False
    referenced_names = {child.id.lower() for child in ast.walk(test) if isinstance(child, ast.Name)}
    return bool(referenced_names & TEXT_FIELD_NAMES) or any(name.endswith("_type") for name in referenced_names)


def _contains_score_mutation(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.AugAssign) and _target_mentions_score(child.target):
            return True
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
            if any(_target_mentions_score(target) for target in targets):
                return True
    return False


def _target_mentions_score(target: ast.AST) -> bool:
    if isinstance(target, ast.Name):
        return target.id.lower() in SCORE_NAME_TOKENS
    if isinstance(target, ast.Attribute):
        return target.attr.lower() in SCORE_NAME_TOKENS
    if isinstance(target, ast.Subscript):
        return _target_mentions_score(target.value)
    return False


def _finding(
    root: Path,
    path: Path,
    line_number: int,
    rule: str,
    line: str,
    *,
    match: str | None = None,
) -> dict[str, object]:
    finding: dict[str, object] = {
        "file": path.relative_to(root).as_posix(),
        "line": line_number,
        "rule": rule,
        "text": line.strip(),
    }
    if match is not None:
        finding["match"] = match
    return finding


def _line_at(lines: list[str], line_number: int) -> str:
    index = line_number - 1
    if 0 <= index < len(lines):
        return lines[index]
    return ""
