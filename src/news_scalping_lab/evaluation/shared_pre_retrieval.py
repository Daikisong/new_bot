"""Build one sealed pre-retrieval context shared by runtime variants."""

from __future__ import annotations

import inspect
import json
import re
import types
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

import news_scalping_lab.inference.event_clustering as event_clustering_module
from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.contracts.memory_context import (
    EventClusterEntry,
    EventClusterManifest,
    NewsCoverageManifest,
    NewsRowCoverage,
)
from news_scalping_lab.contracts.models import (
    ContextManifest,
    NewsNoveltyLabel,
    NewsNoveltyReview,
    OpenWorldFirstAnalysis,
)
from news_scalping_lab.contracts.quality_evaluation import (
    QualityArtifactReference,
    QualityEvaluationProfile,
    SharedDMinusOneContext,
    SharedDownstreamDigest,
    SharedMapReduceNode,
    SharedOpenWorldReduceOutput,
    SharedPreRetrievalContext,
    SharedPreRetrievalContextManifest,
    reject_forbidden_blind_payload_keys,
)
from news_scalping_lab.inference.analyzer import (
    NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
    OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
    DailyAnalyzer,
    OpenWorldCoverageError,
)
from news_scalping_lab.inference.event_clustering import (
    EVENT_CLUSTERING_VERSION,
    EventClusteringResult,
    OpenWorldClusterInput,
)
from news_scalping_lab.ingest.news import (
    NewsBatch,
    load_news_csv,
    news_batch_content_root,
)
from news_scalping_lab.llm.base import count_provider_tokens
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.memory.runtime import production_embedding_method
from news_scalping_lab.retrieval.production_embedding import (
    ProductionEmbeddingUnavailableError,
)
from news_scalping_lab.utils import (
    canonical_json,
    default_news_window_start,
    file_sha256,
    now_kst,
    read_json,
    relative_to_root,
    sha256_bytes,
    sha256_text,
    stable_id,
)

