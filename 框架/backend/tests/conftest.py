# -*- coding: utf-8 -*-
"""06-B 集成测试隔离夹具。

原测试使用的两个实验 ID 在这里仅作为测试别名。所有设计、登记与冻结事件
均通过真实 ExperimentRegistry API 在临时目录内合成；它们不是历史真实实验，
不复制生产登记记录，不代表策略获批或任何市场回测结果。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_candidate_registry(request, tmp_path, monkeypatch):
    """仅为原 candidate 测试提供自包含登记库，保持原测试文件逐字不变。"""
    module_path = Path(getattr(request.module, "__file__", ""))
    if module_path.name != "test_candidate_backtest_runner.py":
        yield
        return

    import experiment_registry as registry_module
    import bt_run_log

    fixture_root = tmp_path / "candidate_fixture"
    registry_root = fixture_root / "registry"
    provenance_root = fixture_root / "provenance"
    log_root = fixture_root / "logs"
    log_root.mkdir(parents=True)
    monkeypatch.setenv(registry_module.REGISTRY_ENV, str(registry_root))
    monkeypatch.setenv("WX_PROVENANCE_HOME", str(provenance_root))
    monkeypatch.setenv("BT_RUN_LOG", str(log_root / "backtest_runs.log"))
    # 日志模块已在测试收集期导入，故同时隔离其已解析的路径常量。
    monkeypatch.setattr(bt_run_log, "LOG_DIR", log_root)
    monkeypatch.setattr(bt_run_log, "RUN_LOG_PATH", log_root / "backtest_runs.log")

    registry = registry_module.get_registry()
    frozen_alias = "exp_dee96a16cfe89331"
    unfrozen_alias = "exp_74a00fc7772bf37d"
    sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
    design = {
        "strategy_id": "TEST_06B_SYNTHETIC_REGISTRY",
        "strategy_version": "fixture_frozen_v1",
        "code_sha256": sha("synthetic-fixture-code"),
        "config_sha256": sha("synthetic-fixture-config"),
        "rules_sha256": sha("synthetic-fixture-rules"),
    }
    scope = {
        "symbols": ["BTCUSDT"],
        "window_start": 1704067200000,
        "window_end": 1704672000000,
    }
    common = {
        "sample_scope": scope,
        "split_policy": None,
        "metrics_spec": {"primary": "synthetic_fixture_only"},
        "mode": registry_module.MODE_RESEARCH,
        "by": "pytest-synthetic-fixture",
        "note": "ID 仅为原测试别名；全新合成夹具，不是历史真实实验记录",
    }
    registry.register(design=design, experiment_id=frozen_alias, **common)
    registry.freeze(frozen_alias, by="pytest-synthetic-fixture",
                    note="仅冻结临时合成夹具，不授予真实候选运行资格")
    registry.register(
        design=dict(design, strategy_version="fixture_unfrozen_v1"),
        experiment_id=unfrozen_alias, **common,
    )
    yield
