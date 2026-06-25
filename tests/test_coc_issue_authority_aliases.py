import json
import tempfile
import unittest
from pathlib import Path

from backend_server import _coc_issue_authority_country_aliases
from candidate_facts.aliases.coc_issue_authority import load_coc_issue_authority_aliases


COUNTRY_ALIASES = _coc_issue_authority_country_aliases()


def _write_alias_payload(payload):
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "coc_issue_authority.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return temp_dir, path


def _base_payload(authorities):
    return {
        "version": "0.1.0",
        "last_updated": "2026-06-25",
        "source": "test",
        "authorities": authorities,
    }


class CocIssueAuthorityAliasTests(unittest.TestCase):
    def test_existing_coc_issue_authority_aliases_pass_validation(self):
        alias_file = Path("candidate_facts/aliases/coc_issue_authority.json")

        aliases = load_coc_issue_authority_aliases(alias_file, country_aliases=COUNTRY_ALIASES)

        self.assertIn("india_dg_shipping", aliases.display_labels)
        self.assertIn("pakistan_mmd", aliases.display_labels)

    def test_loader_rejects_cross_canonical_bare_alias_conflict(self):
        payload = _base_payload({
            "india": {
                "country_canonical": "india",
                "authorities": {
                    "india_mmd": {
                        "display_label": "MMD India",
                        "aliases": [
                            "mmd",
                            "mercantile marine department",
                        ],
                    },
                },
            },
            "pakistan": {
                "country_canonical": "pakistan",
                "authorities": {
                    "pakistan_mmd": {
                        "display_label": "MMD Pakistan",
                        "aliases": [
                            "mmd pakistan",
                            "mercantile marine department karachi",
                        ],
                    },
                },
            },
        })
        temp_dir, path = _write_alias_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(
            ValueError,
            "ambiguous bare CoC authority alias across canonicals: 'mercantile marine department'.*india_mmd/india.*'mercantile marine department karachi'.*pakistan_mmd/pakistan",
        ):
            load_coc_issue_authority_aliases(path, country_aliases=COUNTRY_ALIASES)

    def test_loader_rejects_dotted_abbreviation_bare_alias_conflict(self):
        payload = _base_payload({
            "india": {
                "country_canonical": "india",
                "authorities": {
                    "india_dg_shipping": {
                        "display_label": "DGS India",
                        "aliases": ["dgs"],
                    },
                },
            },
            "pakistan": {
                "country_canonical": "pakistan",
                "authorities": {
                    "pakistan_dgs": {
                        "display_label": "DGS Pakistan",
                        "aliases": ["d.g.s. pakistan"],
                    },
                },
            },
        })
        temp_dir, path = _write_alias_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(
            ValueError,
            "ambiguous bare CoC authority alias across canonicals: 'dgs'.*'d g s pakistan'.*pakistan_dgs/pakistan",
        ):
            load_coc_issue_authority_aliases(path, country_aliases=COUNTRY_ALIASES)

    def test_loader_allows_other_side_country_qualified_alias(self):
        payload = _base_payload({
            "panama": {
                "country_canonical": "panama",
                "authorities": {
                    "panama_maritime_authority": {
                        "display_label": "Panama Maritime Authority",
                        "aliases": ["pma"],
                    },
                },
            },
            "bahamas": {
                "country_canonical": "bahamas",
                "authorities": {
                    "bahamas_port_maritime_authority": {
                        "display_label": "Port Maritime Authority, Bahamas",
                        "aliases": ["pma bahamas"],
                    },
                },
            },
        })
        temp_dir, path = _write_alias_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        aliases = load_coc_issue_authority_aliases(path, country_aliases=COUNTRY_ALIASES)

        self.assertEqual(aliases.alias_map["pma"], "panama_maritime_authority")
        self.assertEqual(aliases.alias_map["pma bahamas"], "bahamas_port_maritime_authority")

    def test_loader_allows_qualified_cross_canonical_aliases(self):
        payload = _base_payload({
            "india": {
                "country_canonical": "india",
                "authorities": {
                    "india_mmd": {
                        "display_label": "MMD India",
                        "aliases": [
                            "mmd india",
                            "mercantile marine department india",
                        ],
                    },
                },
            },
            "pakistan": {
                "country_canonical": "pakistan",
                "authorities": {
                    "pakistan_mmd": {
                        "display_label": "MMD Pakistan",
                        "aliases": [
                            "mmd pakistan",
                            "mercantile marine department pakistan",
                        ],
                    },
                },
            },
        })
        temp_dir, path = _write_alias_payload(payload)
        self.addCleanup(temp_dir.cleanup)

        aliases = load_coc_issue_authority_aliases(path, country_aliases=COUNTRY_ALIASES)

        self.assertEqual(aliases.alias_map["mmd india"], "india_mmd")
        self.assertEqual(aliases.alias_map["mmd pakistan"], "pakistan_mmd")


if __name__ == "__main__":
    unittest.main()
