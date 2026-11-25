"""pCloud Sync (Unofficial) - upload Home Assistant data to pCloud.

Not affiliated with or endorsed by pCloud AG.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PCloudApi, PCloudAuthError, PCloudError
from .const import API_HOST_US, API_HOSTS, CONF_REGION, DOMAIN, QUOTA_UPDATE_MINUTES
from .manager import SyncManager
from .services import async_setup_services
from .settings import SyncSettings

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class PCloudSyncData:
    """Runtime data of a config entry."""

    api: PCloudApi
    manager: SyncManager
    quota: DataUpdateCoordinator[dict[str, Any]]


type PCloudSyncConfigEntry = ConfigEntry[PCloudSyncData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services (once, independent of config entries)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PCloudSyncConfigEntry) -> bool:
    """Set up pCloud Sync from a config entry."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def _token() -> str:
        await oauth_session.async_ensure_token_valid()
        return str(oauth_session.token["access_token"])

    token = entry.data.get("token", {})
    host = token.get("pcloud_host") or API_HOSTS.get(entry.data.get(CONF_REGION, ""), API_HOST_US)
    api = PCloudApi(async_get_clientsession(hass), host, _token)

    try:
        await api.async_userinfo(retry=False)
    except PCloudAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except PCloudError as err:
        raise ConfigEntryNotReady(str(err)) from err

    async def _update_quota() -> dict[str, Any]:
        try:
            info = await api.async_userinfo()
        except PCloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PCloudError as err:
            raise UpdateFailed(str(err)) from err
        quota = int(info.get("quota", 0))
        used = int(info.get("usedquota", 0))
        return {"quota": quota, "used": used, "free": max(0, quota - used)}

    quota = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=f"{DOMAIN} quota",
        update_method=_update_quota,
        update_interval=timedelta(minutes=QUOTA_UPDATE_MINUTES),
    )
    await quota.async_config_entry_first_refresh()

    settings = SyncSettings.from_options(hass, entry.options)
    manager = SyncManager(hass, entry, api, settings)
    await manager.async_load()

    entry.runtime_data = PCloudSyncData(api=api, manager=manager, quota=quota)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    manager.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _LOGGER.info(
        "pCloud Sync ready: target %s, %d watch folder(s), interval %s min, "
        "delete after upload: %s, local cache: %s, dry run: %s",
        settings.remote_folder,
        len(settings.watch_folders),
        settings.scan_interval_min,
        settings.delete_after_upload,
        settings.use_local_cache,
        settings.dry_run,
    )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: PCloudSyncConfigEntry) -> None:
    """Reload the entry so new options take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PCloudSyncConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.manager.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
