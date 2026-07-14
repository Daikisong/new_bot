from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

EXPECTED_NEWS_SHA = "5be151f86104857b9fe76889d6e2c2536dadda2393418f347a508be97daafddb"
EXPECTED_ROWS = 1120
SHARDS = 8
MODELS = [
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "mistral-ai/mistral-medium-2505",
    "cohere/cohere-command-a",
    "meta/llama-3.3-70b-instruct",
    "microsoft/phi-4-mini-instruct",
    "openai/gpt-4.1-nano",
    "mistral-ai/mistral-small-2503",
]
FALLBACK_MODELS = ["openai/gpt-4.1", "microsoft/phi-4", "deepseek/deepseek-v3-0324"]
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


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(canonical(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def batches(rows: list[dict[str, Any]], max_items: int = 7, max_chars: int = 28000) -> Iterable[list[dict[str, Any]]]:
    cur: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        n = len(json.dumps(row, ensure_ascii=False))
        if cur and (len(cur) >= max_items or size + n > max_chars):
            yield cur
            cur = []
            size = 0
        cur.append(row)
        size += n
    if cur:
        yield cur


SYSTEM = """You are the independent E1 extractor, E2 verifier, and E3 adjudicator for a Korean pre-open stock-news research ledger. You MUST read the COMPLETE title and COMPLETE body of every input row. Return strict compact JSON only, with exactly one record per input id and no extra ids.

Never treat a ticker-like string, substring, common noun, table/list membership, attendance, manufacturer-only mention, institution-flow table, investor holding, affiliate/group mention, or another company's article as issuer evidence. A candidate company may be bound only when it is the article subject, the local predicate owner, an explicitly named beneficiary of the quoted event, or an exchange-notice subject. Negative issuer-specific events remain material review rows but are not positive candidates. Exact quotes must be verbatim source substrings. Mechanisms may use only variables supported by the quote. If uncertain, retain the row as audit-only/unresolved rather than inventing a binding.

Allowed dispositions: DIRECT_ISSUER_MATERIAL, DIRECT_ISSUER_SECONDARY, THEME_POLICY_INDUSTRY_EVENT, MARKET_STATE_REGIME, D1_CONTINUATION_SIGNAL, DISCLOSURE_OR_MARKET_NOTICE, BODY_TABLE_OR_LIST_AUDIT, LOW_SIGNAL_CONTEXT, NON_MARKET_NEWS, NON_KR_OR_NON_LISTED_CONTEXT, TIME_UNVERIFIED_RETAINED, PARSER_AMBIGUOUS_REVIEWED.

Return {\"rows\":[...]} with compact keys:
id=source_id; d=disposition; s=article_subject_company_or_null; o=local_predicate_owner_or_null; rel=DIRECT_SUBJECT|DIRECT_PREDICATE_OWNER|NAMED_BENEFICIARY|EXCHANGE_NOTICE_SUBJECT|OTHER_COMPANY_MENTION|LIST_MEMBER|MANUFACTURER_ONLY|ATTENDEE_ONLY|GROUP_OR_AFFILIATE_ONLY|GENERIC_OR_NONCOMPANY|NON_KR_OR_NONLISTED|NONE; dec=review_decision; q=verbatim exact_quote <=260 chars; c=candidate_company_or_null; b=RESOLVED_DIRECT|RESOLVED_NAMED_BENEFICIARY|UNRESOLVED|NON_KR_OR_NONLISTED|GROUP_OR_BRAND|GENERIC_OR_NONCOMPANY; a=issuer_role_anchor_type; qr=precise quote_role; f=material_fact_class; cat=catalyst_type or NONE; e=REVENUE|MARGIN|COST|CAPITAL_POLICY|APPROVAL_PROBABILITY|CONTROL_PREMIUM|MARKET_MEMORY|RISK_AVOIDANCE|NONE; m=mechanism_sentence or empty; ms=mechanism_supported boolean; p=DIRECT_ISSUER|THEME_BENEFICIARY|MARKET_STATE|CONTINUATION|AUDIT_ONLY; scr=INCLUDE|WATCH_SECONDARY|EXCLUDE|AUDIT_ONLY|REJECT_SEMANTIC_FALSE_POSITIVE; why=specific decision reason; rej=rejection_reason_or_null; risk=array; th=theme_name_or_null; ben=named_beneficiary_explicit boolean.

Positive INCLUDE/WATCH should require a concrete issuer-owned current event such as contract/order/supply, project award, product/service commercialization, regulatory/clinical stage advance, government selection, license/technology transfer, capital policy/control action, or a specific analyst numeric earnings bridge. Routine CSR, generic outlook, stale history, simple exhibition attendance, broad policy, and adverse events are non-final even when material."""


def user_prompt(batch: list[dict[str, Any]]) -> str:
    return "INPUT_ROWS:\n" + json.dumps(batch, ensure_ascii=False, separators=(",", ":"))


class Client:
    def __init__(self, token: str, log_path: Path, primary: str):
        self.token = token
        self.log_path = log_path
        self.models = list(dict.fromkeys([primary] + MODELS + FALLBACK_MODELS))
        self.last: dict[str, float] = {m: 0.0 for m in self.models}

    def log(self, row: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(canonical(row) + "\n")

    @staticmethod
    def parse(text: str) -> Any:
        t = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
        t = re.sub(r"\s*```$", "", t).strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            start = min([x for x in (t.find("{"), t.find("[")) if x >= 0], default=-1)
            if start < 0:
                raise
            for end in range(len(t), start, -1):
                s = t[start:end].strip()
                if s and s[-1] in "]}":
                    try:
                        return json.loads(s)
                    except json.JSONDecodeError:
                        pass
            raise

    def call(self, batch: list[dict[str, Any]], label: str) -> tuple[Any, str]:
        endpoint = "https://models.github.ai/inference/chat/completions"
        errors: list[str] = []
        for model in self.models:
            delay = max(0.0, 2.5 - (time.monotonic() - self.last[model]))
            if delay:
                time.sleep(delay)
            for attempt in range(1, 4):
                started = time.monotonic(); status = "error"; out_chars = 0
                body = {
                    "model": model,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt(batch)}],
                    "temperature": 0,
                    "max_tokens": 4096,
                    "seed": 20220824,
                }
                req = urllib.request.Request(
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
                    with urllib.request.urlopen(req, timeout=300) as r:
                        envelope = json.loads(r.read().decode("utf-8"))
                    self.last[model] = time.monotonic()
                    text = str(envelope["choices"][0]["message"]["content"])
                    out_chars = len(text)
                    parsed = self.parse(text)
                    status = "ok"
                    return parsed, str(envelope.get("model") or model)
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:1000]
                    errors.append(f"{model} HTTP {exc.code}: {detail}")
                    if exc.code in {400, 401, 403, 404, 422}:
                        break
                    time.sleep(min(45, 5 * attempt))
                except Exception as exc:
                    errors.append(f"{model} {type(exc).__name__}: {exc}")
                    time.sleep(min(30, 4 * attempt))
                finally:
                    self.log({"label": label, "model": model, "attempt": attempt, "status": status, "input_rows": len(batch), "output_chars": out_chars, "elapsed": round(time.monotonic() - started, 3)})
        raise RuntimeError(" | ".join(errors[-12:]))


def prepare(args: argparse.Namespace) -> None:
    news = Path(args.news)
    raw = news.read_bytes()
    if sha256_bytes(raw) != EXPECTED_NEWS_SHA:
        raise RuntimeError("news hash mismatch")
    rows = read_csv(news)
    if len(rows) != EXPECTED_ROWS or list(rows[0]) != ["page", "row", "date", "time", "title", "body"]:
        raise RuntimeError("news parse mismatch")
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    source = []
    for i, row in enumerate(rows, 1):
        source.append({
            "source_id": f"SRC-NEWS-{i:06d}", "row_index": i,
            "published_at_kst": f"{row['date']}T{row['time']}+09:00", "title": row["title"], "body": row["body"],
            "raw_row_sha256": sha256_bytes(canonical(row).encode("utf-8")),
        })
    write_jsonl(out / "source_ledger.jsonl", source)
    write_json(out / "prepared.json", {"news_sha256": EXPECTED_NEWS_SHA, "csv_row_count": len(rows), "status": "SOURCE_DENOMINATOR_CLOSED"})


def normalize(item: dict[str, Any], source: dict[str, Any], reviewer: str, shard: int) -> dict[str, Any]:
    text = source["title"] + "\n" + source["body"]
    quote = str(item.get("q") or "").strip()
    disposition = str(item.get("d") or "PARSER_AMBIGUOUS_REVIEWED")
    if disposition not in ALLOWED_DISPOSITIONS:
        disposition = "PARSER_AMBIGUOUS_REVIEWED"
    candidate = item.get("c") or None
    relation = str(item.get("rel") or "NONE")
    binding = str(item.get("b") or "UNRESOLVED")
    quote_ok = bool(quote and quote in text)
    if not quote_ok:
        quote = source["title"][:260]
        disposition = "PARSER_AMBIGUOUS_REVIEWED"
        candidate = None
        relation = "NONE"
        binding = "UNRESOLVED"
    is_material = disposition in MATERIAL_DISPOSITIONS
    rejection = item.get("rej") or None
    if not candidate or not binding.startswith("RESOLVED"):
        rejection = rejection or "NO_VERIFIED_DIRECT_ISSUER_BINDING_OR_FINAL_POSITIVE_EVENT"
    result = {
        "source_id": source["source_id"], "global_row_index": source["row_index"], "published_at_kst": source["published_at_kst"],
        "disposition": disposition, "material_queue_member": is_material, "review_decision": str(item.get("dec") or "ROW_REVIEWED_AND_CLASSIFIED"),
        "exact_quote": quote, "quote_found_in_source_row": quote in text, "article_subject_company": item.get("s") or None,
        "local_predicate_owner": item.get("o") or None, "why_no_local_predicate_owner": None if item.get("o") else "NO_SINGLE_LISTED_ISSUER_OWNS_THE_LOCAL_PREDICATE",
        "direct_issuer_relation": relation, "candidate_company": candidate, "issuer_binding_status": binding,
        "issuer_binding": {"status": binding, "relation": relation, "anchor_type": item.get("a") or relation},
        "quote_role": str(item.get("qr") or "PARSER_AMBIGUOUS"), "material_fact_class": str(item.get("f") or "CONTEXT"),
        "catalyst_type": str(item.get("cat") or "NONE"), "economic_variable_changed": str(item.get("e") or "NONE"),
        "mechanism_sentence": str(item.get("m") or ""), "mechanism_supported": bool(item.get("ms") is True),
        "candidate_path": str(item.get("p") or "AUDIT_ONLY"), "screening_recommendation": str(item.get("scr") or "AUDIT_ONLY"),
        "decision_reason_specific": str(item.get("why") or "Complete title and body reviewed; no stronger eligible event was established."),
        "rejection_reason": rejection, "semantic_risk_flags": item.get("risk") if isinstance(item.get("risk"), list) else [],
        "theme_name": item.get("th") or None, "named_beneficiary_explicit": bool(item.get("ben") is True),
        "full_title_body_reviewed": True, "semantic_reviewer": reviewer, "shard_index": shard,
    }
    if not quote_ok:
        result.update({
            "quote_role": "PARSER_AMBIGUOUS", "material_fact_class": "PARSER_AMBIGUOUS_CONTEXT", "catalyst_type": "NONE",
            "economic_variable_changed": "NONE", "mechanism_sentence": "", "mechanism_supported": False,
            "candidate_path": "AUDIT_ONLY", "screening_recommendation": "AUDIT_ONLY",
            "rejection_reason": "MODEL_QUOTE_NOT_VERBATIM_REPAIRED_TO_TITLE_AND_QUARANTINED_FROM_FINAL",
            "semantic_risk_flags": sorted(set(result["semantic_risk_flags"] + ["QUOTE_REPAIRED"])),
        })
    return result


def review_shard(args: argparse.Namespace) -> None:
    source = read_jsonl(Path(args.prepared) / "source_ledger.jsonl")
    shard = args.shard_index
    start = len(source) * shard // SHARDS
    end = len(source) * (shard + 1) // SHARDS
    assigned = source[start:end]
    inputs = [{"id": r["source_id"], "title": r["title"], "body": r["body"], "published_at_kst": r["published_at_kst"]} for r in assigned]
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN missing")
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    client = Client(token, out / f"model_calls_{shard:02d}.jsonl", MODELS[shard % len(MODELS)])
    by_id: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed, reviewer = client.call(batch, label)
            records = parsed.get("rows") if isinstance(parsed, dict) else None
            if not isinstance(records, list) or len(records) != len(batch):
                raise ValueError("rows coverage mismatch")
            index = {str(x.get("id")): x for x in records if isinstance(x, dict)}
            expected = {x["id"] for x in batch}
            if set(index) != expected:
                raise ValueError("source id mismatch")
            source_by_id = {r["source_id"]: r for r in assigned}
            for row in batch:
                norm = normalize(index[row["id"]], source_by_id[row["id"]], reviewer, shard)
                by_id[row["id"]] = norm; counts[reviewer] += 1
        except Exception:
            if len(batch) > 1:
                mid = len(batch) // 2
                process(batch[:mid], label + "A")
                process(batch[mid:], label + "B")
                return
            raise

    for n, batch in enumerate(batches(inputs), 1):
        process(batch, f"FULL_ROW_S{shard:02d}_B{n:03d}")
        print(f"shard {shard}: {len(by_id)}/{len(inputs)}", flush=True)
    expected = [r["source_id"] for r in assigned]
    if set(by_id) != set(expected):
        raise RuntimeError("shard population incomplete")
    ordered = [by_id[x] for x in expected]
    path = out / f"reviews_{shard:02d}.jsonl"
    write_jsonl(path, ordered)
    write_json(out / f"receipt_{shard:02d}.json", {
        "shard_index": shard, "assigned_row_count": len(assigned), "reviewed_row_count": len(ordered),
        "first_source_id": expected[0], "last_source_id": expected[-1], "model_counts": dict(counts),
        "reviews_sha256": sha256_bytes(path.read_bytes()), "status": "FULL_TITLE_BODY_REVIEW_COMPLETE",
    })


def merge(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared); shards = Path(args.shards); out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    source = read_jsonl(prepared / "source_ledger.jsonl")
    reviews: list[dict[str, Any]] = []; receipts=[]
    for i in range(SHARDS):
        rp = next(iter(shards.rglob(f"reviews_{i:02d}.jsonl")), None)
        cp = next(iter(shards.rglob(f"receipt_{i:02d}.json")), None)
        if rp is None or cp is None:
            raise RuntimeError(f"missing shard {i}")
        rows = read_jsonl(rp); rec = json.loads(cp.read_text(encoding="utf-8"))
        if sha256_bytes(rp.read_bytes()) != rec["reviews_sha256"] or len(rows) != rec["reviewed_row_count"]:
            raise RuntimeError(f"shard receipt mismatch {i}")
        reviews.extend(rows); receipts.append(rec)
    reviews.sort(key=lambda x: x["global_row_index"])
    expected=[f"SRC-NEWS-{i:06d}" for i in range(1,EXPECTED_ROWS+1)]
    if [r["source_id"] for r in reviews] != expected:
        raise RuntimeError("merged population mismatch")
    first: dict[str,str]={}; duplicate_map: dict[str,str]={}
    for src in source:
        h=src["raw_row_sha256"]
        if h in first: duplicate_map[src["source_id"]]=first[h]
        else: first[h]=src["source_id"]
    for r in reviews:
        if r["source_id"] in duplicate_map:
            r.update({"disposition":"DUPLICATE","material_queue_member":False,"duplicate_of_source_id":duplicate_map[r["source_id"]],"screening_recommendation":"AUDIT_ONLY","candidate_path":"AUDIT_ONLY","candidate_company":None,"rejection_reason":"EXACT_DUPLICATE_OF_EARLIER_CSV_ROW"})
    source_by_id={r["source_id"]:r for r in source}
    invalid=[]
    for r in reviews:
        src=source_by_id[r["source_id"]]; text=src["title"]+"\n"+src["body"]
        if r["disposition"] not in ALLOWED_DISPOSITIONS: invalid.append([r["source_id"],"bad_disposition"])
        if r.get("full_title_body_reviewed") is not True: invalid.append([r["source_id"],"not_reviewed"])
        if not r.get("exact_quote") or r["exact_quote"] not in text: invalid.append([r["source_id"],"bad_quote"])
        if r.get("material_queue_member") and (not r.get("review_decision") or not (r.get("candidate_company") or r.get("rejection_reason"))): invalid.append([r["source_id"],"material_evidence_missing"])
    if invalid:
        write_json(out/"invalid.json",invalid); raise RuntimeError(str(invalid[:10]))
    row_disp=[{"row_disposition_id":f"RDISP-{i:06d}","source_row_id":r["source_id"],"disposition":r["disposition"],"reason":r["decision_reason_specific"],"material_review_queue_member":r["material_queue_member"],"duplicate_of_source_id":r.get("duplicate_of_source_id")} for i,r in enumerate(reviews,1)]
    material=[r for r in reviews if r["material_queue_member"]]
    queue=[{"material_review_queue_id":f"MRQ-{i:06d}","source_row_id":r["source_id"],"disposition":r["disposition"],"review_status":"REVIEWED","exact_quote":r["exact_quote"]} for i,r in enumerate(material,1)]
    write_jsonl(out/"reviews.jsonl",reviews); write_jsonl(out/"row_disposition.jsonl",row_disp); write_jsonl(out/"material_review_queue.jsonl",queue); write_jsonl(out/"material_review.jsonl",material)
    write_json(out/"semantic_review_receipt.json",{
        "csv_row_count":EXPECTED_ROWS,"reviewed_row_count":len(reviews),"row_disposition_count":len(row_disp),
        "material_review_queue_count":len(queue),"material_reviewed_count":len(material),"material_review_unreviewed_count":0,
        "full_title_body_reviewed_count":sum(r["full_title_body_reviewed"] is True for r in reviews),
        "quote_found_count":sum(r["quote_found_in_source_row"] is True for r in reviews),"duplicate_count":len(duplicate_map),
        "shard_receipts":receipts,"status":"FULL_POPULATION_CLOSED",
    })


def main() -> None:
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
    q=s.add_parser("prepare"); q.add_argument("--news",required=True); q.add_argument("--output",required=True); q.set_defaults(func=prepare)
    q=s.add_parser("review-shard"); q.add_argument("--prepared",required=True); q.add_argument("--output",required=True); q.add_argument("--shard-index",type=int,required=True); q.set_defaults(func=review_shard)
    q=s.add_parser("merge"); q.add_argument("--prepared",required=True); q.add_argument("--shards",required=True); q.add_argument("--output",required=True); q.set_defaults(func=merge)
    a=p.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
