import tempfile
import unittest
from pathlib import Path

from scraper_engine import Scraper


class ScraperEngineDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.scraper = Scraper(download_folder=str(self.base))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_candidate_file_exists_current_name(self):
        target = self.base / "Chief_Officer"
        target.mkdir(parents=True, exist_ok=True)
        (target / "Chief_Officer_12345.pdf").write_bytes(b"pdf")

        exists = self.scraper._candidate_file_exists(
            str(target), "Chief Officer", "Bulk Carrier", "12345"
        )
        self.assertTrue(exists)

    def test_candidate_file_exists_legacy_timestamp_name(self):
        target = self.base / "Chief_Officer"
        target.mkdir(parents=True, exist_ok=True)
        # Legacy pattern: rank-file_ship-file_candidateid_YYYY-MM-DD_HH-MM-SS.pdf
        (target / "Chief-Officer_Bulk-Carrier_12345_2025-09-05_11-48-49.pdf").write_bytes(b"pdf")

        exists = self.scraper._candidate_file_exists(
            str(target), "Chief Officer", "Bulk Carrier", "12345"
        )
        self.assertTrue(exists)

    def test_candidate_file_exists_false_when_no_match(self):
        target = self.base / "Chief_Officer"
        target.mkdir(parents=True, exist_ok=True)
        (target / "Chief-Officer_Bulk-Carrier_99999_2025-09-05_11-48-49.pdf").write_bytes(b"pdf")

        exists = self.scraper._candidate_file_exists(
            str(target), "Chief Officer", "Bulk Carrier", "12345"
        )
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()

