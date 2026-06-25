import json
import tempfile
import unittest
from pathlib import Path

from candidate_facts.aliases.coc_issue_authority import load_coc_issue_authority_aliases


COUNTRY_ALIASES = {
    "india": "india",
    "indian": "india",
    "pakistan": "pakistan",
    "pakistani": "pakistan",
}


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


def _country_aliases_for_payload(payload):
    return {
        country_key: country_entry["country_canonical"]
        for country_key, country_entry in (payload.get("authorities") or {}).items()
    }


class CocIssueAuthorityAliasTests(unittest.TestCase):
    def test_existing_coc_issue_authority_aliases_pass_validation(self):
        alias_file = Path("candidate_facts/aliases/coc_issue_authority.json")
        payload = json.loads(alias_file.read_text(encoding="utf-8"))

        aliases = load_coc_issue_authority_aliases(country_aliases=_country_aliases_for_payload(payload))

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
            "ambiguous bare CoC authority alias across canonicals: 'mmd'.*india_mmd/india.*pakistan_mmd/pakistan",
        ):
            load_coc_issue_authority_aliases(path, country_aliases=COUNTRY_ALIASES)

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
