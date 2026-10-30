# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 任务 A 历史数据路径回归测试。

工单要求覆盖五类场景，本文件逐一对应，且**全部打在真实入口上**：

  T01 默认路径兼容     —— 不设任何环境变量时，三条路径与旧版逐字一致
  T02 不依赖 cwd       —— chdir 到任意目录，解析结果仍跟着代码走
  T03 合法外部路径     —— 环境变量指向外部只读历史库/快照，预检 ok
  T04 缺失路径         —— 明确报错（FileNotFoundError + 可定位信息），不静默回退
  T05 非法路径         —— 目录冒充数据库 / 非 sqlite 文件 ⇒ 记录错误，不换源
  T06 隔离工作树       —— 裸树（无 `数据/`）下三条路径全部显式暴露，不假装「没信号」

被真正调用的是：
  · `oi_feed.default_hot_db / resolve_hot_db / probe_hot_db / _connect`
  · `instrument_spec.SpecBook / get_spec_book`
  · `EventTrendRunner.__init__ / data_paths_report`

不测「没人调用的工具函数」。本机无 pytest ⇒ 用标准库 unittest，零额外依赖。
运行：`/usr/bin/python3 -m unittest 策略.WyckoffMaster.event_trend_v0_1.tests.test_backtest_repair_01_paths -v`
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# 工程根（本文件在 策略/WyckoffMaster/event_trend_v0_1/tests/ 下 ⇒ parents[4]）
PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "策略"))
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

from WyckoffMaster.event_trend_v0_1 import instrument_spec as IS          # noqa: E402
from WyckoffMaster.event_trend_v0_1.runner import (                       # noqa: E402
    EventTrendRunner, _default_base_dir, _default_parquet_base, _default_runs_base)

import oi_feed as OF                                                      # noqa: E402

# 真实历史数据落点（只读；不存在时相关用例 skip，不伪造）
REAL_MARKET_DB = PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
REAL_ARCHIVE = PROJECT_ROOT / "数据" / "市场数据" / "parquet"
REAL_CONTRACTS = PROJECT_ROOT / "数据" / "contracts" / "旧标的池"

# skip 判据在**导入时**求值，早于 setUp 清环境 ⇒ 这里要同时认环境变量
_REAL_DB = Path(os.environ.get("MARKET_DB") or REAL_MARKET_DB)
_REAL_ARCHIVE = Path(os.environ.get("MARKET_ARCHIVE") or REAL_ARCHIVE)
_REAL_CONTRACTS = Path(os.environ.get("CONTRACTS_DIR") or REAL_CONTRACTS)

_ENV_KEYS = ("MARKET_DB", "MARKET_ARCHIVE", "CONTRACTS_DIR",
             "WX_RUNS_BASE", "WX_PROJECT_ROOT", "DATA_HUB_PROJECT")


