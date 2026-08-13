"""
Generic Column Migration Utility
Provides functions to add or remove columns from chat_history table dynamically.
Usage: Can be called from API endpoints with column name and operation type.
"""
import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from src.database.database import engine

def add_column(column_name: str, column_type: str = "VARCHAR(255)", nullable: bool = True, comment: str = None):
    """
    Add a column to chat_history table if it doesn't exist.
    
    Args:
        column_name: Name of the column to add
        column_type: SQL data type (default: VARCHAR(255))
        nullable: Whether column can be NULL (default: True)
        comment: Optional comment for the column
        
    Returns: dict with migration result information
    """
    try:
        with engine.connect() as connection:
            # Check if column already exists
            check_column_query = text("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'chat_history'
                AND COLUMN_NAME = :column_name
            """)

            result = connection.execute(check_column_query, {"column_name": column_name})
            column_exists = result.scalar() > 0

            if column_exists:
                message = f"Column '{column_name}' already exists in 'chat_history' table"
                print(message)
                return {
                    "success": True,
                    "message": message,
                    "column_exists": True,
                    "verification_passed": True,
                    "error": None
                }

            # Build ALTER TABLE query
            null_clause = "NULL" if nullable else "NOT NULL"
            comment_clause = f"COMMENT '{comment}'" if comment else ""
            
            alter_query = text(f"""
                ALTER TABLE chat_history
                ADD COLUMN {column_name} {column_type} {null_clause} {comment_clause}
            """)

            connection.execute(alter_query)
            connection.commit()

            # Verify column was added
            verify_result = connection.execute(check_column_query, {"column_name": column_name})
            verification_passed = verify_result.scalar() > 0

            if verification_passed:
                message = f"Successfully added '{column_name}' column to 'chat_history' table"
                print(message)
                return {
                    "success": True,
                    "message": message,
                    "column_exists": False,
                    "verification_passed": True,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "message": "Migration completed but verification failed",
                    "column_exists": False,
                    "verification_passed": False,
                    "error": "Column verification failed after migration"
                }

    except Exception as e:
        error_msg = f"Error adding {column_name} column: {e}"
        print(error_msg)
        return {
            "success": False,
            "message": "Migration failed",
            "column_exists": False,
            "verification_passed": False,
            "error": error_msg
        }

def remove_column(column_name: str):
    """
    Remove a column from chat_history table if it exists.
    
    Args:
        column_name: Name of the column to remove
        
    Returns: dict with rollback result information
    """
    try:
        with engine.connect() as connection:
            # Check if column exists
            check_column_query = text("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'chat_history'
                AND COLUMN_NAME = :column_name
            """)

            result = connection.execute(check_column_query, {"column_name": column_name})
            column_existed = result.scalar() > 0

            if not column_existed:
                message = f"Column '{column_name}' does not exist in 'chat_history' table"
                print(message)
                return {
                    "success": True,
                    "message": message,
                    "column_existed": False,
                    "verification_passed": True,
                    "error": None
                }

            # Remove the column
            alter_query = text(f"""
                ALTER TABLE chat_history
                DROP COLUMN {column_name}
            """)

            connection.execute(alter_query)
            connection.commit()

            # Verify column was removed
            verify_result = connection.execute(check_column_query, {"column_name": column_name})
            column_still_exists = verify_result.scalar() > 0

            if not column_still_exists:
                message = f"Successfully removed '{column_name}' column from 'chat_history' table"
                print(message)
                return {
                    "success": True,
                    "message": message,
                    "column_existed": True,
                    "verification_passed": True,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "message": "Rollback completed but verification failed",
                    "column_existed": True,
                    "verification_passed": False,
                    "error": "Column still exists after rollback"
                }

    except Exception as e:
        error_msg = f"Error removing {column_name} column: {e}"
        print(error_msg)
        return {
            "success": False,
            "message": "Rollback failed",
            "column_existed": False,
            "verification_passed": False,
            "error": error_msg
        }

def list_columns():
    """
    List all columns in chat_history table.
    
    Returns: dict with column information
    """
    try:
        with engine.connect() as connection:
            query = text("""
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'chat_history'
                ORDER BY ORDINAL_POSITION
            """)
            
            result = connection.execute(query)
            columns = []
            
            for row in result:
                columns.append({
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2],
                    "default": row[3],
                    "comment": row[4]
                })
            
            return {
                "success": True,
                "columns": columns,
                "total_count": len(columns),
                "error": None
            }
            
    except Exception as e:
        return {
            "success": False,
            "columns": [],
            "total_count": 0,
            "error": str(e)
        }

# Convenience functions for specific columns
def remove_json_export_column():
    """Drop the legacy json_export column (the STEP->JSON pipeline was removed)."""
    return remove_column("json_export")

def add_technical_drawing_export_column():
    """Add technical_drawing_export column specifically."""
    return add_column(
        column_name="technical_drawing_export",
        column_type="VARCHAR(255)",
        nullable=True,
        comment="Path to technical drawing export file"
    )

def remove_technical_drawing_export_column():
    """Remove technical_drawing_export column specifically."""
    return remove_column("technical_drawing_export")

if __name__ == "__main__":
    # Interactive mode for testing
    print("Generic Column Migration Utility")
    print("Available operations:")
    print("1. Add column")
    print("2. Remove column") 
    print("3. List columns")
    
    choice = input("Enter choice (1-3): ").strip()
    
    if choice == "1":
        column_name = input("Enter column name: ").strip()
        column_type = input("Enter column type (default: VARCHAR(255)): ").strip() or "VARCHAR(255)"
        nullable = input("Nullable? (y/N): ").strip().lower() == 'y'
        comment = input("Enter comment (optional): ").strip() or None
        
        result = add_column(column_name, column_type, nullable, comment)
        print(f"Result: {result}")
        
    elif choice == "2":
        column_name = input("Enter column name to remove: ").strip()
        confirm = input(f"Are you sure you want to remove '{column_name}'? (y/N): ").strip().lower()
        
        if confirm == 'y':
            result = remove_column(column_name)
            print(f"Result: {result}")
        else:
            print("Operation cancelled.")
            
    elif choice == "3":
        result = list_columns()
        if result["success"]:
            print(f"\nFound {result['total_count']} columns:")
            for col in result["columns"]:
                print(f"  - {col['name']} ({col['type']}) - Nullable: {col['nullable']}")
        else:
            print(f"Error: {result['error']}")
    else:
        print("Invalid choice.")
