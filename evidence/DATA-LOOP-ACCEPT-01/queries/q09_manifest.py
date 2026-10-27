# -*- coding: utf-8 -*-
"""q09｜生成本轮交付清单（排除自身、逐文件 SHA-256、记录生成时刻）。

教训来源：UI-EVIDENCE-04 里旧清单把「自己」也算了进去（先建空文件再 glob），
得出 size=0 + 空哈希 e3b0c442…，且清单先于产物生成导致字节数对不上。
本脚本：先收集除 manifest 外的全部文件 → 算哈希 → 最后写 manifest → 再单独算 manifest 自身哈希。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # evidence/DATA-LOOP-ACCEPT-01
TZ = dt.timezone(dt.timedelta(hours=8))
MANIFEST = ROOT / "manifest.json"


def sha(p: Path):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    files = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_dir() or p.resolve() == MANIFEST.resolve():
            continue
        st = p.stat()
        files.append({
            "path": str(p.relative_to(ROOT)),
            "size": st.st_size,
            "sha256": sha(p),
            "mtime": dt.datetime.fromtimestamp(st.st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S"),
        })
    data = {
        "work_order": "DATA-LOOP-ACCEPT-01",
        "generated_at": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files),
        "self_excluded": True,
        "files": files,
    }
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"files={len(files)} bytes={data['total_bytes']} manifest_sha256={sha(MANIFEST)}")


if __name__ == "__main__":
    main()
