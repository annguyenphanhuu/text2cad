"""
API-TEST: 14 Core Endpoints for CAD Generation System + Comprehensive Diagnostics
This is the test API with simplified, focused endpoints including FreeCAD installation verification,
system information, Python environment checks, and comprehensive diagnostics.
"""

import os
import logging
import subprocess
import sys
import time
import platform
import psutil
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Query
from src.api.cors import configure_cors
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tolery-api-test")

# Database and model imports
try:
    from ...database.database import get_db
    from ...models.sessions import Session as SessionModel, ChatHistory
    from ... import crud
    from ...core.chatbot import text_to_cad_agent
    from ...middleware.auth import verify_token_dependency as verify_token
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from src.database.database import get_db
    from src.models.sessions import Session as SessionModel, ChatHistory
    import src.crud as crud
    from src.core.chatbot import text_to_cad_agent
    from src.middleware.auth import verify_token_dependency as verify_token

# FastAPI app (no lifespan when mounted as sub-app)
app = FastAPI(
    title="Tolery API-TEST",
    description="14 Core Endpoints for CAD Generation System + Comprehensive Diagnostics - Token Authentication Required",
    version="1.3.0",
    openapi_tags=[
        {"name": "auth", "description": "Authentication endpoints"},
        {"name": "session", "description": "Session management"},
        {"name": "cad", "description": "CAD generation endpoints"},
        {"name": "diagnostics", "description": "System diagnostics"},
        {"name": "logs", "description": "Application log endpoints"}
    ]
)


# CORS middleware (shared policy — see src/api/cors.py)
configure_cors(app)

# Response Models
class SessionInfo(BaseModel):
    """Session information response model."""
    session_id: str = Field(..., description="Session ID")
    created_at: str = Field(..., description="Session creation timestamp")
    updated_at: str = Field(..., description="Session last update timestamp")

    class Config:
        from_attributes = True

class SessionInfoResponse(BaseModel):
    """Detailed session information response."""
    session_id: str = Field(..., description="Session ID")
    created_at: str = Field(..., description="Session creation timestamp")
    updated_at: str = Field(..., description="Session last update timestamp")
    message_count: int = Field(..., description="Number of messages in session")
    latest_code: Optional[str] = Field(None, description="Latest generated code")

class ExportResponse(BaseModel):
    """Export files response model."""
    session_id: str = Field(..., description="Session ID")
    obj_exports: List[str] = Field(default_factory=list, description="OBJ file URLs")
    step_exports: List[str] = Field(default_factory=list, description="STEP file URLs")
    total_exports: int = Field(..., description="Total number of exports")

class ChatHistoryResponse(BaseModel):
    """Chat history response model."""
    session_id: str = Field(..., description="Session ID")
    messages: List[dict] = Field(..., description="List of chat messages")
    total_messages: int = Field(..., description="Total number of messages")

try:
    from ...schemas.sessions import ChatRequest
except ImportError:
    from src.schemas.sessions import ChatRequest


class ChatResponse(BaseModel):
    """Chat response model."""
    message: str = Field(..., description="Response message")
    session_id: str = Field(..., description="Session ID")
    obj_export: Optional[str] = Field(None, description="OBJ file URL if generated")
    step_export: Optional[str] = Field(None, description="STEP file URL if generated")
    error: Optional[str] = Field(None, description="Error message if any")

    completed_at: str = Field(..., description="Completion time of execution (ISO format)")
    error: Optional[str] = Field(None, description="Error message if any exception occurred")

class FreeCADTestResult(BaseModel):
    """Individual FreeCAD test result model."""
    test_name: str = Field(..., description="Name of the test")
    success: bool = Field(..., description="Whether the test passed")
    output: str = Field(..., description="Test output")
    error: Optional[str] = Field(None, description="Error message if test failed")

class FreeCADVersionResponse(BaseModel):
    """FreeCAD version check response model."""
    overall_success: bool = Field(..., description="Whether all tests passed")
    tests_passed: int = Field(..., description="Number of tests that passed")
    total_tests: int = Field(..., description="Total number of tests")
    test_results: List[FreeCADTestResult] = Field(..., description="Individual test results")
    freecad_version: Optional[str] = Field(None, description="FreeCAD version if detected")
    apt_package_info: Optional[str] = Field(None, description="APT package information")
    execution_time: float = Field(..., description="Total execution time in seconds")
    error: Optional[str] = Field(None, description="Overall error message if any")

class SystemInfoResponse(BaseModel):
    """System information response model."""
    os_name: str = Field(..., description="Operating system name")
    os_version: str = Field(..., description="Operating system version")
    kernel_version: str = Field(..., description="Kernel version")
    architecture: str = Field(..., description="System architecture")
    hostname: str = Field(..., description="System hostname")
    python_version: str = Field(..., description="Python version")
    platform_info: str = Field(..., description="Platform information")
    cpu_count: int = Field(..., description="Number of CPU cores")
    memory_total: str = Field(..., description="Total memory")
    disk_usage: str = Field(..., description="Disk usage information")
    docker_info: Optional[str] = Field(None, description="Docker information if available")
    container_id: Optional[str] = Field(None, description="Container ID if running in container")
    error: Optional[str] = Field(None, description="Error message if any")

class PythonEnvironmentResponse(BaseModel):
    """Python environment check response model."""
    python_executable: str = Field(..., description="Python executable path")
    python_version: str = Field(..., description="Python version")
    python_path: List[str] = Field(..., description="Python path")
    stdlib_modules: List[str] = Field(..., description="Available standard library modules")
    missing_stdlib: List[str] = Field(..., description="Missing standard library modules")
    site_packages: List[str] = Field(..., description="Installed packages")
    freecad_paths: List[str] = Field(..., description="FreeCAD related paths")
    environment_vars: dict = Field(..., description="Relevant environment variables")
    error: Optional[str] = Field(None, description="Error message if any")

class DiagnosticResponse(BaseModel):
    """Comprehensive diagnostic response model."""
    system_info: dict = Field(..., description="System information")
    python_env: dict = Field(..., description="Python environment")
    freecad_diagnosis: dict = Field(..., description="FreeCAD specific diagnosis")
    recommendations: List[str] = Field(..., description="Recommended actions")
    overall_status: str = Field(..., description="Overall system status")
    error: Optional[str] = Field(None, description="Error message if any")

class MigrationInfo(BaseModel):
    """Migration file information model."""
    name: str = Field(..., description="Migration file name")
    path: str = Field(..., description="Full path to migration file")
    description: str = Field(..., description="Migration description")
    size: int = Field(..., description="File size in bytes")
    last_modified: str = Field(..., description="Last modification time")

class MigrationsListResponse(BaseModel):
    """List of available migrations response model."""
    migrations: List[MigrationInfo] = Field(..., description="List of available migration files")
    total_count: int = Field(..., description="Total number of migration files")
    migrations_path: str = Field(..., description="Path to migrations directory")
    error: Optional[str] = Field(None, description="Error message if any")

class MigrationRunResponse(BaseModel):
    """Migration execution response model."""
    migration_name: str = Field(..., description="Name of the migration that was run")
    migration_type: str = Field(..., description="Type of migration: 'add' or 'rollback'")
    success: bool = Field(..., description="Whether the migration was successful")
    message: str = Field(..., description="Migration result message")
    column_exists: Optional[bool] = Field(None, description="Whether column existed before migration")
    column_existed: Optional[bool] = Field(None, description="Whether column existed before rollback")
    verification_passed: Optional[bool] = Field(None, description="Whether post-migration verification passed")
    execution_time: float = Field(..., description="Migration execution time in seconds")
    started_at: str = Field(..., description="Migration start time")
    completed_at: str = Field(..., description="Migration completion time")
    error: Optional[str] = Field(None, description="Error message if migration failed")

class RollbackResponse(BaseModel):
    """Rollback execution response model."""
    migration_name: str = Field(..., description="Name of the rollback migration that was run")
    success: bool = Field(..., description="Whether the rollback was successful")
    message: str = Field(..., description="Rollback result message")
    column_existed: bool = Field(..., description="Whether column existed before rollback")
    verification_passed: bool = Field(..., description="Whether post-rollback verification passed")
    execution_time: float = Field(..., description="Rollback execution time in seconds")
    started_at: str = Field(..., description="Rollback start time")
    completed_at: str = Field(..., description="Rollback completion time")
    error: Optional[str] = Field(None, description="Error message if rollback failed")

class DatabaseSchemaInfo(BaseModel):
    """Database schema information model."""
    table_name: str = Field(..., description="Table name")
    column_name: str = Field(..., description="Column name")
    data_type: str = Field(..., description="Column data type")
    is_nullable: str = Field(..., description="Whether column is nullable")
    column_default: Optional[str] = Field(None, description="Column default value")
    column_comment: Optional[str] = Field(None, description="Column comment")

