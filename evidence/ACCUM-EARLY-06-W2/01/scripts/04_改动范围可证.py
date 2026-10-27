# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 04：改动范围可证（逆向重建 + sha 对撞）+ 正文数字对撞

为什么要逆向重建：本单改代码前**没有先留副本**（取证流程缺陷，已在 README 登记）。
只报「改了哪些行」是自报。这里把 06-03 的每一处编辑**逆向替换回去**，若重建结果的
sha256 等于脚本 00 记录的修前 sha，则「改动仅限这些区域」是被哈希证明的，不是声明的。

同时对撞 README：README 里出现的数字必须能在 out/*.json 里找到出处（正向），
out/*.json 里的关键值必须真的出现在 README 里（反向）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402

AEE_PATH = W.BACKEND / "accum_early_entry.py"
PRE_FIX_SHA = "d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52"

#: 06-03 的每一处编辑：pair = 精确成对替换；splice = 按首尾锚点整段替换
OLD_ON_BAR = (
    "            if ok:\n"
    "                self.sweep_low = detail[\"lowest_low\"]\n"
    "                self.sweep_ms = close_ms\n"
    "                self.sweep_conditions = conds\n"
    "                self.state = \"SWEEP\"\n"
    "                self.substage = \"await_confirm\"\n"
    "                acts.append(self._transition(\"SWEEP\", bar, \"扫低收回成立\", {\n"
    "                    \"conditions\": conds, \"detail\": detail}))")

EDITS = [
    {"kind": "pair",
     "label": "_absorption：detail 增加 bar_open_ms / bar_close_ms",
     "after": (
         '              "sell_mult": sp_mult, "require_upper_half": require_upper,\n'
         '              # 06-03：把「判的是哪一根」写进证据（调用方不再靠推断）\n'
         '              "bar_open_ms": int(cur["open_time"]),\n'
         '              "bar_close_ms": int(cur["close_time"])}\n'
         '    return ok, detail, conds'),
     "before": (
         '              "sell_mult": sp_mult, "require_upper_half": require_upper}\n'
         '    return ok, detail, conds')},
    {"kind": "pair",
     "label": "SchemeObserver.__init__：新增扫低根承接状态 4 项",
     "after": (
         '        self.sweep_low: Optional[float] = None\n'
         '        self.sweep_ms: Optional[int] = None\n'
         '        # 06-03：承接判定在**扫低发生根**求值一次，之后只读结果（不再按确认根重算）\n'
         '        self.sweep_i: Optional[int] = None\n'
         '        self.sweep_bar_ms: Optional[int] = None\n'
         '        self.sweep_baseline_window: Optional[dict] = None\n'
         '        self.sweep_absorption = None'),
     "before": (
         '        self.sweep_low: Optional[float] = None\n'
         '        self.sweep_ms: Optional[int] = None')},
    {"kind": "splice",
     "label": "on_bar：进入 SWEEP 时在扫低根求值承接并写进迁移 payload",
     "start": "            if ok:\n                # ── 06-03 时序修正",
     "end": "\n            else:\n                self._last_conditions = conds\n            return acts",
     "replacement": OLD_ON_BAR},
    {"kind": "pair",
     "label": "_confirm_actions：改为只读扫低根求值结果（不再按确认根重算）",
     "after": (
         '        # 承接：只读**扫低根**求值的那一份结果（06-03）。\n'
         '        # 绝不在这里用确认根重算——那正是修前的缺陷（突破根偷换了承接判断）。\n'
         '        if self.sweep_absorption is None:\n'
         '            return [self._close_action(\n'
         '                "DATA_INSUFFICIENT", bar,\n'
         '                "承接未在扫低根求值（内部状态缺失）⇒ 拒绝用确认根替代",\n'
         '                {"conditions": conds, "sweep_bar_i": self.sweep_i})]\n'
         '        sp_ok, sp_detail, sp_conds = self.sweep_absorption\n'
         '        sp_detail = {**sp_detail,\n'
         '                     "judged_at": "sweep_bar",\n'
         '                     "judged_bar_i": self.sweep_i,\n'
         '                     "judged_bar_close_ms": self.sweep_bar_ms,\n'
         '                     "baseline_window": self.sweep_baseline_window}\n'
         '        conds.extend(sp_conds)'),
     "before": (
         '        sp_ok, sp_detail, sp_conds = _absorption(bars_1h, i, self.cfg)\n'
         '        conds.extend(sp_conds)')},
    {"kind": "pair",
     "label": "_confirm_actions：DATA_INSUFFICIENT 原因写到「扫低根 + 缺什么」",
     "after": (
         '        if sp_ok is None:\n'
         '            # 06-03：原因写到「扫低根 + 缺什么」，不再只写一句笼统的缺字段\n'
         '            return [self._close_action(\n'
         '                "DATA_INSUFFICIENT", bar,\n'
         '                ("承接在扫低根（%s）无法判定：%s"\n'
         '                 % (_iso(self.sweep_bar_ms), sp_detail.get("reason"))),\n'
         '                {"conditions": conds, "detail": sp_detail,\n'
         '                 "sweep_bar_i": self.sweep_i,\n'
         '                 "sweep_bar_close_ms": self.sweep_bar_ms})]'),
     "before": (
         '        if sp_ok is None:\n'
         '            return [self._close_action("DATA_INSUFFICIENT", bar,\n'
         '                                       "承接证据缺字段（quote_volume / taker_buy_quote_volume）",\n'
         '                                       {"conditions": conds, "detail": sp_detail})]')},
]


def reconstruct() -> dict:
    """把编辑逆向替换回去；每处都必须命中且唯一。"""
    cur = AEE_PATH.read_text(encoding="utf-8")
    text = cur
    rows = []
    for e in EDITS:
        if e["kind"] == "pair":
            n = text.count(e["after"])
            rows.append({"edit": e["label"], "kind": "pair",
                         "after_occurrences": n, "applied": n == 1})
            if n == 1:
                text = text.replace(e["after"], e["before"])
        else:
            a = text.find(e["start"])
            b = text.find(e["end"], a + 1) if a >= 0 else -1
            ok = a >= 0 and b > a
            rows.append({"edit": e["label"], "kind": "splice",
                         "start_found": a >= 0, "end_found": b > a, "applied": ok})
            if ok:
                text = text[:a] + e["replacement"] + text[b:]
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"edits": rows, "reconstructed_sha256": sha,
            "pre_fix_sha256": PRE_FIX_SHA,
            "reconstruction_matches_pre_fix": sha == PRE_FIX_SHA,
            "reconstructed_text": text,
            "current_text": cur}


def main() -> int:
    rec = reconstruct()
    out = {"code_current": W.sha256_file(AEE_PATH),
           "pre_fix_sha256": PRE_FIX_SHA,
           "edits": rec["edits"],
           "reconstructed_sha256": rec["reconstructed_sha256"],
           "reconstruction_matches_pre_fix": rec["reconstruction_matches_pre_fix"]}

    # 真 diff（仅当重建对得上才有意义）
    if rec["reconstruction_matches_pre_fix"]:
        pre = W.OUT / "04_pre_fix_reconstructed.py"
        pre.write_text(rec["reconstructed_text"], encoding="utf-8")
        diff = "".join(difflib.unified_diff(
            rec["reconstructed_text"].splitlines(keepends=True),
            rec["current_text"].splitlines(keepends=True),
            fromfile="accum_early_entry.py@修前(d96ad60e)", tofile="accum_early_entry.py@修后",
            n=3))
        (W.OUT / "04_code_diff.patch").write_text(diff, encoding="utf-8")
        changed = sum(1 for l in diff.splitlines()
                      if (l.startswith("+") or l.startswith("-"))
                      and not l.startswith("+++") and not l.startswith("---"))
        out["diff"] = {"patch": str(W.OUT / "04_code_diff.patch"),
                       "changed_lines": changed,
                       "added": sum(1 for l in diff.splitlines()
                                    if l.startswith("+") and not l.startswith("+++")),
                       "removed": sum(1 for l in diff.splitlines()
                                      if l.startswith("-") and not l.startswith("---"))}
    else:
        out["diff"] = None

    W.dump("04_code_change_proof.json", out)
    print("reconstruction_matches_pre_fix =", out["reconstruction_matches_pre_fix"])
    print("edits:", json.dumps(out["edits"], ensure_ascii=False))
    if out["diff"]:
        print("diff:", json.dumps(out["diff"], ensure_ascii=False))
    return 0 if out["reconstruction_matches_pre_fix"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
