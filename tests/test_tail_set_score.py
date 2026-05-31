from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/opt/anaconda3/bin/python"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class TailSetScoreTests(unittest.TestCase):
    def _run(self, tail_set: dict, eval_report: dict, solved_report: dict | None = None):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tail_set_path = tmp / "tail_set.json"
            eval_path = tmp / "eval.json"
            solved_path = tmp / "solved.json"
            out_path = tmp / "report.json"
            _write_json(tail_set_path, tail_set)
            _write_json(eval_path, eval_report)
            args = [
                PYTHON,
                str(PROJECT_ROOT / "scripts" / "tail_set_score.py"),
                "--tail-set",
                str(tail_set_path),
                "--eval",
                str(eval_path),
                "--output",
                str(out_path),
            ]
            if solved_report is not None:
                _write_json(solved_path, solved_report)
                args.extend(["--solved-set-report", str(solved_path)])
            completed = subprocess.run(args, capture_output=True, text=True, check=True)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            return completed, report

    def test_solved_set_regression_blocks_promotion_candidate(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ]
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "regression",
                            "legacy_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"type": "age_range", "minimum_years": 30, "maximum_years": 45},
                            },
                        }
                    ],
                }
            ]
        }
        solved_report = {
            "family_summaries": {
                "age_range": {
                    "rows": [
                        {
                            "prompt": "2nd engineer between 30 and 45 years old",
                            "expected_primary_family": "age_range",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                }
            }
        }

        completed, report = self._run(tail_set, eval_report, solved_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["solved_set"]["regressions"], 1)
        self.assertEqual(report["rescue_by_family"]["age_range"]["solved_set_regressions"], 1)
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "hold")
        self.assertEqual(report["promote_candidates"], [])

    def test_missing_solved_set_report_holds_promotion_candidates(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ]
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "equivalent",
                            "legacy_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"type": "age_range", "minimum_years": 30, "maximum_years": 45},
                            },
                        }
                    ],
                }
            ]
        }

        completed, report = self._run(tail_set, eval_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(report["solved_set_gate_active"])
        self.assertEqual(report["solved_set"]["regressions"], 0)
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "hold")
        self.assertEqual(report["promote_candidates"], [])

    def test_global_solved_set_regression_blocks_all_promotion_candidates(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ],
                "controls": [
                    {
                        "prompt": "good candidate",
                        "expected_primary_family": "unsupported_ok",
                        "expected_constraint": {},
                        "current_parser": "unsupported_ok",
                    }
                ],
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "equivalent",
                            "legacy_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"type": "age_range", "minimum_years": 30, "maximum_years": 45},
                            },
                        }
                    ],
                },
                {
                    "prompt": "good candidate",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [],
                    "comparison_results": [],
                },
                {
                    "prompt": "2nd engineer with valid passport and visa",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "passport_validity", "status": "applied", "mode": "required"},
                        {"family": "us_visa", "status": "applied", "mode": "required"},
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "regression",
                            "legacy_record": {
                                "family": "passport_validity",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "passport_validity",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"required": True, "must_be_valid": True},
                            },
                        },
                        {
                            "comparison_outcome": "equivalent",
                            "legacy_record": {
                                "family": "us_visa",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "us_visa",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"required": True, "must_be_valid": True},
                            },
                        },
                    ],
                },
            ]
        }
        solved_report = {
            "family_summaries": {
                "age_range": {
                    "rows": [
                        {
                            "prompt": "2nd engineer between 30 and 45 years old",
                            "expected_primary_family": "age_range",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                },
                "passport_validity": {
                    "rows": [
                        {
                            "prompt": "2nd engineer with valid passport and visa",
                            "expected_primary_family": "passport_validity",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                },
            }
        }

        completed, report = self._run(tail_set, eval_report, solved_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["solved_set"]["regressions"], 1)
        self.assertEqual(report["promote_candidates"], [])
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "hold")

    def test_empty_comparison_results_count_as_regression(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ]
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [],
                }
            ]
        }
        solved_report = {
            "family_summaries": {
                "age_range": {
                    "rows": [
                        {
                            "prompt": "2nd engineer between 30 and 45 years old",
                            "expected_primary_family": "age_range",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                }
            }
        }

        completed, report = self._run(tail_set, eval_report, solved_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["solved_set"]["regressions"], 1)
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "hold")
        self.assertEqual(report["promote_candidates"], [])

    def test_clean_solved_set_allows_promotion_candidate(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ],
                "controls": [
                    {
                        "prompt": "good candidate",
                        "expected_primary_family": "unsupported_ok",
                        "expected_constraint": {},
                        "current_parser": "unsupported_ok",
                    }
                ],
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "equivalent",
                            "legacy_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"type": "age_range", "minimum_years": 30, "maximum_years": 45},
                            },
                        }
                    ],
                },
                {
                    "prompt": "good candidate",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [],
                    "comparison_results": [],
                },
            ]
        }
        solved_report = {
            "family_summaries": {
                "age_range": {
                    "rows": [
                        {
                            "prompt": "2nd engineer between 30 and 45 years old",
                            "expected_primary_family": "age_range",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                }
            }
        }

        completed, report = self._run(tail_set, eval_report, solved_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["solved_set"]["regressions"], 0)
        self.assertEqual(report["rescue_by_family"]["age_range"]["solved_set_regressions"], 0)
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "promote_candidate")
        self.assertEqual(report["promote_candidates"], ["age_range"])

    def test_expected_delta_and_legacy_missed_do_not_count_as_regressions(self):
        tail_set = {
            "families": {
                "age_range": [
                    {
                        "prompt": "2nd engineer between 30 and 45 years old",
                        "expected_primary_family": "age_range",
                        "expected_constraint": {"age_years": {"min_age": 30, "max_age": 45}},
                        "current_parser": "miss",
                    }
                ]
            }
        }
        eval_report = {
            "rows": [
                {
                    "prompt": "2nd engineer between 30 and 45 years old",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [
                        {"family": "age_range", "status": "applied", "mode": "required"}
                    ],
                    "comparison_results": [
                        {
                            "comparison_outcome": "expected_delta",
                            "legacy_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                            },
                            "llm_record": {
                                "family": "age_range",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"type": "age_range", "minimum_years": 30, "maximum_years": 45},
                            },
                        }
                    ],
                },
                {
                    "prompt": "with valid STCW basic safety",
                    "shadow_mode": "enabled",
                    "llm_plan": {"normalizer": {"name": "shadow"}},
                    "legacy_comparison_records": [],
                    "comparison_results": [
                        {
                            "comparison_outcome": "legacy_missed",
                            "legacy_record": None,
                            "llm_record": {
                                "family": "stcw_basic",
                                "status": "applied",
                                "mode": "required",
                                "normalized_payload": {"required": True, "must_be_valid": True},
                            },
                        }
                    ],
                },
            ]
        }
        solved_report = {
            "family_summaries": {
                "age_range": {
                    "rows": [
                        {
                            "prompt": "2nd engineer between 30 and 45 years old",
                            "expected_primary_family": "age_range",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                },
                "stcw_basic": {
                    "rows": [
                        {
                            "prompt": "with valid STCW basic safety",
                            "expected_primary_family": "stcw_basic",
                            "primary_family_matched": True,
                            "expected_family_present": True,
                        }
                    ]
                },
            }
        }

        completed, report = self._run(tail_set, eval_report, solved_report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["solved_set"]["regressions"], 0)
        self.assertEqual(report["rescue_by_family"]["age_range"]["solved_set_regressions"], 0)
        self.assertEqual(report["rescue_by_family"]["age_range"]["verdict"], "promote_candidate")
        self.assertIn("age_range", report["promote_candidates"])


if __name__ == "__main__":
    unittest.main()
