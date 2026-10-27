#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-EVIDENCE-04｜离线反例测试（工单要求 7）

在临时目录构造 8 个「假交付」，用同一个 verify_delivery() 校验，证明：
  · 正确的交付能 PASS
  · 错误 run / 缺源文件 / 哈希不符 / manifest 谎称 / 清单含自身 / 重复截图改名 / 字段路径错
    全部能被识别为 FAIL

全程零网络、零后端、零浏览器；只写 tempfile 目录。
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib_verifier as V  # noqa: E402

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJECT_ROOT / "evidence" / "UI-EVIDENCE-04" / "out"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_baseline(root: Path) -> dict:
    """造一份「全部正确」的交付，返回关键路径。"""
    deliv = root / "deliv"
    src = root / "src" / "run_TEST_001"
    deliv.mkdir(parents=True)
    src.mkdir(parents=True)

    (src / "summary.json").write_text(json.dumps({
        "symbol": "BTCUSDT", "variant": "A", "spec_status": "research_import",
        "summary_variant_a": {"initial_equity": 2000.0, "net_pnl": -8.2432223,
                              "trade_count": 1, "order_count": 2},
        "research_note": "测试用",
    }, ensure_ascii=False), encoding="utf-8")
    (src / "run.json").write_text(json.dumps({
        "run_id": "run_TEST_001", "status": "succeeded",
        "metrics": {"win_rate_pct": 0.0}, "hashes": {},
    }, ensure_ascii=False), encoding="utf-8")

    # 两张互不相同的假 PNG
    (deliv / "view_01.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"A" * 200)
    (deliv / "view_02.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"B" * 200)
    (deliv / "state_signal_normal.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"C" * 200)

    # 报告：数量写对
    (deliv / "00_报告.md").write_text(
        "# 测试报告\n\n本轮共 3 张截图（2 个 View 视图 + 1 张独立 State 状态覆盖截图）。\n",
        encoding="utf-8")

    # 字段路径全部真实
    with open(deliv / "04_回测run详情与summary逐字段对账表.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["对账维度", "前端页面DOM渲染值", "服务端真实文件落盘值", "服务端文件真实字段路径", "核对判定", "说明"])
        w.writerow(["Run ID", "run_TEST_001", "run_TEST_001", "run.json / run_id", "一致", ""])
        w.writerow(["初始资金", "$2,000.00", "2000.0", "summary.json / summary_variant_a.initial_equity", "一致", ""])
        w.writerow(["净盈亏", "$-8.24", "-8.2432223", "summary.json / summary_variant_a.net_pnl", "一致", ""])
        w.writerow(["胜率", "0.0", "0.0", "run.json / metrics.win_rate_pct", "一致", ""])

    audit = {
        "suite": "UI-EVIDENCE-04-negative-test",
        "backtest_run_parity": {
            "displayed_run_id": "run_TEST_001",
            "source_dir": str(src),
            "manifest_present": False,
        },
        # C5 需要至少一个绑定字段
        "source_binding": {"frontend_src": "x", "backend": "y", "script": "z"},
    }
    (deliv / "audit_ui_verification.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清单：排除自身，全部产物算完再算
    write_inventory(deliv, root)
    return {"deliv": deliv, "src": src, "root": root}


def write_inventory(deliv: Path, root: Path, include_self: bool = False,
                    tamper: dict | None = None) -> None:
    """生成清单。include_self=True 复现 UI-03 的自指缺陷；tamper 指定篡改。"""
    inv = deliv / "08_文件清单与哈希.csv"
    rows = [["文件类别", "相对路径", "文件大小(Bytes)", "SHA-256完整哈希"]]
    targets = [p for p in sorted(deliv.glob("*")) if p.is_file() and p.name != inv.name]
    for p in targets:
        rel = str(p.relative_to(root))
        size, h = p.stat().st_size, sha(p)
        if tamper and p.name in tamper:
            h = tamper[p.name]
        rows.append(["交付证据文件", rel, size, h])
    if include_self:
        rows.append(["交付证据文件", str(inv.relative_to(root)), 0, V.EMPTY_SHA])
    with open(inv, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def run_case(name: str, mutate, expect_fail: list) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="_negt_"))
    try:
        env = build_baseline(tmp)
        mutate(env)
        rep = V.verify_delivery(env["deliv"], tmp)
        failed = {k for k, v in rep["检查项"].items() if v["判定"] == "FAIL"}
        hit = all(any(k.startswith(e) for k in failed) for e in expect_fail)
        return {
            "场景": name,
            "期望失败项": expect_fail,
            "实际失败项": sorted(failed),
            "识别成功": hit,
            "总判定": rep["总判定"],
            "汇总": rep["汇总"],
            "失败原因摘录": {k: v.get("原因", "")[:150]
                       for k, v in rep["检查项"].items() if v["判定"] == "FAIL"},
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    # N0 基线：应全 PASS
    def n0(env):
        pass
    results.append(run_case("N0 基线（全部正确）", n0, []))

    # N1 错误 run：页面 run_id 与磁盘不一致
    def n1(env):
        p = env["deliv"] / "audit_ui_verification.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["backtest_run_parity"]["displayed_run_id"] = "run_WRONG_999"
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        write_inventory(env["deliv"], env["root"])   # 只暴露目标缺陷，不让清单连坐
    results.append(run_case("N1 错误 run（页面 run_id 与磁盘不符）", n1, ["C6"]))

    # N2 缺源文件：删掉 summary.json
    def n2(env):
        (env["src"] / "summary.json").unlink()
    results.append(run_case("N2 缺源文件（summary.json 被删）", n2, ["C1"]))

    # N3 哈希不符：清单里篡改 view_01 的哈希
    def n3(env):
        write_inventory(env["deliv"], env["root"], tamper={"view_01.png": "0" * 64})
    results.append(run_case("N3 哈希不符（清单哈希被篡改）", n3, ["C3"]))

    # N4 manifest 谎称：CSV 声称哈希来自 manifest，但 manifest 不存在
    def n4(env):
        p = env["deliv"] / "04_回测run详情与summary逐字段对账表.csv"
        rows = list(csv.reader(open(p, encoding="utf-8")))
        rows.append(["产物哈希", "abc...", sha(env["src"] / "summary.json"),
                     "manifest.json / files.summary.json.sha256", "一致", "真实文件SHA256核验"])
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        write_inventory(env["deliv"], env["root"])
    results.append(run_case("N4 manifest 谎称（manifest 不存在却声称哈希来源）", n4, ["C2"]))

    # N5 清单含自身且写成空文件
    def n5(env):
        write_inventory(env["deliv"], env["root"], include_self=True)
    results.append(run_case("N5 清单自指（自身被写成 size=0 空哈希）", n5, ["C3"]))

    # N6 重复截图改名：state_signal_normal 复制自 view_02
    def n6(env):
        shutil.copyfile(env["deliv"] / "view_02.png", env["deliv"] / "state_signal_normal.png")
        write_inventory(env["deliv"], env["root"])
    results.append(run_case("N6 重复截图改名（state 复制自 view）", n6, ["C4"]))

    # N7 字段路径错：写 trading_summary.* 但实际在 summary_variant_a.*
    def n7(env):
        p = env["deliv"] / "04_回测run详情与summary逐字段对账表.csv"
        rows = list(csv.reader(open(p, encoding="utf-8")))
        for r in rows[1:]:
            r[3] = r[3].replace("summary_variant_a.", "trading_summary.")
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        write_inventory(env["deliv"], env["root"])
    results.append(run_case("N7 字段路径错（trading_summary 层不存在）", n7, ["C1"]))

    # N8 数量矛盾：报告写 24 张，实际 3 张
    def n8(env):
        (env["deliv"] / "00_报告.md").write_text(
            "# 测试报告\n\n本轮共 24 张高清测试截图（2 个 View 视图 + 1 张独立 State 状态覆盖截图）。\n",
            encoding="utf-8")
        write_inventory(env["deliv"], env["root"])
    results.append(run_case("N8 数量矛盾（报告 24 张，实际 3 张）", n8, ["C4"]))

    ok = sum(1 for r in results if r["识别成功"])
    rec = {
        "工单": "UI-EVIDENCE-04 / 离线反例",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "校验库": "queries/lib_verifier.py",
        "场景数": len(results),
        "识别成功数": ok,
        "全部识别": ok == len(results),
        "场景": results,
    }
    outp = OUT / "negative_tests.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"离线反例：{ok}/{len(results)} 识别成功\n")
    for r in results:
        mark = "✓" if r["识别成功"] else "✗"
        print(f"{mark} {r['场景']}")
        print(f"    期望失败 {r['期望失败项']} | 实际失败 {r['实际失败项']} | 总判定 {r['总判定']}")
        for k, why in r["失败原因摘录"].items():
            print(f"      {k}: {why[:110]}")
    print(f"\n落盘: {outp}")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
