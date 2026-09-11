"""
Logging configuration for the DFM Shape ChatBot.

Three audiences, three shapes:

* CONSOLE — one human watching one run. Lines stay short: the session id that
  repeats on nearly every record collapses into a 6-char tag printed once per
  line, and internal-plumbing tags are muted. Set ENABLE_VERBOSE_LOGGING=true
  to unmute everything.
* application.log — the full record stream, one line each, session id inlined
  so a single grep reconstructs one user's run.
* main_flow.log — the console view, with full timestamps and no colors.
* process_flow.log / errors.log — post-mortem material, unchanged.

Formatters here never mutate the LogRecord. Several handlers format the same
record in sequence, so a formatter that rewrites `record.msg` (or wraps
`record.levelname` in ANSI codes) corrupts what every later handler sees.
"""

import logging
import logging.handlers
import sys
import re
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Session-id handling
# ─────────────────────────────────────────────────────────────────────────────
# Session ids look like `session_cfd189_870869`; some call sites log a
# truncated `session_cfd189_...` form.
_SID = r'session_[0-9a-f]{6}_(?:\d{6}|\.{3})'

#: Same shape as _SID, but capturing the short tag. Kept separate because _SID
#: is spliced into an alternation below, where a repeated group name is illegal.
SESSION_ID_PATTERN = re.compile(r'session_(?P<sid>[0-9a-f]{6})_(?:\d{6}|\.{3})')

# The same session id is repeated as a `| session=…` / `| user_id=…` trailer on
# nearly every record. On the console it is redundant with the per-line tag, so
# these fragments are stripped there (application.log keeps them verbatim).
_SESSION_NOISE_PATTERN = re.compile('|'.join([
    r'\s*\|?\s*(?:session|session_id|user_id)\s*=\s*' + _SID,
    r'\s*\|\s*Session:\s*' + _SID,
    r'\s*\[Session:\s*' + _SID + r'\]',
    r'\s+(?:for|in|to)\s+session\s+' + _SID,
    r'\s+session\s+' + _SID,
]))

#: Separators left dangling once a trailer is cut out of the middle of a message.
#: The leading-tag rule is anchored to `^[TAG]` rather than to any `]`, so a
#: bracketed value mid-message ("Classes: [x] | Retrieved: 6") keeps its pipe.
_DANGLING_SEPARATORS = (
    (re.compile(r'\|\s*\|'), '|'),                       # a | b | c, b cut → a || c
    (re.compile(r'^(\[[A-Za-z_0-9]+\])\s*\|\s*'), r'\1 '),  # [TAG] | rest → [TAG] rest
    (re.compile(r'\s*\|\s*$'), ''),                      # trailing separator
)


def short_session(session_id):
    """`session_cfd189_870869` → `cfd189`. Anything else is passed through."""
    if not session_id:
        return None
    match = SESSION_ID_PATTERN.search(str(session_id))
    return match.group('sid') if match else str(session_id)[:6]


#: Trailing logger-name segments that identify nothing on their own — `uvicorn.error`
#: would otherwise be filed under `[error]`, and every package's `main` under `[main]`.
_AMBIGUOUS_MODULES = frozenset({'main', 'error', 'access', 'asgi', 'app', 'base', 'utils'})


def _module_label(logger_name: str) -> str:
    """Shortest unambiguous name for a logger, for the file-log module column."""
    parts = logger_name.split('.')
    if len(parts) > 1 and parts[-1] in _AMBIGUOUS_MODULES:
        return '.'.join(parts[-2:])
    return parts[-1]


def _context_session_id():
    """Session id of the in-flight request, or None outside a request."""
    try:
        from ..utils.context_manager import get_session_id
        return get_session_id()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Console noise control
