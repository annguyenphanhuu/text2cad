"""
API routes for CAD-related operations.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import logging
import mimetypes
from pathlib import Path

# Set up logging
logger = logging.getLogger("tolery-api-cad")

# Create a separate router for file downloads that will be mounted at the root level
download_router = APIRouter(
    tags=["downloads"],
    responses={404: {"description": "File not found"}},
)

@download_router.get("/download/{file_path:path}", summary="Download File")
def download_file(file_path: str):
    """
    Download a file by its path.

    Args:
        file_path (str): Path to the file relative to the project root

    Returns:
        FileResponse: The requested file for download

    Raises:
        HTTPException: If the file is not found
    """
    try:
        # Log the requested file path for debugging
        logger.info(f"Download requested for file: {file_path}")

        # Print current working directory for debugging
        project_root = Path.cwd()

        # Ensure outputs directory exists
        outputs_dir = project_root / "outputs"
        outputs_dir.mkdir(exist_ok=True)

        # Construct the full file path - ensure it's relative to the project root
        # Remove any leading slashes to ensure it's treated as a relative path
        file_path = file_path.lstrip('\\/')

        # If the path contains the project root directory, extract only the part after it
        project_root_str = str(project_root).replace('\\', '/')
        file_path_str = str(file_path).replace('\\', '/')

        if project_root_str in file_path_str:
            # Extract the part after the project root
            relative_path = file_path_str.split(project_root_str, 1)[1].lstrip('\\/')
            logger.info(f"Extracted relative path: {relative_path}")
            file_path = relative_path

        full_path = project_root / file_path
        logger.info(f"Looking for file at: {full_path}")

        # Check if the file exists
        if not full_path.exists():
            logger.error(f"File not found: {full_path}")
            # Check if the directory exists
            if not full_path.parent.exists():
                logger.error(f"Directory does not exist: {full_path.parent}")
                # Try to create the directory structure
                full_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created directory: {full_path.parent}")

            # Try to list files in parent directory if it exists
            if full_path.parent.exists():
                logger.info(f"Files in directory {full_path.parent}:")
                for f in full_path.parent.iterdir():
                    logger.info(f"  - {f.name}")



            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        # Get the filename for the download
        filename = full_path.name

        # Determine the media type
        media_type, _ = mimetypes.guess_type(str(full_path))
        if media_type is None:
            # Default media types based on extension
            extension = full_path.suffix.lower()
            if extension == '.obj':
                media_type = 'model/obj'
            elif extension == '.step':
                media_type = 'application/step'
            elif extension == '.dxf':
                media_type = 'application/dxf'
            elif extension == '.pdf':
                media_type = 'application/pdf'
            elif extension == '.svg':
                media_type = 'image/svg+xml'
            else:
                media_type = 'application/octet-stream'

        logger.info(f"Serving file for download: {full_path} (media type: {media_type})")

        # Return the file as a download with appropriate headers
        return FileResponse(
            path=str(full_path),
            filename=filename,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error serving file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving file: {str(e)}")


