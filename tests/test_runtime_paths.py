"""隔离路径检查器的负例；全部使用临时目录和空输入文件。"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-runtime-paths.py"
SPEC = importlib.util.spec_from_file_location("runtime_path_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.code = self.root / "code"
        self.code.mkdir()
        self.market = self.root / "market"
        self.market.mkdir()
        self.db = self.market / "market.sqlite3"
        self.db.touch()
        self.archive = self.market / "parquet"
        self.archive.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {
            "MARKET_DB": str(self.db),
            "MARKET_ARCHIVE": str(self.archive),
            "EVENT_TREND_RUNS_DIR": str(self.state / "runs"),
            "OI_RESONANCE_RUNS_DIR": str(self.state / "runs"),
            "FRAME_FIT_REGISTRY": str(self.state / "experiments"),
            "WX_PROVENANCE_HOME": str(self.state / "provenance"),
            "BT_RUN_LOG": str(self.state / "logs" / "backtest_runs.log"),
        }

    def errors(self, changes=None):
        return MODULE.validate_paths(self.env | (changes or {}), self.code)

    def test_separated_paths_pass_without_creating_runtime_directories(self):
        before = set(self.root.rglob("*"))
        self.assertEqual(self.errors(), [])
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertFalse((self.state / "runs").exists())
        self.assertFalse((self.state / "logs").exists())

    def test_missing_or_relative_configuration_fails(self):
        for value in ("", "relative/experiments"):
            with self.subTest(value=value):
                self.assertTrue(any("FRAME_FIT_REGISTRY" in error for error in self.errors({"FRAME_FIT_REGISTRY": value})))

    def test_input_metadata_must_exist_and_have_correct_type(self):
        for changes in (
            {"MARKET_DB": str(self.market / "missing.sqlite3")},
            {"MARKET_DB": str(self.archive)},
            {"MARKET_ARCHIVE": str(self.db)},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(self.errors(changes))

    def test_dotdot_and_symlink_cannot_enter_code_tree(self):
        alias = self.root / "outside_alias"
        alias.symlink_to(self.code, target_is_directory=True)
        for path in (self.state / ".." / "code" / "provenance", alias / "provenance"):
            with self.subTest(path=path):
                self.assertTrue(any("代码树" in error for error in self.errors({"WX_PROVENANCE_HOME": str(path)})))

    def test_write_directory_cannot_overlap_market_inputs_or_other_writers(self):
        for path in (self.archive / "experiments", self.state / "runs" / "experiments", self.market.parent):
            with self.subTest(path=path):
                self.assertTrue(any("重叠" in error for error in self.errors({"FRAME_FIT_REGISTRY": str(path)})))

    def test_code_tree_cannot_be_nested_inside_market_archive(self):
        nested_code = self.archive / "code"
        nested_code.mkdir()
        errors = MODULE.validate_paths(self.env, nested_code)
        self.assertTrue(any("MARKET_ARCHIVE" in error and "代码树重叠" in error for error in errors))

    def test_runs_aliases_must_agree(self):
        self.assertTrue(any("必须解析到同一目录" in error for error in self.errors({"OI_RESONANCE_RUNS_DIR": str(self.root / "different_runs")})))

    def test_log_cannot_be_a_hardlink_to_market_database(self):
        log_path = Path(self.env["BT_RUN_LOG"])
        log_path.parent.mkdir()
        os.link(self.db, log_path)
        self.assertTrue(any("同一个文件" in error for error in self.errors()))

    def test_sibling_runs_archive_is_rejected(self):
        (self.state / "runs_archive").mkdir()
        self.assertTrue(any("同级存在其他 runs*" in error for error in self.errors()))

    def test_leaf_symlink_checks_the_actual_scan_parent(self):
        target = self.root / "separate" / "current"
        target.mkdir(parents=True)
        alias = self.state / "current"
        alias.symlink_to(target, target_is_directory=True)
        (self.state / "runs_legacy").mkdir()
        self.assertTrue(any("runs_legacy" in error for error in self.errors({
            "EVENT_TREND_RUNS_DIR": str(alias),
            "OI_RESONANCE_RUNS_DIR": str(alias),
        })))


if __name__ == "__main__":
    unittest.main()
