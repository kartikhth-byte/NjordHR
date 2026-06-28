import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


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
import backend_server  # noqa: E402
from ai_analyzer import AIResumeAnalyzer  # noqa: E402
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
        self._reset_alias_caches()

    def tearDown(self):
        self._reset_alias_caches()

    def _reset_alias_caches(self):
        AIResumeAnalyzer._COC_COUNTRY_ALIASES = None
        AIResumeAnalyzer._COC_COUNTRY_ALIAS_SOURCE_LOGGED = False
        AIResumeAnalyzer._COC_ISSUE_AUTHORITY_ALIASES = None
        AIResumeAnalyzer._COC_ISSUE_AUTHORITY_ALIAS_SOURCE = None
        backend_server._COC_COUNTRY_ALIASES = None
        backend_server._COC_COUNTRY_ALIAS_SOURCE_LOGGED = False
        backend_server._COC_ISSUE_AUTHORITY_ALIASES = None
        backend_server._COC_ISSUE_AUTHORITY_ALIAS_SOURCE = None

    def test_json_alias_maps_match_current_inline_analyzer_snapshots(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(
            dict(aliases.alias_map),
            self.analyzer._inline_coc_country_aliases(include_ambiguous_shortcuts=True),
        )
        self.assertEqual(
            dict(aliases.alias_map_without_ambiguous_shortcuts),
            self.analyzer._inline_coc_country_aliases(include_ambiguous_shortcuts=False),
        )

    def test_authority_country_alias_map_matches_current_backend_allow_list(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(
            dict(aliases.authority_country_alias_map),
            backend_server._inline_coc_issue_authority_country_aliases(),
        )

    def test_runtime_source_switch_preserves_analyzer_maps(self):
        for include_ambiguous_shortcuts in (True, False):
            with self.subTest(include_ambiguous_shortcuts=include_ambiguous_shortcuts):
                with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "inline"}):
                    inline_map = self.analyzer._coc_country_aliases(
                        include_ambiguous_shortcuts=include_ambiguous_shortcuts
                    )
                self._reset_alias_caches()
                with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "json"}):
                    json_map = self.analyzer._coc_country_aliases(
                        include_ambiguous_shortcuts=include_ambiguous_shortcuts
                    )
                self.assertEqual(json_map, inline_map)

    def test_runtime_source_switch_preserves_backend_authority_allow_list(self):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "inline"}):
            inline_map = backend_server._coc_issue_authority_country_aliases()
        self._reset_alias_caches()
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "json"}):
            json_map = backend_server._coc_issue_authority_country_aliases()

        self.assertEqual(json_map, inline_map)

    def test_unknown_runtime_source_falls_back_to_json(self):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "unexpected"}):
            self.assertEqual(self.analyzer._coc_country_alias_source(), "json")
            self.assertEqual(backend_server._coc_country_alias_source(), "json")

    def test_runtime_source_switch_rebuilds_analyzer_authority_alias_cache(self):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "inline"}):
            inline_aliases = self.analyzer._coc_issue_authority_aliases()
            self.assertEqual(AIResumeAnalyzer._COC_ISSUE_AUTHORITY_ALIAS_SOURCE, "inline")

        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "json"}):
            json_aliases = self.analyzer._coc_issue_authority_aliases()

        self.assertEqual(AIResumeAnalyzer._COC_ISSUE_AUTHORITY_ALIAS_SOURCE, "json")
        self.assertEqual(json_aliases.alias_map, inline_aliases.alias_map)

    def test_runtime_source_switch_rebuilds_backend_authority_alias_cache(self):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "inline"}):
            inline_aliases = backend_server._load_coc_issue_authority_aliases()
            self.assertEqual(backend_server._COC_ISSUE_AUTHORITY_ALIAS_SOURCE, "inline")

        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "json"}):
            json_aliases = backend_server._load_coc_issue_authority_aliases()

        self.assertEqual(backend_server._COC_ISSUE_AUTHORITY_ALIAS_SOURCE, "json")
        self.assertEqual(json_aliases.alias_map, inline_aliases.alias_map)

    def test_display_labels_match_current_analyzer_labels(self):
        aliases = load_coc_country_aliases(ALIAS_FILE)

        self.assertEqual(aliases.display_labels["uk"], "UK")
        self.assertEqual(aliases.display_labels["uae"], "UAE")
        self.assertEqual(aliases.display_labels["usa"], "USA")
        for country_id, display_label in aliases.display_labels.items():
            with self.subTest(country_id=country_id):
                self.assertEqual(display_label, self.analyzer._coc_country_display_label(country_id))

    def test_display_label_first_path_logs_active_source(self):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": "json"}):
            self.assertEqual(self.analyzer._coc_country_display_label("uk"), "UK")

        self.assertTrue(AIResumeAnalyzer._COC_COUNTRY_ALIAS_SOURCE_LOGGED)

    def test_existing_issue_authority_catalog_country_values_are_allowed(self):
        country_aliases = load_coc_country_aliases(ALIAS_FILE)

        authority_aliases = load_coc_issue_authority_aliases(
            AUTHORITY_FILE,
            country_aliases=country_aliases.authority_country_alias_map,
        )

        allowed_countries = set(country_aliases.authority_country_alias_map.values())
        self.assertTrue(authority_aliases.country_by_authority)
        self.assertTrue(set(authority_aliases.country_by_authority.values()).issubset(allowed_countries))

    def test_country_consuming_paths_match_inline_and_json_sources(self):
        cases = [
            ("normalize", "Indian", (), "india"),
            ("normalize", "Iranian", (), "iran"),
            ("normalize", "American", (), "usa"),
            ("snippet", "Indian CoC", (), "india"),
            ("snippet", "Iranian CoC", (), "iran"),
            ("snippet", "American CoC holder", (), "usa"),
            ("snippet", "COC issued in 2019", (), None),
            ("snippet", "give us coc details", (), None),
            ("snippet", "USA-issued certificate of competency", (), "usa"),
            ("snippet", "Marshall Islands certificate of competency", (), "marshall islands"),
            ("snippet", "South African CoC", (), "south africa"),
            ("snippet", "United Arab Emirates CoC", (), "uae"),
            ("phrase", "Indian CoC", (), "indian"),
            ("prompt_country_for_alias", "india issued mca coc", ("mca",), "india"),
            ("prompt_country_for_alias", "mca from uk", ("mca",), "uk"),
            ("prompt_country_for_alias", "mca from united kingdom", ("mca",), "uk"),
            ("prompt_country_for_alias", "filipino mca coc holder", ("mca",), None),
            ("prompt_country_for_alias", "maritime and coastguard agency issuing uk coc", ("maritime and coastguard agency",), "uk"),
            ("prompt_country_for_alias", "uk mca coc", ("mca",), "uk"),
            ("snippet", "Panamanian certificate of competency", (), "panama"),
            ("snippet", "Dutch CoC", (), "netherlands"),
            ("snippet", "Filipino CoC", (), "philippines"),
        ]

        for kind, value, extra_args, expected in cases:
            with self.subTest(kind=kind, value=value):
                inline_result = self._run_country_path(kind, value, extra_args, source="inline")
                self._reset_alias_caches()
                json_result = self._run_country_path(kind, value, extra_args, source="json")
                self.assertEqual(json_result, inline_result)
                self.assertEqual(json_result, expected)

    def _run_country_path(self, kind, value, extra_args, *, source):
        with patch.dict(os.environ, {"COC_COUNTRY_ALIAS_SOURCE": source}):
            if kind == "normalize":
                return self.analyzer._normalize_coc_country(value)
            if kind == "snippet":
                return self.analyzer._extract_coc_country_from_snippet(value)
            if kind == "phrase":
                return self.analyzer._is_coc_country_phrase_candidate(value)
            if kind == "prompt_country_for_alias":
                return self.analyzer._coc_issue_authority_prompt_country_for_alias(value, *extra_args)
        raise AssertionError(f"unknown country path test kind: {kind}")

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
