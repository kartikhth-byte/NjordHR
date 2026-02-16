# csv_manager.py - CSV Export Manager with Plain Filename & URL

import os
import csv
import pandas as pd
from datetime import datetime

class CSVManager:
    """Manages CSV export with plain filename and URL columns"""
    
    def __init__(self, base_folder='Verified_Resumes', server_url='http://127.0.0.1:5000'):
        self.base_folder = base_folder
        self.server_url = server_url
        self.master_csv = os.path.join(base_folder, 'verified_resumes.csv')
        
        # Ensure base folder exists
        os.makedirs(base_folder, exist_ok=True)
    
    def get_rank_csv_path(self, rank):
        """Get path to rank-specific CSV file"""
        rank_folder = os.path.join(self.base_folder, rank)
        os.makedirs(rank_folder, exist_ok=True)
        return os.path.join(rank_folder, f"{rank}_verified.csv")
    
    def append_to_csv(self, csv_path, data_dict, rank):
        """
        Append data to CSV file, or create if doesn't exist.
        Updates existing row if filename already exists.
        """
        # Define column order - Filename, Resume_URL, and Date_Added first
        columns = [
            'Filename',
            'Resume_URL',
            'Date_Added',
            'Name', 
            'Present_Rank',
            'Email',
            'Country',
            'Mobile_No',
            'AI_Match_Reason'
        ]
        
        # Build the URL for this resume
        resume_url = f"{self.server_url}/get_resume/{rank}/{data_dict['resume']}"
        
        # Generate ISO 8601 timestamp
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # Map extracted fields to CSV columns
        csv_row = {
            'Filename': data_dict['resume'],
            'Resume_URL': resume_url,
            'Date_Added': timestamp,
            'Name': data_dict.get('name', ''),
            'Present_Rank': data_dict.get('present_rank', ''),
            'Email': data_dict.get('email', ''),
            'Country': data_dict.get('country', ''),
            'Mobile_No': data_dict.get('mobile_no', ''),
            'AI_Match_Reason': data_dict.get('ai_match_reason', '')
        }
        
        try:
            if os.path.exists(csv_path):
                # Read existing CSV
                df = pd.read_csv(csv_path)
                
                # Check if this resume already exists (by checking Filename column)
                if data_dict['resume'] in df['Filename'].values:
                    # Update existing row
                    idx = df[df['Filename'] == data_dict['resume']].index[0]
                    for col in columns:
                        df.at[idx, col] = csv_row[col]
                    print(f"[CSV] Updated existing entry for {data_dict['resume']}")
                else:
                    # Append new row
                    df = pd.concat([df, pd.DataFrame([csv_row])], ignore_index=True)
                    print(f"[CSV] Added new entry for {data_dict['resume']}")
            else:
                # Create new CSV
                df = pd.DataFrame([csv_row], columns=columns)
                print(f"[CSV] Created new CSV: {csv_path}")
            
            # Save CSV
            df.to_csv(csv_path, index=False)
            return True
            
        except Exception as e:
            print(f"[CSV ERROR] Failed to update {csv_path}: {e}")
            return False
    
    def export_resume_data(self, resume_data, rank):
        """
        Export resume data to both master CSV and rank-specific CSV.
        
        Args:
            resume_data: dict with extracted resume fields
            rank: rank folder name (e.g., 'Chief_Officer')
            
        Returns:
            tuple: (master_success, rank_success)
        """
        # Update master CSV
        master_success = self.append_to_csv(self.master_csv, resume_data, rank)
        
        # Update rank-specific CSV
        rank_csv = self.get_rank_csv_path(rank)
        rank_success = self.append_to_csv(rank_csv, resume_data, rank)
        
        return (master_success, rank_success)
    
    def get_csv_stats(self):
        """Get statistics about exported CSVs"""
        stats = {
            'master_csv_exists': os.path.exists(self.master_csv),
            'master_csv_rows': 0,
            'rank_csvs': []
        }
        
        if stats['master_csv_exists']:
            try:
                df = pd.read_csv(self.master_csv)
                stats['master_csv_rows'] = len(df)
            except:
                pass
        
        # Find all rank CSVs
        if os.path.exists(self.base_folder):
            for rank_folder in os.listdir(self.base_folder):
                rank_csv = os.path.join(self.base_folder, rank_folder, f"{rank_folder}_verified.csv")
                if os.path.exists(rank_csv):
                    try:
                        df = pd.read_csv(rank_csv)
                        stats['rank_csvs'].append({
                            'rank': rank_folder,
                            'rows': len(df),
                            'path': rank_csv
                        })
                    except:
                        pass
        
        return stats