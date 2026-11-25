"""Minimal, async pCloud API client used by this integration.

Only the API methods that are actually needed are implemented:

* ``userinfo``                - account / quota info, connection test
* ``createfolderifnotexists`` - build the remote folder tree
* ``uploadfile``              - upload a file (multipart, streamed from disk)
* ``checksumfile``            - verify an upload before deleting local data
* ``listfolder``              - list uploaded files (read back, e.g. Alcedo NVR timeline)
* ``getfilelink``             - temporary download link of an uploaded file

Authentication uses an OAuth2 access token sent as ``Authorization: Bearer``
header. The token is never put into URLs or log messages.

API reference: https://docs.pcloud.com/
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .const import AUTH_ERROR_CODES
from .helpers import split_remote_path

_LOGGER = logging.getLogger(__name__)

STANDARD_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)
RETRY_DELAYS: tuple[float, ...] = (2.0, 10.0)


class PCloudError(Exception):
    """Generic pCloud error."""


class PCloudAuthError(PCloudError):
    """pCloud rejected the access token - the user has to re-authenticate."""


class PCloudConnectionError(PCloudError):
    """Network problem / timeout talking to pCloud."""


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Result of a successful upload."""

    file_id: int
    name: str
    size: int
    sha1: str | None
    remote_path: str


def _raise_for_result(result: Any, what: str) -> dict[str, Any]:
    """Validate a pCloud JSON response and return it."""
    if not isinstance(result, dict):
        raise PCloudError(f"{what}: unexpected response from pCloud")
    code = result.get("result")
    if code == 0:
        return result
    message = result.get("error", "unknown error")
    if code in AUTH_ERROR_CODES:
        raise PCloudAuthError(f"{what}: authentication failed ({code}: {message})")
    raise PCloudError(f"{what}: pCloud error {code}: {message}")


