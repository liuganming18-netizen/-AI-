# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜05-B 回归脚本的**原样调用器**（只改落盘目录，不改断言）

为什么要有这一层：
  · 05-B 的 `04_配置闸门.py` / `07_经济口径一致性校验.py` 自带完整负向用例，
    但它们把结果写回 `evidence/ACCUM-EARLY-05-B/out/` 与 `runs/`；
  · 本单纪律是**只写本窗**，所以这里只做两件事：
      ① `_common.RUNS` 指到 `06-W2/02/runs/05b_regression`（闸门/账本都落本窗）；
      ② `_common.dump` 换成写本窗 `out/`；
    断言逻辑一行不改，脚本正文一行不改。
  · 每个脚本在**独立子进程**里跑（与 05-B 当时一致），避免 env 互相污染。

用法：python _regr_runner.py 04|07
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
W2B = HERE.parents[1]
PROJECT = HERE.parents[4]
B5 = PROJECT / "evidence" / "ACCUM-EARLY-05-B" / "scripts"

for p in (str(B5), str(PROJECT), str(PROJECT / "框架" / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

import _common as C5                                            # noqa: E402

C5.RUNS = W2B / "runs" / "05b_regression"
C5.OUT = W2B / "out"
OUT = W2B / "out"


def _dump(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"03_regr_{name}"
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                 encoding="utf-8")
    print(json.dumps({"written": str(p), "bytes": p.stat().st_size}, ensure_ascii=False))
    return p


C5.dump = _dump

FILES = {"04": "04_配置闸门.py", "07": "07_经济口径一致性校验.py"}


def main() -> int:
    which = sys.argv[1]
    path = B5 / FILES[which]
    spec = importlib.util.spec_from_file_location(f"regr05b_{which}", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)                 # 04 的模块级 _set_env 在这里执行

    if which == "04":
        rc = int(mod.main())
    else:
        mod.OUT_DIR = OUT / "03_regr"             # 只改落盘目录
        mod.OUT_DIR.mkdir(parents=True, exist_ok=True)
        mod.run_tests()
        rc = 0
    print(json.dumps({"script": str(path), "rc": rc}, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
