# -*- coding: utf-8 -*-
"""01 测试真实性核对（AUDIT-05 第一段「先核对测试真实性」）。

工单四条判据，逐条用 AST/字节级证据回答，不采信任何文字声明：
  A. 参数来自冻结配置，挂单和持仓保留配置身份。
  B. 行情由服务端从隔离数据源读取。
  C. 测试脚本没有逐周期调用执行方法、直接记止损历史或手工写成交。
  D. 加速回放允许，但必须由隔离运行器自行推进；标为工程测试，不计入策略成绩。

做法：
  · 对被审测试脚本做 **AST 解析**，定位每一处 `execute_cycle` / `record_stop_update` /
    裸 SQL 写入 / 请求体里出现 `stoploss_s`、`trail_d`、`candle_4h`、`bar_15m` 的位置（带行号）；
  · 对账本 schema 做字段检查：挂单/持仓表里有没有「配置身份」列；
  · 对 `live_api.py` 做静态检查：行情与参数是不是从请求体取。
产物：out/01_测试真实性.json
"""
from __future__ import annotations

import ast
import datetime as dt
import io
import json
import pathlib
import re
import sqlite3
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
THEIR = BASE / "evidence/SYSTEM-CLOSELOOP-04"
BACKEND = BASE / "框架/backend"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def calls_in(path: pathlib.Path, names: tuple[str, ...]) -> list[dict]:
    """AST：找出对指定函数名的调用（含属性调用 obj.name）。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = None
            if isinstance(fn, ast.Attribute):
                fname = fn.attr
            elif isinstance(fn, ast.Name):
                fname = fn.id
            if fname in names:
                kws = {k.arg: ast.unparse(k.value)[:90] for k in node.keywords if k.arg}
                args = [ast.unparse(a)[:70] for a in node.args]
                out.append({"行": node.lineno, "函数": fname, "位置参数": args, "关键字": kws})
    return sorted(out, key=lambda x: x["行"])


def payload_keys(path: pathlib.Path, keys: tuple[str, ...]) -> list[dict]:
    """AST：找出以字面量键出现的 payload 字典（请求体），报告命中的键与行号。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            ks = []
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    ks.append(k.value)
            hit = [k for k in ks if k in keys]
            if hit:
                out.append({"行": node.lineno, "命中键": hit,
                            "全部键": ks[:12]})
    return sorted(out, key=lambda x: x["行"])