class MigrationStatusResponse(BaseModel):
    """Migration status and database schema response model."""
    chat_history_columns: List[DatabaseSchemaInfo] = Field(..., description="Chat history table columns")
    technical_drawing_column_exists: bool = Field(..., description="Whether technical_drawing_export column exists")
    total_columns: int = Field(..., description="Total number of columns in chat_history table")
    schema_check_time: str = Field(..., description="Time when schema was checked")
    error: Optional[str] = Field(None, description="Error message if any")

class ConversationRow(BaseModel):
    """Single conversation row for CSV preview."""
    session_id: str = Field(..., description="Session ID")
    session_created_at: str = Field(..., description="Session creation timestamp")
    conversation_id: int = Field(..., description="Chat history entry ID")
    user_message: Optional[str] = Field(None, description="User message")
    ai_response: Optional[str] = Field(None, description="AI chatbot response")
    part_file_name: Optional[str] = Field(None, description="Part file name")
    export_format: Optional[str] = Field(None, description="Export format")
    material_choice: Optional[str] = Field(None, description="Material choice")
    obj_export: Optional[str] = Field(None, description="OBJ export path")
    step_export: Optional[str] = Field(None, description="STEP export path")
    created_at: str = Field(..., description="Message creation timestamp")

class ConversationCSVPreviewResponse(BaseModel):
    """Preview response for conversation CSV export."""
    total_sessions: int = Field(..., description="Number of sessions included")
    total_rows: int = Field(..., description="Total number of conversation rows")
    sessions_included: List[str] = Field(..., description="List of session IDs included")
    preview_data: List[ConversationRow] = Field(..., description="Preview of first 20 rows")
    export_hint: str = Field(..., description="Hint for downloading the full CSV")

# LogResponse model moved to log_viewer.py

