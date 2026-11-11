# -*- coding: utf-8 -*-
"""GM-BACKTEST-DATA-GATE-17｜回测**启动前**的数据质量门禁（隔离补丁，纯只读）。

为什么需要它（本单要解决的真问题）
──────────────────────────────────
`backtest_runner.load_inputs` 目前只做三件事：**已收盘 + 时间戳等间隔 + 全窗覆盖**
（`_closed_contiguous`，`backtest_runner.py:225-255`），命中即 `RuntimeError` 崩。
DATA-09 独立审计（`evidence/GM-DATA-QUALITY-AUDIT-09/`）已确认的缺陷里，有三类
**根本不在那道校验的射程内**：

  · **异常 OHLC**（全库 1,618 根 `high<low` / `open|close` 越界 / 价格 ≤ 0）——
    等间隔校验只看时间戳，一根 `high<low` 的坏 bar 会**静默进入撮合**，
    止损/盈亏全部按坏值算，跑完还是「ok」。
  · **时间错位**（12h 有 386 根未整点对齐，OKX 12h 混入 4h 时间戳）——
    「相邻差 == 间隔」在整段平移时**恒成立**，查不出来。
  · **OI 历史可得性 / 资金费缺口**——现有代码只把它们记进 `coverage` 元数据，
    不做启动前判定，跑完才在 `data_quality.status` 里降级，等于**事后追认**。

本模块把这些判据前移到**启动前**，并把结论压成三态（PASS / FAIL / UNPROVEN），
再据此给出**两个出口**：
  · `trusted`（正式可信回测）：只有 PASS 才放行；
  · `isolated_research`（隔离研究运行）：**严重异常（severe）也拒绝运行**，其余状态放行，
    但**强制打标记**，且 `claim_level` 恒为 `ISOLATED_RESEARCH`，
    不得被后续任何环节升格成「通过」。
    「严重」= `high<low` / 任一 OHLC 价格 ≤ 0 / 任一 OHLC 缺失或非有限（31 号新增分级）；
    `open|close` 越界、`volume<0` 属普通异常，隔离研究仍可跑（verdict 仍为 FAIL）。

纪律（写进代码，不靠记性）
──────────────────────────
1. **零伪造**：本模块不构造任何价格/OI/资金费数值。OI 缺就是缺（NaN 不落 0），
   资金费缺格就是缺格（不补 0、不假设 8h）。模块内无 `fillna/interpolate/ffill/bfill`。
2. **零写入**：不打开任何数据库、不写任何文件（CLI 的 `--out` 是**唯一**例外，
   且只在显式传入时发生）。行情库只以「调用方传进来的 repo / 已加载快照」形式读取。
3. **三态不塌陷**：UNPROVEN 绝不计入 PASS；`verdict` 与 `claim_level` 分离，
   `trusted_backtest_allowed` 只认 `verdict == PASS`。
4. **判定可复算**：同一快照 → `verdict_sha256` 恒等（时间戳不参与哈希）。

与既有机制的关系（**复用，不另立一套**）
────────────────────────────────────────
· 连续性/全窗覆盖：判据与 `backtest_runner._closed_contiguous` 一致（首根 == 起点、
  末根 + iv == 终点、根数 == 期望、相邻差 == iv、全部 is_closed=1），此处只是把
  它变成**返回判定**而不是抛异常。
· 资金费频率/结算网格：判据与 `backtest_runner._funding_frequency` /
  `_expected_settlement_grid` 一致（观测众数间隔、锚点、半开区间含左端点、60s 容差），
  不假设 8h。
· 取数：`collect_snapshot(repo, win)` 只调用 `MarketDataRepository.candles/oi/funding`，
  分页口径与 `_fetch_candles_all` 一致（逆序翻页、超上限拒绝静默截断）。

接线点（**已接线，45 号更新**）
──────────────────────────────
17 号当时**没有**改 `backtest_runner.py`，所以那会儿「拦截已生效」不成立。之后：
  · 21 号（`backtest_gate_wiring.py`）把门禁接进 `_run` 与 `_run_event_trend`，
    调用点在引擎启动之前；
  · 45 号把 4H 辅助序列（ATR / 箱体 / 跟踪止损的真实输入）接进快照契约
    （`snapshot_from_frames(df_aux=...)` → `aux_*` 键 → `_check_aux`），
    并让 `run.json` 通过 `stamp()` 保留 `data_gate.aux` 覆盖声明。
仍未做的：**本模块之外**的入口（研究脚本直连引擎、结果登记工具、生产 API）
不在本模块能力范围内，也不在 45 号授权文件内 ⇒ 见
`evidence/GM-BACKTEST-4H-GATE-ENTRYPOINT-FIX-45/out/04_entry_reachability.json` 的
BLOCKED 清单。「门禁已接线」只对 `backtest_runner` 的两条执行路径成立。
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GATE_ID = "GM-BACKTEST-DATA-GATE-17"
GATE_VERSION = "v3"   # v2→v3：31号严重OHLC阻断+37号PIT修复+33号接线审计字段透传（40号）
# 45 号**不升** GATE_VERSION，而是新增一条正交的 `AUX_POLICY_VERSION`（见下方 `a1`）。
# 为什么（这是一个需要写清楚的决定，不是遗漏）：
#   · 43 号实测 4H 从不进快照，而 4H 的 high/low 决定 ATR、箱体宽度与跟踪止损 ——
#     单根污染即可让「本该形成的箱体」永不形成（4.60 ATR → 0.00 / 17.84 / 0.40），
#     跟踪止损候选放大 13,182 倍，门禁却仍给 PASS。判据集合确实变了。
#   · 但项目已有先例：31 号新增严重度分级时也没有动 `GATE_VERSION`，只加了 `s1` 轴。
#   · 且 40 号在 `test_gate_v3_finalize_40.py` 里把 `GATE_VERSION == "v3"` 钉死在 4 处；
#     升 v4 会让 40 号回归必然失败，而 40 号的审计断言不在本单授权内、也不该被
#     后来者为了「让自己变绿」而改写。
# 代价：只读 `gate_version` 的下游分不清「v3」与「v3+aux 判据」。补偿：判定里
# `core["aux"]["policy_version"] == "a1"` 与 `run.json` 的 `data_gate.aux.policy_version`
# 两处显式暴露，读得到 `a1` 才说明 4H 判据真的跑过。

# 三态：只有 PASS 能进正式可信回测；UNPROVEN 与 FAIL 都不得计为通过
PASS = "PASS"
FAIL = "FAIL"
UNPROVEN = "UNPROVEN"
STATUS_ORDER = {PASS: 0, UNPROVEN: 1, FAIL: 2}

MODE_TRUSTED = "trusted"
MODE_ISOLATED = "isolated_research"
MODES = (MODE_TRUSTED, MODE_ISOLATED)

DAY_MS = 86_400_000
INTERVAL_MS = {
    "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": DAY_MS,
}

DEFAULT_POLICY: Dict[str, Any] = {
    # OI：窗口内「有 OI 的信号 bar 占比」低于此值 ⇒ 可得性不足 ⇒ UNPROVEN
    "oi_min_coverage_ratio": 0.95,
    # OI：是否要求每条被用到的 OI 都带可证明的可得时刻（available_at）
    #     项目现实是冷档 available_at 填充率 0.0% ⇒ 默认要求 ⇒ 历史窗口恒 UNPROVEN，
    #     这与 GM-HISTORICAL-PIT-AUDIT-05 的结论一致，不是缺陷。
    "oi_pit_required": True,
    # 资金费：网格匹配容差（与 backtest_runner.FUNDING_GRID_TOL_MS 同值）
    "funding_grid_tol_ms": 60_000,
    # 资金费：频率观测回看窗（与 backtest_runner.FUNDING_LOOKBACK_MS 同值）
    "funding_lookback_ms": 7 * DAY_MS,
    # OI：陈旧上限（None = 自动取 max(2×观测步长, 信号周期)）
    "oi_max_staleness_ms": None,
    # OHLC 相对容差（价格量级 1e-9 相对误差视为浮点噪声，不算越界）
    "ohlc_rel_tol": 1e-9,
    # 证据里每类异常最多保留几个样本
    "max_samples": 5,
    # ── 辅助序列（4H）通道（45 号）──
    # 是否**强制**要求快照携带辅助序列。由 `snapshot_from_frames` 置 True；
    # 17/27/31/40 号的手工快照不设此键 ⇒ 默认 False ⇒ 老判据逐位不变（零回归）。
    "aux_required": False,
    # 窗口起点之前至少需要多少根辅助 bar（4H 箱体回看 42 根）
    "aux_min_bars_before_window": 42,
    # ATR 有效性只检查最后这么多根（只有它们会被 `sub_4h["atr"].iloc[-1]` 取用）
    "aux_atr_tail_bars": 42,
}

ISOLATION_LABEL = "隔离研究运行：结论不得作为正式可信回测引用，也不得升格为「通过」"

# ── OHLC 严重度（31 号）────────────────────────────────────────────────────────
# 为什么需要分级：isolated 出口原先对所有 FAIL 一视同仁地放行，于是「价格 <= 0」「high < low」
# 这类**物理上不可能**的序列也能跑完并产出指标。分级只回答一个问题：
# 这条异常是「价格本身不存在」（severe）还是「价格存在但数值不合逻辑」（normal）。
# 分级不新增任何检测 —— 17 号 `_check_ohlc` 已能识别全部这些行，本单只给它们打标。
SEVERITY_SEVERE = "severe"
SEVERITY_NORMAL = "normal"
SEVERITY_NONE = "none"

SEVERE_OHLC_CRITERIA = (
    "high < low（区间自相矛盾）",
    "任一 OHLC 价格 <= 0（非正价格）",
    "任一 OHLC 缺失或非有限（None / NaN / ±Inf / 不可转数值）",
)
NORMAL_OHLC_CRITERIA = (
    "open / close 越出 [low, high]（超出 ohlc_rel_tol 相对容差）",
    "volume < 0",
)
SEVERITY_POLICY_VERSION = "s1"   # 严重度契约版本（与 GATE_VERSION 正交：v2 判据未动）

# ── 辅助序列（4H）契约版本（45 号）─────────────────────────────────────────────
# 与 `SEVERITY_POLICY_VERSION` 同一个模式：**新增判据族不升 `GATE_VERSION`**，
# 而是给它一条正交的子版本轴。两条理由：
#   1. 先例：31 号新增严重度分级时也没有动 `GATE_VERSION`，只加了 `s1`；
#   2. 硬约束：40 号在 `test_gate_v3_finalize_40.py` 里把 `GATE_VERSION == "v3"`
#      钉死在 4 处。升 v4 会让 40 号回归必然失败，而 40 号的审计断言不在本单授权内、
#      也不应被后来者为了「让自己变绿」而改写。
# 代价与补偿：只读 `gate_version` 的下游无法区分「v3」与「v3+aux 判据」。因此本单
# 在**两个**地方显式暴露该轴 —— `core["aux"]["policy_version"]` 与 `run.json` 的
# `data_gate.aux.policy_version`。读得到 `a1` 才说明 4H 判据真的跑过。
AUX_POLICY_VERSION = "a1"


# ══════════════════════════ 小工具 ══════════════════════════

def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return None


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _get(row: Any, names: Sequence[str], default=None):
    """按候选列名取值（兼容热库/冷档/DataFrame 行三种命名）。"""
    for n in names:
        try:
            if isinstance(row, dict) and n in row:
                v = row[n]
            else:
                v = getattr(row, n, None)
        except Exception:                                    # noqa: BLE001
            v = None
        if v is not None:
            return v
    return default


def _num(v) -> Optional[float]:
    """转 float；NaN/inf/不可转 ⇒ None（**不当 0**）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _int(v) -> Optional[int]:
    f = _num(v)
    return None if f is None else int(f)


