import logging
import os
from datetime import datetime

def setup_logger(session_id):
    """Sets up a logger to write to a unique, timestamped file."""
    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"download_session_{timestamp}_{session_id}.log"
    log_filepath = os.path.join(logs_dir, log_filename)
    
    logger = logging.getLogger(f"download_logger_{session_id}")
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    handler = logging.FileHandler(log_filepath)
    handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    
    return logger, log_filepath