SHARED_PRE_RETRIEVAL_VERSION = "nslab.shared_pre_retrieval.v10"
SHARED_CAPSULE_VERSION = "nslab.shared_cluster_capsule.v1"
SHARED_REDUCE_PROMPT_VERSION = "shared_open_world_reduce.v1"
SHARED_CONTENT_IDENTITY_VERSION = "nslab.shared_pre_retrieval_content_identity.v4"
SHARED_COMPONENT_ROOT_VERSION = "nslab.shared_component_artifact_root.v1"
PROVIDER_CHECKPOINT_COMMITMENT_VERSION = "nslab.shared_provider_checkpoint_commitment.v1"
PROVIDER_CHECKPOINT_THREAT_BOUNDARY = (
    "DETECTS_SHARED_PACKAGE_REWRITE_WITH_INTACT_PROVIDER_CHECKPOINTS;"
    "OS_LEVEL_REWRITE_OF_BOTH_SHARED_PACKAGE_AND_LLM_CHECKPOINT_STORE_"
    "REQUIRES_AN_EXTERNAL_SIGNED_OR_REMOTE_ANCHOR"
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")
_SALIENT_SENTENCE = re.compile(
    r"(?:\d|[%$₩]|억원|조원|만원|계약|승인|허가|수주|공급|투자|증자|"
    r"감자|합병|인수|매각|실적|매출|영업이익|정책|규제|소송|특허|임상)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SharedPreRetrievalBuildResult:
    context: SharedPreRetrievalContext
    context_path: Path
    manifest: SharedPreRetrievalContextManifest
    manifest_path: Path
    news_batch: NewsBatch
    cache_hit: bool


@dataclass(frozen=True)
class _CallReceipt:
    checkpoint_hit: bool
    live_call_count: int
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class _NodeState:
    contract: SharedMapReduceNode
    output: OpenWorldFirstAnalysis | SharedOpenWorldReduceOutput


@dataclass(frozen=True)
class _VerifiedBlindArtifact:
    path: Path
    raw_bytes: bytes
    sha256: str
    payload: object
    json_lines: bool


@dataclass(frozen=True)
class _ProviderCheckpointCommitment:
    root_sha256: str
    checkpoint_count: int
    novelty_checkpoint_count: int
    prompt_tokens_estimate: int
    completion_tokens_estimate: int


@contextmanager
def _without_mutable_llm_checkpoint_reuse(
    analyzer: DailyAnalyzer,
) -> Iterator[None]:
    """Require a live provider response when no external shared anchor exists."""

    provider = analyzer.llm
    if not isinstance(provider, TracingLLMProvider):
        yield
        return
    previous = provider.resume_from_checkpoints
    provider.resume_from_checkpoints = False
    try:
        yield
    finally:
        provider.resume_from_checkpoints = previous


def _source_root(*callables: Callable[..., Any]) -> str:
    sources: list[str] = []
    for callable_value in callables:
        try:
            sources.append(inspect.getsource(callable_value))
        except (OSError, TypeError):
            code = getattr(callable_value, "__code__", None)
            if code is None:
                sources.append(repr(callable_value))
            else:
                sources.append(
                    canonical_json(
                        {
                            "bytecode": code.co_code.hex(),
                            "constants": [repr(value) for value in code.co_consts],
                        }
                    )
                )
    return sha256_text(canonical_json(sources))


def _code_semantic_payload(code: types.CodeType) -> dict[str, Any]:
    constants: list[object] = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            constants.append(_code_semantic_payload(value))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            constants.append(value)
        elif isinstance(value, bytes):
            constants.append({"bytes_hex": value.hex()})
        else:
            constants.append({"type": (f"{type(value).__module__}.{type(value).__qualname__}")})
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "flags": code.co_flags,
        "bytecode": code.co_code.hex(),
        "constants": constants,
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def _runtime_semantic_payload(
    value: object,
    *,
    module_name: str,
    depth: int = 0,
) -> object:
    """Return a deterministic runtime fingerprint without address-based reprs."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": value.flags}
    if isinstance(value, (list, tuple)):
        return [_runtime_semantic_payload(item, module_name=module_name, depth=depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_runtime_semantic_payload(item, module_name=module_name, depth=depth + 1) for item in value]
        return sorted(items, key=canonical_json)
    if isinstance(value, dict):
        return {
            str(key): _runtime_semantic_payload(
                child,
                module_name=module_name,
                depth=depth + 1,
            )
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if inspect.ismodule(value):
        return {
            "module": getattr(value, "__name__", type(value).__module__),
            "version": str(getattr(value, "__version__", "")),
        }
    if inspect.isfunction(value) or inspect.ismethod(value):
        code = getattr(value, "__code__", None)
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            source = ""
        return {
            "callable": (
                f"{getattr(value, '__module__', '')}.{getattr(value, '__qualname__', getattr(value, '__name__', ''))}"
            ),
            "source_sha256": sha256_text(source),
            "code": (_code_semantic_payload(code) if isinstance(code, types.CodeType) else None),
            "defaults": _runtime_semantic_payload(
                getattr(value, "__defaults__", None),
                module_name=module_name,
                depth=depth + 1,
            ),
            "kwdefaults": _runtime_semantic_payload(
                getattr(value, "__kwdefaults__", None),
                module_name=module_name,
                depth=depth + 1,
            ),
        }
    if inspect.isclass(value):
        identity = f"{value.__module__}.{value.__qualname__}"
        members: dict[str, object] = {}
        if value.__module__ == module_name and depth < 3:
            for name, member in sorted(vars(value).items()):
                if name.startswith("__") or not callable(member):
                    continue
                members[name] = _runtime_semantic_payload(
                    member,
                    module_name=module_name,
                    depth=depth + 1,
                )
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            source = ""
        return {
            "class": identity,
            "source_sha256": sha256_text(source),
            "members": members,
        }
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def _module_semantic_root(module: types.ModuleType) -> str:
    module_path = Path(str(getattr(module, "__file__", "")))
    source_file_sha256 = file_sha256(module_path) if module_path.is_file() else ""
    symbols = {
        name: _runtime_semantic_payload(value, module_name=module.__name__)
        for name, value in sorted(vars(module).items())
        if not name.startswith("__")
    }
    return sha256_text(
        canonical_json(
            {
                "schema_version": "nslab.module_semantic_root.v1",
                "module": module.__name__,
                "source_file_sha256": source_file_sha256,
                "runtime_symbols": symbols,
            }
        )
    )


def _prompt_renderer_sha256() -> str:
    return _source_root(
        _map_prompt,
        _reduce_prompt,
        _cluster_capsule,
        _reduction_projection,
        _map_batches,
        _reduce_batches,
        _novelty_batches,
        DailyAnalyzer._build_news_novelty_review_prompt,
    )


def _event_cluster_renderer_sha256() -> str:
    return _module_semantic_root(event_clustering_module)


def _input_cluster_root_sha256(payload: object) -> str:
    return sha256_text(canonical_json(payload))


def _prompt_sha256_root(
    *,
    map_reduce_prompt_sha256s: list[str],
    novelty_batch_prompt_root_sha256: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "schema_version": "nslab.shared_prompt_root.v1",
                "map_reduce_prompt_sha256s": map_reduce_prompt_sha256s,
                "novelty_batch_prompt_root_sha256": (novelty_batch_prompt_root_sha256),
            }
        )
    )


def _content_identity_sha256(
    *,
    lookup_identity_sha256: str,
    parsed_news_root_sha256: str,
    input_cluster_root_sha256: str,
    prompt_sha256_root: str,
    component_artifact_root_sha256: str,
    downstream_digest_payload_sha256: str,
    context_payload_sha256: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "schema_version": SHARED_CONTENT_IDENTITY_VERSION,
                "code_semantic_version": SHARED_PRE_RETRIEVAL_VERSION,
                "lookup_identity_sha256": lookup_identity_sha256,
                "parsed_news_root_sha256": parsed_news_root_sha256,
                "input_cluster_root_sha256": input_cluster_root_sha256,
                "prompt_sha256_root": prompt_sha256_root,
                "component_artifact_root_sha256": (component_artifact_root_sha256),
                "downstream_digest_payload_sha256": (downstream_digest_payload_sha256),
                "context_payload_sha256": context_payload_sha256,
            }
        )
    )


def _lookup_identity(
    *,
    settings: Settings,
    profile: QualityEvaluationProfile,
    news_sha256: str,
    parsed_news_root_sha256: str,
    input_cluster_root_sha256: str,
    d_minus_one_reference: QualityArtifactReference,
    d_minus_one_payload_sha256: str,
    d_minus_one_candidate_universe_root_sha256: str,
    d_minus_one_snapshot_root_sha256: str,
    trade_date: date,
    cutoff_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": SHARED_PRE_RETRIEVAL_VERSION,
        "news_sha256": news_sha256,
        "parsed_news_root_sha256": parsed_news_root_sha256,
        "input_cluster_root_sha256": input_cluster_root_sha256,
        "d_minus_one_reference": d_minus_one_reference.model_dump(mode="json"),
        "d_minus_one_payload_sha256": d_minus_one_payload_sha256,
        "d_minus_one_candidate_universe_root_sha256": (d_minus_one_candidate_universe_root_sha256),
        "d_minus_one_snapshot_root_sha256": d_minus_one_snapshot_root_sha256,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "provider": profile.provider,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "code_semantic_version": SHARED_PRE_RETRIEVAL_VERSION,
        "event_clustering_version": EVENT_CLUSTERING_VERSION,
        "open_world_prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
        "novelty_prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
        "reduce_prompt_version": SHARED_REDUCE_PROMPT_VERSION,
        "provider_checkpoint_commitment_version": (PROVIDER_CHECKPOINT_COMMITMENT_VERSION),
        "provider_checkpoint_threat_boundary": (PROVIDER_CHECKPOINT_THREAT_BOUNDARY),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "embedding_batch_size": settings.limits.event_cluster_embedding_batch_size,
        "similarity_threshold": settings.limits.event_cluster_similarity_threshold,
        "max_semantic_variants": settings.limits.event_cluster_max_semantic_variants,
        "max_prompt_chars": settings.limits.open_world_max_prompt_chars,
        "open_world_cluster_batch_size": (
            settings.limits.open_world_cluster_batch_size
        ),
        "novelty_cluster_batch_size": settings.limits.novelty_cluster_batch_size,
        "packing_policy": "CONTEXT_CHARS_AND_CONFIGURED_CLUSTER_LIMIT.v2",
        "novelty_packing_policy": ("CONTEXT_CHARS_AND_CONFIGURED_CLUSTER_LIMIT.v2"),
        "prompt_renderer_sha256": _prompt_renderer_sha256(),
        "event_cluster_renderer_sha256": _event_cluster_renderer_sha256(),
    }


def _pretty_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f"immutable shared pre-retrieval artifact conflict: {path}") from None


def _write_immutable_json(path: Path, payload: object) -> None:
    _write_immutable_bytes(path, _pretty_json_bytes(payload))


def _validate_blind_payload(payload: object) -> None:
    reject_forbidden_blind_payload_keys(payload)
    _reject_shared_result_aliases(payload)


def _reject_shared_result_aliases(payload: object) -> None:
    discovered: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                separated = re.sub(
                    r"([A-Z]+)([A-Z][a-z])",
                    r"\1_\2",
                    str(raw_key).strip(),
                )
                separated = re.sub(
                    r"([a-z0-9])([A-Z])",
                    r"\1_\2",
                    separated,
                )
                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    separated.casefold(),
                ).strip("_")
                tokens = set(normalized.split("_")) if normalized else set()
                compact = normalized.replace("_", "")
                safe_zero_accounting = {
                    "outcome_access_count",
                    "outcome_reference_count",
                }
                result_tokens = {"outcome", "postmortem", "truth", "winner"}
                return_tokens = {"return", "returns"}
                result_qualifiers = {
                    "actual",
                    "close",
                    "d0",
                    "d1",
                    "day",
                    "high",
                    "intraday",
                    "label",
                    "next",
                    "observed",
                    "pct",
                    "percent",
                    "percentage",
                    "rate",
                    "realized",
                    "session",
                    "target",
                }
                compact_result_alias = any(token in compact for token in result_tokens)
                compact_return_alias = "return" in compact and any(
                    qualifier in compact for qualifier in result_qualifiers
                )
                safe_zero = normalized in safe_zero_accounting and child == 0
                if not safe_zero and (
                    tokens & result_tokens
                    or compact_result_alias
                    or (tokens & return_tokens and tokens & result_qualifiers)
                    or compact_return_alias
                ):
                    discovered.add(str(raw_key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    if discovered:
        raise ValueError("shared blind payload contains result-label aliases: " + ", ".join(sorted(discovered)))


def _verified_blind_artifact_from_bytes(
    path: Path,
    raw_bytes: bytes,
) -> _VerifiedBlindArtifact:
    suffix = path.suffix.casefold()
    if suffix not in {".json", ".jsonl"}:
        raise ValueError("shared component must be JSON or JSONL")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("shared component is not valid UTF-8") from exc
    if suffix == ".json":
        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid shared JSON artifact") from exc
        _validate_blind_payload(payload)
        return _VerifiedBlindArtifact(
            path=path,
            raw_bytes=raw_bytes,
            sha256=sha256_bytes(raw_bytes),
            payload=payload,
            json_lines=False,
        )
    rows: list[object] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid shared JSONL artifact at line {line_number}") from exc
        _validate_blind_payload(row)
        rows.append(row)
    return _VerifiedBlindArtifact(
        path=path,
        raw_bytes=raw_bytes,
        sha256=sha256_bytes(raw_bytes),
        payload=rows,
        json_lines=True,
    )


def _read_blind_artifact(path: Path) -> _VerifiedBlindArtifact:
    return _verified_blind_artifact_from_bytes(path, path.read_bytes())


def _read_blind_json(path: Path) -> object:
    artifact = _read_blind_artifact(path)
    return _blind_json_payload(artifact)


def _blind_json_payload(artifact: _VerifiedBlindArtifact) -> object:
    if artifact.json_lines:
        raise ValueError("expected one shared JSON object, found JSONL")
    return artifact.payload


def _blind_jsonl_dict_rows(
    artifact: _VerifiedBlindArtifact,
) -> list[dict[str, Any]]:
    if not artifact.json_lines or not isinstance(artifact.payload, list):
        raise ValueError("expected a shared JSONL artifact")
    if any(not isinstance(row, dict) for row in artifact.payload):
        raise ValueError("shared JSONL artifact contains a non-object row")
    return [row for row in artifact.payload if isinstance(row, dict)]


def _scan_blind_json_artifact(path: Path) -> None:
    _read_blind_artifact(path)


def _resolve_d_minus_one_reference(
    root: Path,
    *,
    reference: QualityArtifactReference,
    expected_context: SharedDMinusOneContext,
    trade_date: date,
    cutoff_at: datetime,
) -> _VerifiedBlindArtifact:
    normalized = reference.artifact_path.replace("\\", "/")
    logical = PurePosixPath(normalized)
    expected_prefix = (
        "runs",
        "semantic_brain_upgrade",
        "quality_full",
        "blind_inputs",
    )
    if (
        logical.is_absolute()
        or ".." in logical.parts
        or tuple(logical.parts[:4]) != expected_prefix
        or len(logical.parts) != 6
        or not logical.parts[4].startswith("QINPUT-")
        or logical.name != "d_minus_one_safe_context.json"
    ):
        raise ValueError("shared D-1 reference must be the sealed QINPUT artifact")
    resolved = (root / Path(*logical.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("shared D-1 artifact escapes evaluation root") from exc
    if not resolved.is_file():
        raise ValueError("shared D-1 artifact reference hash mismatch")
    artifact = _read_blind_artifact(resolved)
    if artifact.sha256 != reference.sha256 or artifact.json_lines:
        raise ValueError("shared D-1 artifact reference hash mismatch")
    loaded = SharedDMinusOneContext.model_validate(artifact.payload)
    if loaded.model_dump(mode="json") != expected_context.model_dump(mode="json"):
        raise ValueError("shared D-1 supplied model differs from its QINPUT artifact")
    if loaded.trade_date != trade_date or loaded.cutoff_at != cutoff_at:
        raise ValueError("shared D-1 temporal identity differs from the blind case")
    return artifact


def _component_artifact_root_sha256(
    *,
    references: dict[str, QualityArtifactReference],
    map_reduce_nodes: list[SharedMapReduceNode],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "schema_version": SHARED_COMPONENT_ROOT_VERSION,
                "components": {
                    name: reference.model_dump(mode="json") for name, reference in sorted(references.items())
                },
                "map_reduce_nodes": [node.model_dump(mode="json") for node in map_reduce_nodes],
            }
        )
    )


def _named_context_references(
    context: SharedPreRetrievalContext,
) -> dict[str, QualityArtifactReference]:
    return {
        "event_clustering_result": context.event_clustering_result,
        "row_disposition_ledger": context.row_disposition_ledger,
        "event_cluster_ledger": context.event_cluster_ledger,
        "news_coverage_manifest": context.news_coverage_manifest,
        "event_cluster_manifest": context.event_cluster_manifest,
        "open_world_first_analysis": context.open_world_first_analysis,
        "news_novelty_review": context.news_novelty_review,
        "downstream_digest": context.downstream_digest,
        "d_minus_one_safe_context": context.d_minus_one_safe_context,
        **{f"map_reduce_output:{node.node_id}": node.output for node in context.map_reduce_nodes},
    }


async def build_shared_pre_retrieval_context(
    root: Path,
    *,
    settings: Settings,
    profile: QualityEvaluationProfile,
    news_csv: Path,
    trade_date: date,
    cutoff_at: datetime,
    d_minus_one_context: SharedDMinusOneContext,
    d_minus_one_reference: QualityArtifactReference,
    trusted_cache_context_sha256: str | None = None,
    analyzer: DailyAnalyzer | None = None,
) -> SharedPreRetrievalBuildResult:
    """Create or verify a complete content-addressed shared context."""

    if profile.profile != "QUALITY_FULL":
        raise ValueError("shared pre-retrieval builder requires QUALITY_FULL")
    if (
        settings.llm_provider != profile.provider
        or settings.llm.model != profile.model
        or str(settings.llm.reasoning_effort or "") != profile.reasoning_effort
    ):
        raise ValueError("shared pre-retrieval profile differs from LLM settings")
    root = root.resolve()
    news_csv = news_csv.resolve()
    verified_d_minus_one = _resolve_d_minus_one_reference(
        root,
        reference=d_minus_one_reference,
        expected_context=d_minus_one_context,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
    )
    full_batch = load_news_csv(news_csv, trade_date=trade_date)
    parsed_news_root_sha256 = news_batch_content_root(full_batch)
    active_analyzer = analyzer or DailyAnalyzer(
        settings,
        runtime_retrieval_variant="legacy",
    )
    clustering_batch = _deterministic_clustering_batch(full_batch)
    event_clustering = await _cluster_once(
        settings,
        analyzer=active_analyzer,
        full_batch=clustering_batch,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
    )
    input_cluster_root_sha256 = _input_cluster_root_sha256(
        event_clustering_module.event_clustering_payload(event_clustering)
    )
    d_minus_one_payload_sha256 = sha256_text(canonical_json(d_minus_one_context.model_dump(mode="json")))
    identity = _lookup_identity(
        settings=settings,
        profile=profile,
        news_sha256=full_batch.sha256,
        parsed_news_root_sha256=parsed_news_root_sha256,
        input_cluster_root_sha256=input_cluster_root_sha256,
        d_minus_one_reference=d_minus_one_reference,
        d_minus_one_payload_sha256=d_minus_one_payload_sha256,
        d_minus_one_candidate_universe_root_sha256=(d_minus_one_context.candidate_universe_root_sha256),
        d_minus_one_snapshot_root_sha256=(d_minus_one_context.snapshot_root_sha256),
        trade_date=trade_date,
        cutoff_at=cutoff_at,
    )
    lookup_identity_sha256 = sha256_text(canonical_json(identity))
    context_id = stable_id("SHAREDCTX", lookup_identity_sha256, length=20)
    output_dir = root / "runs" / "semantic_brain_upgrade" / "quality_full" / "shared_pre_retrieval" / context_id
    context_path = output_dir / "shared_pre_retrieval_context.json"
    manifest_path = output_dir / "shared_pre_retrieval_context_manifest.json"
    cached = _load_cached(
        root,
        context_path=context_path,
        manifest_path=manifest_path,
        analyzer=active_analyzer,
        lookup_identity_sha256=lookup_identity_sha256,
        news_batch=full_batch,
        expected_parsed_news_root_sha256=parsed_news_root_sha256,
        expected_input_cluster_root_sha256=input_cluster_root_sha256,
        expected_event_clustering=event_clustering,
        expected_d_minus_one_payload_sha256=d_minus_one_payload_sha256,
        expected_d_minus_one_reference=d_minus_one_reference,
        expected_d_minus_one_artifact=verified_d_minus_one,
        expected_d_minus_one_candidate_universe_root_sha256=(d_minus_one_context.candidate_universe_root_sha256),
        expected_d_minus_one_snapshot_root_sha256=(d_minus_one_context.snapshot_root_sha256),
        trusted_cache_context_sha256=trusted_cache_context_sha256,
    )
    if cached is not None:
        return cached

    output_dir.mkdir(parents=True, exist_ok=True)
    event_clustering_path = output_dir / "event_clustering_result.json"
    _write_immutable_json(
        event_clustering_path,
        event_clustering_module.event_clustering_payload(event_clustering),
    )
    row_disposition_path = output_dir / "row_disposition_ledger.jsonl"
    event_cluster_path = output_dir / "event_cluster_capsules.jsonl"
    news_coverage_path = output_dir / "news_coverage_manifest.json"
    event_manifest_path = output_dir / "event_cluster_manifest.json"
    source_row_ids = _write_coverage_artifacts(
        row_disposition_path=row_disposition_path,
        event_cluster_path=event_cluster_path,
        news_coverage_path=news_coverage_path,
        event_manifest_path=event_manifest_path,
        result=event_clustering,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        news_sha256=full_batch.sha256,
        run_id=stable_id(
            "RUN-SHARED",
            lookup_identity_sha256,
            length=12,
        ),
        settings=settings,
    )
    shared_manifest = _shared_component_manifest(
        active_analyzer,
        result=event_clustering,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        news_csv=news_csv,
        news_sha256=full_batch.sha256,
        run_seed=lookup_identity_sha256,
        event_cluster_path=event_cluster_path,
    )
    with _without_mutable_llm_checkpoint_reuse(active_analyzer):
        nodes, root_state, open_world_analysis = await _run_map_reduce(
            active_analyzer,
            clusters=event_clustering_module.open_world_cluster_inputs(event_clustering),
            cutoff_at=cutoff_at,
            output_dir=output_dir,
        )
    aggregate_open_world_prompt_sha256 = _aggregate_hash([node.prompt_sha256 for node in nodes])
    _write_open_world_first_analysis_artifact(
        active_analyzer,
        analysis=open_world_analysis,
        manifest=shared_manifest,
        prompt_sha256=aggregate_open_world_prompt_sha256,
        cutoff_at=cutoff_at,
    )
    with _without_mutable_llm_checkpoint_reuse(active_analyzer):
        (
            novelty,
            novelty_prompt_sha256,
            _novelty_prompt_tokens,
            novelty_prompt_batch_hashes,
        ) = await _run_shared_novelty_review(
            active_analyzer,
            manifest=shared_manifest,
            cutoff_at=cutoff_at,
            output_dir=output_dir,
        )
    downstream_digest_path = output_dir / "shared_downstream_digest.json"
    downstream_digest = _shared_downstream_digest(
        context_id=context_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        material_cluster_ids=[cluster.cluster_id for cluster in event_clustering.material_clusters],
        root_state=root_state,
        open_world_source=_reference(
            root,
            root / str(shared_manifest.open_world_first_analysis_artifact),
        ),
        novelty=novelty,
        novelty_source=_reference(root, output_dir / "news_novelty_review.json"),
    )
    _write_immutable_json(
        downstream_digest_path,
        downstream_digest.model_dump(mode="json"),
    )
    node_artifacts = {
        f"map_reduce_output:{node.node_id}": _resolve_shared_reference(
            root,
            reference=node.output,
        )
        for node in nodes
    }
    node_states = _load_and_verify_node_states(
        nodes=nodes,
        cutoff_at=cutoff_at,
        verified_artifacts=node_artifacts,
    )
    event_cluster_artifact = _read_blind_artifact(event_cluster_path)
    cluster_rows = _blind_jsonl_dict_rows(event_cluster_artifact)
    provider_checkpoint_commitment = _verify_provider_checkpoint_authenticity(
        active_analyzer,
        event_clustering=event_clustering,
        cutoff_at=cutoff_at,
        nodes=nodes,
        node_states=node_states,
        novelty=novelty,
        shared_manifest=shared_manifest,
        cluster_rows=cluster_rows,
    )
    material_cluster_ids = [cluster.cluster_id for cluster in event_clustering.material_clusters]
    low_signal_cluster_ids = [cluster.cluster_id for cluster in event_clustering.clusters if not cluster.material]
    prompt_hashes = {
        "open_world_map_reduce": aggregate_open_world_prompt_sha256,
        "news_novelty_review": novelty_prompt_sha256,
        "news_novelty_review_batches": _aggregate_hash(novelty_prompt_batch_hashes),
        "provider_checkpoint_commitment_root": provider_checkpoint_commitment.root_sha256,
    }
    prompt_sha256_root = _prompt_sha256_root(
        map_reduce_prompt_sha256s=[node.prompt_sha256 for node in nodes],
        novelty_batch_prompt_root_sha256=prompt_hashes["news_novelty_review_batches"],
    )
    component_references = {
        "event_clustering_result": _reference(root, event_clustering_path),
        "row_disposition_ledger": _reference(root, row_disposition_path),
        "event_cluster_ledger": _reference(root, event_cluster_path),
        "news_coverage_manifest": _reference(root, news_coverage_path),
        "event_cluster_manifest": _reference(root, event_manifest_path),
        "open_world_first_analysis": _reference(
            root,
            root / str(shared_manifest.open_world_first_analysis_artifact),
        ),
        "news_novelty_review": _reference(
            root,
            root / str(shared_manifest.news_novelty_review_artifact),
        ),
        "downstream_digest": _reference(root, downstream_digest_path),
        "d_minus_one_safe_context": d_minus_one_reference,
        **{f"map_reduce_output:{node.node_id}": node.output for node in nodes},
    }
    component_artifact_root_sha256 = _component_artifact_root_sha256(
        references=component_references,
        map_reduce_nodes=nodes,
    )
    downstream_digest_payload_sha256 = sha256_text(canonical_json(downstream_digest.model_dump(mode="json")))
    context = SharedPreRetrievalContext(
        context_id=context_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        news_sha256=full_batch.sha256,
        provider=profile.provider,
        model=profile.model,
        reasoning_effort=profile.reasoning_effort,
        code_semantic_version=SHARED_PRE_RETRIEVAL_VERSION,
        parsed_news_root_sha256=parsed_news_root_sha256,
        input_cluster_root_sha256=input_cluster_root_sha256,
        prompt_sha256_root=prompt_sha256_root,
        component_artifact_root_sha256=component_artifact_root_sha256,
        downstream_digest_payload_sha256=(downstream_digest_payload_sha256),
        source_row_ids=source_row_ids,
        event_cluster_ids=[cluster.cluster_id for cluster in event_clustering.clusters],
        material_cluster_ids=material_cluster_ids,
        low_signal_cluster_ids=low_signal_cluster_ids,
        event_clustering_result=component_references["event_clustering_result"],
        row_disposition_ledger=component_references["row_disposition_ledger"],
        event_cluster_ledger=component_references["event_cluster_ledger"],
        news_coverage_manifest=component_references["news_coverage_manifest"],
        event_cluster_manifest=component_references["event_cluster_manifest"],
        open_world_first_analysis=component_references["open_world_first_analysis"],
        news_novelty_review=component_references["news_novelty_review"],
        downstream_digest=component_references["downstream_digest"],
        d_minus_one_safe_context=d_minus_one_reference,
        map_reduce_nodes=nodes,
        root_node_id=root_state.contract.node_id,
        prompt_hashes=prompt_hashes,
        logical_llm_call_count=provider_checkpoint_commitment.checkpoint_count,
        novelty_logical_llm_call_count=(provider_checkpoint_commitment.novelty_checkpoint_count),
        provider_checkpoint_commitment_count=(provider_checkpoint_commitment.checkpoint_count),
        committed_prompt_tokens_estimate=(provider_checkpoint_commitment.prompt_tokens_estimate),
        committed_completion_tokens_estimate=(provider_checkpoint_commitment.completion_tokens_estimate),
    )
    _write_immutable_json(context_path, context.model_dump(mode="json"))
    context_reference = _reference(root, context_path)
    content_identity_sha256 = _content_identity_sha256(
        lookup_identity_sha256=lookup_identity_sha256,
        parsed_news_root_sha256=parsed_news_root_sha256,
        input_cluster_root_sha256=input_cluster_root_sha256,
        prompt_sha256_root=prompt_sha256_root,
        component_artifact_root_sha256=component_artifact_root_sha256,
        downstream_digest_payload_sha256=(downstream_digest_payload_sha256),
        context_payload_sha256=context_reference.sha256,
    )
    manifest = SharedPreRetrievalContextManifest(
        context_id=context_id,
        identity_sha256=content_identity_sha256,
        lookup_identity_sha256=lookup_identity_sha256,
        code_semantic_version=SHARED_PRE_RETRIEVAL_VERSION,
        parsed_news_root_sha256=parsed_news_root_sha256,
        input_cluster_root_sha256=input_cluster_root_sha256,
        prompt_sha256_root=prompt_sha256_root,
        component_artifact_root_sha256=component_artifact_root_sha256,
        downstream_digest_payload_sha256=(downstream_digest_payload_sha256),
        context=context_reference,
        news_sha256=full_batch.sha256,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        source_row_count=len(source_row_ids),
        event_cluster_count=len(event_clustering.clusters),
        material_cluster_count=len(material_cluster_ids),
        low_signal_cluster_count=len(low_signal_cluster_ids),
        source_row_root_sha256=sha256_text(canonical_json(source_row_ids)),
        event_cluster_root_sha256=sha256_text(canonical_json(context.event_cluster_ids)),
        material_cluster_root_sha256=sha256_text(canonical_json(material_cluster_ids)),
    )
    _write_immutable_json(manifest_path, manifest.model_dump(mode="json"))
    return SharedPreRetrievalBuildResult(
        context=context,
        context_path=context_path,
        manifest=manifest,
        manifest_path=manifest_path,
        news_batch=full_batch,
        cache_hit=False,
    )


def _deterministic_clustering_batch(batch: NewsBatch) -> NewsBatch:
    return NewsBatch(
        path=batch.path,
        sha256=batch.sha256,
        trade_date=batch.trade_date,
        items=[
            item.model_copy(
                update={
                    "provenance": [
                        provenance.model_copy(update={"observed_at": item.published_at})
                        for provenance in item.provenance
                    ]
                }
            )
            for item in batch.items
        ],
    )


_DOWNSTREAM_NOVELTY_FIELDS = (
    "cluster_id",
    "cluster_index",
    "event_ids",
    "evidence_source_ids",
    "row_numbers",
    "novelty",
    "time_verified",
    "first_public_evidence_at",
    "customer",
    "period",
    "approval_stage",
    "contract_stage",
    "attributable_amount",
    "after_hours_new_disclosure",
    "recycled_news",
)


def _shared_downstream_digest(
    *,
    context_id: str,
    trade_date: date,
    cutoff_at: datetime,
    material_cluster_ids: list[str],
    root_state: _NodeState,
    open_world_source: QualityArtifactReference,
    novelty: NewsNoveltyReview,
    novelty_source: QualityArtifactReference,
) -> SharedDownstreamDigest:
    raw_findings = [finding.model_dump(mode="json") for finding in novelty.findings]
    all_fields = sorted({key for finding in raw_findings for key in finding})
    omitted_fields = sorted(set(all_fields) - set(_DOWNSTREAM_NOVELTY_FIELDS))
    projections: list[dict[str, Any]] = []
    for finding in raw_findings:
        omitted = {key: finding[key] for key in omitted_fields if key in finding}
        projection = {key: finding[key] for key in _DOWNSTREAM_NOVELTY_FIELDS if key in finding}
        projection["omitted_payload_sha256"] = sha256_text(canonical_json(omitted))
        projections.append(projection)
    return SharedDownstreamDigest(
        context_id=context_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        material_cluster_ids=material_cluster_ids,
        material_cluster_root_sha256=sha256_text(canonical_json(material_cluster_ids)),
        open_world_source=open_world_source,
        novelty_source=novelty_source,
        open_world_root=_reduction_projection(root_state),
        novelty_projection_fields=list(_DOWNSTREAM_NOVELTY_FIELDS),
        novelty_omitted_fields=omitted_fields,
        novelty_findings=projections,
    )


async def _cluster_once(
    settings: Settings,
    *,
    analyzer: DailyAnalyzer,
    full_batch: Any,
    trade_date: date,
    cutoff_at: datetime,
) -> EventClusteringResult:
    try:
        return await event_clustering_module.cluster_news_events(
            full_batch.items,
            window_start_at=default_news_window_start(trade_date),
            cutoff_at=cutoff_at,
            embedding_provider=analyzer.embedding_provider,
            embedding_batch_size=(settings.limits.event_cluster_embedding_batch_size),
            similarity_threshold=(settings.limits.event_cluster_similarity_threshold),
            max_semantic_variants=(settings.limits.event_cluster_max_semantic_variants),
            fallback_policy=settings.event_cluster_fallback_policy,
            max_retries=settings.llm.max_retries,
            production_runtime_identity=(
                production_embedding_method(
                    settings,
                    analyzer.embedding_provider,
                )
                if settings.event_cluster_fallback_policy.value == "fail-closed"
                else None
            ),
        )
    except ProductionEmbeddingUnavailableError:
        raise


def _shared_component_manifest(
    analyzer: DailyAnalyzer,
    *,
    result: EventClusteringResult,
    trade_date: date,
    cutoff_at: datetime,
    news_csv: Path,
    news_sha256: str,
    run_seed: str,
    event_cluster_path: Path,
    event_cluster_sha256: str | None = None,
) -> ContextManifest:
    manifest = ContextAssembler(
        analyzer.root,
        shard_episode_count=analyzer.settings.limits.shard_episode_count,
    ).assemble(
        mode="exhaustive",
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        run_seed=run_seed,
        retrieved_episode_ids=[],
        retrieved_record_ids=[],
        web_queries=[],
    )
    manifest.news_file = relative_to_root(news_csv, analyzer.root)
    manifest.news_sha256 = news_sha256
    manifest.news_window_start_at = default_news_window_start(trade_date)
    manifest.news_window_end_at = cutoff_at
    manifest.news_row_count = result.input_row_count
    manifest.included_news_row_count = result.cutoff_safe_row_count
    manifest.excluded_news_row_count = result.audit_only_row_count
    manifest.blind_context_mode = "CSV_MEMORY_ONLY_STRICT"
    manifest.evidence_policy = "csv-memory-only-strict"
    manifest.web_provider = "disabled"
    manifest.web_required = False
    manifest.event_cluster_artifact = relative_to_root(
        event_cluster_path,
        analyzer.root,
    )
    manifest.event_cluster_sha256 = (
        event_cluster_sha256 if event_cluster_sha256 is not None else file_sha256(event_cluster_path)
    )
    manifest.event_cluster_count = len(result.material_clusters)
    manifest.event_cluster_summary = {
        "source_row_count": result.cutoff_safe_row_count,
        "cluster_count": len(result.material_clusters),
        "cluster_method": result.clustering_version,
        "shared_pre_retrieval": True,
    }
    manifest.event_clustering_result_sha256 = sha256_text(
        canonical_json(event_clustering_module.event_clustering_payload(result))
    )
    manifest.price_snapshot.source_name = analyzer._blind_price_source_name()
    manifest.price_snapshot.source_ref = analyzer._blind_price_source_ref()
    return manifest


def _write_open_world_first_analysis_artifact(
    analyzer: DailyAnalyzer,
    *,
    analysis: OpenWorldFirstAnalysis,
    manifest: ContextManifest,
    prompt_sha256: str,
    cutoff_at: datetime,
) -> None:
    normalized = analysis.model_copy(
        update={
            "run_id": manifest.run_id,
            "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
            "prompt_sha256": prompt_sha256,
            "cutoff_at": cutoff_at,
        }
    )
    artifact_relative = (
        Path("runs") / "checkpoints" / "open_world_first_analysis" / manifest.run_id / "open_world_first_analysis.json"
    )
    artifact_path = analyzer.root / artifact_relative
    _write_immutable_json(artifact_path, normalized.model_dump(mode="json"))
    manifest.open_world_first_analysis_artifact = artifact_relative.as_posix()
    manifest.open_world_first_analysis_sha256 = file_sha256(artifact_path)
    manifest.open_world_first_analysis_summary = {
        "source_cluster_count": len(normalized.source_cluster_ids),
        "analyzed_cluster_count": len(normalized.analyzed_cluster_ids),
        "uncovered_cluster_count": len(normalized.uncovered_cluster_ids),
        "analysis_batch_count": normalized.analysis_batch_count,
        "cluster_finding_count": len(normalized.cluster_findings),
        "event_cluster_count": len(normalized.event_clusters),
        "direct_company_event_count": len(normalized.direct_company_events),
        "policy_industry_event_count": len(normalized.policy_industry_events),
        "mechanism_count": len(normalized.mechanisms),
        "transmission_path_count": len(normalized.beneficiary_transmission_paths),
        "narrative_conversion_point_count": len(normalized.narrative_conversion_points),
        "direct_candidate_count": len(normalized.direct_candidates),
        "potential_sector_count": len(normalized.potential_sectors),
        "investigation_question_count": len(normalized.beneficiary_investigation_questions),
        "uncertainty_count": len(normalized.uncertainties),
    }


async def _run_shared_novelty_review(
    analyzer: DailyAnalyzer,
    *,
    manifest: ContextManifest,
    cutoff_at: datetime,
    output_dir: Path,
) -> tuple[NewsNoveltyReview, str, int, list[str]]:
    cluster_rows = analyzer._read_event_cluster_context(manifest)
    batches = _novelty_batches(
        analyzer,
        manifest=manifest,
        cutoff_at=cutoff_at,
        cluster_rows=cluster_rows,
    )
    partial_reviews: list[NewsNoveltyReview] = []
    prompt_hashes: list[str] = []
    prompt_tokens = 0
    for batch_index, batch_rows in enumerate(batches, start=1):
        prompt = analyzer._build_news_novelty_review_prompt(
            cluster_rows=batch_rows,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        prompt_hashes.append(prompt_sha256)
        purpose = f"shared_news_novelty_review.batch_{batch_index:04d}"
        review = _replay_provider_checkpoint_if_available(
            analyzer,
            prompt=prompt,
            purpose=purpose,
            response_model=NewsNoveltyReview,
        )
        if review is None:
            review = await analyzer.llm.generate_structured(
                prompt=prompt,
                response_model=NewsNoveltyReview,
                purpose=purpose,
            )
        prompt_tokens += count_provider_tokens(analyzer.llm, prompt)
        partial_reviews.append(
            analyzer._normalize_news_novelty_review(
                review,
                manifest=manifest,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
                cluster_rows=batch_rows,
            )
        )
    findings = sorted(
        [finding for review in partial_reviews for finding in review.findings],
        key=lambda finding: finding.cluster_index,
    )
    expected_cluster_ids = [str(row["cluster_id"]) for row in cluster_rows]
    if [finding.cluster_id for finding in findings] != expected_cluster_ids:
        raise OpenWorldCoverageError("shared novelty review changed complete cluster coverage")
    aggregate_prompt_sha256 = _aggregate_hash(prompt_hashes)
    normalized = NewsNoveltyReview(
        run_id=manifest.run_id,
        prompt_version=NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
        prompt_sha256=aggregate_prompt_sha256,
        created_at=max(review.created_at for review in partial_reviews),
        cutoff_at=cutoff_at,
        review_mode=manifest.blind_context_mode,
        cluster_count=len(cluster_rows),
        reviewed_cluster_count=len(findings),
        findings=findings,
        excluded_after_cutoff_source_ids=manifest.excluded_web_source_ids,
        notes=_unique_strings(note for review in partial_reviews for note in review.notes),
    )
    artifact_path = output_dir / "news_novelty_review.json"
    _write_immutable_json(artifact_path, normalized.model_dump(mode="json"))
    novelty_counts = {
        label.value: sum(finding.novelty == label for finding in normalized.findings) for label in NewsNoveltyLabel
    }
    manifest.news_novelty_review_artifact = relative_to_root(
        artifact_path,
        analyzer.root,
    )
    manifest.news_novelty_review_sha256 = file_sha256(artifact_path)
    manifest.news_novelty_review_count = normalized.reviewed_cluster_count
    manifest.news_novelty_review_summary = {
        "cluster_count": normalized.cluster_count,
        "reviewed_cluster_count": normalized.reviewed_cluster_count,
        "review_mode": normalized.review_mode,
        "novelty_counts": novelty_counts,
        "packing_policy": "CONTEXT_CHARS_AND_CONFIGURED_CLUSTER_LIMIT.v2",
        "batch_count": len(batches),
    }
    return normalized, aggregate_prompt_sha256, prompt_tokens, prompt_hashes


def _novelty_batches(
    analyzer: DailyAnalyzer,
    *,
    manifest: ContextManifest,
    cutoff_at: datetime,
    cluster_rows: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    max_chars = max(1, analyzer.settings.limits.open_world_max_prompt_chars)
    max_clusters = max(1, analyzer.settings.limits.novelty_cluster_batch_size)
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in cluster_rows:
        single_prompt = analyzer._build_news_novelty_review_prompt(
            cluster_rows=[row],
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        if len(single_prompt) > max_chars:
            raise OpenWorldCoverageError("one explicit novelty capsule exceeds the shared prompt budget")
        tentative = [*current, row]
        tentative_prompt = analyzer._build_news_novelty_review_prompt(
            cluster_rows=tentative,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        if current and (
            len(current) >= max_clusters
            or len(tentative_prompt) > max_chars
        ):
            batches.append(current)
            current = []
        current.append(row)
    if current:
        batches.append(current)
    if [str(row["cluster_id"]) for batch in batches for row in batch] != [
        str(row["cluster_id"]) for row in cluster_rows
    ]:
        raise OpenWorldCoverageError("shared novelty batching changed cluster coverage")
    return batches


async def _run_map_reduce(
    analyzer: DailyAnalyzer,
    *,
    clusters: list[OpenWorldClusterInput],
    cutoff_at: datetime,
    output_dir: Path,
) -> tuple[list[SharedMapReduceNode], _NodeState, OpenWorldFirstAnalysis]:
    if not clusters:
        raise OpenWorldCoverageError("shared pre-retrieval requires at least one material cluster")
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    map_batches = _map_batches(analyzer, clusters=clusters, cutoff_at=cutoff_at)
    states: list[_NodeState] = []
    all_contracts: list[SharedMapReduceNode] = []
    map_states: list[_NodeState] = []
    for batch_index, batch in enumerate(map_batches, start=1):
        cluster_ids = [cluster.cluster_id for cluster in batch]
        node_id = stable_id(
            "OWMAP",
            canonical_json(cluster_ids),
            length=16,
        )
        prompt = _map_prompt(
            node_id=node_id,
            clusters=batch,
            cutoff_at=cutoff_at,
        )
        output, receipt = await _map_call(
            analyzer,
            prompt=prompt,
            purpose=f"shared_open_world_map.batch_{batch_index:04d}",
            clusters=batch,
            cutoff_at=cutoff_at,
        )
        output_path = output_dir / "map_reduce" / f"{node_id}.json"
        _write_immutable_json(output_path, output.model_dump(mode="json"))
        contract = SharedMapReduceNode(
            node_id=node_id,
            level=0,
            kind="MAP",
            covered_cluster_ids=cluster_ids,
            prompt_sha256=sha256_text(prompt),
            output=_reference(analyzer.root, output_path),
            prompt_tokens=receipt.prompt_tokens,
            completion_tokens=receipt.completion_tokens,
            checkpoint_hit=False,
            live_call_count=1,
        )
        state = _NodeState(contract=contract, output=output)
        states.append(state)
        map_states.append(state)
        all_contracts.append(contract)

    level = 1
    while len(states) > 1:
        next_states: list[_NodeState] = []
        batches = _reduce_batches(
            analyzer,
            states=states,
            level=level,
            cutoff_at=cutoff_at,
        )
        for batch_index, children in enumerate(batches, start=1):
            child_ids = [child.contract.node_id for child in children]
            covered_cluster_ids = [
                cluster_id for child in children for cluster_id in child.contract.covered_cluster_ids
            ]
            if len(covered_cluster_ids) != len(set(covered_cluster_ids)):
                raise OpenWorldCoverageError("shared reduce children overlap material cluster coverage")
            node_id = stable_id(
                "OWREDUCE",
                canonical_json([level, child_ids]),
                length=16,
            )
            prompt = _reduce_prompt(
                node_id=node_id,
                children=children,
                cutoff_at=cutoff_at,
            )
            reduce_output, receipt = await _reduce_call(
                analyzer,
                prompt=prompt,
                purpose=(f"shared_open_world_reduce.level_{level:02d}.batch_{batch_index:04d}"),
                node_id=node_id,
                child_node_ids=child_ids,
                covered_cluster_ids=covered_cluster_ids,
            )
            output_path = output_dir / "map_reduce" / f"{node_id}.json"
            _write_immutable_json(
                output_path,
                reduce_output.model_dump(mode="json"),
            )
            contract = SharedMapReduceNode(
                node_id=node_id,
                level=level,
                kind="REDUCE",
                child_node_ids=child_ids,
                covered_cluster_ids=covered_cluster_ids,
                prompt_sha256=sha256_text(prompt),
                output=_reference(analyzer.root, output_path),
                prompt_tokens=receipt.prompt_tokens,
                completion_tokens=receipt.completion_tokens,
                checkpoint_hit=False,
                live_call_count=1,
            )
            next_states.append(_NodeState(contract=contract, output=reduce_output))
            all_contracts.append(contract)
        if len(next_states) >= len(states):
            raise OpenWorldCoverageError("shared reduce tree did not reduce the child population")
        states = next_states
        level += 1

    root_state = states[0]
    required_cluster_ids = [cluster.cluster_id for cluster in clusters]
    if root_state.contract.covered_cluster_ids != required_cluster_ids:
        raise OpenWorldCoverageError("shared reduce root does not preserve material cluster order")
    map_findings = [finding for state in map_states for finding in _map_output(state).cluster_findings]
    if [finding.cluster_id for finding in map_findings] != required_cluster_ids:
        raise OpenWorldCoverageError("shared map findings do not cover every material cluster exactly once")
    root_output = root_state.output
    final = OpenWorldFirstAnalysis(
        run_id="RUN-shared-pre-retrieval-pending",
        prompt_version=OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
        prompt_sha256=_aggregate_hash([node.prompt_sha256 for node in all_contracts]),
        created_at=max(_map_output(state).created_at for state in map_states),
        cutoff_at=cutoff_at,
        event_ids=[event_id for cluster_id in required_cluster_ids for event_id in cluster_by_id[cluster_id].event_ids],
        source_cluster_ids=required_cluster_ids,
        analyzed_cluster_ids=required_cluster_ids,
        uncovered_cluster_ids=[],
        analysis_batch_count=len(map_states),
        cluster_findings=map_findings,
        event_clusters=list(root_output.event_clusters),
        direct_company_events=list(root_output.direct_company_events),
        policy_industry_events=list(root_output.policy_industry_events),
        mechanisms=list(root_output.mechanisms),
        beneficiary_transmission_paths=list(root_output.beneficiary_transmission_paths),
        narrative_conversion_points=list(root_output.narrative_conversion_points),
        direct_candidates=list(root_output.direct_candidates),
        potential_sectors=list(root_output.potential_sectors),
        beneficiary_investigation_questions=list(root_output.beneficiary_investigation_questions),
        uncertainties=list(root_output.uncertainties),
        notes=[
            *root_output.notes,
            "All material clusters are closed through the shared map/reduce tree.",
        ],
    )
    return all_contracts, root_state, final


def _map_output(state: _NodeState) -> OpenWorldFirstAnalysis:
    if not isinstance(state.output, OpenWorldFirstAnalysis):
        raise OpenWorldCoverageError("shared map node has a non-map output")
    return state.output


async def _map_call(
    analyzer: DailyAnalyzer,
    *,
    prompt: str,
    purpose: str,
    clusters: list[OpenWorldClusterInput],
    cutoff_at: datetime,
) -> tuple[OpenWorldFirstAnalysis, _CallReceipt]:
    before = _trace_paths(analyzer.settings)
    output = _replay_provider_checkpoint_if_available(
        analyzer,
        prompt=prompt,
        purpose=purpose,
        response_model=OpenWorldFirstAnalysis,
    )
    if output is None:
        output = await analyzer.llm.generate_structured(
            prompt=prompt,
            response_model=OpenWorldFirstAnalysis,
            purpose=purpose,
        )
    cluster_ids = [cluster.cluster_id for cluster in clusters]
    output = analyzer._normalize_open_world_first_analysis(
        output,
        news_texts=[cluster.representative_text for cluster in clusters],
        event_ids=[event_id for cluster in clusters for event_id in cluster.event_ids],
        cluster_ids=cluster_ids,
        cutoff_at=cutoff_at,
        prompt_sha256=sha256_text(prompt),
    )
    trace = _single_new_trace(analyzer.settings, before=before, purpose=purpose)
    return output, _call_receipt(trace)


async def _reduce_call(
    analyzer: DailyAnalyzer,
    *,
    prompt: str,
    purpose: str,
    node_id: str,
    child_node_ids: list[str],
    covered_cluster_ids: list[str],
) -> tuple[SharedOpenWorldReduceOutput, _CallReceipt]:
    before = _trace_paths(analyzer.settings)
    output = _replay_provider_checkpoint_if_available(
        analyzer,
        prompt=prompt,
        purpose=purpose,
        response_model=SharedOpenWorldReduceOutput,
    )
    if output is None:
        output = await analyzer.llm.generate_structured(
            prompt=prompt,
            response_model=SharedOpenWorldReduceOutput,
            purpose=purpose,
        )
    output = _normalize_reduce_output(
        output,
        node_id=node_id,
        child_node_ids=child_node_ids,
        covered_cluster_ids=covered_cluster_ids,
    )
    trace = _single_new_trace(analyzer.settings, before=before, purpose=purpose)
    return output, _call_receipt(trace)


def _normalize_reduce_output(
    output: SharedOpenWorldReduceOutput,
    *,
    node_id: str,
    child_node_ids: list[str],
    covered_cluster_ids: list[str],
) -> SharedOpenWorldReduceOutput:
    model_identity_sha256 = sha256_text(
        canonical_json(
            {
                "node_id": output.node_id,
                "child_node_ids": output.child_node_ids,
                "covered_cluster_ids": output.covered_cluster_ids,
            }
        )
    )
    return SharedOpenWorldReduceOutput.model_validate(
        {
            **output.model_dump(mode="json"),
            "node_id": node_id,
            "child_node_ids": child_node_ids,
            "covered_cluster_ids": covered_cluster_ids,
            "notes": [
                *output.notes,
                (
                    "Coverage identity is bound from the deterministic child ledger; "
                    f"the untrusted model echo is committed as {model_identity_sha256}."
                ),
            ],
        }
    )


def _map_batches(
    analyzer: DailyAnalyzer,
    *,
    clusters: list[OpenWorldClusterInput],
    cutoff_at: datetime,
) -> list[list[OpenWorldClusterInput]]:
    batches: list[list[OpenWorldClusterInput]] = []
    current: list[OpenWorldClusterInput] = []
    max_chars = max(1, analyzer.settings.limits.open_world_max_prompt_chars)
    max_clusters = max(1, analyzer.settings.limits.open_world_cluster_batch_size)
    for cluster in clusters:
        if len(_map_prompt(node_id="probe", clusters=[cluster], cutoff_at=cutoff_at)) > max_chars:
            raise OpenWorldCoverageError("one explicit cluster capsule exceeds the shared prompt budget")
        tentative = [*current, cluster]
        if current and (
            len(current) >= max_clusters
            or
            len(
                _map_prompt(
                    node_id="probe",
                    clusters=tentative,
                    cutoff_at=cutoff_at,
                )
            )
            > max_chars
        ):
            batches.append(current)
            current = []
        current.append(cluster)
    if current:
        batches.append(current)
    if [cluster.cluster_id for batch in batches for cluster in batch] != [cluster.cluster_id for cluster in clusters]:
        raise OpenWorldCoverageError("shared map batching changed cluster coverage")
    return batches


def _reduce_batches(
    analyzer: DailyAnalyzer,
    *,
    states: list[_NodeState],
    level: int,
    cutoff_at: datetime,
) -> list[list[_NodeState]]:
    max_chars = max(1, analyzer.settings.limits.open_world_max_prompt_chars)
    batches: list[list[_NodeState]] = []
    current: list[_NodeState] = []
    for state in states:
        tentative = [*current, state]
        prompt_chars = len(
            _reduce_prompt(
                node_id=f"probe-{level}",
                children=tentative,
                cutoff_at=cutoff_at,
            )
        )
        if current and prompt_chars > max_chars:
            batches.append(current)
            current = []
        current.append(state)
    if current:
        batches.append(current)
    if any(len(batch) == 1 for batch in batches) and len(batches) == len(states):
        raise OpenWorldCoverageError("shared reduce summaries cannot fit a reducing batch")
    return batches


def _map_prompt(
    *,
    node_id: str,
    clusters: list[OpenWorldClusterInput],
    cutoff_at: datetime,
) -> str:
    payload = {
        "schema": "nslab.shared_open_world_map.v1",
        "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
        "capsule_version": SHARED_CAPSULE_VERSION,
        "node_id": node_id,
        "cutoff_at": cutoff_at.isoformat(),
        "current_event_clusters": [_cluster_capsule(cluster) for cluster in clusters],
        "required_cluster_ids": [cluster.cluster_id for cluster in clusters],
        "forbidden_inputs": [
            "past research search results",
            "semantic retrieval hits",
            "D-day prices or outcomes",
            "cutoff-after evidence",
        ],
        "coverage_contract": {
            "first_n_shortcut": False,
            "silent_truncation": False,
            "every_cluster_requires_one_finding": True,
            "compressed_sentence_hashes_are_auditable_not_silently_dropped": True,
        },
    }
    return (
        "Run one shared pre-retrieval open-world MAP node as "
        "OpenWorldFirstAnalysis. Analyze every required_cluster_id exactly once. "
        "The cluster capsules preserve every row identity and explicitly list hashes "
        "for compressed prose. Do not use memory, outcomes, or cutoff-after evidence. "
        "Return source_cluster_ids and analyzed_cluster_ids in the required order, no "
        "uncovered IDs, analysis_batch_count=1, and one cluster_finding per cluster.\n"
        "---OPEN_WORLD_FIRST_ANALYSIS_PAYLOAD---\n"
        f"{canonical_json(payload)}"
    )


def _reduce_prompt(
    *,
    node_id: str,
    children: list[_NodeState],
    cutoff_at: datetime,
) -> str:
    required_cluster_ids = [cluster_id for child in children for cluster_id in child.contract.covered_cluster_ids]
    payload = {
        "schema": "nslab.shared_open_world_reduce.v1",
        "prompt_version": SHARED_REDUCE_PROMPT_VERSION,
        "node_id": node_id,
        "cutoff_at": cutoff_at.isoformat(),
        "required_child_node_ids": [child.contract.node_id for child in children],
        "required_cluster_ids": required_cluster_ids,
        "children": [_reduction_projection(child) for child in children],
        "requirements": [
            "preserve every child_node_id and covered cluster ID in order",
            "synthesize mechanisms without dropping contradiction or uncertainty",
            "do not introduce memory, outcomes, or cutoff-after evidence",
        ],
    }
    return (
        "Reduce the child open-world summaries into SharedOpenWorldReduceOutput. "
        "Return node_id, child_node_ids, and covered_cluster_ids exactly as required. "
        "This is a complete tree reduction, not a first-N summary.\n"
        "---SHARED_OPEN_WORLD_REDUCE_PAYLOAD---\n"
        f"{canonical_json(payload)}"
    )


def _reduction_projection(state: _NodeState) -> dict[str, Any]:
    output = state.output
    return {
        "node_id": state.contract.node_id,
        "covered_cluster_ids": state.contract.covered_cluster_ids,
        "event_clusters": output.event_clusters,
        "direct_company_events": output.direct_company_events,
        "policy_industry_events": output.policy_industry_events,
        "mechanisms": output.mechanisms,
        "beneficiary_transmission_paths": output.beneficiary_transmission_paths,
        "narrative_conversion_points": output.narrative_conversion_points,
        "direct_candidates": output.direct_candidates,
        "potential_sectors": output.potential_sectors,
        "beneficiary_investigation_questions": (output.beneficiary_investigation_questions),
        "uncertainties": output.uncertainties,
        "notes": output.notes,
    }


def _cluster_capsule(cluster: OpenWorldClusterInput) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "event_ids": list(cluster.event_ids),
        "row_numbers": list(cluster.row_numbers),
        "representative_news": _news_capsule(cluster.representative_text),
        "member_news": [_news_capsule(text) for text in cluster.member_news],
        "member_news_sha256": [sha256_text(text) for text in cluster.member_news],
        "member_count": len(cluster.event_ids),
        "compression_policy": (
            "FULL_TITLE_PLUS_STRUCTURAL_AND_FACT_BEARING_SENTENCES_WITH_EXPLICIT_OMITTED_SENTENCE_HASH_LEDGER"
        ),
    }


def _news_capsule(text: str) -> str:
    lines = text.splitlines()
    title = lines[0].strip() if lines else ""
    body = "\n".join(lines[1:]).strip()
    sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(body) if sentence.strip()]
    selected_indexes = {index for index, sentence in enumerate(sentences) if _SALIENT_SENTENCE.search(sentence)}
    if sentences:
        selected_indexes.update({0, len(sentences) - 1})
    selected = [sentences[index] for index in sorted(selected_indexes)]
    omitted = [sha256_text(sentence) for index, sentence in enumerate(sentences) if index not in selected_indexes]
    payload = {
        "title": title,
        "body_sha256": sha256_text(body),
        "body_char_count": len(body),
        "sentence_count": len(sentences),
        "selected_sentences": selected,
        "omitted_sentence_sha256": omitted,
        "omitted_sentence_count": len(omitted),
    }
    return canonical_json(payload)


def _write_coverage_artifacts(
    *,
    row_disposition_path: Path,
    event_cluster_path: Path,
    news_coverage_path: Path,
    event_manifest_path: Path,
    result: EventClusteringResult,
    trade_date: date,
    cutoff_at: datetime,
    news_sha256: str,
    run_id: str,
    settings: Settings,
) -> list[str]:
    row_rows: list[dict[str, Any]] = []
    cluster_rows: list[dict[str, Any]] = []
    source_row_ids: list[str] = []
    assigned_rows: set[int] = set()
    coverage_rows: list[NewsRowCoverage] = []
    cluster_entries: list[EventClusterEntry] = []
    for cluster_index, cluster in enumerate(result.clusters, start=1):
        for member_index, item in enumerate(cluster.members):
            row_id = f"ROW-{item.row_number:06d}-{item.source_id}"
            source_row_ids.append(row_id)
            if item.row_number in assigned_rows:
                raise OpenWorldCoverageError("shared coverage assigned one news row more than once")
            assigned_rows.add(item.row_number)
            row_rows.append(
                {
                    "schema_version": "nslab.shared_row_disposition.v1",
                    "row_id": row_id,
                    "row_number": item.row_number,
                    "event_id": item.event_id,
                    "source_id": item.source_id,
                    "cluster_id": cluster.cluster_id,
                    "disposition": cluster.disposition,
                    "published_at": item.published_at.isoformat(),
                    "cutoff_at": cutoff_at.isoformat(),
                    "eligible_for_blind_evidence": cluster.material,
                    "title_sha256": sha256_text(item.title),
                    "body_sha256": sha256_text(item.body),
                }
            )
            coverage_rows.append(
                NewsRowCoverage(
                    row_number=item.row_number,
                    event_id=item.event_id,
                    source_id=item.source_id,
                    primary_cluster_id=(cluster.cluster_id if member_index == 0 else None),
                    duplicate_parent_cluster_id=(cluster.cluster_id if member_index > 0 else None),
                    disposition=(cluster.disposition if member_index == 0 else "DUPLICATE"),
                )
            )
        published = sorted(item.published_at for item in cluster.members)
        cluster_rows.append(
            {
                "schema_version": SHARED_CAPSULE_VERSION,
                "cluster_id": cluster.cluster_id,
                "cluster_index": cluster_index,
                "cluster_method": result.clustering_version,
                "row_numbers": [item.row_number for item in cluster.members],
                "event_ids": [item.event_id for item in cluster.members],
                "source_ids": [item.source_id for item in cluster.members],
                "row_count": len(cluster.members),
                "disposition": cluster.disposition,
                "eligible_for_blind_evidence": cluster.material,
                "first_published_at": published[0].isoformat(),
                "cutoff_at": cutoff_at.isoformat(),
                "representative_title_excerpt": cluster.representative.title,
                "representative_body_excerpt": _news_capsule(cluster.representative.combined_text),
                "member_capsules": [
                    {
                        "row_number": item.row_number,
                        "event_id": item.event_id,
                        "news_sha256": sha256_text(item.combined_text),
                        "capsule": _news_capsule(item.combined_text),
                    }
                    for item in cluster.members
                ],
                "first_n_shortcut_used": False,
                "silent_truncation_used": False,
            }
        )
        cluster_entries.append(
            EventClusterEntry(
                cluster_id=cluster.cluster_id,
                representative_event_id=cluster.representative.event_id,
                member_event_ids=[item.event_id for item in cluster.members],
                member_source_ids=[item.source_id for item in cluster.members],
                member_row_numbers=[item.row_number for item in cluster.members],
                disposition=cluster.disposition,
                exact_duplicate_count=cluster.exact_duplicate_count,
                semantic_duplicate_count=cluster.semantic_duplicate_count,
                cluster_signature_sha256=cluster.cluster_signature_sha256,
            )
        )
    if len(assigned_rows) != result.input_row_count:
        raise OpenWorldCoverageError("shared row coverage does not equal the input news population")
    _write_immutable_bytes(
        row_disposition_path,
        "".join(canonical_json(row) + "\n" for row in row_rows).encode("utf-8"),
    )
    _write_immutable_bytes(
        event_cluster_path,
        "".join(canonical_json(row) + "\n" for row in cluster_rows).encode("utf-8"),
    )
    coverage_rows.sort(key=lambda row: row.row_number)
    disposition_counts = dict(sorted(Counter(row.disposition for row in coverage_rows).items()))
    _write_immutable_json(
        news_coverage_path,
        NewsCoverageManifest(
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            input_news_sha256=news_sha256,
            input_row_count=result.input_row_count,
            covered_row_count=len(coverage_rows),
            missing_row_count=0,
            duplicate_assignment_count=disposition_counts.get("DUPLICATE", 0),
            disposition_counts=disposition_counts,
            row_coverage_sha256=sha256_text(canonical_json([row.model_dump(mode="json") for row in coverage_rows])),
            rows=coverage_rows,
        ).model_dump(mode="json"),
    )
    _write_immutable_json(
        event_manifest_path,
        EventClusterManifest(
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            clustering_version=result.clustering_version,
            embedding_provider=result.embedding_method,
            embedding_status=result.embedding_status,
            embedding_model=result.embedding_model,
            embedding_revision=result.embedding_revision,
            embedding_artifact_sha256=result.embedding_artifact_sha256,
            embedding_dimensions=result.embedding_dimensions,
            embedding_fallback_policy=result.embedding_fallback_policy,
            deterministic_fallback_used=result.deterministic_fallback_used,
            embedding_retry_count=result.embedding_retry_count,
            embedding_failure_type=result.embedding_failure_type,
            production_runtime_identity=result.production_runtime_identity,
            embedding_batch_size=(settings.limits.event_cluster_embedding_batch_size),
            similarity_threshold=(settings.limits.event_cluster_similarity_threshold),
            max_semantic_variants=(settings.limits.event_cluster_max_semantic_variants),
            input_row_count=result.input_row_count,
            cluster_count=len(cluster_entries),
            material_cluster_count=len(result.material_clusters),
            unassigned_row_count=0,
            duplicate_assignment_count=0,
            clusters=cluster_entries,
        ).model_dump(mode="json"),
    )
    return source_row_ids


def _load_cached(
    root: Path,
    *,
    context_path: Path,
    manifest_path: Path,
    analyzer: DailyAnalyzer,
    lookup_identity_sha256: str,
    news_batch: NewsBatch,
    expected_parsed_news_root_sha256: str,
    expected_input_cluster_root_sha256: str,
    expected_event_clustering: EventClusteringResult,
    expected_d_minus_one_payload_sha256: str,
    expected_d_minus_one_reference: QualityArtifactReference,
    expected_d_minus_one_artifact: _VerifiedBlindArtifact,
    expected_d_minus_one_candidate_universe_root_sha256: str,
    expected_d_minus_one_snapshot_root_sha256: str,
    trusted_cache_context_sha256: str | None,
) -> SharedPreRetrievalBuildResult | None:
    if not context_path.exists() and not manifest_path.exists():
        return None
    if context_path.is_file() and not manifest_path.exists():
        return None
    if not context_path.is_file() or not manifest_path.is_file():
        raise ValueError("shared pre-retrieval cache is incomplete")
    context_artifact = _read_blind_artifact(context_path)
    if trusted_cache_context_sha256 is not None and context_artifact.sha256 != trusted_cache_context_sha256:
        raise ValueError("shared pre-retrieval cache differs from its external anchor")
    manifest_artifact = _read_blind_artifact(manifest_path)
    raw_context = _blind_json_payload(context_artifact)
    raw_manifest = _blind_json_payload(manifest_artifact)
    context = SharedPreRetrievalContext.model_validate(raw_context)
    manifest = SharedPreRetrievalContextManifest.model_validate(raw_manifest)
    actual_context_reference = QualityArtifactReference(
        artifact_path=relative_to_root(context_path, root),
        sha256=context_artifact.sha256,
    )
    if (
        manifest.lookup_identity_sha256 != lookup_identity_sha256
        or manifest.context != actual_context_reference
        or context.context_id != manifest.context_id
        or context.code_semantic_version != SHARED_PRE_RETRIEVAL_VERSION
        or manifest.code_semantic_version != SHARED_PRE_RETRIEVAL_VERSION
        or context.news_sha256 != news_batch.sha256
        or manifest.news_sha256 != news_batch.sha256
        or context.trade_date != news_batch.trade_date
        or manifest.trade_date != news_batch.trade_date
        or context.parsed_news_root_sha256 != expected_parsed_news_root_sha256
        or manifest.parsed_news_root_sha256 != expected_parsed_news_root_sha256
    ):
        raise ValueError("shared pre-retrieval cache identity drifted")
    if len({node.node_id for node in context.map_reduce_nodes}) != len(context.map_reduce_nodes):
        raise ValueError("shared pre-retrieval node identities are not unique")

    if context.d_minus_one_safe_context != expected_d_minus_one_reference:
        raise ValueError("shared pre-retrieval cached D-1 reference drifted")
    named_references = _named_context_references(context)
    verified_artifacts = {
        name: (
            expected_d_minus_one_artifact
            if name == "d_minus_one_safe_context"
            else _resolve_shared_reference(root, reference=reference)
        )
        for name, reference in named_references.items()
    }
    if expected_d_minus_one_artifact.sha256 != expected_d_minus_one_reference.sha256:
        raise ValueError("shared pre-retrieval cached D-1 hash drifted")
    d_minus_one_payload = _blind_json_payload(verified_artifacts["d_minus_one_safe_context"])
    d_minus_one_context = SharedDMinusOneContext.model_validate(d_minus_one_payload)
    if (
        sha256_text(canonical_json(d_minus_one_context.model_dump(mode="json"))) != expected_d_minus_one_payload_sha256
        or d_minus_one_context.candidate_universe_root_sha256 != expected_d_minus_one_candidate_universe_root_sha256
        or d_minus_one_context.snapshot_root_sha256 != expected_d_minus_one_snapshot_root_sha256
        or d_minus_one_context.trade_date != context.trade_date
        or d_minus_one_context.cutoff_at != context.cutoff_at
    ):
        raise ValueError("shared pre-retrieval cached D-1 identity drifted")

    event_clustering_data = _blind_json_payload(verified_artifacts["event_clustering_result"])
    expected_event_clustering_data = event_clustering_module.event_clustering_payload(expected_event_clustering)
    if event_clustering_data != expected_event_clustering_data:
        raise ValueError("shared cached event clustering differs from fresh deterministic clustering")
    event_clustering = event_clustering_module.event_clustering_from_payload(event_clustering_data)
    input_cluster_root_sha256 = _input_cluster_root_sha256(
        event_clustering_module.event_clustering_payload(event_clustering)
    )
    expected_source_row_ids = [
        f"ROW-{item.row_number:06d}-{item.source_id}"
        for cluster in event_clustering.clusters
        for item in cluster.members
    ]
    expected_event_cluster_ids = [cluster.cluster_id for cluster in event_clustering.clusters]
    expected_material_cluster_ids = [cluster.cluster_id for cluster in event_clustering.material_clusters]
    expected_low_signal_cluster_ids = [
        cluster.cluster_id for cluster in event_clustering.clusters if not cluster.material
    ]
    if (
        event_clustering.input_row_count != news_batch.row_count
        or input_cluster_root_sha256 != expected_input_cluster_root_sha256
        or context.source_row_ids != expected_source_row_ids
        or context.event_cluster_ids != expected_event_cluster_ids
        or context.material_cluster_ids != expected_material_cluster_ids
        or context.low_signal_cluster_ids != expected_low_signal_cluster_ids
    ):
        raise ValueError("shared pre-retrieval cluster population drifted")

    novelty_batch_prompt_root_sha256 = context.prompt_hashes.get("news_novelty_review_batches")
    if not isinstance(novelty_batch_prompt_root_sha256, str):
        raise ValueError("shared pre-retrieval novelty prompt root is missing")
    prompt_sha256_root = _prompt_sha256_root(
        map_reduce_prompt_sha256s=[node.prompt_sha256 for node in context.map_reduce_nodes],
        novelty_batch_prompt_root_sha256=novelty_batch_prompt_root_sha256,
    )
    aggregate_open_world_prompt_sha256 = _aggregate_hash([node.prompt_sha256 for node in context.map_reduce_nodes])
    node_states = _load_and_verify_node_states(
        nodes=context.map_reduce_nodes,
        cutoff_at=context.cutoff_at,
        verified_artifacts=verified_artifacts,
    )
    root_state = node_states.get(context.root_node_id)
    if root_state is None:
        raise ValueError("shared pre-retrieval root output is missing")

    open_world_payload = _blind_json_payload(verified_artifacts["open_world_first_analysis"])
    open_world = OpenWorldFirstAnalysis.model_validate(open_world_payload)
    if (
        open_world.source_cluster_ids != context.material_cluster_ids
        or open_world.analyzed_cluster_ids != context.material_cluster_ids
        or open_world.uncovered_cluster_ids
        or open_world.prompt_sha256 != aggregate_open_world_prompt_sha256
        or open_world.cutoff_at != context.cutoff_at
    ):
        raise ValueError("shared final open-world output closure drifted")

    novelty_payload = _blind_json_payload(verified_artifacts["news_novelty_review"])
    novelty = NewsNoveltyReview.model_validate(novelty_payload)
    if (
        [finding.cluster_id for finding in novelty.findings] != context.material_cluster_ids
        or novelty.reviewed_cluster_count != len(context.material_cluster_ids)
        or novelty.prompt_sha256 != context.prompt_hashes.get("news_novelty_review")
        or novelty.cutoff_at != context.cutoff_at
    ):
        raise ValueError("shared novelty output closure drifted")

    event_cluster_artifact = verified_artifacts["event_cluster_ledger"]
    cached_shared_manifest = _shared_component_manifest(
        analyzer,
        result=expected_event_clustering,
        trade_date=context.trade_date,
        cutoff_at=context.cutoff_at,
        news_csv=news_batch.path,
        news_sha256=news_batch.sha256,
        run_seed=lookup_identity_sha256,
        event_cluster_path=event_cluster_artifact.path,
        event_cluster_sha256=event_cluster_artifact.sha256,
    )
    provider_checkpoint_commitment = _verify_provider_checkpoint_authenticity(
        analyzer,
        event_clustering=expected_event_clustering,
        cutoff_at=context.cutoff_at,
        nodes=context.map_reduce_nodes,
        node_states=node_states,
        novelty=novelty,
        shared_manifest=cached_shared_manifest,
        cluster_rows=_blind_jsonl_dict_rows(event_cluster_artifact),
    )
    if context.prompt_hashes.get("provider_checkpoint_commitment_root") != (provider_checkpoint_commitment.root_sha256):
        raise ValueError("shared provider checkpoint commitment root drifted")
    if (
        context.logical_llm_call_count != provider_checkpoint_commitment.checkpoint_count
        or context.novelty_logical_llm_call_count != provider_checkpoint_commitment.novelty_checkpoint_count
        or context.provider_checkpoint_commitment_count != provider_checkpoint_commitment.checkpoint_count
        or context.committed_prompt_tokens_estimate != provider_checkpoint_commitment.prompt_tokens_estimate
        or context.committed_completion_tokens_estimate != provider_checkpoint_commitment.completion_tokens_estimate
    ):
        raise ValueError("shared provider checkpoint accounting drifted")

    downstream_payload = _blind_json_payload(verified_artifacts["downstream_digest"])
    downstream_digest = SharedDownstreamDigest.model_validate(downstream_payload)
    downstream_digest_payload_sha256 = sha256_text(canonical_json(downstream_digest.model_dump(mode="json")))
    _verify_downstream_digest(
        context=context,
        actual=downstream_digest,
        root_state=root_state,
        novelty=novelty,
    )

    component_artifact_root_sha256 = _component_artifact_root_sha256(
        references=named_references,
        map_reduce_nodes=context.map_reduce_nodes,
    )
    expected_content_identity_sha256 = _content_identity_sha256(
        lookup_identity_sha256=lookup_identity_sha256,
        parsed_news_root_sha256=expected_parsed_news_root_sha256,
        input_cluster_root_sha256=input_cluster_root_sha256,
        prompt_sha256_root=prompt_sha256_root,
        component_artifact_root_sha256=component_artifact_root_sha256,
        downstream_digest_payload_sha256=(downstream_digest_payload_sha256),
        context_payload_sha256=actual_context_reference.sha256,
    )
    if (
        context.prompt_hashes.get("open_world_map_reduce") != aggregate_open_world_prompt_sha256
        or context.input_cluster_root_sha256 != input_cluster_root_sha256
        or manifest.input_cluster_root_sha256 != input_cluster_root_sha256
        or context.prompt_sha256_root != prompt_sha256_root
        or manifest.prompt_sha256_root != prompt_sha256_root
        or context.component_artifact_root_sha256 != component_artifact_root_sha256
        or manifest.component_artifact_root_sha256 != component_artifact_root_sha256
        or context.downstream_digest_payload_sha256 != downstream_digest_payload_sha256
        or manifest.downstream_digest_payload_sha256 != downstream_digest_payload_sha256
        or manifest.identity_sha256 != expected_content_identity_sha256
        or manifest.source_row_count != len(context.source_row_ids)
        or manifest.event_cluster_count != len(context.event_cluster_ids)
        or manifest.material_cluster_count != len(context.material_cluster_ids)
        or manifest.low_signal_cluster_count != len(context.low_signal_cluster_ids)
        or manifest.source_row_root_sha256 != sha256_text(canonical_json(context.source_row_ids))
        or manifest.event_cluster_root_sha256 != sha256_text(canonical_json(context.event_cluster_ids))
        or manifest.material_cluster_root_sha256 != sha256_text(canonical_json(context.material_cluster_ids))
    ):
        raise ValueError("shared pre-retrieval content identity drifted")
    return SharedPreRetrievalBuildResult(
        context=context,
        context_path=context_path,
        manifest=manifest,
        manifest_path=manifest_path,
        news_batch=news_batch,
        cache_hit=True,
    )


def _resolve_shared_reference(
    root: Path,
    *,
    reference: QualityArtifactReference,
) -> _VerifiedBlindArtifact:
    normalized = reference.artifact_path.replace("\\", "/")
    logical = PurePosixPath(normalized)
    if logical.is_absolute() or ".." in logical.parts:
        raise ValueError("shared pre-retrieval artifact reference is unsafe")
    path = (root / Path(*logical.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("shared pre-retrieval artifact escapes root") from exc
    if not path.is_file():
        raise ValueError("shared pre-retrieval cached artifact hash mismatch")
    artifact = _read_blind_artifact(path)
    if artifact.sha256 != reference.sha256:
        raise ValueError("shared pre-retrieval cached artifact hash mismatch")
    return artifact


def _load_and_verify_node_states(
    *,
    nodes: list[SharedMapReduceNode],
    cutoff_at: datetime,
    verified_artifacts: dict[str, _VerifiedBlindArtifact],
) -> dict[str, _NodeState]:
    states: dict[str, _NodeState] = {}
    for node in nodes:
        payload = _blind_json_payload(verified_artifacts[f"map_reduce_output:{node.node_id}"])
        if node.kind == "MAP":
            map_output = OpenWorldFirstAnalysis.model_validate(payload)
            if (
                map_output.source_cluster_ids != node.covered_cluster_ids
                or map_output.analyzed_cluster_ids != node.covered_cluster_ids
                or map_output.uncovered_cluster_ids
                or map_output.prompt_sha256 != node.prompt_sha256
                or map_output.cutoff_at != cutoff_at
            ):
                raise ValueError("shared MAP output closure drifted")
            output: OpenWorldFirstAnalysis | SharedOpenWorldReduceOutput = map_output
        else:
            reduce_output = SharedOpenWorldReduceOutput.model_validate(payload)
            if (
                reduce_output.node_id != node.node_id
                or reduce_output.child_node_ids != node.child_node_ids
                or reduce_output.covered_cluster_ids != node.covered_cluster_ids
            ):
                raise ValueError("shared REDUCE output closure drifted")
            output = reduce_output
        states[node.node_id] = _NodeState(contract=node, output=output)
    return states


def _verify_downstream_digest(
    *,
    context: SharedPreRetrievalContext,
    actual: SharedDownstreamDigest,
    root_state: _NodeState,
    novelty: NewsNoveltyReview,
) -> None:
    expected = _shared_downstream_digest(
        context_id=context.context_id,
        trade_date=context.trade_date,
        cutoff_at=context.cutoff_at,
        material_cluster_ids=context.material_cluster_ids,
        root_state=root_state,
        open_world_source=context.open_world_first_analysis,
        novelty=novelty,
        novelty_source=context.news_novelty_review,
    )
    if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("shared downstream digest differs from root-node and novelty sources")


def _provider_checkpoint_output[TModel: BaseModel](
    analyzer: DailyAnalyzer,
    *,
    prompt: str,
    purpose: str,
    response_model: type[TModel],
) -> tuple[TModel, dict[str, Any]]:
    tracer = analyzer.llm
    if not isinstance(tracer, TracingLLMProvider):
        raise ValueError("shared provider checkpoint verification requires the tracing provider")
    input_payload = {
        "prompt_sha256": sha256_text(prompt),
        "prompt_chars": len(prompt),
        "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "prompt_tokens_counted": tracer.count_tokens(prompt),
        "response_model": response_model.__name__,
    }
    checkpoint_path = tracer._checkpoint_path(
        operation="generate_structured",
        purpose=purpose,
        input_payload=input_payload,
    ).resolve()
    checkpoint_root = tracer.checkpoint_dir.resolve()
    project_root = analyzer.root.resolve()
    try:
        checkpoint_path.relative_to(checkpoint_root)
        checkpoint_root.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("shared provider checkpoint escapes the independent checkpoint store") from exc
    if "shared_pre_retrieval" in checkpoint_path.parts:
        raise ValueError("shared provider checkpoint cannot live inside the shared package")
    if not checkpoint_path.is_file():
        raise FileNotFoundError("shared provider checkpoint is missing; verification will not make a live OAuth call")
    artifact = _read_blind_artifact(checkpoint_path)
    payload = _blind_json_payload(artifact)
    if not isinstance(payload, dict):
        raise ValueError("shared provider checkpoint payload is invalid")
    checkpoint_id = tracer._checkpoint_id(
        operation="generate_structured",
        purpose=purpose,
        input_payload=input_payload,
    )
    output = payload.get("output")
    output_sha256 = sha256_text(canonical_json(output))
    token_usage = payload.get("token_usage")
    prompt_tokens = token_usage.get("prompt_tokens_estimate") if isinstance(token_usage, dict) else None
    completion_tokens = token_usage.get("completion_tokens_estimate") if isinstance(token_usage, dict) else None
    if (
        payload.get("schema_version") != "nslab.llm_checkpoint.v1"
        or payload.get("checkpoint_id") != checkpoint_id
        or checkpoint_path.stem != checkpoint_id
        or payload.get("operation") != "generate_structured"
        or payload.get("purpose") != purpose
        or payload.get("status") != "ok"
        or payload.get("input") != input_payload
        or payload.get("input_sha256") != sha256_text(canonical_json(input_payload))
        or not isinstance(output, dict)
        or payload.get("output_sha256") != output_sha256
        or isinstance(prompt_tokens, bool)
        or not isinstance(prompt_tokens, int)
        or prompt_tokens < 0
        or isinstance(completion_tokens, bool)
        or not isinstance(completion_tokens, int)
        or completion_tokens < 0
    ):
        raise ValueError("shared provider checkpoint commitment drifted")
    restored = response_model.model_validate(output)
    return restored, {
        "checkpoint_id": checkpoint_id,
        "checkpoint_file_sha256": artifact.sha256,
        "output_sha256": output_sha256,
        "prompt_sha256": str(input_payload["prompt_sha256"]),
        "purpose": purpose,
        "response_model": response_model.__name__,
        "prompt_tokens_estimate": prompt_tokens,
        "completion_tokens_estimate": completion_tokens,
    }


def _replay_provider_checkpoint_if_available[TModel: BaseModel](
    analyzer: DailyAnalyzer,
    *,
    prompt: str,
    purpose: str,
    response_model: type[TModel],
) -> TModel | None:
    try:
        restored, commitment = _provider_checkpoint_output(
            analyzer,
            prompt=prompt,
            purpose=purpose,
            response_model=response_model,
        )
    except FileNotFoundError:
        return None
    tracer = analyzer.llm
    if not isinstance(tracer, TracingLLMProvider):
        raise ValueError("authenticated checkpoint replay requires tracing")
    input_payload = {
        "prompt_sha256": sha256_text(prompt),
        "prompt_chars": len(prompt),
        "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "prompt_tokens_counted": tracer.count_tokens(prompt),
        "response_model": response_model.__name__,
    }
    output = restored.model_dump(mode="json")
    tracer._write_trace(
        operation="generate_structured",
        purpose=purpose,
        started_at=now_kst(),
        status="checkpoint_hit",
        input_payload=input_payload,
        output=output,
        token_usage={
            "prompt_tokens_estimate": tracer.count_tokens(prompt),
            "completion_tokens_estimate": max(
                1,
                len(canonical_json(output)) // 4,
            ),
        },
        checkpoint_id=commitment["checkpoint_id"],
    )
    return restored


def _verify_provider_checkpoint_authenticity(
    analyzer: DailyAnalyzer,
    *,
    event_clustering: EventClusteringResult,
    cutoff_at: datetime,
    nodes: list[SharedMapReduceNode],
    node_states: dict[str, _NodeState],
    novelty: NewsNoveltyReview,
    shared_manifest: ContextManifest,
    cluster_rows: list[dict[str, Any]],
) -> _ProviderCheckpointCommitment:
    commitments: list[dict[str, Any]] = []
    clusters = event_clustering_module.open_world_cluster_inputs(event_clustering)
    map_batches = _map_batches(
        analyzer,
        clusters=clusters,
        cutoff_at=cutoff_at,
    )
    map_nodes = [node for node in nodes if node.kind == "MAP"]
    if len(map_nodes) != len(map_batches):
        raise ValueError("shared provider checkpoint MAP topology drifted")
    states: list[_NodeState] = []
    for batch_index, (batch, node) in enumerate(
        zip(map_batches, map_nodes, strict=True),
        start=1,
    ):
        cluster_ids = [cluster.cluster_id for cluster in batch]
        expected_node_id = stable_id(
            "OWMAP",
            canonical_json(cluster_ids),
            length=16,
        )
        prompt = _map_prompt(
            node_id=expected_node_id,
            clusters=batch,
            cutoff_at=cutoff_at,
        )
        if (
            node.node_id != expected_node_id
            or node.covered_cluster_ids != cluster_ids
            or node.prompt_sha256 != sha256_text(prompt)
        ):
            raise ValueError("shared provider checkpoint MAP identity drifted")
        raw_output, commitment = _provider_checkpoint_output(
            analyzer,
            prompt=prompt,
            purpose=f"shared_open_world_map.batch_{batch_index:04d}",
            response_model=OpenWorldFirstAnalysis,
        )
        expected_output = analyzer._normalize_open_world_first_analysis(
            raw_output,
            news_texts=[cluster.representative_text for cluster in batch],
            event_ids=[event_id for cluster in batch for event_id in cluster.event_ids],
            cluster_ids=cluster_ids,
            cutoff_at=cutoff_at,
            prompt_sha256=sha256_text(prompt),
        )
        actual_state = node_states.get(node.node_id)
        if actual_state is None or actual_state.output.model_dump(mode="json") != expected_output.model_dump(
            mode="json"
        ):
            raise ValueError("shared MAP output differs from its provider checkpoint commitment")
        states.append(actual_state)
        commitments.append(commitment)

    reduce_nodes = iter(node for node in nodes if node.kind == "REDUCE")
    level = 1
    while len(states) > 1:
        next_states: list[_NodeState] = []
        batches = _reduce_batches(
            analyzer,
            states=states,
            level=level,
            cutoff_at=cutoff_at,
        )
        for batch_index, children in enumerate(batches, start=1):
            try:
                node = next(reduce_nodes)
            except StopIteration as exc:
                raise ValueError("shared provider checkpoint REDUCE topology is incomplete") from exc
            child_ids = [child.contract.node_id for child in children]
            covered_cluster_ids = [
                cluster_id for child in children for cluster_id in child.contract.covered_cluster_ids
            ]
            expected_node_id = stable_id(
                "OWREDUCE",
                canonical_json([level, child_ids]),
                length=16,
            )
            prompt = _reduce_prompt(
                node_id=expected_node_id,
                children=children,
                cutoff_at=cutoff_at,
            )
            if (
                node.node_id != expected_node_id
                or node.child_node_ids != child_ids
                or node.covered_cluster_ids != covered_cluster_ids
                or node.prompt_sha256 != sha256_text(prompt)
            ):
                raise ValueError("shared provider checkpoint REDUCE identity drifted")
            purpose = f"shared_open_world_reduce.level_{level:02d}.batch_{batch_index:04d}"
            raw_reduce_output, reduce_commitment = _provider_checkpoint_output(
                analyzer,
                prompt=prompt,
                purpose=purpose,
                response_model=SharedOpenWorldReduceOutput,
            )
            expected_reduce_output = _normalize_reduce_output(
                raw_reduce_output,
                node_id=expected_node_id,
                child_node_ids=child_ids,
                covered_cluster_ids=covered_cluster_ids,
            )
            actual_state = node_states.get(node.node_id)
            if actual_state is None or actual_state.output.model_dump(mode="json") != expected_reduce_output.model_dump(
                mode="json"
            ):
                raise ValueError("shared REDUCE output differs from its provider checkpoint commitment")
            next_states.append(actual_state)
            commitments.append(reduce_commitment)
        states = next_states
        level += 1
    try:
        next(reduce_nodes)
    except StopIteration:
        pass
    else:
        raise ValueError("shared provider checkpoint REDUCE topology has extra nodes")

    novelty_batches = _novelty_batches(
        analyzer,
        manifest=shared_manifest,
        cutoff_at=cutoff_at,
        cluster_rows=cluster_rows,
    )
    partial_reviews: list[NewsNoveltyReview] = []
    novelty_prompt_hashes: list[str] = []
    for batch_index, batch_rows in enumerate(novelty_batches, start=1):
        prompt = analyzer._build_news_novelty_review_prompt(
            cluster_rows=batch_rows,
            manifest=shared_manifest,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        raw_review, commitment = _provider_checkpoint_output(
            analyzer,
            prompt=prompt,
            purpose=f"shared_news_novelty_review.batch_{batch_index:04d}",
            response_model=NewsNoveltyReview,
        )
        partial_reviews.append(
            analyzer._normalize_news_novelty_review(
                raw_review,
                manifest=shared_manifest,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
                cluster_rows=batch_rows,
            )
        )
        novelty_prompt_hashes.append(prompt_sha256)
        commitments.append(commitment)
    expected_findings = sorted(
        [finding for review in partial_reviews for finding in review.findings],
        key=lambda finding: finding.cluster_index,
    )
    expected_novelty = NewsNoveltyReview(
        run_id=shared_manifest.run_id,
        prompt_version=NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
        prompt_sha256=_aggregate_hash(novelty_prompt_hashes),
        created_at=novelty.created_at,
        cutoff_at=cutoff_at,
        review_mode=shared_manifest.blind_context_mode,
        cluster_count=len(cluster_rows),
        reviewed_cluster_count=len(expected_findings),
        findings=expected_findings,
        excluded_after_cutoff_source_ids=(shared_manifest.excluded_web_source_ids),
        notes=_unique_strings(note for review in partial_reviews for note in review.notes),
    )
    if novelty.model_dump(mode="json") != expected_novelty.model_dump(mode="json"):
        raise ValueError("shared novelty output differs from provider checkpoint commitments")
    return _ProviderCheckpointCommitment(
        root_sha256=sha256_text(
            canonical_json(
                {
                    "schema_version": PROVIDER_CHECKPOINT_COMMITMENT_VERSION,
                    "threat_boundary": PROVIDER_CHECKPOINT_THREAT_BOUNDARY,
                    "checkpoints": commitments,
                }
            )
        ),
        checkpoint_count=len(commitments),
        novelty_checkpoint_count=sum(
            str(commitment["purpose"]).startswith("shared_news_novelty_review") for commitment in commitments
        ),
        prompt_tokens_estimate=sum(int(commitment["prompt_tokens_estimate"]) for commitment in commitments),
        completion_tokens_estimate=sum(int(commitment["completion_tokens_estimate"]) for commitment in commitments),
    )


def _reference(root: Path, path: Path) -> QualityArtifactReference:
    path = path.resolve()
    return QualityArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=file_sha256(path),
    )


def _trace_paths(settings: Settings) -> set[str]:
    trace_dir = settings.path(settings.output_dirs.traces)
    return {path.resolve().as_posix() for path in trace_dir.glob("*.json") if path.is_file()}


def _new_llm_traces(
    settings: Settings,
    *,
    before: set[str],
    purpose_prefix: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path_text in sorted(_trace_paths(settings) - before):
        payload = read_json(Path(path_text))
        if not isinstance(payload, dict):
            continue
        purpose = payload.get("purpose")
        if purpose_prefix is not None and (not isinstance(purpose, str) or not purpose.startswith(purpose_prefix)):
            continue
        if payload.get("operation") != "generate_structured":
            continue
        rows.append(payload)
    return rows


def _single_new_trace(
    settings: Settings,
    *,
    before: set[str],
    purpose: str,
) -> dict[str, Any]:
    rows = _new_llm_traces(
        settings,
        before=before,
        purpose_prefix=purpose,
    )
    exact = [row for row in rows if row.get("purpose") == purpose]
    if len(exact) != 1:
        raise ValueError("shared LLM call did not produce exactly one trace")
    return exact[0]


def _call_receipt(trace: dict[str, Any]) -> _CallReceipt:
    status = trace.get("status")
    if status not in {"ok", "checkpoint_hit"}:
        raise ValueError("shared LLM call trace is not reusable")
    return _CallReceipt(
        checkpoint_hit=status == "checkpoint_hit",
        live_call_count=int(status == "ok"),
        prompt_tokens=_trace_tokens(trace, "prompt"),
        completion_tokens=_trace_tokens(trace, "completion"),
    )


def _trace_tokens(trace: dict[str, Any], kind: str) -> int:
    token_usage = trace.get("token_usage")
    if not isinstance(token_usage, dict):
        return 0
    key = f"{kind}_tokens_estimate"
    value = token_usage.get(key)
    return int(value) if isinstance(value, int) else 0


def _aggregate_hash(values: list[str]) -> str:
    if not values:
        return sha256_text(canonical_json([]))
    return values[0] if len(values) == 1 else sha256_text(canonical_json(values))


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value not in result:
            result.append(value)
    return result
