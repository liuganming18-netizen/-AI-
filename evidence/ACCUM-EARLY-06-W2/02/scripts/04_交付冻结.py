# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 04：冻结交付（给窗口五、窗口六）

派工要求「交付冻结代码哈希、测试配置、差异说明和调用接口给窗口五、六」，
所以这里只做四件可核对的事，不做结论：

  D1 身份：修前/修后/测试配置的 sha256、字节数、mtime
  D2 差异说明：`difflib` 逐行 unified diff 落 `out/04_code_diff.patch`，
     并按「顶层定义」用 AST 比出哪些函数/类真的变了（不是靠肉眼看 diff）
  D3 调用接口：`scan_symbol` / `SchemeObserver` / `on_bar` / `open_store` /
     `load_config` / `add_gap_once` 的签名（修前 vs 修后），以及**新增的 payload 键**
  D4 下游注意：给窗口五（06-09/06-10）与窗口六（06-12）的**接口变更清单**

**不宣称**：本脚本不判断策略好坏、不产出绩效、不声明自然成交。
"""
from __future__ import annotations

import ast
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402

#: 本单新增的 payload 键，**并标出它该出现在哪个函数的函数体里**
#: （只看全文件字面量会误判：`bar_open_ms` 在修前别处就有，但在 `_absorption` 里没有）
NEW_PAYLOAD_KEYS = [
    ("sweep_bar_i", "SchemeObserver.on_bar"),
    ("sweep_scope_mode", "SchemeObserver.on_bar"),
    ("multi_bar_scope_unverified", "SchemeObserver.on_bar"),
    ("absorption_bar_close_ms", "SchemeObserver.on_bar"),
    ("absorption_baseline_window", "SchemeObserver.on_bar"),
    ("bar_open_ms", "_absorption"),
    ("bar_close_ms", "_absorption"),
]

#: 交付给下游的接口名
IFACE = ["load_config", "open_store", "scan_symbol", "add_gap_once",
         "record_candidate", "try_open", "drive_all_exits"]


def _sigs(path: Path) -> dict:
    """顶层函数/类方法的签名（只看结构，不看函数体）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}

    def sig(fn: ast.AST) -> str:
        a = fn.args
        parts = [x.arg for x in a.posonlyargs] + [x.arg for x in a.args]
        if a.vararg:
            parts.append("*" + a.vararg.arg)
        elif a.kwonlyargs:
            parts.append("*")
        parts += [x.arg for x in a.kwonlyargs]
        if a.kwarg:
            parts.append("**" + a.kwarg.arg)
        return "(" + ", ".join(parts) + ")"

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = sig(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = sig(sub)
    return out


def identity() -> dict:
    pre = W.sha256_file(W.PRE_FIX_PATH)
    post = W.sha256_file(W.POST_FIX_PATH)
    cfg = W.sha256_file(W.CONFIG_PATH)
    for d, p in ((pre, W.PRE_FIX_PATH), (post, W.POST_FIX_PATH), (cfg, W.CONFIG_PATH)):
        d["mtime_utc"] = W.iso(int(p.stat().st_mtime * 1000))
        d["rel"] = str(p.relative_to(W.PROJECT))
    return {"pre_fix": pre, "post_fix": post, "test_config": cfg,
            "pre_fix_expected": W.PRE_FIX_SHA_EXPECT,
            "post_fix_expected": W.POST_FIX_SHA_EXPECT,
            "config_expected": W.CONFIG_SHA_EXPECT,
            "all_match_expectation": (pre["sha256"] == W.PRE_FIX_SHA_EXPECT
                                      and post["sha256"] == W.POST_FIX_SHA_EXPECT
                                      and cfg["sha256"] == W.CONFIG_SHA_EXPECT),
            "third_party_reference": {
                "version": W.VERSION_05E_R1,
                "note": ("05-E-R1 的 7 事件结论产生在这个版本上，与本单修前/修后**都不同**；"
                         "本单没有该版本的文件，因此只作对照列，不重跑、不混作同输入对照。")}}


def diff_doc() -> dict:
    a = W.PRE_FIX_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    b = W.POST_FIX_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = list(difflib.unified_diff(a, b,
                                      fromfile="accum_early_entry.py@d96ad60e（修前）",
                                      tofile="accum_early_entry.py@3c570edc（修后）",
                                      n=3))
    p = W.OUT / "04_code_diff.patch"
    p.write_text("".join(lines), encoding="utf-8")
    plus = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    hunks = sum(1 for l in lines if l.startswith("@@"))
    return {"patch": str(p), "patch_bytes": p.stat().st_size,
            "lines_added": plus, "lines_removed": minus, "hunks": hunks,
            "net_lines": plus - minus}


def ast_compare() -> dict:
    pre, post = _sigs(W.PRE_FIX_PATH), _sigs(W.POST_FIX_PATH)
    added = sorted(set(post) - set(pre))
    removed = sorted(set(pre) - set(post))
    changed = sorted(k for k in set(pre) & set(post) if pre[k] != post[k])
    return {"n_pre": len(pre), "n_post": len(post),
            "added": added, "removed": removed, "signature_changed": changed,
            "iface_before": {k: pre.get(k) for k in IFACE},
            "iface_after": {k: post.get(k) for k in IFACE},
            "iface_unchanged": all(pre.get(k) == post.get(k) for k in IFACE)}


def _func_src(path: Path, dotted: str) -> str:
    """取某个函数（支持 `Class.method`）的函数体源码切片。"""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    parts = dotted.split(".")
    node = None
    for cur in tree.body:
        if getattr(cur, "name", None) == parts[0]:
            node = cur
            break
    for name in parts[1:]:
        if node is None:
            break
        node = next((c for c in getattr(node, "body", [])
                     if getattr(c, "name", None) == name), None)
    if node is None:
        return ""
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def payload_keys() -> dict:
    rows = []
    for k, scope in NEW_PAYLOAD_KEYS:
        pre_body = _func_src(W.PRE_FIX_PATH, scope)
        post_body = _func_src(W.POST_FIX_PATH, scope)
        rows.append({"key": k, "scope": scope,
                     "in_pre_scope": k in pre_body, "in_post_scope": k in post_body,
                     "is_new": (k not in pre_body) and (k in post_body),
                     "in_pre_file_anywhere": k in W.PRE_FIX_PATH.read_text(encoding="utf-8")})
    return {"keys": rows,
            "all_new": all(r["is_new"] for r in rows),
            "note": ("判据是「该键在**指定函数体**里修前不存在、修后存在」。"
                     "只按全文件字面量判会误报：`bar_open_ms` 修前别处已有，"
                     "但在 `_absorption` 的 detail 里没有。")}


def downstream() -> dict:
    return {
        "window5_06_09_06_10": {
            "must_know": [
                "SWEEP 迁移 payload 新增 6 个键：sweep_bar_i / sweep_scope_mode / "
                "multi_bar_scope_unverified / absorption_bar_close_ms / "
                "absorption_baseline_window / absorption{ok,detail,conditions}",
                "`_absorption` 的 detail 新增 bar_open_ms / bar_close_ms（承接判定用的 bar 时刻）",
                "承接**只在扫低根求值一次**，确认根不再重算；`_confirm_actions` 缺 "
                "`self.sweep_absorption` 时直接 DATA_INSUFFICIENT（拒绝回退确认根）",
                "DATA_INSUFFICIENT 的 reason 文案变成「承接在扫低根（<ISO时刻>）无法判定：<原因>」，"
                "断言 reason 全文的用例要改成匹配前缀",
            ],
            "reusable_evidence": [
                "out/01_pre_run.json / 01_post_run.json（两版本全量轨迹）",
                "out/02_event_compare.json（逐事件对比 + NOT_FOUND 归因 + 候选机制）",
                "out/03_protection_regression.json（闸门/口径/去重/写域）",
                "out/04_code_diff.patch（修前→修后 unified diff）",
                "runs/pre_fix_accum_early_entry.py（修前代码副本，可独立 import）",
            ],
            "cautions": [
                "原 7 事件在两版本上**逐字段一致**，拿它们当「修好了」的证据是无效的；"
                "差异只出现在原清单之外的箱体（见 02 的 B 段）。",
                "05-E-R1 的结论产生在 f7056980 上，与本单两个版本都不同，不能混作同输入对照。",
            ],
        },
        "window6_06_12": {
            "must_know": [
                "修后代码身份：3c570edc…（%d 字节）；修前：d96ad60e…（%d 字节）"
                % (W.sha256_file(W.POST_FIX_PATH)["bytes"],
                   W.sha256_file(W.PRE_FIX_PATH)["bytes"]),
                "测试配置身份：f322a940…（与 05-B fixture 逐字节相同，未改一个字节）",
                "本单未改：paper_engine / observe_store / research_15m / live_store / "
                "replay_calc / 冻结包 / 采集器 / 前端 / 配置模板（mtime 全部早于本日 00:00Z）",
                "本单未开仓：`open_candidates=False`，账本 0 笔 ⇒ 不得据此宣称自然成交或绩效",
            ],
            "handover_files": [
                "out/04_handoff.json（本文件）",
                "out/04_code_diff.patch",
                "runs/pre_fix_accum_early_entry.py",
                "config/fixture_rules_05.json",
            ],
        },
        "do_not_claim": [
            "不宣称自然成交", "不宣称策略有效", "不宣称开仓闸门已放行",
            "不把 NOT_FOUND 写成 INVALIDATED",
        ],
    }


def main() -> int:
    out = {"generated_utc": W.iso(int(__import__("time").time() * 1000)),
           "D1_identity": identity(),
           "D2_diff": diff_doc(),
           "D3_interface": {"ast": ast_compare(), "payload_keys": payload_keys()},
           "D4_downstream": downstream()}
    out["summary"] = {
        "identity_ok": out["D1_identity"]["all_match_expectation"],
        "lines_added": out["D2_diff"]["lines_added"],
        "lines_removed": out["D2_diff"]["lines_removed"],
        "hunks": out["D2_diff"]["hunks"],
        "ast_added": out["D3_interface"]["ast"]["added"],
        "ast_removed": out["D3_interface"]["ast"]["removed"],
        "ast_sig_changed": out["D3_interface"]["ast"]["signature_changed"],
        "iface_unchanged": out["D3_interface"]["ast"]["iface_unchanged"],
        "payload_keys_all_new": out["D3_interface"]["payload_keys"]["all_new"],
    }
    W.dump("04_handoff.json", out)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    print("post sha:", out["D1_identity"]["post_fix"]["sha256"])
    print("patch   :", out["D2_diff"]["patch"], out["D2_diff"]["patch_bytes"], "bytes")
    return 0 if out["summary"]["identity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
