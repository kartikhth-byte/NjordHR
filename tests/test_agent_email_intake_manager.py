import base64
import configparser
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.config_store import AgentConfigStore
from agent.email_intake import OutlookEmailIntakeManager


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "" if payload is None else "json"

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, attachment_b64=None, attachments=None):
        self.attachment_b64 = attachment_b64
        self.attachments = attachments

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        if url.endswith("/me/mailFolders/inbox/childFolders"):
            return _FakeResponse(payload={"value": [{"id": "folder-resumes", "displayName": "NjordHR Resumes"}]})
        if url.endswith("/me/mailFolders/folder-resumes/messages"):
            return _FakeResponse(payload={"value": [{
                "id": "msg-1",
                "subject": "Application for Chief Officer",
                "bodyPreview": "Please find my resume attached",
                "receivedDateTime": "2026-04-26T10:00:00Z",
                "hasAttachments": True,
                "internetMessageId": "<msg-1@example.com>",
                "from": {"emailAddress": {"address": "candidate@example.com"}},
            }]})
        if url.endswith("/me/messages/msg-1/attachments"):
            if self.attachments is not None:
                return _FakeResponse(payload={"value": self.attachments})
            return _FakeResponse(payload={"value": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "att-1",
                "name": "chief-officer-cv.pdf",
                "isInline": False,
                "contentBytes": self.attachment_b64,
            }]})
        raise AssertionError(f"Unexpected request: {method} {url}")


class _FakeAuth:
    def __init__(self, token="token-123"):
        self.token = token

    def acquire_access_token(self):
        return self.token


