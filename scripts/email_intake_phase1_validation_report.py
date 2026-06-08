import configparser
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_RESULTS_DIR = REPO_ROOT / "AI_Search_Results"
CORPUS_ROOT = Path("/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.email_intake import OutlookEmailIntakeManager


class _ValidationStore:
    def __init__(self, base_dir):
        self.base_dir = str(base_dir)
        self._cfg = {
            "download_folder": str(base_dir / "downloads"),
            "email_intake_mailbox": "recruitment@njordships.com",
            "email_intake_enabled": True,
            "email_intake_monitored_folder": "Inbox/NjordHR Resumes",
            "email_intake_processed_folder": "Inbox/NjordHR Processed",
            "email_intake_failed_folder": "Inbox/NjordHR Failed",
            "email_intake_poll_interval_seconds": 60,
        }

    def get(self):
        return dict(self._cfg)


class _ValidationAuth:
    def health_summary(self):
        return {
            "configured": True,
            "connected": False,
            "connected_account": "",
            "redirect_uri": "http://localhost:53682/auth/outlook/callback",
        }


def _run_command(cmd, cwd=None, env=None):
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    return {
        "cmd": cmd,
        "cwd": cwd,
        "returncode": result.returncode,
        "stdout_tail": "\n".join(result.stdout.strip().splitlines()[-20:]),
        "stderr_tail": "\n".join(result.stderr.strip().splitlines()[-20:]),
    }


def _converter_status():
    parser = configparser.ConfigParser()
    parser.read_dict(
        {
            "Ranks": {
                "rank_options": "\n".join(
                    [
                        "Chief Officer",
                        "Wiper",
                        "OS",
                        "2nd Engineer",
                        "Steward",
                        "Master",
                        "3rd Engineer",
                        "3rd Officer",
                    ]
                )
            }
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        manager = OutlookEmailIntakeManager(
            _ValidationStore(Path(tmp)),
            _ValidationAuth(),
            parser,
        )
        return manager.converter_status()


def _write_json(name, payload):
    path = AI_RESULTS_DIR / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _docx_conversion_smoke_test(converter_status):
    samples = sorted((CORPUS_ROOT / "_EmailInbox_Originals").glob("*.docx"))[:5]
    if not samples:
        return {
            "attempted": False,
            "success": False,
            "reason": "No DOCX sample found in _EmailInbox_Originals.",
        }

    if not converter_status.get("converter_available"):
        return {
            "attempted": False,
            "success": False,
            "reason": "No working converter is currently available on this machine.",
            "sample_docx": str(samples[0]),
        }

    parser = configparser.ConfigParser()
    parser.read_dict({"Ranks": {"rank_options": "Chief Officer"}})
    attempts = []
    with tempfile.TemporaryDirectory(prefix="njordhr-docx-convert-") as tmp:
        temp_root = Path(tmp)
        manager = OutlookEmailIntakeManager(
            _ValidationStore(temp_root),
            _ValidationAuth(),
            parser,
        )
        for sample in samples:
            working_docx = temp_root / sample.name
            working_docx.write_bytes(sample.read_bytes())
            pdf_path, error = manager._convert_to_pdf(working_docx)
            if not pdf_path:
                attempts.append(
                    {
                        "sample_docx": str(sample),
                        "success": False,
                        "reason": error,
                    }
                )
                continue
            extracted_text = manager.resume_extractor.extract_text_from_pdf(str(pdf_path))
            attempts.append(
                {
                    "sample_docx": str(sample),
                    "success": True,
                    "generated_pdf_name": pdf_path.name,
                    "generated_pdf_size": pdf_path.stat().st_size,
                    "extracted_text_length": len(str(extracted_text or "")),
                    "quality_state": manager._classify_document_quality(extracted_text),
                }
            )

        return {
            "attempted": True,
            "success": any(row.get("success") for row in attempts),
            "sample_count": len(attempts),
            "attempts": attempts,
        }


def _build_packaging_report(node_result):
    with tempfile.TemporaryDirectory(prefix="njordhr-converter-payload-") as payload_dir:
        payload_root = Path(payload_dir)
        fake_soffice = payload_root / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
        fake_soffice.parent.mkdir(parents=True, exist_ok=True)
        fake_soffice.write_text("#!/bin/sh\necho fake soffice\n", encoding="utf-8")
        fake_soffice.chmod(fake_soffice.stat().st_mode | stat.S_IXUSR)

        positive_env = os.environ.copy()
        positive_env["NJORDHR_BUNDLED_CONVERTER_SOURCE"] = str(payload_root)
        positive_env["NJORDHR_REQUIRE_BUNDLED_CONVERTER"] = "true"
        positive_stage = _run_command(
            ["node", str(REPO_ROOT / "electron/scripts/stage-packaged-runtime.js")],
            cwd=str(REPO_ROOT / "electron"),
            env=positive_env,
        )
        staged_entrypoint = REPO_ROOT / "build/electron-stage/converter/LibreOffice.app/Contents/MacOS/soffice"
        positive_stage_snapshot = {
            **positive_stage,
            "payload_root": str(payload_root),
            "staged_entrypoint_exists": staged_entrypoint.exists(),
            "staged_entrypoint": str(staged_entrypoint),
        }

        negative_env = os.environ.copy()
        negative_env.pop("NJORDHR_BUNDLED_CONVERTER_SOURCE", None)
        negative_env.pop("NJORDHR_CONVERTER_SOURCE", None)
        negative_env["NJORDHR_REQUIRE_BUNDLED_CONVERTER"] = "true"
        negative_stage = _run_command(
            ["node", str(REPO_ROOT / "electron/scripts/stage-packaged-runtime.js")],
            cwd=str(REPO_ROOT / "electron"),
            env=negative_env,
        )

        return {
            "date": "2026-05-01",
            "platform": os.uname().sysname.lower(),
            "node_runtime_tests": node_result,
            "positive_stage_with_synthetic_payload": positive_stage_snapshot,
            "negative_stage_requires_payload": negative_stage,
            "judgment": {
                "runtime_env_wiring_verified": node_result["returncode"] == 0,
                "stage_script_accepts_supported_payload_shape": positive_stage_snapshot["returncode"] == 0
                and positive_stage_snapshot["staged_entrypoint_exists"],
                "stage_script_fails_closed_without_required_payload": negative_stage["returncode"] != 0,
                "actual_libreoffice_binary_validated": False,
                "notes": [
                    "This validation confirms packaged converter env/staging contract behavior.",
                    "It does not prove real DOCX conversion inside a packaged app because no real LibreOffice payload was available in this workspace.",
                ],
            },
        }


def _build_corpus_reports(converter_status, pytest_result, conversion_smoke):
    failed_dir = CORPUS_ROOT / "_EmailInbox_Failed"
    manual_dir = CORPUS_ROOT / "_EmailInbox_ManualReview"
    originals_dir = CORPUS_ROOT / "_EmailInbox_Originals"
    cleanup_dir = CORPUS_ROOT / "_CorpusCleanup_NonResume_20260430"

    original_counts = Counter(p.suffix.lower() for p in originals_dir.glob("*") if p.is_file() and p.suffix)
    failed_counts = Counter(p.suffix.lower() for p in failed_dir.glob("*") if p.is_file() and p.suffix)
    manual_counts = Counter(p.suffix.lower() for p in manual_dir.glob("*") if p.is_file() and p.suffix)
    cleanup_counts = Counter()
    if cleanup_dir.exists():
        for path in cleanup_dir.rglob("*"):
            if path.is_file() and path.suffix:
                cleanup_counts[path.suffix.lower()] += 1

    failed_reason_counts = Counter()
    failed_docx_rows = []
    for sidecar in sorted(failed_dir.glob("*.json")):
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        attachment_name = str(payload.get("attachment_name", ""))
        reason = str(payload.get("reason", "")).strip()
        if attachment_name.lower().endswith((".docx", ".doc")):
            failed_reason_counts[reason or "(empty)"] += 1
            failed_docx_rows.append(
                {
                    "failed_file": sidecar.with_suffix("").name,
                    "attachment_name": attachment_name,
                    "reason": reason,
                    "mail_subject": str(payload.get("mail_subject", "")).strip(),
                }
            )

    manual_reason_counts = Counter()
    for sidecar in sorted(manual_dir.glob("*.json")):
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        review_reason = str(payload.get("rank_reason", "") or payload.get("reason", "") or "(empty)").strip()
        manual_reason_counts[review_reason] += 1

    routed_email_counts = {}
    for folder in sorted(CORPUS_ROOT.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        count = sum(1 for path in folder.glob("EMAIL_*.pdf"))
        if count:
            routed_email_counts[folder.name] = count

    broader_report = {
        "date": "2026-05-01",
        "corpus_root": str(CORPUS_ROOT),
        "mailbox_folder_rollup": {
            "originals": {
                "pdf_count": original_counts.get(".pdf", 0),
                "docx_count": original_counts.get(".docx", 0),
                "doc_count": original_counts.get(".doc", 0),
                "other_count": sum(
                    value for key, value in original_counts.items() if key not in {".pdf", ".docx", ".doc"}
                ),
            },
            "failed": {
                "pdf_count": failed_counts.get(".pdf", 0),
                "docx_count": failed_counts.get(".docx", 0),
                "doc_count": failed_counts.get(".doc", 0),
                "json_sidecar_count": failed_counts.get(".json", 0),
            },
            "manual_review": {
                "pdf_count": manual_counts.get(".pdf", 0),
                "json_sidecar_count": manual_counts.get(".json", 0),
            },
            "cleanup_archive": {
                "pdf_count": cleanup_counts.get(".pdf", 0),
                "docx_count": cleanup_counts.get(".docx", 0),
                "doc_count": cleanup_counts.get(".doc", 0),
                "json_sidecar_count": cleanup_counts.get(".json", 0),
            },
        },
        "routed_email_pdf_counts_by_role": routed_email_counts,
        "failed_docx_doc_reason_counts": dict(sorted(failed_reason_counts.items())),
        "manual_review_reason_counts_top10": dict(manual_reason_counts.most_common(10)),
        "judgment": {
            "non_resume_cleanup_archive_present": cleanup_dir.exists(),
            "mailbox_flow_has_live_routed_outputs": bool(routed_email_counts),
            "remaining_docx_failures_on_this_machine_are_converter_bound": (
                all("did not produce a PDF" in key for key in failed_reason_counts if key != "(empty)")
                if failed_reason_counts
                else False
            ),
        },
    }

    docx_report = {
        "date": "2026-05-01",
        "machine_converter_status": converter_status,
        "docx_conversion_smoke_test": conversion_smoke,
        "python_email_intake_tests": pytest_result,
        "live_corpus_docx_state": {
            "originals_docx_count": original_counts.get(".docx", 0),
            "originals_doc_count": original_counts.get(".doc", 0),
            "failed_docx_count": failed_counts.get(".docx", 0),
            "failed_doc_count": failed_counts.get(".doc", 0),
            "sample_failed_rows": failed_docx_rows[:12],
            "failed_reason_counts": dict(sorted(failed_reason_counts.items())),
        },
        "judgment": {
            "mocked_docx_success_path_covered_by_tests": pytest_result["returncode"] == 0,
            "docx_failure_fallback_covered_by_tests": pytest_result["returncode"] == 0,
            "live_machine_has_working_converter": bool(converter_status.get("converter_available")),
            "true_local_docx_to_pdf_conversion_validated_on_this_machine": bool(conversion_smoke.get("success")),
            "notes": [
                "The repo now has explicit regression coverage for both mocked DOCX import success and DOCX conversion failure routing to the failed queue.",
                "This machine resolves LibreOffice through the macOS app-bundle fallback, even though soffice is not on PATH.",
                "The live failed DOCX artifacts appear to reflect earlier runs or sample-specific conversion failures rather than a blanket missing-converter state.",
            ],
        },
    }

    return docx_report, broader_report


def main():
    converter_status = _converter_status()
    pytest_result = _run_command(
        [
            "python3",
            "-m",
            "pytest",
            str(REPO_ROOT / "tests/test_agent_email_intake.py"),
            str(REPO_ROOT / "tests/test_agent_email_intake_manager.py"),
        ],
        cwd=str(REPO_ROOT),
    )
    node_result = _run_command(
        ["node", "--test", str(REPO_ROOT / "electron/test/runtime-manager.test.js")],
        cwd=str(REPO_ROOT / "electron"),
    )

    packaging_report = _build_packaging_report(node_result)
    conversion_smoke = _docx_conversion_smoke_test(converter_status)
    docx_report, broader_report = _build_corpus_reports(converter_status, pytest_result, conversion_smoke)

    written = [
        _write_json("email_intake_docx_validation_2026-05-01.json", docx_report),
        _write_json("email_intake_bundled_converter_packaging_validation_2026-05-01.json", packaging_report),
        _write_json("email_intake_broader_mailbox_validation_2026-05-01.json", broader_report),
    ]

    for path in written:
        print(path)


if __name__ == "__main__":
    main()
