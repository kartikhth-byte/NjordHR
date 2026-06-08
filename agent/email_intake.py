import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests

from rank_folders import rank_folder_path
from resume_extractor import ResumeExtractor


SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".doc", ".docx"}
CHECKSUM_RETENTION_SECONDS = 90 * 24 * 60 * 60
BUNDLED_CONVERTER_ENV = "NJORDHR_BUNDLED_CONVERTER_DIR"
RESUME_SIGNAL_PATTERNS = (
    r"\bcurriculum vitae\b",
    r"\bresume\b",
    r"\bcv\b",
    r"\bbio[-\s]?data\b",
    r"\bpersonal details\b",
    r"\bobjective\b",
    r"\bsea service\b",
    r"\bdocument details\b",
    r"\bpassport\b",
    r"\bcdc\b",
    r"\bindos\b",
    r"\bstcw\b",
    r"\bnationality\b",
    r"\bdate of birth\b",
    r"\bdob\b",
    r"\bmobile\b",
    r"\bcontact\b",
    r"\bemail\b",
    r"\bapplied for\b",
    r"\brank applied\b",
    r"\bposition applied\b",
)
NON_RESUME_ATTACHMENT_PATTERNS = (
    r"\bcertificate\b",
    r"\bcertificates\b",
    r"\btest certificate\b",
    r"\btest result\b",
    r"\bfor printing\b",
    r"\bces test\b",
)
SUPPORTING_DOCUMENT_ATTACHMENT_PATTERNS = (
    r"\bcertificate\b",
    r"\bcertificates\b",
    r"\bcoc\b",
    r"\bdce\b",
    r"\badvance dce\b",
    r"\bwk certificate\b",
)
PROPOSAL_CONTEXT_PATTERNS = (
    r"\bfinal proposal\b",
    r"\binitial proposal\b",
    r"\bproposal\b",
)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_segment(value, fallback="item"):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


