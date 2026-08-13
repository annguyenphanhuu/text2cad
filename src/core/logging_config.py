"""
Comprehensive logging configuration for the DFM Shape ChatBot application.
This module provides structured logging with different levels and formatters
to improve debugging and monitoring capabilities.
"""

import logging
import logging.handlers
import sys
import re
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels."""

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }

    def format(self, record):
        # Add color to the levelname
        if hasattr(record, 'levelname'):
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            record.levelname = f"{color}{record.levelname}{self.COLORS['RESET']}"

        return super().format(record)


class MainFlowFilter(logging.Filter):
    """Filter to only show main execution flow logs and hide verbose debugging."""

    # Main flow keywords that should always be shown
    MAIN_FLOW_KEYWORDS = [
        'Starting DFM Shape ChatBot',
        'Database configuration',
        'Log level',
        'Starting uvicorn server',
        'CAD generation request:',
        'Generated session ID:',
        'Successfully created session',
        'Analysis completed',
        'Parameters complete',
        'Code generation completed',
        'Export completed',
        'All processing completed',
        'Error',
        'WARNING',
        'CRITICAL'
    ]

    # Verbose prefixes that should be filtered out at INFO level
    VERBOSE_PREFIXES = [
        '[SSE_PARAMS]', '[SSE_EVENT]', '[SSE_YIELD]', '[SSE_STREAM]',
        '[STREAM_STEP]', '[STREAM_YIELD]', '[STREAM_WEB]', '[STREAM_PROGRESS]',
        '[AGENT_STATE]', '[AGENT_CONTINUATION]', '[AGENT_INFO]', '[AGENT_CHAIN]',
        '[AGENT_QUESTIONS]', '[AGENT_PARAMS]', '[AGENT_CODEGEN]', '[AGENT_EXPORT]',
        '[SESSION_RESOLVE]', '[SESSION_GET]', '[SESSION_CREATE]',
        '[RESULT_PROCESS]', '[EXPORT_PATHS]', '[CHAT_HISTORY_ADD]', '[DOWNLOAD]'
    ]

    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True

        msg = str(record.msg)

        # Always show main flow messages
        for keyword in self.MAIN_FLOW_KEYWORDS:
            if keyword in msg:
                return True

        # Filter out verbose debugging messages at INFO level
        for prefix in self.VERBOSE_PREFIXES:
            if msg.startswith(prefix):
                return False

        # Allow other messages through
        return True


class ProcessFlowFilter(logging.Filter):
    """Filter to identify and categorize process flow logs."""

    FLOW_CATEGORIES = [
        'FLOW', 'SESSION', 'AGENT', 'STREAM', 'EXPORT', 'WEB_SEARCH',
        'EDIT_MODE', 'RESULT_PROCESS', 'CHAT_HISTORY', 'SSE'
    ]

    def filter(self, record):
        # Add flow category if present in message
        if hasattr(record, 'msg'):
            msg = str(record.msg)
            for category in self.FLOW_CATEGORIES:
                if f"[{category}" in msg:
                    record.flow_category = category
                    break
            else:
                record.flow_category = "GENERAL"

        return True


class CompactFileFormatter(logging.Formatter):
    """
    Compact formatter for file logs - Phase 1 optimizations:
    1. Removes ANSI color codes
    2. Replaces emojis with text
    3. Extracts and places session_id at the beginning
    4. Compact format without filename:lineno in production
    """
    
    # Emoji to text mapping
    EMOJI_MAP = {
        '📤': '[SEND]',
        '📥': '[RECV]',
        '✅': '[OK]',
        '❌': '[FAIL]',
        '⚠️': '[WARN]',
        '🔍': '[CHECK]',
        '🎧': '[LISTEN]',
        '🔌': '[CONNECT]',
        '📊': '[PROGRESS]',
        '📢': '[STATUS]',
        '📋': '[INFO]',
        '📄': '[FILE]',
        '🔧': '[FIX]',
        '⏳': '[WAIT]',
        '🚀': '[START]',
        '🏁': '[END]',
        '📈': '[PROGRESS]',
        '📝': '[NOTE]',
        '💾': '[SAVE]',
        '🎉': '[SUCCESS]',
    }
    
    # ANSI color code pattern
    ANSI_PATTERN = re.compile(r'\033\[[0-9;]*m')
    
    # Session ID pattern (session_xxxxxx_xxxxxx)
    SESSION_PATTERN = re.compile(r'session_[a-f0-9]{6}_[0-9]{6}')
    
    def __init__(self, include_location=False, *args, **kwargs):
        """
        Args:
            include_location: If True, include filename:lineno (for debug mode)
        """
        super().__init__(*args, **kwargs)
        self.include_location = include_location
    
    def _remove_ansi_codes(self, text):
        """Remove ANSI color codes from text."""
        return self.ANSI_PATTERN.sub('', text)
    
    def _replace_emojis(self, text):
        """Replace emojis with text equivalents."""
        for emoji, replacement in self.EMOJI_MAP.items():
            text = text.replace(emoji, replacement)
        return text
    
    def _extract_session_id(self, text):
        """Extract session_id from message if present, but keep it in text for searchability."""
        match = self.SESSION_PATTERN.search(text)
        if match:
            session_id = match.group(0)
            # DON'T remove session_id from text - keep it for searchability in logs
            # Just return both the extracted session_id and original text
            return session_id, text
        return None, text
    
    def format(self, record):
        # Get original message
        original_msg = record.getMessage()
        
        # Remove ANSI codes
        clean_msg = self._remove_ansi_codes(original_msg)
        
        # Replace emojis
        clean_msg = self._replace_emojis(clean_msg)
        
        # Extract session_id from message if present
        session_id_from_msg, clean_msg = self._extract_session_id(clean_msg)
        
        # Clean up extra spaces
        clean_msg = re.sub(r'\s+', ' ', clean_msg).strip()
        
        # Extract module name from logger name (e.g., 'src.core.text_to_cad_agent' -> 'text_to_cad_agent')
        logger_name = record.name
        if '.' in logger_name:
            module_name = logger_name.split('.')[-1]
        else:
            module_name = logger_name
        
        # Set module name
        record.module = module_name
        
        # Set the cleaned message
        record.msg = clean_msg
        record.args = ()  # Clear args since we've formatted the message
        
        # Get session_id from context (for multi-user traceability)
        # Note: user_id is not needed since session_id is sufficient for identification
        try:
            from ..utils.context_manager import get_session_id
            session_id_from_context = get_session_id()
        except:
            session_id_from_context = None
        
        # Prefer session_id from message, fallback to context
        final_session_id = session_id_from_msg or session_id_from_context
        
        # Build format string with session_id at the beginning for multi-user traceability
        if final_session_id:
            # Has session_id: include it at the beginning
            if self.include_location:
                fmt = '[%(asctime)s] [%(session_id)s] %(levelname)-8s [%(module)s] %(filename)s:%(lineno)d - %(message)s'
            else:
                fmt = '[%(asctime)s] [%(session_id)s] %(levelname)-8s [%(module)s] %(message)s'
            record.session_id = final_session_id
        else:
            # No session_id: use format without session_id
            if self.include_location:
                fmt = '[%(asctime)s] %(levelname)-8s [%(module)s] %(filename)s:%(lineno)d - %(message)s'
            else:
                fmt = '[%(asctime)s] %(levelname)-8s [%(module)s] %(message)s'
        
        # Create a temporary formatter with the dynamic format
        temp_formatter = logging.Formatter(
            fmt=fmt,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        return temp_formatter.format(record)


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "logs",
    enable_file_logging: bool = True,
    enable_console_logging: bool = True,
    enable_flow_logging: bool = True,
    enable_verbose_logging: bool = False,  # New parameter for verbose debugging
    max_log_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    include_location: bool = False  # Phase 1: Include filename:lineno in file logs (default: False for compact)
):
    """
    Setup comprehensive logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        enable_file_logging: Whether to enable file logging
        enable_console_logging: Whether to enable console logging
        enable_flow_logging: Whether to enable separate flow logging
        enable_verbose_logging: Whether to show verbose debugging logs
        max_log_size: Maximum size of each log file in bytes
        backup_count: Number of backup log files to keep
    """

    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    # Convert log level string to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Clear any existing handlers
    logging.getLogger().handlers.clear()

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Compact formatter for application.log (Phase 1 optimizations)
    compact_formatter = CompactFileFormatter(
        include_location=include_location,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Detailed formatter for files (kept for backward compatibility with other log files)
    detailed_formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Simple formatter for console (clean main flow)
    console_formatter = ColoredFormatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )

    # Flow-specific formatter
    flow_formatter = logging.Formatter(
        fmt='%(asctime)s - [%(flow_category)s] - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handlers = []

    # Console handler with main flow filter
    if enable_console_logging:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(console_formatter)

        # Add main flow filter unless verbose logging is enabled
        if not enable_verbose_logging:
            console_handler.addFilter(MainFlowFilter())

        handlers.append(console_handler)

    # Main application log file handler (Phase 1: using compact formatter)
    if enable_file_logging:
        main_log_file = log_path / "application.log"
        file_handler = logging.handlers.RotatingFileHandler(
            filename=main_log_file,
            maxBytes=max_log_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(compact_formatter)  # Phase 1: Use compact formatter
        handlers.append(file_handler)

    # Clean main flow log file (filtered)
    if enable_file_logging:
        main_flow_file = log_path / "main_flow.log"
        main_flow_handler = logging.handlers.RotatingFileHandler(
            filename=main_flow_file,
            maxBytes=max_log_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        main_flow_handler.setLevel(numeric_level)
        main_flow_handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        main_flow_handler.addFilter(MainFlowFilter())
        handlers.append(main_flow_handler)

    # Process flow log file handler (detailed debugging)
    if enable_flow_logging:
        flow_log_file = log_path / "process_flow.log"
        flow_handler = logging.handlers.RotatingFileHandler(
            filename=flow_log_file,
            maxBytes=max_log_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        flow_handler.setLevel(numeric_level)
        flow_handler.setFormatter(flow_formatter)
        flow_handler.addFilter(ProcessFlowFilter())
        handlers.append(flow_handler)

    # Error-only log file handler
    if enable_file_logging:
        error_log_file = log_path / "errors.log"
        error_handler = logging.handlers.RotatingFileHandler(
            filename=error_log_file,
            maxBytes=max_log_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(detailed_formatter)
        handlers.append(error_handler)

    # Add all handlers to root logger
    for handler in handlers:
        root_logger.addHandler(handler)

    # Set specific logger levels for noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Log the configuration
    root_logger.info(f"Logging configured - Level: {log_level}, Verbose: {enable_verbose_logging}")
    root_logger.info(f"Log directory: {log_path.absolute()}")

    return root_logger


# Configure UTF-8 encoding for Windows compatibility
if sys.platform.startswith('win'):
    import codecs
    if not hasattr(sys.stdout, 'encoding') or sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
