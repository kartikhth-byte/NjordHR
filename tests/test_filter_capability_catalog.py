import json
import tempfile
import unittest
from pathlib import Path

from candidate_facts.aliases.filter_capability_catalog import (
    CATALOG_FILE,
    PROMOTED_FAMILIES,
    backend_catalog,
    llm_facing_catalog,
    load_filter_capability_catalog,
    validate_catalog_parameters,
)


def _write_catalog(payload):
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "filter_capability_catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return temp_dir, path


def _base_parameters(**overrides):
    value = {
        "version": "v1",
        "value_type": "status",
        "status": "immediate",
        "available_by_date": None,
        "available_from_date": None,
        "available_until_date": None,
        "relative_days": None,
        "resolved_reference_date": "2026-04-06",
        "display_value": "available immediately",
    }
    value.update(overrides)
    return value


class FilterCapabilityCatalogTests(unittest.TestCase):
    def test_catalog_loads_availability_as_promoted_family(self):
        catalog = load_filter_capability_catalog(CATALOG_FILE)

        self.assertEqual(catalog.version, "1.0.0")
        self.assertIn("availability", catalog.families_by_id)
        self.assertEqual(PROMOTED_FAMILIES, {"availability"})
        availability = catalog.families_by_id["availability"]
        self.assertEqual(availability["executor_id"], "availability")
        self.assertEqual(
            availability["plausibility_bounds"],
            {"relative_days": {"min": 0, "max": 365}},
        )

    def test_llm_facing_catalog_redacts_executor_id(self):
        catalog = load_filter_capability_catalog(CATALOG_FILE)

        backend_row = backend_catalog(catalog)[0]
        public_row = llm_facing_catalog(catalog)[0]

        self.assertEqual(backend_row["executor_id"], "availability")
        self.assertNotIn("executor_id", public_row)
        self.assertEqual(public_row["family"], "availability")

    def test_catalog_views_do_not_poison_cached_rows(self):
        catalog = load_filter_capability_catalog(CATALOG_FILE)

        backend_row = backend_catalog(catalog)[0]
        backend_row["plausibility_bounds"]["relative_days"]["max"] = 99999
        backend_row["output_schema"]["properties"]["version"]["const"] = "v999"
        public_row = llm_facing_catalog(catalog)[0]
        public_row["accepted_phrases"].append("mutated phrase")

        fresh_backend_row = backend_catalog(catalog)[0]
        fresh_public_row = llm_facing_catalog(catalog)[0]
        self.assertEqual(fresh_backend_row["plausibility_bounds"]["relative_days"]["max"], 365)
        self.assertEqual(fresh_backend_row["output_schema"]["properties"]["version"]["const"], "v1")
        self.assertNotIn("mutated phrase", fresh_public_row["accepted_phrases"])

    def test_loader_rejects_executor_missing_from_capability_registry(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "CAPABILITY_REGISTRY"):
            load_filter_capability_catalog(path, capability_registry={})

    def test_loader_rejects_missing_root_key(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        del payload["families"]
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "filter_capability_catalog.families is required"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_malformed_version(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["version"] = "1"
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "version must match x.y.z"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_short_phrase_lists(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"][0]["accepted_phrases"] = ["available now", "available by date"]
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "accepted_phrases must contain at least 3 entries"):
            load_filter_capability_catalog(path)

        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"][0]["do_not_use_for"] = ["salary"]
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "do_not_use_for must contain at least 2 entries"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_duplicate_family_id(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"].append(dict(payload["families"][0]))
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "duplicate filter capability family: availability"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_invalid_family_id(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"][0]["family"] = "Availability-Filter"
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, r"family must match \[a-z0-9_\]\+"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_missing_numeric_plausibility_bound(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"][0]["plausibility_bounds"] = {}
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "missing numeric fields: relative_days"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_extra_plausibility_bound(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["families"][0]["plausibility_bounds"]["available_by_date"] = {"min": 0, "max": 1}
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "bounds for non-numeric fields: available_by_date"):
            load_filter_capability_catalog(path)

    def test_loader_rejects_malformed_last_updated(self):
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        payload["last_updated"] = "2026-6-29"
        temp_dir, path = _write_catalog(payload)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaisesRegex(ValueError, "last_updated must match YYYY-MM-DD"):
            load_filter_capability_catalog(path)

    def test_availability_schema_accepts_all_value_types(self):
        cases = [
            _base_parameters(),
            _base_parameters(
                value_type="by_date",
                status=None,
                available_by_date="2026-04-15",
                display_value="available by 2026-04-15",
            ),
            _base_parameters(
                value_type="from_date",
                status=None,
                available_from_date="2026-09-01",
                display_value="available from 2026-09-01",
            ),
            _base_parameters(
                value_type="relative_days",
                status=None,
                relative_days=30,
                display_value="available within 30 days",
            ),
            _base_parameters(
                value_type="window",
                status=None,
                available_from_date="2026-04-01",
                available_until_date="2026-05-01",
                display_value="available between 2026-04-01 and 2026-05-01",
            ),
        ]

        for parameters in cases:
            with self.subTest(value_type=parameters["value_type"]):
                validate_catalog_parameters("availability", parameters)

    def test_availability_schema_rejects_inactive_fields(self):
        parameters = _base_parameters(
            value_type="by_date",
            status=None,
            available_by_date="2026-04-15",
            available_from_date="2026-04-01",
            display_value="available by 2026-04-15",
        )

        with self.assertRaisesRegex(ValueError, "exactly one value_type schema"):
            validate_catalog_parameters("availability", parameters)

    def test_availability_schema_rejects_missing_required_discriminator_field(self):
        parameters = _base_parameters(
            value_type="relative_days",
            status=None,
            relative_days=None,
            display_value="available within 30 days",
        )

        with self.assertRaisesRegex(ValueError, "exactly one value_type schema"):
            validate_catalog_parameters("availability", parameters)

    def test_availability_schema_rejects_relative_days_out_of_bounds(self):
        parameters = _base_parameters(
            value_type="relative_days",
            status=None,
            relative_days=366,
            display_value="available within 366 days",
        )

        with self.assertRaisesRegex(ValueError, "outside plausibility bounds"):
            validate_catalog_parameters("availability", parameters)

    def test_availability_schema_rejects_invalid_dates_and_reversed_window(self):
        with self.assertRaisesRegex(ValueError, "available_by_date must match YYYY-MM-DD"):
            validate_catalog_parameters(
                "availability",
                _base_parameters(
                    value_type="by_date",
                    status=None,
                    available_by_date="15/04/2026",
                    display_value="available by 15 Apr 2026",
                ),
            )

        with self.assertRaisesRegex(ValueError, "available_by_date must match YYYY-MM-DD"):
            validate_catalog_parameters(
                "availability",
                _base_parameters(
                    value_type="by_date",
                    status=None,
                    available_by_date="2026-02-30",
                    display_value="available by 2026-02-30",
                ),
            )

        with self.assertRaisesRegex(ValueError, "resolved_reference_date must match YYYY-MM-DD"):
            validate_catalog_parameters(
                "availability",
                _base_parameters(resolved_reference_date="2026-13-01"),
            )

        with self.assertRaisesRegex(ValueError, "available_from_date cannot exceed"):
            validate_catalog_parameters(
                "availability",
                _base_parameters(
                    value_type="window",
                    status=None,
                    available_from_date="2026-05-01",
                    available_until_date="2026-04-01",
                    display_value="available between 2026-05-01 and 2026-04-01",
                ),
            )

    def test_availability_schema_rejects_unknown_family_and_extra_field(self):
        with self.assertRaisesRegex(ValueError, "Unknown filter capability family"):
            validate_catalog_parameters("unknown", _base_parameters())

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_catalog_parameters(
                "availability",
                {**_base_parameters(), "availability_extracted_on_date": "2026-04-01"},
            )


if __name__ == "__main__":
    unittest.main()
