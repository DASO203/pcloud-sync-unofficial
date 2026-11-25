"""Sensors: sync status, counters and pCloud quota."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTIVITY_ATTRIBUTE_SIZE,
    STATUS_AUTH_ERROR,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_SYNCING,
)
from .entity import PCloudSyncEntity

if TYPE_CHECKING:
    from . import PCloudSyncConfigEntry
    from .manager import SyncManager

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ManagerSensorDescription(SensorEntityDescription):
    """Sensor fed by the sync manager."""

    value_fn: Callable[[SyncManager], Any]
    attrs_fn: Callable[[SyncManager], dict[str, Any]] | None = None


MANAGER_SENSORS: tuple[ManagerSensorDescription, ...] = (
    ManagerSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_IDLE, STATUS_SYNCING, STATUS_ERROR, STATUS_AUTH_ERROR],
        value_fn=lambda m: m.status,
        attrs_fn=lambda m: {
            "last_error": m.last_error,
            "dry_run": m.settings.dry_run,
            "delete_after_upload": m.settings.delete_after_upload,
            "local_cache": m.settings.use_local_cache,
            "remote_folder": m.settings.remote_folder,
            "watch_folders": [f"{f.local} -> {f.remote}" for f in m.settings.watch_folders],
            "recent_activity": list(m.activity)[:ACTIVITY_ATTRIBUTE_SIZE],
        },
    ),
    ManagerSensorDescription(
        key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: m.last_sync,
    ),
    ManagerSensorDescription(
        key="pending_files",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.pending,
    ),
    ManagerSensorDescription(
        key="uploaded_files",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda m: m.stats.uploaded_files,
    ),
    ManagerSensorDescription(
        key="uploaded_data",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        value_fn=lambda m: m.stats.uploaded_bytes,
    ),
    ManagerSensorDescription(
        key="deleted_local_files",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.stats.deleted_local,
    ),
    ManagerSensorDescription(
        key="failed_uploads",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.stats.failed,
    ),
)

QUOTA_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="used",
        translation_key="quota_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="free",
        translation_key="quota_free",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="quota",
        translation_key="quota_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PCloudSyncConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data
    entities: list[SensorEntity] = [ManagerSensor(entry, data.manager, d) for d in MANAGER_SENSORS]
    entities.extend(QuotaSensor(entry, data.quota, d) for d in QUOTA_SENSORS)
    async_add_entities(entities)


class ManagerSensor(PCloudSyncEntity, SensorEntity):
    """Sensor showing sync manager state."""

    _attr_should_poll = False
    # Keep the (potentially large) activity log out of the recorder database.
    _unrecorded_attributes = frozenset({"recent_activity", "watch_folders"})
    entity_description: ManagerSensorDescription

    def __init__(
        self,
        entry: PCloudSyncConfigEntry,
        manager: SyncManager,
        description: ManagerSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(entry, description.key)
        self.entity_description = description
        self._manager = manager

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> Any:
        """Return the value."""
        return self.entity_description.value_fn(self._manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._manager)


class QuotaSensor(PCloudSyncEntity, CoordinatorEntity, SensorEntity):
    """pCloud storage quota."""

    def __init__(
        self, entry: PCloudSyncConfigEntry, coordinator: Any, description: SensorEntityDescription
    ) -> None:
        """Initialize."""
        PCloudSyncEntity.__init__(self, entry, f"quota_{description.key}")
        CoordinatorEntity.__init__(self, coordinator)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> int | None:
        """Return the value."""
        data = self.coordinator.data or {}
        return data.get(self.entity_description.key)
