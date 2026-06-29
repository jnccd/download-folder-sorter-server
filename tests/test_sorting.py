import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import AppConfig, MatchRule
from app.matcher import matches_rule
from app.sorter import SorterService


class SortingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sorter-test-", dir=".")
        self.download_dir = Path(self.temp_dir) / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.target_dir = Path(self.temp_dir) / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        config = AppConfig()
        config.download_folder = str(self.download_dir)
        config.sort_delay_seconds = 0
        config.rules = [MatchRule(name="Pictures", match=".png|.jpg", target=str(self.target_dir))]
        self.service = SorterService(config)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_matching_rule_supports_or_and_and(self) -> None:
        self.assertTrue(matches_rule("sunset.png", self.service.config.rules[0]))
        self.assertTrue(matches_rule("invoice_001.jpg", self.service.config.rules[0]))
        self.assertFalse(matches_rule("notes.pdf", self.service.config.rules[0]))

    def test_sorter_moves_matching_file_to_target(self) -> None:
        sample = self.download_dir / "photo.png"
        sample.write_text("hello", encoding="utf-8")

        self.service.run_once()

        self.assertFalse(sample.exists())
        self.assertTrue((self.target_dir / "photo.png").exists())


if __name__ == "__main__":
    unittest.main()
