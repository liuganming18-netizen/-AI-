# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜04b 在当前代码版本上重做「离线件 ≡ 生产路径」parity。

**为什么必须重做**：06 的 `04_生产一致性.json` 是 `2026-10-25T05:33:13Z` 跑的，
而 `unified_runtime.py` 的 mtime 是 `2026-10-25T06:00:47Z`、`live_store.py` 是
`05:58:53Z` —— **都晚于那次 parity**。也就是说 06 的 03/04/05/06 四份结论
是在**上一版代码**上得到的，对当前版本没有背书力。

R1 后面所有结论都跑在 `00_common.prod_session()` 上，所以必须先在**当前版本**上
重新证明「离线件没有引入行为差异」，否则 R1 的结论同样不算数。

本脚本不重写逻辑：`importlib` 载入 06 的 `04_parity_offline_vs_prod.py`，
只改输出目录，并把两个关键文件的 SHA-256 补进结果，形成「结论绑定版本」。

用法：python code/04b_parity_recheck.py [--upto 2026-10-25T04:59:59Z]
产出：out/04b_生产一致性_当前版本.json / out/04b_生产一致性_当前版本.csv
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]

    shas = {name: cm.sha256_file(cm.BACKEND / name)
            for name in ("unified_runtime.py", "live_store.py", "paper_engine.py")}
    print("[sha] " + "  ".join(f"{k}={v[:16]}" for k, v in shas.items()))

    mod = importlib.import_module("04_parity_offline_vs_prod")
    mod.cm = cm
    sys.argv = ["04_parity_offline_vs_prod.py", "--upto", upto_s]
    rc = mod.main()

    src = OUT / "04_生产一致性.json"
    dst = OUT / "04b_生产一致性_当前版本.json"
    if src.exists():
        d = json.loads(src.read_text(encoding="utf-8"))
        d["r1_code_version"] = shas
        d["r1_why"] = ("06 的 04 跑在 05:33Z，而 unified_runtime.py 在 06:00:47Z 才定稿 ⇒ "
                       "本文件是**当前版本**上的 parity 重证。")
        dst.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str),
                       encoding="utf-8")
        src.unlink()
        print(f"[out] {dst.name}  verdict={d.get('verdict')} "
              f"n_diff_symbols={d.get('n_diff_symbols')}")
    src_csv = OUT / "04_生产一致性.csv"
    if src_csv.exists():
        shutil.move(str(src_csv), str(OUT / "04b_生产一致性_当前版本.csv"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
