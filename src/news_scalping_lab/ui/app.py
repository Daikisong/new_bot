"""Minimal Streamlit UI wrapper.

Run with:

    streamlit run src/news_scalping_lab/ui/app.py
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from news_scalping_lab.config import load_settings
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.utils import parse_datetime


def main() -> None:
    try:
        import streamlit as st  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install the ui extra to run the Streamlit app") from exc

    settings = load_settings(Path.cwd())
    st.title("news-scalping-lab")
    st.caption("LLM-native blind pre-open research")
    uploaded = st.file_uploader("News CSV", type=["csv"])
    trade_day = st.date_input("Trade date", value=date.today())
    cutoff = st.text_input("Cutoff", value=f"{trade_day.isoformat()}T08:59:59+09:00")
    mode = st.selectbox("Mode", ["exhaustive", "brain", "fast"])
    web_search = st.checkbox("Web search", value=False)
    if st.button("Analyze") and uploaded is not None:
        temp = settings.path("data/inbox/news") / uploaded.name
        temp.write_bytes(uploaded.getvalue())
        analysis = asyncio.run(
            DailyAnalyzer(settings).analyze(
                news_csv=temp,
                trade_date=trade_day,
                cutoff_at=parse_datetime(cutoff),
                mode=mode,
                web_search=web_search,
            )
        )
        st.success(f"Run complete: {analysis.run_id}")
        st.json(analysis.model_dump(mode="json"))


if __name__ == "__main__":  # pragma: no cover
    main()
