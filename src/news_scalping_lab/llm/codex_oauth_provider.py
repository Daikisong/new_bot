"""ChatGPT OAuth-backed LLM provider using the supported Codex CLI surface."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from news_scalping_lab.llm.base import conservative_token_upper_bound
from news_scalping_lab.utils import sha256_text

T = TypeVar("T", bound=BaseModel)
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class CodexOAuthError(RuntimeError):
    """Raised when the supported Codex OAuth agent interface fails."""


class CodexOAuthInteractiveLoginRequired(CodexOAuthError):
    """Raised when a local interactive login is required."""


class CodexOAuthEmbeddingUnavailableError(CodexOAuthError):
    """Codex CLI does not expose a supported dense embedding interface."""


class CodexOAuthProvider:
    provider_name = "codex-oauth"

    def __init__(
        self,
        *,
        command: str = "codex",
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "xhigh",
        max_output_tokens: int | None = None,
        structured_repair_retries: int = 1,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self.command = resolve_codex_command(command)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.structured_repair_retries = max(0, structured_repair_retries)
        self._runner = runner
        self.cli_version = codex_cli_version(command, runner=runner)
        self.oauth_health_check_status = "NOT_CHECKED"
        self.live_agent_call_count = 0
        self.cache_hit_count = 0
        self.structured_validation_status = "NOT_RUN"
        self.last_prompt_hash: str | None = None
        self.last_run_id: str | None = None

    def count_tokens(self, text: str) -> int:
        return conservative_token_upper_bound(text)

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        return await asyncio.to_thread(
            self._execute,
            prompt,
            purpose,
            None,
        )

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        purpose: str,
    ) -> T:
        current_prompt = prompt
        last_error: Exception | None = None
        for attempt in range(self.structured_repair_retries + 1):
            try:
                output = await asyncio.to_thread(
                    self._execute,
                    current_prompt,
                    purpose,
                    _strict_output_schema(response_model.model_json_schema()),
                )
                parsed = response_model.model_validate_json(output)
            except (CodexOAuthError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt >= self.structured_repair_retries:
                    self.structured_validation_status = "FAILED"
                    raise CodexOAuthError(
                        "Codex structured output failed schema validation: "
                        + _validation_error_detail(exc)
                    ) from exc
                current_prompt = _repair_prompt(
                    original_prompt=prompt,
                    validation_error=exc,
                )
                continue
            self.structured_validation_status = "PASSED"
            return parsed
        raise CodexOAuthError("Codex structured output failed") from last_error

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del texts, purpose
        raise CodexOAuthEmbeddingUnavailableError(
            "Codex CLI 0.147.0 exposes no supported dense embedding command"
        )

    def health_status(self) -> dict[str, Any]:
        status = codex_login_status(self.command, runner=self._runner)
        self.oauth_health_check_status = (
            "PASS" if status["logged_in"] is True else "LOGIN_REQUIRED"
        )
        return {
            **status,
            "provider": self.provider_name,
            "codex_cli_version": self.cli_version,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }

    def identity(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "codex_cli_version": self.cli_version,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "prompt_hash": self.last_prompt_hash,
            "run_id": self.last_run_id,
            "live_agent_call_count": self.live_agent_call_count,
            "cache_hit_count": self.cache_hit_count,
            "structured_validation_status": self.structured_validation_status,
            "oauth_health_check_status": self.oauth_health_check_status,
        }

    def _execute(
        self,
        prompt: str,
        purpose: str,
        output_schema: dict[str, Any] | None,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="nslab-codex-oauth-") as temp_name:
            temp_root = Path(temp_name)
            output_path = temp_root / "last-message.txt"
            arguments = self._base_arguments(output_path)
            if output_schema is not None:
                schema_path = temp_root / "response.schema.json"
                schema_path.write_text(
                    json.dumps(
                        output_schema,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
                arguments.extend(["--output-schema", str(schema_path)])
            arguments.append("-")
            completed = self._runner(
                arguments,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                cwd=temp_root,
                check=False,
            )
            if completed.returncode != 0:
                raise CodexOAuthError(
                    "Codex agent execution failed: "
                    + _safe_cli_error(completed.stderr, completed.stdout)
                )
            if not output_path.is_file():
                raise CodexOAuthError(
                    "Codex agent did not produce --output-last-message"
                )
            output = output_path.read_text(encoding="utf-8").strip()
            if not output:
                raise CodexOAuthError("Codex agent returned an empty final message")
            events = _jsonl_events(completed.stdout)
            self.live_agent_call_count += 1
            self.cache_hit_count += sum(
                1
                for event in events
                if event.get("type") in {"cache_hit", "checkpoint_hit"}
            )
            self.last_prompt_hash = sha256_text(prompt)
            self.last_run_id = _event_run_id(events) or f"CODEX-{self.last_prompt_hash[:16]}"
            self.oauth_health_check_status = "PASS"
            return output

    def _base_arguments(self, output_path: Path) -> list[str]:
        arguments = [
            self.command,
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
            "--output-last-message",
            str(output_path),
        ]
        if self.model.strip():
            arguments.extend(["--model", self.model])
        if self.reasoning_effort.strip():
            arguments.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{self.reasoning_effort}"',
                ]
            )
        return arguments


def codex_cli_version(
    command: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> str:
    resolved_command = resolve_codex_command(command)
    completed = runner(
        [resolved_command, "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CodexOAuthError("Codex CLI is not installed or not executable")
    version = completed.stdout.strip()
    if not version:
        raise CodexOAuthError("Codex CLI version output is empty")
    return version


def codex_login_status(
    command: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    resolved_command = resolve_codex_command(command)
    completed = runner(
        [resolved_command, "login", "status"],
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(
        value.strip()
        for value in (completed.stdout, completed.stderr)
        if value.strip()
    )
    logged_in = completed.returncode == 0 and output.startswith("Logged in")
    method = "chatgpt" if "ChatGPT" in output else "other" if logged_in else None
    return {
        "logged_in": logged_in,
        "login_method": method,
        "status": "PASS" if logged_in else "LOGIN_REQUIRED",
    }


def run_interactive_codex_login(
    command: str,
    *,
    interactive: bool,
    runner: CommandRunner = subprocess.run,
) -> int:
    if not interactive:
        raise CodexOAuthInteractiveLoginRequired(
            "CODEX_OAUTH_INTERACTIVE_LOGIN_REQUIRED"
        )
    completed = runner([resolve_codex_command(command), "login"], check=False)
    return int(completed.returncode)


def probe_codex_embedding_capability(
    command: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    resolved_command = resolve_codex_command(command)
    completed = runner(
        [resolved_command, "exec", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    help_text = completed.stdout if completed.returncode == 0 else ""
    supported = any(
        token in help_text
        for token in ("--embedding-output", "--embed", "embedding vector")
    )
    return {
        "supported": supported,
        "probe": "codex exec --help",
        "credential_accessed": False,
        "reason": (
            "supported dense embedding option detected"
            if supported
            else "no supported dense embedding option in Codex CLI help"
        ),
    }


def resolve_codex_command(command: str) -> str:
    configured = command.strip()
    if not configured:
        return command
    if os.name == "nt" and Path(configured).suffix == "":
        for suffix in (".cmd", ".exe"):
            resolved = shutil.which(configured + suffix)
            if resolved:
                return resolved
    return shutil.which(configured) or configured


def _repair_prompt(*, original_prompt: str, validation_error: Exception) -> str:
    return "\n".join(
        [
            original_prompt,
            "",
            "The previous response failed the supplied JSON Schema validation.",
            "Return a complete corrected response matching the schema exactly.",
            f"Validation error type: {type(validation_error).__name__}",
            "Validation error detail: " + _validation_error_detail(validation_error),
        ]
    )


def _validation_error_detail(error: Exception, *, max_chars: int = 4000) -> str:
    if isinstance(error, ValidationError):
        detail = json.dumps(
            error.errors(include_url=False, include_input=False),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        detail = str(error).strip() or type(error).__name__
    if len(detail) <= max_chars:
        return detail
    return detail[: max_chars - 16] + "...[truncated]"


def _strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic JSON Schema to the Codex strict-output subset."""

    def normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {
            key: normalize(item)
            for key, item in value.items()
            if key != "default"
        }
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
            normalized["additionalProperties"] = False
        return normalized

    result = normalize(schema)
    if not isinstance(result, dict):
        raise CodexOAuthError("Codex output schema normalization failed")
    return result


def _jsonl_events(value: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in value.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _event_run_id(events: Sequence[dict[str, Any]]) -> str | None:
    for event in events:
        for key in ("thread_id", "turn_id", "id"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _safe_cli_error(stderr: str, stdout: str = "") -> str:
    first_line = next(
        (line.strip() for line in stderr.splitlines() if line.strip()),
        "",
    )
    if first_line:
        return first_line[:300]
    for event in reversed(_jsonl_events(stdout)):
        if event.get("type") not in {"error", "turn.failed"}:
            continue
        error = event.get("error")
        message = (
            error.get("message")
            if isinstance(error, dict)
            else event.get("message")
        )
        if isinstance(message, str) and message.strip():
            return " ".join(message.split())[:300]
    return "unknown Codex CLI error"
