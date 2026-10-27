#!/bin/sh
set -eu
ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
cd "$ROOT"
"$PY" evidence/ENTRY-VOLUME-02/verify_recalc.py 2>&1 | tee evidence/ENTRY-VOLUME-02/logs/01_independent_reaudit.log
"$PY" evidence/ENTRY-VOLUME-02/code/02_verify_source_manifests.py 2>&1 | tee evidence/ENTRY-VOLUME-02/logs/02_source_manifest_check.log
"$PY" evidence/ENTRY-VOLUME-02/code/03_write_hash_manifest.py 2>&1 | tee evidence/ENTRY-VOLUME-02/logs/03_hash_manifest.log
