# -*- coding: utf-8 -*-
"""08｜重启恢复与生产隔离核验

为什么单独一段
--------------
04 脚本验的是「同一进程内重复扫描不重置期限」，这不足以证明**进程重启后**还能接上：
研究侧要能跨进程读出既有事件、候选序号续接、冻结列不被改。所以本段用**真子进程**
（不是同进程重开连接）做三段接力：写 → 重启只读核验 → 重启追加并再核验。

隔离不能只看「我把库放在 research 目录」。必须同时证明：
  ① 研究进程没有打开/写入任何生产库；
  ② 研究写入前后，生产库的写入痕迹里没有研究标记（方案名、研究标的）；
  ③ 研究代码里的路径常量全部指向 research 根，没有一条回落生产。

判据说明（避免假通过）
----------------------
* 生产纸账本 `paper_3a.sqlite3` 由后台纸交易进程持续写入 ⇒ **sha 变化是正常的**，
  不能用「sha 没变」当隔离判据。改用「研究标记查询 + 写入归属」判断。
* `market_v2.sqlite3` 由采集器实时写 ⇒ 本段完全不碰它，也不拿它做判据。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]                       # 工程根
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "框架" / "backend"))

PY = sys.executable
OUT = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
RESEARCH_ROOT = PROJECT / "数据" / "research" / "ACCUM-EARLY-01"

PROD_LIVE = PROJECT / "框架" / "backend" / "live" / "live_signals.sqlite3"
PROD_LEDGERS = [PROJECT / "数据" / "paper" / f"paper_3{a}.sqlite3" for a in "abcd"]

RESEARCH_MODULES = [
    PROJECT / "框架" / "backend" / "observe_store.py",
    PROJECT / "框架" / "backend" / "accum_research.py",
    PROJECT / "框架" / "backend" / "research_15m.py",
    PROJECT / "框架" / "backend" / "oi_feed.py",
]

FORBIDDEN_WRITE_TOKENS = [
    "live_signals.sqlite3", "paper_3a.sqlite3", "paper_3b.sqlite3",
    "paper_3c.sqlite3", "paper_3d.sqlite3",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_db(p: Path) -> dict:
    if not p.exists():
        return {"path": str(p), "exists": False}
    d = {"path": str(p), "exists": True, "sha256": sha256(p),
         "size": p.stat().st_size, "mtime_utc": time.strftime(
             "%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        d["tables"] = {}
        for t in tabs:
            try:
                n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                d["tables"][t] = n
            except Exception as e:
                d["tables"][t] = f"ERR:{e}"
        con.close()
    except Exception as e:
        d["read_error"] = str(e)
    return d


def research_markers_in_production() -> dict:
    """在生产库里找研究标记：方案名、研究事件 ID 前缀、研究标的。

    这是「隔离是否真的没接触」的直接判据——比 sha 稳定，因为后台进程写自己的账
    不会写出这些字符串。
    """
    markers = {"4a": 0, "4b": 0, "4c": 0, "ACCUM": 0, "ev_4": 0, "cd_ev_": 0}
    hits = []
    for p in [PROD_LIVE] + PROD_LEDGERS:
        if not p.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        except Exception:
            continue
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tabs:
                try:
                    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
                except Exception:
                    continue
                text_cols = []
                for c in cols:
                    try:
                        ty = con.execute(
                            f'SELECT typeof("{c}") FROM "{t}" WHERE "{c}" IS NOT NULL LIMIT 1'
                        ).fetchone()
                        if ty and ty[0] == "text":
                            text_cols.append(c)
                    except Exception:
                        pass
                for c in text_cols:
                    for mk in markers:
                        try:
                            n = con.execute(
                                f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" LIKE ?',
                                (f"%{mk}%",)).fetchone()[0]
                        except Exception:
                            continue
                        if n:
                            markers[mk] += n
                            hits.append({"db": p.name, "table": t, "column": c,
                                         "marker": mk, "rows": n})
        finally:
            con.close()
    return {"counts": markers, "hits": hits,
            "verdict": "clean" if sum(markers.values()) == 0 else "contaminated"}


# ────────────────────────────── 子进程角色 ──────────────────────────────
def child_write(db: str, tag: str = "restart_probe"):
    """子进程一：建档 + 3 条候选。"""
    from observe_store import ObserveStore
    st = ObserveStore(db)
    ev = st.create_event(
        scheme="4a", symbol="RESTARTTESTUSDT", venue="binance",
        strategy_version="ACCUM-RESEARCH-v0", param_version="pending",
        box_version_id="bx_restart_1", structure_seen_ms=1790000000000,
        confirmed_ms=1790000600000, expires_at_ms=1791209600000,
        old_low=1.11, sweep_low=1.09, vwap_anchor_ms=1790000000000,
        vwap_anchor_source="cold_15m_vwap", state="BOX",
        evidence={"tag": tag})
    for i in range(3):
        st.add_candidate(ev["event_id"], scheme="4a",
                         created_ms=1790000600000 + i * 60000,
                         local_support_price=1.12 + i * 0.01,
                         entry_price_ref=1.13 + i * 0.01)
    st.transition(ev["event_id"], "BOX", "SWEEP", "跌破箱体下沿后收回",
                  1790001200000)
    st.transition(ev["event_id"], "SWEEP", "RETEST", "回抽至下沿附近",
                  1790001800000)
    st.add_data_gap(ev["event_id"], "15m", "回踩区间 15m 缺 2 根",
                    from_ms=1790001500000, to_ms=1790001800000)
    st.close()
    print(json.dumps({"event_id": ev["event_id"], "created": ev["created"]},
                     ensure_ascii=False))


def child_reopen_readonly(db: str):
    """子进程二：重启后只读核验，不改任何东西。"""
    from observe_store import ObserveStore
    st = ObserveStore(db)
    rows = st.con.execute("SELECT * FROM observe_event").fetchall()
    ev = dict(rows[0])
    cands = [dict(r) for r in st.con.execute(
        "SELECT * FROM observe_candidate ORDER BY seq_in_event").fetchall()]
    trans = [dict(r) for r in st.con.execute(
        "SELECT * FROM observe_transition ORDER BY at_ms, id").fetchall()]
    gaps = [dict(r) for r in st.con.execute(
        "SELECT * FROM observe_data_gap").fetchall()]
    st.close()
    print(json.dumps({
        "event": ev, "candidates": cands,
        "transition_count": len(trans),
        "transition_states": [t["to_state"] for t in trans],
        # 事件级迁移 = candidate_id 为空的那几条；候选迁移单独列出，
        # 否则 WAIT_ENTRY 会混进事件状态链，比出来的序列不是状态机口径
        "event_level_states": [t["to_state"] for t in trans
                               if t["candidate_id"] is None],
        "candidate_level_states": [t["to_state"] for t in trans
                                   if t["candidate_id"] is not None],
        "gap_count": len(gaps),
    }, ensure_ascii=False, default=str))


def child_reopen_append(db: str):
    """子进程三：重启后追加候选，验序号续接。"""
    from observe_store import ObserveStore
    st = ObserveStore(db)
    ev_id = st.con.execute("SELECT event_id FROM observe_event").fetchone()[0]
    # 重复建档必须命中既有事件、且期限不被重置
    again = st.create_event(
        scheme="4a", symbol="RESTARTTESTUSDT", venue="binance",
        strategy_version="ACCUM-RESEARCH-v0", param_version="pending",
        box_version_id="bx_restart_1", structure_seen_ms=1790000000000,
        confirmed_ms=1790000600000, expires_at_ms=1799999999000,
        state="BOX")
    cd = st.add_candidate(ev_id, scheme="4a", created_ms=1790002400000,
                          local_support_price=1.16, entry_price_ref=1.17)
    # 冻结列必须仍不可改
    frozen_blocked = None
    try:
        st.con.execute("UPDATE observe_event SET old_low=9.99 WHERE event_id=?",
                       (ev_id,))
        st.con.commit()
        frozen_blocked = False
    except sqlite3.DatabaseError as e:
        frozen_blocked = True
        frozen_reason = str(e)
    st.close()
    print(json.dumps({"event_id": ev_id, "dup_created": again["created"],
                      "dup_expires_at_ms": again["expires_at_ms"],
                      "new_seq": cd["seq_in_event"],
                      "frozen_blocked": frozen_blocked,
                      "frozen_reason": locals().get("frozen_reason")},
                     ensure_ascii=False))


def run_child(role: str, db: str) -> dict:
    r = subprocess.run([PY, str(Path(__file__).resolve()), "--child", role,
                        "--db", db], capture_output=True, text=True,
                       cwd=str(PROJECT))
    if r.returncode != 0:
        return {"ok": False, "role": role, "stderr": r.stderr[-2000:]}
    try:
        return {"ok": True, "role": role, "data": json.loads(r.stdout)}
    except Exception as e:
        return {"ok": False, "role": role, "stdout": r.stdout[-2000:],
                "parse_error": str(e)}


# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    errors = []
    out = {"probe": "08_隔离与重启恢复",
           "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime())}

    # ── A 段：真进程重启接力 ────────────────────────────────────────────
    tmpdir = Path(tempfile.mkdtemp(prefix="accum_restart_"))
    db = tmpdir / "observe_restart.sqlite3"

    s1 = run_child("write", str(db))
    s2 = run_child("readonly", str(db))
    s3 = run_child("append", str(db))

    a = {"strength": "behavior_real_process_restart",
         "tmp_db": str(db),
         "子进程1_建档": s1, "子进程2_重启只读核验": s2,
         "子进程3_重启追加": s3, "assertions": {}}

    if not (s1["ok"] and s2["ok"] and s3["ok"]):
        a["verdict"] = "fail"
        errors.append("重启接力子进程失败")
        out["A_重启恢复"] = a
    else:
        ev1 = s1["data"]
        ev2 = s2["data"]
        ev3 = s3["data"]
        ev = ev2["event"]
        a["assertions"]["事件跨进程仍在"] = ev["event_id"] == ev1["event_id"]
        a["assertions"]["冻结列跨进程逐位一致"] = (
            float(ev["old_low"]) == 1.11
            and float(ev["sweep_low"]) == 1.09
            and int(ev["vwap_anchor_ms"]) == 1790000000000
            and ev["vwap_anchor_source"] == "cold_15m_vwap"
            and int(ev["structure_seen_ms"]) == 1790000000000
            and int(ev["expires_at_ms"]) == 1791209600000
            and ev["box_version_id"] == "bx_restart_1")
        a["assertions"]["候选序号跨进程续接为1_2_3"] = (
            [c["seq_in_event"] for c in ev2["candidates"]] == [1, 2, 3])
        a["assertions"]["候选内容跨进程未丢"] = (
            all(c["local_support_price"] is not None
                for c in ev2["candidates"]))
        a["assertions"]["状态迁移链跨进程完整"] = (
            ev2["event_level_states"] == ["BOX", "SWEEP", "RETEST"])
        a["assertions"]["候选级迁移未混入事件状态链"] = (
            ev2["candidate_level_states"] == ["WAIT_ENTRY"] * 3)
        a["assertions"]["数据缺口跨进程保留"] = ev2["gap_count"] == 1
        a["assertions"]["重启后重复建档不新建"] = ev3["dup_created"] is False
        a["assertions"]["重启后期限未被重置为新值"] = (
            int(ev3["dup_expires_at_ms"]) == 1791209600000)
        a["assertions"]["重启后追加候选序号为4"] = int(ev3["new_seq"]) == 4
        a["assertions"]["重启后冻结列仍不可改"] = ev3["frozen_blocked"] is True
        a["frozen_reason"] = ev3.get("frozen_reason")
        a["verdict"] = ("pass" if all(a["assertions"].values()) else "fail")
        if a["verdict"] != "pass":
            errors.append("重启恢复断言未全过")
    out["A_重启恢复"] = a

    # ── B 段：生产隔离指纹 ──────────────────────────────────────────────
    before = {"live_signals": snapshot_db(PROD_LIVE),
              "ledgers": {p.name: snapshot_db(p) for p in PROD_LEDGERS}}
    markers_before = research_markers_in_production()

    # 研究侧做一次真实写入（写研究根下的独立库，不碰生产）
    research_write = None
    try:
        from observe_store import ObserveStore
        isodb = RESEARCH_ROOT / "observe_isolation_probe.sqlite3"
        st = ObserveStore(isodb)
        iso = st.assert_isolated()
        ev = st.create_event(
            scheme="4b", symbol="ISOLATIONPROBEUSDT", venue="binance",
            strategy_version="ACCUM-RESEARCH-v0", param_version="pending",
            box_version_id="bx_iso_1", structure_seen_ms=1790100000000,
            confirmed_ms=1790100600000, expires_at_ms=1791309600000,
            old_low=2.0, sweep_low=1.95, state="BOX",
            evidence={"purpose": "隔离探针"})
        st.add_candidate(ev["event_id"], scheme="4b",
                         created_ms=1790100600000, local_support_price=2.01)
        st.close()
        research_write = {"db": str(isodb), "event_id": ev["event_id"],
                          "assert_isolated": iso,
                          "under_research_root": str(isodb.resolve()).startswith(
                              str(RESEARCH_ROOT.resolve()))}
    except Exception as e:
        research_write = {"error": f"{type(e).__name__}: {e}"}
        errors.append("研究写入失败")

    time.sleep(1.0)
    after = {"live_signals": snapshot_db(PROD_LIVE),
             "ledgers": {p.name: snapshot_db(p) for p in PROD_LEDGERS}}
    markers_after = research_markers_in_production()

    b = {"strength": "fingerprint_before_after_research_write",
         "研究写入": research_write,
         "before": before, "after": after,
         "研究标记_写前": markers_before, "研究标记_写后": markers_after,
         "assertions": {}}
    b["assertions"]["研究库在research根下"] = bool(
        research_write and research_write.get("under_research_root"))
    b["assertions"]["assert_isolated通过"] = bool(
        research_write and research_write.get("assert_isolated"))
    b["assertions"]["研究写入后生产库无研究标记"] = (
        markers_after["verdict"] == "clean")
    b["assertions"]["研究标记数写前后一致"] = (
        markers_before["counts"] == markers_after["counts"])
    # sha 变化只做记录，不做判据（后台进程持续写入）
    changed = []
    for name, key in (("live_signals", "live_signals"),):
        if before[key].get("sha256") != after[key].get("sha256"):
            changed.append(name)
    for n in before["ledgers"]:
        if before["ledgers"][n].get("sha256") != after["ledgers"][n].get("sha256"):
            changed.append(n)
    b["sha_changed_during_window"] = changed
    b["sha_note"] = ("生产纸账本与信号库由后台进程持续写入 ⇒ sha 变化属正常，"
                     "不构成隔离判据；隔离判据只用「研究标记查询」与「研究库路径」。")
    b["verdict"] = ("pass" if all(b["assertions"].values()) else "fail")
    if b["verdict"] != "pass":
        errors.append("生产隔离断言未全过")
    out["B_生产隔离"] = b

    # ── C 段：研究模块路径常量静态断言 ──────────────────────────────────
    # 判据不能只看「源码里出现生产库文件名」——`observe_store.PRODUCTION_DBS`
    # 正是用来**断言不指向生产库**的对照常量，出现文件名是合规的。
    # 真正的违规只有一种：以**可写方式**连接生产库（无 mode=ro）。
    c = {"strength": "static_ast_assertion",
         "rule": "违规 = sqlite3.connect 指向生产库且未带 mode=ro；"
                 "仅把生产库路径当对照常量比较不算违规",
         "modules": {}}
    c["assertions"] = {}
    ok_all = True
    for m in RESEARCH_MODULES:
        if not m.exists():
            c["modules"][m.name] = {"exists": False}
            ok_all = False
            continue
        src = m.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            c["modules"][m.name] = {"exists": True, "parse_error": str(e)}
            ok_all = False
            continue
        writable_hits = []
        readonly_hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", ""))
            if name != "connect" or not node.args:
                continue
            arg = node.args[0]
            try:
                seg = ast.get_source_segment(src, arg) or ""
            except Exception:
                seg = ""
            if not any(tok in seg for tok in FORBIDDEN_WRITE_TOKENS):
                continue
            ro = "mode=ro" in seg
            for kw in node.keywords:
                v = ast.get_source_segment(src, kw.value) or ""
                if "mode=ro" in v:
                    ro = True
            item = {"line": node.lineno, "arg": seg[:120],
                    "mode_ro": ro}
            (readonly_hits if ro else writable_hits).append(item)
        c["modules"][m.name] = {
            "exists": True, "bytes": len(src.encode()),
            "writable_production_connects": writable_hits,
            "readonly_production_connects": readonly_hits,
        }
        if writable_hits:
            ok_all = False
    c["assertions"]["研究模块无以可写方式连生产库"] = ok_all
    c["verdict"] = "pass" if ok_all else "fail"
    if not ok_all:
        errors.append("研究模块里出现可写连接生产库")
    out["C_路径常量"] = c

    out["errors"] = errors
    out["summary"] = {
        "A": out["A_重启恢复"]["verdict"],
        "B": out["B_生产隔离"]["verdict"],
        "C": out["C_路径常量"]["verdict"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "08_隔离与重启恢复.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    print("written:", dst)
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    if "--child" in sys.argv:
        role = sys.argv[sys.argv.index("--child") + 1]
        dbp = sys.argv[sys.argv.index("--db") + 1]
        {"write": child_write, "readonly": child_reopen_readonly,
         "append": child_reopen_append}[role](dbp)
    else:
        main()
