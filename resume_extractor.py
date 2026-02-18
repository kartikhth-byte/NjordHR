# resume_extractor.py - Resume Data Extraction Module

import re
import os
import PyPDF2

class ResumeExtractor:
    """Extracts structured data from SeaJob resumes"""
    
    def __init__(self):
        pass

    _EMAIL_RE = re.compile(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})')

    def _clean_email(self, value):
        value = value.strip().strip('.,;:()[]{}<>').lower()
        value = re.sub(r'\s*@\s*', '@', value)
        value = re.sub(r'\s*\.\s*', '.', value)
        value = value.replace(' ', '')
        return value

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
        name_match = re.search(r'Name\s+([^\n]+)', text)
        data['name'] = name_match.group(1).strip() if name_match else ''
        
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
