# -*- coding: utf-8 -*-
"""第1步：锁定唯一一次有效运行。

要做的事只有三件：
  1. 判断 FT-BASELINE-04 是「引用原结果」还是「产生新结果」；
  2. 选定唯一结果包，记录文件名、sha256、大小、时间、策略与配置指纹；
  3. 校验包内策略与目录内策略是否同源，防止拿错版本。
不跨版本拼接数据：选定后只读这一个包。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (EVID, OUT, last_result_pointer, load_bundle, pick_target,
                    rel, result_zips, sha256_file, sha256_bytes, ts, write_json)

LOG = []


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    print(line)


def main() -> int:
    target, probe = pick_target()
    log("基线目录探测：")
    for p in probe:
        log(f"  {p['目录']}: 存在={p['存在']} 结果包={p['结果包数']} 指针={p['指针'] or '无'}")

    b04 = EVID / "FT-BASELINE-04"
    b03 = EVID / "FT-BASELINE-03"
    relation = {
        "FT-BASELINE-04存在": b04.exists(),
        "FT-BASELINE-04产出新结果包": False,
        "关系判定": "未判定",
    }
    if b04.exists():
        z4 = result_zips(b04)
        z3 = result_zips(b03)
        h3 = {z.name: sha256_file(z, 64) for z in z3}
        if z4:
            h4 = {z.name: sha256_file(z, 64) for z in z4}
            same = [n for n in h4 if h4[n] in h3.values()]
            relation["FT-BASELINE-04产出新结果包"] = len(same) == 0
            relation["FT-BASELINE-04结果包"] = list(h4)
            relation["与03同哈希的包"] = same
            relation["关系判定"] = ("产生新结果（包哈希与03不同）" if not same
                                 else "引用原结果（与03同哈希）")
            # 新包 ≠ 新结论：逐笔是否一致才是关键
            if not same and z3:
                try:
                    t4 = load_bundle(z4[-1]).trades
                    t3 = load_bundle(z3[-1]).trades
                    key = lambda t: (t.get("pair"), str(t.get("open_date")),
                                     float(t.get("open_rate")), float(t.get("amount")),
                                     float(t.get("close_rate")), t.get("exit_reason"),
                                     round(float(t.get("profit_abs", 0)), 8),
                                     round(float(t.get("funding_fees", 0)), 8))
                    a, bb = sorted(map(key, t3)), sorted(map(key, t4))
                    relation["逐笔与03完全一致"] = (a == bb)
                    relation["03笔数"] = len(a)
                    relation["04笔数"] = len(bb)
                except Exception as e:
                    relation["逐笔比对"] = f"失败: {e}"
        else:
            relation["关系判定"] = "尚无结果包（仅目录与配置，运行未完成或未开始）"

    if target is None:
        write_json(OUT / "00_锁定与版本.json",
                   {"生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "状态": "无可锁定结果包", "探测": probe, "关系": relation})
        log("没有可锁定的结果包，终止。")
        return 2

    zips = result_zips(target)
    pointer = last_result_pointer(target)
    zp = next((z for z in zips if z.name == pointer), zips[-1])
    log(f"锁定目录：{rel(target)}")
    log(f"指针指向：{pointer or '无'}")
    log(f"锁定结果包：{zp.name}")
    if len(zips) > 1:
        log(f"注意：目录内有 {len(zips)} 个结果包，按指针取 {zp.name}，其余不参与计算："
            + ", ".join(z.name for z in zips if z.name != zp.name))

    b = load_bundle(zp)
    strat_file = target / "user_data" / "strategies" / "WyckoffEventTrendBaseline.py"
    cfg_file = target / "user_data" / "config.json"
    file_sha = sha256_file(strat_file, 64) if strat_file.exists() else ""
    log(f"包内策略指纹 {b.embedded_strategy_sha[:16]}… / 目录策略指纹 {file_sha[:16]}…"
        f" → {'同源' if b.embedded_strategy_sha == file_sha else '不同（需说明）'}")

    # 结果包内自报的运行窗口
    log(f"策略名：{b.strategy_name}；交易笔数：{len(b.trades)}")
    log(f"结果包自报窗口：{b.metrics.get('backtest_start')} ~ {b.metrics.get('backtest_end')}")
    log(f"初始资金：{b.metrics.get('starting_balance')}；最终余额：{b.metrics.get('final_balance')}")

    logfile = target / "logs" / "freqtrade_backtest_5symbols.log"
    log_span = None
    if logfile.exists():
        txt = logfile.read_text(errors="replace").splitlines()
        head = txt[0][:23] if txt else ""
        dated = [x[:23] for x in txt if len(x) > 23 and x[:2] == "20" and x[4] == "-"]
        tail = dated[-1] if dated else ""
        log_span = {"首行时间": head, "末行时间": tail, "行数": len(txt),
                    "sha256": sha256_file(logfile, 64)}
        log(f"运行日志：{log_span['首行时间']} ~ {log_span['末行时间']}（{len(txt)} 行）")

    versions = {}
    vf = target / "out" / "versions.json"
    if vf.exists():
        try:
            versions = json.loads(vf.read_text())
        except Exception:
            versions = {}

    lock = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "任务": "FT-METRICS-03",
        "锁定基线目录": rel(target),
        "锁定结果包": {
            "文件名": zp.name,
            "相对路径": rel(zp),
            "sha256": sha256_file(zp, 64),
            "大小字节": zp.stat().st_size,
            "修改时间": ts(zp),
        },
        "目录内其它结果包": [z.name for z in zips if z.name != zp.name],
        "指针文件指向": pointer,
        "策略指纹": {
            "包内": b.embedded_strategy_sha,
            "目录内": file_sha,
            "同源": b.embedded_strategy_sha == file_sha,
        },
        "配置指纹": sha256_file(cfg_file, 64) if cfg_file.exists() else "",
        "运行参数": {
            "策略": b.strategy_name,
            "初始资金_USDT": b.metrics.get("starting_balance"),
            "最大同时持仓": b.config.get("max_open_trades"),
            "时间框架": b.config.get("timeframe"),
            "交易模式": b.config.get("trading_mode"),
            "白名单": b.config.get("exchange", {}).get("pair_whitelist"),
            "自报窗口": [b.metrics.get("backtest_start"), b.metrics.get("backtest_end")],
        },
        "运行日志": log_span,
        "基线自报版本": versions,
        "FT-BASELINE-04关系": relation,
        "基线探测": probe,
        "是否混用其它版本数据": False,
        "混用说明": "只读锁定包；FT-BASELINE-02 的逐笔、指标、日志一律不参与本轮计算。",
    }
    write_json(OUT / "00_锁定与版本.json", lock)
    log("已写 out/00_锁定与版本.json")

    (OUT / "_lock_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    print(json.dumps({"status": "OK", "zip": zp.name,
                      "trades": len(b.trades),
                      "relation04": relation["关系判定"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
