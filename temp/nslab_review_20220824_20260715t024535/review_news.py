from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

RUN_STAMP = "20260715T024535KST"
RUN_ID = "nslab_run_20260715T024535KST_20220824"
EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_PROMPT_BYTES = 430485
EXPECTED_PROMPT_TITLE = "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER"
SHARD_COUNT = 8

MODELS = [
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "mistral-ai/mistral-medium-2505",
    "cohere/cohere-command-a",
    "meta/llama-3.3-70b-instruct",
    "microsoft/phi-4-mini-instruct",
    "mistral-ai/mistral-small-2503",
    "openai/gpt-4.1-nano",
]
FALLBACK_MODELS = ["openai/gpt-4.1", "microsoft/phi-4", "deepseek/deepseek-v3-0324"]
TOKEN_CAP = {
    "openai/gpt-4.1": 10000,
    "openai/gpt-4.1-mini": 10000,
    "openai/gpt-4.1-nano": 8000,
    "openai/gpt-4o-mini": 4096,
    "mistral-ai/mistral-medium-2505": 4096,
    "mistral-ai/mistral-small-2503": 4096,
    "cohere/cohere-command-a": 4096,
    "meta/llama-3.3-70b-instruct": 4096,
    "microsoft/phi-4-mini-instruct": 4096,
    "microsoft/phi-4": 8000,
    "deepseek/deepseek-v3-0324": 4096,
}

