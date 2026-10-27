# -*- coding: utf-8 -*-
"""FT-VALIDATION-02 公共只读工具（交付判据与验证启动审核）。"""
from __future__ import annotations

import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E07 = os.path.join(ROOT, "evidence/FT-ENTRY-07")
E08 = os.path.join(ROOT, "evidence/FT-ENTRY-08")
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
V01 = os.path.join(ROOT, "evidence/FT-VALIDATION-01")
V02 = os.path.join(ROOT, "evidence/FT-VALIDATION-02")
VDATA = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01")
OUT = os.path.join(V02, "out")
LOGS = os.path.join(V02, "logs")


def sha256_file(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(df, name):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path
