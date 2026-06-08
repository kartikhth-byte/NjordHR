import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_helper():
    repo_root = Path(__file__).resolve().parents[1]
    helper_path = repo_root / "scripts" / "packaging" / "release_bundle_common.py"
    spec = importlib.util.spec_from_file_location("release_bundle_common", helper_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release_bundle_common = _load_helper()


class ReleaseBundleCommonTests(unittest.TestCase):
    def test_write_release_metadata_creates_manifest_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "NjordHR-1.2.3-setup.exe").write_bytes(b"setup-binary")
            (root / "NjordHR-1.2.3-portable.zip").write_bytes(b"portable-binary")
            (root / "NjordHR-1.2.3-portable.zip.sig").write_text("signature-123", encoding="utf-8")
            (root / "INSTALL.md").write_text("ignore", encoding="utf-8")

            manifest = release_bundle_common.write_release_metadata("1.2.3", root)

            manifest_path = root / "manifest.json"
            checksums_path = root / "checksums.txt"
            self.assertTrue(manifest_path.exists())
            self.assertTrue(checksums_path.exists())

            manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_json["version"], "1.2.3")
            self.assertEqual(manifest_json["artifact_count"], 2)
            self.assertEqual([artifact["name"] for artifact in manifest_json["artifacts"]], [
                "NjordHR-1.2.3-portable.zip",
                "NjordHR-1.2.3-setup.exe",
            ])
            portable = next(item for item in manifest_json["artifacts"] if item["name"].endswith("portable.zip"))
            self.assertEqual(portable["signature"], "signature-123")
            self.assertEqual(manifest, manifest_json)

            checksum_lines = checksums_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(checksum_lines), 2)
            self.assertTrue(all("  " in line for line in checksum_lines))

    def test_collect_artifacts_skips_generated_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "artifact.bin").write_bytes(b"abc")
            (root / "checksums.txt").write_text("ignored", encoding="utf-8")
            (root / "manifest.json").write_text("ignored", encoding="utf-8")
            (root / "INSTALL.md").write_text("ignored", encoding="utf-8")

            artifacts = release_bundle_common.collect_artifacts(root)

            self.assertEqual([path.name for path in artifacts], ["artifact.bin"])


if __name__ == "__main__":
    unittest.main()
