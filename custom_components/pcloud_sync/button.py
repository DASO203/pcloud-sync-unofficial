"""Button to start a sync manually."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PCloudSyncEntity

if TYPE_CHECKING:
    from . import PCloudSyncConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PCloudSyncConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([SyncNowButton(entry)])


class SyncNowButton(PCloudSyncEntity, ButtonEntity):
    """Start a sync of all watch folders."""

    def __init__(self, entry: PCloudSyncConfigEntry) -> None:
        """Initialize."""
        super().__init__(entry, "sync_now")

    async def async_press(self) -> None:
        """Run the sync in the background (it may take a while)."""
        manager = self._entry.runtime_data.manager
        self._entry.async_create_background_task(
            self.hass, manager.async_sync(reason="button"), "pcloud_sync button sync"
        )
