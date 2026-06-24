from __future__ import annotations

from news_scalping_lab.llm.mock import DeterministicMockLLMProvider


def test_region_name_change_keeps_mechanism_shape() -> None:
    llm = DeterministicMockLLMProvider()
    first = llm.infer_mechanisms("A지역에 대규모 첨단산업단지 건설이 추진된다.")
    second = llm.infer_mechanisms("B지역에 대규모 첨단산업단지 건설이 추진된다.")
    assert first == second
    assert "catalyst" in first[0]
