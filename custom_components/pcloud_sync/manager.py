"""The sync manager: decides what is uploaded, uploads it and keeps a log.

Safety rules implemented here (see also SECURITY.md):

* Only folders that Home Assistant allows (``allowlist_external_dirs`` /
  ``media_dirs``) are read. Symlinks are never followed.
* A local file is only deleted if the user enabled it, the upload succeeded,
  pCloud's SHA-1 equals the local SHA-1, and the file did not change while it
  was uploaded. Anything else keeps the local file.
* In dry-run mode nothing is uploaded or deleted - only logged.
* Every action is logged, fired as an event and kept in an activity log.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import PCloudApi, PCloudAuthError, PCloudError, UploadResult
from .const import (
    ACTIVITY_LOG_SIZE,
    DOMAIN,
    EVENT_FAILED,
    EVENT_LOCAL_DELETED,
    EVENT_UPLOADED,
    MAX_IN_MEMORY_BYTES,
    MEDIA_BOTH,
    MEDIA_CLIP,
    MEDIA_SNAPSHOT,
    STATUS_AUTH_ERROR,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_SYNCING,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .helpers import (
    LocalFile,
    UnsafePathError,
    date_subfolder,
    human_size,
    is_within,
    join_remote,
    remove_empty_parents,
    sanitize_name,
    scan_folder,
    sha1_of_bytes,
    sha1_of_file,
    split_remote_path,
)
from .settings import SyncSettings

_LOGGER = logging.getLogger(__name__)

_FRIGATE_EVENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DOWNLOAD_CHUNK = 1024 * 1024
_PART_SUFFIX = ".part"


@dataclass(slots=True)
class SyncStats:
    """Counters that are shown as sensors and persisted."""

    uploaded_files: int = 0
    uploaded_bytes: int = 0
    deleted_local: int = 0
    failed: int = 0


class SyncManager:
    """Runs syncs and service uploads for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: PCloudApi,
        settings: SyncSettings,
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.settings = settings
        self.status = STATUS_IDLE
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.pending = 0
        self.stats = SyncStats()
        self.activity: deque[dict[str, Any]] = deque(maxlen=ACTIVITY_LOG_SIZE)
        self._files: dict[str, dict[str, Any]] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
        )
        self._run_lock = asyncio.Lock()
        self._upload_slots = asyncio.Semaphore(settings.max_parallel_uploads)
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_interval: CALLBACK_TYPE | None = None

    # ---------------------------------------------------------------- lifecycle

    async def async_load(self) -> None:
        """Restore persisted state."""
        data = await self._store.async_load() or {}
        self._files = data.get("files", {})
        stats = data.get("stats", {})
        self.stats = SyncStats(**{k: int(v) for k, v in stats.items() if hasattr(SyncStats, k)})
        if last := data.get("last_sync"):
            self.last_sync = dt_util.parse_datetime(last)

    @callback
    def async_start(self) -> None:
        """Start the periodic sync (if enabled and folders are configured)."""
        if self.settings.scan_interval_min <= 0:
            _LOGGER.info("Automatic sync disabled (interval 0); use the service or button")
            return
        if not self.settings.watch_folders and not self.settings.use_local_cache:
            return

        async def _tick(_now: datetime) -> None:
            await self.async_sync(reason="schedule")

        self._unsub_interval = async_track_time_interval(
            self.hass,
            _tick,
            timedelta(minutes=self.settings.scan_interval_min),
            name=f"{DOMAIN} periodic sync",
            cancel_on_shutdown=True,
        )

    async def async_stop(self) -> None:
        """Stop timers and flush state."""
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "files": self._files,
            "stats": {
                "uploaded_files": self.stats.uploaded_files,
                "uploaded_bytes": self.stats.uploaded_bytes,
                "deleted_local": self.stats.deleted_local,
                "failed": self.stats.failed,
            },
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 10)

    # ---------------------------------------------------------------- listeners

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    def _log(self, action: str, **details: Any) -> None:
        entry = {"time": dt_util.utcnow().isoformat(), "action": action, **details}
        self.activity.appendleft(entry)
        self._notify()

    # ---------------------------------------------------------------- helpers

    def _service_folder(self, subfolder: str | None) -> str:
        """Remote folder for service uploads (optionally with date folders)."""
        segments = [self.settings.remote_folder, subfolder or ""]
        if self.settings.date_subfolders:
            segments.append(date_subfolder(dt_util.now()))
        return join_remote(*segments)

    def _handle_auth_error(self, err: PCloudAuthError) -> None:
        self.status = STATUS_AUTH_ERROR
        self.last_error = str(err)
        _LOGGER.error("pCloud rejected the access token, re-authentication required")
        self.entry.async_start_reauth(self.hass)
        self._notify()

    def _record_failure(self, source: str, name: str, err: Exception) -> None:
        self.stats.failed += 1
        self.last_error = f"{name}: {err}"
        _LOGGER.warning("Upload of %s (%s) failed: %s", name, source, err)
        self.hass.bus.async_fire(
            EVENT_FAILED,
            {"entry_id": self.entry.entry_id, "source": source, "name": name, "error": str(err)},
        )
        self._log("failed", source=source, name=name, message=str(err))

    async def _verify(self, result: UploadResult, local_sha1: str, size: int) -> bool:
        """Return True if pCloud stored exactly the bytes we sent."""
        remote_sha1 = result.sha1
        if not remote_sha1:
            try:
                remote_sha1 = await self.api.async_checksum(result.file_id)
            except PCloudError as err:
                _LOGGER.warning("Could not verify %s: %s", result.remote_path, err)
                return False
        size_ok = result.size < 0 or result.size == size
        return bool(remote_sha1) and remote_sha1.lower() == local_sha1.lower() and size_ok

    def _after_upload(
        self,
        result: UploadResult,
        *,
        source: str,
        local: str | None,
        size: int,
        verified: bool,
    ) -> dict[str, Any]:
        self.stats.uploaded_files += 1
        self.stats.uploaded_bytes += size
        info = {
            "source": source,
            "local_path": local,
            "remote_path": result.remote_path,
            "file_id": result.file_id,
            "size": size,
            "verified": verified,
        }
        _LOGGER.info(
            "Uploaded %s (%s) -> pCloud:%s%s",
            local or source,
            human_size(size),
            result.remote_path,
            "" if verified else " [checksum NOT verified]",
        )
        self.hass.bus.async_fire(EVENT_UPLOADED, {"entry_id": self.entry.entry_id, **info})
        self._log("uploaded", **info)
        self._schedule_save()
        return info

    # ---------------------------------------------------------------- core upload

    async def _upload_local_file(
        self,
        file: LocalFile,
        remote_folder: str,
        *,
        base: str,
        delete_after: bool,
        source: str,
        state_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Upload one local file, verify it and optionally delete it."""
        name = PurePosixPath(file.relative).name
        try:
            filename = sanitize_name(name)
        except UnsafePathError as err:
            self._record_failure(source, name, err)
            return None

        if self.settings.dry_run:
            target = join_remote(remote_folder, filename)
            _LOGGER.info(
                "[dry run] would upload %s -> pCloud:%s%s",
                file.path,
                target,
                " and delete it locally" if delete_after else "",
            )
            self._log(
                "dry_run",
                source=source,
                local_path=file.path,
                remote_path=target,
                size=file.size,
                delete_after=delete_after,
            )
            return None

        async with self._upload_slots:
            try:
                local_sha1 = await self.hass.async_add_executor_job(sha1_of_file, file.path)
                result = await self.api.async_upload(
                    remote_folder,
                    filename,
                    path=file.path,
                    rename_if_exists=self.settings.rename_if_exists,
                    timeout_seconds=self.settings.upload_timeout_s,
                )
                verified = await self._verify(result, local_sha1, file.size)
            except PCloudAuthError:
                raise
            except (PCloudError, OSError) as err:
                self._record_failure(source, file.path, err)
                return None

        info = self._after_upload(
            result, source=source, local=file.path, size=file.size, verified=verified
        )
        if state_key is not None:
            self._files[state_key] = {
                "s": file.size,
                "m": file.mtime,
                "r": result.remote_path,
                "id": result.file_id,
                "t": dt_util.utcnow().isoformat(),
            }

        if delete_after:
            info["deleted_local"] = await self._delete_local(file, base, verified, source)
            if info["deleted_local"] and state_key is not None:
                self._files.pop(state_key, None)
        self._schedule_save()
        return info

    async def _delete_local(self, file: LocalFile, base: str, verified: bool, source: str) -> bool:
        """Delete a local file after a verified upload. Returns True if deleted."""
        if not verified:
            _LOGGER.warning("Keeping %s: upload could not be verified by checksum", file.path)
            self._log(
                "kept_local", source=source, local_path=file.path, message="checksum not verified"
            )
            return False

        def _remove() -> str | None:
            try:
                stat = os.stat(file.path, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.st_size != file.size or stat.st_mtime != file.mtime:
                return "file changed during upload"
            if not is_within(file.path, base):
                return "file is outside of its base folder"
            os.remove(file.path)
            remove_empty_parents(file.path, base)
            return None

        try:
            problem = await self.hass.async_add_executor_job(_remove)
        except OSError as err:
            problem = str(err)
        if problem:
            _LOGGER.warning("Keeping %s: %s", file.path, problem)
            self._log("kept_local", source=source, local_path=file.path, message=problem)
            return False

        self.stats.deleted_local += 1
        _LOGGER.info("Deleted local file %s after verified upload", file.path)
        self.hass.bus.async_fire(
            EVENT_LOCAL_DELETED,
            {"entry_id": self.entry.entry_id, "source": source, "local_path": file.path},
        )
        self._log("deleted_local", source=source, local_path=file.path)
        return True

    # ---------------------------------------------------------------- folder sync

    async def async_sync(self, reason: str = "manual") -> dict[str, Any]:
        """Scan all watch folders (and the cache) and upload new files."""
        if self._run_lock.locked():
            _LOGGER.debug("Sync already running, skipping (%s)", reason)
            return {"started": False, "reason": "already running"}

        async with self._run_lock:
            self.status = STATUS_SYNCING
            self.last_error = None
            self._notify()
            settings = self.settings
            now = time.time()
            jobs: list[tuple[LocalFile, str, str, bool, str, str | None]] = []

            try:
                for folder in settings.watch_folders:
                    allowed = await self.hass.async_add_executor_job(
                        self._folder_usable, folder.local
                    )
                    if not allowed:
                        self._record_failure(
                            "watch_folder",
                            folder.local,
                            HomeAssistantError("folder missing or not allowed"),
                        )
                        continue
                    files = await self.hass.async_add_executor_job(
                        lambda f=folder: list(
                            scan_folder(
                                f.local,
                                list(settings.include),
                                list(settings.exclude),
                                min_age=settings.min_file_age,
                                now=now,
                            )
                        )
                    )
                    prefix = f"{folder.local}|"
                    present = {prefix + f.relative for f in files}
                    # Forget files that no longer exist locally.
                    for key in [
                        k for k in self._files if k.startswith(prefix) and k not in present
                    ]:
                        self._files.pop(key, None)
                    for f in files:
                        key = prefix + f.relative
                        known = self._files.get(key)
                        if known and known.get("s") == f.size and known.get("m") == f.mtime:
                            continue
                        parent = str(PurePosixPath(f.relative).parent)
                        remote = join_remote(
                            settings.remote_folder, folder.remote, "" if parent == "." else parent
                        )
                        jobs.append(
                            (
                                f,
                                remote,
                                folder.local,
                                settings.delete_after_upload,
                                "watch_folder",
                                key,
                            )
                        )

                if settings.use_local_cache:
                    jobs.extend(await self._cache_jobs(now))

                jobs.sort(key=lambda j: j[0].mtime)
                if len(jobs) > settings.max_files_per_run:
                    _LOGGER.info(
                        "%d files pending, uploading %d this run",
                        len(jobs),
                        settings.max_files_per_run,
                    )
                self.pending = len(jobs)
                self._notify()

                async def _run(job: tuple[LocalFile, str, str, bool, str, str | None]) -> None:
                    file, remote, base, delete_after, source, key = job
                    await self._upload_local_file(
                        file,
                        remote,
                        base=base,
                        delete_after=delete_after,
                        source=source,
                        state_key=key,
                    )
                    self.pending = max(0, self.pending - 1)
                    self._notify()

                results = await asyncio.gather(
                    *(_run(j) for j in jobs[: settings.max_files_per_run]),
                    return_exceptions=True,
                )
                for res in results:
                    if isinstance(res, PCloudAuthError):
                        self._handle_auth_error(res)
                        return {"started": True, "error": "auth"}
                    if isinstance(res, Exception):
                        _LOGGER.exception("Unexpected sync error", exc_info=res)
                        self.last_error = str(res)
            finally:
                self.last_sync = dt_util.utcnow()
                if self.status != STATUS_AUTH_ERROR:
                    self.status = STATUS_ERROR if self.last_error else STATUS_IDLE
                self.pending = max(0, len(jobs) - settings.max_files_per_run)
                self._schedule_save()
                self._notify()

            return {
                "started": True,
                "files": min(len(jobs), settings.max_files_per_run),
                "remaining": self.pending,
            }

    def _folder_usable(self, path: str) -> bool:
        return os.path.isdir(path) and self.hass.config.is_allowed_path(path)

    async def _cache_jobs(
        self, now: float
    ) -> list[tuple[LocalFile, str, str, bool, str, str | None]]:
        """Files left in the cache (e.g. failed uploads) are retried each sync."""
        cache = self.settings.cache_path
        if not await self.hass.async_add_executor_job(os.path.isdir, cache):
            return []
        files = await self.hass.async_add_executor_job(
            lambda: list(scan_folder(cache, ["*"], [f"*{_PART_SUFFIX}"], min_age=5, now=now))
        )
        jobs = []
        for f in files:
            parent = str(PurePosixPath(f.relative).parent)
            remote = join_remote(self.settings.remote_folder, "" if parent == "." else parent)
            jobs.append((f, remote, cache, True, "cache", None))
        return jobs

    # ---------------------------------------------------------------- service uploads

    def _cache_target(self, remote_folder: str, filename: str) -> Path:
        """Local cache file that mirrors the remote location."""
        root = split_remote_path(self.settings.remote_folder)
        parts = split_remote_path(remote_folder)[len(root) :]
        return Path(self.settings.cache_path, *parts, filename)

    async def _store_in_cache_and_upload(
        self, target: Path, source: str, remote_folder: str
    ) -> dict[str, Any]:
        stat = await self.hass.async_add_executor_job(target.stat)
        relative = target.relative_to(self.settings.cache_path).as_posix()
        file = LocalFile(
            path=str(target), relative=relative, size=stat.st_size, mtime=stat.st_mtime
        )
        info = await self._upload_local_file(
            file, remote_folder, base=self.settings.cache_path, delete_after=True, source=source
        )
        if info is None:
            if self.settings.dry_run:
                return {"dry_run": True, "cached": str(target)}
            return {
                "queued": True,
                "cached": str(target),
                "message": "upload failed, file kept in cache and retried on next sync",
            }
        return info

    async def async_upload_bytes(
        self, data: bytes, filename: str, subfolder: str | None, source: str
    ) -> dict[str, Any]:
        """Upload in-memory data (camera snapshot, Frigate media, ...)."""
        filename = sanitize_name(filename)
        remote_folder = self._service_folder(subfolder)

        if self.settings.dry_run:
            target = join_remote(remote_folder, filename)
            _LOGGER.info(
                "[dry run] would upload %s (%s) -> pCloud:%s", source, human_size(len(data)), target
            )
            self._log("dry_run", source=source, remote_path=target, size=len(data))
            return {"dry_run": True, "remote_path": target}

        if self.settings.use_local_cache:
            target = self._cache_target(remote_folder, filename)

            def _write() -> Path:
                final = _unique_path(target)
                final.parent.mkdir(parents=True, exist_ok=True)
                part = final.with_name(final.name + _PART_SUFFIX)
                part.write_bytes(data)
                os.replace(part, final)
                return final

            final = await self.hass.async_add_executor_job(_write)
            return await self._guard_auth(
                self._store_in_cache_and_upload(final, source, remote_folder)
            )

        async def _do() -> dict[str, Any]:
            async with self._upload_slots:
                try:
                    result = await self.api.async_upload(
                        remote_folder,
                        filename,
                        data=data,
                        rename_if_exists=self.settings.rename_if_exists,
                        timeout_seconds=self.settings.upload_timeout_s,
                    )
                    verified = await self._verify(result, sha1_of_bytes(data), len(data))
                except PCloudAuthError:
                    raise
                except PCloudError as err:
                    self._record_failure(source, filename, err)
                    raise HomeAssistantError(f"Upload of {filename} failed: {err}") from err
            return self._after_upload(
                result, source=source, local=None, size=len(data), verified=verified
            )

        return await self._guard_auth(_do())

    async def _guard_auth(self, coro: Any) -> dict[str, Any]:
        try:
            return await coro
        except PCloudAuthError as err:
            self._handle_auth_error(err)
            raise HomeAssistantError(
                "pCloud authentication failed, please re-authenticate"
            ) from err

    async def async_upload_path(
        self, path: str, subfolder: str | None, delete_after: bool
    ) -> dict[str, Any]:
        """Upload a single local file (service ``upload_file``)."""

        def _check() -> LocalFile:
            resolved = Path(path).resolve()
            if not self.hass.config.is_allowed_path(str(resolved)):
                raise HomeAssistantError(
                    f"{path} is not in an allowed folder (allowlist_external_dirs)"
                )
            if Path(path).is_symlink() or not resolved.is_file():
                raise HomeAssistantError(f"{path} is not a regular file")
            stat = resolved.stat()
            return LocalFile(
                path=str(resolved), relative=resolved.name, size=stat.st_size, mtime=stat.st_mtime
            )

        file = await self.hass.async_add_executor_job(_check)
        remote_folder = self._service_folder(subfolder)
        info = await self._guard_auth(
            self._upload_local_file(
                file,
                remote_folder,
                base=str(Path(file.path).parent),
                delete_after=delete_after,
                source="upload_file",
            )
        )
        if info is None and not self.settings.dry_run:
            raise HomeAssistantError(f"Upload of {path} failed: {self.last_error}")
        return info or {"dry_run": True}

    async def async_upload_camera_snapshot(
        self, entity_id: str, subfolder: str | None, filename: str | None
    ) -> dict[str, Any]:
        """Take a snapshot of a camera entity and upload it."""
        from homeassistant.components.camera import async_get_image

        image = await async_get_image(self.hass, entity_id, timeout=20)
        ext = {"image/png": "png", "image/gif": "gif"}.get(image.content_type, "jpg")
        object_id = entity_id.split(".", 1)[-1]
        stamp = dt_util.now().strftime("%Y%m%d_%H%M%S")
        name = filename or f"{object_id}_{stamp}.{ext}"
        return await self.async_upload_bytes(
            image.content, name, subfolder or f"snapshots/{object_id}", source=entity_id
        )

    async def async_upload_frigate_event(
        self,
        event_id: str,
        media: str,
        camera_name: str | None,
        subfolder: str | None,
        frigate_url: str | None,
        retries: int,
    ) -> dict[str, Any]:
        """Download clip and/or snapshot of a Frigate event and upload it."""
        base = (frigate_url or self.settings.frigate_url or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise HomeAssistantError(
                "No Frigate URL configured (integration options or service field)"
            )
        if not _FRIGATE_EVENT_ID.match(event_id):
            raise HomeAssistantError(f"Invalid Frigate event id: {event_id!r}")

        kinds = [MEDIA_CLIP, MEDIA_SNAPSHOT] if media == MEDIA_BOTH else [media]
        folder = subfolder or f"frigate/{camera_name or 'events'}"
        prefix = f"{camera_name}_" if camera_name else ""
        results: dict[str, Any] = {}
        for kind in kinds:
            suffix = "clip.mp4" if kind == MEDIA_CLIP else "snapshot.jpg"
            url = f"{base}/api/events/{event_id}/{suffix}"
            filename = f"{prefix}{event_id}{'.mp4' if kind == MEDIA_CLIP else '.jpg'}"
            results[kind] = await self._download_and_upload(
                url, filename, folder, f"frigate:{event_id}:{kind}", retries
            )
        return results

    async def _download_and_upload(
        self, url: str, filename: str, subfolder: str, source: str, retries: int
    ) -> dict[str, Any]:
        """Download *url* (with retries) and upload the result."""
        session = async_get_clientsession(self.hass)
        timeout = aiohttp.ClientTimeout(total=self.settings.upload_timeout_s, connect=15)
        last_error = ""
        for attempt in range(retries + 1):
            if attempt:
                await asyncio.sleep(10)
            try:
                async with session.get(url, timeout=timeout) as response:
                    if response.status in (404, 500, 502, 503):
                        # Frigate creates clips after the event ended - try again.
                        last_error = f"HTTP {response.status}"
                        continue
                    response.raise_for_status()
                    if self.settings.use_local_cache and not self.settings.dry_run:
                        return await self._stream_to_cache(response, filename, subfolder, source)
                    data = await _read_limited(response, MAX_IN_MEMORY_BYTES)
            except (TimeoutError, aiohttp.ClientError) as err:
                last_error = str(err) or type(err).__name__
                continue
            return await self.async_upload_bytes(data, filename, subfolder, source)

        err = HomeAssistantError(f"Download of {url} failed: {last_error}")
        self._record_failure(source, filename, err)
        raise err

    async def _stream_to_cache(
        self, response: aiohttp.ClientResponse, filename: str, subfolder: str, source: str
    ) -> dict[str, Any]:
        """Write a download into the cache without holding it in memory."""
        remote_folder = self._service_folder(subfolder)
        target = await self.hass.async_add_executor_job(
            _unique_path, self._cache_target(remote_folder, sanitize_name(filename))
        )
        part = target.with_name(target.name + _PART_SUFFIX)
        await self.hass.async_add_executor_job(
            lambda: part.parent.mkdir(parents=True, exist_ok=True)
        )
        handle = await self.hass.async_add_executor_job(open, part, "wb")
        try:
            async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK):
                await self.hass.async_add_executor_job(handle.write, chunk)
        except BaseException:
            await self.hass.async_add_executor_job(handle.close)
            await self.hass.async_add_executor_job(lambda: part.unlink(missing_ok=True))
            raise
        await self.hass.async_add_executor_job(handle.close)
        await self.hass.async_add_executor_job(os.replace, part, target)
        return await self._guard_auth(
            self._store_in_cache_and_upload(target, source, remote_folder)
        )

    # ---------------------------------------------------------------- diagnostics

    def diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive runtime information."""
        return {
            "status": self.status,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_error": self.last_error,
            "pending": self.pending,
            "stats": self._data_to_save()["stats"],
            "tracked_files": len(self._files),
            "activity": list(self.activity),
        }


def _unique_path(path: Path) -> Path:
    """Return *path* or ``name (n).ext`` if it already exists (blocking)."""
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise HomeAssistantError(f"Too many files named {path.name} in cache")


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    """Read a response body but refuse anything larger than *limit* bytes."""
    if response.content_length and response.content_length > limit:
        raise HomeAssistantError(
            f"Download is {human_size(response.content_length)}, more than the "
            f"{human_size(limit)} allowed in memory - enable the local cache"
        )
    buffer = bytearray()
    async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise HomeAssistantError(
                f"Download exceeds {human_size(limit)} - enable the local cache"
            )
    return bytes(buffer)
