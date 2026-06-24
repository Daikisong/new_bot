"""Structured semantic research import contracts and prompt assembly."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from news_scalping_lab.contracts.models import StrictModel
from news_scalping_lab.utils import canonical_json, sha256_text

SEMANTIC_IMPORT_PROMPT_VERSION = "semantic_import.v1"


class SemanticResearchDraft(StrictModel):
    """LLM-produced draft used to convert free-form research into a canonical episode."""

    schema_version: str = "nslab.semantic_research_draft.v1"
    trade_date: date
    cutoff_at: datetime
    research_version: str = "semantic-llm-v1"
    summary: str
    open_world_mechanisms: list[str]
    initial_uncertainties: list[str] = Field(default_factory=list)
    input_news_files: list[str] = Field(default_factory=list)
    input_news_hashes: list[str] = Field(default_factory=list)
    price_source_snapshot: dict[str, Any] = Field(default_factory=dict)
    available_from: datetime | None = None

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must not be empty")
        return value

    @field_validator("open_world_mechanisms")
    @classmethod
    def mechanisms_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("open_world_mechanisms must not be empty")
        return value


def build_semantic_import_prompt(*, root: Path, source_path: Path, source_sha256: str, text: str) -> str:
    instruction_path = root / "prompts" / "research_import" / "semantic_import.md"
    if instruction_path.exists():
        instructions = instruction_path.read_text(encoding="utf-8")
    else:
        instructions = (
            "Convert the research source into nslab.semantic_research_draft.v1. "
            "Return only structured data."
        )
    source_metadata = {
        "prompt_version": SEMANTIC_IMPORT_PROMPT_VERSION,
        "source_path": source_path.as_posix(),
        "source_sha256": source_sha256,
        "source_text_sha256": sha256_text(text),
    }
    return "\n\n".join(
        [
            instructions.strip(),
            "Return a SemanticResearchDraft. Do not invent ticker/theme mappings.",
            "---SOURCE_METADATA---",
            canonical_json(source_metadata),
            "---SOURCE_TEXT---",
            text[:50_000],
        ]
    )
