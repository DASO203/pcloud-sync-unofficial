"""Typed access to the integration options."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_CACHE_PATH,
    CONF_CONFLICT_MODE,
    CONF_DATE_SUBFOLDERS,
    CONF_DELETE_AFTER_UPLOAD,
    CONF_DRY_RUN,
    CONF_EXCLUDE_PATTERNS,
    CONF_FRIGATE_URL,
    CONF_INCLUDE_PATTERNS,
    CONF_MAX_FILES_PER_RUN,
    CONF_MAX_PARALLEL_UPLOADS,
    CONF_MIN_FILE_AGE,
    CONF_REMOTE_FOLDER,
    CONF_SCAN_INTERVAL,
    CONF_UPLOAD_TIMEOUT,
    CONF_USE_LOCAL_CACHE,
    CONF_WATCH_FOLDERS,
    CONFLICT_RENAME,
    DEFAULT_CACHE_DIRNAME,
    DEFAULT_CONFLICT_MODE,
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_INCLUDE_PATTERNS,
    DEFAULT_MAX_FILES_PER_RUN,
    DEFAULT_MAX_PARALLEL_UPLOADS,
    DEFAULT_MIN_FILE_AGE,
    DEFAULT_REMOTE_FOLDER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPLOAD_TIMEOUT,
)
from .helpers import WatchFolder, parse_watch_folders, split_patterns


def default_options(hass: HomeAssistant) -> dict[str, Any]:
    """Return the default options. Nothing is synced until a folder is added."""
    return {
        CONF_REMOTE_FOLDER: DEFAULT_REMOTE_FOLDER,
        CONF_WATCH_FOLDERS: "",
        CONF_INCLUDE_PATTERNS: DEFAULT_INCLUDE_PATTERNS,
        CONF_EXCLUDE_PATTERNS: DEFAULT_EXCLUDE_PATTERNS,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_MIN_FILE_AGE: DEFAULT_MIN_FILE_AGE,
        CONF_DELETE_AFTER_UPLOAD: False,
        CONF_DATE_SUBFOLDERS: True,
        CONF_CONFLICT_MODE: DEFAULT_CONFLICT_MODE,
        CONF_DRY_RUN: False,
        CONF_USE_LOCAL_CACHE: False,
        CONF_CACHE_PATH: hass.config.path(DEFAULT_CACHE_DIRNAME),
        CONF_FRIGATE_URL: "",
        CONF_MAX_FILES_PER_RUN: DEFAULT_MAX_FILES_PER_RUN,
        CONF_MAX_PARALLEL_UPLOADS: DEFAULT_MAX_PARALLEL_UPLOADS,
        CONF_UPLOAD_TIMEOUT: DEFAULT_UPLOAD_TIMEOUT,
    }


@dataclass(frozen=True, slots=True)
class SyncSettings:
    """Parsed, validated options."""

    remote_folder: str
    watch_folders: tuple[WatchFolder, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    scan_interval_min: int
    min_file_age: int
    delete_after_upload: bool
    date_subfolders: bool
    rename_if_exists: bool
    dry_run: bool
    use_local_cache: bool
    cache_path: str
    frigate_url: str
    max_files_per_run: int
    max_parallel_uploads: int
    upload_timeout_s: int

    @classmethod
    def from_options(cls, hass: HomeAssistant, options: Mapping[str, Any]) -> SyncSettings:
        """Build settings from config entry options (missing keys -> defaults)."""
        o = {**default_options(hass), **options}
        return cls(
            remote_folder=o[CONF_REMOTE_FOLDER],
            watch_folders=tuple(parse_watch_folders(o[CONF_WATCH_FOLDERS])),
            include=tuple(split_patterns(o[CONF_INCLUDE_PATTERNS])),
            exclude=tuple(split_patterns(o[CONF_EXCLUDE_PATTERNS])),
            scan_interval_min=int(o[CONF_SCAN_INTERVAL]),
            min_file_age=int(o[CONF_MIN_FILE_AGE]),
            delete_after_upload=bool(o[CONF_DELETE_AFTER_UPLOAD]),
            date_subfolders=bool(o[CONF_DATE_SUBFOLDERS]),
            rename_if_exists=o[CONF_CONFLICT_MODE] == CONFLICT_RENAME,
            dry_run=bool(o[CONF_DRY_RUN]),
            use_local_cache=bool(o[CONF_USE_LOCAL_CACHE]),
            cache_path=str(o[CONF_CACHE_PATH]),
            frigate_url=str(o[CONF_FRIGATE_URL] or "").rstrip("/"),
            max_files_per_run=int(o[CONF_MAX_FILES_PER_RUN]),
            max_parallel_uploads=max(1, min(4, int(o[CONF_MAX_PARALLEL_UPLOADS]))),
            upload_timeout_s=int(o[CONF_UPLOAD_TIMEOUT]) * 60,
        )