class PCloudApi:
    """Async pCloud API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        token_getter: Callable[[], Awaitable[str]],
    ) -> None:
        """Initialize the client.

        ``token_getter`` returns the current access token. It is called for
        every request so a re-authenticated token is picked up immediately.
        """
        self._session = session
        self._host = host.rstrip("/")
        self._token_getter = token_getter
        self._folder_cache: dict[str, int] = {"/": 0}
        self._folder_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Return the API host in use."""
        return self._host

    async def _headers(self) -> dict[str, str]:
        token = await self._token_getter()
        return {"Authorization": f"Bearer {token}"}

    async def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Call a (small) pCloud API method with GET and retry transient errors."""
        url = f"{self._host}/{method}"
        delays = RETRY_DELAYS if retry else ()
        attempt = 0
        while True:
            try:
                async with self._session.get(
                    url,
                    params=params or {},
                    headers=await self._headers(),
                    timeout=STANDARD_TIMEOUT,
                ) as response:
                    result = await response.json(content_type=None)
                return _raise_for_result(result, method)
            except (TimeoutError, aiohttp.ClientError) as err:
                if attempt >= len(delays):
                    raise PCloudConnectionError(f"{method}: {err or type(err).__name__}") from err
                _LOGGER.debug("pCloud %s failed (%s), retrying", method, err)
                await asyncio.sleep(delays[attempt])
                attempt += 1

    # ------------------------------------------------------------------ account

    async def async_userinfo(self, *, retry: bool = True) -> dict[str, Any]:
        """Return account info (email, userid, quota, usedquota)."""
        return await self._call("userinfo", retry=retry)

    # ------------------------------------------------------------------ folders

    def clear_folder_cache(self) -> None:
        """Forget cached folder ids (e.g. after a folder was deleted remotely)."""
        self._folder_cache = {"/": 0}

    async def async_ensure_folder(self, path: str) -> int:
        """Return the folder id of *path*, creating missing folders."""
        parts = split_remote_path(path)
        key = "/" + "/".join(parts)
        if key in self._folder_cache:
            return self._folder_cache[key]
        async with self._folder_lock:
            folder_id = 0
            current = ""
            for part in parts:
                current = f"{current}/{part}"
                cached = self._folder_cache.get(current)
                if cached is not None:
                    folder_id = cached
                    continue
                result = await self._call(
                    "createfolderifnotexists", {"folderid": folder_id, "name": part}
                )
                folder_id = int(result["metadata"]["folderid"])
                self._folder_cache[current] = folder_id
            return folder_id

    # ------------------------------------------------------------------ upload

    async def async_upload(
        self,
        remote_folder: str,
        filename: str,
        *,
        data: bytes | None = None,
        path: str | Path | None = None,
        rename_if_exists: bool = True,
        timeout_seconds: int = 3600,
    ) -> UploadResult:
        """Upload *data* or the file at *path* to *remote_folder*.

        The file is streamed from disk (no full copy in memory).
        ``nopartial=1`` makes pCloud discard incomplete uploads, so a failed
        transfer never leaves a broken file behind.
        """
        if (data is None) == (path is None):
            raise ValueError("Pass exactly one of data or path")

        folder_id = await self.async_ensure_folder(remote_folder)
        url = f"{self._host}/uploadfile"
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=30, sock_read=300)
        query = {"folderid": str(folder_id), "nopartial": "1"}
        if rename_if_exists:
            query["renameifexists"] = "1"

        attempt = 0
        while True:
            handle = None
            try:
                form = aiohttp.FormData()
                if path is not None:
                    handle = await asyncio.to_thread(open, path, "rb")
                    payload: Any = handle
                else:
                    payload = data
                form.add_field(
                    "file", payload, filename=filename, content_type="application/octet-stream"
                )
                async with self._session.post(
                    url,
                    params=query,
                    data=form,
                    headers=await self._headers(),
                    timeout=timeout,
                ) as response:
                    result = await response.json(content_type=None)
                break
            except (TimeoutError, aiohttp.ClientError) as err:
                if attempt >= len(RETRY_DELAYS):
                    raise PCloudConnectionError(
                        f"upload of {filename} failed: {err or type(err).__name__}"
                    ) from err
                _LOGGER.warning("Upload of %s failed (%s), retrying", filename, err)
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
            finally:
                if handle is not None:
                    await asyncio.to_thread(handle.close)

        result = _raise_for_result(result, f"upload of {filename}")
        metadata_list = result.get("metadata") or []
        if not metadata_list:
            raise PCloudError(f"upload of {filename}: pCloud returned no file metadata")
        metadata = metadata_list[0]
        checksums = result.get("checksums") or [{}]
        sha1 = (checksums[0] or {}).get("sha1")
        name = str(metadata.get("name", filename))
        remote_path = "/" + "/".join([*split_remote_path(remote_folder), name])
        return UploadResult(
            file_id=int(metadata["fileid"]),
            name=name,
            size=int(metadata.get("size", -1)),
            sha1=sha1,
            remote_path=remote_path,
        )

    async def async_checksum(self, file_id: int) -> str | None:
        """Return the SHA-1 checksum pCloud calculated for a file."""
        result = await self._call("checksumfile", {"fileid": file_id})
        return result.get("sha1")

    # ------------------------------------------------------------------ read back

    async def async_list_folder(self, path: str, *, recursive: bool = False) -> dict[str, Any]:
        """Return the pCloud metadata tree of *path* (``contents`` per folder)."""
        result = await self._call("listfolder", {"path": path or "/", "recursive": int(recursive)})
        return result.get("metadata") or {}

    async def async_file_link(self, path: str) -> tuple[str, str | None]:
        """Return a temporary download URL of the file at *path* and its expiry."""
        result = await self._call("getfilelink", {"path": path, "forcedownload": 0})
        hosts = result.get("hosts") or []
        if not hosts or not result.get("path"):
            raise PCloudError("getfilelink: pCloud returned no download host")
        return f"https://{hosts[0]}{result['path']}", result.get("expires")
