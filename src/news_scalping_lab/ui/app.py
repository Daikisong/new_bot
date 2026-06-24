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
from news_scalping_lab.contracts.models import Candidate, DominantSectorHypothesis, PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ui.view_model import AnalysisViewModel, build_analysis_view_model
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
            status.update(label=f"Run complete: {analysis.run_id}", state="complete")
        _render_analysis(build_analysis_view_model(settings.project_root, analysis), st)


def _render_analysis(view: AnalysisViewModel, st: Any) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", view.mode)
    col2.metric("Brain", view.brain_version)
    col3.metric("Swept episodes", f"{view.swept_episode_count}/{view.accepted_episode_count}")
    col4.metric("Sweep shards", view.memory_sweep_shard_count)
    st.caption(f"Memory sweep cache hits: {view.memory_sweep_cache_hits}")
    if view.coverage_errors:
        st.error("; ".join(view.coverage_errors))

    st.subheader("Dominant Sector Hypotheses")
    if not view.dominant_sectors:
        st.write("No sector hypotheses.")
    for sector in view.dominant_sectors:
        _render_sector(sector, st)

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


def _render_candidate(candidate: Candidate, st: Any) -> None:
    st.markdown(f"**{candidate.rank}. {candidate.company_name} ({candidate.ticker})**")
    st.write(candidate.thesis)
    st.caption(f"Confidence: {candidate.confidence_label} | Evidence: {candidate.evidence_quality}")
    with st.expander("Evidence and objections"):
        st.write({"why_now": candidate.why_now, "causal_chain": candidate.causal_chain})
        st.write(
            {
                "direct_evidence": candidate.direct_evidence,
                "inferred_evidence": candidate.inferred_evidence,
                "market_memory_evidence": candidate.market_memory_evidence,
                "counterarguments": candidate.counterarguments,
                "memory_episode_ids": candidate.memory_episode_ids,
                "source_urls": candidate.source_urls,
            }
        )


def _download_if_exists(st: Any, label: str, path: Path) -> None:
    if not path.exists():
        st.warning(f"Missing artifact: {path.as_posix()}")
        return
    st.download_button(
        label,
        data=path.read_bytes(),
        file_name=path.name,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
