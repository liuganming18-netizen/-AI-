# -*- coding: utf-8 -*-
"""31｜重建写前快照（**重建件，不是原件**，如实标注）。

背景：`20_隔离演练.py` 早期版本的 `reset_iso()` 会 `rmtree(isolated/)`，把
`30_生产接入.py` 写的 `isolated/pre_import/live_signals_before_import.sqlite3`
（生产库写前 0 行的快照）一起删掉了。写前快照只能在生产库**还没被本单写过**时生成，
所以原件不可恢复。

本脚本做两件事：
  1) 按可验证的方式重建一份「等价写前状态」：拷当前生产库 → 删掉本单写入的 289 条研究行
     → 核对「剩余行数 = 0」，与 `out/30_生产接入.json` 记录的 `rows_before=0` 对齐；
  2) 把重建方式、核对结果、sha256 一起写进 `isolated/pre_import/重建说明.json`，
     不冒充原件。

输出：out/31_写前快照重建.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

PRE = L.ISO / "pre_import"
DST = PRE / "live_signals_before_import.sqlite3"


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc(), "性质": "重建件（原件已被 20 的 reset_iso 误删）"}
    PRE.mkdir(parents=True, exist_ok=True)

    # 「写前 0 行」这个事实的**不可变出处**：08-WORK 原始交付里的只读探测
    # （`../out/03_隔离与空链路.json` → 隔离链路/生产库只读探测/rows_before）。
    # 不用 `out/30_生产接入.json`：30 是幂等脚本，复跑会把「写前行数」更新为当时的值（289）。
    src08 = L.WORK08 / "out" / "03_隔离与空链路.json"
    rows_before_08 = None
    if src08.exists():
        d08 = json.loads(src08.read_text(encoding="utf-8"))
        rows_before_08 = (((d08.get("隔离链路") or {}).get("生产库只读探测") or {})
                          .get("rows_before"))
    rep["08-WORK记录的写前行数"] = rows_before_08
    rep["08-WORK出处"] = str(src08)

    # 30 的当前记录（幂等脚本，可能是复跑后的值）——只作对照，不作判据
    rec30 = json.loads((L.OUT / "30_生产接入.json").read_text(encoding="utf-8"))
    rows_before_30 = ((rec30.get("导入") or {}).get("stats") or {}).get("rows_before")
    rep["30当前记录的写前行数（复跑值，仅对照）"] = rows_before_30
    rep["30记录的原件sha256"] = (rec30.get("写前快照") or {}).get("sha256")

    # 拷当前生产库，再删本单写入的研究行
    rep["拷贝"] = L.backup_db(L.LIVE_DB, DST)
    con = sqlite3.connect(str(DST))
    try:
        con.execute("PRAGMA busy_timeout=15000")
        n_all = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        n_res = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research' "
            "AND json_extract(payload_json,'$.source')='antigravity_08_backtest'"
        ).fetchone()[0]
        con.execute(
            "DELETE FROM live_signal WHERE origin='historical_research' "
            "AND json_extract(payload_json,'$.source')='antigravity_08_backtest'")
        con.commit()
        con.execute("VACUUM")
        n_left = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    finally:
        con.close()

    fp = L.fingerprint(DST)
    rep["重建结果"] = {
        "拷贝时行数": n_all, "删除的研究行数": n_res, "删除后行数": n_left,
        "文件": str(DST), "bytes": fp.get("size"), "sha256": fp.get("sha256"),
        "与08-WORK记录的写前行数一致": n_left == rows_before_08,
    }
    note = {
        "性质": "重建件",
        "为什么重建": "20_隔离演练.py 早期版本 reset_iso() 会删掉整个 isolated/，"
                     "把 30 写的写前快照一起删除（脚本缺陷，已修：reset_iso 现在保留 pre_import/）",
        "重建方式": "拷贝当前生产库 → 精确删除 origin='historical_research' 且 "
                 "payload.source='antigravity_08_backtest' 的行 → VACUUM",
        "可验证的等价性": f"删除后剩余 {n_left} 行，与 08-WORK 只读探测记录的写前行数 "
                    f"{rows_before_08} 一致（出处 {src08}）",
        "不等价的地方": [
            "文件字节布局（页分配、freelist、sqlite_sequence）与原件不可能逐位相同",
            "原件 sha256 = 18de9c2fbb553deeb956c51c2dbfdc448588b4e97f58dc22954c09f3e47e6449"
            "（45,056 B，第一次接入时记在 30 的产出里；该记录已被复跑覆盖，此值为**转引**）",
            "因此本件只用于「把库恢复到写前的内容状态」，不能用来证明「原件未被篡改」",
        ],
        "生成时刻": L.now_utc(),
        "sha256": fp.get("sha256"),
    }
    (PRE / "重建说明.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8")
    rep["重建说明"] = note

    checks = {
        "删除的研究行数 = 289": n_res == 289,
        "删除后行数 = 0": n_left == 0,
        "与 08-WORK 只读探测记录的写前行数一致": n_left == rows_before_08,
        "重建件已落盘且有 sha256": fp.get("exists") is True and bool(fp.get("sha256")),
        "重建说明已落盘": (PRE / "重建说明.json").exists(),
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = ("写前状态已重建并与 08-WORK 的只读探测记录对齐（0 行）；"
                       "但这是重建件，原件 sha256 只能从 30 的记录里查，不能冒充原件。"
                       if not fail else f"存在未通过项：{fail}")

    p = L.dump("31_写前快照重建.json", rep)
    print("== 31 写前快照重建 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
