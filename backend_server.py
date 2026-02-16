from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import configparser
import os
import re
import sys
import uuid
import json

# Dependency Check & Imports
try:
    import PyPDF2
except ImportError:
    print("\n--- FATAL ERROR --- \nPlease run: pip install PyPDF2\n")
    sys.exit(1)

from scraper_engine import Scraper
from ai_analyzer import Analyzer
from logger_config import setup_logger
from resume_extractor import ResumeExtractor
from csv_manager import CSVManager

# --- App Initialization ---
app = Flask(__name__)
CORS(app) 

# --- Configuration ---
config = configparser.ConfigParser()
config.read('config.ini')
creds = config['Credentials']
settings = config['Settings']

# --- Global State ---
scraper_session = None

# --- Initialize Extractors ---
resume_extractor = ResumeExtractor()
csv_manager = CSVManager(base_folder='Verified_Resumes')


def _resolve_within_base(base_dir, *parts):
    """Resolve a path and ensure it stays within base_dir."""
    base_abs = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_abs, *parts))
    if os.path.commonpath([base_abs, candidate]) != base_abs:
        raise ValueError("Path escapes base directory")
    return candidate


def _is_safe_name(value):
    """Allow only single path components (no separators/traversal)."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or value in {'.', '..'}:
        return False
    return value == os.path.basename(value)


def _extract_candidate_id_from_filename(filename):
    """Extract numeric candidate ID from {rank}_{candidate_id}.pdf pattern."""
    match = re.search(r'_(\d+)\.pdf$', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

# --- NEW: Serve the Frontend ---
@app.route('/')
def serve_frontend():
    """Serve the frontend HTML file"""
    return send_from_directory('.', 'frontend.html')

# --- API Endpoints ---
@app.route('/start_session', methods=['POST'])
def start_session():
    global scraper_session
    data = request.json
    mobile_number = data.get('mobileNumber')
    if scraper_session: scraper_session.quit()
    scraper_session = Scraper(settings['Default_Download_Folder'])
    result = scraper_session.start_session(creds['Username'], creds['Password'], mobile_number)
    return jsonify(result)

@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    global scraper_session
    data = request.json
    otp = data.get('otp')
    if not scraper_session:
        return jsonify({"success": False, "message": "Session not started."})
    
    login_result = scraper_session.verify_otp(otp)
    if login_result["success"]:
        try:
            ranks_str = config.get('Ranks', 'rank_options', fallback='').strip()
            ship_types_str = config.get('ShipTypes', 'ship_type_options', fallback='').strip()
            login_result["ranks"] = [r.strip() for r in ranks_str.split('\n') if r.strip()]
            login_result["ship_types"] = [s.strip() for s in ship_types_str.split('\n') if s.strip()]
        except Exception as e:
            return jsonify({"success": False, "message": f"Error in config.ini: {e}"})
    return jsonify(login_result)

@app.route('/start_download', methods=['POST'])
def start_download():
    global scraper_session
    data = request.json
    if not scraper_session or not scraper_session.driver:
        return jsonify({"success": False, "message": "Website session is not active or has expired."})

    session_id = str(uuid.uuid4())
    logger, log_filepath = setup_logger(session_id)
    
    result = scraper_session.download_resumes(
        data['rank'], 
        data['shipType'], 
        data['forceRedownload'], 
        logger
    )
    
    result['log_file'] = log_filepath
    return jsonify(result)

@app.route('/disconnect_session', methods=['POST'])
def disconnect_session():
    global scraper_session
    if scraper_session:
        scraper_session.quit()
        scraper_session = None
    return jsonify({"success": True, "message": "Session disconnected successfully."})

@app.route('/get_rank_folders', methods=['GET'])
def get_rank_folders():
    base_folder = settings['Default_Download_Folder']
    if not os.path.isdir(base_folder):
        return jsonify({"success": False, "folders": [], "message": "Download folder not found."})
    
    try:
        subfolders = [d for d in os.listdir(base_folder) if os.path.isdir(os.path.join(base_folder, d))]
        return jsonify({"success": True, "folders": sorted(subfolders)})
    except Exception as e:
        return jsonify({"success": False, "folders": [], "message": str(e)})

@app.route('/analyze_stream', methods=['GET'])
def analyze_stream():
    """Stream analysis progress using Server-Sent Events"""
    prompt = request.args.get('prompt')
    rank_folder = request.args.get('rank_folder')

    def generate():
        try:
            if not prompt or not rank_folder:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Missing required data'})}\n\n"
                return
            
            target_folder = os.path.join(settings['Default_Download_Folder'], rank_folder)
            
            # Create analyzer and run streaming analysis
            analyzer = Analyzer(creds['Gemini_API_Key'])
            
            # Stream progress events
            for progress_event in analyzer.run_analysis_stream(target_folder, prompt):
                yield f"data: {json.dumps(progress_event)}\n\n"
            
        except Exception as e:
            print(f"[BACKEND ERROR] {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/analyze', methods=['POST'])
def analyze():
    """Non-streaming endpoint for backward compatibility"""
    try:
        data = request.json
        prompt = data.get('prompt')
        rank_folder = data.get('rank_folder')

        if not prompt or not rank_folder:
            return jsonify({"success": False, "message": "AI prompt and a rank folder selection are required."}), 400
        
        target_folder = os.path.join(settings['Default_Download_Folder'], rank_folder)
        
        print(f"[BACKEND] Starting analysis for rank folder: {rank_folder}")
        print(f"[BACKEND] Prompt: {prompt}")
        
        analyzer = Analyzer(creds['Gemini_API_Key'])
        result = analyzer.run_analysis(target_folder, prompt)
        
        print(f"[BACKEND] Analysis complete. Success: {result.get('success')}")
        return jsonify(result)
    
    except Exception as e:
        print(f"[BACKEND ERROR] {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    """Store user feedback for learning"""
    try:
        data = request.json
        
        analyzer = Analyzer(creds['Gemini_API_Key'])
        analyzer.store_feedback(
            filename=data.get('filename'),
            query=data.get('query'),
            llm_decision=data.get('llm_decision'),
            llm_reason=data.get('llm_reason'),
            llm_confidence=data.get('llm_confidence'),
            user_decision=data.get('user_decision'),
            user_notes=data.get('user_notes', '')
        )
        
        return jsonify({"success": True, "message": "Feedback recorded successfully"})
    
    except Exception as e:
        print(f"[ERROR] Feedback submission failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_resume/<path:rank_folder>/<path:filename>')
def get_resume(rank_folder, filename):
    try:
        base_dir = os.path.abspath(settings['Default_Download_Folder'])
        full_path = _resolve_within_base(base_dir, rank_folder, filename)

        if not os.path.isfile(full_path):
            print(f"[ERROR] File not found: {full_path}")
            return "File not found", 404

        directory = os.path.dirname(full_path)
        safe_filename = os.path.basename(full_path)

        if not safe_filename:
            return "Access denied.", 403

        print(f"[PDF] Serving file: {full_path}")
        return send_from_directory(directory, safe_filename, as_attachment=False)

    except ValueError:
        print("[SECURITY] Access denied. Invalid path request.")
        return "Access denied.", 403
    except FileNotFoundError:
        return "File not found", 404
    except Exception as e:
        print(f"[ERROR] Exception in get_resume: {e}")
        import traceback
        traceback.print_exc()
        return str(e), 500

@app.route('/verify_resumes', methods=['POST'])
def verify_resumes():
    """Verify resumes by logging initial verification events to master CSV."""
    data = request.json
    rank_folder = data.get('rank_folder')
    filenames = data.get('filenames')
    
    # Get AI match data for each file (if available from frontend)
    match_data = data.get('match_data', {})  # {filename: {reason: "...", confidence: 0.9}}
    ai_prompt = data.get('ai_prompt', '')
    search_ship_type = data.get('search_ship_type', '')

    if not rank_folder or not filenames:
        return jsonify({"success": False, "message": "Missing required data."}), 400
    if not _is_safe_name(rank_folder):
        return jsonify({"success": False, "message": "Invalid rank folder."}), 400
    if not isinstance(filenames, list):
        return jsonify({"success": False, "message": "Invalid filenames payload."}), 400
    for filename in filenames:
        if not _is_safe_name(filename) or not filename.lower().endswith('.pdf'):
            return jsonify({"success": False, "message": f"Invalid filename: {filename}"}), 400

    source_base_dir = os.path.abspath(settings['Default_Download_Folder'])
    source_folder = _resolve_within_base(source_base_dir, rank_folder)

    try:
        processed_files = 0
        csv_exports = 0
        extraction_errors = []
        
        for filename in filenames:
            source_path = _resolve_within_base(source_folder, filename)

            if os.path.isfile(source_path):
                candidate_id = _extract_candidate_id_from_filename(filename)
                if not candidate_id:
                    extraction_errors.append(f"{filename}: Could not extract candidate ID from filename")
                    continue

                ai_match_reason = match_data.get(filename, {}).get('reason', 'Manually verified')
                
                print(f"[VERIFY] Extracting data from {filename}...")
                resume_data = resume_extractor.extract_resume_data(
                    source_path,
                    candidate_id=candidate_id,
                    match_reason=ai_match_reason
                )

                csv_ok = csv_manager.log_event(
                    candidate_id=candidate_id,
                    filename=filename,
                    event_type='initial_verification',
                    status='New',
                    notes='',
                    rank_applied_for=rank_folder,
                    search_ship_type=search_ship_type,
                    ai_prompt=ai_prompt,
                    ai_reason=ai_match_reason,
                    extracted_data=resume_data
                )

                if csv_ok:
                    csv_exports += 1
                    print(f"[VERIFY] Event logged for {filename}")
                else:
                    extraction_errors.append(f"{filename}: CSV event logging failed")

                if resume_data.get('extraction_status') != 'Success':
                    extraction_errors.append(f"{filename}: Data extraction failed")

                processed_files += 1
            else:
                extraction_errors.append(f"{filename}: Source file not found")
        
        # Prepare response message
        message = f"Successfully processed {processed_files} file(s). "
        message += f"Logged {csv_exports} event(s) to master CSV."
        
        if extraction_errors:
            message += f" Warnings: {len(extraction_errors)} file(s) had extraction issues."
        
        # Get CSV stats for response
        csv_stats = csv_manager.get_csv_stats()
        
        return jsonify({
            "success": True, 
            "message": message,
            "processed": processed_files,
            "csv_exports": csv_exports,
            "errors": extraction_errors,
            "csv_stats": csv_stats
        })

    except Exception as e:
        print(f"[ERROR] Verify resumes failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_dashboard_data', methods=['GET'])
def get_dashboard_data():
    """Fetch CSV data for dashboard display"""
    try:
        view_type = request.args.get('view', 'master')  # 'master' or 'rank'
        rank_name = request.args.get('rank_name', '')
        
        if view_type not in ('master', 'rank'):
            return jsonify({"success": False, "message": "Invalid view type or missing rank_name"}), 400
        if view_type == 'rank' and not rank_name:
            return jsonify({"success": False, "message": "Invalid view type or missing rank_name"}), 400

        rows = csv_manager.get_latest_status_per_candidate(rank_name if view_type == 'rank' else '')

        if rows.empty:
            return jsonify({
                "success": True,
                "view": view_type,
                "total_count": 0,
                "data": [],
                "message": "No data available yet"
            })
        data = []
        for _, row in rows.iterrows():
            data.append({
                "candidate_id": row.get('Candidate_ID', ''),
                "filename": row.get('Filename', ''),
                "resume_url": row.get('Resume_URL', ''),
                "date_added": row.get('Date_Added', ''),
                "event_type": row.get('Event_Type', ''),
                "status": row.get('Status', ''),
                "notes": row.get('Notes', ''),
                "rank_applied_for": row.get('Rank_Applied_For', ''),
                "search_ship_type": row.get('Search_Ship_Type', ''),
                "name": row.get('Name', ''),
                "present_rank": row.get('Present_Rank', ''),
                "email": row.get('Email', ''),
                "country": row.get('Country', ''),
                "mobile_no": row.get('Mobile_No', ''),
                "ai_match_reason": row.get('AI_Match_Reason', '')
            })
        
        return jsonify({
            "success": True,
            "view": view_type,
            "rank_name": rank_name if view_type == 'rank' else None,
            "total_count": len(data),
            "data": data
        })
    
    except Exception as e:
        print(f"[ERROR] Dashboard data fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_available_ranks', methods=['GET'])
def get_available_ranks():
    """Get list of ranks from master CSV latest-candidate view."""
    try:
        rank_counts = csv_manager.get_rank_counts()
        ranks = []
        for row in rank_counts:
            rank_name = row.get('Rank_Applied_For', '')
            if rank_name:
                ranks.append({
                    "rank": rank_name,
                    "display_name": rank_name.replace('_', ' '),
                    "count": int(row.get('count', 0))
                })

        ranks.sort(key=lambda x: x['rank'])
        
        return jsonify({"success": True, "ranks": ranks})
    
    except Exception as e:
        print(f"[ERROR] Get available ranks failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == '__main__':
    os.makedirs(settings['Default_Download_Folder'], exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    print("\n" + "="*70)
    print("🚀 NjordHR Backend Server - With Dashboard")
    print("="*70)
    print("\n🌐 Open your browser and go to:")
    print("   👉 http://127.0.0.1:5000")
    print("\n" + "="*70 + "\n")
    
    app.run(port=5000, debug=False, threaded=True)
