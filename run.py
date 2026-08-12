#!/usr/bin/env python
"""
DFM Shape ChatBot - Main entry point

This script starts the FastAPI server for the DFM Shape ChatBot application.
"""
import os
import sys

# Configure FAISS for CPU-only usage before any other imports
os.environ['FAISS_DISABLE_GPU'] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
os.environ['OMP_NUM_THREADS'] = '1'

import logging
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Suppress FAISS GPU warnings
logging.getLogger('faiss').setLevel(logging.ERROR)

def log_startup_memory():
    """Log system memory at application startup."""
    try:
        import psutil
        memory = psutil.virtual_memory()
        
        total_ram_gb = memory.total / (1024**3)
        available_ram_gb = memory.available / (1024**3)
        used_ram_gb = memory.used / (1024**3)
        ram_percent = memory.percent
        
        logger = logging.getLogger("dfm-shapechatbot")

        
            

        
    except ImportError:
        logger = logging.getLogger("dfm-shapechatbot")
        logger.warning("psutil not available - cannot monitor memory usage")
    except Exception as e:
        logger = logging.getLogger("dfm-shapechatbot")
        logger.error(f"Failed to get startup memory information: {e}")

# Set console encoding to UTF-8 for Windows before configuring logging
if sys.platform.startswith('win'):
    import codecs
    # Only set encoding if not already set
    if not hasattr(sys.stdout, 'encoding') or sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            # Fallback for older Python versions
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

try:
    from src.core.logging_config import setup_logging
    
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    enable_flow_logging = os.getenv('ENABLE_FLOW_LOGGING', 'true').lower() == 'true'
    enable_verbose_logging = os.getenv('ENABLE_VERBOSE_LOGGING', 'false').lower() == 'true'
    
    setup_logging(
        log_level=log_level,
        log_dir="logs",
        enable_file_logging=True,
        enable_console_logging=True,
        enable_flow_logging=enable_flow_logging,
        enable_verbose_logging=enable_verbose_logging,
        max_log_size=20 * 1024 * 1024,  # 20MB
        backup_count=10
    )
    
    logger = logging.getLogger("dfm-shapechatbot")
    
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("server.log", encoding='utf-8')
        ]
    )
    logger = logging.getLogger("dfm-shapechatbot")
    logger.warning("Could not import enhanced logging configuration, using basic setup")

def main():
    """
    Main entry point for the application.
    Starts the FastAPI server with the specified configuration.
    """
    try:
        # Get port from environment variable or use default
        port = int(os.getenv('UVICORN_PORT', 80))

        # Log startup information
        logger.info(f"Starting DFM Shape ChatBot server on port {port}")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Working directory: {Path.cwd()}")

        # Log database connection info
        db_host = os.getenv('MYSQL_HOST', 'localhost')
        db_port = os.getenv('MYSQL_PORT', '3306')
        db_user = os.getenv('MYSQL_USER', 'root')
        db_name = os.getenv('MYSQL_DATABASE', 'local')
        logger.info(f"Database configuration: {db_user}@{db_host}:{db_port}/{db_name}")

        # Log memory status before starting server
        


        log_level = os.getenv('LOG_LEVEL', 'INFO')
        enable_flow_logging = os.getenv('ENABLE_FLOW_LOGGING', 'true').lower() == 'true'
        enable_verbose_logging = os.getenv('ENABLE_VERBOSE_LOGGING', 'false').lower() == 'true'
        logger.info(f"Log level: {log_level}, Flow logging: {enable_flow_logging}, Verbose: {enable_verbose_logging}")

        logger.info(f"Starting uvicorn server...")
        uvicorn.run(
            "src.api.main:app",
            host="0.0.0.0",
            port=port,
            reload=False,  # Set to True for development
            log_level=log_level.lower()
        )
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        logger.error(f"Traceback: {__import__('traceback').format_exc()}")
        sys.exit(1)

if __name__ == '__main__':

    main()