# ─────────────────────────────────────────────────────────────────────────────
# Tags carrying internal plumbing rather than flow. Muted on the console (below
# WARNING only) because something else already reports the same outcome — the
# reason is noted per group. Everything still lands in application.log.
CONSOLE_MUTED_TAGS = (
    # Session plumbing — the per-line session tag already says which run this is.
    '[SESSION_CREATE]', '[SESSION_RESOLVE]', '[SESSION_GET]', '[STREAM_SESSION]',
    # Step bookkeeping — the domain lines trace the flow, and each turn is
    # closed by "CAD generation completed in Xs (N events)".
    '[AGENT_STEP]', '[AGENT_PRIORITY]', '[AGENT_STATE]', '[AGENT_PROGRESS]', '[AGENT_GREETING]',
    # Per-stage stopwatches, kept for profiling rather than for reading a run.
    '[TIMING]',
    # SSE / stream internals.
    '[SSE_PARAMS]', '[SSE_EVENT]', '[SSE_YIELD]', '[SSE_STREAM]', '[SSE_PROGRESS]',
    '[STREAM_STEP]', '[STREAM_YIELD]', '[STREAM_WEB]', '[STREAM_PROGRESS]',
    # Per-file / per-chunk bookkeeping.
    '[FILE_COPY]', '[CHAT_HISTORY_ADD]', '[DOWNLOAD]',
    '[RESULT_PROCESS]', '[EXPORT_PATHS]', '[HISTORY]',
    '[POST_CODEGEN_HISTORY]', '[FILE_DOWNLOAD]',
)


class ConsoleNoiseFilter(logging.Filter):
    """Drops internal-plumbing records from the console. WARNING+ always passes."""

    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True

        return not str(record.msg).startswith(CONSOLE_MUTED_TAGS)


class AccessLogFilter(logging.Filter):
    """
    Trims uvicorn access records.

    Two problems with the raw line: auth tokens and 200-char viewer URLs make it
    wrap several times, and static-asset hits bury the API calls. Attached to the
    `uvicorn.access` logger (not to a handler) so every sink sees the same
    scrubbed line.
    """

    #: Prefixes whose access records carry no diagnostic value.
    IGNORED_PREFIXES = ('/static/', '/favicon.ico', '/assets/')

    #: Endpoints logged only when they fail. These are the browser fetching what
    #: the run just produced (the same paths the [FILES] block lists) and the
    #: generation stream itself, which the agent already logs as a CAD request.
    QUIET_WHEN_OK_PREFIXES = (
        '/api/pdf-viewer/', '/api/3d-viewer/', '/api/step-viewer/',
        '/download/', '/api/generate-cad-stream',
    )

    #: Query parameters dropped outright — long and/or secret.
    DROPPED_PARAMS = ('token', 'access_token', 'jwt')

    MAX_PATH = 80
    MAX_QUERY = 60

    @classmethod
    def _scrub(cls, url: str) -> str:
        path, _, query = url.partition('?')

        if len(path) > cls.MAX_PATH:
            path = path[:cls.MAX_PATH] + '…'

        if query:
            kept = [
                param for param in query.split('&')
                if param.split('=', 1)[0] not in cls.DROPPED_PARAMS
            ]
            query = '&'.join(kept)
            if len(query) > cls.MAX_QUERY:
                query = query[:cls.MAX_QUERY] + '…'

        return f"{path}?{query}" if query else path

    def filter(self, record):
        # uvicorn logs '%s - "%s %s HTTP/%s" %d' % (client, method, url, ver, status)
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True

        client, method, url, http_version, status = args
        url = str(url)

        if url.startswith(self.IGNORED_PREFIXES):
            return False
        if url.startswith(self.QUIET_WHEN_OK_PREFIXES) and int(status) < 400:
            return False

        record.msg = '%s %s → %s'
        record.args = (method, self._scrub(url), status)
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────
class ConsoleFormatter(logging.Formatter):
    """
    Compact colored console line:

        09:18:50 INFO  cfd189  CAD request 'sheet 100x100x2' (new)
        09:18:35 INFO          Starting uvicorn on port 8124

    The session tag comes from the message when it names a session, otherwise
    from the request contextvar — so records from modules that never received a
    session id (cost tracker, RAG, MQTT client) still line up with their run.
    """

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    DIM = '\033[2m'

    #: WARNING/CRITICAL are abbreviated so the level column stays 5 wide.
    LEVEL_ABBREV = {'WARNING': 'WARN', 'CRITICAL': 'CRIT'}

    def __init__(self, use_color=True, datefmt='%H:%M:%S'):
        super().__init__(datefmt=datefmt)
        self.use_color = use_color

    def _paint(self, text, color):
        return f"{color}{text}{self.RESET}" if self.use_color else text

    def format(self, record):
        raw = record.getMessage()

        match = SESSION_ID_PATTERN.search(raw)
        tag = match.group('sid') if match else (short_session(_context_session_id()) or '')

        message = _SESSION_NOISE_PATTERN.sub('', raw)
        for pattern, replacement in _DANGLING_SEPARATORS:
            message = pattern.sub(replacement, message)
        message = message.rstrip()

        level = self.LEVEL_ABBREV.get(record.levelname, record.levelname)
        timestamp = self.formatTime(record, self.datefmt)

        head = (
            f"{self._paint(timestamp, self.DIM)} "
            f"{self._paint(f'{level:<5}', self.COLORS.get(record.levelname, ''))} "
            f"{self._paint(f'{tag:<6}', self.DIM)} "
        )
        # Width of `head` as the terminal sees it — the ANSI codes in it occupy
        # no columns, so len(head) would over-indent by ~20 characters.
        column = len(timestamp) + len(f'{level:<5}') + len(f'{tag:<6}') + 3

        # Continuation lines of multi-line records (cost table, file list) are
        # indented to the message column so the block hangs off its own header
        # instead of floating at the left margin. Leading whitespace does not
        # stop a terminal from turning a path into a Ctrl+Click link.
        head_line, _, rest = message.partition('\n')
        out = head + head_line
        if rest:
            out += '\n' + '\n'.join(' ' * column + line for line in rest.split('\n'))

        if record.exc_info:
            out += '\n' + self.formatException(record.exc_info)
        return out


