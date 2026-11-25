"""Diagnostics (Settings -> Devices & services -> pCloud Sync -> Download diagnostics).

Tokens and personal data are redacted, so the file can be attached to issues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import PCloudSyncConfigEntry

TO_REDACT = {
    "token",
    "access_token",
    "refresh_token",
    "pcloud_uid",
    "email",
    "userid",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PCloudSyncConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options), "title": entry.title},
            TO_REDACT,
        ),
        "api_host": data.api.host,
        "quota": data.quota.data,
        "manager": data.manager.diagnostics(),
    }
