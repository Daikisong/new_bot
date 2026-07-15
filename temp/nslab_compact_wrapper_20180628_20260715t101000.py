from __future__ import annotations

import re
import sys
from pathlib import Path

runner = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
text = runner.read_text(encoding="utf-8")

replacements = [
    ("20260715T171800KST", "20260715T101000Z"),
    ("20260715t171800", "20260715t101000"),
    ("20220826", "20180628"),
    ("2022-08-26", "2018-06-28"),
    ("20220825", "20180627"),
    ("2022-08-25", "2018-06-27"),
    ("20220829", "20180629"),
    ("2022-08-29", "2018-06-29"),
    ("2022/08", "2018/06"),
]
for old, new in replacements:
    text = text.replace(old, new)

old_batch = 'row_batches(model_inputs, max_items=12, max_chars=52000)'
new_batch = 'row_batches(model_inputs, max_items=8, max_chars=40000)'
assert old_batch in text
text = text.replace(old_batch, new_batch, 1)
old_workers = 'ThreadPoolExecutor(max_workers=6, thread_name_prefix="nslab-semantic")'
new_workers = 'ThreadPoolExecutor(max_workers=8, thread_name_prefix="nslab-semantic")'
assert old_workers in text
text = text.replace(old_workers, new_workers, 1)

namespace = {"__name__": "nslab_compact_runner_20180628", "__file__": str(runner)}
exec(compile(text, str(runner), "exec"), namespace)
original_prepare = namespace["prepare_inputs_and_pipeline"]


