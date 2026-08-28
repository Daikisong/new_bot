from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import BrainManifest
from news_scalping_lab.diagnostics import build_doctor_report
from news_scalping_lab.llm.codex_oauth_provider import (
    CodexOAuthError,
    CodexOAuthInteractiveLoginRequired,
    CodexOAuthProvider,
    _strict_output_schema,
    probe_codex_embedding_capability,
    run_interactive_codex_login,
)


class _StructuredResult(BaseModel):
    status: Literal["ok"]


class _StructuredResultWithDefaults(BaseModel):
    status: Literal["ok"] = "ok"
    tags: list[str] = []


class _FakeCodexRunner:
    def __init__(self, *, structured_payload: str = '{"status":"ok"}') -> None:
        self.calls: list[list[str]] = []
        self.call_kwargs: list[dict[str, Any]] = []
        self.structured_payload = structured_payload

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        self.call_kwargs.append(dict(kwargs))
        if "--version" in args:
            return subprocess.CompletedProcess(args, 0, "codex-cli 0.147.0\n", "")
        if args[-2:] == ["login", "status"]:
            return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT\n", "")
        if "--help" in args:
            return subprocess.CompletedProcess(args, 0, "--output-schema\n--json\n", "")
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(self.structured_payload, encoding="utf-8")
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"type": "thread.started", "thread_id": "THREAD-1"}) + "\n",
            "",
        )


def test_codex_oauth_provider_uses_supported_cli_or_sdk_interface() -> None:
    runner = _FakeCodexRunner()
    provider = CodexOAuthProvider(runner=runner)

    result = asyncio.run(
        provider.generate_structured(
            prompt="return ok",
            response_model=_StructuredResult,
            purpose="test",
        )
    )

    assert result.status == "ok"
    execution = runner.calls[-1]
    assert Path(execution[0]).stem == "codex"
    assert execution[1:4] == ["--ask-for-approval", "never", "exec"]
    assert "--output-schema" in execution
    assert "--output-last-message" in execution
    assert "--json" in execution
    assert "--ephemeral" in execution
    assert "--ignore-user-config" in execution


def test_codex_oauth_provider_sends_non_ascii_prompt_as_utf8() -> None:
    runner = _FakeCodexRunner(structured_payload="완료")
    provider = CodexOAuthProvider(runner=runner)

    output = asyncio.run(
        provider.generate_text(
            prompt="한글 연구자료를 요약해줘",
            purpose="utf8",
        )
    )

    assert output == "완료"
    execution_kwargs = runner.call_kwargs[-1]
    assert execution_kwargs["input"] == "한글 연구자료를 요약해줘"
    assert execution_kwargs["text"] is True
    assert execution_kwargs["encoding"] == "utf-8"
    assert execution_kwargs["errors"] == "strict"


def test_codex_oauth_provider_never_reads_credential_files() -> None:
    import news_scalping_lab.llm.codex_oauth_provider as module

    source = inspect.getsource(module)
    assert ".codex/auth" not in source
    assert "auth.json" not in source
    assert "keyring" not in source.lower()


def test_codex_oauth_structured_output_validates_schema() -> None:
    provider = CodexOAuthProvider(runner=_FakeCodexRunner())
    result = asyncio.run(
        provider.generate_structured(
            prompt="return ok",
            response_model=_StructuredResult,
            purpose="structured",
        )
    )
    assert result == _StructuredResult(status="ok")
    assert provider.structured_validation_status == "PASSED"


def test_codex_oauth_normalizes_pydantic_defaults_for_strict_output() -> None:
    schema = _strict_output_schema(_StructuredResultWithDefaults.model_json_schema())

    assert schema["required"] == ["status", "tags"]
    assert schema["additionalProperties"] is False
    assert "default" not in schema["properties"]["status"]
    assert "default" not in schema["properties"]["tags"]


def test_codex_oauth_structured_failure_fails_closed() -> None:
    runner = _FakeCodexRunner(structured_payload='{"status":"wrong"}')
    provider = CodexOAuthProvider(
        runner=runner,
        structured_repair_retries=1,
    )
    with pytest.raises(CodexOAuthError, match="Input should be 'ok'"):
        asyncio.run(
            provider.generate_structured(
                prompt="return ok",
                response_model=_StructuredResult,
                purpose="structured",
            )
        )
    assert provider.structured_validation_status == "FAILED"
    assert "Validation error detail:" in runner.call_kwargs[-1]["input"]
    assert "Input should be 'ok'" in runner.call_kwargs[-1]["input"]


def test_codex_oauth_noninteractive_login_reports_required() -> None:
    with pytest.raises(
        CodexOAuthInteractiveLoginRequired,
        match="CODEX_OAUTH_INTERACTIVE_LOGIN_REQUIRED",
    ):
        run_interactive_codex_login("codex", interactive=False)


def test_doctor_requires_codex_oauth_health_for_selected_provider(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="codex-oauth",
        codex_command="missing-codex-command",
    )
    report = build_doctor_report(settings, production=False)
    assert report["api_connections"]["codex_oauth"]["required"] is True
    assert report["api_connections"]["codex_oauth"]["logged_in"] is False


def test_production_manifest_records_codex_identity() -> None:
    manifest = BrainManifest(
        brain_version="brain-codex",
        created_at="2030-01-10T08:59:59+09:00",
        build_mode="llm-full",
        production_eligible=True,
        llm_provider="codex-oauth",
        llm_model="gpt-5.4",
        codex_cli_version="codex-cli 0.147.0",
        reasoning_effort="high",
        live_agent_call_count=1,
        cache_hit_count=0,
        structured_validation_status="PASSED",
        oauth_health_check_status="PASS",
        accepted_episode_count=1,
        covered_episode_count=1,
        covered_episode_ids=["EP-1"],
        coverage_complete=True,
    )
    assert manifest.llm_provider == "codex-oauth"
    assert manifest.live_agent_call_count == 1
    assert manifest.oauth_health_check_status == "PASS"


def test_codex_embedding_capability_is_probed_not_assumed() -> None:
    result = probe_codex_embedding_capability(
        "codex",
        runner=_FakeCodexRunner(),
    )
    assert result["supported"] is False
    assert result["probe"] == "codex exec --help"
    assert result["credential_accessed"] is False
