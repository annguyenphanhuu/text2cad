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

# Print the actual values for debugging (without showing the full password)
print(f"Database config values:")
print(f"  DB_USER: {DB_USER}")
print(f"  DB_PASSWORD: {'*' * len(DB_PASSWORD)}")
print(f"  DB_HOST: {DB_HOST}")
print(f"  DB_PORT: {DB_PORT}")
print(f"  DB_NAME: {DB_NAME}")

# Create the database URL with explicit parameters
DATABASE_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD_ENCODED}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Log database connection info (without password)
print(f"Connecting to MySQL database: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

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

# Replace the complex connection logic with optimized version
try:
    print("Creating optimized database engine...")
    engine = create_optimized_engine()
    
    # Test connection once
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        print(f"Database connection successful! Test result: {result.scalar()}")
        
except Exception as e:
    print(f"Database connection failed: {e}")
    # Fallback to engine with adequate pool size for concurrent operations
    engine = create_engine(
        DATABASE_URL,
        pool_size=30,  # Increased from 2 to handle concurrent requests
        max_overflow=50,  # Increased from 2 to allow more overflow connections
        pool_timeout=60,  # Add timeout for connection acquisition
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 30,
            "use_pure": True,
            "auth_plugin": "mysql_native_password",
            "ssl_disabled": True,  # Disable SSL to avoid connection issues
            "raise_on_warnings": False
        }
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()

# Function to initialize database
def init_db():
    """
    Initialize database by creating all tables.
    """
    Base.metadata.create_all(bind=engine)

# Database dependency for FastAPI
def get_db():
    """
    Dependency function for FastAPI to get database session.
    Automatically handles session lifecycle with retry logic.
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