def compact_prepare() -> dict:
    receipt = original_prepare()
    blind_path = namespace["PIPELINE"] / "blind.py"
    blind = blind_path.read_text(encoding="utf-8")
    compact_block = r'''def detailed_review_system() -> str:
    return """You are the independent semantic adjudicator for a Korean pre-open stock-news ledger. Read every complete title and body. Decide the real predicate owner and issuer relation; never promote a ticker, substring, common noun, list member, attendee, manufacturer-only mention, group/affiliate mention, investor holding, or another-company article into issuer evidence. Preserve negative issuer events and policy/market context as material audit rows. Return strict JSON only, with concise field values and no prose outside the requested records."""


def detailed_review_user(batch: list[dict[str, Any]]) -> str:
    return """Return {\"records\":[...]} with exactly one record per INPUT_ROWS source_id and no extra IDs. Read the full title and body for every row.

Each record must contain exactly these semantic fields:
source_id; disposition; article_subject_company_or_null; local_predicate_owner_or_null; direct_issuer_relation; exact_quote; chosen_ticker_or_null; chosen_company_or_null; issuer_binding_status; quote_role; material_fact_class; catalyst_type; economic_variable_changed; mechanism_sentence; mechanism_supported; candidate_path; screening_recommendation; rejection_reason_or_null; theme_name_or_null; named_beneficiary_explicit.

Disposition: DIRECT_ISSUER_MATERIAL|DIRECT_ISSUER_SECONDARY|THEME_POLICY_INDUSTRY_EVENT|MARKET_STATE_REGIME|D1_CONTINUATION_SIGNAL|DISCLOSURE_OR_MARKET_NOTICE|BODY_TABLE_OR_LIST_AUDIT|DUPLICATE|LOW_SIGNAL_CONTEXT|NON_MARKET_NEWS|NON_KR_OR_NON_LISTED_CONTEXT|TIME_UNVERIFIED_RETAINED|PARSER_AMBIGUOUS_REVIEWED.
Relation: DIRECT_SUBJECT|DIRECT_PREDICATE_OWNER|NAMED_BENEFICIARY|EXCHANGE_NOTICE_SUBJECT|OTHER_COMPANY_MENTION|LIST_MEMBER|MANUFACTURER_ONLY|ATTENDEE_ONLY|GROUP_OR_AFFILIATE_ONLY|GENERIC_OR_NONCOMPANY|NON_KR_OR_NONLISTED|NONE.
Binding: RESOLVED_DIRECT|RESOLVED_NAMED_BENEFICIARY|UNRESOLVED|NON_KR_OR_NONLISTED|GROUP_OR_BRAND|GENERIC_OR_NONCOMPANY.
Candidate path: DIRECT_ISSUER|THEME_BENEFICIARY|MARKET_STATE|CONTINUATION|AUDIT_ONLY.
Screening: INCLUDE|WATCH_SECONDARY|EXCLUDE|AUDIT_ONLY|REJECT_SEMANTIC_FALSE_POSITIVE.
Economic variable: REVENUE|MARGIN|COST|CAPITAL_POLICY|APPROVAL_PROBABILITY|CONTROL_PREMIUM|MARKET_MEMORY|RISK_AVOIDANCE|NONE.

Positive quote_role/fact/catalyst combinations:
ISSUER_CONTRACT_ACTION or ISSUER_ORDER_OR_SUPPLY_ACTION or ISSUER_PROJECT_AWARDED_ACTION -> CONTRACT_ORDER with CONTRACT_SIGNED|ORDER_RECEIVED|SUPPLY_AGREEMENT|PROJECT_AWARDED.
ISSUER_PRODUCT_RELEASE_ACTION or ISSUER_SERVICE_RELEASE_ACTION or ISSUER_COMMERCIALIZATION_ACTION -> PRODUCT_COMMERCIALIZATION with PRODUCT_LAUNCHED_BY_ISSUER|PRODUCT_COMMERCIALIZATION_BY_ISSUER|SERVICE_RELEASE_BY_ISSUER.
ISSUER_REGULATORY_APPROVAL_ACTION or ISSUER_CLINICAL_OR_PIPELINE_STAGE_ACTION or ISSUER_GOVERNMENT_PROJECT_SELECTION_ACTION or ISSUER_LICENSE_OR_TECH_TRANSFER_ACTION -> BIO_STAGE_ADVANCE with REGULATORY_APPROVAL|CLINICAL_STAGE_ADVANCE|LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS|GOVERNMENT_PROJECT_SELECTED.
ISSUER_CAPITAL_POLICY_ACTION -> CAPITAL_POLICY with DIVIDEND|BUYBACK|SHARE_CANCELLATION|RIGHTS_ISSUE|THIRD_PARTY_ALLOCATION|MERGER_OR_SPINOFF|STAKE_SALE_OR_CONTROL_CHANGE.
ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION -> STRATEGIC_INVESTMENT with THIRD_PARTY_ALLOCATION|STAKE_SALE_OR_CONTROL_CHANGE|MERGER_OR_SPINOFF.
ISSUER_ANALYST_NUMERIC_BRIDGE -> ANALYST_BRIDGE with ANALYST_NUMERIC_EARNINGS_BRIDGE.
ISSUER_EXPLICIT_MARKET_STATE_NOTICE -> CONTINUATION_EXPLICIT with EXPLICIT_MARKET_STATE_NOTICE.

For non-final rows use the most accurate non-final quote_role already implied by the source, such as DIRECT_ISSUER_ADVERSE_EVENT_NONFINAL, DIRECT_ISSUER_ROUTINE_FACT_NONFINAL, POLICY_OR_INDUSTRY_CONTEXT, NON_KR_OR_NONLISTED_ISSUER, NON_MARKET_CONTEXT, DISCLOSURE_OR_ETF_NOTICE_NONISSUER, BODY_TABLE_LIST_MEMBER, OTHER_COMPANY_ARTICLE, MARKET_FLOW_TABLE_MEMBER_ONLY, ATTENDEE_LIST_ONLY, MANUFACTURER_ONLY, GENERAL_MARKET_COMMENTARY_ONLY, or PARSER_AMBIGUOUS. Set catalyst_type=NONE, economic_variable_changed=NONE, mechanism_supported=false, candidate_path=AUDIT_ONLY, and provide a specific rejection_reason.

Only choose a ticker/company from that row's krx_candidate_options, and only when the candidate is the article subject, local predicate owner, explicit named beneficiary, or exchange-notice subject. exact_quote must be a verbatim source substring no longer than 180 characters. A positive mechanism_sentence must be one concise sentence supported only by that quote. Keep every string concise. If uncertain, preserve the row as audit-only and unresolved rather than inventing a binding.

INPUT_ROWS:
""" + json.dumps(batch, ensure_ascii=False)
'''
    pattern = r"def detailed_review_system\(\) -> str:\n.*?\n\ndef normalize_review"
    replacement = compact_block + "\n\ndef normalize_review"
    blind, count = re.subn(pattern, replacement, blind, count=1, flags=re.S)
    assert count == 1, "compact semantic prompt patch anchor not found"
    blind = blind.replace("max_tokens=15000,", "max_tokens=9000,", 1)
    blind_path.write_text(blind, encoding="utf-8")
    namespace["run"](
        [
            sys.executable,
            "-m",
            "py_compile",
            str(namespace["PIPELINE"] / "common.py"),
            str(namespace["PIPELINE"] / "blind.py"),
            str(namespace["PIPELINE"] / "reseal.py"),
            str(namespace["PIPELINE"] / "postmortem.py"),
        ]
    )
    return receipt


acquisition = compact_prepare()
namespace["run_blind"](acquisition)
outcome_path = namespace["acquire_outcome_after_seal"](acquisition)
final_path = namespace["run_postmortem"](acquisition, outcome_path)
print(namespace["json"].dumps({"status": "ACCEPT_FULL", "final": str(final_path)}, sort_keys=True))
