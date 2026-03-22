"""
Google Drive sync — uploads Parquet files from the local data_lake_output
directory to a Google Drive folder using PyDrive2.

Setup:
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Create an OAuth 2.0 Client ID (Desktop app)
  3. Download the JSON and save as credentials.json in the project root
  4. On first run, a browser window opens for auth; token is cached in token.json

Usage:
    python -m backend.data_lake.gdrive_sync
    python -m backend.data_lake.gdrive_sync --dry-run
"""
import os
import logging
import argparse
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_drive_service():
    """Authenticate and return a PyDrive2 GoogleDrive instance."""
    try:
        from pydrive2.auth import GoogleAuth
        from pydrive2.drive import GoogleDrive
    except ImportError:
        logger.error("PyDrive2 not installed. Run: pip install PyDrive2")
        raise

    from .config import GDRIVE_CREDENTIALS_FILE

    settings_yaml = os.path.join(os.path.dirname(GDRIVE_CREDENTIALS_FILE), "pydrive_settings.yaml")
    if not os.path.exists(settings_yaml):
        _write_pydrive_settings(settings_yaml, GDRIVE_CREDENTIALS_FILE)

    gauth = GoogleAuth(settings_file=settings_yaml)
    gauth.LocalWebserverAuth()
    return GoogleDrive(gauth)


def _write_pydrive_settings(path: str, creds_file: str) -> None:
    """Generate a pydrive_settings.yaml for OAuth."""
    content = f"""client_config_backend: file
client_config_file: {creds_file}
save_credentials: True
save_credentials_backend: file
save_credentials_file: token.json
get_refresh_token: True
oauth_scope:
  - https://www.googleapis.com/auth/drive.file
"""
    with open(path, "w") as f:
        f.write(content)
    logger.info("Created PyDrive settings at %s", path)


def _find_or_create_folder(drive, folder_name: str, parent_id: str = "root") -> str:
    """Find a folder by name in Drive, or create it."""
    query = (
        f"title='{folder_name}' and "
        f"'{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = drive.ListFile({"q": query}).GetList()
    if results:
        return results[0]["id"]

    folder = drive.CreateFile({
        "title": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [{"id": parent_id}],
    })
    folder.Upload()
    logger.info("Created Drive folder: %s", folder_name)
    return folder["id"]


def upload_directory(local_dir: str, drive_folder_name: str | None = None,
                     dry_run: bool = False) -> dict:
    """
    Upload all files in local_dir to Google Drive, preserving subdirectory structure.
    Returns a summary dict.
    """
    from .config import GDRIVE_FOLDER_NAME

    folder_name = drive_folder_name or GDRIVE_FOLDER_NAME
    local_path = Path(local_dir)
    if not local_path.exists():
        logger.warning("Local directory does not exist: %s", local_dir)
        return {"uploaded": 0, "skipped": 0, "errors": []}

    files_to_upload = list(local_path.rglob("*.parquet")) + list(local_path.rglob("*.json"))
    logger.info("Found %d files to upload from %s", len(files_to_upload), local_dir)

    if dry_run:
        for f in files_to_upload[:5]:
            logger.info("[DRY RUN] Would upload: %s", f.relative_to(local_path))
        if len(files_to_upload) > 5:
            logger.info("[DRY RUN] ... and %d more files", len(files_to_upload) - 5)
        return {"uploaded": 0, "skipped": len(files_to_upload), "dry_run": True}

    drive = _get_drive_service()
    root_folder_id = _find_or_create_folder(drive, folder_name)

    folder_cache: dict[str, str] = {"": root_folder_id}
    uploaded = 0
    errors = []

    for file_path in files_to_upload:
        relative = file_path.relative_to(local_path)
        parent_parts = relative.parent.parts

        parent_id = root_folder_id
        accumulated = ""
        for part in parent_parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part
            if accumulated not in folder_cache:
                folder_cache[accumulated] = _find_or_create_folder(drive, part, parent_id)
            parent_id = folder_cache[accumulated]

        try:
            gfile = drive.CreateFile({
                "title": file_path.name,
                "parents": [{"id": parent_id}],
            })
            gfile.SetContentFile(str(file_path))
            gfile.Upload()
            uploaded += 1
            if uploaded % 50 == 0:
                logger.info("Uploaded %d / %d files", uploaded, len(files_to_upload))
        except Exception as e:
            errors.append(f"{relative}: {e}")
            logger.warning("Failed to upload %s: %s", relative, e)

    logger.info("Upload complete: %d uploaded, %d errors", uploaded, len(errors))
    return {"uploaded": uploaded, "errors": errors}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Upload data lake to Google Drive")
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    args = parser.parse_args()

    from .config import DATA_DIR
    upload_directory(DATA_DIR, dry_run=args.dry_run)
