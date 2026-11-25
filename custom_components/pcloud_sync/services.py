"""Services of pCloud Sync (Unofficial)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    ATTR_CAMERA_NAME,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DELETE_AFTER,
    ATTR_EVENT_ID,
    ATTR_FILENAME,
    ATTR_MEDIA,
    ATTR_PATH,
    ATTR_RECURSIVE,
    ATTR_RETRIES,
    ATTR_SUBFOLDER,
    CONF_FRIGATE_URL,
    DOMAIN,
    MEDIA_BOTH,
    MEDIA_CLIP,
    MEDIA_SNAPSHOT,
    SERVICE_GET_FILE_LINK,
    SERVICE_LIST_FOLDER,
    SERVICE_SYNC_NOW,
    SERVICE_UPLOAD_FILE,
    SERVICE_UPLOAD_FRIGATE_EVENT,
    SERVICE_UPLOAD_SNAPSHOT,
)

if TYPE_CHECKING:
    from .manager import SyncManager

_BASE = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
_SUBFOLDER = {vol.Optional(ATTR_SUBFOLDER): cv.string}

SYNC_NOW_SCHEMA = vol.Schema(_BASE)
UPLOAD_FILE_SCHEMA = vol.Schema(
    {
        **_BASE,
        **_SUBFOLDER,
        vol.Required(ATTR_PATH): cv.string,
        vol.Optional(ATTR_DELETE_AFTER, default=False): cv.boolean,
    }
)
UPLOAD_SNAPSHOT_SCHEMA = vol.Schema(
    {
        **_BASE,
        **_SUBFOLDER,
        vol.Required("entity_id"): cv.entity_ids,
        vol.Optional(ATTR_FILENAME): cv.string,
    }
)
UPLOAD_FRIGATE_SCHEMA = vol.Schema(
    {
        **_BASE,
        **_SUBFOLDER,
        vol.Required(ATTR_EVENT_ID): cv.string,
        vol.Optional(ATTR_MEDIA, default=MEDIA_BOTH): vol.In(
            [MEDIA_CLIP, MEDIA_SNAPSHOT, MEDIA_BOTH]
        ),
        vol.Optional(ATTR_CAMERA_NAME): cv.string,
        vol.Optional(CONF_FRIGATE_URL): cv.url,
        vol.Optional(ATTR_RETRIES, default=3): vol.All(vol.Coerce(int), vol.Range(0, 10)),
    }
)

LIST_FOLDER_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(ATTR_PATH, default=""): cv.string,
        vol.Optional(ATTR_RECURSIVE, default=False): cv.boolean,
    }
)
FILE_LINK_SCHEMA = vol.Schema({**_BASE, vol.Required(ATTR_PATH): cv.string})


def _manager(hass: HomeAssistant, call: ServiceCall) -> SyncManager:
    """Find the manager of the requested (or the only) loaded config entry."""
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="multiple_entries")
    return entries[0].runtime_data.manager


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register all services."""

    async def sync_now(call: ServiceCall) -> ServiceResponse:
        return await _manager(hass, call).async_sync(reason="service")

    async def upload_file(call: ServiceCall) -> ServiceResponse:
        return await _manager(hass, call).async_upload_path(
            call.data[ATTR_PATH],
            call.data.get(ATTR_SUBFOLDER),
            call.data[ATTR_DELETE_AFTER],
        )

    async def upload_snapshot(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass, call)
        results: dict[str, Any] = {}
        for entity_id in call.data["entity_id"]:
            results[entity_id] = await manager.async_upload_camera_snapshot(
                entity_id, call.data.get(ATTR_SUBFOLDER), call.data.get(ATTR_FILENAME)
            )
        return results

    async def upload_frigate(call: ServiceCall) -> ServiceResponse:
        return await _manager(hass, call).async_upload_frigate_event(
            call.data[ATTR_EVENT_ID],
            call.data[ATTR_MEDIA],
            call.data.get(ATTR_CAMERA_NAME),
            call.data.get(ATTR_SUBFOLDER),
            call.data.get(CONF_FRIGATE_URL),
            call.data[ATTR_RETRIES],
        )

    async def list_folder(call: ServiceCall) -> ServiceResponse:
        return await _manager(hass, call).async_list_folder(
            call.data[ATTR_PATH], call.data[ATTR_RECURSIVE]
        )

    async def get_file_link(call: ServiceCall) -> ServiceResponse:
        return await _manager(hass, call).async_file_link(call.data[ATTR_PATH])

    # Reading back is limited to the target folder and to administrators
    # (internal calls from other integrations, e.g. Alcedo NVR, have no user).
    for name, handler, schema in (
        (SERVICE_LIST_FOLDER, list_folder, LIST_FOLDER_SCHEMA),
        (SERVICE_GET_FILE_LINK, get_file_link, FILE_LINK_SCHEMA),
    ):
        async_register_admin_service(
            hass, DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.ONLY
        )

    for name, handler, schema in (
        (SERVICE_SYNC_NOW, sync_now, SYNC_NOW_SCHEMA),
        (SERVICE_UPLOAD_FILE, upload_file, UPLOAD_FILE_SCHEMA),
        (SERVICE_UPLOAD_SNAPSHOT, upload_snapshot, UPLOAD_SNAPSHOT_SCHEMA),
        (SERVICE_UPLOAD_FRIGATE_EVENT, upload_frigate, UPLOAD_FRIGATE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
