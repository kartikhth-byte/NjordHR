import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _stub_ai_dependencies():
    if "fitz" not in sys.modules:
        sys.modules["fitz"] = types.ModuleType("fitz")

    if "PIL" not in sys.modules:
        pil_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        pil_module.Image = image_module
        sys.modules["PIL"] = pil_module
        sys.modules["PIL.Image"] = image_module

    if "pinecone" not in sys.modules:
        pinecone_module = types.ModuleType("pinecone")

        class DummyPinecone:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyServerlessSpec:
            def __init__(self, *_args, **_kwargs):
                pass

        pinecone_module.Pinecone = DummyPinecone
        pinecone_module.ServerlessSpec = DummyServerlessSpec
        sys.modules["pinecone"] = pinecone_module


_stub_ai_dependencies()
from ai_analyzer import AIResumeAnalyzer  # noqa: E402
from backend_server import _coc_issue_authority_country_aliases  # noqa: E402
from candidate_facts.aliases.coc_country import load_coc_country_aliases  # noqa: E402
from candidate_facts.aliases.coc_issue_authority import load_coc_issue_authority_aliases  # noqa: E402


ALIAS_FILE = Path("candidate_facts/aliases/coc_country.json")
AUTHORITY_FILE = Path("candidate_facts/aliases/coc_issue_authority.json")


def _write_country_payload(payload):
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "coc_country.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return temp_dir, path


def _base_payload(countries):
    return {
        "version": "1.0.0",
        "last_updated": "2026-06-28",
        "source": "test",
        "countries": countries,
    }


class CocCountryAliasTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_json_alias_maps_match_current_inline_analyzer_snapshots(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(
            dict(aliases.alias_map),
            self.analyzer._coc_country_aliases(include_ambiguous_shortcuts=True),
        )
        self.assertEqual(
            dict(aliases.alias_map_without_ambiguous_shortcuts),
            self.analyzer._coc_country_aliases(include_ambiguous_shortcuts=False),
        )

    def test_authority_country_alias_map_matches_current_backend_allow_list(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(
            dict(aliases.authority_country_alias_map),
            _coc_issue_authority_country_aliases(),
        )

    def test_display_labels_match_current_analyzer_labels(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(aliases.display_labels["uk"], "UK")
        self.assertEqual(aliases.display_labels["uae"], "UAE")
        self.assertEqual(aliases.display_labels["usa"], "USA")
        for country_id, display_label in aliases.display_labels.items():
            with self.subTest(country_id=country_id):
                self.assertEqual(display_label, self.analyzer._coc_country_display_label(country_id))

    def test_existing_issue_authority_catalog_country_values_are_allowed(self):
        country_aliases = load_coc_country_aliases(ALIAS_FILE)

        authority_aliases = load_coc_issue_authority_aliases(
            AUTHORITY_FILE,
            country_aliases=country_aliases.authority_country_alias_map,
        )

        allowed_countries = set(country_aliases.authority_country_alias_map.values())
        self.assertTrue(authority_aliases.country_by_authority)
        self.assertTrue(set(authority_aliases.country_by_authority.values()).issubset(allowed_countries))

    def test_loader_rejects_ambiguous_shortcut_outside_closed_list(self):
        payload = _base_payload({
            "canada": {
                "display_label": "Canada",
                "authority_catalog_allowed": False,
                "aliases": ["canada", "canadian"],
                "ambiguous_shortcuts": ["ca"],
            },
        })
        temp_dir, path = _write_country_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "unsupported v1 shortcut"):
            load_coc_country_aliases(path)

    def test_loader_rejects_non_boolean_authority_catalog_flag(self):
        payload = _base_payload({
            "india": {
                "display_label": "India",
                "authority_catalog_allowed": "true",
                "aliases": ["india", "indian"],
            },
        })
        temp_dir, path = _write_country_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "authority_catalog_allowed must be boolean"):
            load_coc_country_aliases(path)

    def test_loader_rejects_malformed_last_updated(self):
        payload = _base_payload({
            "india": {
                "display_label": "India",
                "authority_catalog_allowed": True,
                "aliases": ["india", "indian"],
            },
        })
        payload["last_updated"] = "2026-6-28"
        temp_dir, path = _write_country_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "last_updated must match YYYY-MM-DD"):
            load_coc_country_aliases(path)

    def test_loader_rejects_empty_source(self):
        payload = _base_payload({
            "india": {
                "display_label": "India",
                "authority_catalog_allowed": True,
                "aliases": ["india", "indian"],
            },
        })
        payload["source"] = ""
        temp_dir, path = _write_country_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "source must be a non-empty string"):
            load_coc_country_aliases(path)

    def test_loader_rejects_duplicate_alias_within_country(self):
        payload = _base_payload({
            "india": {
                "display_label": "India",
                "authority_catalog_allowed": True,
                "aliases": ["india", "India", "indian"],
            },
        })
        temp_dir, path = _write_country_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "duplicate alias after normalization"):
            load_coc_country_aliases(path)


if __name__ == "__main__":
    unittest.main()