def _chk(cid: str, status: str, detail: str,
         severity: Optional[str] = None, **evidence) -> dict:
    """一条判据。`severity`（31 号）只由 OHLC 判据给出，放在**顶层**：
    出口与验收方读 `c["severity"]` 即可，不必下钻 `evidence`。"""
    out = {"id": cid, "status": status, "detail": detail, "evidence": evidence}
    if severity is not None:
        out["severity"] = severity
    return out


def _window_filter(bars: List[dict], lo: int, hi: int) -> Tuple[List[dict], int]:
    """按半开区间 `[lo, hi)` 截取（只截范围、不动数值）；返回 (窗口内, 窗口外条数)。"""
    keep = [b for b in bars if lo <= int(b["open_time"]) < hi]
    return keep, len(bars) - len(keep)


def _bar_norm(raw) -> dict:
    """把一行 K 线规范成内部结构（不做任何修补）。"""
    return {
        "open_time": _int(_get(raw, ("open_time", "timestamp", "ts", "open_ms"))),
        "close_time": _int(_get(raw, ("close_time", "close_ms"))),
        "open": _num(_get(raw, ("open",))),
        "high": _num(_get(raw, ("high",))),
        "low": _num(_get(raw, ("low",))),
        "close": _num(_get(raw, ("close",))),
        "volume": _num(_get(raw, ("volume", "vol"))),
        "is_closed": _int(_get(raw, ("is_closed", "closed"))),
    }


# ══════════════════════════ 判据 1：K 线缺失 / 未收盘 / 重复 ══════════════════════════