class OutlookEmailIntakeManager:
    def __init__(self, settings_store, outlook_auth, parser, http_client=None, analyzer_factory=None):
        self.settings_store = settings_store
        self.outlook_auth = outlook_auth
        self.parser = parser
        self.http_client = http_client or requests
        self.analyzer_factory = analyzer_factory or self._default_analyzer_factory
        self.resume_extractor = ResumeExtractor()

    @property
    def _state_dir(self):
        path = os.path.join(self.settings_store.base_dir, "state")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def _registry_path(self):
        return os.path.join(self._state_dir, "email_intake_registry.json")

    def _load_registry(self):
        if not os.path.exists(self._registry_path):
            return {"processed_attachments": {}, "checksums": {}}
        try:
            with open(self._registry_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return {"processed_attachments": {}, "checksums": {}}
        return {
            "processed_attachments": dict(raw.get("processed_attachments") or {}),
            "checksums": dict(raw.get("checksums") or {}),
        }

    def _save_registry(self, registry):
        payload = {
            "processed_attachments": dict(registry.get("processed_attachments") or {}),
            "checksums": dict(registry.get("checksums") or {}),
        }
        with open(self._registry_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)

    def _prune_registry(self, registry):
        now = time.time()
        checksums = {}
        for checksum, ts in (registry.get("checksums") or {}).items():
            try:
                ts_value = float(ts)
            except (TypeError, ValueError):
                continue
            if (now - ts_value) <= CHECKSUM_RETENTION_SECONDS:
                checksums[checksum] = ts_value
        registry["checksums"] = checksums
        return registry

    def _graph_request(self, token, method, path, *, params=None, json_body=None):
        base = "https://graph.microsoft.com/v1.0"
        headers = {"Authorization": f"Bearer {token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        resp = self.http_client.request(
            method=method,
            url=f"{base}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=30,
        )
        payload = {}
        if resp.text:
            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": resp.text}
        if resp.status_code >= 400:
            message = ""
            if isinstance(payload, dict):
                message = (
                    ((payload.get("error") or {}).get("message"))
                    or payload.get("message")
                    or str(payload)[:300]
                )
            raise RuntimeError(f"Graph API {method} {path} failed ({resp.status_code}): {message or resp.text[:300]}")
        return payload if isinstance(payload, dict) else {}

    def _resolve_folder_id(self, token, folder_path):
        normalized = str(folder_path or "").strip()
        if not normalized:
            raise RuntimeError("email_intake_monitored_folder is not configured.")
        parts = [part.strip() for part in normalized.split("/") if part.strip()]
        if not parts:
            raise RuntimeError("email_intake_monitored_folder is invalid.")
        current_id = "inbox"
        current_path = "Inbox"
        start_index = 1 if parts[0].lower() == "inbox" else 0
        for segment in parts[start_index:]:
            payload = self._graph_request(
                token,
                "GET",
                f"/me/mailFolders/{current_id}/childFolders",
                params={"$top": 200, "$select": "id,displayName"},
            )
            rows = payload.get("value") or []
            match = next((row for row in rows if str(row.get("displayName", "")).strip().lower() == segment.lower()), None)
            if not match:
                raise RuntimeError(f"Outlook folder not found: {current_path}/{segment}")
            current_id = match["id"]
            current_path = f"{current_path}/{segment}"
        return current_id

    def _list_messages(self, token, folder_id):
        payload = self._graph_request(
            token,
            "GET",
            f"/me/mailFolders/{folder_id}/messages",
            params={
                "$top": 100,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,bodyPreview,receivedDateTime,hasAttachments,internetMessageId,from",
            },
        )
        return payload.get("value") or []

    def _list_file_attachments(self, token, message_id):
        payload = self._graph_request(
            token,
            "GET",
            f"/me/messages/{message_id}/attachments",
            params={"$top": 100},
        )
        rows = []
        for item in payload.get("value") or []:
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            if item.get("isInline"):
                continue
            name = str(item.get("name", "")).strip()
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_ATTACHMENT_EXTENSIONS:
                continue
            rows.append(item)
        return rows

    def _attachment_registry_key(self, message_id, attachment_id):
        return f"{message_id}:{attachment_id}"

    def _download_root(self):
        cfg = self.settings_store.get()
        root = str(cfg.get("download_folder", "")).strip()
        if not root:
            raise RuntimeError("download_folder is not configured.")
        path = Path(root)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _special_folder(self, folder_name):
        path = self._download_root() / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _manual_review_dir(self):
        return self._special_folder("_EmailInbox_ManualReview")

    def _write_bytes(self, folder, filename, content_bytes):
        folder.mkdir(parents=True, exist_ok=True)
        stem = _safe_segment(Path(filename).stem, "attachment")
        suffix = Path(filename).suffix.lower() or ".bin"
        candidate = folder / f"{stem}{suffix}"
        counter = 1
        while candidate.exists():
            candidate = folder / f"{stem}_{counter}{suffix}"
            counter += 1
        candidate.write_bytes(content_bytes)
        return candidate

    def _resolve_soffice_path(self):
        soffice, _source = self._resolve_soffice_details()
        return soffice

    def _resolve_bundled_soffice_path(self):
        root_value = str(os.getenv(BUNDLED_CONVERTER_ENV, "")).strip()
        if not root_value:
            return ""

        root = Path(root_value).expanduser()
        for candidate in self._bundled_soffice_candidates(root):
            if self._is_executable_file(candidate):
                return str(candidate)
        return ""

    def _bundled_soffice_candidates(self, root):
        executable_names = ("soffice.exe", "soffice.bin", "soffice")
        preferred = [
            root / "program" / "soffice.exe",
            root / "program" / "soffice",
            root / "program" / "soffice.bin",
            root / "LibreOffice" / "program" / "soffice.exe",
            root / "LibreOffice" / "program" / "soffice",
            root / "LibreOffice" / "program" / "soffice.bin",
            root / "LibreOffice.app" / "Contents" / "MacOS" / "soffice",
            root / "Contents" / "MacOS" / "soffice",
            root / "soffice.exe",
            root / "soffice",
        ]
        seen = set()
        for candidate in preferred:
            text = str(candidate)
            if text in seen:
                continue
            seen.add(text)
            yield candidate

        if root.is_dir():
            for name in executable_names:
                for candidate in sorted(root.rglob(name)):
                    text = str(candidate)
                    if text in seen:
                        continue
                    seen.add(text)
                    yield candidate

    def _system_app_bundle_soffice_candidates(self):
        return [
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]

    def _is_executable_file(self, candidate):
        return candidate.is_file() and os.access(candidate, os.X_OK)

    def _resolve_soffice_details(self):
        bundled = self._resolve_bundled_soffice_path()
        if bundled:
            return bundled, "bundled"

        candidate = shutil.which("soffice")
        if candidate:
            return candidate, "system_path"

        for bundle_path in self._system_app_bundle_soffice_candidates():
            if self._is_executable_file(bundle_path):
                return str(bundle_path), "system_app_bundle"
        return "", ""

    def _macos_app_bundle_root(self, soffice_path):
        path = Path(str(soffice_path or ""))
        parts = path.parts
        for index, part in enumerate(parts):
            if part.endswith(".app"):
                return str(Path(*parts[: index + 1]))
        return ""

    def _run_converter_command(self, command):
        return subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

    def _convert_via_open_app_bundle(self, app_bundle_root, source_path, output_dir):
        if not app_bundle_root:
            return None
        return self._run_converter_command(
            [
                "open",
                "-W",
                "-n",
                "-a",
                app_bundle_root,
                "--args",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(source_path),
            ]
        )

    def _format_converter_failure(self, command_result, source_path):
        parts = []
        if command_result is not None:
            parts.append(f"Converter exited with code {command_result.returncode}.")
            stdout_text = str(command_result.stdout or "").strip()
            stderr_text = str(command_result.stderr or "").strip()
            if stdout_text:
                parts.append(f"stdout: {stdout_text[:300]}")
            if stderr_text:
                parts.append(f"stderr: {stderr_text[:300]}")
        if not parts:
            return f"LibreOffice did not produce a PDF for {source_path.name}."
        return f"LibreOffice did not produce a PDF for {source_path.name}. {' '.join(parts)}"

    def _convert_to_pdf(self, source_path):
        soffice, source = self._resolve_soffice_details()
        if not soffice:
            return None, "LibreOffice (soffice) is not available."
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = self._run_converter_command(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(tmp_dir), str(source_path)],
            )
            pdf_path = tmp_dir / f"{source_path.stem}.pdf"
            if (
                not pdf_path.exists()
                and sys.platform == "darwin"
                and source == "system_app_bundle"
            ):
                app_bundle_root = self._macos_app_bundle_root(soffice)
                retry_result = self._convert_via_open_app_bundle(app_bundle_root, source_path, tmp_dir)
                if retry_result is not None:
                    result = retry_result
            if not pdf_path.exists():
                return None, self._format_converter_failure(result, source_path)
            target = source_path.with_suffix(".pdf")
            target.write_bytes(pdf_path.read_bytes())
            return target, ""

    def converter_status(self):
        soffice, source = self._resolve_soffice_details()
        if soffice:
            return {
                "converter_available": True,
                "converter_name": "LibreOffice",
                "converter_command": soffice,
                "converter_source": source,
                "converter_message": f"LibreOffice (soffice) is available via {source}.",
            }
        return {
            "converter_available": False,
            "converter_name": "LibreOffice",
            "converter_command": "",
            "converter_source": "",
            "converter_message": "LibreOffice (soffice) is not available.",
        }

    def health_summary(self):
        cfg = self.settings_store.get()
        summary = {
            "mailbox": str(cfg.get("email_intake_mailbox", "")).strip(),
            "enabled": bool(cfg.get("email_intake_enabled", False)),
            "monitored_folder": str(cfg.get("email_intake_monitored_folder", "")).strip(),
            "processed_folder": str(cfg.get("email_intake_processed_folder", "")).strip(),
            "failed_folder": str(cfg.get("email_intake_failed_folder", "")).strip(),
            "poll_interval_seconds": int(cfg.get("email_intake_poll_interval_seconds", 60) or 60),
        }
        summary.update(self.outlook_auth.health_summary())
        summary.update(self.converter_status())
        return summary

    def _default_analyzer_factory(self):
        from ai_analyzer import AIResumeAnalyzer
        return AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def _configured_rank_map(self, analyzer):
        ranks_str = self.parser.get("Ranks", "rank_options", fallback="").strip()
        configured = [row.strip() for row in ranks_str.splitlines() if row.strip()]
        canonical_map = {}
        direct_map = {}
        for rank in configured:
            normalized_label = analyzer._normalize_rank_label(rank)
            if normalized_label and normalized_label not in direct_map:
                direct_map[normalized_label] = rank
            canonical_id, _department, _seniority_bucket, _confidence = analyzer._normalize_rank(rank)
            if canonical_id and canonical_id not in canonical_map:
                canonical_map[canonical_id] = rank
        return canonical_map, direct_map, set(configured)

    def _contains_rank_phrase(self, haystack, phrase):
        if not haystack or not phrase:
            return False
        return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack, flags=re.IGNORECASE) is not None

    def _allow_direct_label_match(self, normalized_label, source_label):
        compact = re.sub(r"\s+", "", str(normalized_label or ""))
        if len(compact) <= 2 and source_label == "body":
            return False
        return True

    def _collect_rank_matches(self, analyzer, direct_map, canonical_map, text, source_label):
        normalized_text = analyzer._normalize_rank_label(text)
        if not normalized_text:
            return []

        matches = []
        for normalized_label, display_rank in direct_map.items():
            if not self._allow_direct_label_match(normalized_label, source_label):
                continue
            if self._contains_rank_phrase(normalized_text, normalized_label):
                matches.append({
                    "rank": display_rank,
                    "source": source_label,
                    "match_type": "configured_label",
                    "matched_text": normalized_label,
                })

        for alias, entry in getattr(analyzer, "RANK_ALIAS_TABLE", {}).items():
            if not self._contains_rank_phrase(normalized_text, alias):
                continue
            display_rank = canonical_map.get(entry["canonical_id"])
            if not display_rank:
                continue
            matches.append({
                "rank": display_rank,
                "source": source_label,
                "match_type": "alias",
                "matched_text": alias,
            })
        return matches

    def _normalized_attachment_stem(self, attachment_name):
        name = str(attachment_name or "").strip()
        if not name:
            return "resume"
        stem = name
        lower = stem.lower()
        while lower.endswith(".pdf"):
            stem = stem[:-4]
            lower = stem.lower()
        stem = Path(stem).stem if "." in stem else stem
        return _safe_segment(stem, "resume")

    def _resume_like_signal_count(self, subject, attachment_name, pdf_text):
        signal_sources = [
            str(subject or ""),
            str(attachment_name or ""),
            str(pdf_text or ""),
        ]
        combined = "\n".join(signal_sources)
        score = 0
        if self.resume_extractor._extract_best_email(combined):
            score += 1
        lowered = combined.lower()
        for pattern in RESUME_SIGNAL_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                score += 1
        return score

    def _should_skip_non_resume_attachment(self, subject, sender, attachment_name, pdf_text):
        sender_lower = str(sender or "").strip().lower()
        subject_lower = str(subject or "").strip().lower()
        attachment_lower = str(attachment_name or "").strip().lower()
        pdf_text_lower = str(pdf_text or "").strip().lower()

        sender_is_internal = sender_lower.endswith("@njordships.com")
        proposal_context = any(
            re.search(pattern, subject_lower, flags=re.IGNORECASE)
            for pattern in PROPOSAL_CONTEXT_PATTERNS
        )
        non_resume_signal = any(
            re.search(pattern, " ".join([subject_lower, attachment_lower, pdf_text_lower[:2000]]), flags=re.IGNORECASE)
            for pattern in NON_RESUME_ATTACHMENT_PATTERNS
        )
        supporting_doc_attachment = any(
            re.search(pattern, attachment_lower, flags=re.IGNORECASE)
            for pattern in SUPPORTING_DOCUMENT_ATTACHMENT_PATTERNS
        )
        resume_signal_count = self._resume_like_signal_count(subject, attachment_name, pdf_text)

        if sender_is_internal and proposal_context:
            return True, "Attachment looks like internal proposal/certificate material, not a candidate resume."
        if supporting_doc_attachment and resume_signal_count < 4:
            return True, "Attachment looks like a certificate/supporting document, not a primary candidate resume."
        return False, ""

    def _cleanup_skipped_attachment_files(self, original_path, working_pdf_path):
        paths = []
        for path in (working_pdf_path, original_path):
            if path is None:
                continue
            path_obj = Path(path)
            if path_obj not in paths:
                paths.append(path_obj)
        for path_obj in paths:
            if path_obj.exists():
                try:
                    path_obj.unlink()
                except OSError:
                    pass

    def _classify_rank(self, subject, body_preview, attachment_name, pdf_text):
        analyzer = self.analyzer_factory()
        canonical_map, direct_map, configured_ranks = self._configured_rank_map(analyzer)

        evidence_matches = []
        evidence_matches.extend(self._collect_rank_matches(analyzer, direct_map, canonical_map, subject, "subject"))
        evidence_matches.extend(self._collect_rank_matches(analyzer, direct_map, canonical_map, attachment_name, "filename"))
        evidence_matches.extend(self._collect_rank_matches(analyzer, direct_map, canonical_map, body_preview, "body"))

        source_priority = ["subject", "filename", "body"]
        ranks_by_source = {}
        for source in source_priority:
            ranks_by_source[source] = []
        for match in evidence_matches:
            source_ranks = ranks_by_source.setdefault(match["source"], [])
            if match["rank"] not in source_ranks:
                source_ranks.append(match["rank"])

        for source in source_priority:
            source_ranks = ranks_by_source[source]
            if len(source_ranks) == 1:
                corroborating_sources = sorted({
                    match["source"]
                    for match in evidence_matches
                    if match["rank"] == source_ranks[0] and match["source"] != source
                })
                corroboration = ""
                if corroborating_sources:
                    corroboration = f" corroborated by {', '.join(corroborating_sources)} evidence."
                winning_rank = source_ranks[0]
                if winning_rank not in configured_ranks:
                    return {
                        "outcome": "manual_review",
                        "rank": "",
                        "reason": f"Resolved rank {winning_rank} is not in configured rank_options allowlist.",
                        "evidence": evidence_matches,
                    }
                return {
                    "outcome": "classified",
                    "rank": winning_rank,
                    "reason": f"Rank classified as {winning_rank} from explicit {source} evidence.{corroboration}",
                    "evidence": evidence_matches,
                }
            if len(source_ranks) > 1:
                return {
                    "outcome": "manual_review",
                    "rank": "",
                    "reason": f"Conflicting rank evidence detected in {source}: {', '.join(source_ranks)}.",
                    "evidence": evidence_matches,
                }

        evidence = "\n".join(
            part for part in [
                f"Subject: {subject}",
                f"Body: {body_preview}",
                f"Attachment: {attachment_name}",
                pdf_text,
            ] if str(part or "").strip()
        )
        rank_fact = analyzer._extract_rank_fact_from_text(evidence)
        canonical_id = rank_fact.get("canonical_id")
        if (
            canonical_id
            and canonical_id in canonical_map
            and rank_fact.get("source_label") == "applied_for_rank"
        ):
            winning_rank = canonical_map[canonical_id]
            if winning_rank not in configured_ranks:
                return {
                    "outcome": "manual_review",
                    "rank": "",
                    "reason": f"Resolved rank {winning_rank} is not in configured rank_options allowlist.",
                    "evidence": [{"source": "resume_text", "match_type": "applied_for_rank", "rank": winning_rank}],
                }
            return {
                "outcome": "classified",
                "rank": winning_rank,
                "reason": f"Rank classified as {winning_rank} from resume applied-for-rank evidence.",
                "evidence": [{"source": "resume_text", "match_type": "applied_for_rank", "rank": winning_rank}],
            }
        return {
            "outcome": "manual_review",
            "rank": "",
            "reason": "Rank could not be classified confidently from subject, body, filename, or applied-for-rank evidence.",
            "evidence": evidence_matches,
        }

    def _message_sender(self, message):
        sender = (((message.get("from") or {}).get("emailAddress") or {}).get("address")) or ""
        return str(sender).strip()

    def _message_received_stamp(self, message):
        raw = str(message.get("receivedDateTime", "")).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return dt.strftime("%Y%m%d_%H%M%S")

    def _final_pdf_name(self, message, attachment_name):
        stamp = self._message_received_stamp(message)
        message_fragment = _safe_segment(message.get("id", ""), "msg")[:12]
        stem = self._normalized_attachment_stem(attachment_name)
        return f"EMAIL_{stamp}_{message_fragment}_{stem}.pdf"

    def _write_sidecar(self, target_path, payload):
        sidecar_path = Path(f"{target_path}.json")
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)

    def _read_sidecar(self, pdf_path):
        sidecar_path = Path(f"{pdf_path}.json")
        if not sidecar_path.exists():
            return {}, sidecar_path, "missing"
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                return json.load(fh), sidecar_path, ""
        except Exception as exc:
            return {}, sidecar_path, str(exc)

    def _manual_review_item_id(self, pdf_path):
        return Path(pdf_path).name

    def _resolve_manual_review_pdf_path(self, item_id):
        decoded = unquote(str(item_id or "")).strip()
        if not decoded:
            raise RuntimeError("manual review item id is required.")
        filename = Path(decoded).name
        if not filename.lower().endswith(".pdf"):
            raise RuntimeError("manual review item id must reference a PDF.")
        pdf_path = self._manual_review_dir() / filename
        if not pdf_path.exists():
            raise RuntimeError(f"Manual review item not found: {filename}")
        return pdf_path

    def _manual_review_item_payload(self, pdf_path):
        pdf_path = Path(pdf_path)
        sidecar, sidecar_path, sidecar_error = self._read_sidecar(pdf_path)
        stat = pdf_path.stat()
        received_at = str(sidecar.get("received_at", "")).strip()
        latest_ts = received_at or datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"
        extracted_text = self.resume_extractor.extract_text_from_pdf(str(pdf_path))
        quality_state = self._classify_document_quality(extracted_text)
        unreadable_text = quality_state == "UNREADABLE"
        candidate_name_details = self._derive_candidate_name_details(
            stored_candidate_name=sidecar.get("candidate_name", ""),
            pdf_path=pdf_path if not unreadable_text else None,
            pdf_text="" if unreadable_text else extracted_text,
            mail_subject=sidecar.get("mail_subject", ""),
            mail_sender=sidecar.get("mail_sender", ""),
            attachment_name=sidecar.get("attachment_name", ""),
            quality_state=quality_state,
        )
        evidence = sidecar.get("rank_evidence", [])
        evidence_summary = ", ".join(
            f"{row.get('source', 'unknown')}: {row.get('rank', '')}".strip(": ")
            for row in evidence[:3]
            if isinstance(row, dict)
        )
        return {
            "id": self._manual_review_item_id(pdf_path),
            "pdf_filename": pdf_path.name,
            "pdf_path": str(pdf_path),
            "sidecar_path": str(sidecar_path),
            "candidate_name": candidate_name_details["candidate_name"],
            "candidate_name_source": candidate_name_details["source"],
            "candidate_name_confidence": candidate_name_details["confidence"],
            "mail_sender": str(sidecar.get("mail_sender", "")).strip(),
            "mail_subject": str(sidecar.get("mail_subject", "")).strip(),
            "received_at": latest_ts,
            "review_reason": str(sidecar.get("rank_reason", "") or sidecar.get("reason", "")).strip(),
            "rank_evidence_summary": evidence_summary,
            "original_attachment_name": str(sidecar.get("attachment_name", "")).strip(),
            "rank_outcome": str(sidecar.get("rank_outcome", "")).strip(),
            "rank_reason": str(sidecar.get("rank_reason", "") or sidecar.get("reason", "")).strip(),
            "rank_evidence": evidence if isinstance(evidence, list) else [],
            "connected_account": str(sidecar.get("connected_account", "")).strip(),
            "internet_message_id": str(sidecar.get("internet_message_id", "")).strip(),
            "message_id": str(sidecar.get("message_id", "")).strip(),
            "attachment_id": str(sidecar.get("attachment_id", "")).strip(),
            "pdf_checksum": str(sidecar.get("pdf_checksum", "")).strip(),
            "sidecar_status": "invalid" if sidecar_error else "ok",
            "sidecar_error": sidecar_error,
            "unreadable_text": unreadable_text,
            "quality_state": quality_state,
        }

    def _extract_candidate_name_from_subject(self, subject):
        source = re.sub(r"\s+", " ", str(subject or "")).strip()
        if not source:
            return ""

        subject_patterns = [
            re.compile(r"\b(?:cv|resume)\s+of\s+([A-Za-z][A-Za-z\s]{2,80}?)(?:\s+with\b|$)", flags=re.IGNORECASE),
            re.compile(r"^\s*(?:cv|resume)\b.*?\b([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+){1,3})\s*$", flags=re.IGNORECASE),
            re.compile(r"^\s*([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+){1,3})\s*[-–:]\s*[A-Za-z0-9/().\s]+$"),
            re.compile(r"\bapplication\s+for(?:\s+the\s+position\s+of)?\b[^–:-]*[–:-]\s*([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+){1,3})", flags=re.IGNORECASE),
        ]
        for pattern in subject_patterns:
            match = pattern.search(source)
            if not match:
                continue
            candidate = self.resume_extractor._normalize_candidate_name(match.group(1))
            if candidate:
                return candidate
        return ""

    def _extract_candidate_name_from_filename(self, attachment_name):
        attachment_base = Path(str(attachment_name or "")).stem
        attachment_base = re.sub(r"[_-]+", " ", attachment_base)
        attachment_base = re.sub(r"\b(?:cv|resume|biodata|bio data|application form|seaman)\b", " ", attachment_base, flags=re.IGNORECASE)
        attachment_base = re.sub(r"\s+", " ", attachment_base).strip()
        return self.resume_extractor._normalize_candidate_name(attachment_base)

    def _extract_candidate_name_from_sender(self, sender):
        email = str(sender or "").strip().lower()
        if "@" not in email:
            return ""
        local_part = email.split("@", 1)[0]
        local_part = re.sub(r"\d+", " ", local_part)
        tokens = [token for token in re.split(r"[._-]+", local_part) if len(token) >= 2]
        if len(tokens) < 2:
            return ""
        candidate = " ".join(tokens[:4])
        return self.resume_extractor._normalize_candidate_name(candidate)

    def _collect_candidate_name_evidence(self, *, pdf_text="", mail_subject="", mail_sender="", attachment_name=""):
        evidence = []
        extracted_text = str(pdf_text or "")
        evidence.extend(self.resume_extractor.collect_candidate_name_evidence_from_text(extracted_text))

        candidate = self._extract_candidate_name_from_subject(mail_subject)
        if candidate:
            evidence.append({
                "source": "SUBJECT_IDENTITY",
                "normalized_value": candidate,
                "confidence": "medium",
                "raw_value": str(mail_subject or "").strip(),
                "rejection_reason": "",
            })

        candidate = self._extract_candidate_name_from_sender(mail_sender)
        if candidate:
            evidence.append({
                "source": "SENDER_IDENTITY",
                "normalized_value": candidate,
                "confidence": "low",
                "raw_value": str(mail_sender or "").strip(),
                "rejection_reason": "",
            })

        candidate = self._extract_candidate_name_from_filename(attachment_name)
        if candidate:
            evidence.append({
                "source": "FILENAME_IDENTITY",
                "normalized_value": candidate,
                "confidence": "low",
                "raw_value": str(attachment_name or "").strip(),
                "rejection_reason": "",
            })

        deduped = []
        seen = set()
        for row in evidence:
            key = (str(row.get("source", "")), str(row.get("normalized_value", "")).lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    def _derive_candidate_name_details(self, *, stored_candidate_name="", pdf_path=None, pdf_text="", mail_subject="", mail_sender="", attachment_name="", quality_state="READABLE"):
        extracted_text = str(pdf_text or "")
        if not extracted_text and pdf_path:
            extracted_text = self.resume_extractor.extract_text_from_pdf(str(pdf_path))
        evidence = self._collect_candidate_name_evidence(
            pdf_text=extracted_text,
            mail_subject=mail_subject,
            mail_sender=mail_sender,
            attachment_name=attachment_name,
        )
        source_priority = {
            "STRUCTURED_FIELD": 0,
            "HEADER_IDENTITY": 1,
            "SUBJECT_IDENTITY": 2,
            "SENDER_IDENTITY": 3,
            "FILENAME_IDENTITY": 4,
        }

        winning = None
        for row in sorted(evidence, key=lambda item: source_priority.get(item.get("source", ""), 99)):
            if not row.get("normalized_value"):
                continue
            if quality_state == "READABLE" and row.get("source") in {"SENDER_IDENTITY", "FILENAME_IDENTITY"}:
                # Keep weak fallbacks for sparse/weak resumes, but do not prefer them on clearly readable text.
                continue
            winning = row
            break

        if winning is None:
            for row in sorted(evidence, key=lambda item: source_priority.get(item.get("source", ""), 99)):
                if row.get("normalized_value"):
                    winning = row
                    break

        stored = self.resume_extractor._normalize_candidate_name(stored_candidate_name)
        if stored and (
            winning is None
            or str(winning.get("source", "")) in {"SENDER_IDENTITY", "FILENAME_IDENTITY"}
        ):
            winning = {
                "source": "SIDECAR_STORED",
                "normalized_value": stored,
                "confidence": "low",
            }

        return {
            "candidate_name": str((winning or {}).get("normalized_value", "")).strip(),
            "source": str((winning or {}).get("source", "")).strip(),
            "confidence": str((winning or {}).get("confidence", "")).strip(),
            "evidence": evidence,
        }

    def _derive_candidate_name(self, *, stored_candidate_name="", pdf_path=None, pdf_text="", mail_subject="", mail_sender="", attachment_name="", quality_state="READABLE"):
        return self._derive_candidate_name_details(
            stored_candidate_name=stored_candidate_name,
            pdf_path=pdf_path,
            pdf_text=pdf_text,
            mail_subject=mail_subject,
            mail_sender=mail_sender,
            attachment_name=attachment_name,
            quality_state=quality_state,
        )["candidate_name"]

    def _classify_document_quality(self, pdf_text):
        source = str(pdf_text or "")
        if not source.strip():
            return "WEAK_BUT_USABLE"
        compact = re.sub(r"\s+", "", source)
        if compact.startswith("0M8R4KGx"):
            return "UNREADABLE"
        slash_count = compact.count("/")
        repeated_a_runs = len(re.findall(r"A{12,}", compact))
        if slash_count >= 300 and repeated_a_runs >= 20:
            return "UNREADABLE"

        natural_tokens = re.findall(r"[A-Za-z]{2,}", source)
        token_count = len(natural_tokens)
        if token_count < 20:
            if self.resume_extractor._extract_best_email(source) or self.resume_extractor.extract_candidate_name_from_text(source):
                return "WEAK_BUT_USABLE"
        return "READABLE"

    def _looks_like_unreadable_text_extraction(self, pdf_text):
        return self._classify_document_quality(pdf_text) == "UNREADABLE"

    def manual_review_summary(self):
        items = self.list_manual_review_items()
        latest_received_at = items[0]["received_at"] if items else ""
        latest_item_name = items[0]["pdf_filename"] if items else ""
        return {
            "pending_count": len(items),
            "latest_received_at": latest_received_at,
            "latest_item_name": latest_item_name,
        }

    def list_manual_review_items(self):
        rows = []
        for pdf_path in sorted(self._manual_review_dir().glob("*.pdf")):
            rows.append(self._manual_review_item_payload(pdf_path))
        rows.sort(key=lambda row: row.get("received_at", ""), reverse=True)
        return rows

    def get_manual_review_item(self, item_id):
        pdf_path = self._resolve_manual_review_pdf_path(item_id)
        return self._manual_review_item_payload(pdf_path)

    def _configured_rank_options(self):
        ranks_str = self.parser.get("Ranks", "rank_options", fallback="").strip()
        return [row.strip() for row in ranks_str.splitlines() if row.strip()]

    def _reserve_output_path(self, folder, filename):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / filename
        if not candidate.exists() and not Path(f"{candidate}.json").exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            next_candidate = folder / f"{stem}_{counter}{suffix}"
            if not next_candidate.exists() and not Path(f"{next_candidate}.json").exists():
                return next_candidate
            counter += 1

    def _find_existing_manual_review_duplicate(self, manual_review_dir, sidecar, pdf_bytes):
        manual_review_dir = Path(manual_review_dir)
        target_message_id = str(sidecar.get("message_id", "")).strip()
        target_attachment_id = str(sidecar.get("attachment_id", "")).strip()
        target_internet_message_id = str(sidecar.get("internet_message_id", "")).strip()
        target_attachment_name = str(sidecar.get("attachment_name", "")).strip()
        target_checksum = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes is not None else ""

        for existing_pdf in sorted(manual_review_dir.glob("*.pdf")):
            existing_sidecar, _sidecar_path, _sidecar_error = self._read_sidecar(existing_pdf)
            existing_message_id = str(existing_sidecar.get("message_id", "")).strip()
            existing_attachment_id = str(existing_sidecar.get("attachment_id", "")).strip()
            existing_internet_message_id = str(existing_sidecar.get("internet_message_id", "")).strip()
            existing_attachment_name = str(existing_sidecar.get("attachment_name", "")).strip()
            existing_checksum = str(existing_sidecar.get("pdf_checksum", "")).strip()

            if (
                target_message_id
                and target_attachment_id
                and target_message_id == existing_message_id
                and target_attachment_id == existing_attachment_id
            ):
                return existing_pdf

            if (
                target_internet_message_id
                and target_attachment_name
                and target_internet_message_id == existing_internet_message_id
                and target_attachment_name == existing_attachment_name
            ):
                return existing_pdf

            if (
                target_message_id
                and target_attachment_name
                and target_message_id == existing_message_id
                and target_attachment_name == existing_attachment_name
            ):
                return existing_pdf

            if target_checksum:
                if existing_checksum and existing_checksum == target_checksum:
                    return existing_pdf
                try:
                    existing_bytes = existing_pdf.read_bytes()
                except Exception:
                    existing_bytes = b""
                if existing_bytes and hashlib.sha256(existing_bytes).hexdigest() == target_checksum:
                    return existing_pdf

        return None

    def move_manual_review_item(self, item_id, selected_role):
        role = str(selected_role or "").strip()
        if not role:
            raise RuntimeError("selected_role is required.")

        configured_roles = self._configured_rank_options()
        if role not in configured_roles:
            raise RuntimeError(f"Selected role {role} is not in configured rank_options allowlist.")

        pdf_path = self._resolve_manual_review_pdf_path(item_id)
        _sidecar_payload, sidecar_path, _sidecar_error = self._read_sidecar(pdf_path)

        target_dir = rank_folder_path(self._download_root(), role)
        final_pdf_path = self._reserve_output_path(target_dir, pdf_path.name)
        shutil.move(str(pdf_path), str(final_pdf_path))

        final_sidecar_path = Path(f"{final_pdf_path}.json")
        if sidecar_path.exists():
            shutil.move(str(sidecar_path), str(final_sidecar_path))
        else:
            final_sidecar_path = Path("")

        return {
            "success": True,
            "moved_item_id": self._manual_review_item_id(pdf_path),
            "selected_role": role,
            "destination_pdf_path": str(final_pdf_path),
            "destination_sidecar_path": str(final_sidecar_path) if str(final_sidecar_path) else "",
            "pending_count": self.manual_review_summary()["pending_count"],
        }

    def _open_path_in_system_viewer(self, path):
        target = str(path)
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
            return
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
            return
        subprocess.Popen(["xdg-open", target])

    def open_manual_review_item(self, item_id):
        pdf_path = self._resolve_manual_review_pdf_path(item_id)
        if not pdf_path.exists():
            raise RuntimeError(f"Manual review item not found: {item_id}")
        self._open_path_in_system_viewer(pdf_path)
        return {
            "success": True,
            "id": self._manual_review_item_id(pdf_path),
            "pdf_path": str(pdf_path),
        }

    def fetch_from_outlook(self, emit):
        cfg = self.settings_store.get()
        mailbox = str(cfg.get("email_intake_mailbox", "")).strip()
        connected_account = str(cfg.get("outlook_connected_account", "")).strip()
        if not mailbox:
            raise RuntimeError("email_intake_mailbox is not configured.")
        if not connected_account:
            raise RuntimeError("Outlook mailbox is not connected.")
        if mailbox.lower() != connected_account.lower():
            raise RuntimeError(
                f"Connected Outlook account {connected_account} does not match configured mailbox {mailbox}."
            )

        token = self.outlook_auth.acquire_access_token()
        registry = self._prune_registry(self._load_registry())
        folder_id = self._resolve_folder_id(token, cfg.get("email_intake_monitored_folder", ""))
        messages = self._list_messages(token, folder_id)

        emit("log", f"Connected mailbox: {connected_account}")
        emit("log", f"Scanning folder: {cfg.get('email_intake_monitored_folder', '')}")
        emit("log", f"Found {len(messages)} mail item(s) in the intake folder.")

        originals_dir = self._special_folder("_EmailInbox_Originals")
        failed_dir = self._special_folder("_EmailInbox_Failed")
        manual_review_dir = self._special_folder("_EmailInbox_ManualReview")

        summary = {
            "imported": 0,
            "manual_review": 0,
            "failed": 0,
            "duplicates": 0,
            "skipped": 0,
            "messages_scanned": len(messages),
            "attachments_seen": 0,
            "saved_files": [],
            "failed_items": [],
        }

        processed = registry["processed_attachments"]
        checksums = registry["checksums"]

        for message in messages:
            subject = str(message.get("subject", "")).strip()
            sender = self._message_sender(message)
            emit("log", f"Checking mail: {subject or '(no subject)'} | from {sender or 'unknown sender'}")
            if not message.get("hasAttachments"):
                summary["skipped"] += 1
                emit("log", "Skipped: no attachments.")
                continue

            attachments = self._list_file_attachments(token, message["id"])
            if not attachments:
                summary["skipped"] += 1
                emit("log", "Skipped: no supported PDF/DOC/DOCX attachments.")
                continue

            for attachment in attachments:
                summary["attachments_seen"] += 1
                attachment_name = str(attachment.get("name", "")).strip() or "attachment"
                registry_key = self._attachment_registry_key(message["id"], attachment["id"])
                if registry_key in processed:
                    summary["duplicates"] += 1
                    emit("log", f"Skipped duplicate attachment: {attachment_name}")
                    continue

                content_bytes_b64 = attachment.get("contentBytes")
                if not content_bytes_b64:
                    emit("log", f"Failed: attachment payload missing for {attachment_name}")
                    summary["failed"] += 1
                    summary["failed_items"].append({"attachment": attachment_name, "reason": "content_missing"})
                    continue
                content_bytes = base64.b64decode(content_bytes_b64)
                checksum = hashlib.sha256(content_bytes).hexdigest()
                if checksum in checksums:
                    processed[registry_key] = {
                        "status": "duplicate_checksum_skip",
                        "attachment_name": attachment_name,
                        "processed_at": _now_iso(),
                    }
                    summary["duplicates"] += 1
                    emit("log", f"Skipped checksum duplicate: {attachment_name}")
                    continue

                original_path = self._write_bytes(originals_dir, attachment_name, content_bytes)
                working_pdf_path = original_path
                if original_path.suffix.lower() in {".doc", ".docx"}:
                    working_pdf_path, conversion_error = self._convert_to_pdf(original_path)
                    if not working_pdf_path:
                        failure_path = failed_dir / original_path.name
                        shutil.copy2(original_path, failure_path)
                        self._write_sidecar(
                            failure_path,
                            {
                                "reason": conversion_error,
                                "mail_subject": subject,
                                "mail_sender": sender,
                                "attachment_name": attachment_name,
                                "message_id": message.get("id", ""),
                            },
                        )
                        summary["failed"] += 1
                        summary["failed_items"].append({"attachment": attachment_name, "reason": conversion_error})
                        emit("log", f"Failed conversion: {attachment_name} | {conversion_error}")
                        continue

                pdf_text = self.resume_extractor.extract_text_from_pdf(str(working_pdf_path))
                final_name = self._final_pdf_name(message, attachment_name)
                quality_state = self._classify_document_quality(pdf_text)
                if quality_state == "UNREADABLE":
                    failed_path = self._reserve_output_path(failed_dir, final_name)
                    failed_path.write_bytes(working_pdf_path.read_bytes())
                    self._write_sidecar(
                        failed_path,
                        {
                            "reason": "Unreadable or corrupted text extraction",
                            "mail_subject": subject,
                            "mail_sender": sender,
                            "attachment_name": attachment_name,
                            "message_id": message.get("id", ""),
                            "internet_message_id": message.get("internetMessageId", ""),
                            "attachment_id": attachment.get("id", ""),
                        },
                    )
                    summary["failed"] += 1
                    summary["failed_items"].append(
                        {"attachment": attachment_name, "reason": "Unreadable or corrupted text extraction"}
                    )
                    processed[registry_key] = {
                        "status": "failed_unreadable",
                        "attachment_name": attachment_name,
                        "target_path": str(failed_path),
                        "processed_at": _now_iso(),
                    }
                    checksums[checksum] = time.time()
                    emit("log", f"Failed unreadable extraction: {attachment_name} | Unreadable or corrupted text extraction.")
                    continue
                should_skip_non_resume, skip_reason = self._should_skip_non_resume_attachment(
                    subject=subject,
                    sender=sender,
                    attachment_name=attachment_name,
                    pdf_text=pdf_text,
                )
                if should_skip_non_resume:
                    self._cleanup_skipped_attachment_files(original_path, working_pdf_path)
                    processed[registry_key] = {
                        "status": "non_resume_skip",
                        "attachment_name": attachment_name,
                        "processed_at": _now_iso(),
                    }
                    checksums[checksum] = time.time()
                    summary["skipped"] += 1
                    emit("log", f"Skipped non-resume attachment: {attachment_name} | {skip_reason}")
                    continue
                rank_result = self._classify_rank(
                    subject=subject,
                    body_preview=str(message.get("bodyPreview", "")).strip(),
                    attachment_name=attachment_name,
                    pdf_text=pdf_text,
                )
                candidate_name_details = self._derive_candidate_name_details(
                    pdf_text=pdf_text,
                    mail_subject=subject,
                    mail_sender=sender,
                    attachment_name=attachment_name,
                    quality_state=quality_state,
                )

                sidecar = {
                    "candidate_name": candidate_name_details["candidate_name"],
                    "candidate_name_source": candidate_name_details["source"],
                    "candidate_name_confidence": candidate_name_details["confidence"],
                    "mail_subject": subject,
                    "mail_sender": sender,
                    "received_at": message.get("receivedDateTime", ""),
                    "message_id": message.get("id", ""),
                    "internet_message_id": message.get("internetMessageId", ""),
                    "attachment_id": attachment.get("id", ""),
                    "attachment_name": attachment_name,
                    "rank_outcome": rank_result["outcome"],
                    "rank_reason": rank_result["reason"],
                    "rank_evidence": rank_result.get("evidence", []),
                    "connected_account": connected_account,
                    "pdf_checksum": hashlib.sha256(working_pdf_path.read_bytes()).hexdigest(),
                    "quality_state": quality_state,
                }

                if rank_result["outcome"] == "classified":
                    target_dir = rank_folder_path(self._download_root(), rank_result["rank"])
                    target_dir.mkdir(parents=True, exist_ok=True)
                    final_path = self._reserve_output_path(target_dir, final_name)
                    final_path.write_bytes(working_pdf_path.read_bytes())
                    self._write_sidecar(final_path, sidecar)
                    summary["imported"] += 1
                    summary["saved_files"].append(str(final_path))
                    processed[registry_key] = {
                        "status": "imported",
                        "attachment_name": attachment_name,
                        "target_path": str(final_path),
                        "processed_at": _now_iso(),
                    }
                    checksums[checksum] = time.time()
                    emit("log", f"Imported: {attachment_name} -> {rank_result['rank']}/{final_path.name}")
                    continue

                working_pdf_bytes = working_pdf_path.read_bytes()
                existing_review_path = self._find_existing_manual_review_duplicate(
                    manual_review_dir,
                    sidecar,
                    working_pdf_bytes,
                )
                if existing_review_path is not None:
                    processed[registry_key] = {
                        "status": "duplicate_manual_review_skip",
                        "attachment_name": attachment_name,
                        "target_path": str(existing_review_path),
                        "processed_at": _now_iso(),
                    }
                    checksums[checksum] = time.time()
                    summary["duplicates"] += 1
                    emit("log", f"Skipped existing manual review duplicate: {attachment_name}")
                    continue

                review_path = self._reserve_output_path(manual_review_dir, final_name)
                review_path.write_bytes(working_pdf_bytes)
                self._write_sidecar(review_path, sidecar)
                summary["manual_review"] += 1
                processed[registry_key] = {
                    "status": "manual_review",
                    "attachment_name": attachment_name,
                    "target_path": str(review_path),
                    "processed_at": _now_iso(),
                }
                checksums[checksum] = time.time()
                emit("log", f"Manual review: {attachment_name} -> {review_path.name}")

        self._save_registry(registry)
        summary["message"] = (
            f"Outlook fetch complete: {summary['imported']} imported, "
            f"{summary['manual_review']} manual review, "
            f"{summary['failed']} failed, {summary['duplicates']} duplicates."
        )
        return {"success": True, **summary}
