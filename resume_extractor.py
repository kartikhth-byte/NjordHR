# resume_extractor.py - Resume Data Extraction Module

import re
import os
import PyPDF2

class ResumeExtractor:
    """Extracts structured data from SeaJob resumes"""
    
    def __init__(self):
        pass

    _EMAIL_RE = re.compile(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})')
    _CANDIDATE_NAME_HEADER_PATTERNS = [
        re.compile(r'([A-Z][A-Z\s]{4,60})\s+CONTACT\s*:', flags=re.IGNORECASE),
        re.compile(r'([A-Z][A-Z\s]{4,60})\s+E-?MAIL\s*:', flags=re.IGNORECASE),
        re.compile(r'([A-Z][A-Z\s]{4,60})\s+ID\s+No\s*:'),
        re.compile(r'([A-Za-z][A-Za-z\s]{4,80})(?:,\s*\d{1,2})?\s+[A-Za-z]+(?:\s*/\s*[A-Za-z]+){1,4}', flags=re.IGNORECASE),
    ]
    _TOP_LINE_ROLE_HEADER_PATTERN = re.compile(
        r'^\W*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?=\s+(?:Marine|Deck|Engine|Chief|Second|Third|Fourth|Electrical|Junior|Trainee|Cadet|Officer|Engineer|Cook|Bosun|Seaman|Oiler|Wiper|Motorman|Fitter|AB)\b)(?=.*(?:\+\d[\d\s-]{6,}|\|\s*[A-Za-z0-9._%+-]+@))'
    )
    _STANDALONE_NAME_LINE_PATTERN = re.compile(r'^\W*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\W*$')
    _ROLE_OR_CONTACT_FOLLOWUP_PATTERN = re.compile(
        r'(?:Marine|Deck|Engine|Chief|Second|Third|Fourth|Electrical|Junior|Trainee|Cadet|Officer|Engineer|Cook|Bosun|Seaman|Oiler|Wiper|Motorman|Fitter|AB|\+?\d[\d\s-]{6,}|[A-Za-z0-9._%+-]+@)',
        flags=re.IGNORECASE,
    )
    _CANDIDATE_NAME_LINE_PATTERNS = [
        re.compile(r'^\s*full\s+name\s*[:.\-]?\s*(.+?)\s*$', flags=re.IGNORECASE),
        re.compile(r'^\s*name\s*[:.\-]?\s*(.+?)\s*$', flags=re.IGNORECASE),
        re.compile(r'^\s*name\.\s*[:.\-]?\s*(.+?)\s*$', flags=re.IGNORECASE),
    ]
    _CANDIDATE_NAME_INLINE_PATTERNS = [
        re.compile(r'(?<![A-Za-z])full\s+name\s*[:.\-]?\s*([A-Za-z][A-Za-z\s]{2,})', flags=re.IGNORECASE),
        re.compile(r'(?<![A-Za-z])name\s*[:.\-]?\s*([A-Za-z][A-Za-z\s]{2,})', flags=re.IGNORECASE),
    ]
    _CANDIDATE_NAME_COMPRESSED_PATTERNS = [
        re.compile(r'(?<![A-Za-z])full\s*name\s*[:.\-]?\s*([A-Za-z][A-Za-z\s]{2,120})', flags=re.IGNORECASE),
        re.compile(r'(?<![A-Za-z])name\s*[:.\-]?\s*([A-Za-z][A-Za-z\s]{2,120})', flags=re.IGNORECASE),
        re.compile(r'(?<![A-Za-z])name\.\s*[:.\-]?\s*([A-Za-z][A-Za-z\s]{2,120})', flags=re.IGNORECASE),
    ]
    _CONTACT_LABEL_PATTERNS = (
        re.compile(r'\bcontact\s*:', flags=re.IGNORECASE),
        re.compile(r'\be-?mail\s*:', flags=re.IGNORECASE),
        re.compile(r'\bmobile\b', flags=re.IGNORECASE),
        re.compile(r'\bphone\b', flags=re.IGNORECASE),
    )
    _SPACED_NAME_PATTERN = re.compile(r'\b(?:[A-Za-z]\s+){5,}[A-Za-z]\b')
    _SPACED_HEADER_BEFORE_EMAIL_PATTERN = re.compile(
        r'^\W*((?:[A-Za-z]\s+){6,20})(?=[A-Za-z]\s*-\s*m\s*a\s*i\s*l\s*:)',
        flags=re.IGNORECASE,
    )
    _CANDIDATE_NAME_STOP_LABELS = (
        "father",
        "father's",
        "father’s",
        "mother",
        "mother's",
        "mother’s",
        "d.o.b",
        "dob",
        "date of birth",
        "permanent address",
        "address",
        "religion",
        "contact",
        "email",
        "mobile",
        "mob no",
        "languages known",
        "education qualification",
        "qualification",
        "nationality",
        "marital status",
        "document details",
        "post for applying",
        "post applied for",
        "rank applied",
        "next of kin",
    )

    def _clean_email(self, value):
        value = value.strip().strip('.,;:()[]{}<>').lower()
        value = re.sub(r'\s*@\s*', '@', value)
        value = re.sub(r'\s*\.\s*', '.', value)
        value = value.replace(' ', '')
        return value

    def _collapse_letter_spaced_name(self, value):
        text = str(value or '').strip()
        if not text:
            return ''
        compact = re.sub(r'\s+', ' ', text)
        if not self._SPACED_NAME_PATTERN.search(compact):
            return compact

        pieces = []
        current_letters = []
        for token in compact.split():
            if len(token) == 1 and token.isalpha():
                current_letters.append(token)
                continue
            if current_letters:
                pieces.append(''.join(current_letters))
                current_letters = []
            pieces.append(token)
        if current_letters:
            pieces.append(''.join(current_letters))
        return ' '.join(pieces)

    def _is_valid_email_candidate(self, email):
        """
        Lightweight guardrails against OCR/noise captures like '1@gmail.com'.
        """
        if not email:
            return False
        if '@' not in email:
            return False
        local, _domain = email.split('@', 1)
        if len(local) < 3:
            return False
        if local.isdigit():
            return False
        return True

    def _extract_best_email(self, text):
        normalized_text = text
        normalized_text = re.sub(r'\s*@\s*', '@', normalized_text)
        normalized_text = re.sub(r'\s*\.\s*', '.', normalized_text)

        # Prefer email found right after the "Email Address" label.
        labeled_line = re.search(r'Email Address\s+([^\n\r]+)', normalized_text, flags=re.IGNORECASE)
        if labeled_line:
            labeled_text = labeled_line.group(1)
            for source in (labeled_text, labeled_text.replace(' ', '')):
                labeled_match = self._EMAIL_RE.search(source)
                if labeled_match:
                    candidate = self._clean_email(labeled_match.group(1))
                    if self._is_valid_email_candidate(candidate):
                        return candidate

        # Fallback: choose best valid email across full text.
        best = ''
        best_score = -1
        all_candidates = list(self._EMAIL_RE.findall(normalized_text))
        # Also try with local-part space splits repaired (common PDF extraction artifact).
        repaired_local_text = re.sub(r'([A-Za-z0-9._%+-])\s+([A-Za-z0-9._%+-]+@)', r'\1\2', normalized_text)
        all_candidates.extend(self._EMAIL_RE.findall(repaired_local_text))
        compressed_text = re.sub(r'\s+', '', normalized_text)
        all_candidates.extend(self._EMAIL_RE.findall(compressed_text))
        for raw in all_candidates:
            candidate = self._clean_email(raw)
            if not self._is_valid_email_candidate(candidate):
                continue
            local, _domain = candidate.split('@', 1)
            score = len(local)
            if any(c.isalpha() for c in local):
                score += 5
            if any(c.isdigit() for c in local):
                score += 2
            if score > best_score:
                best = candidate
                best_score = score
        return best

    def _candidate_name_from_email_local_part(self, email):
        candidate = self._clean_email(str(email or ''))
        if '@' not in candidate:
            return ''
        local_part = candidate.split('@', 1)[0]
        local_part = re.sub(r'\d+', ' ', local_part)
        tokens = [token for token in re.split(r'[._-]+', local_part) if len(token) >= 2]
        if len(tokens) < 2:
            return ''
        return self._normalize_candidate_name(' '.join(tokens[:4]))
    
    def extract_text_from_pdf(self, pdf_path):
        """Extract text from PDF file"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text()
                return text
        except Exception as e:
            print(f"[ERROR] Failed to extract text from {pdf_path}: {e}")
            return ""

    def _candidate_name_rejection_reason(self, value):
        text = str(value or '').strip()
        text = self._collapse_letter_spaced_name(text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r',\s*\d{1,2}\b', '', text)
        text = text.strip(' .,:;|-')
        if not text:
            return 'empty'
        if len(text) < 3:
            return 'too_short'
        if any(char.isdigit() for char in text):
            return 'contains_digits'
        lowered = text.lower()
        blocked_fragments = (
            'father',
            'mother',
            'address',
            'resume',
            'curriculum vitae',
            'bio-data',
            'post applied',
            'rank applied',
            'date of birth',
            'nationality',
            'religion',
            'email',
            'mobile',
            'contact',
            'flag type me power',
            'main information',
            'personal data',
            'certificate number',
            'issued date',
            'valid until',
            'gmdss',
        )
        if any(fragment in lowered for fragment in blocked_fragments):
            return 'blocked_fragment'
        if lowered in {'name', 'full name', 'surname'}:
            return 'generic_label'
        words = text.split()
        if len(words) >= 3 and all(len(word) == 1 and word.isalpha() for word in words):
            return 'single_letter_junk'
        return ''

    def _normalize_candidate_name(self, value):
        text = str(value or '').strip()
        text = self._collapse_letter_spaced_name(text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r',\s*\d{1,2}\b', '', text)
        text = text.strip(' .,:;|-')
        if self._candidate_name_rejection_reason(text):
            return ''

        pieces = []
        for word in text.split():
            if len(word) == 1 and word.isalpha():
                pieces.append(word.upper())
            elif word.isupper() or word.islower():
                pieces.append(word.capitalize())
            else:
                pieces.append(word)
        normalized = ' '.join(pieces).strip()
        return normalized

    def _candidate_name_evidence(self, source, raw_value, confidence):
        normalized = self._normalize_candidate_name(raw_value)
        entry = {
            'source': source,
            'raw_value': str(raw_value or '').strip(),
            'normalized_value': normalized,
            'confidence': confidence,
            'rejection_reason': '',
        }
        if not normalized:
            entry['rejection_reason'] = self._candidate_name_rejection_reason(raw_value)
        return entry

    def _dedupe_candidate_name_evidence(self, evidence_rows):
        deduped = []
        seen = set()
        for row in evidence_rows:
            key = (
                str(row.get('source', '')),
                str(row.get('normalized_value', '')).lower(),
                str(row.get('raw_value', '')).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    def _collect_structured_candidate_name_evidence(self, source):
        evidence = []
        if not str(source or '').strip():
            return evidence

        lines = [line.strip() for line in str(source).splitlines() if line.strip()]
        for line in lines[:80]:
            normalized_line = re.sub(r'\s+', ' ', line)
            for pattern in self._CANDIDATE_NAME_LINE_PATTERNS:
                match = pattern.match(normalized_line)
                if match:
                    evidence.append(self._candidate_name_evidence('STRUCTURED_FIELD', match.group(1), 'high'))

        flattened = re.sub(r'\s+', ' ', str(source))
        for pattern in self._CANDIDATE_NAME_INLINE_PATTERNS:
            match = pattern.search(flattened)
            if match:
                evidence.append(self._candidate_name_evidence('STRUCTURED_FIELD', match.group(1), 'high'))

        compressed = re.sub(r'\s+', '', str(source))
        for pattern in self._CANDIDATE_NAME_COMPRESSED_PATTERNS:
            match = pattern.search(compressed)
            if not match:
                continue
            candidate_chunk = match.group(1)
            lowered_chunk = candidate_chunk.lower()
            stop_index = len(candidate_chunk)
            for stop_label in self._CANDIDATE_NAME_STOP_LABELS:
                found_at = lowered_chunk.find(stop_label)
                if found_at != -1:
                    stop_index = min(stop_index, found_at)
            evidence.append(self._candidate_name_evidence('STRUCTURED_FIELD', candidate_chunk[:stop_index], 'high'))

        compressed_lower = compressed.lower()
        label_tokens = ("fullname.:", "fullname:", "name.:", "name:", "name.")
        blocked_prefixes = ("father", "father's", "father’s", "mother", "mother's", "mother’s")
        stop_labels_lower = tuple(label.lower().replace(' ', '') for label in self._CANDIDATE_NAME_STOP_LABELS)
        for label_token in label_tokens:
            search_start = 0
            while True:
                found_at = compressed_lower.find(label_token, search_start)
                if found_at == -1:
                    break
                prefix_window = compressed_lower[max(0, found_at - 12):found_at]
                if any(prefix_window.endswith(prefix) for prefix in blocked_prefixes):
                    search_start = found_at + 1
                    continue
                candidate_start = found_at + len(label_token)
                candidate_chunk = compressed[candidate_start:candidate_start + 120]
                lowered_chunk = candidate_chunk.lower()
                stop_index = len(candidate_chunk)
                for stop_label in stop_labels_lower:
                    found_stop = lowered_chunk.find(stop_label)
                    if found_stop != -1:
                        stop_index = min(stop_index, found_stop)
                evidence.append(self._candidate_name_evidence('STRUCTURED_FIELD', candidate_chunk[:stop_index], 'high'))
                search_start = found_at + 1
        return self._dedupe_candidate_name_evidence(evidence)

    def _collect_header_candidate_name_evidence(self, source):
        evidence = []
        if not str(source or '').strip():
            return evidence

        flattened = re.sub(r'\s+', ' ', str(source))
        for pattern in self._CANDIDATE_NAME_HEADER_PATTERNS:
            match = pattern.search(flattened)
            if match:
                evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', match.group(1), 'high'))

        lines = [re.sub(r'\s+', ' ', line.strip()) for line in str(source).splitlines() if line.strip()]
        for index, line in enumerate(lines[:20]):
            if any(pattern.search(line) for pattern in self._CONTACT_LABEL_PATTERNS):
                window = []
                if index > 0:
                    window.append(lines[index - 1])
                window.append(line)
                header_text = ' '.join(window)
                header_text = re.split(r'\b(?:contact|e-?mail|email|mobile|phone)\b', header_text, maxsplit=1, flags=re.IGNORECASE)[0]
                evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', header_text, 'high'))

        for match in self._SPACED_NAME_PATTERN.finditer(flattened[:600]):
            raw_value = match.group(0)
            normalized = self._normalize_candidate_name(raw_value)
            if normalized and len(normalized.split()) == 1 and len(normalized) >= 8:
                continue
            evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', raw_value, 'medium'))

        spaced_header_match = self._SPACED_HEADER_BEFORE_EMAIL_PATTERN.search(flattened[:600])
        if spaced_header_match:
            adjacent_email_name = self._candidate_name_from_email_local_part(self._extract_best_email(source))
            if adjacent_email_name:
                evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', adjacent_email_name, 'high'))

        top_lines = lines[:8]
        for index, line in enumerate(top_lines):
            cleaned = re.split(r'\b(?:curriculum vitae|resume|bio[-\s]?data)\b', line, maxsplit=1, flags=re.IGNORECASE)[0]
            if cleaned != line:
                evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', cleaned, 'medium'))
            top_line_match = self._TOP_LINE_ROLE_HEADER_PATTERN.search(line)
            if top_line_match:
                evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', top_line_match.group(1), 'high'))
            standalone_match = self._STANDALONE_NAME_LINE_PATTERN.match(line)
            if standalone_match:
                followup = ' '.join(top_lines[index + 1:index + 3])
                if self._ROLE_OR_CONTACT_FOLLOWUP_PATTERN.search(followup):
                    evidence.append(self._candidate_name_evidence('HEADER_IDENTITY', standalone_match.group(1), 'high'))
        return self._dedupe_candidate_name_evidence(evidence)

    def collect_candidate_name_evidence_from_text(self, text):
        source = str(text or '')
        evidence = []
        evidence.extend(self._collect_structured_candidate_name_evidence(source))
        evidence.extend(self._collect_header_candidate_name_evidence(source))
        return self._dedupe_candidate_name_evidence(evidence)

    def extract_candidate_name_from_text(self, text):
        for evidence in self.collect_candidate_name_evidence_from_text(text):
            if evidence.get('normalized_value'):
                return evidence['normalized_value']
        return ''

    def extract_candidate_name_from_pdf(self, pdf_path):
        text = self.extract_text_from_pdf(pdf_path)
        return self.extract_candidate_name_from_text(text)
    
    def extract_resume_data(self, pdf_path, candidate_id=None, match_reason=""):
        """
        Extract structured data from resume PDF.
        
        Args:
            pdf_path: Path to the PDF file
            candidate_id: Candidate ID from filename/url
            match_reason: AI match reason from the verification process
            
        Returns:
            dict with extracted data
        """
        text = self.extract_text_from_pdf(pdf_path)
        
        if not text:
            # Return minimal data if extraction fails
            return {
                'candidate_id': str(candidate_id or ''),
                'resume': os.path.basename(pdf_path),
                'name': 'Extraction Failed',
                'present_rank': '',
                'email': '',
                'country': '',
                'mobile_no': '',
                'ai_match_reason': match_reason or 'Extraction Failed',
                'extraction_status': 'Failed'
            }
        
        data = {
            'candidate_id': str(candidate_id or ''),
            'resume': os.path.basename(pdf_path),
            'extraction_status': 'Success'
        }
        
        # Extract Name
        data['name'] = self.extract_candidate_name_from_text(text)
        
        # Extract Present Rank
        present_rank_match = re.search(r'Present Rank\s+([^\n]+)', text)
        data['present_rank'] = present_rank_match.group(1).strip() if present_rank_match else ''
        
        # Extract Email with label-priority + noise filtering.
        data['email'] = self._extract_best_email(text)
        
        # Extract Country (from address field)
        country_match = re.search(r'City, State, Country\s+[^,]+,\s*[^,]+,\s*([^\n]+)', text)
        data['country'] = country_match.group(1).strip() if country_match else ''
        
        # Extract Mobile Number
        mobile_match = re.search(r'Mobile No\s+([^\n]+)', text)
        if mobile_match:
            mobile_raw = mobile_match.group(1).strip()
            # Clean up mobile number (take first if multiple)
            data['mobile_no'] = mobile_raw.split(',')[0].strip()
        else:
            data['mobile_no'] = ''
        
        # Add AI match reason
        data['ai_match_reason'] = match_reason or 'Manually verified'
        
        # Log extraction quality
        missing_fields = [k for k, v in data.items() if not v and k != 'ai_match_reason']
        if missing_fields:
            print(f"[EXTRACTOR WARNING] Missing fields for {data['resume']}: {missing_fields}")
        
        return data
    
    def validate_data(self, data):
        """Check if extracted data has minimum required fields"""
        required_fields = ['name', 'email', 'resume']
        missing = [f for f in required_fields if not data.get(f)]
        
        if missing:
            print(f"[EXTRACTOR] Validation failed for {data.get('resume', 'unknown')}: missing {missing}")
            return False
        
        return True
