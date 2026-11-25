"""Constants for the pCloud Sync (Unofficial) integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pcloud_sync"

# --- pCloud endpoints -------------------------------------------------------
# pCloud runs two data regions. An account lives in exactly one of them and
# every API call has to go to the matching host.
API_HOST_US: Final = "https://api.pcloud.com"
API_HOST_EU: Final = "https://eapi.pcloud.com"
API_HOSTS: Final = {"us": API_HOST_US, "eu": API_HOST_EU}
# pCloud's "locationid" (returned by OAuth) mapped to the region name.
LOCATION_IDS: Final = {1: "us", 2: "eu"}

OAUTH2_AUTHORIZE: Final = "https://my.pcloud.com/oauth2/authorize"
OAUTH2_TOKEN_PATH: Final = "/oauth2_token"  # noqa: S105 - URL path, not a secret

# pCloud access tokens never expire and pCloud does not issue refresh tokens.
# Home Assistant's OAuth helper needs an expiry, so a very long one is used.
TOKEN_LIFETIME_SECONDS: Final = 10 * 365 * 24 * 3600

# pCloud result codes that mean "your credentials are not valid (any more)".
AUTH_ERROR_CODES: Final = frozenset({1000, 2000, 2094})

# --- Config entry data / options --------------------------------------------
CONF_REGION: Final = "region"
CONF_REMOTE_FOLDER: Final = "remote_folder"
CONF_WATCH_FOLDERS: Final = "watch_folders"
CONF_INCLUDE_PATTERNS: Final = "include_patterns"
CONF_EXCLUDE_PATTERNS: Final = "exclude_patterns"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MIN_FILE_AGE: Final = "min_file_age"
CONF_DELETE_AFTER_UPLOAD: Final = "delete_after_upload"
CONF_DATE_SUBFOLDERS: Final = "date_subfolders"
CONF_CONFLICT_MODE: Final = "conflict_mode"
CONF_USE_LOCAL_CACHE: Final = "use_local_cache"
CONF_CACHE_PATH: Final = "cache_path"
CONF_FRIGATE_URL: Final = "frigate_url"
CONF_MAX_FILES_PER_RUN: Final = "max_files_per_run"
CONF_MAX_PARALLEL_UPLOADS: Final = "max_parallel_uploads"
CONF_UPLOAD_TIMEOUT: Final = "upload_timeout"
CONF_DRY_RUN: Final = "dry_run"

CONFLICT_RENAME: Final = "rename"
CONFLICT_OVERWRITE: Final = "overwrite"

DEFAULT_REMOTE_FOLDER: Final = "/HomeAssistant/Sync"
DEFAULT_INCLUDE_PATTERNS: Final = "*"
DEFAULT_EXCLUDE_PATTERNS: Final = "*.tmp, *.part, .*"
DEFAULT_SCAN_INTERVAL: Final = 15  # minutes, 0 = only manual
DEFAULT_MIN_FILE_AGE: Final = 60  # seconds
DEFAULT_CONFLICT_MODE: Final = CONFLICT_RENAME
DEFAULT_CACHE_DIRNAME: Final = "pcloud_sync_cache"
DEFAULT_MAX_FILES_PER_RUN: Final = 200
DEFAULT_MAX_PARALLEL_UPLOADS: Final = 2
DEFAULT_UPLOAD_TIMEOUT: Final = 60  # minutes

# Upper bound for data kept in memory when the local cache is disabled.
MAX_IN_MEMORY_BYTES: Final = 200 * 1024 * 1024

# How many recent actions are kept for the activity log (diagnostics / sensor).
ACTIVITY_LOG_SIZE: Final = 50
ACTIVITY_ATTRIBUTE_SIZE: Final = 10

# Storage
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.state"

# Quota polling
QUOTA_UPDATE_MINUTES: Final = 30

# --- Services ---------------------------------------------------------------
SERVICE_SYNC_NOW: Final = "sync_now"
SERVICE_UPLOAD_FILE: Final = "upload_file"
SERVICE_UPLOAD_SNAPSHOT: Final = "upload_camera_snapshot"
SERVICE_UPLOAD_FRIGATE_EVENT: Final = "upload_frigate_event"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_PATH: Final = "path"
ATTR_SUBFOLDER: Final = "subfolder"
ATTR_DELETE_AFTER: Final = "delete_after_upload"
ATTR_FILENAME: Final = "filename"
ATTR_EVENT_ID: Final = "event_id"
ATTR_MEDIA: Final = "media"
ATTR_CAMERA_NAME: Final = "camera_name"
ATTR_RETRIES: Final = "retries"

MEDIA_CLIP: Final = "clip"
MEDIA_SNAPSHOT: Final = "snapshot"
MEDIA_BOTH: Final = "both"

# --- Events fired on the HA event bus (for automations & transparency) ------
EVENT_UPLOADED: Final = f"{DOMAIN}_file_uploaded"
EVENT_FAILED: Final = f"{DOMAIN}_upload_failed"
EVENT_LOCAL_DELETED: Final = f"{DOMAIN}_local_file_deleted"

# --- Manager status ---------------------------------------------------------
STATUS_IDLE: Final = "idle"
STATUS_SYNCING: Final = "syncing"
STATUS_ERROR: Final = "error"
STATUS_AUTH_ERROR: Final = "auth_error"
