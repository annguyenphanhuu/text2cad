#!/usr/bin/env python
"""
DFM Shape ChatBot - Main entry point

This script starts the FastAPI server for the DFM Shape ChatBot application.
"""
import os
import sys
import logging
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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

        db_host = os.getenv('MYSQL_HOST', 'localhost')
        db_port = os.getenv('MYSQL_PORT', '3306')
        db_user = os.getenv('MYSQL_USER', 'root')
        db_name = os.getenv('MYSQL_DATABASE', 'local')

        # One startup banner. The details below used to be five separate INFO
        # lines that repeated what setup_logging() had just printed.
        logger.info(
            f"DFM Shape ChatBot → http://0.0.0.0:{port} | "
            f"db={db_user}@{db_host}:{db_port}/{db_name} | "
            f"python={sys.version.split()[0]} | cwd={Path.cwd()}"
        )

        log_level = os.getenv('LOG_LEVEL', 'INFO')
        uvicorn.run(
            "src.api.main:app",
            host="0.0.0.0",
            port=port,
            reload=False,  # Set to True for development
            log_level=log_level.lower(),
            # Let uvicorn's own loggers propagate to the root logger instead of
            # installing their own handlers — otherwise its records bypass our
            # formatter and print as unaligned `INFO:     …` lines.
            log_config=None,
        )
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        logger.error(f"Traceback: {__import__('traceback').format_exc()}")
        sys.exit(1)

if __name__ == '__main__':
    main()