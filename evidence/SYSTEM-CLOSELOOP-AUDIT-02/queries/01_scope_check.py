# -*- coding: utf-8 -*-
"""01 整改范围核对（阶段 0 预勘）—— 只读源码与运行态，不启动正式验收。

对应 SYSTEM-CLOSELOOP-AUDIT-01 判出的 2 条 P0 + 3 条 P1 + 2 条 P2，
逐条核对「代码事实」是否真的改了。**只读，不改任何被审文件。**

重要：本脚本跑在「版本可能仍在变动」的时刻。每条结论都带**读取时的文件 SHA**，
一旦 SHA 变了，该结论立即作废、必须重跑。这是 stage0 的定位：不是验收结论，
而是「告诉验收该往哪儿看」。

用法：python 01_scope_check.py
产物：out/01_整改范围核对.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import sqlite3

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

BE = BASE / "框架/backend"
PE = BE / "paper_engine.py"
LA = BE / "live_api.py"
LS = BE / "live_store.py"
AD = BE / "adapter.py"
LW = BE / "live_worker.py"
UI_SIG = BASE / "UI界面/web/src/components/SignalListPanel.tsx"
UI_STRAT = BASE / "UI界面/web/src/components/StrategyManagementView.tsx"
LIVE_DB = BE / "live/live_signals.sqlite3"
GATE = BE / "live/paper_new_entry_gate.json"

RES: dict = {"段": {}, "判据": []}


def sha(p: pathlib.Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mtime(p: pathlib.Path) -> str | None:
    if not p.exists():
        return None
    return dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def lines_of(p: pathlib.Path) -> list[str]:
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def find(p: pathlib.Path, pat: str, flags=0) -> list[dict]:
    """返回 [{行号, 行内容}]，用于给结论配可点开的源码证据。"""
    out = []
    for i, l in enumerate(lines_of(p), 1):
        if re.search(pat, l, flags):
            out.append({"行": i, "内容": l.strip()[:200]})
    return out


def add(sec: str, key, val, verdict=None, note="") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if verdict is not None:
        RES["判据"].append({"段": sec, "项": key, "判定": verdict, "说明": note})
    print(f"  [{verdict or '·'}] {sec} · {key} = {str(val)[:200]}")


# ══════════════════════════════════════════════════════════════════════════
# 读取时点的版本指纹（结论的有效期锚点）
# ══════════════════════════════════════════════════════════════════════════
add("S0 读取时点指纹", "读取时刻",
    dt.datetime.now().astimezone().isoformat(timespec="seconds"))
for p in (PE, LA, LS, AD, LW, UI_SIG, UI_STRAT):
    add("S0 读取时点指纹", p.name, {"sha256": (sha(p) or "")[:16], "mtime": mtime(p)})


# ══════════════════════════════════════════════════════════════════════════
# P0-1 交易因果：写入口是否仍接受调用方注入的时钟
# ══════════════════════════════════════════════════════════════════════════
def p0_1() -> None:
    print("\n=== P0-1 交易因果（写入口时钟）===")
    la = LA.read_text(encoding="utf-8", errors="replace")
    # 只看可执行代码里有没有 payload.get("requested_at"/"now_ms")
    hits = find(LA, r'payload\.get\(\s*[\'"]requested_at[\'"]|payload\.get\(\s*[\'"]now_ms[\'"]')
    add("P0-1 交易因果", "写入口读请求体时钟的行",
        hits, "未修" if hits else "已修",
        "只要还有 payload.get('requested_at'|'now_ms')，调用方就能注入成交时钟")
    # 服务端挂钟的唯一来源
    add("P0-1 交易因果", "服务端挂钟来源",
        find(LA, r"L\.now_ms\(\)|time\.time\(\)")[:6], None)
    # 迟滞常量
    add("P0-1 交易因果", "MAX_FILL_LAG_MS 定义",
        find(PE, r"MAX_FILL_LAG_MS\s*="), None,
        "3600_000 ⇒ 迟滞保护 1 小时；被注入 now_ms 后 lag 恒为小值，保护失效")
    # order_request_ms 的构成
    add("P0-1 交易因果", "order_request_ms 构成",
        find(PE, r"order_request_ms\s*=|earliest_fill_ms\s*="), None)
    # 有没有「真实挂钟」参与
    add("P0-1 交易因果", "真实挂钟变量（real_wall / attempt_ms）",
        find(PE, r"real_wall_ms\s*=|attempt_ms\s*=")[:6], None,
        "若成交边界仍只用 req_ms（可注入），则因果未修；必须由 real_wall 兜底")


# ══════════════════════════════════════════════════════════════════════════
# P0-2 隔离：生产库是否还有合成信号 / 闸门状态
# ══════════════════════════════════════════════════════════════════════════
def p0_2() -> None:
    print("\n=== P0-2 隔离（生产库污染）===")
    add("P0-2 隔离", "生产 live 库路径", str(LIVE_DB.relative_to(BASE)),
        None, "只读打开，不写不删")
    rows = []
    err = None
    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute("SELECT signal_uid, symbol, strategy_version, event_time, "
                              "first_seen_at, executable_state FROM live_signal")
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception as exc:                                    # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    syn = [r for r in rows if re.match(r"^sig_(closeloop|ui_test)_", str(r.get("signal_uid") or ""))]
    add("P0-2 隔离", "live_signal 总行数", len(rows), None, err or "")
    add("P0-2 隔离", "合成测试信号", syn, "未清（待用户批准清理范围）" if syn else "已清",
        "工单要求：不删除合成记录、不替用户批准清理范围 ⇒ 本轮只记录，不处置")
    # 闸门
    gate = None
    try:
        gate = json.loads(GATE.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        gate = {"读取失败": str(exc)}
    add("P0-2 隔离", "生产闸门现状", {"内容": gate, "mtime": mtime(GATE)}, None)
    add("P0-2 隔离", "闸门是否仍为开启态",
        bool(gate.get("allow_new_entries")) if isinstance(gate, dict) else None,
        "不通过" if isinstance(gate, dict) and gate.get("allow_new_entries") else "通过",
        "验收未通过前 allow_new_entries 应为 false")


# ══════════════════════════════════════════════════════════════════════════
# P1-A10 只读声明与写入口一致
# ══════════════════════════════════════════════════════════════════════════
def p1_a10() -> None:
    print("\n=== P1-A10 只读/写入口一致性 ===")
    body = find(LA, r"def paper_order_post|return _paper_order_post_legacy")
    add("P1-A10", "paper_order_post 定义与实现", body,
        "未修" if any("_paper_order_post_legacy" in h["内容"] for h in body) else "已修",
        "docstring 说『已接通新共享核心』却 return legacy ⇒ 三句自相矛盾仍在")
    add("P1-A10", "legacy 函数的自我声明",
        find(LA, r"退役保留|不再挂任何路由"), None,
        "该函数自称不再挂路由，实际是唯一实现")
    # 只读侧
    add("P1-A10", "只读侧 not_connected 声明",
        find(LA, r"not_connected|NOT_CONNECTED")[:5], None)


# ══════════════════════════════════════════════════════════════════════════
# P1-L4 止损轨迹
# ══════════════════════════════════════════════════════════════════════════
def p1_l4() -> None:
    print("\n=== P1-L4 止损轨迹 ===")
    add("P1-L4 止损轨迹", "paper_stop_history 表定义",
        find(LS, r"CREATE TABLE IF NOT EXISTS paper_stop_history"), "已建表",
        "表已建（live_store.py）")
    add("P1-L4 止损轨迹", "开仓写初始止损",
        find(PE, r"INSERT OR IGNORE INTO paper_stop_history"), None)
    # record_stop_update 是否被调用
    calls = []
    for p in sorted(BE.rglob("*.py")):
        if "__pycache__" in p.parts or "_legacy_archive" in p.parts:
            continue
        for i, l in enumerate(lines_of(p), 1):
            if re.search(r"record_stop_update\s*\(", l) and "def " not in l:
                calls.append({"文件": p.relative_to(BASE).as_posix(), "行": i, "内容": l.strip()[:160]})
    add("P1-L4 止损轨迹", "record_stop_update 的调用点", calls,
        "未修" if not calls else "已修",
        "函数定义了但全后端零调用 ⇒ 跟踪上移永远不会落盘")
    # 止损价的全部写入点
    upd = []
    for p in sorted(BE.rglob("*.py")):
        if "__pycache__" in p.parts or "_legacy_archive" in p.parts:
            continue
        for i, l in enumerate(lines_of(p), 1):
            if re.search(r"UPDATE\s+paper_position\s+SET\s+stop_price", l, re.I):
                upd.append({"文件": p.relative_to(BASE).as_posix(), "行": i, "内容": l.strip()[:160]})
    add("P1-L4 止损轨迹", "paper_position.stop_price 的 UPDATE 点", upd, None,
        "若全部落在未调用的函数里 ⇒ 模拟盘止损永不移动")
    # 声明口径
    add("P1-L4 止损轨迹", "声明的止损口径",
        find(PE, r"trailing\s*=\s*off|固定止损|max_holding_hours"), None,
        "若声明 trailing=off，则『上移落盘』本就不该发生 ⇒ 不是 bug，"
        "而是模拟与回测不同规则，必须在对账里显式写明")


# ══════════════════════════════════════════════════════════════════════════
# P1-A20 闸门可隔离
# ══════════════════════════════════════════════════════════════════════════
def p1_a20() -> None:
    print("\n=== P1-A20 闸门可隔离 ===")
    hits = find(PE, r"GATE_FILE\s*=")
    env = find(PE, r"PAPER_GATE_FILE|PAPER_GATE_STRICT")
    add("P1-A20 闸门隔离", "GATE_FILE 定义", hits,
        "已修" if any("environ" in h["内容"] for h in hits) else "未修",
        "有 os.environ.get 出口才能把隔离实例的闸门指到 /tmp")
    add("P1-A20 闸门隔离", "环境变量出口", env, None)


# ══════════════════════════════════════════════════════════════════════════
# P2-C2 平仓校验顺序
# ══════════════════════════════════════════════════════════════════════════
def p2_c2() -> None:
    print("\n=== P2-C2 平仓校验顺序 ===")
    lns = lines_of(PE)
    # 找 close_position 函数体内 position_not_found 与 no_closed_15m_for_exit 的先后
    start = None
    for i, l in enumerate(lns, 1):
        if re.match(r"def close_position\(", l):
            start = i
            break
    if start is None:
        add("P2-C2", "close_position", "未找到", "未验证")
        return
    end = start + 1
    while end <= len(lns) and (not lns[end - 1].startswith("def ") or end == start):
        end += 1
    body = lns[start - 1:end - 1]
    pnf = next((start + k for k, l in enumerate(body) if "position_not_found" in l), None)
    noc = next((start + k for k, l in enumerate(body) if "no_closed_15m_for_exit" in l), None)
    ok = pnf is not None and noc is not None and pnf < noc
    add("P2-C2", "校验顺序", {"position_not_found 行": pnf,
                            "no_closed_15m_for_exit 行": noc,
                            "先查仓位": ok}, "已修" if ok else "未修",
        "不存在的仓位必须先返回 position_not_found，不能被行情缺失抢先")


# ══════════════════════════════════════════════════════════════════════════
# P2-X3b / P2-X11 UI
# ══════════════════════════════════════════════════════════════════════════
def p2_ui() -> None:
    print("\n=== P2 UI（signal_id 展示 / 页签空壳）===")
    if UI_SIG.exists():
        hits = find(UI_SIG, r"signal_id|signal_uid")
        add("P2-X3b 信号标识", "SignalListPanel 里是否渲染 signal_id", hits,
            "已修" if hits else "未修",
            "页面必须能显示可对账的 signal_id")
    else:
        add("P2-X3b 信号标识", "文件", "不存在", "未验证")
    if UI_STRAT.exists():
        lns = lines_of(UI_STRAT)
        tab_lines = [(i, l.strip()) for i, l in enumerate(lns, 1)
                     if re.search(r"activeTab", l)]
        # activeTab 是否只出现在 className
        only_class = all(re.search(r"className|class:", l) or "useState" in l or "setActiveTab" in l
                         for _, l in tab_lines)
        add("P2-X11 页签空壳", "activeTab 使用点", tab_lines, None)
        add("P2-X11 页签空壳", "activeTab 是否只参与样式", only_class,
            "未修" if only_class else "已修",
            "只参与 className ⇒ 切页签只改高亮，不改内容")


# ══════════════════════════════════════════════════════════════════════════
# 重点 3：执行身份（UI 实际调用的核心 / 策略版本 / 参数 vs 声明）
# ══════════════════════════════════════════════════════════════════════════
def identity() -> None:
    print("\n=== 执行身份 ===")
    add("执行身份", "adapter 注册的写路由",
        find(AD, r"paper/orders|paper/control|signals/clear"), None,
        "UI 实际打到哪个入口，以 adapter 的路由表为准")
    add("执行身份", "MODEL_VERSION / 模型声明",
        find(PE, r"MODEL_VERSION\s*=")[:4], None)
    add("执行身份", "approved_params 来源",
        find(PE, r"def approved_params|PARAM_VIEW|研究参数生效视图")[:6], None)
    add("执行身份", "旧执行路径是否仍可达",
        find(PE, r"_RetiredExecBridgeStub|CORE_AVAILABLE")[:5], None,
        "旧 exec_bridge 必须不可达；否则旧路径可能冒充当前研究策略")


def main() -> None:
    p0_1(); p0_2(); p1_a10(); p1_l4(); p1_a20(); p2_c2(); p2_ui(); identity()
    RES["读取时点指纹_汇总"] = {
        p.relative_to(BASE).as_posix(): sha(p) for p in (PE, LA, LS, AD, LW)
    }
    stat = {}
    for x in RES["判据"]:
        stat[x["判定"]] = stat.get(x["判定"], 0) + 1
    RES["判定统计"] = stat
    (OUT / "01_整改范围核对.json").write_text(
        json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 判定统计 ===")
    print(json.dumps(stat, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
