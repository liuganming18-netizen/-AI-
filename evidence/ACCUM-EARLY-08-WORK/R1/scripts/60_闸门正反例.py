# -*- coding: utf-8 -*-
"""60｜批准闸门正反例（生产库只读；所有「改坏条件」实验只在副本上做）。

三件事：
  A. 生产库上真调一次路由（origin=live / recovered / historical_research）：
     处理 0 条、登记 0 条、不写账本 —— 这是「研究信号进不了下单路径」的正面证据
  B. 副本上做「单层失效」矩阵：逐层把防护条件改坏，看第几层才漏
     （结论决定「我们到底靠什么挡住它」）
  C. 失败原因可见：289 条的不可执行原因、批准阻塞项、缺口都必须能读到

输出：out/60_闸门正反例.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

WORKER = Path(__file__).resolve().parent / "60b_闸门矩阵_worker.py"
PROBE = L.ISO / "gate_probe"

WATCH = {
    "行情库": L.MARKET_DB,
    "纸面账本_3a": L.PAPER_DIR / "paper_3a.sqlite3",
    "纸面账本_3b": L.PAPER_DIR / "paper_3b.sqlite3",
    "纸面账本_3c": L.PAPER_DIR / "paper_3c.sqlite3",
    "纸面账本_3d": L.PAPER_DIR / "paper_3d.sqlite3",
}


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    PROBE.mkdir(parents=True, exist_ok=True)

    before = {k: L.fingerprint(p) for k, p in WATCH.items()}
    rep["生产信号库_前"] = L.fingerprint(L.LIVE_DB)

    # 副本（生产当前状态：289 条研究行）
    copy_db = PROBE / "live_signals.sqlite3"
    rep["副本"] = {"live": L.backup_db(L.LIVE_DB, copy_db)}
    for arm in ("3a", "3b", "3c", "3d"):
        rep["副本"][f"paper_{arm}"] = L.backup_db(
            L.PAPER_DIR / f"paper_{arm}.sqlite3", PROBE / "paper" / f"paper_{arm}.sqlite3")

    env = dict(os.environ)
    env.update({
        "LIVE_DB": str(copy_db.resolve()),
        "PAPER_LEDGER_DIR": str((PROBE / "paper").resolve()),
        "PAPER_LOCK_DIR": str((PROBE / "locks").resolve()),
        "MARKET_DB": str(L.MARKET_DB),
        "PYTHONPATH": f"{L.PROJECT / '框架'}:{L.BACKEND}:{L.PROJECT}",
    })
    (PROBE / "locks").mkdir(parents=True, exist_ok=True)
    w = L.run([L.PY, str(WORKER)], env=env, timeout=600)
    parsed = None
    for line in (w["stdout"] or "").splitlines():
        if line.startswith("@@RESULT@@"):
            parsed = json.loads(line[len("@@RESULT@@"):])
    rep["矩阵worker"] = {"rc": w["returncode"], "elapsed_s": w["elapsed_s"],
                       "stderr_tail": (w["stderr"] or "")[-2000:]}
    if parsed is None:
        rep["矩阵worker"]["parse_error"] = True
        rep["矩阵worker"]["stdout_tail"] = (w["stdout"] or "")[-3000:]
    else:
        rep["矩阵"] = parsed

    # A. 生产库上真调路由（不写账本；origin 四种都试）
    prod_call = L.run([L.PY, str(Path(__file__).resolve().parent / "60c_生产路由只读.py")], env={
        **{k: v for k, v in os.environ.items()},
        "PYTHONPATH": f"{L.PROJECT / '框架'}:{L.BACKEND}:{L.PROJECT}",
        # 生产库只读使用；账本目录指向副本，万一有写也落在副本上
        "PAPER_LEDGER_DIR": str((PROBE / "paper").resolve()),
        "PAPER_LOCK_DIR": str((PROBE / "locks").resolve()),
        "MARKET_DB": str(L.MARKET_DB),
    }, timeout=300)
    for line in (prod_call["stdout"] or "").splitlines():
        if line.startswith("@@RESULT@@"):
            rep["生产路由"] = json.loads(line[len("@@RESULT@@"):])
    rep["生产路由_call"] = {"rc": prod_call["returncode"],
                          "stderr_tail": (prod_call["stderr"] or "")[-1500:]}

    # C. 失败原因可见（走真实 HTTP）
    import urllib.request
    with urllib.request.urlopen(
            L.API + "/api/v1/signals?scope=all&origin=historical_research&limit=1000",
            timeout=60) as resp:
        j = json.loads(resp.read().decode())
    items = j.get("items") or []
    reason_ok = sum(1 for i in items
                    if "approved=false" in (i.get("actionable_reason") or ""))
    rep["原因可见"] = {
        "条数": len(items),
        "带具体原因(含 approved=false)": reason_ok,
        "样本原因": (items[0].get("actionable_reason") if items else None),
        "样本可执行态": (items[0].get("executable_state") if items else None),
        "样本actionable": (items[0].get("actionable") if items else None),
    }
    con = L.ro_conn(L.LIVE_DB)
    try:
        rep["原因可见"]["payload批准阻塞项"] = {
            "有 approval_blockers 的条数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research' "
                "AND json_array_length(json_extract(payload_json,'$.approval_blockers'))>0"
            ).fetchone()[0],
            "有 gaps 的条数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research' "
                "AND json_array_length(json_extract(payload_json,'$.gaps'))>0").fetchone()[0],
            "样本 approval_blockers": json.loads(con.execute(
                "SELECT payload_json FROM live_signal WHERE origin='historical_research' "
                "ORDER BY signal_uid LIMIT 1").fetchone()[0]).get("approval_blockers"),
        }
        rep["原因可见"]["当前区排除明细"] = json.loads(json.dumps(
            (json.loads(urllib.request.urlopen(
                L.API + "/api/v1/signals?scope=current&limit=1", timeout=60).read().decode())
             .get("quality") or {}).get("excluded_breakdown"), ensure_ascii=False))
    finally:
        con.close()

    # 生产零写入复核
    rep["生产信号库_后"] = L.fingerprint(L.LIVE_DB)
    after = {k: L.fingerprint(p) for k, p in WATCH.items()}
    same = {k: L.same_file(before[k], after[k]) for k in before}
    ledgers = {k: v for k, v in same.items() if k != "行情库"}
    # 行情库由采集进程持续写入，文件必然变；改用「本进程 SQL 级探针 + 增长归因」
    market_now = after["行情库"]
    holders = L.run(["lsof", str(L.MARKET_DB)], timeout=30)
    holder_pids = sorted({ln.split()[1] for ln in (holders["stdout"] or "").splitlines()[1:]
                          if len(ln.split()) > 1})
    rep["生产零写入"] = {
        "四本账本": ledgers,
        "四本账本全部逐位未变": all(v["same"] for v in ledgers.values()),
        "行情库": {
            "前": before["行情库"], "后": market_now,
            "文件是否变": not same["行情库"]["same"],
            "变的原因": "行情库由采集进程持续写入（本单只读）；指纹变化不可归因于本单，"
                        "故另用 SQL 级写探针证明本进程 0 条写语句",
            "持有该库的进程": holder_pids,
        },
        "行情库写探针": {
            "矩阵worker": (rep.get("矩阵") or {}).get("行情库写探针"),
            "生产路由": (rep.get("生产路由") or {}).get("行情库写探针"),
        },
        "生产信号库": {
            "前": rep["生产信号库_前"].get("sha256"),
            "后": rep["生产信号库_后"].get("sha256"),
            "未变": rep["生产信号库_前"].get("sha256") == rep["生产信号库_后"].get("sha256"),
        },
    }

    m = rep.get("矩阵") or {}
    pr = rep.get("生产路由") or {}
    checks = {
        "矩阵 worker 正常结束": rep["矩阵worker"]["rc"] == 0,
        "矩阵确认在副本上跑（非生产库）": m.get("自报", {}).get("is_production") is False,
        "基线：289 条研究行扫不到（scan(None)=0）":
            (m.get("A_基线（原样）") or {}).get("scan(None)") == 0,
        "正向对照：合成 live+enterable 能扫到（scan(None)=1）":
            (m.get("B_正向对照") or {}).get("合成 live+enterable 被扫到(scan(None))") == 1,
        "正向对照：删除后归零":
            (m.get("B_正向对照") or {}).get("删除后(scan(None))") == 0,
        "只改坏 L1（可执行态）仍扫不到 ⇒ L2 独立生效":
            (m.get("C1_只改坏L1(executable_state=enterable)") or {}).get("scan(None)") == 0,
        "再改坏 L2（角色）仍扫不到 ⇒ L3 独立生效":
            (m.get("C2_再改坏L2(role=enterable_arm_entry)") or {}).get("scan(None)") == 0,
        "再改坏 L3（臂）仍扫不到 ⇒ L4 独立生效":
            (m.get("C3_再改坏L3(arm=3a)") or {}).get("scan(None)") == 0,
        "L1–L4 全坏后才扫得到（说明四层缺一不可）":
            (m.get("C4_再改坏L4(available_at=confirmed_at)") or {}).get("scan(None)") == 289,
        "五层全坏的单条行：origin=live 过滤仍挡住":
            (m.get("D_五层全坏的单条研究行") or {}).get("scan(live)") == 0,
        "生产路由 origin=live 处理 0 条": pr.get("route(live)", {}).get("processed") == 0,
        "生产路由 origin=recovered 处理 0 条": pr.get("route(recovered)", {}).get("processed") == 0,
        "生产路由 origin=historical_research 处理 0 条":
            pr.get("route(historical_research)", {}).get("processed") == 0,
        "生产路由三种 origin 登记 0 单":
            all(pr.get(f"route({o})", {}).get("registered") == 0
                for o in ("live", "recovered", "historical_research")),
        "289 条原因都写明了 approved=false": reason_ok == len(items) == 289,
        "289 条都带批准阻塞项": rep["原因可见"]["payload批准阻塞项"]["有 approval_blockers 的条数"] == 289,
        "289 条都带缺口说明": rep["原因可见"]["payload批准阻塞项"]["有 gaps 的条数"] == 289,
        "当前区排除明细可见（289 条被排除）":
            (rep["原因可见"]["当前区排除明细"] or {}).get("excluded") == 289,
        "四本账本逐位未变（sha256）": rep["生产零写入"]["四本账本全部逐位未变"] is True,
        "矩阵 worker 对行情库 0 条写语句（SQL 级）":
            ((rep.get("矩阵") or {}).get("行情库写探针") or {}).get("行情库写语句数") == 0,
        "生产路由进程对行情库 0 条写语句（SQL 级）":
            ((rep.get("生产路由") or {}).get("行情库写探针") or {}).get("行情库写语句数") == 0,
        "生产信号库未被本单改动（指纹不变）":
            rep["生产零写入"]["生产信号库"]["未变"] is True,
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = (
        "研究信号在生产库上被四层独立条件挡住（改坏任一层都不漏，四层全坏才扫到）；"
        "生产自然路径（origin=live）另加第五层；三种 origin 真调路由均 0 处理 0 登记；"
        "不可执行原因与批准阻塞项逐条可读。"
        if not fail else f"存在未通过项：{fail}")

    p = L.dump("60_闸门正反例.json", rep)
    print("== 60 批准闸门正反例 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
