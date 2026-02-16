# resume_extractor.py - Resume Data Extraction Module

import re
import os
from datetime import datetime
import PyPDF2

class ResumeExtractor:
    """Extracts structured data from SeaJob resumes"""
    
    def __init__(self):
        pass
    
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
    
    def extract_resume_data(self, pdf_path, match_reason=""):
        """
        Extract structured data from resume PDF.
        
        Args:
            pdf_path: Path to the PDF file
            match_reason: AI match reason from the verification process
            
        Returns:
            dict with extracted data
        """
        text = self.extract_text_from_pdf(pdf_path)
        
        if not text:
            # Return minimal data if extraction fails
            return {
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
            'resume': os.path.basename(pdf_path),
            'extraction_status': 'Success'
        }
        
        # Extract Name
        name_match = re.search(r'Name\s+([^\n]+)', text)
        data['name'] = name_match.group(1).strip() if name_match else ''
        
        # Extract Present Rank
        present_rank_match = re.search(r'Present Rank\s+([^\n]+)', text)
        data['present_rank'] = present_rank_match.group(1).strip() if present_rank_match else ''
        
        # Extract Email - use proper email regex pattern
        email_match = re.search(r'Email Address.*?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text)
        data['email'] = email_match.group(1).strip() if email_match else ''
        
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