def sql_writes(path: pathlib.Path) -> list[dict]:
    """找裸 SQL 写入（INSERT/UPDATE/DELETE）目标表。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r"(INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
                         src, re.I):
        line = src[:m.start()].count("\n") + 1
        out.append({"行": line, "动作": m.group(1).upper(), "表": m.group(2)})
    return out


def schema_cols(db: pathlib.Path, table: str) -> list[str]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def main() -> None:
    tests = [p for p in sorted(THEIR.glob("*.py")) if p.name.startswith(("test_", "reconcile_"))]
    res: dict = {"生成时刻": now(), "被审测试脚本": [p.name for p in tests]}

    # ── C：逐周期调用执行方法 / 直接记止损历史 / 手工写成交 ──
    c_detail = {}
    for p in tests:
        c_detail[p.name] = {
            "execute_cycle_调用": calls_in(p, ("execute_cycle",)),
            "record_stop_update_调用": calls_in(p, ("record_stop_update",)),
            "裸SQL写入": sql_writes(p),
        }
    res["C_测试脚本是否自己推进"] = c_detail
    total_ec = sum(len(v["execute_cycle_调用"]) for v in c_detail.values())
    total_rst = sum(len(v["record_stop_update_调用"]) for v in c_detail.values())
    fill_inserts = [w for v in c_detail.values() for w in v["裸SQL写入"]
                    if w["表"] in ("paper_fill", "paper_position", "paper_order", "paper_stop_history")]
    res["C_汇总"] = {
        "execute_cycle_调用总次数": total_ec,
        "record_stop_update_调用总次数": total_rst,
        "对账本四表的裸SQL写入": fill_inserts,
        "判定": ("不通过" if (total_ec > 0 or total_rst > 0 or fill_inserts) else "通过"),
        "说明": "只要测试脚本自己调用执行方法/自己记止损/自己写成交，就不是「系统自动完成」",
    }

    # ── A/D：参数与时钟是否由测试给 ──
    a_detail = {}
    for p in tests:
        a_detail[p.name] = {
            "请求体出现参数键": payload_keys(p, ("stoploss_s", "trail_d")),
            "execute_cycle传参": [c["关键字"] for c in calls_in(p, ("execute_cycle",))],
            "now_ms字面注入": len(re.findall(r"now_ms\s*=", p.read_text(encoding="utf-8", errors="replace"))),
        }
    res["A_参数来源"] = a_detail

    # 账本是否保留「配置身份」列
    paper_dbs = sorted((BASE / "数据/paper").glob("*.sqlite3")) if (BASE / "数据/paper").exists() else []
    iso = sorted(pathlib.Path("/tmp/wx_audit05/paper").glob("*.sqlite3"))
    ident = {}
    for db in paper_dbs + iso:
        tag = db.name if db.parent.name == "paper" and str(db.parent).startswith("/tmp") is False else f"隔离/{db.name}"
        ident[tag] = {
            "paper_pending": schema_cols(db, "paper_pending"),
            "paper_position": schema_cols(db, "paper_position"),
        }
    res["A_账本配置身份列"] = ident
    res["A_账本配置身份列_说明"] = ("生产库（数据/paper）是旧 schema，尚未迁移；隔离新库（/tmp/wx_audit05/paper）"
                              "由新版 live_store.ensure_live_schema 建表，含 stoploss_s/trail_d/config_fingerprint")
    ident_cols = set()
    for v in ident.values():
        for cols in v.values():
            ident_cols |= {c for c in cols
                           if any(k in c.lower() for k in ("config", "param", "sha", "source", "rule", "view"))}
    res["A_汇总"] = {
        "请求体是否携带 stoploss_s/trail_d": any(v["请求体出现参数键"] for v in a_detail.values()),
        "账本疑似身份列": sorted(ident_cols),
        "判定": "不通过" if any(v["请求体出现参数键"] for v in a_detail.values()) else "需人工判读",
        "说明": "参数若由请求体给出，则「来自冻结配置」不成立；账本若无配置身份列，则「保留配置身份」不成立",
    }

    # ── B：行情来源（静态） ──
    la = (BACKEND / "live_api.py").read_text(encoding="utf-8", errors="replace").split("\n")
    body_candle = [(i, l.strip()[:150]) for i, l in enumerate(la, 1)
                   if re.search(r'(payload|body|req)\.get\(\s*"(candle_4h|bar_15m|candle_15m|now_ms|trail_d)"', l)]
    adv = [(i, l.strip()[:150]) for i, l in enumerate(la, 1)
           if "advance_position_trailing" in l or "advance_pending" in l]
    res["B_行情来源"] = {
        "live_api_请求体取行情或参数": [{"行": i, "文本": t} for i, t in body_candle],
        "live_api_推进调用点": [{"行": i, "文本": t} for i, t in adv],
        "判定": "不通过" if any(re.search(r'"(candle_4h|bar_15m|candle_15m)"', t) for _, t in body_candle) else "通过",
        "说明": "行情若从请求体取，就不是「服务端从隔离数据源读取」",
    }

    # ── D：是否标注为工程测试 ──
    label = {}
    for p in tests:
        s = p.read_text(encoding="utf-8", errors="replace")
        label[p.name] = {
            "出现'工程测试'": "工程测试" in s,
            "出现'策略成绩/收益'字样": any(k in s for k in ("策略成绩", "收益", "收益率")),
        }
    res["D_工程测试标注"] = label

    # ── 总判定 ──
    res["总判定"] = {
        "A_参数来自冻结配置": res["A_汇总"]["判定"],
        "B_行情服务端读取": res["B_行情来源"]["判定"],
        "C_测试未自行推进": res["C_汇总"]["判定"],
        "D_隔离运行器自行推进": "不通过" if total_ec > 0 else "未验证",
        "结论": ("被审测试不满足工单「测试真实性」四条：它自己给参数、自己给时钟、自己逐周期调执行方法"
                 if (total_ec > 0 or res["A_汇总"]["判定"] == "不通过") else "四条均满足"),
    }

    (OUT / "01_测试真实性.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")

    print("=== C 测试脚本是否自己推进 ===")
    print(f"  execute_cycle 调用 {total_ec} 次 / record_stop_update 调用 {total_rst} 次 / 账本四表裸写 {len(fill_inserts)} 处")
    for c in res["C_汇总"]["判定"], :
        print("  判定:", c)
    print("=== A 参数来源 ===")
    print("  请求体携带 stoploss_s/trail_d:", res["A_汇总"]["请求体是否携带 stoploss_s/trail_d"])
    print("  账本疑似身份列:", res["A_汇总"]["账本疑似身份列"] or "无")
    print("=== B 行情来源 ===")
    for x in res["B_行情来源"]["live_api_请求体取行情或参数"][:8]:
        print(f"  L{x['行']}: {x['文本']}")
    print("  判定:", res["B_行情来源"]["判定"])
    print("=== 总判定 ===")
    print(" ", res["总判定"]["结论"])


if __name__ == "__main__":
    main()
