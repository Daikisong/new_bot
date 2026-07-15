from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

runner_path = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826_single", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {runner_path}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.STAMP = "20260715T215900KST"
runner.STAMP_LOWER = "20260715t215900_single"
runner.RUN_ID = "nslab_run_20260715T215900KST_20220826_single"
runner.MODEL_NAME = "openai/gpt-4o"
runner.WORK = runner.ROOT / "work_20220826_20260715t215900_single"
runner.INPUTS = runner.WORK / "inputs"
runner.PIPELINE = runner.WORK / "pipeline"
runner.BLIND_OUT = runner.WORK / "blind"
runner.POST_INPUTS = runner.WORK / "post_inputs"
runner.POST_OUT = runner.WORK / "post_output"
runner.FINAL_ARTIFACT = runner.ROOT / "final_artifact_20220826_20260715t215900_single"

_original_prepare = runner.fresh_prepare


def tuned_prepare():
    receipt = _original_prepare()

    blind_path = runner.PIPELINE / "blind.py"
    blind_text = blind_path.read_text(encoding="utf-8")
    anchors = {
        "batches = list(row_batches(model_inputs, max_items=18, max_chars=78000))":
            "batches = list(row_batches(model_inputs, max_items=1, max_chars=16000))",
        "max_workers = min(5, max(1, len(batches)))":
            "max_workers = min(30, max(1, len(batches)))",
    }
    for old, new in anchors.items():
        if old not in blind_text:
            raise RuntimeError(f"semantic tuning anchor missing: {old}")
        blind_text = blind_text.replace(old, new, 1)
    blind_path.write_text(blind_text, encoding="utf-8")

    post_path = runner.PIPELINE / "postmortem.py"
    post_text = post_path.read_text(encoding="utf-8")
    marker = "\ndef build_outcome_audit(\n"
    if marker not in post_text:
        raise RuntimeError("postmortem reverse-audit insertion anchor missing")
    parallel_reverse = r'''

def reverse_audit_unmatched(
    leaders: list[dict[str, Any]],
    context: list[dict[str, Any]],
    token: str,
    output: Path,
) -> dict[str, dict[str, Any]]:
    if not leaders:
        return {}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    allowed_sources = {row["source_row_id"] for row in context}
    allowed_facts = {row["fact_id"] for row in context}
    compact_context = [{
        "source_row_id": row["source_row_id"],
        "fact_id": row["fact_id"],
        "ticker": row.get("ticker"),
        "company": row.get("company"),
        "candidate_path": row.get("candidate_path"),
        "theme_name": row.get("theme_name"),
        "quote": str(row.get("quote") or "")[:320],
        "screening_decision": row.get("screening_decision"),
    } for row in context]

    batches = [(start, leaders[start:start + 5]) for start in range(0, len(leaders), 5)]

    def audit_one(start: int, batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        system = "You are a post-seal reverse auditor. You may use only the sealed pre-open context IDs supplied. Never invent a catalyst. Outcome data can label errors but cannot create a pre-open relation. Return strict JSON only."
        user = """For each OUTCOME_LEADER, decide whether a concrete sealed source supports a DIRECT_MATCH, THEME_BRIDGE, MARKET_STATE, CONTINUATION, or NONE. A theme bridge must be explicit enough to connect the winner's business to the sealed policy/industry fact; generic market commentary is NONE. Return {\"records\":[{\"outcome_leader_id\":\"...\",\"sealed_source_match\":\"DIRECT_MATCH|THEME_BRIDGE|MARKET_STATE|CONTINUATION|NONE\",\"matched_source_row_ids\":[],\"matched_fact_ids\":[],\"reason\":\"specific\"}]}. Use only IDs present in SEALED_CONTEXT; if uncertain return NONE and empty IDs.\nOUTCOME_LEADERS:\n""" + json.dumps(batch, ensure_ascii=False) + "\nSEALED_CONTEXT:\n" + json.dumps(compact_context, ensure_ascii=False)
        try:
            parsed = model_json(
                token,
                system=system,
                user=user,
                label=f"OUTCOME_REVERSE_AUDIT_{start // 5 + 1:03d}",
                log_path=output / "model_call_log.jsonl",
                max_tokens=10000,
            )
            records = parsed.get("records", []) if isinstance(parsed, dict) else parsed
        except Exception:
            records = []
        by_id = {str(row.get("outcome_leader_id")): row for row in records if isinstance(row, dict)}
        local: dict[str, dict[str, Any]] = {}
        for leader in batch:
            raw = by_id.get(leader["outcome_leader_id"], {})
            match = str(raw.get("sealed_source_match") or "NONE").upper()
            if match not in {"DIRECT_MATCH", "THEME_BRIDGE", "MARKET_STATE", "CONTINUATION", "NONE"}:
                match = "NONE"
            sources = [sid for sid in string_list(raw.get("matched_source_row_ids")) if sid in allowed_sources]
            facts = [fid for fid in string_list(raw.get("matched_fact_ids")) if fid in allowed_facts]
            if match != "NONE" and (not sources or not facts):
                match = "NONE"
                sources = []
                facts = []
            local[leader["outcome_leader_id"]] = {
                "sealed_source_match": match,
                "matched_source_row_ids": sources,
                "matched_fact_ids": facts,
                "reason": string_or_none(raw.get("reason")) or "No sufficiently local sealed source relation was established.",
            }
        return local

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(batches))), thread_name_prefix="nslab-outcome-audit") as executor:
        futures = [executor.submit(audit_one, start, batch) for start, batch in batches]
        for future in as_completed(futures):
            results.update(future.result())
    return results
'''
    post_text = post_text.replace(marker, parallel_reverse + marker, 1)
    post_path.write_text(post_text, encoding="utf-8")

    runner.run([
        sys.executable,
        "-m",
        "py_compile",
        str(runner.PIPELINE / "common.py"),
        str(blind_path),
        str(runner.PIPELINE / "reseal.py"),
        str(post_path),
    ])
    return receipt


runner.fresh_prepare = tuned_prepare
runner.main()
