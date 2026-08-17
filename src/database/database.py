"""
Database connection and session management for Tolery API.
"""
from sqlalchemy import create_engine, text, event
import time
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os
from pathlib import Path
from contextlib import contextmanager
from .db_retry import retry_db_operation, DatabaseRetryError
import logging

logger = logging.getLogger(__name__)

# Load environment variables from root directory
config_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(str(config_path))

# Get database connection parameters from environment variables
DB_USER = os.getenv("MYSQL_USER", "root")
# Try to get password from MYSQL_ROOT_PASSWORD first, then fall back to MYSQL_PASSWORD
DB_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", os.getenv("MYSQL_PASSWORD", "your_password"))
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_NAME = os.getenv("MYSQL_DATABASE", "chatbot_db")

# Create database URL - URL encode the password to handle special characters
from urllib.parse import quote_plus
DB_PASSWORD_ENCODED = quote_plus(DB_PASSWORD)

# Create the database URL with explicit parameters
DATABASE_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD_ENCODED}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# The connection target is already reported by the startup banner in run.py, so
# it is only worth a line here when someone is debugging the connection itself.
logger.debug(f"MySQL target: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# Create optimized engine with connection handling
def create_optimized_engine():
    """Create engine with proper connection pool and disconnect handling"""

    # Define pool configuration constants
    POOL_SIZE = 30  # Increased from 10 to handle more concurrent connections
    MAX_OVERFLOW = 50  # Increased from 15 to allow more overflow connections
    POOL_TIMEOUT = 120  # Increased from 60 to allow more time for connection acquisition

    engine = create_engine(
        DATABASE_URL,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_recycle=1800,  # Recycle connections every 30 minutes to prevent stale connections
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 30,  # Increased timeout for better stability
            "use_pure": True,
            "auth_plugin": "mysql_native_password",
            # Removed autocommit to avoid conflict with SessionLocal(autocommit=False)
            "sql_mode": "TRADITIONAL",  # Strict SQL mode
            "charset": "utf8mb4",
            # SSL Configuration to handle SSL errors
            "ssl_disabled": True,   # Disable SSL if causing issues
            # Alternative: Enable SSL with proper configuration
            # "ssl_ca": "/path/to/ca.pem",
            # "ssl_cert": "/path/to/client-cert.pem",
            # "ssl_key": "/path/to/client-key.pem",
            # "ssl_verify_cert": True,
            # "ssl_verify_identity": True,
            "raise_on_warnings": False,  # Don't raise on MySQL warnings
            "get_warnings": False,       # Don't fetch warnings
        }
    )
    
    # Add connection pool monitoring
    @event.listens_for(engine, "connect")
    def receive_connect(dbapi_conn, connection_record):
        pool = engine.pool
        logger.debug(f"DB Connection opened - Pool: {pool.size()}/{POOL_SIZE}, Overflow: {pool.overflow()}/{MAX_OVERFLOW}")

    @event.listens_for(engine, "close")
    def receive_close(dbapi_conn, connection_record):
        pool = engine.pool
        logger.debug(f"DB Connection closed - Pool: {pool.size()}/{POOL_SIZE}, Overflow: {pool.overflow()}/{MAX_OVERFLOW}")

    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_conn, connection_record):
        pool = engine.pool
        logger.debug(f"DB Connection returned to pool - Available: {pool.size()}/{POOL_SIZE}")

    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_conn, connection_record, connection_proxy):
        pool = engine.pool
        logger.debug(f"DB Connection checked out - In use: {pool.checkedout()}, Overflow: {pool.overflow()}/{MAX_OVERFLOW}")
    
    return engine

# SQLite fallback: used when SKIP_DB is set or MySQL is unreachable, so the API
# keeps working (sessions/chat history are stored locally instead of in MySQL).
SQLITE_PATH = Path(__file__).parent.parent.parent / "local_fallback.db"
SQLITE_URL = f"sqlite:///{SQLITE_PATH.as_posix()}"

USING_SQLITE_FALLBACK = False


def create_sqlite_engine():
    """Local file-based engine used when MySQL is not available."""
    return create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )


def _skip_db_requested():
    return os.getenv("SKIP_DB", "").lower() in ("1", "true", "yes")


# Replace the complex connection logic with optimized version
if _skip_db_requested():
    logger.info(f"SKIP_DB set — using local SQLite at {SQLITE_PATH}")
    engine = create_sqlite_engine()
    USING_SQLITE_FALLBACK = True
else:
    try:
        engine = create_optimized_engine()

        # Test connection once
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info(f"MySQL connected: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    except Exception as e:
        # The SQLAlchemy message spans several lines and ends with a docs URL;
        # only the first line names the actual failure.
        reason = str(e).split('\n')[0]
        logger.warning(f"MySQL unreachable ({reason}) — falling back to SQLite at {SQLITE_PATH}")
        engine = create_sqlite_engine()
        USING_SQLITE_FALLBACK = True

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()

# Function to initialize database
def init_db():
    """
    Initialize database by creating all tables.

    Set SKIP_DB=1 to boot without a reachable MySQL server (local dev only);
    the tables are then created in the local SQLite fallback file instead, so
    every endpoint keeps working.
    """
    # Which backend is in use was already reported when the engine was built.
    Base.metadata.create_all(bind=engine)

def _session_scope():
    """
    Open a session with connection retry, yield it, then roll back on error and
    always close. Shared by get_db() and get_db_session(); `yield from` forwards
    throw()/close() in, so both callers see the same error handling.
    """
    @retry_db_operation(max_retries=3, delay=1.0)
    def _get_session():
        return SessionLocal()

    db = None
    try:
        db = _get_session()
        yield db
    except DatabaseRetryError as e:
        logger.error(f"Failed to establish database connection after retries: {e}")
        raise
    except Exception as e:
        logger.error(f"Database session error: {e}")
        if db:
            try:
                db.rollback()
            except Exception as rollback_error:
                logger.error(f"Error during rollback: {rollback_error}")
        raise
    finally:
        if db:
            try:
                db.close()
            except Exception as close_error:
                logger.error(f"Error closing database session: {close_error}")

# Database dependency for FastAPI
def get_db():
    """
    Dependency function for FastAPI to get database session.
    Automatically handles session lifecycle with retry logic.
    """
    yield from _session_scope()

# Context manager for manual database sessions
@contextmanager
def get_db_session():
    """
    Context manager for manual database session management with retry logic.
    Use this when you need to control exactly when database connections are used.

    Example:
        with get_db_session() as db:
            # Perform database operations
            result = db.query(Model).all()
        # Connection is automatically closed here
    """
    yield from _session_scope()