def _make_sqlite_with_oi(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE oi_history (ts INTEGER PRIMARY KEY, "
                    "open_interest REAL, source TEXT, fetched_at INTEGER)")
        con.execute("INSERT INTO oi_history VALUES (1700000000000, 1.0, 't', NULL)")
        con.commit()
    finally:
        con.close()
    return path


class _Base(unittest.TestCase):
    """每个用例都从「干净环境 + 空规格簿缓存」出发。"""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        IS._BOOK_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory()
        # macOS 的 /var 是指向 /private/var 的符号链接；临时目录一律按 resolve 后比较
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        IS._BOOK_CACHE.clear()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def setenv(self, **kv):
        for k, v in kv.items():
            os.environ[k] = str(v)

    def simulate_bare_tree(self):
        """把「按模块位置推导出来的默认路径」也搬进裸树。

        真实隔离工作树里 `oi_feed.__file__` 与 `instrument_spec.__file__` 都落在
        裸树上，所以热库、合约目录天然指向「裸树/数据/…」。本文件是在**完整树**
        里跑的，模块仍留在完整树上 —— 若只设 `DATA_HUB_PROJECT`，热库与合约目录
        会照旧指向完整树（本机真实存在），用例就会假失败。

        这里只把这两个**默认值**改到裸树，不改生产路径解析逻辑，也不改任何
        环境变量口径；用完由 addCleanup 还原。
        """
        bare_hot = self.tmp / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
        bare_contracts = self.tmp / "数据" / "contracts" / "旧标的池"
        self.addCleanup(setattr, OF, "default_hot_db", OF.default_hot_db)
        self.addCleanup(setattr, IS, "DEFAULT_CONTRACTS_DIR", IS.DEFAULT_CONTRACTS_DIR)
        OF.default_hot_db = lambda: bare_hot
        IS.DEFAULT_CONTRACTS_DIR = bare_contracts


# ══════════════════════════ T01 默认路径兼容 ══════════════════════════

class TestT01DefaultCompatibility(_Base):
    """不设环境变量时，路径必须与旧版完全一致（生产默认行为不得被改坏）。"""

    def test_base_dir_follows_code_not_cwd(self):
        self.assertEqual(_default_base_dir(), str(PROJECT_ROOT))

    def test_parquet_and_runs_under_project_root(self):
        base = _default_base_dir()
        self.assertEqual(_default_parquet_base(base),
                         os.path.join(base, "数据", "市场数据", "parquet"))
        self.assertEqual(_default_runs_base(base),
                         os.path.join(base, "回测日志", "event_trend_v0_1", "runs"))

    def test_oi_feed_default_hot_db_matches_legacy(self):
        """`oi_feed.default_hot_db()` 缺省值 = 旧版 `parents[2]/数据/.../market_v2.sqlite3`。"""
        legacy = (Path(OF.__file__).resolve().parents[2]
                  / "数据" / "市场数据" / "db" / "market_v2.sqlite3")
        self.assertEqual(OF.default_hot_db(), legacy)

    def test_contracts_default_dir_unchanged(self):
        """默认合约快照目录仍是 `数据/contracts/旧标的池`（未因本次改动漂移）。"""
        self.assertEqual(IS.DEFAULT_CONTRACTS_DIR,
                         PROJECT_ROOT / "数据" / "contracts" / "旧标的池")

    def test_runner_default_paths_equal_legacy_join(self):
        """`EventTrendRunner()` 无参构造 ⇒ 与旧版 `os.path.join(base_dir, ...)` 等价。"""
        r = EventTrendRunner()
        self.assertEqual(r.base_dir, str(PROJECT_ROOT))
        self.assertEqual(r.parquet_base,
                         str(PROJECT_ROOT / "数据" / "市场数据" / "parquet"))

    def test_default_spec_book_missing_is_not_fatal(self):
        """**生产兼容关键点**：默认路径缺失时不得抛错（旧行为），但要留 `missing_reason`。

        `get_spec_book` 内部会 `SpecBook(Path(key))`，若不区分「显式指定」，
        默认路径缺失就会抛错 —— 这里就是防那条路被走回去。
        """
        ghost = self.tmp / "no_such_contracts"
        old = IS.DEFAULT_CONTRACTS_DIR
        try:
            IS.DEFAULT_CONTRACTS_DIR = ghost
            IS._BOOK_CACHE.clear()
            book = IS.get_spec_book()                  # 不传参、不设环境变量
        finally:
            IS.DEFAULT_CONTRACTS_DIR = old
            IS._BOOK_CACHE.clear()
        self.assertIsNotNone(book.missing_reason)
        self.assertIn("contracts_dir_missing", book.missing_reason)
        self.assertEqual(book._snapshots, [])          # 空簿，但不抛


# ══════════════════════════ T02 不依赖 cwd ══════════════════════════

class TestT02CwdIndependence(_Base):

    def test_chdir_elsewhere_keeps_project_root(self):
        old = os.getcwd()
        try:
            os.chdir(self.tmp)
            self.assertEqual(Path.cwd(), self.tmp)
            self.assertEqual(_default_base_dir(), str(PROJECT_ROOT))
            self.assertEqual(OF.default_hot_db(),
                             Path(OF.__file__).resolve().parents[2]
                             / "数据" / "市场数据" / "db" / "market_v2.sqlite3")
        finally:
            os.chdir(old)

    def test_chdir_to_root_slash(self):
        old = os.getcwd()
        try:
            os.chdir("/")
            self.assertEqual(_default_base_dir(), str(PROJECT_ROOT))
        finally:
            os.chdir(old)

    def test_relative_env_is_absolutised(self):
        """环境变量给相对路径时，必须基于**当前工作目录**展开成绝对路径。"""
        target = self.tmp / "rel_root"
        target.mkdir()
        old = os.getcwd()
        try:
            os.chdir(self.tmp)
            self.setenv(DATA_HUB_PROJECT="rel_root")
            self.assertEqual(_default_base_dir(), str(target.resolve()))
        finally:
            os.chdir(old)

    def test_tilde_env_is_expanded(self):
        self.setenv(DATA_HUB_PROJECT="~/wx_root_probe")
        got = _default_base_dir()
        self.assertNotIn("~", got)
        self.assertTrue(Path(got).is_absolute())


# ══════════════════════════ T03 合法外部路径 ══════════════════════════

class TestT03ExternalPathsOk(_Base):
    """合成环境（永远可跑）：三条路径都指到临时目录 ⇒ 预检全绿。"""

    def _build_ext(self):
        ext = self.tmp / "ext"
        ext.mkdir()
        db = _make_sqlite_with_oi(ext / "hist.sqlite3")
        arch = ext / "parquet"
        (arch / "candles" / "binance" / "um" / "BTCUSDT" / "4h").mkdir(parents=True)
        (arch / "oi" / "binance" / "um" / "BTCUSDT").mkdir(parents=True)
        contracts = ext / "contracts"
        contracts.mkdir()
        return db, arch, contracts, self.tmp / "runs"

    def test_synthetic_external_env_all_green(self):
        db, arch, contracts, runs = self._build_ext()
        self.setenv(MARKET_DB=db, MARKET_ARCHIVE=arch,
                    CONTRACTS_DIR=contracts, WX_RUNS_BASE=runs)
        r = EventTrendRunner()
        rep = r.data_paths_report()
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertEqual(rep["problems"], [])
        self.assertEqual(rep["parquet"]["source"], "env:MARKET_ARCHIVE")
        self.assertEqual(rep["parquet"]["symbol_dirs"], 1)
        self.assertEqual(rep["parquet"]["oi_symbol_dirs"], 1)
        self.assertEqual(rep["market_db"]["source"], "env:MARKET_DB")
        self.assertTrue(rep["market_db"]["readable"])
        self.assertTrue(rep["market_db"]["has_oi_history"])
        self.assertEqual(rep["contracts"]["source"], "env:CONTRACTS_DIR")
        self.assertTrue(rep["contracts"]["exists"])
        self.assertEqual(r.runs_base, str(runs.resolve()))

    def test_spec_book_reads_external_dir(self):
        contracts = self.tmp / "contracts"
        contracts.mkdir()
        # 注意：`_load` 会跳过 `symbols` 为空的快照（那是「抓到了但没内容」），
        # 所以 fixture 必须带一个真 symbol，否则本用例测不到「读进来了」。
        (contracts / "exchangeInfo_futures_2026-10-05.json").write_text(
            json.dumps({"serverTime": 1759622400000, "symbols": [
                {"symbol": "BTCUSDT", "pricePrecision": 2, "quantityPrecision": 3,
                 "filters": []}]}), encoding="utf-8")
        self.setenv(CONTRACTS_DIR=contracts)
        book = IS.get_spec_book()
        self.assertEqual(book.dir, contracts)
        self.assertTrue(book.strict)                   # 环境变量 ⇒ 显式指定
        self.assertIsNone(book.missing_reason)
        self.assertEqual(len(book._snapshots), 1)
        self.assertIn("BTCUSDT", book._snapshots[0].symbols)

    @unittest.skipUnless(_REAL_DB.exists(),
                         "本机无真实热库（隔离工作树），跳过真实数据用例")
    def test_real_readonly_paths(self):
        self.setenv(MARKET_DB=_REAL_DB, MARKET_ARCHIVE=_REAL_ARCHIVE,
                    CONTRACTS_DIR=_REAL_CONTRACTS)
        rep = EventTrendRunner().data_paths_report()
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertTrue(rep["market_db"]["has_oi_history"])
        self.assertGreater(rep["parquet"]["symbol_dirs"], 0)
        self.assertGreaterEqual(rep["contracts"]["snapshots"], 1)

    def test_readonly_connection_does_not_mutate(self):
        """`_connect` 必须是只读连接：写操作应被 sqlite 拒绝。"""
        db = _make_sqlite_with_oi(self.tmp / "hist.sqlite3")
        self.setenv(MARKET_DB=db)
        before = db.stat().st_size
        con = OF._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO oi_history VALUES (1,2,'x',NULL)")
        finally:
            con.close()
        self.assertEqual(db.stat().st_size, before)


# ══════════════════════════ T04 缺失路径必须明确报错 ══════════════════════════

class TestT04MissingPathsFailLoud(_Base):

    def test_missing_market_db_probe_reports(self):
        ghost = self.tmp / "no" / "such" / "market.sqlite3"
        self.setenv(MARKET_DB=ghost)
        probe = OF.probe_hot_db()
        self.assertFalse(probe["exists"])
        self.assertEqual(probe["error"], "hot_db_missing")
        self.assertEqual(probe["source"], "env:MARKET_DB")
        self.assertEqual(probe["path"], str(ghost.resolve()))

    def test_missing_market_db_connect_raises_filenotfound(self):
        ghost = self.tmp / "no" / "such" / "market.sqlite3"
        self.setenv(MARKET_DB=ghost)
        with self.assertRaises(FileNotFoundError) as ei:
            OF._connect()
        msg = str(ei.exception)
        self.assertIn("hot_db_missing", msg)
        self.assertIn("MARKET_DB", msg)                # 提示怎么修

    def test_missing_market_db_does_not_fall_back(self):
        """**不许静默换源**：环境变量指错了就报错，不能偷偷去读工程默认库。"""
        ghost = self.tmp / "ghost.sqlite3"
        self.setenv(MARKET_DB=ghost)
        self.assertEqual(OF.resolve_hot_db(), ghost.resolve())

    def test_missing_contracts_dir_explicit_raises(self):
        self.setenv(CONTRACTS_DIR=self.tmp / "no_such_contracts")
        with self.assertRaises(FileNotFoundError) as ei:
            IS.get_spec_book()
        msg = str(ei.exception)
        self.assertIn("contracts_dir_missing", msg)
        self.assertIn("CONTRACTS_DIR", msg)

    def test_missing_contracts_dir_explicit_arg_raises(self):
        with self.assertRaises(FileNotFoundError) as ei:
            IS.SpecBook(self.tmp / "nope")
        self.assertIn("contracts_dir_missing", str(ei.exception))

    def test_missing_contracts_dir_non_strict_records_reason(self):
        """非严格模式（默认路径）不抛错，但必须留下可读的 `missing_reason`。"""
        book = IS.SpecBook(self.tmp / "nope", strict=False)
        self.assertIn("contracts_dir_missing", book.missing_reason or "")
        self.assertEqual(book._snapshots, [])

    def test_runner_report_lists_all_missing(self):
        self.setenv(DATA_HUB_PROJECT=self.tmp,
                    MARKET_DB=self.tmp / "m.sqlite3",
                    MARKET_ARCHIVE=self.tmp / "parquet",
                    CONTRACTS_DIR=self.tmp / "contracts",
                    WX_RUNS_BASE=self.tmp / "runs")
        rep = EventTrendRunner().data_paths_report()
        self.assertFalse(rep["ok"])
        joined = " | ".join(rep["problems"])
        self.assertIn("hot_db_missing", joined)
        self.assertIn("parquet_archive_missing", joined)
        self.assertIn("contracts_dir_missing", joined)


# ══════════════════════════ T05 非法路径 ══════════════════════════

class TestT05IllegalPaths(_Base):

    def test_directory_as_market_db(self):
        """目录冒充数据库：`exists()` 为真，但打不开 ⇒ 必须报错而不是当空库。"""
        d = self.tmp / "iam_a_dir"
        d.mkdir()
        self.setenv(MARKET_DB=d)
        probe = OF.probe_hot_db()
        self.assertTrue(probe["exists"])
        self.assertFalse(probe["readable"])
        self.assertIsNotNone(probe["error"])

    def test_non_sqlite_file_as_market_db(self):
        f = self.tmp / "not_a_db.sqlite3"
        f.write_bytes(b"this is definitely not sqlite" * 100)
        self.setenv(MARKET_DB=f)
        probe = OF.probe_hot_db()
        self.assertTrue(probe["exists"])
        self.assertFalse(probe["readable"])
        self.assertIsNotNone(probe["error"])

    def test_sqlite_without_oi_history_table(self):
        """库能打开但没有 `oi_history` ⇒ 必须如实标 False，不能当作「OI 全 0」。"""
        p = self.tmp / "empty.sqlite3"
        con = sqlite3.connect(str(p))
        con.execute("CREATE TABLE other (x INTEGER)")
        con.commit()
        con.close()
        self.setenv(MARKET_DB=p)
        probe = OF.probe_hot_db()
        self.assertTrue(probe["exists"])
        self.assertTrue(probe["readable"])
        self.assertFalse(probe["has_oi_history"])
        self.assertIsNone(probe["error"])

    def test_runner_report_flags_unreadable_db(self):
        f = self.tmp / "bad.sqlite3"
        f.write_bytes(b"garbage" * 200)
        self.setenv(MARKET_DB=f)
        rep = EventTrendRunner().data_paths_report()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("hot_db" in p for p in rep["problems"]), rep["problems"])

    def test_contracts_dir_pointing_at_file(self):
        """把文件当目录：`exists()` 真但 `glob` 无内容 ⇒ 不能当「有快照」。"""
        f = self.tmp / "contracts_file"
        f.write_text("x", encoding="utf-8")
        self.setenv(CONTRACTS_DIR=f)
        book = IS.get_spec_book()
        self.assertEqual(book._snapshots, [])


