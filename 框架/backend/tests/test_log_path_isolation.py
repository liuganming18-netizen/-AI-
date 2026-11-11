# -*- coding: utf-8 -*-
"""日志外置回归：外部父目录不存在时仍落盘，源码默认日志目录保持未创建。"""
import json

import bt_run_log


def test_external_log_parent_is_created_without_code_log_directory(tmp_path, monkeypatch):
    code_log_dir = tmp_path / "code" / "logs"
    output = tmp_path / "state" / "new-parent" / "runs.log"
    monkeypatch.setattr(bt_run_log, "LOG_DIR", code_log_dir)
    monkeypatch.setattr(bt_run_log, "RUN_LOG_PATH", output)

    bt_run_log.run_log("isolation_check", marker="synthetic-test")

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["event"] == "backtest.isolation_check"
    assert record["marker"] == "synthetic-test"
    assert not code_log_dir.exists()