def _check_bars(bars: List[dict], tf: str, label: str,
                expect_start_ms: Optional[int], expect_end_ms: Optional[int],
                policy: dict) -> List[dict]:
    """已收盘 + 无重复 + 等间隔 + 全窗覆盖。判据与 `_closed_contiguous` 一致。"""
    out: List[dict] = []
    iv = INTERVAL_MS[tf]
    n = len(bars)

    if n == 0:
        out.append(_chk("KL_EMPTY", FAIL,
                        f"{label}：窗口内 0 根 K 线（空窗不可回测）",
                        timeframe=tf, rows=0))
        return out

    ts = [b["open_time"] for b in bars]
    bad_ts = [i for i, t in enumerate(ts) if t is None]
    if bad_ts:
        out.append(_chk("KL_TIMESTAMP", FAIL,
                        f"{label}：{len(bad_ts)} 根缺少可用 open_time",
                        timeframe=tf, sample_idx=bad_ts[:policy["max_samples"]]))
        return out

    unclosed = [t for t, b in zip(ts, bars) if b["is_closed"] != 1]
    if len(unclosed) == n and all(b["is_closed"] is None for b in bars):
        out.append(_chk("KL_UNCLOSED", UNPROVEN,
                        f"{label}：未提供 is_closed 字段，收盘状态无法证明",
                        timeframe=tf, rows=n))
    elif unclosed:
        out.append(_chk("KL_UNCLOSED", FAIL,
                        f"{label}：{len(unclosed)} 根未收盘 bar 混入",
                        timeframe=tf, sample_utc=[_iso(t) for t in unclosed[:policy["max_samples"]]]))

    dup = [t for t, c in Counter(ts).items() if c > 1]
    if dup:
        out.append(_chk("KL_DUPLICATE", FAIL,
                        f"{label}：{len(dup)} 个 open_time 重复",
                        timeframe=tf, sample_utc=[_iso(t) for t in sorted(dup)[:policy["max_samples"]]]))

    # 相邻差（先去重，否则重复会让差值恒为 0 掩盖真实缺口）
    uniq = sorted(set(ts))
    gaps = [(uniq[i], uniq[i + 1]) for i in range(len(uniq) - 1) if uniq[i + 1] - uniq[i] != iv]
    if gaps:
        missing = sum(max(0, (b - a) // iv - 1) for a, b in gaps)
        out.append(_chk("KL_GAP", FAIL,
                        f"{label}：{len(gaps)} 处时间缺口（累计缺 {missing} 根）",
                        timeframe=tf, interval_ms=iv, gaps=len(gaps), missing_bars=int(missing),
                        sample=[{"after_utc": _iso(a), "before_utc": _iso(b),
                                 "missing": int((b - a) // iv - 1)}
                                for a, b in gaps[:policy["max_samples"]]]))

    if expect_start_ms is not None and expect_end_ms is not None:
        exp = int((int(expect_end_ms) - int(expect_start_ms)) // iv)
        head_ok = uniq[0] == int(expect_start_ms)
        tail_ok = (uniq[-1] + iv) == int(expect_end_ms)
        count_ok = len(uniq) == exp
        if not (head_ok and tail_ok and count_ok):
            out.append(_chk("KL_COVERAGE", FAIL,
                            f"{label}：未全窗覆盖（期望 {exp} 根，实际 {len(uniq)} 根；"
                            f"首根{'齐' if head_ok else '不齐'}、尾根{'齐' if tail_ok else '不齐'}）",
                            timeframe=tf,
                            expect_start_utc=_iso(expect_start_ms),
                            expect_end_utc=_iso(expect_end_ms),
                            expected_bars=int(exp), actual_bars=int(len(uniq)),
                            first_utc=_iso(uniq[0]), last_utc=_iso(uniq[-1]),
                            head_ok=bool(head_ok), tail_ok=bool(tail_ok),
                            count_ok=bool(count_ok)))
    if not out:
        # 通过也要留一条可审计记录：否则 PASS 看起来像「没查」。
        out.append(_chk("KL_OK", PASS,
                        f"{label}：{len(uniq)} 根，等间隔、全部已收盘、无重复、全窗覆盖",
                        timeframe=tf, rows=int(len(uniq)),
                        expected_bars=(None if expect_start_ms is None else
                                       int((int(expect_end_ms) - int(expect_start_ms)) // iv)),
                        first_utc=_iso(uniq[0]), last_utc=_iso(uniq[-1]),
                        interval_ms=iv))
    return out


# ══════════════════════════ 判据 2：时间错位 ══════════════════════════

def _check_alignment(bars: List[dict], tf: str, label: str, policy: dict) -> List[dict]:
    """open_time 必须落在本周期网格上，close_time 必须是 open_time + iv（允许 -1ms 右闭）。"""
    iv = INTERVAL_MS[tf]
    off_grid = [b["open_time"] for b in bars
                if b["open_time"] is not None and int(b["open_time"]) % iv != 0]
    bad_close = [(b["open_time"], b["close_time"]) for b in bars
                 if b["close_time"] is not None
                 and int(b["close_time"]) not in (int(b["open_time"]) + iv,
                                                  int(b["open_time"]) + iv - 1)]
    out = []
    if off_grid:
        out.append(_chk("KL_ALIGN", FAIL,
                        f"{label}：{len(off_grid)} 根 open_time 未落在 {tf} 网格（时间错位）",
                        timeframe=tf, interval_ms=iv,
                        sample_utc=[_iso(t) for t in off_grid[:policy["max_samples"]]],
                        sample_mod=[int(t) % iv for t in off_grid[:policy["max_samples"]]]))
    if bad_close:
        out.append(_chk("KL_CLOSE_TIME", FAIL,
                        f"{label}：{len(bad_close)} 根 close_time 与 open_time+{tf} 不符",
                        timeframe=tf,
                        sample=[{"open_utc": _iso(o), "close_utc": _iso(c),
                                 "expect_ms": int(o) + iv} for o, c in bad_close[:policy["max_samples"]]]))
    if not out:
        out.append(_chk("KL_ALIGN_OK", PASS,
                        f"{label}：{len(bars)} 根 open_time 全部落在 {tf} 网格，"
                        "close_time 与 open_time+周期一致",
                        timeframe=tf, rows=len(bars), interval_ms=iv))
    return out


def _check_cross_tf(sig_tf: str, exec_tf: str, win_start_ms: Optional[int],
                    win_end_ms: Optional[int], warmup_bars: int) -> List[dict]:
    """**一根信号 bar 必须装下整数根执行 bar**（`sig_iv % exec_iv == 0`）；
    窗口两端必须同时落在两个网格上。

    方向为什么是这个（27 号修正，17 号原实现写反）
    ────────────────────────────────────────────
    信号周期是**决策粒度**，执行周期是**撮合粒度**：一次信号 bar 收盘给出决策，该决策要在
    这根信号 bar 覆盖的时间区间内、按执行周期的 bar 逐根撮合。因此判据是
    「执行周期整除信号周期」⇒ `s_iv % e_iv == 0`，比值 = 一根信号 bar 含几根执行 bar。

    17 号原实现写成 `e_iv % s_iv == 0`（执行周期必须是信号周期的整数倍），方向相反，
    后果是双向错误：
      · 本项目真实基线「事件 1h / 执行 15m」→ `1h % 15m = 0` 应 PASS，却被判 FAIL；
      · 「信号 15m / 执行 4h」→ 一根 15m bar 装不下 4h bar，根本无处撮合，却被判 PASS。
    21 号报告（`evidence/GM-BACKTEST-GATE-WIRING-21/`）已独立论证该方向与项目基线相反。
    """
    out: List[dict] = []
    s_iv, e_iv = INTERVAL_MS[sig_tf], INTERVAL_MS[exec_tf]
    if s_iv % e_iv != 0:
        out.append(_chk("KL_XTF_RATIO", FAIL,
                        f"信号周期 {sig_tf}（{s_iv}ms）装不下整数根执行周期 {exec_tf}"
                        f"（{e_iv}ms）：一根信号 bar 内的撮合时刻无法与执行网格对齐",
                        signal_iv=s_iv, exec_iv=e_iv,
                        sig_mod_exec=int(s_iv % e_iv),
                        exec_bars_per_signal_bar=round(s_iv / e_iv, 6)))
        return out
    if win_start_ms is None or win_end_ms is None:
        out.append(_chk("KL_XTF_WINDOW", UNPROVEN,
                        "未提供窗口起止，无法验证窗口是否落在两套网格上",
                        signal_iv=s_iv, exec_iv=e_iv))
        return out
    ws, we = int(win_start_ms), int(win_end_ms)
    bad = []
    if ws % s_iv or ws % e_iv:
        bad.append(("win_start", ws))
    if we % s_iv or we % e_iv:
        bad.append(("win_end", we))
    if (we - ws) % s_iv or (we - ws) % e_iv:
        bad.append(("win_len", we - ws))
    if bad:
        out.append(_chk("KL_XTF_WINDOW", FAIL,
                        "窗口端点未落在周期网格上（错位窗口会切出半根 bar）",
                        signal_iv=s_iv, exec_iv=e_iv,
                        bad=[{"field": f, "ms": m, "utc": _iso(m),
                              "mod_sig": m % s_iv, "mod_exec": m % e_iv} for f, m in bad]))
    if warmup_bars < 0:
        out.append(_chk("KL_XTF_WARMUP", FAIL, f"预热根数为负（{warmup_bars}）",
                        warmup_bars=warmup_bars))
    if not out:
        n_exec = int(s_iv // e_iv)
        out.append(_chk("KL_XTF_OK", PASS,
                        f"1 根 {sig_tf} 信号 bar 装下 {n_exec} 根 {exec_tf} 执行 bar，"
                        "窗口端点同时落在两套网格上",
                        signal_iv=s_iv, exec_iv=e_iv,
                        # `ratio` 保留 17 号输出键名以免下游证据对不上，语义已明确为
                        # 「每根信号 bar 含几根执行 bar」（27 号前该值恒为 e_iv//s_iv）。
                        ratio=n_exec, exec_bars_per_signal_bar=n_exec,
                        win_start_utc=_iso(ws), win_end_utc=_iso(we),
                        window_bars_signal=int((we - ws) // s_iv),
                        window_bars_exec=int((we - ws) // e_iv)))
    return out


# ══════════════════════════ 判据 3：异常 OHLC ══════════════════════════

def _check_ohlc(bars: List[dict], label: str, policy: dict) -> List[dict]:
    """数值合法性 + 逻辑自洽：high>=low、low>0、open/close 落在 [low, high] 内、volume>=0。

    `severity`（31 号新增，逐行与整条判据都带）：
      severe —— high<low / 任一 OHLC 价格<=0 / 任一 OHLC 缺失或非有限 ⇒ 隔离模式也必须阻断
      normal —— open/close 越界（超容差）/ volume<0 ⇒ 隔离研究可放行，但 verdict 仍为 FAIL
    判定「哪些行算异常」的逻辑与 17 号逐字一致，本单只加分级，不新增检测、不改状态。
    """
    rt = float(policy["ohlc_rel_tol"])
    bad: List[dict] = []
    nonfinite = 0
    severe_rows = 0
    normal_rows = 0
    severe_why: Counter = Counter()
    normal_why: Counter = Counter()
    for b in bars:
        # 再走一遍 `_num`：NaN/±Inf 归一成 None（与 `_bar_norm` 同口径），
        # 这样即便调用方直接传原始 dict（不经 evaluate），严重度判定也成立。
        o, h, l, c = _num(b["open"]), _num(b["high"]), _num(b["low"]), _num(b["close"])
        v = b["volume"]
        if None in (o, h, l, c):
            nonfinite += 1
            severe_rows += 1
            severe_why["OHLC 缺失或非有限"] += 1
            bad.append({"open_utc": _iso(b["open_time"]), "why": "OHLC 含空值/非有限值",
                        "severity": SEVERITY_SEVERE,
                        "open": o, "high": h, "low": l, "close": c})
            continue
        sev: List[str] = []
        nor: List[str] = []
        if l <= 0 or h <= 0 or o <= 0 or c <= 0:
            sev.append("价格 <= 0")
        if h < l:
            sev.append("high < low")
        eps = rt * max(1.0, abs(h), abs(l))
        if o < l - eps or o > h + eps:
            nor.append("open 越出 [low, high]")
        if c < l - eps or c > h + eps:
            nor.append("close 越出 [low, high]")
        if v is not None and v < 0:
            nor.append("volume < 0")
        if sev or nor:
            # 同一行既有严重项又有普通项时按严重记（不允许「有普通项就降级」）。
            row_sev = SEVERITY_SEVERE if sev else SEVERITY_NORMAL
            if sev:
                severe_rows += 1
                for w in sev:
                    severe_why[w] += 1
            else:
                normal_rows += 1
                for w in nor:
                    normal_why[w] += 1
            bad.append({"open_utc": _iso(b["open_time"]), "why": " / ".join(sev + nor),
                        "severity": row_sev,
                        "open": o, "high": h, "low": l, "close": c, "volume": v})
    if not bad:
        return [_chk("OHLC_OK", PASS,
                     f"{label}：{len(bars)} 根 OHLC 数值合法且自洽（high>=low、价格>0、"
                     "open/close 在区间内、volume>=0）",
                     rows_checked=len(bars), rel_tol=rt,
                     severity=SEVERITY_NONE, severe_rows=0, normal_rows=0)]
    return [_chk("OHLC_INVALID", FAIL,
                 f"{label}：{len(bad)} 根异常 OHLC（含 {nonfinite} 根空值/非有限值；"
                 f"严重 {severe_rows} 根、普通 {normal_rows} 根）",
                 rows_checked=len(bars), bad_rows=len(bad), nonfinite=nonfinite,
                 severity=(SEVERITY_SEVERE if severe_rows else SEVERITY_NORMAL),
                 severe_rows=severe_rows, normal_rows=normal_rows,
                 severe_reasons=dict(severe_why), normal_reasons=dict(normal_why),
                 severe_criteria=list(SEVERE_OHLC_CRITERIA),
                 sample=bad[:policy["max_samples"]])]


# ══════════════════════════ 判据 4：OI 可得性（只出 UNPROVEN，绝不补数）══════════════════════════

def _check_oi(oi_rows: Sequence, sig_bars: List[dict], sig_tf: str,
              win_start_ms: Optional[int], win_end_ms: Optional[int],
              policy: dict) -> List[dict]:
    """按 as-of（bar 收盘前最近一条）统计可得性，并对持仓量数据执行严格 PIT 因果契约核验。

    因果契约（GM-WYCKOFF-V3.1-OI-PIT-P0-FIX-50）：
        对于每个持仓量数据桶，必须严格满足：
            bucket_end <= available_at <= decision_time
        1. 明确时间戳语义：默认 ts 为桶开始时间（bucket_start），通过数据周期众数计算 bucket_end = ts + step；
           若行数据显式带 close_time/bucket_end 或 policy 配置 bucket_end，则尊重该显式边界。
        2. 闭合下限拦截：若 available_at < bucket_end，说明桶未闭合前视获取，判定为因果违规。
        3. 未来到达拦截：若 available_at > max_eval_time，说明为未来荒谬数据（如 2099 年），判定为未来违规。
        4. 决策时刻拦截：在各信号 bar 收盘决策时刻 cl，若使用的 OI 数据在该时刻尚未到达（available_at > cl），不可用。
        5. 污染标签拦截：命中 is_backfill / gapfill / taint 等回填标记一律维持 UNPROVEN。
    """
    iv = INTERVAL_MS[sig_tf]
    if not oi_rows:
        return [_chk("OI_EMPTY", UNPROVEN,
                     "窗口内 0 条可用持仓量观测 ⇒ 持仓量因子不可证（不伪造、不填 0）",
                     rows_in=0)]

    raw_pts: List[dict] = []
    pit_field = None
    for r in oi_rows:
        ts = _int(_get(r, ("ts", "timestamp", "open_time", "time_ms")))
        val = _num(_get(r, ("open_interest", "oi", "open_interest_value", "oi_usd")))
        if ts is None or val is None:
            continue

        found_cand = None
        avail_val = None
        for cand in ("available_at", "available_ms", "ts_available", "fetched_at"):
            raw_avail = _get(r, (cand,))
            if raw_avail is not None:
                found_cand = cand
                avail_val = _int(raw_avail)
                break
        if found_cand and not pit_field:
            pit_field = found_cand

        # 检查是否含回填或推断污染
        r_str = str(r).lower()
        is_tainted = False
        if (found_cand and ("backfill" in found_cand.lower() or "taint" in found_cand.lower())) or \
           ("backfill" in r_str or "taint" in r_str or "gapfill" in r_str) or \
           (_get(r, ("is_backfill", "backfilled", "tainted", "is_inferred")) is True):
            is_tainted = True

        explicit_end = _int(_get(r, ("bucket_end", "close_time", "end_time", "end_ms")))

        raw_pts.append({
            "ts": ts,
            "val": val,
            "avail": avail_val,
            "field": found_cand,
            "tainted": is_tainted,
            "explicit_end": explicit_end,
            "row": r
        })

    raw_pts.sort(key=lambda x: x["ts"])
    if not raw_pts:
        return [_chk("OI_EMPTY", UNPROVEN,
                     "窗口内 0 条有效数值的持仓量观测 ⇒ 持仓量因子不可证",
                     rows_in=len(oi_rows))]

    closes = [int(b["open_time"]) + iv for b in sig_bars if b["open_time"] is not None]
    if not closes:
        return [_chk("OI_NO_BARS", UNPROVEN,
                     "信号序列为空，持仓量可得性无从判定（空窗已在 KL_EMPTY 判失败）",
                     rows_in=len(oi_rows))]

    out: List[dict] = []
    ts_arr = [p["ts"] for p in raw_pts]
    diffs = [b - a for a, b in zip(ts_arr, ts_arr[1:]) if b > a]
    oi_step = Counter(diffs).most_common(1)[0][0] if diffs else iv
    end_tol = max(int(oi_step), iv)

    # 确定时间戳语义及各点 bucket_end
    ts_semantic = policy.get("oi_timestamp_semantic", "bucket_start")
    for p in raw_pts:
        if p["explicit_end"] is not None:
            p["bucket_end"] = p["explicit_end"]
        elif ts_semantic == "bucket_end":
            p["bucket_end"] = p["ts"]
        else: # 默认 bucket_start
            p["bucket_end"] = p["ts"] + int(oi_step)

    # 全局到达时间上限（防止 2099 年未来时间逃逸）
    max_eval_time = max([closes[-1]] + ([int(win_end_ms)] if win_end_ms is not None else [])) + end_tol

    # 统计单点因果合规性
    with_pit = 0
    pit_tainted = 0
    pit_unclosed_invalids = 0
    pit_future_invalids = 0
    for p in raw_pts:
        if p["tainted"]:
            pit_tainted += 1
            continue
        avail = p["avail"]
        if avail is None:
            continue
        # 1. 验证下限：bucket_end <= available_at
        if avail < p["bucket_end"]:
            pit_unclosed_invalids += 1
            continue
        # 2. 验证上限：available_at <= max_eval_time
        if avail > max_eval_time:
            pit_future_invalids += 1
            continue
        with_pit += 1

    # 陈旧上限与逐 Bar 决策时刻 As-of 匹配
    stale_tol = policy.get("oi_max_staleness_ms")
    stale_tol = int(stale_tol) if stale_tol else max(2 * int(oi_step), iv)
    have = 0
    pit_post_decision_invalids = 0

    # 按照 38 号因果契约：以已闭合桶 bucket_end 进行决策时刻匹配（bucket_end <= cl）
    bucket_end_arr = [p["bucket_end"] for p in raw_pts]
    for cl in closes:
        pos = bisect.bisect_right(bucket_end_arr, cl) - 1
        if pos < 0:
            continue
        # 寻找在决策时刻 cl 已经到达且未陈旧的最新已闭合观测
        matched = False
        curr_i = pos
        while curr_i >= 0:
            p = raw_pts[curr_i]
            if (cl - p["bucket_end"]) > stale_tol:
                # 已经超出陈旧容差，不可用
                break
            # 决策因果检验：如果要求 PIT 证明且该点带到达时间，必须在 cl 之前已到达
            if bool(policy.get("oi_pit_required", False)) and p["avail"] is not None:
                if p["avail"] <= cl:
                    matched = True
                    break
                # 若最新一根刚闭合的桶网络稍有延迟 (avail > cl)，继续回退寻找在 cl 之前已到达的上一根已闭合桶
                curr_i -= 1
            else:
                matched = True
                break

        if matched:
            have += 1
        elif bool(policy.get("oi_pit_required", False)):
            pit_post_decision_invalids += 1

    ratio = (have / len(closes)) if closes else 0.0
    first_ms, last_ms = ts_arr[0], ts_arr[-1]

    if ratio < float(policy["oi_min_coverage_ratio"]):
        out.append(_chk("OI_COVERAGE", UNPROVEN,
                        f"持仓量可得性不足：{have}/{len(closes)} 根信号 bar 有有效 OI"
                        f"（{ratio:.2%} < 阈值 {float(policy['oi_min_coverage_ratio']):.2%}）",
                        bars=int(len(closes)), bars_with_oi=int(have),
                        bars_missing_oi=int(len(closes) - have),
                        coverage_ratio=round(ratio, 6),
                        min_ratio=float(policy["oi_min_coverage_ratio"]),
                        staleness_tolerance_ms=int(stale_tol),
                        oi_first_utc=_iso(first_ms), oi_last_utc=_iso(last_ms),
                        win_start_utc=_iso(win_start_ms), win_end_utc=_iso(win_end_ms),
                        note="缺 OI 记 NaN，不写 0 / 0.5；不足只降级为 UNPROVEN"))

    if win_start_ms is not None and first_ms > int(win_start_ms):
        out.append(_chk("OI_WINDOW_START", UNPROVEN,
                        f"持仓量历史起点晚于窗口起点：OI 首条 {_iso(first_ms)} > "
                        f"窗口起点 {_iso(win_start_ms)}",
                        oi_first_utc=_iso(first_ms), win_start_utc=_iso(win_start_ms),
                        late_by_hours=round((first_ms - int(win_start_ms)) / 3_600_000, 3)))
    if win_end_ms is not None and last_ms < int(win_end_ms) - end_tol:
        out.append(_chk("OI_WINDOW_END", UNPROVEN,
                        f"持仓量末端早于窗口末端超过一个采样步长：OI 末条 {_iso(last_ms)} < "
                        f"窗口末端 {_iso(win_end_ms)}（容差 {end_tol / 60_000:g} 分钟）",
                        oi_last_utc=_iso(last_ms), win_end_utc=_iso(win_end_ms),
                        tolerance_ms=int(end_tol), observed_step_ms=int(oi_step)))

    if bool(policy["oi_pit_required"]):
        # IFACE-01: 严格因果契约 bucket_end <= available_at <= decision_time
        causal_pass = (
            with_pit == len(raw_pts) and len(raw_pts) > 0 and
            pit_tainted == 0 and
            pit_unclosed_invalids == 0 and
            pit_future_invalids == 0 and
            pit_post_decision_invalids == 0 and
            ratio >= float(policy["oi_min_coverage_ratio"])
        )
        if causal_pass:
            out.append(_chk("OI_PIT", PASS,
                            f"持仓量可得时刻已证明：{with_pit}/{len(raw_pts)} 条具备可核验到达时间，"
                            "数据桶严格因果闭合（bucket_end <= available_at <= decision_time）且无回填污染",
                            rows=int(len(raw_pts)), rows_with_available_at=int(with_pit),
                            rows_with_pit=int(with_pit),
                            available_field=pit_field, is_backfill=False, has_valid_arrival=True,
                            tainted=False, is_inferred=False,
                            observed_step_ms=int(oi_step),
                            note="具备真实到达时间证据且满足严格因果闭环"))
        else:
            reasons = []
            if with_pit < len(raw_pts):
                reasons.append(f"{len(raw_pts) - with_pit}/{len(raw_pts)} 条缺失有效到达时间")
            if pit_tainted > 0:
                reasons.append(f"{pit_tainted} 条受回填/推断污染")
            if pit_unclosed_invalids > 0:
                reasons.append(f"{pit_unclosed_invalids} 条到达时间早于桶闭合（前视未闭合违规）")
            if pit_future_invalids > 0:
                reasons.append(f"{pit_future_invalids} 条到达时间超出评估窗口（未来数据违规）")
            if pit_post_decision_invalids > 0:
                reasons.append(f"{pit_post_decision_invalids} 处决策发生于数据到达之前（决策前视违规）")
            if ratio < float(policy["oi_min_coverage_ratio"]):
                reasons.append("决策时刻可用覆盖率未达标")
            reason_str = "；".join(reasons) if reasons else "历史可得性证据不完备"
            out.append(_chk("OI_PIT", UNPROVEN,
                            f"持仓量可得时刻不可证：{reason_str}（历史可得性未证明 ⇒ 不得声称当时拿得到）",
                            rows=int(len(raw_pts)), rows_with_available_at=int(with_pit),
                            rows_with_pit=int(with_pit),
                            available_field=pit_field, is_backfill=(pit_tainted > 0),
                            has_valid_arrival=False,
                            unclosed_invalids=int(pit_unclosed_invalids),
                            future_invalids=int(pit_future_invalids),
                            post_decision_invalids=int(pit_post_decision_invalids),
                            tainted=(pit_tainted > 0), is_inferred=False,
                            note="冷档 available_at 缺失或因果未闭合 ⇒ 保持 UNPROVEN"))

    if not [c for c in out if c["status"] != PASS]:
        out.append(_chk("OI_OK", PASS,
                        f"持仓量可得性充分：{have}/{len(closes)} 根信号 bar 有 OI（{ratio:.2%}），"
                        f"且 {with_pit}/{len(raw_pts)} 条带可得时刻",
                        bars=int(len(closes)), bars_with_oi=int(have),
                        coverage_ratio=round(ratio, 6), staleness_tolerance_ms=int(stale_tol),
                        rows=int(len(raw_pts)), rows_with_available_at=int(with_pit),
                        oi_first_utc=_iso(first_ms), oi_last_utc=_iso(last_ms),
                        observed_step_ms=int(oi_step)))
    return out


# ══════════════════════════ 判据 5：资金费缺口（只出 UNPROVEN，不补 0）══════════════════════════

def _funding_frequency(ts_sorted: List[int]) -> Optional[int]:
    """由真实 settled 事件的间隔众数观测频率（与 `_funding_frequency` 同判据，不假设 8h）。"""
    diffs = [b - a for a, b in zip(ts_sorted, ts_sorted[1:])]
    if not diffs:
        return None
    c = Counter(int(round(d / 1000)) for d in diffs)
    return int(c.most_common(1)[0][0]) * 1000


def _expected_grid(ts_sorted: List[int], interval_ms: Optional[int],
                   win_start_ms: int, win_end_ms: int) -> Optional[List[int]]:
    """应有结算网格（半开 [start, end)，**含左端点**；锚点 = 回看窗内最后一个 <= start 的真实事件）。"""
    if not interval_ms or not ts_sorted:
        return None
    anchor = None
    for t in ts_sorted:
        if t <= win_start_ms:
            anchor = t
        else:
            break
    if anchor is None:
        anchor = ts_sorted[0]
        while anchor - interval_ms >= win_start_ms:
            anchor -= interval_ms
    grid: List[int] = []
    t = anchor
    while t < win_end_ms:
        if win_start_ms <= t < win_end_ms:
            grid.append(int(t))
        t += interval_ms
    return grid


def _check_funding(rows: Sequence, win_start_ms: Optional[int], win_end_ms: Optional[int],
                   policy: dict) -> List[dict]:
    """settled 事件覆盖判定：缺格 / 缺字段 / 频率不可证 一律 UNPROVEN，不补 0。"""
    tol = int(policy["funding_grid_tol_ms"])
    lookback = int(policy["funding_lookback_ms"])
    all_ev: List[dict] = []
    for r in rows:
        t = _int(_get(r, ("funding_time", "time_ms", "ts", "timestamp")))
        if t is None:
            continue
        all_ev.append({
            "t": t,
            "rate": _num(_get(r, ("funding_rate", "rate"))),
            "mark": _num(_get(r, ("mark_price", "mark"))),
            "type": str(_get(r, ("rate_type", "type"), "") or ""),
        })
    all_ev.sort(key=lambda e: e["t"])

    if win_start_ms is None or win_end_ms is None:
        return [_chk("FUNDING_WINDOW", UNPROVEN, "未提供窗口起止，无法判定结算覆盖")]

    ws, we = int(win_start_ms), int(win_end_ms)
    if not all_ev:
        return [_chk("FUNDING_EMPTY", UNPROVEN,
                     "回看窗内 0 条资金费事件 ⇒ 结算频率与覆盖均不可证（不假设 8h、不补 0）",
                     rows_in=len(rows), lookback_ms=lookback)]

    settled = [e for e in all_ev if e["type"] == "settled"]
    if not settled:
        return [_chk("FUNDING_TYPE", UNPROVEN,
                     f"{len(all_ev)} 条事件中 0 条为 settled ⇒ 资金费覆盖不可证",
                     rows_in=len(rows))]

    out: List[dict] = []
    ts_all = [e["t"] for e in all_ev]
    interval = _funding_frequency([e["t"] for e in settled])
    if interval is None:
        out.append(_chk("FUNDING_FREQ", UNPROVEN,
                        "settled 事件不足 2 条，结算间隔不可观测 ⇒ 频率未验证（不假设 8h）",
                        settled_rows=len(settled)))
        return out

    grid = _expected_grid(ts_all, interval, ws, we)
    in_win = [e for e in settled if ws <= e["t"] < we]
    bad_fields = [e["t"] for e in in_win if e["rate"] is None or e["mark"] is None]

    missing: List[int] = []
    if grid:
        actual = sorted(e["t"] for e in in_win)
        for g in grid:
            i = bisect.bisect_left(actual, g - tol)
            if not (i < len(actual) and actual[i] <= g + tol):
                missing.append(int(g))

    if missing:
        out.append(_chk("FUNDING_GAP", UNPROVEN,
                        f"资金费结算缺格 {len(missing)} 处（应有 {len(grid)}，"
                        f"实际 {len(in_win)}，观测频率 {interval / 3_600_000:g}h）",
                        expected_settlements=len(grid), actual_settlements=len(in_win),
                        missing_count=len(missing),
                        missing_utc=[_iso(t) for t in missing[:policy["max_samples"]]],
                        observed_interval_hours=interval / 3_600_000,
                        match_tolerance_ms=tol,
                        note="缺格记 funding_gap，不补 0、不按 8h 推算补点"))
    if bad_fields:
        out.append(_chk("FUNDING_FIELDS", UNPROVEN,
                        f"{len(bad_fields)} 条窗口内结算缺 rate/mark",
                        sample_utc=[_iso(t) for t in bad_fields[:policy["max_samples"]]]))
    if not out:
        out.append(_chk("FUNDING_OK", PASS,
                        f"资金费结算覆盖完整：应有 {len(grid)}、实际 {len(in_win)}，"
                        f"观测频率 {interval / 3_600_000:g}h，逐网格点命中（容差 {tol / 1000:g}s）",
                        expected_settlements=len(grid), actual_settlements=len(in_win),
                        observed_interval_hours=interval / 3_600_000,
                        match_tolerance_ms=tol, settled_rows=len(settled),
                        first_utc=_iso(in_win[0]["t"]) if in_win else None,
                        last_utc=_iso(in_win[-1]["t"]) if in_win else None))
    return out


# ══════════════════════════ 判据 5：辅助序列（4H）覆盖 ══════════════════════════
#
# 为什么需要这条判据（45 号）：
#   `EventTrendRunner.load_symbol_data` 返回的 `df_4h` 同时进入两处计算 ——
#     ① `run_state_machine_simulation`：`sub_4h.tail(42)` 的 high/low/close 决定**箱体**，
#        `sub_4h["atr"].iloc[-1]` 决定**箱体宽度与全部 ATR 倍数**；
#     ② `run_event_trend_backtest(df_4h=...)`：4H `low` 决定**跟踪止损**候选。
#   而 43 号审计证明：`snapshot_from_frames` 从来没有 `df_4h` 参数 ⇒ 上述数据**从不进快照**，
#   门禁对 4H 的完整性一无所知。本判据只补这一个缺口，**不改任何策略计算**。
#
# 分级原则（与 31 号一致，只加不破）：
#   物理不可能（high<low / 价格<=0 / 非有限 / 缺失 OHLC） ⇒ severe ⇒ **两种模式都阻断**
#   结构破坏（乱序 / 重复 / 未收盘 / 不在 4h 网格）       ⇒ FAIL  ⇒ 阻断 TRUSTED
#   覆盖不足（缺口 / 窗口前不足 42 根 / 尾部未覆盖窗口末）⇒ UNPROVEN ⇒ 阻断 TRUSTED
#   ATR 无效（最后 42 根出现非有限或 <=0）                ⇒ FAIL  ⇒ 阻断 TRUSTED

AUX_ROLE_ATR_BOX_TRAIL = "atr_box_trailing_stop"


def _prefix_checks(checks: List[dict], prefix: str, tag: str) -> List[dict]:
    """给既有判据的 id 加前缀、detail 加标签，**不改状态与严重度**。

    复用 `_check_ohlc` 而不重写：4H 与 1H 的「物理不可能」定义必须逐字一致，
    否则同一根坏 bar 在两条序列上会得出不同结论。
    """
    out = []
    for c in checks:
        d = dict(c)
        d["id"] = f"{prefix}{c['id']}"
        d["detail"] = f"{tag}{c['detail']}"
        out.append(d)
    return out


def _check_aux(aux_bars: List[dict], aux_tf: Optional[str], aux_role: Optional[str],
               aux_warmup_bars: int, win_start_ms: Optional[int],
               win_end_ms: Optional[int], sig_tf: str, policy: dict) -> List[dict]:
    """辅助序列（本项目里是 4H）的完整性判据。

    `aux_bars` 里允许带 `atr` 列（真实调用链就是带着它算箱体宽度的）。
    `aux_required=False` 且未提供序列时**不产出任何判据** ⇒ 老快照行为逐位不变。
    """
    required = bool(policy.get("aux_required"))
    tag = f"[{aux_tf or '未声明周期'}·{aux_role or '未声明用途'}] "
    label = (f"辅助序列 {aux_tf}（{aux_role or '未声明用途'}，"
             f"含预热 {int(aux_warmup_bars or 0)} 根）")

    if not aux_bars:
        if not required:
            return []
        return [_chk("AUX_ABSENT", UNPROVEN,
                     f"{label}：快照未携带任何辅助 bar ⇒ 4H 完整性未被证明，"
                     "不得据此声明可信（4H 参与 ATR / 箱体 / 跟踪止损）",
                     timeframe=aux_tf, role=aux_role, rows=0,
                     declared_required=True)]

    out: List[dict] = []

    if aux_tf is None:
        return [_chk("AUX_TIMEFRAME", FAIL,
                     f"{label}：携带了辅助 bar 却未声明 aux_timeframe ⇒ 无法判定网格与间隔",
                     rows=len(aux_bars))]
    if aux_tf not in INTERVAL_MS:
        return [_chk("AUX_TIMEFRAME", FAIL,
                     f"{label}：不支持的辅助周期 {aux_tf!r}",
                     timeframe=aux_tf, rows=len(aux_bars))]
    if aux_tf == sig_tf:
        # 45 号工单 A 节明文：不得用「只检查 1H」代替 4H 检查。
        return [_chk("AUX_TF_MISMATCH", FAIL,
                     f"{label}：辅助序列周期与信号序列相同（{sig_tf}）"
                     "⇒ 这是重复检查 1H，不是检查 4H",
                     timeframe=aux_tf, signal_timeframe=sig_tf, rows=len(aux_bars))]

    iv = INTERVAL_MS[aux_tf]

    # 归一化；把 atr 一并带上（`_bar_norm` 不管它，但它决定箱体宽度）
    bars = []
    for raw in aux_bars:
        b = _bar_norm(raw)
        b["atr"] = _num(_get(raw, ("atr",)))
        bars.append(b)

    no_ts = [i for i, b in enumerate(bars) if b["open_time"] is None]
    if no_ts:
        out.append(_chk("AUX_TIMESTAMP", FAIL,
                        f"{label}：{len(no_ts)} 根缺少可用 open_time",
                        timeframe=aux_tf,
                        sample_idx=no_ts[:policy["max_samples"]]))
        return out

    bars.sort(key=lambda b: b["open_time"])
    ts = [b["open_time"] for b in bars]
    n = len(ts)

    # 乱序：入参必须已按 open_time 非降序。到这里还乱 = 上游契约破坏。
    # 注意：先于排序判断才有意义，所以用原始顺序单独算一次。
    raw_ts = [b["open_time"] for b in (_bar_norm(r) for r in aux_bars)]
    disorder = [i for i in range(1, len(raw_ts))
                if raw_ts[i] is not None and raw_ts[i - 1] is not None
                and int(raw_ts[i]) < int(raw_ts[i - 1])]
    if disorder:
        out.append(_chk("AUX_ORDER", FAIL,
                        f"{label}：{len(disorder)} 处 open_time 逆序（未按时间排序）",
                        timeframe=aux_tf, rows=n,
                        sample_utc=[_iso(raw_ts[i]) for i in disorder[:policy["max_samples"]]]))

    dup = [t for t, c in Counter(ts).items() if c > 1]
    if dup:
        out.append(_chk("AUX_DUPLICATE", FAIL,
                        f"{label}：{len(dup)} 个 open_time 重复"
                        "（42 根箱体窗口会取到错误的 42 根）",
                        timeframe=aux_tf,
                        sample_utc=[_iso(t) for t in sorted(dup)[:policy["max_samples"]]]))

    # 已收盘：4H 箱体只能吃已收盘 bar（`sub_4h = df_4h[close_time <= t_close]` 是下游的闸，
    # 门禁这里先确认序列本身没有混入未收盘 bar）
    unclosed = [b["open_time"] for b in bars if b["is_closed"] not in (1, None)]
    if all(b["is_closed"] is None for b in bars):
        out.append(_chk("AUX_UNCLOSED", UNPROVEN,
                        f"{label}：未提供 is_closed 字段，收盘状态无法证明",
                        timeframe=aux_tf, rows=n))
    elif unclosed:
        out.append(_chk("AUX_UNCLOSED", FAIL,
                        f"{label}：{len(unclosed)} 根未收盘 bar 混入",
                        timeframe=aux_tf,
                        sample_utc=[_iso(t) for t in unclosed[:policy["max_samples"]]]))

    # 网格对齐
    off_grid = [t for t in ts if int(t) % iv != 0]
    bad_close = [(b["open_time"], b["close_time"]) for b in bars
                 if b["close_time"] is not None
                 and int(b["close_time"]) not in (int(b["open_time"]) + iv,
                                                  int(b["open_time"]) + iv - 1)]
    if off_grid or bad_close:
        out.append(_chk("AUX_ALIGNMENT", FAIL,
                        f"{label}：{len(off_grid)} 根 open_time 不在 {aux_tf} 网格上，"
                        f"{len(bad_close)} 根 close_time 不等于 open_time+{iv}",
                        timeframe=aux_tf, interval_ms=iv,
                        off_grid_sample=[_iso(t) for t in off_grid[:policy["max_samples"]]],
                        bad_close_sample=[{"open_utc": _iso(o), "close_utc": _iso(c)}
                                          for o, c in bad_close[:policy["max_samples"]]]))

    # 缺口：4H 缺一根 ⇒ 42 根箱体窗口跨过更长的时间跨度 ⇒ 箱体高低点口径被静默改变。
    uniq = sorted(set(ts))
    gaps = [(uniq[i], uniq[i + 1]) for i in range(len(uniq) - 1)
            if uniq[i + 1] - uniq[i] != iv]
    if gaps:
        missing = sum(max(0, (b - a) // iv - 1) for a, b in gaps)
        out.append(_chk("AUX_GAP", UNPROVEN,
                        f"{label}：{len(gaps)} 处时间缺口（累计缺 {missing} 根）"
                        "⇒ 箱体 42 根窗口的实际时间跨度已不可证明",
                        timeframe=aux_tf, interval_ms=iv, gaps=len(gaps),
                        missing_bars=int(missing),
                        sample=[{"after_utc": _iso(a), "before_utc": _iso(b),
                                 "missing": int((b - a) // iv - 1)}
                                for a, b in gaps[:policy["max_samples"]]]))

    # 覆盖：窗口之前必须有足够根数（箱体回看），末根必须覆盖到窗口末
    if win_start_ms is not None and win_end_ms is not None:
        need_before = int(policy.get("aux_min_bars_before_window") or 0)
        n_before = sum(1 for t in uniq if int(t) < int(win_start_ms))
        last_close = int(uniq[-1]) + iv
        tail_ok = last_close <= int(win_end_ms) and (int(win_end_ms) - last_close) < iv
        if n_before < need_before or not tail_ok:
            out.append(_chk("AUX_COVERAGE", UNPROVEN,
                            f"{label}：覆盖不足（窗口前 {n_before} 根 < 需要 {need_before}；"
                            f"末根收盘 {_iso(last_close)} {'已' if tail_ok else '未'}覆盖窗口末 "
                            f"{_iso(win_end_ms)}）",
                            timeframe=aux_tf, rows_before_window=int(n_before),
                            required_before_window=need_before,
                            last_close_utc=_iso(last_close),
                            win_end_utc=_iso(win_end_ms),
                            tail_ok=bool(tail_ok)))

    # 物理 OHLC：复用 31 号判据（severe 由 `_check_ohlc` 打标 ⇒ 两种模式都阻断）
    out += _prefix_checks(_check_ohlc(bars, label, policy), "AUX_", tag)

    # ATR 有效性：最后 N 根会被 `sub_4h["atr"].iloc[-1]` 取用；
    # NaN / <=0 会让 `check_and_form_box` 直接 return None ⇒ 箱体永不形成 ⇒ 静默 0 候选。
    tail_n = int(policy.get("aux_atr_tail_bars") or 42)
    tail = bars[-tail_n:] if tail_n > 0 else bars
    atr_bad = [b for b in tail if b["atr"] is None or float(b["atr"]) <= 0.0]
    if atr_bad:
        out.append(_chk("AUX_ATR_INVALID", FAIL,
                        f"{label}：最后 {len(tail)} 根里有 {len(atr_bad)} 根 ATR 非有限或 <= 0"
                        "⇒ `check_and_form_box` 将直接返回 None，箱体永不形成（静默 0 候选）",
                        timeframe=aux_tf, tail_bars=int(len(tail)),
                        bad_rows=len(atr_bad),
                        sample=[{"open_utc": _iso(b["open_time"]), "atr": b["atr"]}
                                for b in atr_bad[:policy["max_samples"]]]))
    elif any(b["atr"] is not None for b in tail):
        out.append(_chk("AUX_ATR_OK", PASS,
                        f"{label}：最后 {len(tail)} 根 ATR 均有限且 > 0"
                        f"（区间 {min(float(b['atr']) for b in tail if b['atr'] is not None):.6g}"
                        f" ~ {max(float(b['atr']) for b in tail if b['atr'] is not None):.6g}）",
                        timeframe=aux_tf, tail_bars=int(len(tail))))

    if not out:
        out.append(_chk("AUX_OK", PASS,
                        f"{label}：{n} 根，等间隔、已收盘、无重复、无乱序、"
                        f"窗口前 {sum(1 for t in uniq if win_start_ms is not None and int(t) < int(win_start_ms))} 根",
                        timeframe=aux_tf, role=aux_role, rows=n,
                        first_utc=_iso(uniq[0]), last_utc=_iso(uniq[-1]),
                        interval_ms=iv))
    return out


# ══════════════════════════ 汇总判定 ══════════════════════════

def evaluate(snapshot: dict, mode: str = MODE_TRUSTED,
             policy: Optional[dict] = None) -> dict:
    """对一份快照出判定。**纯函数**：不读库、不写盘、不改入参。

    返回的 `verdict` 是事实（PASS/FAIL/UNPROVEN），`claim_level` 是**允许的主张等级**，
    二者分离：隔离模式可以放行，但 `claim_level` 恒为 `ISOLATED_RESEARCH`。
    """
    if mode not in MODES:
        raise ValueError(f"mode 必须是 {MODES} 之一（得到 {mode!r}）")
    pol = dict(DEFAULT_POLICY)
    pol.update(policy or {})

    sig_tf = str(snapshot.get("signal_timeframe") or "")
    exec_tf = str(snapshot.get("exec_timeframe") or "")
    for tf in (sig_tf, exec_tf):
        if tf not in INTERVAL_MS:
            raise ValueError(f"不支持的周期 {tf!r}（可选 {sorted(INTERVAL_MS)}）")

    win_start = _int(snapshot.get("win_start_ms"))
    win_end = _int(snapshot.get("win_end_ms"))
    warmup = int(snapshot.get("warmup_bars") or 0)
    s_iv = INTERVAL_MS[sig_tf]

    sig_bars = [_bar_norm(b) for b in (snapshot.get("signal_bars") or [])]
    exec_bars = [_bar_norm(b) for b in (snapshot.get("exec_bars") or [])]
    sig_bars = [b for b in sig_bars if b["open_time"] is not None]
    exec_bars = [b for b in exec_bars if b["open_time"] is not None]
    sig_bars.sort(key=lambda b: b["open_time"])
    exec_bars.sort(key=lambda b: b["open_time"])

    sig_expect_start = (win_start - warmup * s_iv) if win_start is not None else None

    # 只判**声明窗口内**的 bar（窗口外的行既不算缺口也不算越界，仅记数量）。
    # 这不是修补数据：截取的是窗口范围，不是数值。
    sig_trimmed = exec_trimmed = 0
    if sig_expect_start is not None and win_end is not None:
        sig_bars, sig_trimmed = _window_filter(sig_bars, sig_expect_start, win_end)
    if win_start is not None and win_end is not None:
        exec_bars, exec_trimmed = _window_filter(exec_bars, win_start, win_end)

    checks: List[dict] = []
    checks += _check_bars(sig_bars, sig_tf, f"信号序列 {sig_tf}（含预热 {warmup} 根）",
                          sig_expect_start, win_end, pol)
    checks += _check_bars(exec_bars, exec_tf, f"执行序列 {exec_tf}（窗口）",
                          win_start, win_end, pol)
    checks += _check_alignment(sig_bars, sig_tf, f"信号序列 {sig_tf}", pol)
    checks += _check_alignment(exec_bars, exec_tf, f"执行序列 {exec_tf}", pol)
    checks += _check_cross_tf(sig_tf, exec_tf, win_start, win_end, warmup)
    checks += _check_ohlc(sig_bars, f"信号序列 {sig_tf}", pol)
    checks += _check_ohlc(exec_bars, f"执行序列 {exec_tf}", pol)
    checks += _check_oi(snapshot.get("oi_rows") or [], sig_bars, sig_tf,
                        win_start, win_end, pol)
    checks += _check_funding(snapshot.get("funding_rows") or [], win_start, win_end, pol)

    # ── 辅助序列（4H）：45 号新增 ──
    # `aux_required` 只能被快照**打开**，不能被快照或 policy 关掉（fail-closed）：
    #   · 生产接线 `snapshot_from_frames` 恒置 True；
    #   · 手工构造的老快照不设该键、也没有 aux bar ⇒ 保持 False ⇒ 行为不变；
    #   · 只要快照里**带了** aux bar，无论谁把 `aux_required` 写成 False，都照样按必需处理
    #     ——「给了数据却不检查」是最坏的一种静默。
    aux_bars = list(snapshot.get("aux_bars") or [])
    if snapshot.get("aux_required") or aux_bars:
        pol["aux_required"] = True
    aux_tf_raw = snapshot.get("aux_timeframe")
    aux_tf = str(aux_tf_raw).strip().lower() if aux_tf_raw else None
    aux_role = snapshot.get("aux_role")
    aux_warmup = int(snapshot.get("aux_warmup_bars") or 0)
    checks += _check_aux(aux_bars, aux_tf, aux_role, aux_warmup,
                         win_start, win_end, sig_tf, pol)

    statuses = [c["status"] for c in checks]
    verdict = FAIL if FAIL in statuses else (UNPROVEN if UNPROVEN in statuses else PASS)
    block_reasons = [f"[{c['id']}] {c['detail']}" for c in checks if c["status"] == FAIL]
    unproven_reasons = [f"[{c['id']}] {c['detail']}" for c in checks if c["status"] == UNPROVEN]

    data_qualifies = (verdict == PASS)
    # 「可信回测」是**数据合格 + 本次运行声明为 trusted** 两个条件的交集：
    # 数据再干净，只要运行方选的是 isolated_research，主张等级就只能是隔离研究。
    trusted_ok = bool(data_qualifies and mode == MODE_TRUSTED)
    # 31 号：严重度出口。原先 isolated 分支硬编码 `True`，与 verdict 完全解耦
    # （`data_qualifies` 算了不用），导致「价格 <= 0」「high < low」也能跑完并出指标。
    # 现在：严重 OHLC 在**两种模式**都阻断；普通异常与 UNPROVEN（如 OI_PIT 未证）仍按隔离研究放行，
    # 但主张等级恒为 ISOLATED_RESEARCH，不得升格为可信。
    severe_checks = [c for c in checks if c.get("severity") == SEVERITY_SEVERE]
    has_severe = bool(severe_checks)
    if mode == MODE_TRUSTED:
        run_allowed = trusted_ok
        run_allowed_reason = (
            "trusted：verdict=PASS，数据合格，放行正式可信回测" if trusted_ok
            else f"trusted：verdict={verdict}，正式可信回测只接受 PASS")
    else:
        run_allowed = not has_severe
        run_allowed_reason = (
            f"isolated_research：命中严重异常 {[c['id'] for c in severe_checks]}，隔离模式同样阻断"
            if has_severe else
            "isolated_research：无严重异常，按隔离研究放行（主张等级恒为 ISOLATED_RESEARCH）")
    isolation_required = (not trusted_ok)

    core = {
        "gate": GATE_ID,
        "gate_version": GATE_VERSION,
        "mode": mode,
        "subject": {
            "exchange": snapshot.get("exchange"), "market": snapshot.get("market"),
            "symbol": snapshot.get("symbol"),
            "signal_timeframe": sig_tf, "exec_timeframe": exec_tf,
            "win_start_ms": win_start, "win_end_ms": win_end,
            "win_start_utc": _iso(win_start), "win_end_utc": _iso(win_end),
            "warmup_bars": warmup,
            "bars_outside_window": {"signal": int(sig_trimmed), "exec": int(exec_trimmed)},
        },
        "verdict": verdict,
        "data_qualifies_for_trusted": bool(data_qualifies),
        "trusted_backtest_allowed": bool(trusted_ok),
        "run_allowed": bool(run_allowed),
        "run_allowed_reason": run_allowed_reason,
        "severe_blocked": bool(has_severe),
        "severe_check_ids": [c["id"] for c in severe_checks],
        "severe_reasons": [f"[{c['id']}] {c['detail']}" for c in severe_checks],
        "severity_policy": {
            "version": SEVERITY_POLICY_VERSION,
            "severe_criteria": list(SEVERE_OHLC_CRITERIA),
            "normal_criteria": list(NORMAL_OHLC_CRITERIA),
            "trusted_blocks": "FAIL 与 UNPROVEN（既有口径，本单未改）",
            "isolated_blocks": "仅 severe（本单新增）；normal 与 UNPROVEN 仍放行并打隔离标记",
        },
        "isolation_required": bool(isolation_required),
        "isolation_label": (ISOLATION_LABEL if isolation_required else None),
        "claim_level": ("TRUSTED" if trusted_ok else "ISOLATED_RESEARCH"),
        "block_reasons": block_reasons,
        "unproven_reasons": unproven_reasons,
        "checks": checks,
        "counts": {
            "checks": len(checks),
            "fail": sum(1 for s in statuses if s == FAIL),
            "unproven": sum(1 for s in statuses if s == UNPROVEN),
            "pass": sum(1 for s in statuses if s == PASS),
        },
        "no_fabrication": {
            "oi_filled_rows": 0,
            "funding_filled_rows": 0,
            "price_repaired_rows": 0,
            "policy": "本门禁不生成任何行情数值：OI 缺即缺、资金费缺格即缺格、异常 OHLC 只报不改",
            "input_rows": {"signal": len(sig_bars), "exec": len(exec_bars),
                           "oi": len(snapshot.get("oi_rows") or []),
                           "funding": len(snapshot.get("funding_rows") or [])},
        },
        "policy": pol,
        # 45 号：辅助序列（4H）的覆盖声明。审计方据此判断「这份判定到底看没看 4H」，
        # 而不是靠人去猜。`declared=False` 且 `required=True` ⇒ 一定是 AUX_ABSENT。
        "aux": {
            "declared": bool(aux_tf or aux_bars),
            "timeframe": aux_tf,
            "role": aux_role,
            "required": bool(pol.get("aux_required")),
            "policy_version": AUX_POLICY_VERSION,
            "warmup_bars": int(aux_warmup),
            "bars": len(aux_bars),
            "consumed_by": ["atr", "box_width", "trailing_stop"],
        },
        "input_sha256": _sha(snapshot),
        "summary_zh": None,          # 下面填
    }
    core["summary_zh"] = (
        f"判定 {verdict}（失败 {core['counts']['fail']} 项、未证 {core['counts']['unproven']} 项）"
        f"｜正式可信回测{'放行' if trusted_ok else '拒绝'}"
        f"｜{mode} 模式{'可运行' if run_allowed else '拒绝运行'}"
        f"｜主张等级 {core['claim_level']}"
        + (f"｜严重异常 {len(severe_checks)} 项（已阻断）" if has_severe else "")
    )
    core["verdict_sha256"] = _sha(core)      # 不含时间戳 ⇒ 同输入恒等
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **core,
    }


# ══════════════════════════ 取数（只读，复用既有读取器）══════════════════════════

CANDLE_PAGE = 2000
CANDLE_HARD_CAP = 20_000


def _fetch_candles_all(repo, ex: str, mk: str, sym: str, tf: str,
                       start_ms: int, end_ms: int) -> List[dict]:
    """分页取满 `[start_ms, end_ms)`（口径同 `backtest_runner._fetch_candles_all`）。

    与 runner 的一处差异：每页 `limit` 取 `min(2000, 区间期望根数)`。
    原因：`MarketDataRepository.candles` 的冷档路由条件是 `len(hot) < limit`，
    按固定 2000 请求会让「热层其实已覆盖全区间」的短窗口也无谓触发冷档扫描
    （本机未安装 duckdb ⇒ 直接报错）。按期望根数请求时，热层取满即 `len(hot)==limit`，
    冷档只在**热层确实缺行**时才被问到，语义不变、无谓扫描消失。
    """
    iv = INTERVAL_MS[tf]
    start_ms, end_ms = int(start_ms), int(end_ms)
    pages: List[List[dict]] = []
    end = end_ms
    total = 0
    while True:
        expected = max(1, (int(end) - start_ms) // iv)
        rows = repo.candles(ex, mk, sym, tf, start=start_ms, end=end,
                            limit=min(CANDLE_PAGE, expected))
        if not rows:
            break
        pages.append(rows)
        total += len(rows)
        first = int(rows[0]["open_time"])
        if first <= start_ms:
            break
        end = first
        if total > CANDLE_HARD_CAP:
            raise RuntimeError(f"K 线分页超过上限 {CANDLE_HARD_CAP}"
                               f"（{ex}/{mk}/{sym}/{tf}）⇒ 拒绝静默截断，请缩小窗口")
    merged: Dict[int, dict] = {}
    for rows in reversed(pages):
        for r in rows:
            merged[int(r["open_time"])] = r
    return [merged[k] for k in sorted(merged)]


def collect_snapshot(repo, win: dict, policy: Optional[dict] = None) -> dict:
    """按已解析的 `win`（`backtest_runner.resolve_window` 的输出）只读取数，组装快照。

    只调用 `repo.candles / repo.oi / repo.funding`，**不写库、不联网、不补数**。
    """
    pol = dict(DEFAULT_POLICY)
    pol.update(policy or {})
    ex, mk = win["exchange"], win["market"]
    sym = win["symbol"]
    sig_tf, exec_tf = win["signal_timeframe"], win["exec_timeframe"]
    ws, we = int(win["win_start_ms"]), int(win["win_end_ms"])
    warmup = int(win.get("warmup_bars") or 0)
    sig_start = int(win.get("signal_series_start_ms") or (ws - warmup * INTERVAL_MS[sig_tf]))

    sig_bars = _fetch_candles_all(repo, ex, mk, sym, sig_tf, sig_start, we)
    exec_bars = _fetch_candles_all(repo, ex, mk, sym, exec_tf, ws, we)
    oi_rows = repo.oi(ex, mk, sym, period="5m", start=sig_start, end=we, limit=20_000)
    fu_rows = repo.funding(ex, mk, sym, start=ws - int(pol["funding_lookback_ms"]),
                           end=we - 1, limit=2_000)

    return {
        "exchange": ex, "market": mk, "symbol": sym,
        "signal_timeframe": sig_tf, "exec_timeframe": exec_tf,
        "win_start_ms": ws, "win_end_ms": we, "warmup_bars": warmup,
        "signal_bars": [dict(r) for r in sig_bars],
        "exec_bars": [dict(r) for r in exec_bars],
        "oi_rows": [dict(r) for r in oi_rows],
        "funding_rows": [dict(r) for r in fu_rows],
        "provenance": "collect_snapshot: MarketDataRepository（只读）",
    }


def snapshot_from_loaded_inputs(inputs: dict, win: dict) -> dict:
    """把 `backtest_runner.load_inputs` 的返回结构直接转成快照（复用已加载数据）。

    `load_inputs` 已经做过连续性/覆盖校验并抛过错，此处补的是它**没查**的三类：
    异常 OHLC、网格错位、OI/资金费可得性。

    45 号：本函数**故意不声明 aux**。通用路径（`_run`）读的是 1h + 15m，
    策略计算里没有 4H ⇒ 它没有「4H 实际输入」可言。在这里补一个 `aux_required=True`
    等于让门禁去检查一份根本不影响该路径的数据，反而会伪造出「4H 已覆盖」的假象。
    4H 通道只属于 `snapshot_from_frames`（`_run_event_trend`）。
    """
    def _df_rows(df):
        if df is None:
            return []
        out = []
        for rec in df.to_dict("records"):
            rec = dict(rec)
            rec.setdefault("open_time", rec.get("timestamp"))
            rec.setdefault("is_closed", 1)
            out.append(rec)
        return out

    return {
        "exchange": win.get("exchange"), "market": win.get("market"),
        "symbol": win.get("symbol"),
        "signal_timeframe": win.get("signal_timeframe"),
        "exec_timeframe": win.get("exec_timeframe"),
        "win_start_ms": win.get("win_start_ms"), "win_end_ms": win.get("win_end_ms"),
        "warmup_bars": int(win.get("warmup_bars") or win.get("warmup_bars_requested") or 0),
        "signal_bars": _df_rows(inputs.get("sig_df")),
        "exec_bars": _df_rows(inputs.get("exec_df")),
        "oi_rows": [dict(r) for r in (inputs.get("oi_rows") or [])],
        "funding_rows": [dict(r) for r in (inputs.get("funding_events_all")
                                           or inputs.get("funding_events") or [])],
        "provenance": "snapshot_from_loaded_inputs: backtest_runner.load_inputs 输出",
    }


# ══════════════════════════ CLI ══════════════════════════

_EXIT = {PASS: 0, UNPROVEN: 2, FAIL: 3}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="backtest_data_gate",
        description="回测启动前数据质量门禁（只读）：对快照出 PASS / FAIL / UNPROVEN 判定")
    ap.add_argument("--snapshot", required=True, help="快照 JSON 路径（只读）")
    ap.add_argument("--mode", default=MODE_TRUSTED, choices=list(MODES),
                    help="trusted=正式可信回测；isolated_research=隔离研究运行")
    ap.add_argument("--policy", help="策略覆盖 JSON（可选）")
    ap.add_argument("--out", help="判定结果落盘路径（可选；不传则只打印）")
    args = ap.parse_args(argv)

    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    pol = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else None
    verdict = evaluate(snap, mode=args.mode, policy=pol)

    text = json.dumps(verdict, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(verdict["summary_zh"])
    for r in verdict["block_reasons"]:
        print("  失败：", r)
    for r in verdict["unproven_reasons"]:
        print("  未证：", r)
    print("  verdict_sha256:", verdict["verdict_sha256"])
    return _EXIT[verdict["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
