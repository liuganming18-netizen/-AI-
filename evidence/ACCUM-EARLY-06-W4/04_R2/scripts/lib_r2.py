# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 公共库：身份、官方快照、周期证据分级、预期时刻生成。

设计约束（来自工单）
------------------
1. 历史结算间隔只能**推断**周期，不能证明交易所当时的原生周期。
   官方证据缺失时，一律标 `inferred_*`，完整性最高只能到 `NO_GAP_DETECTED`（未证明）。
2. 预期结算时刻不能只按周期长度套 `(entry//step+1)*step`：
   相位（phase）必须来自观测，或来自官方文档；两者都没有 ⇒ 不可判定。
3. 周期或相位不确定时：**保留已观测事件的财务计算**，完整性标未证明，
   不补零、不静默判完整。
4. 真实证据与合成测试分开落盘，合成用例只在 `03_隔离反例` 里出现。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
ADAPTER_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2" / "out"
OUT.mkdir(parents=True, exist_ok=True)

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE_ROOT = ROOT / "数据" / "市场数据" / "parquet"
ACC2_JSON = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"
R1_JSON = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1" / "out" / "01_资金费重算与判据整改明细.json"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ADAPTER_DIR))

M15 = 15 * 60 * 1000
H1 = 3600 * 1000
DAY = 86400 * 1000

# 两套经济口径（名称与 replay_calc.CALIBERS 一致；本单不改其参数）
CALIBERS = ("生效视图_5bps", "策略包_3bps")

# ── 证据等级常量 ──────────────────────────────────────────────────────
# 周期（interval）证据
EV_OFFICIAL = "official_snapshot"          # 官方快照（带 updateTime），且覆盖持仓区间
EV_OFFICIAL_PARTIAL = "official_partial"   # 官方快照存在，但 updateTime 晚于持仓起点
EV_INFERRED = "inferred_uniform"           # 观测间隔恒定（仅推断）
EV_INFERRED_MULT = "inferred_with_multiples"  # 观测间隔是同一基数的整数倍（可能漏事件）
EV_SWITCH = "switch_detected"              # 观测到周期切换
EV_UNDET = "undetermined"                  # 不可判定

# 相位（phase）证据
PH_OBSERVED = "observed_consistent"        # 观测残差一致
PH_INCONSISTENT = "observed_inconsistent"  # 观测残差冲突
PH_INSUFFICIENT = "insufficient"           # 支撑事件太少

# 完整性结论
CP_NO_GAP = "NO_GAP_DETECTED"              # 在证据网格下未发现缺失（≠已证明完整）
CP_MISSING = "MISSING_DEMONSTRATED"        # 官方周期下至少一个预期时刻没有记录（演示性缺失）
CP_INCONSISTENT = "INCONSISTENT_INFERRED"  # 仅推断周期下与网格不符（不能叫「已证明缺失」）
CP_UNDET = "UNDETERMINED"                  # 周期或相位不足，无法判断
CP_NA = "NOT_APPLICABLE"                   # 拒单等不适用

# 主张强度（供 B 直接判读，避免把「未发现缺失」读成「已证明完整」）
CLAIM_PROVEN = "PROVEN"                    # 官方周期 + 相位一致 + 无缺失
CLAIM_NOT_PROVEN = "NOT_PROVEN"            # 仅推断
CLAIM_REFUTED = "REFUTED"                  # 已演示缺失 ⇒ 完整性被否证
CLAIM_UNKNOWN = "UNKNOWN"


def iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def sha256_file(p: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")))


def load_candidates() -> List[dict]:
    """原 51 候选（口径 A 的逐条记录，含 candidate_id / entry / exit）。

    只读 `ACCUM-EARLY-02` 的既有产物，不重跑策略、不改参数。
    """
    d = json.loads(ACC2_JSON.read_text(encoding="utf-8"))
    rows = [r for r in d["B_全量回放"]["逐条"] if r.get("caliber") == CALIBERS[0]]
    rows.sort(key=lambda r: (r["symbol"], r["candidate_id"]))
    return rows


def official_snapshot_path() -> Path:
    return OUT / "00_官方快照_fundingInfo.json"


def load_official_snapshot() -> dict:
    p = official_snapshot_path()
    if not p.exists():
        raise FileNotFoundError(f"缺少官方快照 {p}，请先运行 00_范围与身份锚定.py")
    return json.loads(p.read_text(encoding="utf-8"))


def official_index() -> Dict[str, dict]:
    """symbol -> {interval_hours, update_ms, disclaimer}"""
    snap = load_official_snapshot()
    idx: Dict[str, dict] = {}
    for r in snap["rows"]:
        sym = r.get("symbol")
        if not sym:
            continue
        iv = r.get("fundingIntervalHours")
        ut = r.get("updateTime")
        idx[sym] = {
            "interval_hours": int(iv) if iv is not None else None,
            "update_ms": int(ut) if ut is not None else None,
            "adjusted_cap": r.get("adjustedFundingRateCap"),
            "adjusted_floor": r.get("adjustedFundingRateFloor"),
            "disclaimer": r.get("disclaimer"),
        }
    return idx


# ── 观测序列工具 ──────────────────────────────────────────────────────
def intervals_h(times_ms: Sequence[int]) -> List[float]:
    return [(times_ms[i] - times_ms[i - 1]) / 3600000.0 for i in range(1, len(times_ms))]


def mode_step_hours(times_ms: Sequence[int]) -> Optional[float]:
    """众数步长（0.25h 粒度）。样本 < 2 返回 None。"""
    if len(times_ms) < 2:
        return None
    from collections import Counter
    c = Counter(round(x * 4) / 4 for x in intervals_h(times_ms))
    return c.most_common(1)[0][0]


def phase_residues(times_ms: Sequence[int], step_h: float) -> Dict[int, int]:
    """t mod step 的小时残差分布（只对整小时结算有意义）。"""
    from collections import Counter
    step_ms = int(round(step_h * H1))
    if step_ms <= 0:
        return {}
    c: Counter = Counter()
    for t in times_ms:
        c[(t // H1) % int(step_h)] += 1
    return dict(sorted(c.items()))


def dominant_phase_hours(times_ms: Sequence[int], step_h: float) -> Tuple[Optional[int], int, int]:
    """返回 (主导相位小时, 该相位支撑数, 总样本数)。"""
    r = phase_residues(times_ms, step_h)
    if not r:
        return None, 0, 0
    ph, n = max(r.items(), key=lambda kv: kv[1])
    return int(ph), int(n), int(sum(r.values()))


def grid_instants(phase_h: int, step_h: float, after_ms: int, upto_ms: int) -> List[int]:
    """生成 (after_ms, upto_ms] 内的结算时刻，相位以 phase_h 为准（不是默认 0）。

    这是本单与 R1 的关键差异：R1 用 `(entry//step+1)*step`，等价于强制 phase=0。
    """
    step_ms = int(round(step_h * H1))
    if step_ms <= 0 or upto_ms <= after_ms:
        return []
    phase_ms = int(round(phase_h * H1))
    # 第一个 > after_ms 的 phase + k*step
    k = (after_ms - phase_ms) // step_ms + 1
    t = phase_ms + k * step_ms
    out: List[int] = []
    while t <= upto_ms:
        out.append(t)
        t += step_ms
        if len(out) > 20000:      # 防御：异常参数不无限循环
            break
    return out


def detect_runs(times_ms: Sequence[int], min_len: int = 1) -> List[dict]:
    """把间隔序列压成连续段（同间隔相邻合并）。用于周期切换识别。"""
    if len(times_ms) < 2:
        return []
    iv = intervals_h(times_ms)
    runs: List[dict] = []
    for i, v in enumerate(iv):
        v = round(v, 2)
        if runs and runs[-1]["interval_h"] == v:
            runs[-1]["count"] += 1
            runs[-1]["end_ms"] = times_ms[i + 1]
        else:
            runs.append({"interval_h": v, "start_ms": times_ms[i],
                         "end_ms": times_ms[i + 1], "count": 1})
    return [r for r in runs if r["count"] >= min_len]


def fmt_h(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x:g}h"
