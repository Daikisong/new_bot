from __future__ import annotations

from pathlib import Path

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.production.bootstrap import (
    HMAC_ENV_KEYS,
    _stock_web_status,
    bootstrap_local_environment,
)
from news_scalping_lab.utils import write_json


def _bootstrap_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".gitignore").write_text(
        ".env\n.env.backup.*\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "news_scalping_lab.production.bootstrap._restrict_secret_file",
        lambda path: "TEST_OWNER_ONLY",
    )
    monkeypatch.setattr(
        "news_scalping_lab.production.bootstrap._env_is_gitignored",
        lambda root: True,
    )
    return tmp_path


def _env_values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_bootstrap_generates_five_distinct_hmac_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    result = bootstrap_local_environment(root)
    values = _env_values(root / ".env")
    secrets = [values[key] for key in HMAC_ENV_KEYS]
    assert len(secrets) == 5
    assert len(set(secrets)) == 5
    assert result["distinct_secret_count"] == 5
    assert result["strong_secret_count"] == 5
    assert all(item["strength"] == "strong" for item in result["secrets"])


def test_bootstrap_does_not_print_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    result = bootstrap_local_environment(root)
    values = _env_values(root / ".env")
    rendered = repr(result)
    assert all(values[key] not in rendered for key in HMAC_ENV_KEYS)


def test_bootstrap_preserves_existing_nonempty_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    existing = "existing-secret-value-that-is-long-enough"
    (root / ".env").write_text(
        f"{HMAC_ENV_KEYS[0]}={existing}\n",
        encoding="utf-8",
    )
    result = bootstrap_local_environment(root)
    assert _env_values(root / ".env")[HMAC_ENV_KEYS[0]] == existing
    assert result["secrets"][0]["status"] == "preserved"
    assert result["secrets"][0]["strength"] == "weak_existing_value"


def test_bootstrap_rotates_only_with_explicit_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    bootstrap_local_environment(root)
    original = _env_values(root / ".env")[HMAC_ENV_KEYS[0]]
    bootstrap_local_environment(root)
    assert _env_values(root / ".env")[HMAC_ENV_KEYS[0]] == original
    bootstrap_local_environment(root, rotate_secrets=True)
    assert _env_values(root / ".env")[HMAC_ENV_KEYS[0]] != original


def test_bootstrap_atomic_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    replacements: list[tuple[Path, Path]] = []
    from news_scalping_lab.production import bootstrap as module

    original_replace = module.os.replace

    def observed_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(module.os, "replace", observed_replace)
    bootstrap_local_environment(root)
    assert replacements
    assert replacements[-1][1] == root / ".env"
    assert replacements[-1][0].suffix == ".tmp"


def test_bootstrap_creates_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    (root / ".env").write_text("EXISTING=value\n", encoding="utf-8")
    result = bootstrap_local_environment(root)
    assert result["backup_path"] is not None
    assert Path(result["backup_path"]).is_file()


def test_bootstrap_env_is_gitignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    result = bootstrap_local_environment(root)
    assert result["gitignored"] is True


def test_bootstrap_does_not_store_codex_oauth_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    result = bootstrap_local_environment(root)
    values = _env_values(root / ".env")
    assert result["oauth_credentials_written"] is False
    assert not any("OAUTH" in key or "ACCESS_TOKEN" in key for key in values)


def test_cloud_environment_does_not_claim_local_secret_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _bootstrap_root(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_CLOUD_TASK_ID", "task-1")
    result = bootstrap_local_environment(root)
    assert result["environment_scope"] == "ephemeral-cloud"
    assert result["local_persistence_claimed"] is False


def test_stock_web_status_uses_runtime_atlas_contract(tmp_path: Path) -> None:
    atlas = tmp_path / "stock-web" / "atlas"
    shard_root = atlas / "ohlcv_tradable_by_symbol_year"
    shard_root.mkdir(parents=True)
    write_json(
        atlas / "manifest.json",
        {
            "source_name": "stock-web-test",
            "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        },
    )
    write_json(
        atlas / "schema.json",
        {
            "tradable_shard_columns": {
                key: {"type": "number"}
                for key in ("date", "open", "high", "low", "close")
            }
        },
    )
    research_daily = atlas / "research_daily"
    (research_daily / "access").mkdir(parents=True)
    (research_daily / "snapshots").mkdir()
    (research_daily / "trading_calendar.csv").write_text(
        "trade_date\n2030-01-10\n",
        encoding="utf-8",
    )
    write_json(
        research_daily / "manifest.json",
        {
            "max_trade_date": "2030-01-10",
            "full_backfill_complete": True,
            "validation_passed": True,
        },
    )
    write_json(
        research_daily / "schema.json",
        {"schema_version": "stock_web.research_daily_snapshot.v1"},
    )
    status = _stock_web_status(
        Settings(
            project_root=tmp_path,
            stock_web_path=tmp_path / "stock-web",
        )
    )
    assert status["ready"] is True
    assert status["atlas"]["status"] == "ok"
    assert status["research_daily"]["ready"] is True