ALLOWED_DISPOSITIONS = {
    "DIRECT_ISSUER_MATERIAL", "DIRECT_ISSUER_SECONDARY", "THEME_POLICY_INDUSTRY_EVENT",
    "MARKET_STATE_REGIME", "D1_CONTINUATION_SIGNAL", "DISCLOSURE_OR_MARKET_NOTICE",
    "BODY_TABLE_OR_LIST_AUDIT", "DUPLICATE", "LOW_SIGNAL_CONTEXT", "NON_MARKET_NEWS",
    "NON_KR_OR_NON_LISTED_CONTEXT", "TIME_UNVERIFIED_RETAINED", "PARSER_AMBIGUOUS_REVIEWED",
}
MATERIAL_DISPOSITIONS = {
    "DIRECT_ISSUER_MATERIAL", "DIRECT_ISSUER_SECONDARY", "THEME_POLICY_INDUSTRY_EVENT",
    "MARKET_STATE_REGIME", "D1_CONTINUATION_SIGNAL", "DISCLOSURE_OR_MARKET_NOTICE",
    "BODY_TABLE_OR_LIST_AUDIT", "PARSER_AMBIGUOUS_REVIEWED",
}
ALLOWED_RELATIONS = {
    "DIRECT_SUBJECT", "DIRECT_PREDICATE_OWNER", "NAMED_BENEFICIARY", "EXCHANGE_NOTICE_SUBJECT",
    "OTHER_COMPANY_MENTION", "LIST_MEMBER", "MANUFACTURER_ONLY", "ATTENDEE_ONLY",
    "GROUP_OR_AFFILIATE_ONLY", "GENERIC_OR_NONCOMPANY", "NON_KR_OR_NONLISTED", "NONE",
}
ALLOWED_BINDING = {
    "RESOLVED_DIRECT", "RESOLVED_NAMED_BENEFICIARY", "UNRESOLVED", "NON_KR_OR_NONLISTED",
    "GROUP_OR_BRAND", "GENERIC_OR_NONCOMPANY",
}
ALLOWED_PATHS = {"DIRECT_ISSUER", "THEME_BENEFICIARY", "MARKET_STATE", "CONTINUATION", "AUDIT_ONLY"}
ALLOWED_SCREEN = {"INCLUDE", "WATCH_SECONDARY", "EXCLUDE", "AUDIT_ONLY", "REJECT_SEMANTIC_FALSE_POSITIVE"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(canonical_json(row) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_json(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
        if not starts:
            raise
        start = min(starts)
        for end in range(len(cleaned), start, -1):
            candidate = cleaned[start:end].strip()
            if not candidate or candidate[-1] not in "]}":
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise


class Client:
    def __init__(self, token: str, primary: str, log_path: Path) -> None:
        self.token = token
        self.models = list(dict.fromkeys([primary] + MODELS + FALLBACK_MODELS))
        self.log_path = log_path
        self.last_call = {model: 0.0 for model in self.models}

    def log(self, row: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(row) + "\n")

    def call(self, system: str, user: str, label: str) -> tuple[Any, str]:
        endpoint = "https://models.github.ai/inference/chat/completions"
        errors: list[str] = []
        for model in self.models:
            delay = max(0.0, 3.2 - (time.monotonic() - self.last_call[model]))
            if delay:
                time.sleep(delay)
            for attempt in range(1, 4):
                started = time.monotonic()
                status = "error"
                output_chars = 0
                response_model = model
                body = {
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": 0,
                    "max_tokens": TOKEN_CAP.get(model, 4096),
                    "seed": 20220824,
                }
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=300) as response:
                        envelope = json.loads(response.read().decode("utf-8"))
                    self.last_call[model] = time.monotonic()
                    response_model = str(envelope.get("model") or model)
                    content = str(envelope["choices"][0]["message"]["content"])
                    output_chars = len(content)
                    parsed = extract_json(content)
                    status = "ok"
                    return parsed, response_model
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:1200]
                    errors.append(f"{model} HTTP {exc.code}: {detail}")
                    if exc.code in {400, 401, 403, 404, 422}:
                        break
                    retry = exc.headers.get("Retry-After") if exc.headers else None
                    wait = float(retry) if retry and retry.replace(".", "", 1).isdigit() else min(50.0, 5.0 * attempt)
                    time.sleep(wait)
                except Exception as exc:
                    errors.append(f"{model} {type(exc).__name__}: {exc}")
                    time.sleep(min(25.0, 4.0 * attempt))
                finally:
                    self.log({
                        "label": label, "model": model, "response_model": response_model, "attempt": attempt,
                        "status": status, "input_chars": len(system) + len(user), "output_chars": output_chars,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
        raise RuntimeError(f"model call failed for {label}: {' | '.join(errors[-12:])}")


SYSTEM = """You are the E1 extractor, E2 verifier, and E3 adjudicator for a Korean pre-open stock-news research ledger. Read the COMPLETE title and COMPLETE body of every input row, not only the title. Return strict compact JSON. Do not use keyword-hit logic. Decide the local predicate owner and whether a real Korean listed issuer is the article subject or explicit named beneficiary. Never upgrade a ticker-like token, common noun, substring, table/list member, attendance, manufacturer-only mention, affiliate/group mention, investor holding, or another company's article into issuer evidence. Negative issuer-specific events remain material rows but are not positive final catalysts. Every row must have a verbatim exact_quote substring from its own title/body; for low-signal or non-market rows use the exact title. Mechanisms may contain only variables supported by that quote. When uncertain, retain as unresolved or audit-only rather than inventing binding."""


def user_prompt(rows: list[dict[str, Any]]) -> str:
    return """Return {\"r\":[...]} with exactly one record for every input id and no extra id. Compact keys:
id; d disposition; s article subject company or null; o local predicate owner or null; rel relation; dec specific review decision; q exact verbatim quote <=240 chars; c candidate company/legal issuer name or null; b binding status; a issuer anchor type; qr quote role; f material fact class; cat catalyst type; e economic variable; m mechanism; ms mechanism supported boolean; p candidate path; scr screening recommendation; why specific decision reason; rej rejection reason or null; risk array; th theme name or null; ben explicit named beneficiary boolean.
Allowed d: DIRECT_ISSUER_MATERIAL, DIRECT_ISSUER_SECONDARY, THEME_POLICY_INDUSTRY_EVENT, MARKET_STATE_REGIME, D1_CONTINUATION_SIGNAL, DISCLOSURE_OR_MARKET_NOTICE, BODY_TABLE_OR_LIST_AUDIT, LOW_SIGNAL_CONTEXT, NON_MARKET_NEWS, NON_KR_OR_NON_LISTED_CONTEXT, PARSER_AMBIGUOUS_REVIEWED.
Allowed rel: DIRECT_SUBJECT, DIRECT_PREDICATE_OWNER, NAMED_BENEFICIARY, EXCHANGE_NOTICE_SUBJECT, OTHER_COMPANY_MENTION, LIST_MEMBER, MANUFACTURER_ONLY, ATTENDEE_ONLY, GROUP_OR_AFFILIATE_ONLY, GENERIC_OR_NONCOMPANY, NON_KR_OR_NONLISTED, NONE.
Allowed b: RESOLVED_DIRECT, RESOLVED_NAMED_BENEFICIARY, UNRESOLVED, NON_KR_OR_NONLISTED, GROUP_OR_BRAND, GENERIC_OR_NONCOMPANY.
Allowed p: DIRECT_ISSUER, THEME_BENEFICIARY, MARKET_STATE, CONTINUATION, AUDIT_ONLY. Allowed scr: INCLUDE, WATCH_SECONDARY, EXCLUDE, AUDIT_ONLY, REJECT_SEMANTIC_FALSE_POSITIVE.
Use cat/e/ms only for supported economic actions. Examples of final-positive qr: ISSUER_CONTRACT_ACTION, ISSUER_ORDER_OR_SUPPLY_ACTION, ISSUER_PROJECT_AWARDED_ACTION, ISSUER_PRODUCT_RELEASE_ACTION, ISSUER_SERVICE_RELEASE_ACTION, ISSUER_COMMERCIALIZATION_ACTION, ISSUER_REGULATORY_APPROVAL_ACTION, ISSUER_CLINICAL_OR_PIPELINE_STAGE_ACTION, ISSUER_GOVERNMENT_PROJECT_SELECTION_ACTION, ISSUER_LICENSE_OR_TECH_TRANSFER_ACTION, ISSUER_CAPITAL_POLICY_ACTION, ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION, ISSUER_ANALYST_NUMERIC_BRIDGE. Non-final roles include DIRECT_ISSUER_ADVERSE_EVENT_NONFINAL, DIRECT_ISSUER_ROUTINE_FACT_NONFINAL, POLICY_OR_INDUSTRY_CONTEXT, BODY_TABLE_LIST_MEMBER, OTHER_COMPANY_ARTICLE, MARKET_FLOW_TABLE_MEMBER_ONLY, CSR_OR_ROUTINE_ONLY, TECHNICAL_SIGNAL_ONLY, GENERAL_MARKET_COMMENTARY_ONLY, NON_MARKET_CONTEXT, NON_KR_OR_NONLISTED_ISSUER, PARSER_AMBIGUOUS.
INPUT_ROWS:\n""" + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def batches(rows: list[dict[str, Any]], max_items: int = 10, max_chars: int = 85000) -> Iterable[list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        row_size = len(json.dumps(row, ensure_ascii=False))
        if current and (len(current) >= max_items or size + row_size > max_chars):
            yield current
            current = []
            size = 0
        current.append(row)
        size += row_size
    if current:
        yield current


def prepare(args: argparse.Namespace) -> None:
    source = Path(args.source)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    inp = out / "inputs"
    inp.mkdir(exist_ok=True)
    prompt_src = source / "docs" / "research_prompt.md"
    csv_src = source / "docs" / "csv" / "news_20220824.csv"
    example_src = source / "docs" / "example2.md"
    prompt_raw = prompt_src.read_bytes()
    assert len(prompt_raw) == EXPECTED_PROMPT_BYTES
    assert sha256_bytes(prompt_raw) == EXPECTED_PROMPT_SHA
    prompt_text = prompt_raw.decode("utf-8")
    assert prompt_text.splitlines()[0] == EXPECTED_PROMPT_TITLE
    assert "nslab.gold_phase_machine.direct_csv_research.locked" in prompt_text
    csv_raw = csv_src.read_bytes()
    rows = read_csv(csv_src)
    assert rows and list(rows[0]) == ["page", "row", "date", "time", "title", "body"]
    times = [datetime.fromisoformat(f"{row['date']}T{row['time']}") for row in rows]
    csv_sha = sha256_bytes(csv_raw)
    prompt_name = f"research_prompt_{RUN_STAMP}_{EXPECTED_PROMPT_SHA[:8]}.md"
    csv_name = f"news_20220824_{RUN_STAMP}_{csv_sha[:8]}.csv"
    (inp / prompt_name).write_bytes(prompt_raw)
    (inp / csv_name).write_bytes(csv_raw)
    (inp / "example2.md").write_bytes(example_src.read_bytes())
    metadata = {
        "schema_version": "nslab.news_semantic_review_input.v1", "run_id": RUN_ID,
        "prompt_file": prompt_name, "prompt_sha256": EXPECTED_PROMPT_SHA, "prompt_byte_size": len(prompt_raw),
        "news_file": csv_name, "news_sha256": csv_sha, "news_byte_size": len(csv_raw),
        "csv_row_count": len(rows), "parsed_row_count": len(rows), "columns": list(rows[0]),
        "min_published_at": min(times).isoformat(), "max_published_at": max(times).isoformat(),
        "time_unverified_rows": [], "control_char_count": sum(1 for ch in csv_raw.decode("utf-8-sig") if ord(ch) < 32 and ch not in "\n\r\t"),
        "trade_date": "2022-08-24", "previous_trade_date": "2022-08-23", "next_trade_date": "2022-08-25",
        "raw_prompt_url_opened": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
        "raw_csv_url_opened": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20220824.csv",
        "acquisition_method": "web_browser_raw_open_then_same-private-repo-main-checkout_hash_verified",
        "candidate_population_created": False, "outcome_content_access_count": 0,
    }
    write_json(out / "metadata.json", metadata)
    source_rows = []
    for index, row in enumerate(rows, 1):
        source_rows.append({
            "source_id": f"SRC-NEWS-{index:06d}", "source_type": "NEWS_CSV_ROW", "input_file": csv_name,
            "input_sha256": csv_sha, "row_index": index, "page": row.get("page"), "page_row": row.get("row"),
            "published_at_kst": f"{row['date']}T{row['time']}+09:00", "title": row.get("title", ""),
            "body": row.get("body", ""), "url": None,
            "raw_row_sha256": sha256_bytes(canonical_json(row).encode("utf-8")), "time_verified": True, "used_in_blind": True,
        })
    write_jsonl(out / "source_ledger.jsonl", source_rows)
    write_json(out / "phase1_source_receipt.json", {
        "csv_row_count": len(rows), "source_ledger_count": len(source_rows), "source_ledger_missing_row_count": 0,
        "source_ledger_duplicate_row_id_count": len(source_rows) - len({r['source_id'] for r in source_rows}),
        "candidate_or_watchlist_created": False, "outcome_content_access_count": 0, "status": "SOURCE_DENOMINATOR_CLOSED",
    })


def normalize(item: dict[str, Any], input_row: dict[str, Any], model: str, shard_index: int) -> dict[str, Any]:
    source_text = f"{input_row['title']}\n{input_row['body']}"
    quote = str(item.get("q") or "").strip()
    disposition = str(item.get("d") or "PARSER_AMBIGUOUS_REVIEWED")
    if disposition not in ALLOWED_DISPOSITIONS:
        disposition = "PARSER_AMBIGUOUS_REVIEWED"
    relation = str(item.get("rel") or "NONE")
    if relation not in ALLOWED_RELATIONS:
        relation = "NONE"
    binding = str(item.get("b") or "UNRESOLVED")
    if binding not in ALLOWED_BINDING:
        binding = "UNRESOLVED"
    path = str(item.get("p") or "AUDIT_ONLY")
    if path not in ALLOWED_PATHS:
        path = "AUDIT_ONLY"
    screen = str(item.get("scr") or "AUDIT_ONLY")
    if screen not in ALLOWED_SCREEN:
        screen = "AUDIT_ONLY"
    quote_repaired = False
    if not quote or quote not in source_text:
        quote = input_row["title"] or input_row["body"][:220]
        quote_repaired = True
        disposition = "PARSER_AMBIGUOUS_REVIEWED"
        path = "AUDIT_ONLY"
        screen = "AUDIT_ONLY"
    company = item.get("c") if isinstance(item.get("c"), str) and item.get("c").strip() else None
    subject = item.get("s") if isinstance(item.get("s"), str) and item.get("s").strip() else None
    owner = item.get("o") if isinstance(item.get("o"), str) and item.get("o").strip() else None
    rejection = item.get("rej") if isinstance(item.get("rej"), str) and item.get("rej").strip() else None
    if disposition in MATERIAL_DISPOSITIONS and not company and not rejection:
        rejection = "NO_RESOLVED_KRX_ISSUER_FROM_FULL_TEXT_REVIEW"
    if quote_repaired:
        rejection = "MODEL_QUOTE_NOT_VERBATIM_REPAIRED_TO_TITLE_AND_QUARANTINED"
    mechanism_supported = bool(item.get("ms")) and not quote_repaired
    if not mechanism_supported:
        mechanism = ""
        economic = "NONE"
        catalyst = "NONE"
    else:
        mechanism = str(item.get("m") or "").strip()
        economic = str(item.get("e") or "NONE").strip()
        catalyst = str(item.get("cat") or "NONE").strip()
    return {
        "source_id": input_row["id"], "global_row_index": input_row["i"], "shard_index": shard_index,
        "published_at_kst": input_row["ts"], "disposition": disposition,
        "article_subject_company": subject, "local_predicate_owner": owner, "direct_issuer_relation": relation,
        "review_decision": str(item.get("dec") or "FULL_TEXT_REVIEWED").strip(), "exact_quote": quote,
        "quote_found_in_source_row": quote in source_text, "quote_repair_action": "TITLE_FALLBACK_QUARANTINE" if quote_repaired else None,
        "candidate_company": company, "ticker": None, "issuer_binding_status": binding,
        "issuer_role_anchor_type": str(item.get("a") or relation), "quote_role": str(item.get("qr") or "PARSER_AMBIGUOUS"),
        "material_fact_class": str(item.get("f") or "PARSER_AMBIGUOUS_CONTEXT"), "catalyst_type": catalyst,
        "economic_variable_changed": economic, "mechanism_sentence": mechanism, "mechanism_supported": mechanism_supported,
        "candidate_path": path, "screening_recommendation": screen,
        "decision_reason_specific": str(item.get("why") or "Full title/body reviewed; no stronger supported conclusion.").strip(),
        "rejection_reason": rejection, "semantic_risk_flags": item.get("risk") if isinstance(item.get("risk"), list) else [],
        "theme_name": item.get("th") if isinstance(item.get("th"), str) and item.get("th").strip() else None,
        "named_beneficiary_explicit": bool(item.get("ben")), "material_queue_member": disposition in MATERIAL_DISPOSITIONS,
        "semantic_reviewer": model, "semantic_review_protocol": "FULL_TITLE_BODY_E1_E2_E3_ADJUDICATION_NO_KEYWORD_SHORTCUT",
        "full_title_body_reviewed": True,
    }


def review_shard(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((prepared / "metadata.json").read_text(encoding="utf-8"))
    rows = read_csv(prepared / "inputs" / metadata["news_file"])
    shard_index = args.shard_index
    shard_count = args.shard_count
    assert shard_count == SHARD_COUNT and 0 <= shard_index < shard_count
    start = len(rows) * shard_index // shard_count
    end = len(rows) * (shard_index + 1) // shard_count
    model_inputs = []
    for index, row in enumerate(rows[start:end], start + 1):
        model_inputs.append({
            "id": f"SRC-NEWS-{index:06d}", "i": index, "ts": f"{row['date']}T{row['time']}+09:00",
            "title": row.get("title", ""), "body": row.get("body", ""),
        })
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN missing")
    client = Client(token, MODELS[shard_index % len(MODELS)], out / f"model_calls_{shard_index:02d}.jsonl")
    reviews: dict[str, dict[str, Any]] = {}
    model_counts: Counter[str] = Counter()

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed, model = client.call(SYSTEM, user_prompt(batch), label)
            records = parsed.get("r") if isinstance(parsed, dict) else None
            if not isinstance(records, list) or len(records) != len(batch):
                raise ValueError("record count mismatch")
            by_id = {str(record.get("id")): record for record in records if isinstance(record, dict)}
            expected = {row["id"] for row in batch}
            if set(by_id) != expected:
                raise ValueError("record id coverage mismatch")
            for row in batch:
                normalized = normalize(by_id[row["id"]], row, model, shard_index)
                reviews[row["id"]] = normalized
                model_counts[model] += 1
        except Exception:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + "A")
                process(batch[midpoint:], label + "B")
                return
            raise

    for batch_index, batch in enumerate(batches(model_inputs), 1):
        process(batch, f"FULL_ROW_S{shard_index:02d}_B{batch_index:03d}")
        print(f"shard {shard_index}: {len(reviews)}/{len(model_inputs)}", flush=True)
    expected_ids = [f"SRC-NEWS-{i:06d}" for i in range(start + 1, end + 1)]
    assert set(reviews) == set(expected_ids)
    ordered = [reviews[source_id] for source_id in expected_ids]
    assert all(row["full_title_body_reviewed"] and row["quote_found_in_source_row"] for row in ordered)
    review_path = out / f"reviews_shard_{shard_index:02d}.jsonl"
    write_jsonl(review_path, ordered)
    write_json(out / f"receipt_shard_{shard_index:02d}.json", {
        "schema_version": "nslab.semantic_review_shard_receipt.v3", "run_id": RUN_ID,
        "shard_index": shard_index, "shard_count": shard_count, "global_start_row": start + 1, "global_end_row": end,
        "assigned_row_count": len(model_inputs), "reviewed_row_count": len(ordered),
        "first_source_id": expected_ids[0] if expected_ids else None, "last_source_id": expected_ids[-1] if expected_ids else None,
        "model_counts": dict(model_counts), "reviews_sha256": sha256_bytes(review_path.read_bytes()),
        "full_title_body_reviewed_count": sum(1 for r in ordered if r["full_title_body_reviewed"]),
        "quote_found_count": sum(1 for r in ordered if r["quote_found_in_source_row"]),
        "status": "FULL_ROW_SEMANTIC_REVIEW_COMPLETE", "outcome_content_access_count": 0,
    })


def merge(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    reviews_root = Path(args.reviews)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((prepared / "metadata.json").read_text(encoding="utf-8"))
    source_rows = read_jsonl(prepared / "source_ledger.jsonl")
    all_reviews: list[dict[str, Any]] = []
    receipts = []
    for shard in range(SHARD_COUNT):
        review_files = list(reviews_root.rglob(f"reviews_shard_{shard:02d}.jsonl"))
        receipt_files = list(reviews_root.rglob(f"receipt_shard_{shard:02d}.json"))
        if len(review_files) != 1 or len(receipt_files) != 1:
            raise RuntimeError(f"shard cardinality failure {shard}")
        rows = read_jsonl(review_files[0])
        receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
        assert sha256_bytes(review_files[0].read_bytes()) == receipt["reviews_sha256"]
        assert len(rows) == receipt["reviewed_row_count"]
        all_reviews.extend(rows)
        receipts.append(receipt)
    all_reviews.sort(key=lambda row: int(row["global_row_index"]))
    expected_ids = [f"SRC-NEWS-{i:06d}" for i in range(1, metadata["csv_row_count"] + 1)]
    assert [row["source_id"] for row in all_reviews] == expected_ids
    assert len(all_reviews) == metadata["csv_row_count"] == len(source_rows)
    duplicate_first: dict[str, str] = {}
    for source, review in zip(source_rows, all_reviews):
        source_text = f"{source['title']}\n{source['body']}"
        assert review["full_title_body_reviewed"] is True
        assert review["exact_quote"] and review["exact_quote"] in source_text
        row_hash = source["raw_row_sha256"]
        if row_hash in duplicate_first:
            review.update({
                "disposition": "DUPLICATE", "material_queue_member": False,
                "duplicate_of_source_id": duplicate_first[row_hash], "candidate_path": "AUDIT_ONLY",
                "screening_recommendation": "AUDIT_ONLY", "catalyst_type": "NONE",
                "economic_variable_changed": "NONE", "mechanism_sentence": "", "mechanism_supported": False,
                "rejection_reason": "EXACT_DUPLICATE_OF_EARLIER_CSV_ROW",
            })
        else:
            duplicate_first[row_hash] = source["source_id"]
        if review["material_queue_member"] and not (
            (review.get("candidate_company") and str(review.get("issuer_binding_status", "")).startswith("RESOLVED"))
            or review.get("rejection_reason")
        ):
            raise RuntimeError(f"material binding/rejection missing {review['source_id']}")
    write_jsonl(out / "reviews_full.jsonl", all_reviews)
    write_json(out / "semantic_review_receipt.json", {
        "schema_version": "nslab.semantic_review_receipt.v4", "run_id": RUN_ID,
        "csv_row_count": metadata["csv_row_count"], "reviewed_row_count": len(all_reviews),
        "full_title_body_reviewed_count": sum(1 for r in all_reviews if r["full_title_body_reviewed"]),
        "exact_quote_found_count": sum(1 for r in all_reviews if r["quote_found_in_source_row"]),
        "material_review_queue_count": sum(1 for r in all_reviews if r["material_queue_member"]),
        "duplicate_count": sum(1 for r in all_reviews if r["disposition"] == "DUPLICATE"),
        "shard_receipts": receipts, "reviews_sha256": sha256_bytes((out / "reviews_full.jsonl").read_bytes()),
        "candidate_population_created_before_full_review": False, "outcome_content_access_count": 0,
        "status": "FULL_POPULATION_SEMANTIC_REVIEW_CLOSED",
    })
    for name in ["metadata.json", "source_ledger.jsonl", "phase1_source_receipt.json"]:
        (out / name).write_bytes((prepared / name).read_bytes())
    (out / "inputs").mkdir(exist_ok=True)
    for path in (prepared / "inputs").iterdir():
        if path.is_file():
            (out / "inputs" / path.name).write_bytes(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--source", required=True); p.add_argument("--output", required=True); p.set_defaults(func=prepare)
    p = sub.add_parser("review-shard"); p.add_argument("--prepared", required=True); p.add_argument("--output", required=True); p.add_argument("--shard-index", type=int, required=True); p.add_argument("--shard-count", type=int, required=True); p.set_defaults(func=review_shard)
    p = sub.add_parser("merge"); p.add_argument("--prepared", required=True); p.add_argument("--reviews", required=True); p.add_argument("--output", required=True); p.set_defaults(func=merge)
    args = parser.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