class CompactFileFormatter(logging.Formatter):
    """
    One line per record for application.log:

        [2026-08-17 09:18:50] [session_cfd189_870869] INFO     [cost_tracker] …

    Strips ANSI codes and replaces emoji with text tags so the file stays
    grep-friendly on any terminal, and pins the session id at the front so
    `grep session_cfd189_870869 application.log` yields one user's whole run.
    """

    EMOJI_MAP = {
        '📤': '[SEND]', '📥': '[RECV]', '✅': '[OK]', '❌': '[FAIL]',
        '⚠️': '[WARN]', '🔍': '[CHECK]', '🎧': '[LISTEN]', '🔌': '[CONNECT]',
        '📊': '[PROGRESS]', '📢': '[STATUS]', '📋': '[INFO]', '📄': '[FILE]',
        '🔧': '[FIX]', '⏳': '[WAIT]', '🚀': '[START]', '🏁': '[END]',
        '📈': '[PROGRESS]', '📝': '[NOTE]', '💾': '[SAVE]', '🎉': '[SUCCESS]',
        '📁': '[FILES]', '💰': '[COST]', '💡': '[EXAMPLES]', '🎯': '[TARGET]',
        '🔀': '[PARALLEL]', '♻️': '[REUSE]', '🔄': '[RERANK]', '⚡': '[FAST]',
        'ℹ️': '[INFO]', '🚪': '[GATE]', '🤖': '[LLM]', '⏱️': '[TIME]',
    }

    ANSI_PATTERN = re.compile(r'\033\[[0-9;]*m')

    def __init__(self, include_location=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.include_location = include_location

    def _clean(self, text):
        text = self.ANSI_PATTERN.sub('', text)
        for emoji, replacement in self.EMOJI_MAP.items():
            text = text.replace(emoji, replacement)
        return re.sub(r'\s+', ' ', text).strip()

    def format(self, record):
        message = self._clean(record.getMessage())

        match = SESSION_ID_PATTERN.search(message)
        session_id = match.group(0) if match else _context_session_id()

        module = _module_label(record.name)
        location = f" {record.filename}:{record.lineno}" if self.include_location else ''
        prefix = f"[{session_id}] " if session_id else ''

        line = (
            f"[{self.formatTime(record, self.datefmt)}] {prefix}"
            f"{record.levelname:<8} [{module}]{location} - {message}"
        )
        if record.exc_info:
            line += '\n' + self.formatException(record.exc_info)
        return line


class ProcessFlowFilter(logging.Filter):
    """Tags each record with a coarse flow category for process_flow.log."""

    FLOW_CATEGORIES = [
        'FLOW', 'SESSION', 'AGENT', 'STREAM', 'EXPORT', 'WEB_SEARCH',
        'EDIT_MODE', 'RESULT_PROCESS', 'CHAT_HISTORY', 'SSE'
    ]

    def filter(self, record):
        msg = str(record.msg)
        record.flow_category = next(
            (c for c in self.FLOW_CATEGORIES if f"[{c}" in msg),
            "GENERAL",
        )
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────
def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "logs",
    enable_file_logging: bool = True,
    enable_console_logging: bool = True,
    enable_flow_logging: bool = True,
    enable_verbose_logging: bool = False,
    max_log_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    include_location: bool = False
):
    """
    Configure application-wide logging.

    Args:
        log_level: DEBUG, INFO, WARNING, ERROR or CRITICAL
        log_dir: Directory for log files
        enable_file_logging: Write application.log / main_flow.log / errors.log
        enable_console_logging: Write the compact console view
        enable_flow_logging: Write process_flow.log
        enable_verbose_logging: Unmute the internal-plumbing tags on the console
        max_log_size: Rotation threshold per log file, in bytes
        backup_count: Number of rotated files to keep
        include_location: Add filename:lineno to application.log

    Returns:
        The configured root logger.
    """
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(numeric_level)

    handlers = []

    if enable_console_logging:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(ConsoleFormatter())
        if not enable_verbose_logging:
            console_handler.addFilter(ConsoleNoiseFilter())
        handlers.append(console_handler)

    if enable_file_logging:
        # Everything, in full.
        application_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "application.log",
            maxBytes=max_log_size, backupCount=backup_count, encoding='utf-8',
        )
        application_handler.setLevel(numeric_level)
        application_handler.setFormatter(CompactFileFormatter(
            include_location=include_location, datefmt='%Y-%m-%d %H:%M:%S',
        ))
        handlers.append(application_handler)

        # The console view, replayable after the fact: same filtering, full
        # dates, no colors.
        main_flow_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "main_flow.log",
            maxBytes=max_log_size, backupCount=backup_count, encoding='utf-8',
        )
        main_flow_handler.setLevel(numeric_level)
        main_flow_handler.setFormatter(ConsoleFormatter(
            use_color=False, datefmt='%Y-%m-%d %H:%M:%S',
        ))
        main_flow_handler.addFilter(ConsoleNoiseFilter())
        handlers.append(main_flow_handler)

        error_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "errors.log",
            maxBytes=max_log_size, backupCount=backup_count, encoding='utf-8',
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        handlers.append(error_handler)

    if enable_flow_logging:
        flow_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "process_flow.log",
            maxBytes=max_log_size, backupCount=backup_count, encoding='utf-8',
        )
        flow_handler.setLevel(numeric_level)
        flow_handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s - [%(flow_category)s] - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        flow_handler.addFilter(ProcessFlowFilter())
        handlers.append(flow_handler)

    for handler in handlers:
        root_logger.addHandler(handler)

    # Noisy third-party loggers.
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Attached to the logger, so file sinks see the scrubbed line too.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogFilter) for f in access_logger.filters):
        access_logger.addFilter(AccessLogFilter())

    root_logger.info(
        f"Logging → {log_path.absolute()} | level={log_level}"
        f"{' | verbose' if enable_verbose_logging else ''}"
    )

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
