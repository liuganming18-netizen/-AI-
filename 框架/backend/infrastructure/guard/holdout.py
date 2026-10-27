"""留出段守卫：把「未来数据」变成一段有名字、可检查、会留痕的区间。

为什么需要这一层
────────────────
`experiment_registry` 提供了完整的记账能力（哈希链、设计身份、冻结、试验计数、
已观察样本登记、解封阻断），但它**只是记账**——实测结果是：2,125 个回测产物目录、
登记库里 0 条业务记录。说明「靠执行者自觉登记」在真实使用中等于没有。

本模块补的是**执行侧的第一道问句**：跑之前先问「这次要用的数据，是不是已经
落在留出段里了」。问完还要问「这段以前被谁看过」。

三条硬纪律
──────────
1. **留出段由人写死在文件里，绝不从请求里取。**
   否则「换个 window 参数就绕过检查」，守卫等于没有。
2. **未定义留出段时一律放行**（返回 `no_holdout`）。
   这不是偷懒，是为了让本模块上线时**不打断任何在跑的批量脚本**。
   当前项目就处于这种状态：`SplitPolicy` 全部未配置。
3. **本模块只读**，不写任何登记事件。
   污染事实由 `experiment_registry.observe()` 记 —— 一个模块只干一件事。

为什么不能只靠提示词约束
────────────────────────
AI 执行研究时并不是想作弊，它只是不知道哪条线不能跨，于是会读文件、看结果、
对着结果调参。这三件事在它看来都是正常工作。所以防线必须在**代码**里，
在它动手之前就问一句，而不是在提示词里写「请不要看」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

#: 留出段定义文件。放 `data/experiments/` 下与登记库同目录，便于一起备份/交接。
HOLDOUT_ENV = "FRAME_HOLDOUT_FILE"
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "experiments"
DEFAULT_HOLDOUT_FILE = _DEFAULT_ROOT / "holdout.json"

STATUS_NO_HOLDOUT = "no_holdout"
STATUS_CLEAR = "clear"
STATUS_OVERLAP = "overlaps"
STATUS_INVALID = "invalid"


def holdout_file() -> Path:
    """留出段定义文件路径（`FRAME_HOLDOUT_FILE` 可覆盖，测试用）。"""
    env = os.environ.get(HOLDOUT_ENV)
    return Path(env) if env else DEFAULT_HOLDOUT_FILE


def load() -> Optional[Dict[str, Any]]:
    """读留出段定义。文件不存在 / 内容为空 ⇒ `None`（= 未定义）。"""
    path = holdout_file()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    if not raw.get("start") or not raw.get("end"):
        return None
    return raw


def _symbols_of(holdout: Dict[str, Any]) -> Optional[List[str]]:
    """留出段覆盖的标的。`None` = 全池（写 `["*"]` 或干脆不写）。"""
    syms = holdout.get("symbols")
    if not syms or not isinstance(syms, list):
        return None
    syms = [str(s) for s in syms if str(s).strip()]
    if not syms or "*" in syms:
        return None
    return syms


def _to_ms(value: Any) -> Optional[int]:
    """把日期串转成毫秒时间戳。复用登记库的解析口径，避免两套日期规则。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        # 秒级时间戳（10 位）自动升到毫秒，避免 1970 年的假结论
        return v * 1000 if v < 10_000_000_000 else v
    try:
        import experiment_registry as EXP
        return EXP.parse_date_ms(str(value))
    except Exception:
        return None


def check(symbols: Sequence[str], window_start: Any, window_end: Any) -> Dict[str, Any]:
    """检查一次运行要用的窗口是否碰到留出段。

    返回 `{status, ...}`，`status` 取四个值之一：

      · `no_holdout` —— 未定义留出段，**放行**（当前项目的状态）；
      · `clear`      —— 定义了留出段，本次窗口与它不重叠，**放行**；
      · `overlaps`   —— 重叠，**应当拒绝**（除非调用方显式声明已知情）；
      · `invalid`    —— 留出段定义写坏了（日期解析不出来），**按重叠处理**，
                       因为「配置坏了就放行」等于给了一条绕过通道。

    只读：不写任何文件、不改任何状态。
    """
    hold = load()
    if hold is None:
        return {"status": STATUS_NO_HOLDOUT,
                "note": "未定义留出段（data/experiments/holdout.json 缺失或未填 start/end）"}

    hs, he = _to_ms(hold.get("start")), _to_ms(hold.get("end"))
    if hs is None or he is None:
        return {"status": STATUS_INVALID,
                "note": f"留出段日期无法解析：start={hold.get('start')!r} end={hold.get('end')!r}",
                "holdout": hold}
    if hs > he:
        return {"status": STATUS_INVALID,
                "note": "留出段起止倒置", "holdout": hold}

    ws, we = _to_ms(window_start), _to_ms(window_end)
    if ws is None or we is None:
        return {"status": STATUS_INVALID,
                "note": f"运行窗口无法解析：window_start={window_start!r} window_end={window_end!r}",
                "holdout": hold}
    if ws > we:
        return {"status": STATUS_INVALID,
                "note": "运行窗口起止倒置", "holdout": hold}

    # 半开区间比较：运行 [ws, we) 与留出 [hs, he] 重叠 ⟺ ws <= he 且 we > hs
    overlap = (ws <= he) and (we > hs)
    if not overlap:
        return {"status": STATUS_CLEAR,
                "holdout": {"start": hold.get("start"), "end": hold.get("end")},
                "window": {"start": window_start, "end": window_end}}

    allowed = _symbols_of(hold)
    hit: List[str] = []
    if allowed is None:
        hit = [str(s) for s in (symbols or [])]
    else:
        aset = {str(s) for s in allowed}
        hit = [str(s) for s in (symbols or []) if str(s) in aset]

    if not hit:
        # 窗口时间重叠，但本次跑的标的都不在留出段里 ⇒ 不算污染
        return {"status": STATUS_CLEAR,
                "note": "时间区间重叠但标的集合不相交",
                "holdout": {"start": hold.get("start"), "end": hold.get("end")}}

    return {
        "status": STATUS_OVERLAP,
        "overlap_start": max(ws, hs),
        "overlap_end": min(we, he),
        "symbols": hit,
        "holdout": {"start": hold.get("start"), "end": hold.get("end"),
                    "symbols": allowed or ["*"]},
        "window": {"start": window_start, "end": window_end},
        "note": ("本次运行的窗口与最终留出段重叠 ⇒ 这段数据会被「看过」，"
                 "之后任何实验都不得再把它当未见留出"),
    }


def describe() -> Dict[str, Any]:
    """当前留出段的状态说明（给台账工具和接口用）。"""
    hold = load()
    if hold is None:
        return {"defined": False, "file": str(holdout_file()),
                "note": "未定义留出段 ⇒ 守卫放行一切窗口（当前状态）"}
    return {
        "defined": True,
        "file": str(holdout_file()),
        "start": hold.get("start"),
        "end": hold.get("end"),
        "symbols": _symbols_of(hold) or ["*"],
        "note": hold.get("note"),
        "defined_by": hold.get("defined_by"),
        "defined_at": hold.get("defined_at"),
    }


__all__ = [
    "HOLDOUT_ENV", "DEFAULT_HOLDOUT_FILE",
    "STATUS_NO_HOLDOUT", "STATUS_CLEAR", "STATUS_OVERLAP", "STATUS_INVALID",
    "holdout_file", "load", "check", "describe",
]