# System Information Functions
def get_system_info():
    """Get comprehensive system information"""
    try:
        # Basic OS info
        os_name = platform.system()
        os_version = platform.release()
        kernel_version = platform.version()
        architecture = platform.machine()
        hostname = platform.node()
        python_version = platform.python_version()
        platform_info = platform.platform()

        # CPU and memory info
        cpu_count = psutil.cpu_count()
        memory = psutil.virtual_memory()
        memory_total = f"{memory.total / (1024**3):.2f} GB"

        # Disk usage
        disk = psutil.disk_usage('/')
        disk_usage = f"Total: {disk.total / (1024**3):.2f} GB, Used: {disk.used / (1024**3):.2f} GB, Free: {disk.free / (1024**3):.2f} GB"

        # Check if running in Docker container
        container_id = None
        docker_info = None

        try:
            # Check for Docker container
            if os.path.exists('/.dockerenv'):
                container_id = "Running in Docker container"

                # Try to get container ID
                try:
                    with open('/proc/self/cgroup', 'r') as f:
                        cgroup_content = f.read()
                        if 'docker' in cgroup_content:
                            # Extract container ID from cgroup
                            for line in cgroup_content.split('\n'):
                                if 'docker' in line and '/' in line:
                                    parts = line.split('/')
                                    if len(parts) > 1:
                                        container_id = f"Container ID: {parts[-1][:12]}"
                                        break
                except:
                    pass

            # Try to get Docker version
            try:
                result = subprocess.run(['docker', '--version'],
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    docker_info = result.stdout.strip()
            except:
                pass

        except Exception:
            pass

        return {
            "os_name": os_name,
            "os_version": os_version,
            "kernel_version": kernel_version,
            "architecture": architecture,
            "hostname": hostname,
            "python_version": python_version,
            "platform_info": platform_info,
            "cpu_count": cpu_count,
            "memory_total": memory_total,
            "disk_usage": disk_usage,
            "docker_info": docker_info,
            "container_id": container_id,
            "error": None
        }

    except Exception as e:
        return {
            "os_name": "Unknown",
            "os_version": "Unknown",
            "kernel_version": "Unknown",
            "architecture": "Unknown",
            "hostname": "Unknown",
            "python_version": "Unknown",
            "platform_info": "Unknown",
            "cpu_count": 0,
            "memory_total": "Unknown",
            "disk_usage": "Unknown",
            "docker_info": None,
            "container_id": None,
            "error": str(e)
        }

def check_python_environment():
    """Check Python environment and standard library"""
    try:
        import sys
        import os
        import importlib

        # Basic Python info
        python_executable = sys.executable
        python_version = sys.version
        python_path = sys.path.copy()

        # Check standard library modules
        stdlib_modules = []
        missing_stdlib = []

        # Common standard library modules to check
        test_modules = [
            'math', 'os', 'sys', 'datetime', 'json', 'urllib', 'collections',
            'itertools', 'functools', 'operator', 'copy', 'pickle', 'tempfile',
            'shutil', 'glob', 'fnmatch', 're', 'string', 'textwrap'
        ]

        for module_name in test_modules:
            try:
                importlib.import_module(module_name)
                stdlib_modules.append(module_name)
            except ImportError:
                missing_stdlib.append(module_name)

        # Get installed packages
        site_packages = []
        try:
            import pkg_resources
            for dist in pkg_resources.working_set:
                site_packages.append(f"{dist.project_name}=={dist.version}")
        except:
            # Fallback method
            try:
                import subprocess
                result = subprocess.run([sys.executable, '-m', 'pip', 'list'],
                                      capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    site_packages = result.stdout.strip().split('\n')[2:]  # Skip header
            except:
                site_packages = ["Unable to get package list"]

        # Check FreeCAD related paths
        freecad_paths = []
        for path in python_path:
            if 'freecad' in path.lower() or 'squashfs' in path.lower():
                freecad_paths.append(path)

        # Environment variables
        env_vars = {}
        relevant_vars = ['PATH', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'FREECAD_USER_HOME']
        for var in relevant_vars:
            env_vars[var] = os.environ.get(var, 'Not set')

        return {
            "python_executable": python_executable,
            "python_version": python_version,
            "python_path": python_path,
            "stdlib_modules": stdlib_modules,
            "missing_stdlib": missing_stdlib,
            "site_packages": site_packages[:20],  # Limit to first 20
            "freecad_paths": freecad_paths,
            "environment_vars": env_vars,
            "error": None
        }

    except Exception as e:
        return {
            "python_executable": "Error",
            "python_version": "Error",
            "python_path": [],
            "stdlib_modules": [],
            "missing_stdlib": [],
            "site_packages": [],
            "freecad_paths": [],
            "environment_vars": {},
            "error": str(e)
        }

def get_available_migrations():
    """Get list of available migration files"""
    try:
        # Get the project root directory (3 levels up from this file)
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
        migrations_path = os.path.join(project_root, "src", "database", "migrations")

        if not os.path.exists(migrations_path):
            return {
                "migrations": [],
                "total_count": 0,
                "migrations_path": migrations_path,
                "error": "Migrations directory not found"
            }

        migrations = []
        for filename in os.listdir(migrations_path):
            if filename.endswith('.py') and not filename.startswith('__'):
                file_path = os.path.join(migrations_path, filename)
                file_stat = os.stat(file_path)

                # Try to extract description from file
                description = "No description available"
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Look for docstring at the beginning
                        if '"""' in content:
                            start = content.find('"""') + 3
                            end = content.find('"""', start)
                            if end > start:
                                description = content[start:end].strip().split('\n')[0]
                except:
                    pass

                migrations.append(MigrationInfo(
                    name=filename,
                    path=file_path,
                    description=description,
                    size=file_stat.st_size,
                    last_modified=datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                ))

        # Sort by name
        migrations.sort(key=lambda x: x.name)

        return {
            "migrations": migrations,
            "total_count": len(migrations),
            "migrations_path": migrations_path,
            "error": None
        }

    except Exception as e:
        return {
            "migrations": [],
            "total_count": 0,
            "migrations_path": "",
            "error": str(e)
        }

def execute_migration(migration_name: str):
    """Run a specific migration"""
    start_time = time.time()
    started_at = datetime.now().isoformat()

    # Determine migration type
    migration_type = "rollback" if "rollback" in migration_name.lower() else "add"

    try:
        # Get the project root directory
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
        migrations_path = os.path.join(project_root, "src", "database", "migrations")
        migration_file = os.path.join(migrations_path, migration_name)

        if not os.path.exists(migration_file):
            return {
                "migration_name": migration_name,
                "migration_type": migration_type,
                "success": False,
                "message": f"Migration file not found: {migration_name}",
                "execution_time": time.time() - start_time,
                "started_at": started_at,
                "completed_at": datetime.now().isoformat(),
                "error": f"Migration file not found: {migration_file}"
            }

        # Import and run the migration
        sys.path.insert(0, migrations_path)

        try:
            # Remove .py extension for import
            module_name = migration_name.replace('.py', '')

            # Import the migration module
            import importlib.util
            spec = importlib.util.spec_from_file_location(module_name, migration_file)
            migration_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration_module)

            # Look for the main migration function based on type
            if migration_type == "rollback":
                # Try specific rollback functions first
                if hasattr(migration_module, 'rollback_technical_drawing_export_column'):
                    result = migration_module.rollback_technical_drawing_export_column()
                elif hasattr(migration_module, 'remove_technical_drawing_export_column'):
                    result = migration_module.remove_technical_drawing_export_column()
                elif hasattr(migration_module, 'remove_json_export_column'):
                    result = migration_module.remove_json_export_column()
                elif hasattr(migration_module, 'remove_column'):
                    # Generic column removal - try to determine column name from filename
                    if 'technical_drawing' in migration_name.lower():
                        result = migration_module.remove_column('technical_drawing_export')
                    elif 'json_export' in migration_name.lower():
                        result = migration_module.remove_column('json_export')
                    else:
                        result = {"success": False, "error": f"Cannot determine column name from migration file: {migration_name}"}
                elif hasattr(migration_module, 'main'):
                    migration_module.main()
                    result = {
                        "success": True,
                        "message": f"Rollback migration {migration_name} executed successfully",
                        "column_existed": None,
                        "verification_passed": True,
                        "error": None
                    }
                else:
                    return {
                        "migration_name": migration_name,
                        "migration_type": migration_type,
                        "success": False,
                        "message": f"No rollback function found in {migration_name}",
                        "execution_time": time.time() - start_time,
                        "started_at": started_at,
                        "completed_at": datetime.now().isoformat(),
                        "error": "No rollback function found"
                    }
            else:  # add migration
                # Try specific add functions first
                if hasattr(migration_module, 'add_technical_drawing_export_column'):
                    result = migration_module.add_technical_drawing_export_column()
                elif hasattr(migration_module, 'add_column'):
                    # Generic column addition - try to determine column name from filename
                    if 'technical_drawing' in migration_name.lower():
                        result = migration_module.add_column('technical_drawing_export', 'VARCHAR(255)', True, 'Path to technical drawing export file')
                    else:
                        result = {"success": False, "error": f"Cannot determine column name from migration file: {migration_name}"}
                elif hasattr(migration_module, 'main'):
                    migration_module.main()
                    result = {
                        "success": True,
                        "message": f"Migration {migration_name} executed successfully",
                        "column_exists": None,
                        "verification_passed": True,
                        "error": None
                    }
                else:
                    return {
                        "migration_name": migration_name,
                        "migration_type": migration_type,
                        "success": False,
                        "message": f"No migration function found in {migration_name}",
                        "execution_time": time.time() - start_time,
                        "started_at": started_at,
                        "completed_at": datetime.now().isoformat(),
                        "error": "No migration function found"
                    }

            end_time = time.time()
            completed_at = datetime.now().isoformat()

            return {
                "migration_name": migration_name,
                "migration_type": migration_type,
                "success": result.get("success", False),
                "message": result.get("message", "Migration completed"),
                "column_exists": result.get("column_exists"),
                "column_existed": result.get("column_existed"),
                "verification_passed": result.get("verification_passed"),
                "execution_time": end_time - start_time,
                "started_at": started_at,
                "completed_at": completed_at,
                "error": result.get("error")
            }

        finally:
            # Clean up sys.path
            if migrations_path in sys.path:
                sys.path.remove(migrations_path)

    except Exception as e:
        end_time = time.time()
        completed_at = datetime.now().isoformat()

        return {
            "migration_name": migration_name,
            "success": False,
            "message": f"Migration failed: {str(e)}",
            "execution_time": end_time - start_time,
            "started_at": started_at,
            "completed_at": completed_at,
            "error": str(e)
        }

def get_database_schema_status():
    """Get current database schema status"""
    try:
        from sqlalchemy import text
        from src.database.database import engine

        with engine.connect() as connection:
            # Get all columns from chat_history table
            schema_query = text("""
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'chat_history'
                ORDER BY ORDINAL_POSITION
            """)

            result = connection.execute(schema_query)
            columns = []

            technical_drawing_exists = False


            for row in result:
                column_info = DatabaseSchemaInfo(
                    table_name="chat_history",
                    column_name=row[0],
                    data_type=row[1],
                    is_nullable=row[2],
                    column_default=row[3],
                    column_comment=row[4]
                )
                columns.append(column_info)

                if row[0] == 'technical_drawing_export':
                    technical_drawing_exists = True


            return {
                "chat_history_columns": columns,
                "technical_drawing_column_exists": technical_drawing_exists,
                "total_columns": len(columns),
                "schema_check_time": datetime.now().isoformat(),
                "error": None
            }

    except Exception as e:
        return {
            "chat_history_columns": [],
            "technical_drawing_column_exists": False,
            "total_columns": 0,
            "schema_check_time": datetime.now().isoformat(),
            "error": str(e)
        }

# Log file reading functions moved to log_viewer.py

def diagnose_freecad_issues():
    """Comprehensive FreeCAD diagnosis"""
    try:
        diagnosis = {
            "freecadcmd_available": False,
            "freecadcmd_path": None,
            "appimage_path": None,
            "extracted_path": None,
            "python_can_import": False,
            "part_module_available": False,
            "math_module_available": False,
            "issues_found": [],
            "suggestions": []
        }

        # Check freecadcmd availability
        try:
            result = subprocess.run(['which', 'freecadcmd'],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                diagnosis["freecadcmd_available"] = True
                diagnosis["freecadcmd_path"] = result.stdout.strip()
        except:
            diagnosis["issues_found"].append("freecadcmd not found in PATH")

        # Check AppImage
        appimage_paths = [
            '/usr/local/bin/FreeCAD.AppImage',
            '/usr/local/bin/freecad',
            '/usr/local/bin/freecadcmd'
        ]

        for path in appimage_paths:
            if os.path.exists(path):
                if 'AppImage' in path:
                    diagnosis["appimage_path"] = path
                elif 'freecad' in path:
                    diagnosis["extracted_path"] = path

        # Test Python import
        try:
            result = subprocess.run(['python', '-c', 'import math; print("math OK")'],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                diagnosis["math_module_available"] = True
            else:
                diagnosis["issues_found"].append(f"Python math module issue: {result.stderr}")
        except Exception as e:
            diagnosis["issues_found"].append(f"Python test failed: {str(e)}")

        # Test FreeCAD import
        if diagnosis["freecadcmd_available"]:
            try:
                result = subprocess.run(['freecadcmd', '-c', 'import FreeCAD; print("FreeCAD OK")'],
                                      capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    diagnosis["python_can_import"] = True
                else:
                    diagnosis["issues_found"].append(f"FreeCAD import issue: {result.stderr}")
            except Exception as e:
                diagnosis["issues_found"].append(f"FreeCAD test failed: {str(e)}")

            # Test Part module
            try:
                result = subprocess.run(['freecadcmd', '-c', 'import Part; print("Part OK")'],
                                      capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    diagnosis["part_module_available"] = True
                else:
                    diagnosis["issues_found"].append(f"Part module issue: {result.stderr}")
            except Exception as e:
                diagnosis["issues_found"].append(f"Part module test failed: {str(e)}")

        # Generate suggestions
        if not diagnosis["math_module_available"]:
            diagnosis["suggestions"].append("Python standard library is corrupted - reinstall Python")

        if not diagnosis["part_module_available"]:
            diagnosis["suggestions"].append("FreeCAD Part module missing - reinstall FreeCAD")

        if not diagnosis["freecadcmd_available"]:
            diagnosis["suggestions"].append("freecadcmd not in PATH - check FreeCAD installation")

        return diagnosis

    except Exception as e:
        return {
            "error": str(e),
            "issues_found": [f"Diagnosis failed: {str(e)}"],
            "suggestions": ["Run manual diagnosis"]
        }

# FreeCAD Testing Functions
def test_apt_show_freecad():
    """Test apt show freecad to check repository package info"""
    try:
        result = subprocess.run(['apt', 'show', 'freecad'],
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # Extract key information
            lines = result.stdout.split('\n')
            key_info = []
            for line in lines:
                if any(keyword in line.lower() for keyword in ['version:', 'package:', 'description:']):
                    key_info.append(line.strip())
            return True, '\n'.join(key_info), None
        else:
            return False, "", result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "apt show freecad timed out"
    except FileNotFoundError:
        return False, "", "apt command not found"
    except Exception as e:
        return False, "", str(e)

def test_freecadcmd_version():
    """Test if freecadcmd is available and get version"""
    try:
        result = subprocess.run(['freecadcmd', '--version'],
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, result.stdout.strip(), None
        else:
            return False, "", result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "freecadcmd version check timed out"
    except FileNotFoundError:
        return False, "", "freecadcmd not found in PATH"
    except Exception as e:
        return False, "", str(e)

def test_freecad_import():
    """Test if FreeCAD Python module can be imported"""
    try:
        result = subprocess.run([
            'freecadcmd', '-c',
            'import FreeCAD; print("FreeCAD Version:", FreeCAD.Version())'
        ], capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return True, result.stdout.strip(), None
        else:
            return False, "", result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "FreeCAD Python import timed out"
    except Exception as e:
        return False, "", str(e)

def test_freecad_basic_operations():
    """Test basic FreeCAD operations"""
    try:
        script = '''
import FreeCAD
import Part

# Create a new document
doc = FreeCAD.newDocument("TestDoc")

# Create a simple box
box = doc.addObject("Part::Box", "TestBox")
box.Length = 10
box.Width = 10
box.Height = 10

# Recompute the document
doc.recompute()

print("Created box with volume:", box.Shape.Volume)
print("Basic FreeCAD operations successful")

# Close document
FreeCAD.closeDocument("TestDoc")
'''

        result = subprocess.run(['freecadcmd', '-c', script],
                              capture_output=True, text=True, timeout=60)

        if result.returncode == 0:
            return True, result.stdout.strip(), None
        else:
            return False, "", result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "FreeCAD basic operations test timed out"
    except Exception as e:
        return False, "", str(e)



# 1. LIST SESSIONS
@app.get("/sessions", response_model=List[SessionInfo])
async def list_sessions(db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """List all available sessions."""
    try:
        # Optimized query to get all sessions and their latest part_file_name
        latest_chat_subquery = db.query(
            ChatHistory.session_id,
            func.max(ChatHistory.id).label("max_id")
        ).filter(ChatHistory.part_file_name.isnot(None)).group_by(ChatHistory.session_id).subquery()

        sessions = db.query(
            SessionModel,
            ChatHistory.part_file_name
        ).outerjoin(
            latest_chat_subquery, SessionModel.session_id == latest_chat_subquery.c.session_id
        ).outerjoin(
            ChatHistory, ChatHistory.id == latest_chat_subquery.c.max_id
        ).order_by(SessionModel.created_at.desc()).all()

        session_list = [
            SessionInfo(
                session_id=session.session_id,
                created_at=session.created_at.isoformat(),
                updated_at=session.updated_at.isoformat() if session.updated_at else None,
                part_file_name=part_file_name
            )
            for session, part_file_name in sessions
        ]
        return session_list
    except SQLAlchemyError as e:
        logger.error(f"Database error when listing sessions: {str(e)}")
        raise HTTPException(status_code=500, detail="Database error occurred while retrieving sessions")
    except Exception as e:
        logger.error(f"Unexpected error when listing sessions: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

# 2. GET SESSION INFO
@app.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Get detailed information about a specific session."""
    try:
        # Get session
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get chat history count
        message_count = db.query(ChatHistory).filter(ChatHistory.session_id == session_id).count()

        # Get latest code
        latest_entry = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id,
            ChatHistory.lasted_code.isnot(None)
        ).order_by(ChatHistory.id.desc()).first()

        latest_code = latest_entry.lasted_code if latest_entry else None

        return SessionInfoResponse(
            session_id=session.session_id,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            message_count=message_count,
            latest_code=latest_code
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session info: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting session info: {str(e)}")

# 3. DELETE SESSION
@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Delete a specific session and its chat history."""
    try:
        # Check if session exists
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Delete chat history first (due to foreign key constraints)
        db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()

        # Delete session
        db.delete(session)
        db.commit()

        return {"message": f"Session {session_id} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")

# 4. GET EXPORT FILES
@app.get("/get-export", response_model=ExportResponse)
async def get_export(session_id: str, types: Optional[str] = None, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Get export files for a session."""
    try:
        # Get chat history with exports
        query = db.query(ChatHistory).filter(ChatHistory.session_id == session_id)

        if types:
            # Filter by export types
            from sqlalchemy import or_
            type_list = [t.strip().lower() for t in types.split(',')]
            filters = []
            if 'obj' in type_list:
                filters.append(ChatHistory.obj_export.isnot(None))
            if 'step' in type_list:
                filters.append(ChatHistory.step_export.isnot(None))

            if filters:
                query = query.filter(or_(*filters))

        exports = query.all()

        obj_exports = [entry.obj_export for entry in exports if entry.obj_export]
        step_exports = [entry.step_export for entry in exports if entry.step_export]

        return ExportResponse(
            session_id=session_id,
            obj_exports=obj_exports,
            step_exports=step_exports,
            total_exports=len(obj_exports) + len(step_exports)
        )

    except Exception as e:
        logger.error(f"Error getting exports: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting exports: {str(e)}")

# 5. GET CHAT HISTORY
@app.get("/chat-history/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Retrieve chat history for any session (PDF, Image, or Chat)."""
    logger.info(f"Getting chat history for session: {session_id}")

    try:
        # Query chat history for the session
        chat_entries = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id
        ).order_by(ChatHistory.created_at.asc()).all()

        if not chat_entries:
            return ChatHistoryResponse(
                session_id=session_id,
                messages=[],
                total_messages=0
            )

        # Convert to conversation format with role-based messages
        messages = []
        for entry in chat_entries:
            # Add user message
            if entry.message:
                messages.append({
                    "timestamp": entry.created_at.strftime("%Y-%m-%d %H:%M:%S.%f") if entry.created_at else None,
                    "role": "human",
                    "content": entry.message
                })

            # Add AI response if available
            if entry.response:
                ai_msg = {
                    "timestamp": (entry.response_at or entry.updated_at or entry.created_at).strftime("%Y-%m-%d %H:%M:%S.%f") if (entry.response_at or entry.updated_at or entry.created_at) else None,
                    "role": "ai",
                    "content": entry.response
                }
                # Add export information to AI response if available
                if entry.obj_export or entry.step_export:
                    ai_msg["exports"] = {
                        "obj": entry.obj_export,
                        "step": entry.step_export
                    }
                messages.append(ai_msg)
            elif entry.output:
                # Fallback to output if no response
                ai_msg = {
                    "timestamp": (entry.response_at or entry.updated_at or entry.created_at).strftime("%Y-%m-%d %H:%M:%S.%f") if (entry.response_at or entry.updated_at or entry.created_at) else None,
                    "role": "ai",
                    "content": entry.output
                }
                # Add export information to AI response if available
                if entry.obj_export or entry.step_export:
                    ai_msg["exports"] = {
                        "obj": entry.obj_export,
                        "step": entry.step_export
                    }
                messages.append(ai_msg)

        return ChatHistoryResponse(
            session_id=session_id,
            messages=messages,
            total_messages=len(messages)
        )

    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting chat history: {str(e)}")

# 6. CHAT (Regular text chat)
@app.post("/chat", response_model=ChatResponse)
async def chat(request_data: ChatRequest, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Process a chat message and generate CAD code with session continuity."""
    user_message = request_data.message
    session_id = request_data.session_id

    logger.info(f"Chat request received: '{user_message[:50]}...' (session_id: {session_id})")

    if not user_message:
        logger.warning("Empty message received")
        raise HTTPException(status_code=400, detail="No message provided")

    try:
        # Create a ChatRequest for the API
        from src.schemas.sessions import ChatRequest as ApiChatRequest
        api_request = ApiChatRequest(
            message=user_message,
            session_id=session_id,
            image_path=request_data.image_path,
            part_file_name=request_data.part_file_name,
            export_format=request_data.export_format,
            material_choice=request_data.material_choice,
            selected_feature_uuid=request_data.selected_feature_uuid,
            is_edit_request=request_data.is_edit_request
        )

        # Process using our enhanced session handling in CRUD
        result = await crud.chat_processing.handle_chat_request(db, api_request, text_to_cad_agent, request_origin='web')

        # Check for errors
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"Error processing chat request: {result['error']}")
            return ChatResponse(
                message=result.get("message", "An error occurred"),
                session_id=session_id or "unknown",
                error=result["error"]
            )

        # Return successful response
        return ChatResponse(
            message=result.chat_response,
            session_id=result.session_id,
            obj_export=result.obj_export,
            step_export=result.step_export
        )

    except Exception as e:
        logger.exception(f"Unexpected error in chat endpoint: {str(e)}")
        return ChatResponse(
            message="An error occurred while processing your request",
            session_id=session_id or "unknown",
            error=str(e)
        )

# Edit mode endpoint
@app.post("/edit", tags=["cad"])
async def edit_chat(request_data: ChatRequest, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Process an edit request to modify existing CAD code."""
    user_message = request_data.message
    session_id = request_data.session_id

    logger.info(f"Edit request received: '{user_message[:50]}...' (session_id: {session_id})")

    if not user_message:
        logger.warning("Empty message received")
        raise HTTPException(status_code=400, detail="No message provided")

    if not session_id:
        logger.warning("No session ID provided for edit request")
        raise HTTPException(status_code=400, detail="Session ID is required for edit mode")

    try:
        # First check if code exists for this session
        latest_code = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id,
            ChatHistory.lasted_code.isnot(None)
        ).order_by(ChatHistory.created_at.desc()).first()

        if not latest_code or not latest_code.lasted_code:
            error_msg = "No code has been generated in this session yet. Generate code first before using edit mode."
            logger.warning(f"{error_msg} Session ID: {session_id}")
            return ChatResponse(
                message=error_msg,
                session_id=session_id,
                error=error_msg
            )

        # Set is_edit_request to True and call the chat endpoint
        request_data.is_edit_request = True
        return await chat(request_data, db)

    except Exception as e:
        logger.exception(f"Unexpected error in edit endpoint: {str(e)}")
        return ChatResponse(
            message="An error occurred while processing your edit request",
            session_id=session_id or "unknown",
            error=str(e)
        )

# 11. CHECK FREECAD VERSION AND INSTALLATION
@app.get("/check-freecad", response_model=FreeCADVersionResponse)
async def check_freecad_version(token: str = Depends(verify_token)):
    """Check FreeCAD installation and version with comprehensive tests."""
    start_time = time.time()

    logger.info("🔍 Starting FreeCAD installation check...")

    # Define tests to run
    tests = [
        ("APT Show FreeCAD Package", test_apt_show_freecad),
        ("FreeCADCmd Version Check", test_freecadcmd_version),
        ("FreeCAD Python Import", test_freecad_import),
        ("FreeCAD Basic Operations", test_freecad_basic_operations),
    ]

    test_results = []
    passed = 0
    freecad_version = None
    apt_package_info = None

    try:
        for test_name, test_func in tests:
            logger.info(f"🧪 Running: {test_name}")

            try:
                success, output, error = test_func()

                test_results.append(FreeCADTestResult(
                    test_name=test_name,
                    success=success,
                    output=output,
                    error=error
                ))

                if success:
                    passed += 1
                    logger.info(f"✅ {test_name} passed")

                    # Extract version info
                    if test_name == "FreeCADCmd Version Check" and "FreeCAD" in output:
                        freecad_version = output.strip()
                    elif test_name == "APT Show FreeCAD Package":
                        apt_package_info = output.strip()
                else:
                    logger.warning(f"❌ {test_name} failed: {error}")

            except Exception as e:
                logger.error(f"❌ {test_name} error: {str(e)}")
                test_results.append(FreeCADTestResult(
                    test_name=test_name,
                    success=False,
                    output="",
                    error=str(e)
                ))

        end_time = time.time()
        execution_time = end_time - start_time
        overall_success = passed == len(tests)

        logger.info(f"📊 FreeCAD check completed: {passed}/{len(tests)} tests passed")

        return FreeCADVersionResponse(
            overall_success=overall_success,
            tests_passed=passed,
            total_tests=len(tests),
            test_results=test_results,
            freecad_version=freecad_version,
            apt_package_info=apt_package_info,
            execution_time=execution_time
        )

    except Exception as e:
        end_time = time.time()
        execution_time = end_time - start_time
        error_msg = f"Error during FreeCAD check: {str(e)}"
        logger.error(error_msg)

        return FreeCADVersionResponse(
            overall_success=False,
            tests_passed=passed,
            total_tests=len(tests),
            test_results=test_results,
            freecad_version=freecad_version,
            apt_package_info=apt_package_info,
            execution_time=execution_time,
            error=error_msg
        )

# 12. GET SYSTEM INFORMATION
@app.get("/system-info", response_model=SystemInfoResponse)
async def get_system_information(token: str = Depends(verify_token)):
    """Get comprehensive system information including OS, hardware, and Docker details."""
    logger.info("🖥️  Getting system information...")

    try:
        system_info = get_system_info()

        logger.info(f"📊 System Info: {system_info['os_name']} {system_info['os_version']} on {system_info['architecture']}")

        return SystemInfoResponse(**system_info)

    except Exception as e:
        error_msg = f"Error getting system information: {str(e)}"
        logger.error(error_msg)

        return SystemInfoResponse(
            os_name="Error",
            os_version="Error",
            kernel_version="Error",
            architecture="Error",
            hostname="Error",
            python_version="Error",
            platform_info="Error",
            cpu_count=0,
            memory_total="Error",
            disk_usage="Error",
            error=error_msg
        )

# 13. CHECK PYTHON ENVIRONMENT
@app.get("/python-env", response_model=PythonEnvironmentResponse)
async def check_python_environment_endpoint(token: str = Depends(verify_token)):
    """Check Python environment and standard library availability."""
    logger.info("🐍 Checking Python environment...")

    try:
        env_info = check_python_environment()

        logger.info(f"📊 Python: {env_info['python_version'][:20]}...")
        logger.info(f"📦 Packages: {len(env_info['site_packages'])} found")
        logger.info(f"❌ Missing stdlib: {len(env_info['missing_stdlib'])} modules")

        return PythonEnvironmentResponse(**env_info)

    except Exception as e:
        error_msg = f"Error checking Python environment: {str(e)}"
        logger.error(error_msg)

        return PythonEnvironmentResponse(
            python_executable="Error",
            python_version="Error",
            python_path=[],
            stdlib_modules=[],
            missing_stdlib=[],
            site_packages=[],
            freecad_paths=[],
            environment_vars={},
            error=error_msg
        )

# 14. COMPREHENSIVE DIAGNOSIS
@app.get("/diagnosis", response_model=DiagnosticResponse)
async def comprehensive_diagnosis(token: str = Depends(verify_token)):
    """Run comprehensive system and FreeCAD diagnosis."""
    logger.info("🔍 Running comprehensive diagnosis...")

    try:
        # Get all information
        system_info = get_system_info()
        python_env = check_python_environment()
        freecad_diagnosis = diagnose_freecad_issues()

        # Generate recommendations
        recommendations = []
        overall_status = "HEALTHY"

        # Analyze system
        if system_info.get('error'):
            recommendations.append("System information collection failed")
            overall_status = "ERROR"

        # Analyze Python
        if python_env.get('missing_stdlib'):
            recommendations.append(f"Missing Python stdlib modules: {', '.join(python_env['missing_stdlib'][:5])}")
            overall_status = "CRITICAL"

        if python_env.get('error'):
            recommendations.append("Python environment check failed")
            overall_status = "ERROR"

        # Analyze FreeCAD
        freecad_issues = freecad_diagnosis.get('issues_found', [])
        if freecad_issues:
            recommendations.extend(freecad_issues[:3])  # Top 3 issues
            if overall_status == "HEALTHY":
                overall_status = "WARNING"

        freecad_suggestions = freecad_diagnosis.get('suggestions', [])
        recommendations.extend(freecad_suggestions[:2])  # Top 2 suggestions

        # Container-specific recommendations
        if system_info.get('container_id'):
            recommendations.append("Running in Docker container - check Dockerfile configuration")

        # Final status
        if not recommendations:
            overall_status = "HEALTHY"
            recommendations.append("All systems appear to be functioning normally")

        logger.info(f"📊 Diagnosis complete: {overall_status}")
        logger.info(f"📝 Recommendations: {len(recommendations)} items")

        return DiagnosticResponse(
            system_info=system_info,
            python_env=python_env,
            freecad_diagnosis=freecad_diagnosis,
            recommendations=recommendations,
            overall_status=overall_status
        )

    except Exception as e:
        error_msg = f"Error during comprehensive diagnosis: {str(e)}"
        logger.error(error_msg)

        return DiagnosticResponse(
            system_info={"error": "Failed to collect"},
            python_env={"error": "Failed to collect"},
            freecad_diagnosis={"error": "Failed to collect"},
            recommendations=[f"Diagnosis failed: {str(e)}"],
            overall_status="ERROR",
            error=error_msg
        )

# Migration response models live with the other models near the top of the file;
# a second RollbackResponse used to be redefined here and silently shadowed it,
# dropping five of the nine fields rollback_migration() actually returns.


# Import and mount PDF and Image Chat routes (endpoints 7 & 8)
# PDF Chat router removed - using main routes/pdf_chat.py instead to eliminate duplication

# 15. LIST AVAILABLE MIGRATIONS
@app.get("/migrations", response_model=MigrationsListResponse)
async def list_migrations(token: str = Depends(verify_token)):
    """List all available migration files in the migrations directory."""
    logger.info("📋 Listing available migrations...")

    try:
        result = get_available_migrations()

        logger.info(f"📊 Found {result['total_count']} migration files")

        return MigrationsListResponse(**result)

    except Exception as e:
        error_msg = f"Error listing migrations: {str(e)}"
        logger.error(error_msg)

        return MigrationsListResponse(
            migrations=[],
            total_count=0,
            migrations_path="",
            error=error_msg
        )

# 16. RUN SPECIFIC MIGRATION
@app.post("/migrations/run/{migration_name}", response_model=MigrationRunResponse)
async def run_specific_migration(migration_name: str, token: str = Depends(verify_token)):
    """Run a specific migration by name."""
    logger.info(f"🚀 Running migration: {migration_name}")

    try:
        result = execute_migration(migration_name)

        if result["success"]:
            logger.info(f"✅ Migration {migration_name} completed successfully")
        else:
            logger.error(f"❌ Migration {migration_name} failed: {result.get('error', 'Unknown error')}")

        return MigrationRunResponse(**result)

    except Exception as e:
        error_msg = f"Error running migration {migration_name}: {str(e)}"
        logger.error(error_msg)

        return MigrationRunResponse(
            migration_name=migration_name,
            success=False,
            message=f"Migration execution failed: {str(e)}",
            execution_time=0.0,
            started_at=datetime.now().isoformat(),
            completed_at=datetime.now().isoformat(),
            error=error_msg
        )

# 17. ROLLBACK MIGRATION
@app.post("/migrations/rollback/{migration_name}", response_model=RollbackResponse)
async def rollback_migration(migration_name: str, token: str = Depends(verify_token)):
    """Rollback a specific migration by running its rollback script."""
    logger.info(f"🔄 Rolling back migration: {migration_name}")

    # Ensure we're using the rollback version
    if not migration_name.startswith("rollback_"):
        # Convert add migration name to rollback name
        if migration_name.startswith("add_"):
            rollback_name = migration_name.replace("add_", "rollback_", 1)
        else:
            rollback_name = f"rollback_{migration_name}"
    else:
        rollback_name = migration_name

    try:
        # Call execute_migration to run the rollback
        result = execute_migration(rollback_name)

        if result.get("success"):
            logger.info(f"✅ Rollback {rollback_name} completed successfully")
        else:
            logger.error(f"❌ Rollback {rollback_name} failed: {result.get('error', 'Unknown error')}")

        # Create proper response for rollback
        return RollbackResponse(
            migration_name=rollback_name,
            success=result.get("success", False),
            message=result.get("message", "Rollback completed"),
            column_existed=result.get("column_existed", result.get("column_exists", False)),
            verification_passed=result.get("verification_passed", True if result.get("success") else False),
            execution_time=result.get("execution_time", 0.0),
            started_at=result.get("started_at", datetime.now().isoformat()),
            completed_at=result.get("completed_at", datetime.now().isoformat()),
            error=result.get("error")
        )

    except Exception as e:
        error_msg = f"Error running rollback {rollback_name}: {str(e)}"
        logger.error(error_msg)

        return RollbackResponse(
            migration_name=rollback_name,
            success=False,
            message=f"Rollback execution failed: {str(e)}",
            column_existed=False,
            verification_passed=False,
            execution_time=0.0,
            started_at=datetime.now().isoformat(),
            completed_at=datetime.now().isoformat(),
            error=error_msg
        )

# 18. GET DATABASE SCHEMA STATUS
@app.get("/migrations/status", response_model=MigrationStatusResponse)
async def get_migration_status(token: str = Depends(verify_token)):
    """
    🔍 Get current database schema status and migration information.

    Shows all columns in chat_history table and their properties.
    Useful for checking what columns exist before adding/removing.
    """
    logger.info("🔍 Checking database schema status...")

    try:
        result = get_database_schema_status()

        logger.info(f"📊 Database schema: {result['total_columns']} columns in chat_history table")
        logger.info(f"📋 Technical drawing column exists: {result['technical_drawing_column_exists']}")

        return MigrationStatusResponse(**result)

    except Exception as e:
        error_msg = f"Error checking migration status: {str(e)}"
        logger.error(error_msg)

        return MigrationStatusResponse(
            chat_history_columns=[],
            technical_drawing_column_exists=False,
            total_columns=0,
            schema_check_time=datetime.now().isoformat(),
            error=error_msg
        )

# 19. GENERIC ADD COLUMN
@app.post("/migrations/add-column")
async def add_column_generic(
    column_name: str,
    column_type: str = "VARCHAR(255)",
    nullable: bool = True,
    comment: str = None,
    token: str = Depends(verify_token)
):
    """Add a column to chat_history table. Usage: ?column_name=field_name&column_type=VARCHAR(255)"""
    logger.info(f"🔧 Adding column: {column_name} ({column_type})")

    try:
        from src.database.migrations.generic_column_migration import add_column
        result = add_column(column_name, column_type, nullable, comment)

        if result["success"]:
            logger.info(f"✅ Column '{column_name}' added successfully")
        else:
            logger.error(f"❌ Failed to add column '{column_name}': {result['error']}")

        return result

    except Exception as e:
        error_msg = f"Error adding column {column_name}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": "Column addition failed",
            "error": error_msg
        }

# 20. GENERIC REMOVE COLUMN
@app.post("/migrations/remove-column")
async def remove_column_generic(column_name: str, token: str = Depends(verify_token)):
    """Remove a column from chat_history table. Usage: ?column_name=field_name ⚠️ Deletes data permanently!"""
    logger.info(f"🗑️ Removing column: {column_name}")

    try:
        from src.database.migrations.generic_column_migration import remove_column
        result = remove_column(column_name)

        if result["success"]:
            logger.info(f"✅ Column '{column_name}' removed successfully")
        else:
            logger.error(f"❌ Failed to remove column '{column_name}': {result['error']}")

        return result

    except Exception as e:
        error_msg = f"Error removing column {column_name}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": "Column removal failed",
            "error": error_msg
        }



# Log viewer endpoints moved to log_viewer.py
# Import and register routes
from . import log_viewer
log_viewer.register_log_endpoints(app, verify_token)

# 21. EXPORT CONVERSATIONS TO XLSX
@app.get("/export-conversations/xlsx", tags=["session"])
async def export_conversations_xlsx(
    top_n: int = Query(default=5, ge=1, le=100, description="Number of most recent sessions to export (used when neither session_id nor days is provided)"),
    session_id: Optional[str] = Query(default=None, description="Export a specific session only (highest priority, overrides days and top_n)"),
    days: Optional[int] = Query(default=None, ge=1, le=365, description="Export ALL sessions updated within the last N days (overrides top_n; ignored when session_id is set)"),
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """
    Export conversations to a formatted Excel (.xlsx) file.

    **Priority order (highest → lowest):**
    1. **session_id** – export exactly one session
    2. **days** – export ALL sessions updated within the last N days (e.g. `days=7`)
    3. **top_n** – export the N most recently updated sessions (default: 5)

    Columns: Type | Turn # | Timestamp | Content (User Message / AI Response)

    Formatting:
    - Header row: dark navy background, white bold text
    - Session separator bars (medium blue)
    - User rows: pale sky-blue | AI rows: pale mint-green
    - Wrap text, frozen header, auto-filter
    """
    import openpyxl
    import re as _re
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

    from datetime import timedelta

    # Regex to strip illegal XML/Excel control characters from cell text
    # openpyxl rejects U+0000–U+001F except \t (\x09), \n (\x0A), \r (\x0D)
    _ILLEGAL_XML_CHARS = _re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

    def sanitize_for_excel(text: str) -> str:
        """Replace control characters illegal in Excel cells with a space."""
        if not text:
            return text
        return _ILLEGAL_XML_CHARS.sub(' ', text)

    logger.info(f"XLSX export requested: top_n={top_n}, days={days}, session_id={session_id}")

    try:
        # ── Step 1: Determine target sessions ──────────────────────────────
        # Priority: session_id > days > top_n
        if session_id:
            # ── Mode 1: single specific session ────────────────────────────
            target_session = db.query(SessionModel).filter(
                SessionModel.session_id == session_id
            ).first()
            if not target_session:
                raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
            target_sessions = [target_session]
            filename_suffix = f"session_{session_id[:8]}"

        elif days is not None:
            # ── Mode 2: all sessions updated within the last N days ─────────
            cutoff_dt = datetime.utcnow() - timedelta(days=days)
            target_sessions = (
                db.query(SessionModel)
                .filter(SessionModel.updated_at >= cutoff_dt)
                .order_by(SessionModel.updated_at.desc())
                .all()
            )
            if not target_sessions:
                raise HTTPException(
                    status_code=404,
                    detail=f"No sessions found in the last {days} day(s) (cutoff: {cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC)"
                )
            filename_suffix = f"last{days}days"
            logger.info(f"Days mode: fetched {len(target_sessions)} sessions since {cutoff_dt.isoformat()} UTC")

        else:
            # ── Mode 3: top N most recent sessions (default) ────────────────
            target_sessions = db.query(SessionModel).order_by(
                SessionModel.updated_at.desc()
            ).limit(top_n).all()
            if not target_sessions:
                raise HTTPException(status_code=404, detail="No sessions found in database")
            filename_suffix = f"top{top_n}_sessions"

        session_ids   = [s.session_id for s in target_sessions]
        logger.info(f"Exporting {len(session_ids)} sessions to single-sheet XLSX")

        # ── Step 2: Workbook + single sheet ────────────────────────────────
        def make_safe_sheet_title(raw_title: str, fallback: str = "Conversations") -> str:
            """
            Keep worksheet titles Excel-safe without touching conversation text content.
            Excel constraints:
            - Max length: 31
            - Invalid chars: []:*?/\\
            """
            title = (raw_title or "").strip()
            if not title:
                return fallback

            for ch in '[]:*?/\\':
                title = title.replace(ch, " ")

            title = " ".join(title.split())
            title = title[:31].strip()
            if not title:
                return fallback
            return title

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = make_safe_sheet_title("Conversations")

        # ── Colour palette ──────────────────────────────────────────────────
        # Column-header bar (dark navy)
        COL_HDR_FILL  = PatternFill("solid", fgColor="1F3864")
        COL_HDR_FONT  = Font(bold=True, color="FFFFFF", size=11)

        # Session separator bar (medium blue)
        SESS_FILL     = PatternFill("solid", fgColor="2E75B6")
        SESS_FONT     = Font(bold=True, color="FFFFFF", size=10, italic=True)

        # User message rows (pale sky-blue)
        USER_FILL     = PatternFill("solid", fgColor="DEEAF1")
        USER_LABEL_F  = Font(bold=True, color="1F3864", size=9)

        # AI response rows (pale mint-green)
        AI_FILL       = PatternFill("solid", fgColor="E2EFDA")
        AI_LABEL_F    = Font(bold=True, color="375623", size=9)

        # Text font for content cells
        CONTENT_FONT  = Font(size=10)

        # Thin border
        _t = Side(style="thin", color="BDD7EE")
        BORDER = Border(left=_t, right=_t, top=_t, bottom=_t)

        # Alignments
        CENTER  = Alignment(horizontal="center", vertical="center", wrap_text=False)
        LABEL_A = Alignment(horizontal="center", vertical="top")
        TEXT_A  = Alignment(horizontal="left",   vertical="top", wrap_text=True)

        # ── Column layout ───────────────────────────────────────────────────
        # A: Type label  | B: Turn # | C: Timestamp | D: Content
        COL_WIDTHS = {"A": 12, "B": 7, "C": 22, "D": 110}
        for col_letter, width in COL_WIDTHS.items():
            ws.column_dimensions[col_letter].width = width

        # ── Global column-header row (row 1) ───────────────────────────────
        HDR_LABELS = ["Type", "Turn #", "Timestamp", "Content (User Message / AI Response)"]
        for col_idx, label in enumerate(HDR_LABELS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=label)
            cell.fill   = COL_HDR_FILL
            cell.font   = COL_HDR_FONT
            cell.alignment = CENTER
            cell.border = BORDER
        ws.row_dimensions[1].height = 26
        ws.freeze_panes = "A2"   # Freeze column-header row

        # ── Auto-filter on column-header row ───────────────────────────────
        ws.auto_filter.ref = "A1:D1"

        # ── Step 3: Write data ─────────────────────────────────────────────
        current_row = 2

        for sess_idx, sess_obj in enumerate(target_sessions, start=1):
            sid     = sess_obj.session_id
            entries = db.query(ChatHistory).filter(
                ChatHistory.session_id == sid
            ).order_by(ChatHistory.id.asc()).all()

            created_str = sess_obj.created_at.strftime("%Y-%m-%d %H:%M:%S") if sess_obj.created_at else "N/A"
            updated_str = sess_obj.updated_at.strftime("%Y-%m-%d %H:%M:%S") if sess_obj.updated_at else "N/A"

            # ── Session separator bar (spans all 4 cols) ──────────────────
            ws.merge_cells(
                start_row=current_row, start_column=1,
                end_row=current_row,   end_column=4
            )
            sep = ws.cell(
                row=current_row, column=1,
                value=(
                    f"  SESSION {sess_idx}   ID: {sid}"
                    f"   ·   Created: {created_str}"
                    f"   ·   Updated: {updated_str}"
                    f"   ·   Turns: {len(entries)}"
                )
            )
            sep.fill      = SESS_FILL
            sep.font      = SESS_FONT
            sep.alignment = Alignment(horizontal="left", vertical="center")
            sep.border    = BORDER
            ws.row_dimensions[current_row].height = 20
            current_row += 1

            # ── Conversation turns ─────────────────────────────────────────
            for turn_idx, entry in enumerate(entries, start=1):
                ts_str     = entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else ""
                ai_content = entry.response or entry.output or ""
                user_msg   = entry.message or ""

                # ── Sanitize illegal XML/Excel control characters ──────────
                # openpyxl rejects U+0000–U+001F except \t (\x09), \n (\x0A), \r (\x0D)
                # Common offenders: \u000b (Vertical Tab), \u0000 (NUL), \u000c (Form Feed)

                ai_content = sanitize_for_excel(ai_content)
                user_msg   = sanitize_for_excel(user_msg)

                # Estimate row heights (each ~90 chars ≈ 1 line in col D at width 110)
                def row_ht(text: str) -> int:
                    lines = max(1, len(text) // 100 + text.count("\n") + 1)
                    return min(max(18, lines * 15), 300)

                # ── USER row ──────────────────────────────────────────────
                u_row = current_row

                c_type = ws.cell(row=u_row, column=1, value="👤 User")
                c_type.fill = USER_FILL; c_type.font = USER_LABEL_F
                c_type.alignment = LABEL_A; c_type.border = BORDER

                c_turn = ws.cell(row=u_row, column=2, value=turn_idx)
                c_turn.fill = USER_FILL; c_turn.font = Font(bold=True, size=10)
                c_turn.alignment = CENTER; c_turn.border = BORDER

                c_ts = ws.cell(row=u_row, column=3, value=ts_str)
                c_ts.fill = USER_FILL; c_ts.font = Font(size=9, color="555555")
                c_ts.alignment = CENTER; c_ts.border = BORDER

                c_msg = ws.cell(row=u_row, column=4, value=user_msg)
                c_msg.fill = USER_FILL; c_msg.font = CONTENT_FONT
                c_msg.alignment = TEXT_A; c_msg.border = BORDER

                ws.row_dimensions[u_row].height = row_ht(user_msg)
                current_row += 1

                # ── AI row ────────────────────────────────────────────────
                a_row = current_row

                c_type2 = ws.cell(row=a_row, column=1, value="🤖 AI")
                c_type2.fill = AI_FILL; c_type2.font = AI_LABEL_F
                c_type2.alignment = LABEL_A; c_type2.border = BORDER

                c_turn2 = ws.cell(row=a_row, column=2, value=turn_idx)
                c_turn2.fill = AI_FILL; c_turn2.font = Font(bold=True, size=10)
                c_turn2.alignment = CENTER; c_turn2.border = BORDER

                # AI timestamp = explicit response timestamp (fallback for old rows)
                ai_time = entry.response_at or entry.updated_at or entry.created_at
                ai_ts = ai_time.strftime("%Y-%m-%d %H:%M:%S") if ai_time else ts_str
                c_ts2 = ws.cell(row=a_row, column=3, value=ai_ts)
                c_ts2.fill = AI_FILL; c_ts2.font = Font(size=9, color="555555")
                c_ts2.alignment = CENTER; c_ts2.border = BORDER

                c_ai = ws.cell(row=a_row, column=4, value=ai_content)
                c_ai.fill = AI_FILL; c_ai.font = CONTENT_FONT
                c_ai.alignment = TEXT_A; c_ai.border = BORDER

                ws.row_dimensions[a_row].height = row_ht(ai_content)
                current_row += 1

            # Blank separator after each session
            current_row += 1

        # ── Step 4: Serialize and save to disk ───────────────────────────────────
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        today_date_str = datetime.now().strftime('%Y-%m-%d')
        filename  = f"conversations_{filename_suffix}_{timestamp}.xlsx"
        
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
        out_dir = os.path.join(project_root, "outputs", "xlsx", today_date_str)
        os.makedirs(out_dir, exist_ok=True)
        
        file_path = os.path.join(out_dir, filename)
        
        # Save directly to file
        wb.save(file_path)

        total_turns = sum(
            db.query(ChatHistory).filter(ChatHistory.session_id == sid).count()
            for sid in session_ids
        )
        
        # Build download URL
        from src.utils.download_url import resolve_base_url
        BASE_URL = resolve_base_url()

        # Construct the standardized URL
        download_url = f"{BASE_URL}/download/outputs/xlsx/{today_date_str}/{filename}"
        download_url = download_url.replace("//download", "/download")
        if download_url.startswith("http:/download"):
            download_url = download_url.replace("http:/download", "http://download")
        if download_url.startswith("https:/download"):
            download_url = download_url.replace("https:/download", "https://download")
        
        # Determine which mode was used (for response transparency)
        if session_id:
            export_mode = "session_id"
        elif days is not None:
            export_mode = "days"
        else:
            export_mode = "top_n"

        logger.info(f"XLSX export saved: {file_path} | {total_turns} turns | {len(session_ids)} sessions | mode={export_mode}")

        return {
            "success": True,
            "message": "Export completed successfully",
            "export_mode": export_mode,
            "date_range_days": days,
            "filename": filename,
            "download_url": download_url,
            "total_turns": total_turns,
            "total_sessions": len(session_ids)
        }

    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="openpyxl is not installed. Run: pip install openpyxl==3.1.5"
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error during XLSX export: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during XLSX export: {str(e)}")
        raise HTTPException(status_code=500, detail=f"XLSX export failed: {str(e)}")


# 21b. PREVIEW CONVERSATIONS BEFORE XLSX EXPORT
@app.get("/export-conversations/preview", response_model=ConversationCSVPreviewResponse, tags=["session"])
async def preview_conversations_export(
    top_n: int = Query(default=5, ge=1, le=100, description="Number of most recent sessions to preview"),
    session_id: Optional[str] = Query(default=None, description="Preview a specific session only"),
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """
    Preview conversations that would be exported to XLSX.

    Returns JSON with session list, total counts, and first 20 rows of data.
    Use this to verify data before calling the actual XLSX export endpoint.
    """
    logger.info(f"XLSX preview requested: top_n={top_n}, session_id={session_id}")

    try:
        # --- Step 1: Determine target sessions ---
        if session_id:
            target_session = db.query(SessionModel).filter(
                SessionModel.session_id == session_id
            ).first()
            if not target_session:
                raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
            target_sessions = [target_session]
        else:
            target_sessions = db.query(SessionModel).order_by(
                SessionModel.updated_at.desc()
            ).limit(top_n).all()

            if not target_sessions:
                raise HTTPException(status_code=404, detail="No sessions found in database")

        session_ids = [s.session_id for s in target_sessions]
        session_map = {s.session_id: s for s in target_sessions}

        # --- Step 2: Fetch chat history ---
        chat_entries = db.query(ChatHistory).filter(
            ChatHistory.session_id.in_(session_ids)
        ).order_by(
            ChatHistory.session_id,
            ChatHistory.id.asc()
        ).all()

        # --- Step 3: Build preview rows (max 20) ---
        preview_rows = []
        turn_counter = {}

        for entry in chat_entries:
            sid = entry.session_id
            if sid not in turn_counter:
                turn_counter[sid] = 1
            else:
                turn_counter[sid] += 1

            if len(preview_rows) >= 20:
                continue  # Count all but only build first 20 for preview

            session_obj = session_map.get(sid)
            session_created = session_obj.created_at.strftime("%Y-%m-%d %H:%M:%S") if session_obj and session_obj.created_at else ""

            ai_response = entry.response or entry.output or ""

            preview_rows.append(ConversationRow(
                session_id=sid,
                session_created_at=session_created,
                conversation_id=entry.id,
                user_message=entry.message,
                ai_response=ai_response if len(ai_response) <= 300 else ai_response[:300] + "...[truncated]",
                part_file_name=entry.part_file_name,
                export_format=entry.export_format,
                material_choice=entry.material_choice,
                obj_export=entry.obj_export,
                step_export=entry.step_export,
                created_at=entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else ""
            ))

        total_rows = len(chat_entries)
        n_param = f"session_id={session_id}" if session_id else f"top_n={top_n}"

        return ConversationCSVPreviewResponse(
            total_sessions=len(session_ids),
            total_rows=total_rows,
            sessions_included=session_ids,
            preview_data=preview_rows,
            export_hint=f"Call GET /export-conversations/xlsx?{n_param} to download the formatted Excel file ({total_rows} turns across {len(session_ids)} sessions)"
        )

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error during preview: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during preview: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")



# 22. MIGRATION HELP & DOCUMENTATION
@app.get("/migrations/help")
async def migration_help(token: str = Depends(verify_token)):
    """
    📚 Complete guide for using migration endpoints.

    This endpoint provides comprehensive documentation for all migration operations.
    """
    return {
        "title": "🔧 Tolery API - Migration System Guide",
        "description": "Complete guide for database column migrations in chat_history table",

        "quick_start": {
            "most_common_operations": [
                {
                    "operation": "Add technical drawing column",
                    "endpoint": "POST /api/migrations/quick/add-technical-drawing",
                    "description": "Adds technical_drawing_export column for PDF file paths"
                },
                {
                    "operation": "Drop the legacy JSON export column",
                    "endpoint": "POST /api/migrations/quick/remove-json-export",
                    "description": "Drops json_export — the STEP->JSON pipeline was removed"
                },
                {
                    "operation": "List all columns",
                    "endpoint": "POST /api/migrations/quick/list-columns",
                    "description": "Shows all columns in chat_history table"
                }
            ]
        },

        "endpoints": {
            "generic_operations": {
                "add_column": {
                    "endpoint": "POST /api/migrations/add-column",
                    "parameters": {
                        "column_name": "string (required) - Name of column to add",
                        "column_type": "string (optional) - SQL data type, default: VARCHAR(255)",
                        "nullable": "boolean (optional) - Can be NULL, default: true",
                        "comment": "string (optional) - Column description"
                    },
                    "examples": [
                        "?column_name=new_field",
                        "?column_name=price&column_type=DECIMAL(10,2)",
                        "?column_name=status&comment=Order status&nullable=false"
                    ]
                },
                "remove_column": {
                    "endpoint": "POST /api/migrations/remove-column",
                    "parameters": {
                        "column_name": "string (required) - Name of column to remove"
                    },
                    "warning": "⚠️ This permanently deletes the column and ALL its data!",
                    "examples": [
                        "?column_name=old_field",
                        "?column_name=json_export"
                    ]
                }
            },

            "quick_shortcuts": {
                "endpoint": "POST /api/migrations/quick/{action}",
                "available_actions": [
                    "add-technical-drawing - Add technical_drawing_export column",
                    "remove-technical-drawing - Remove technical_drawing_export column",
                    "remove-json-export - Drop the legacy json_export column",
                    "list-columns - Show all columns"
                ]
            },

            "file_based_migrations": {
                "list_migrations": "GET /api/migrations - List all migration files",
                "run_migration": "POST /api/migrations/run/{filename} - Run specific migration file",
                "rollback_migration": "POST /api/migrations/rollback/{filename} - Rollback specific migration",
                "migration_status": "GET /api/migrations/status - Check database schema status"
            }
        },

        "common_column_types": {
            "text": ["VARCHAR(255)", "TEXT", "LONGTEXT"],
            "numbers": ["INT", "BIGINT", "DECIMAL(10,2)", "FLOAT"],
            "dates": ["DATETIME", "DATE", "TIMESTAMP"],
            "boolean": ["BOOLEAN", "TINYINT(1)"],
            "json": ["JSON", "LONGTEXT"]
        },

        "best_practices": [
            "🔍 Always check current schema with: POST /api/migrations/quick/list-columns",
            "💾 Backup database before removing columns",
            "🧪 Test migrations on development environment first",
            "📝 Use descriptive column names and comments",
            "⚡ Use quick shortcuts for common operations",
            "🔄 Verify results after each migration"
        ],

        "troubleshooting": {
            "column_already_exists": "Use list-columns to check existing columns first",
            "column_not_found": "Verify column name spelling and case sensitivity",
            "permission_denied": "Check database user permissions for ALTER TABLE",
            "migration_failed": "Check logs for detailed error messages"
        }
    }

# PDF Chat router removed - using main routes/pdf_chat.py instead to eliminate duplication
# app.include_router(pdf_chat_router, tags=["pdf-chat"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8125)
