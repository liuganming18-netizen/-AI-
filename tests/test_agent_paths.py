import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"

REQUIRED = (
    "docs/用户约定/用户要求与决策.md",
    "docs/用户约定/当前交接.md",
)


class AgentPathsTests(unittest.TestCase):
    def test_handoff_paths_are_relative_and_exist(self):
        text = AGENTS.read_text(encoding="utf-8")

        for relative_path in REQUIRED:
            with self.subTest(path=relative_path):
                self.assertIn(f"`{relative_path}`", text)
                self.assertTrue(
                    (ROOT / relative_path).is_file(),
                    f"交接文件不存在: {relative_path}",
                )

        self.assertNotIn(
            "/Users/boos/Desktop/",
            text,
        )


if __name__ == "__main__":
    unittest.main()
