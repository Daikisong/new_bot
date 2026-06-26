"""Minimal Streamlit UI wrapper.

Run with:

    streamlit run src/news_scalping_lab/ui/app.py
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.config import load_settings
from news_scalping_lab.contracts.models import DominantSectorHypothesis, PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ui.view_model import (
    AnalysisViewModel,
    CandidateEvidenceView,
    build_analysis_view_model,
)
from news_scalping_lab.utils import parse_datetime


def main() -> None:
    try:
        import streamlit as st  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install the ui extra to run the Streamlit app") from exc

    settings = load_settings(Path.cwd())
    st.title("news-scalping-lab")
    st.caption("LLM-native blind pre-open research")
    st.metric("Brain version", current_brain_version(settings.project_root) or "none")
    uploaded = st.file_uploader("News CSV", type=["csv"])
    trade_day = st.date_input("Trade date", value=date.today())
    cutoff = st.text_input("Cutoff", value=f"{trade_day.isoformat()}T08:59:59+09:00")
    mode = st.selectbox("Mode", ["exhaustive", "brain", "fast"])
    web_search = st.checkbox("Web search", value=False)
    if st.button("Analyze") and uploaded is not None:
        temp = settings.path("data/inbox/news") / uploaded.name
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(uploaded.getvalue())
        with st.status("Running blind analysis", expanded=True) as status:
            st.write("Loading brain context and sweeping memory shards.")
            st.write("Running candidate generation, red-team review, and final synthesis.")
            analysis = asyncio.run(
                DailyAnalyzer(settings).analyze(
                    news_csv=temp,
                    trade_date=trade_day,
                    cutoff_at=parse_datetime(cutoff),
                    mode=mode,
                    web_search=web_search,
                )
            )
            view = build_analysis_view_model(settings.project_root, analysis)
            _render_run_progress_summary(view, st)
            status.update(label=f"Run complete: {analysis.run_id}", state="complete")
        _render_analysis(view, st)


def _render_analysis(view: AnalysisViewModel, st: Any) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", view.mode)
    col2.metric("Brain", view.brain_version)
    col3.metric("Swept episodes", f"{view.swept_episode_count}/{view.accepted_episode_count}")
    col4.metric("Sweep shards", view.memory_sweep_shard_count)
    st.caption(f"Memory sweep cache hits: {view.memory_sweep_cache_hits}")
    if view.coverage_errors:
        st.error("; ".join(view.coverage_errors))
    _render_memory_sweep_shards(view, st, heading=True)

    st.subheader("Dominant Sector Hypotheses")
    if not view.dominant_sectors:
        st.write("No sector hypotheses.")
    for sector in view.dominant_sectors:
        _render_sector(sector, st)

    st.subheader("All Pre-Open Watchlist Candidates")
    if not view.all_watchlist_candidates:
        st.write("No watchlist candidates.")
    else:
        st.dataframe(
            [
                {
                    "rank": candidate.rank,
                    "company": candidate.company_name,
                    "ticker": candidate.ticker,
                    "path_type": candidate.path_type,
                    "confidence": candidate.confidence_label,
                    "evidence_quality": candidate.evidence_quality,
                    "memory_cases": ", ".join(candidate.memory_episode_ids),
                }
                for candidate in view.all_watchlist_candidates
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("Excluded But Watch")
    if not view.excluded_but_watch:
        st.write("No candidates with retained exclusion or caution reasons.")
    for item in view.excluded_but_watch:
        with st.expander(
            f"{item.candidate.rank}. {item.candidate.company_name} ({item.candidate.ticker})",
            expanded=False,
        ):
            st.write({"reasons": item.reasons})
            _render_candidate(item.candidate, st)

    st.subheader("Candidates")
    labels = {
        PathType.SINGLE_EVENT.value: "Single-news candidates",
        PathType.THEME_BENEFICIARY.value: "Theme beneficiary candidates",
        PathType.CONTINUATION.value: "Prior-leader continuation candidates",
        PathType.HYBRID.value: "Hybrid candidates",
    }
    for path_type, label in labels.items():
        with st.expander(label, expanded=path_type == PathType.SINGLE_EVENT.value):
            candidates = view.candidates_by_path.get(path_type, [])
            if not candidates:
                st.write("No candidates in this path.")
                continue
            for candidate in candidates:
                _render_candidate(candidate, st)

    st.subheader("Downloads")
    _download_if_exists(st, "Context manifest JSON", view.artifacts.context_manifest_json)
    _download_if_exists(st, "Prediction JSON", view.artifacts.prediction_json)
    _download_if_exists(st, "Pre-open report Markdown", view.artifacts.report_markdown)
    _download_optional(st, "Source ledger JSONL", view.artifacts.source_ledger_jsonl)
    _download_optional(
        st,
        "Excluded web sources JSONL",
        view.artifacts.excluded_web_sources_jsonl,
    )
    _download_optional(
        st,
        "Candidate web checks JSONL",
        view.artifacts.candidate_web_checks_jsonl,
    )
    _download_optional(
        st,
        "Candidate verification JSON",
        view.artifacts.candidate_verification_json,
    )
    _download_optional(
        st,
        "Final synthesis context JSON",
        view.artifacts.final_synthesis_context_json,
    )
    _download_optional(
        st,
        "Excluded candidate web checks JSONL",
        view.artifacts.excluded_candidate_web_checks_jsonl,
    )


def _render_sector(sector: DominantSectorHypothesis, st: Any) -> None:
    with st.expander(sector.name, expanded=True):
        st.write(sector.formation_mechanism)
        st.write(
            {
                "expected_breadth": sector.expected_breadth,
                "possible_leaders": sector.possible_leaders,
                "supporting_cases": sector.supporting_cases,
                "contradicting_cases": sector.contradicting_cases,
                "failure_conditions": sector.failure_conditions,
            }
        )


def _render_candidate(candidate: CandidateEvidenceView, st: Any) -> None:
    st.markdown(f"**{candidate.rank}. {candidate.company_name} ({candidate.ticker})**")
    st.write(candidate.thesis)
    st.caption(f"Confidence: {candidate.confidence_label} | Evidence: {candidate.evidence_quality}")
    with st.expander("Evidence and objections"):
        st.write(
            {
                "why_now": candidate.why_now,
                "causal_chain": candidate.causal_chain,
                "direct_evidence": candidate.direct_evidence,
                "inferred_evidence": candidate.inferred_evidence,
                "market_memory_evidence": candidate.market_memory_evidence,
                "prior_positive_cases": candidate.prior_positive_cases,
                "prior_negative_cases": candidate.prior_negative_cases,
                "novel_reasoning": candidate.novel_reasoning,
                "counterarguments": candidate.counterarguments,
                "disconfirming_conditions": candidate.disconfirming_conditions,
                "memory_episode_ids": candidate.memory_episode_ids,
                "source_urls": candidate.source_urls,
            }
        )


def _render_run_progress_summary(view: AnalysisViewModel, st: Any) -> None:
    st.write(
        {
            "run_id": view.run_id,
            "mode": view.mode,
            "brain_version": view.brain_version,
            "memory_coverage": f"{view.swept_episode_count}/{view.accepted_episode_count}",
            "memory_sweep_shard_count": view.memory_sweep_shard_count,
            "memory_sweep_cache_hits": view.memory_sweep_cache_hits,
        }
    )
    if view.coverage_errors:
        st.error("; ".join(view.coverage_errors))
    _render_memory_sweep_shards(view, st, heading=False)


def _render_memory_sweep_shards(view: AnalysisViewModel, st: Any, *, heading: bool) -> None:
    if not view.memory_sweep_shards:
        return
    if heading:
        st.subheader("Memory Sweep Shards")
    st.dataframe(
        _memory_sweep_rows(view),
        hide_index=True,
        use_container_width=True,
    )


def _memory_sweep_rows(view: AnalysisViewModel) -> list[dict[str, object]]:
    return [
        {
            "shard": shard.shard_index,
            "status": shard.status,
            "episodes": shard.episode_count,
            "from_cache": shard.from_cache,
            "episode_ids": ", ".join(shard.episode_ids),
            "artifact": shard.artifact_path.as_posix(),
            "error": shard.error or "",
        }
        for shard in view.memory_sweep_shards
    ]


def _download_if_exists(st: Any, label: str, path: Path) -> None:
    if not path.exists():
        st.warning(f"Missing artifact: {path.as_posix()}")
        return
    st.download_button(
        label,
        data=path.read_bytes(),
        file_name=path.name,
    )


def _download_optional(st: Any, label: str, path: Path | None) -> None:
    if path is None:
        return
    _download_if_exists(st, label, path)


if __name__ == "__main__":  # pragma: no cover
    main()