class _FakeAnalyzer:
    RANK_ALIAS_TABLE = {
        "chief officer": {"canonical_id": "chief_officer", "department": "deck", "seniority_bucket": "senior_officer"},
        "2nd engg": {"canonical_id": "2nd_engineer", "department": "engine", "seniority_bucket": "senior_officer"},
        "ordinary seaman": {"canonical_id": "os", "department": "deck", "seniority_bucket": "rating"},
        "wiper": {"canonical_id": "wiper", "department": "engine", "seniority_bucket": "rating"},
        "ship management": {"canonical_id": "ship_management", "department": "shore", "seniority_bucket": "management"},
    }

    def _normalize_rank_label(self, raw_rank):
        import re
        normalized = str(raw_rank or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _normalize_rank(self, raw_rank):
        normalized = self._normalize_rank_label(raw_rank)
        if normalized == "chief officer":
            return "chief_officer", "deck", "senior_officer", 1.0
        if normalized == "2nd engineer":
            return "2nd_engineer", "engine", "senior_officer", 1.0
        if normalized == "wiper":
            return "wiper", "engine", "rating", 1.0
        if normalized == "os":
            return "os", "deck", "rating", 1.0
        if normalized == "ship management":
            return "ship_management", "shore", "management", 1.0
        return None, None, None, None

    def _extract_rank_fact_from_text(self, text):
        if "Chief Officer" in str(text):
            return {
                "canonical_id": "chief_officer",
                "source_label": "applied_for_rank",
                "status": "PARSED",
            }
        return {"canonical_id": None, "source_label": None, "status": "MISSING"}


class OutlookEmailIntakeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AgentConfigStore(path=os.path.join(self.temp_dir.name, "agent.json"))
        self.store.update({
            "download_folder": os.path.join(self.temp_dir.name, "downloads"),
            "email_intake_mailbox": "recruitment@njordships.com",
            "outlook_connected_account": "recruitment@njordships.com",
        })
        parser = configparser.ConfigParser()
        parser["Ranks"] = {"rank_options": "Chief Officer\n2nd Engineer\nWiper\nOS"}
        self.parser = parser
        pdf_bytes = b"%PDF-1.4 test pdf bytes"
        self.http_client = _FakeHttpClient(base64.b64encode(pdf_bytes).decode("ascii"))
        self.manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=self.http_client,
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: "Present Rank: Chief Officer"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fetch_from_outlook_imports_into_rank_folder_and_tracks_registry(self):
        logs = []
        result = self.manager.fetch_from_outlook(lambda kind, message: logs.append((kind, message)))
        self.assertTrue(result["success"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["manual_review"], 0)
        target_dir = os.path.join(self.temp_dir.name, "downloads", "Chief_Officer")
        self.assertTrue(os.path.isdir(target_dir))
        saved = [name for name in os.listdir(target_dir) if name.lower().endswith(".pdf")]
        self.assertEqual(len(saved), 1)
        registry_path = os.path.join(self.temp_dir.name, "state", "email_intake_registry.json")
        self.assertTrue(os.path.exists(registry_path))
        self.assertTrue(any("Imported:" in line for _kind, line in logs))

        second = self.manager.fetch_from_outlook(lambda *_args: None)
        self.assertTrue(second["success"])
        self.assertEqual(second["duplicates"], 1)

    def test_classify_rank_prefers_subject_and_normalizes_double_pdf_suffix(self):
        result = self.manager._classify_rank(
            subject="Application for 2ND ENGG",
            body_preview="Please review attached CV",
            attachment_name="MANISH RESUME .pdf",
            pdf_text="",
        )
        self.assertEqual(result["outcome"], "classified")
        self.assertEqual(result["rank"], "2nd Engineer")
        self.assertEqual(
            self.manager._final_pdf_name(
                {"id": "msg-123", "receivedDateTime": "2026-04-26T10:00:00Z"},
                "Tandel_Mintesh_Wiper_CV.pdf.pdf",
            ),
            "EMAIL_20260426_100000_msg-123_Tandel_Mintesh_Wiper_CV.pdf",
        )

    def test_classify_rank_uses_configured_label_matches_for_wiper(self):
        result = self.manager._classify_rank(
            subject="Application for position of wiper",
            body_preview="Attached resume",
            attachment_name="resume.pdf",
            pdf_text="",
        )
        self.assertEqual(result["outcome"], "classified")
        self.assertEqual(result["rank"], "Wiper")

    def test_classify_rank_subject_beats_lower_priority_body_conflict(self):
        result = self.manager._classify_rank(
            subject="Application for position of wiper",
            body_preview="Worked as oiler and fitter on prior vessels",
            attachment_name="resume.pdf",
            pdf_text="",
        )
        self.assertEqual(result["outcome"], "classified")
        self.assertEqual(result["rank"], "Wiper")

    def test_classify_rank_subject_alias_maps_ordinary_seaman_to_os(self):
        result = self.manager._classify_rank(
            subject="Application for Deck Rating (Ordinary Seaman)",
            body_preview="Attached CV",
            attachment_name="Sahil_Tandel_TR_Seaman_Resume.pdf.pdf",
            pdf_text="",
        )
        self.assertEqual(result["outcome"], "classified")
        self.assertEqual(result["rank"], "OS")

    def test_short_rank_labels_do_not_match_body_text_directly(self):
        result = self.manager._classify_rank(
            subject="Application for Trainee ordinary seaman",
            body_preview="General purpose crew experience with gp duties mentioned in profile",
            attachment_name="resume.pdf",
            pdf_text="",
        )
        self.assertEqual(result["outcome"], "classified")
        self.assertEqual(result["rank"], "OS")

    def test_resolve_soffice_path_prefers_path_binary(self):
        with mock.patch("agent.email_intake.shutil.which", return_value="/usr/local/bin/soffice"):
            self.assertEqual(self.manager._resolve_soffice_path(), "/usr/local/bin/soffice")

    def test_resolve_soffice_path_detects_mac_app_bundle(self):
        app_path = str(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
        with mock.patch("agent.email_intake.shutil.which", return_value=""):
            with mock.patch("agent.email_intake.Path.is_file", autospec=True) as is_file:
                with mock.patch("agent.email_intake.os.access", return_value=True):
                    is_file.side_effect = lambda path_obj: str(path_obj) == app_path
                    self.assertEqual(self.manager._resolve_soffice_path(), app_path)

    def test_resolve_soffice_path_prefers_bundled_converter_dir(self):
        bundled_root = os.path.join(self.temp_dir.name, "converter")
        bundled_program = os.path.join(bundled_root, "program")
        os.makedirs(bundled_program, exist_ok=True)
        bundled_soffice = os.path.join(bundled_program, "soffice")
        Path(bundled_soffice).write_text("", encoding="utf-8")

        with mock.patch.dict(os.environ, {"NJORDHR_BUNDLED_CONVERTER_DIR": bundled_root}, clear=False):
            with mock.patch("agent.email_intake.os.access", return_value=True):
                with mock.patch("agent.email_intake.shutil.which", return_value="/usr/local/bin/soffice"):
                    self.assertEqual(self.manager._resolve_soffice_path(), bundled_soffice)
                    status = self.manager.converter_status()
                    self.assertTrue(status["converter_available"])
                    self.assertEqual(status["converter_source"], "bundled")

    def test_fetch_from_outlook_keeps_unique_names_for_same_message_pdf_and_docx(self):
        pdf_bytes = base64.b64encode(b"%PDF-1.4 sibling pdf").decode("ascii")
        docx_bytes = base64.b64encode(b"docx-bytes").decode("ascii")
        attachments = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "att-pdf",
                "name": "BABLU WIPER NEW CV.pdf",
                "isInline": False,
                "contentBytes": pdf_bytes,
            },
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "att-docx",
                "name": "BABLU WIPER NEW CV.docx",
                "isInline": False,
                "contentBytes": docx_bytes,
            },
        ]
        manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=_FakeHttpClient(attachments=attachments),
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        manager.resume_extractor.extract_text_from_pdf = lambda _path: ""

        converted_pdf = Path(self.temp_dir.name) / "converted.pdf"
        converted_pdf.write_bytes(b"%PDF-1.4 converted docx")
        with mock.patch.object(manager, "_convert_to_pdf", return_value=(converted_pdf, "")):
            result = manager.fetch_from_outlook(lambda *_args: None)

        self.assertTrue(result["success"])
        self.assertEqual(result["imported"], 2)
        target_dir = Path(self.temp_dir.name) / "downloads" / "Chief_Officer"
        saved = sorted(path.name for path in target_dir.glob("*.pdf"))
        self.assertEqual(len(saved), 2)
        self.assertNotEqual(saved[0], saved[1])
        self.assertTrue(saved[0].startswith("EMAIL_20260426_100000_msg-1_BABLU_WIPER_NEW_CV"))
        self.assertTrue(saved[1].startswith("EMAIL_20260426_100000_msg-1_BABLU_WIPER_NEW_CV"))

    def test_classify_rank_routes_non_configured_resolved_rank_to_manual_review(self):
        with mock.patch.object(
            self.manager,
            "_configured_rank_map",
            return_value=(
                {"ship_management": "Ship Management"},
                {"ship management": "Ship Management"},
                {"Chief Officer", "2nd Engineer", "Wiper", "OS"},
            ),
        ):
            result = self.manager._classify_rank(
                subject="Application for Ship Management",
                body_preview="Attached CV",
                attachment_name="resume.pdf",
                pdf_text="",
            )
        self.assertEqual(result["outcome"], "manual_review")
        self.assertEqual(result["rank"], "")
        self.assertIn("allowlist", result["reason"])

    def test_manual_review_summary_and_list_reads_pending_items_from_disk(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        first_pdf = manual_dir / "EMAIL_20260401_080822_item1.pdf"
        second_pdf = manual_dir / "EMAIL_20260427_093105_item2.pdf"
        first_pdf.write_bytes(b"%PDF-1.4 first")
        second_pdf.write_bytes(b"%PDF-1.4 second")
        first_sidecar = {
            "mail_sender": "first@example.com",
            "mail_subject": "First Subject",
            "received_at": "2026-04-01T08:08:22Z",
            "rank_reason": "First reason",
            "attachment_name": "First.docx",
            "candidate_name": "First Candidate",
        }
        second_sidecar = {
            "mail_sender": "second@example.com",
            "mail_subject": "Second Subject",
            "received_at": "2026-04-27T09:31:05Z",
            "rank_reason": "Second reason",
            "attachment_name": "Second.docx",
            "candidate_name": "Second Candidate",
            "rank_evidence": [{"source": "subject", "rank": "OS"}],
        }
        Path(f"{first_pdf}.json").write_text(json.dumps(first_sidecar), encoding="utf-8")
        Path(f"{second_pdf}.json").write_text(json.dumps(second_sidecar), encoding="utf-8")

        summary = self.manager.manual_review_summary()
        items = self.manager.list_manual_review_items()

        self.assertEqual(summary["pending_count"], 2)
        self.assertEqual(summary["latest_item_name"], second_pdf.name)
        self.assertEqual(items[0]["pdf_filename"], second_pdf.name)
        self.assertEqual(items[0]["candidate_name"], "Second Candidate")
        self.assertEqual(items[0]["mail_sender"], "second@example.com")
        self.assertIn("subject: OS", items[0]["rank_evidence_summary"])

    def test_get_manual_review_item_returns_sidecar_detail(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "candidate@example.com",
            "mail_subject": "Document from Tauhid",
            "received_at": "2026-04-27T05:07:35Z",
            "rank_reason": "Could not determine a confident role",
            "attachment_name": "Md Tauhid-1.pdf",
            "candidate_name": "Md Tauhid",
            "rank_evidence": [{"source": "filename", "rank": "OS"}],
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["id"], pdf_path.name)
        self.assertEqual(item["mail_subject"], "Document from Tauhid")
        self.assertEqual(item["original_attachment_name"], "Md Tauhid-1.pdf")
        self.assertEqual(item["candidate_name"], "Md Tauhid")
        self.assertEqual(item["candidate_name_source"], "SIDECAR_STORED")
        self.assertEqual(item["candidate_name_confidence"], "low")
        self.assertEqual(item["review_reason"], "Could not determine a confident role")
        self.assertEqual(item["rank_evidence"][0]["rank"], "OS")

    def test_get_manual_review_item_backfills_candidate_name_from_pdf_when_missing_in_sidecar(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_033730_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "tandelsahil2001@gmail.com",
            "mail_subject": "TR. Seaman – 14 Months Sailing Experience – Sahil Tandel",
            "received_at": "2026-04-27T03:37:30Z",
            "rank_reason": "Could not determine a confident role",
            "attachment_name": "Sahil_Tandel_TR_Seaman_Resume.pdf.pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: "NAME : SAHILKUMAR KANTILAL TANDEL\nEmail ID : tandelsahil2001@gmail.com"

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Sahilkumar Kantilal Tandel")
        self.assertEqual(item["candidate_name_source"], "STRUCTURED_FIELD")
        self.assertEqual(item["candidate_name_confidence"], "high")

    def test_get_manual_review_item_backfills_candidate_name_from_compressed_pdf_text(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260401_080822_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "santanu951995@gmail.com",
            "mail_subject": "Fwd: Apply for messman",
            "received_at": "2026-04-01T08:08:22Z",
            "rank_reason": "Could not determine a confident role",
            "attachment_name": "Copy2-SANTANU cv new 2025 1.pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "ResumePostforapplying.:2ndcook,/messman"
            "Name.:Santanukhamrai"
            "Father’sName:Swapankhamrai"
            "D.O.B:09/05/1995"
            "EmailId:santanu951995@gmail.com"
        )

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Santanukhamrai")

    def test_get_manual_review_item_backfills_candidate_name_from_header_title(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_094516_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "francisvishal1@gmail.com",
            "mail_subject": "Application for Engine Cadet / TME – Francis Vishal A",
            "received_at": "2026-04-27T09:45:16Z",
            "rank_reason": "Could not determine a confident role",
            "attachment_name": "FRANCIS_VISHAL_A_CV.pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "EXECUTIVE SUMMARY: B.E Marine Engineering Student at AMET University. "
            "FRANCIS VISHAL A CONTACT: +91 8838106679 "
            "E-MAIL: francisvishal1@gmail.com"
        )

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Francis Vishal A")
        self.assertEqual(item["candidate_name_source"], "HEADER_IDENTITY")
        self.assertEqual(item["candidate_name_confidence"], "high")

    def test_get_manual_review_item_prefers_subject_fallback_when_pdf_name_is_noise(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260429_045751_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "candidate_name": "Flag Type Me Power",
            "mail_sender": "ruslan.podkopaiev@gmail.com",
            "mail_subject": "CV AB/Rigger/Roustabout Ruslan Podkopaiev",
            "received_at": "2026-04-29T10:27:00Z",
            "rank_reason": "Conflicting rank evidence detected in subject: AB, Roustabout, Rigger.",
            "attachment_name": "CV Rigger-AB-Roustabout Ruslan Podkopaiev.pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "Ruslan Podkopaiev, 29 Rigger / Roustabout / AB Seaman\n"
            "Main Information\n"
            "PERSONAL DATA\n"
            "Flag Type ME Power"
        )

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Ruslan Podkopaiev")

    def test_get_manual_review_item_backfills_candidate_name_from_subject_when_pdf_form_values_are_missing(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260430_062833_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "alociusj@yahoo.com",
            "mail_subject": "oiler/motorman cv of jilmon alocius with good experience on all type of tankers",
            "received_at": "2026-04-30T06:28:33Z",
            "rank_reason": "Conflicting rank evidence detected in subject: Motorman, Oiler.",
            "attachment_name": "jilmon alocius oiler .pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "APPLICATION FORM\n"
            "Surname:\n"
            "Name:\n"
            "Father's name:\n"
            "Date of birth:\n"
        )

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Jilmon Alocius")
        self.assertEqual(item["candidate_name_source"], "SUBJECT_IDENTITY")
        self.assertEqual(item["candidate_name_confidence"], "medium")

    def test_get_manual_review_item_backfills_candidate_name_from_subject_when_pdf_text_is_spaced_or_weak(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260428_124251_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 detail")
        sidecar = {
            "mail_sender": "arpit.jajoo001@gmail.com",
            "mail_subject": "Arpit Jajoo-2E",
            "received_at": "2026-04-28T12:42:51Z",
            "rank_reason": "Conflicting rank evidence detected in subject/body.",
            "attachment_name": "arpit cv2.pdf",
        }
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        self.manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "A R P I T J A J O O E - m a i l : arpit.jajoo001@gmail.com "
            "CURRICULUM VITAE FOR SECOND ENGINEER"
        )

        item = self.manager.get_manual_review_item(pdf_path.name)

        self.assertEqual(item["candidate_name"], "Arpit Jajoo")
        self.assertEqual(item["candidate_name_source"], "HEADER_IDENTITY")
        self.assertEqual(item["candidate_name_confidence"], "high")

    def test_derive_candidate_name_details_prefers_structured_field_over_subject(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text="Name : Debashish Mohapatra\nEmail ID : debashishmohapatra306@gmail.com",
            mail_subject="CV of Some Other Person",
            mail_sender="other.person@example.com",
            attachment_name="resume.pdf",
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Debashish Mohapatra")
        self.assertEqual(details["source"], "STRUCTURED_FIELD")
        self.assertEqual(details["confidence"], "high")

    def test_derive_candidate_name_details_prefers_subject_over_sender_and_filename(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text="APPLICATION FORM\nSurname:\nName:\nFather's name:\nDate of birth:\n",
            mail_subject="oiler/motorman cv of jilmon alocius with good experience on all type of tankers",
            mail_sender="alociusj@yahoo.com",
            attachment_name="jilmon alocius oiler .pdf",
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Jilmon Alocius")
        self.assertEqual(details["source"], "SUBJECT_IDENTITY")
        self.assertEqual(details["confidence"], "medium")

    def test_derive_candidate_name_details_uses_sender_before_filename_for_weak_documents(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text="Email ID : mdtauhid@example.com",
            mail_subject="Forwarded document",
            mail_sender="md.tauhid@example.com",
            attachment_name="candidate resume final.pdf",
            quality_state="WEAK_BUT_USABLE",
        )

        self.assertEqual(details["candidate_name"], "Md Tauhid")
        self.assertEqual(details["source"], "SENDER_IDENTITY")
        self.assertEqual(details["confidence"], "low")

    def test_derive_candidate_name_details_extracts_top_line_name_before_role_and_contact(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text=(
                "Varun Rakesh Patel Marine Engineer (MEO Class IV) +91 9925224354 | "
                "varun_00766@yahoo.com | pvarun708@gmail.com"
            ),
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Varun Rakesh Patel")
        self.assertEqual(details["source"], "HEADER_IDENTITY")
        self.assertEqual(details["confidence"], "high")

    def test_derive_candidate_name_details_extracts_uppercase_name_before_id_number(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text=(
                "* Tolani Maritime InstituteDURVANG BHAGAT ID No : 2021NS178 "
                "Date of Birth : 02nd APRIL 2003 E-Mail ID : durvang.bhagat2021ns@gmail.com"
            ),
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Durvang Bhagat")
        self.assertEqual(details["source"], "HEADER_IDENTITY")
        self.assertEqual(details["confidence"], "high")

    def test_derive_candidate_name_details_extracts_standalone_name_line_before_role_and_contact(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text=(
                "Varun Rakesh Patel\n"
                "Marine Engineer (MEO Class IV)\n"
                "+91 9925224354 | varun_00766@yahoo.com | pvarun708@gmail.com"
            ),
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Varun Rakesh Patel")
        self.assertEqual(details["source"], "HEADER_IDENTITY")
        self.assertEqual(details["confidence"], "high")

    def test_derive_candidate_name_details_extracts_letter_spaced_header_with_adjacent_email(self):
        details = self.manager._derive_candidate_name_details(
            pdf_text=(
                "A R P I T J A J O O E - m a i l : a r p i t . j a j o o 0 0 1 @ g m a i l . c o m "
                "C o n t a c t N o . : + 9 1 - 8 0 1 5 5 7 0 9 4 1"
            ),
            quality_state="READABLE",
        )

        self.assertEqual(details["candidate_name"], "Arpit Jajoo")
        self.assertEqual(details["source"], "HEADER_IDENTITY")
        self.assertEqual(details["confidence"], "high")

    def test_move_manual_review_item_moves_pdf_and_sidecar_into_selected_role(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 move me")
        sidecar = {"mail_subject": "Document from Tauhid", "attachment_name": "Md Tauhid-1.pdf"}
        Path(f"{pdf_path}.json").write_text(json.dumps(sidecar), encoding="utf-8")

        result = self.manager.move_manual_review_item(pdf_path.name, "Chief Officer")

        destination_pdf = Path(result["destination_pdf_path"])
        destination_sidecar = Path(result["destination_sidecar_path"])
        self.assertTrue(result["success"])
        self.assertEqual(result["selected_role"], "Chief Officer")
        self.assertTrue(destination_pdf.exists())
        self.assertTrue(destination_sidecar.exists())
        self.assertFalse(pdf_path.exists())
        self.assertFalse(Path(f"{pdf_path}.json").exists())
        self.assertEqual(destination_pdf.parent.name, "Chief_Officer")
        self.assertEqual(result["pending_count"], 0)

    def test_move_manual_review_item_rejects_role_outside_allowlist(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 move me")

        with self.assertRaises(RuntimeError) as ctx:
            self.manager.move_manual_review_item(pdf_path.name, "Not A Configured Role")
        self.assertIn("allowlist", str(ctx.exception))

    def test_move_manual_review_item_uses_collision_safe_suffixing(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        target_dir = Path(self.temp_dir.name) / "downloads" / "Chief_Officer"
        target_dir.mkdir(parents=True, exist_ok=True)
        existing_pdf = target_dir / "EMAIL_20260427_050735_item.pdf"
        existing_pdf.write_bytes(b"%PDF-1.4 existing")
        Path(f"{existing_pdf}.json").write_text("{}", encoding="utf-8")

        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 move me")
        Path(f"{pdf_path}.json").write_text("{}", encoding="utf-8")

        result = self.manager.move_manual_review_item(pdf_path.name, "Chief Officer")

        self.assertTrue(result["destination_pdf_path"].endswith("_1.pdf"))

    def test_fetch_from_outlook_skips_existing_manual_review_duplicate_on_disk(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)

        existing_pdf = manual_dir / "EMAIL_20260426_100000_msg-1_resume.pdf"
        existing_bytes = b"%PDF-1.4 existing manual review"
        existing_pdf.write_bytes(existing_bytes)
        existing_sidecar = {
            "mail_subject": "Application for Chief Officer",
            "mail_sender": "candidate@example.com",
            "received_at": "2026-04-26T10:00:00Z",
            "message_id": "msg-1",
            "internet_message_id": "<msg-1@example.com>",
            "attachment_id": "att-1",
            "attachment_name": "chief-officer-cv.pdf",
            "rank_outcome": "manual_review",
            "rank_reason": "Weak role evidence",
            "connected_account": "recruitment@njordships.com",
            "pdf_checksum": hashlib.sha256(existing_bytes).hexdigest(),
        }
        Path(f"{existing_pdf}.json").write_text(json.dumps(existing_sidecar), encoding="utf-8")

        manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=self.http_client,
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        manager.resume_extractor.extract_text_from_pdf = lambda _path: ""

        with mock.patch.object(
            manager,
            "_classify_rank",
            return_value={
                "outcome": "manual_review",
                "rank": "",
                "reason": "Weak role evidence",
                "evidence": [],
            },
        ):
            result = manager.fetch_from_outlook(lambda *_args: None)

        self.assertTrue(result["success"])
        self.assertEqual(result["manual_review"], 0)
        self.assertEqual(result["duplicates"], 1)
        saved = sorted(path.name for path in manual_dir.glob("*.pdf"))
        self.assertEqual(saved, [existing_pdf.name])

    def test_fetch_from_outlook_stores_candidate_name_in_manual_review_sidecar(self):
        manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=self.http_client,
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        manager.resume_extractor.extract_text_from_pdf = lambda _path: "Name : Debashish Mohapatra\nEmail ID : debashishmohapatra306@gmail.com"

        with mock.patch.object(
            manager,
            "_classify_rank",
            return_value={
                "outcome": "manual_review",
                "rank": "",
                "reason": "Weak role evidence",
                "evidence": [],
            },
        ):
            result = manager.fetch_from_outlook(lambda *_args: None)

        self.assertTrue(result["success"])
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        sidecars = sorted(manual_dir.glob("*.pdf.json"))
        self.assertEqual(len(sidecars), 1)
        sidecar = json.loads(sidecars[0].read_text(encoding="utf-8"))
        self.assertEqual(sidecar["candidate_name"], "Debashish Mohapatra")
        self.assertEqual(sidecar["candidate_name_source"], "STRUCTURED_FIELD")
        self.assertEqual(sidecar["candidate_name_confidence"], "high")
        self.assertEqual(sidecar["quality_state"], "WEAK_BUT_USABLE")

    def test_should_skip_non_resume_attachment_for_internal_proposal_certificate(self):
        should_skip, reason = self.manager._should_skip_non_resume_attachment(
            subject="REG: FINAL PROPOSAL - CMA CGM VITORIA - REEFERMAN VIVEK YADAV",
            sender="subhiksha@njordships.com",
            attachment_name="certificates-for-printing-2026-04-29.pdf",
            pdf_text="CES Test Certificate\nIssue Date: 29/04/2026\nResult: PASS",
        )

        self.assertTrue(should_skip)
        self.assertIn("proposal/certificate", reason.lower())

    def test_fetch_from_outlook_skips_internal_proposal_certificate_before_manual_review(self):
        pdf_bytes = base64.b64encode(b"%PDF-1.4 certificate").decode("ascii")
        attachments = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-1",
            "name": "certificates-for-printing-2026-04-29.pdf",
            "isInline": False,
            "contentBytes": pdf_bytes,
        }]
        manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=_FakeHttpClient(attachments=attachments),
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        manager.resume_extractor.extract_text_from_pdf = lambda _path: "CES Test Certificate\nIssue Date: 29/04/2026\nResult: PASS"
        manager._list_messages = lambda _token, _folder_id: [{
            "id": "msg-1",
            "subject": "REG: FINAL PROPOSAL - CMA CGM VITORIA - REEFERMAN VIVEK YADAV",
            "bodyPreview": "Please find test certificate attached",
            "receivedDateTime": "2026-04-29T12:50:54Z",
            "hasAttachments": True,
            "internetMessageId": "<msg-1@example.com>",
            "from": {"emailAddress": {"address": "subhiksha@njordships.com"}},
        }]

        logs = []
        result = manager.fetch_from_outlook(lambda kind, message: logs.append((kind, message)))

        self.assertTrue(result["success"])
        self.assertEqual(result["manual_review"], 0)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(any("Skipped non-resume attachment" in line for _kind, line in logs))
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        self.assertFalse(any(manual_dir.glob("*.pdf")))

    def test_fetch_from_outlook_routes_unreadable_binary_like_pdf_to_failed(self):
        pdf_bytes = base64.b64encode(b"%PDF-1.4 unreadable").decode("ascii")
        attachments = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-1",
            "name": "2ND OFF JAVED - Copy.pdf",
            "isInline": False,
            "contentBytes": pdf_bytes,
        }]
        manager = OutlookEmailIntakeManager(
            self.store,
            _FakeAuth(),
            self.parser,
            http_client=_FakeHttpClient(attachments=attachments),
            analyzer_factory=lambda: _FakeAnalyzer(),
        )
        manager.resume_extractor.extract_text_from_pdf = lambda _path: (
            "0M8R4KGxGuEAAAAAAAAAAAAAAAAAAAPgADAP7/CQAGAAAAAAAAACAAAywAAAAAAAAA"
            + "/" * 400
            + "A" * 260
        )
        manager._list_messages = lambda _token, _folder_id: [{
            "id": "msg-1",
            "subject": "2nd off CV",
            "bodyPreview": "Please find my CV attached",
            "receivedDateTime": "2026-04-29T19:48:28Z",
            "hasAttachments": True,
            "internetMessageId": "<msg-1@example.com>",
            "from": {"emailAddress": {"address": "enriqueaferdinand@yahoo.com"}},
        }]

        logs = []
        result = manager.fetch_from_outlook(lambda kind, message: logs.append((kind, message)))

        self.assertTrue(result["success"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["manual_review"], 0)
        self.assertTrue(any("Failed unreadable extraction" in line for _kind, line in logs))
        failed_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_Failed"
        self.assertTrue(any(failed_dir.glob("*.pdf")))
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        self.assertFalse(any(manual_dir.glob("*.pdf")))

    def test_open_manual_review_item_opens_normalized_pdf(self):
        manual_dir = Path(self.temp_dir.name) / "downloads" / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 open me")

        with mock.patch.object(self.manager, "_open_path_in_system_viewer") as open_mock:
            result = self.manager.open_manual_review_item(pdf_path.name)

        open_mock.assert_called_once_with(pdf_path)
        self.assertTrue(result["success"])
        self.assertEqual(result["id"], pdf_path.name)
        self.assertEqual(result["pdf_path"], str(pdf_path))


if __name__ == "__main__":
    unittest.main()