# ══════════════════════════ T06 隔离工作树（裸树）══════════════════════════

class TestT06IsolatedWorktree(_Base):
    """模拟隔离工作树：有代码、没有 `数据/`。三条路径必须全部显式暴露。"""

    def test_bare_tree_reports_three_problems(self):
        self.setenv(DATA_HUB_PROJECT=self.tmp)
        self.simulate_bare_tree()
        rep = EventTrendRunner().data_paths_report()
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["base_dir"], str(self.tmp))
        self.assertFalse(rep["parquet"]["exists"])
        self.assertFalse(rep["market_db"]["exists"])
        self.assertFalse(rep["contracts"]["exists"])
        self.assertEqual(len(rep["problems"]), 3, rep["problems"])

    def test_bare_tree_then_env_rescue(self):
        """裸树 + 环境变量指向外部只读库 ⇒ 立刻可用（工单要求的核心用法）。"""
        ext = self.tmp / "ext"
        ext.mkdir()
        db = _make_sqlite_with_oi(ext / "hist.sqlite3")
        arch = ext / "parquet"
        (arch / "candles" / "binance" / "um" / "BTCUSDT" / "4h").mkdir(parents=True)
        (arch / "oi" / "binance" / "um" / "BTCUSDT").mkdir(parents=True)
        contracts = ext / "contracts"
        contracts.mkdir()

        self.setenv(DATA_HUB_PROJECT=self.tmp / "bare_tree",
                    MARKET_DB=db, MARKET_ARCHIVE=arch,
                    CONTRACTS_DIR=contracts, WX_RUNS_BASE=self.tmp / "runs")
        rep = EventTrendRunner().data_paths_report()
        self.assertTrue(rep["ok"], rep["problems"])

    def test_runs_base_outside_tree(self):
        """`WX_RUNS_BASE` 可把产物写到工作树外 ⇒ 隔离树不被回测产物污染。"""
        out = self.tmp / "outside_runs"
        self.setenv(WX_RUNS_BASE=out)
        r = EventTrendRunner()
        self.assertEqual(r.runs_base, str(out.resolve()))
        self.assertTrue(Path(r.runs_base).is_dir())    # 构造时自动建好
        self.assertNotIn(PROJECT_ROOT, Path(r.runs_base).parents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
