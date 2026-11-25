"""Config and options flow for pCloud Sync (Unofficial)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import PCloudApi, PCloudAuthError, PCloudError
from .const import (
    API_HOST_US,
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
    CONF_REGION,
    CONF_REMOTE_FOLDER,
    CONF_SCAN_INTERVAL,
    CONF_UPLOAD_TIMEOUT,
    CONF_USE_LOCAL_CACHE,
    CONF_WATCH_FOLDERS,
    CONFLICT_OVERWRITE,
    CONFLICT_RENAME,
    DEFAULT_REMOTE_FOLDER,
    DOMAIN,
)
from .helpers import UnsafePathError, is_within, join_remote, parse_watch_folders
from .settings import default_options

_LOGGER = logging.getLogger(__name__)

SECTION_FOLDERS = "folders"
SECTION_BEHAVIOUR = "behaviour"
SECTION_CACHE = "cache"
SECTION_SOURCES = "sources"
SECTION_ADVANCED = "advanced"

SECTION_KEYS: dict[str, tuple[str, ...]] = {
    SECTION_FOLDERS: (
        CONF_WATCH_FOLDERS,
        CONF_INCLUDE_PATTERNS,
        CONF_EXCLUDE_PATTERNS,
    ),
    SECTION_BEHAVIOUR: (
        CONF_SCAN_INTERVAL,
        CONF_MIN_FILE_AGE,
        CONF_DELETE_AFTER_UPLOAD,
        CONF_DATE_SUBFOLDERS,
        CONF_CONFLICT_MODE,
        CONF_DRY_RUN,
    ),
    SECTION_CACHE: (CONF_USE_LOCAL_CACHE, CONF_CACHE_PATH),
    SECTION_SOURCES: (CONF_FRIGATE_URL,),
    SECTION_ADVANCED: (
        CONF_MAX_FILES_PER_RUN,
        CONF_MAX_PARALLEL_UPLOADS,
        CONF_UPLOAD_TIMEOUT,
    ),
}
INT_KEYS = (
    CONF_SCAN_INTERVAL,
    CONF_MIN_FILE_AGE,
    CONF_MAX_FILES_PER_RUN,
    CONF_MAX_PARALLEL_UPLOADS,
    CONF_UPLOAD_TIMEOUT,
)


def _number(minimum: int, maximum: int, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _options_schema(values: Mapping[str, Any]) -> vol.Schema:
    """Build the (sectioned) options schema with the current values as defaults."""

    def opt(key: str) -> vol.Optional:
        return vol.Optional(key, default=values[key])

    text = TextSelector()
    return vol.Schema(
        {
            vol.Required(CONF_REMOTE_FOLDER, default=values[CONF_REMOTE_FOLDER]): text,
            vol.Required(SECTION_FOLDERS): section(
                vol.Schema(
                    {
                        opt(CONF_WATCH_FOLDERS): TextSelector(TextSelectorConfig(multiline=True)),
                        opt(CONF_INCLUDE_PATTERNS): text,
                        opt(CONF_EXCLUDE_PATTERNS): text,
                    }
                ),
                {"collapsed": False},
            ),
            vol.Required(SECTION_BEHAVIOUR): section(
                vol.Schema(
                    {
                        opt(CONF_SCAN_INTERVAL): _number(0, 1440, "min"),
                        opt(CONF_MIN_FILE_AGE): _number(0, 86400, "s"),
                        opt(CONF_DELETE_AFTER_UPLOAD): BooleanSelector(),
                        opt(CONF_DATE_SUBFOLDERS): BooleanSelector(),
                        opt(CONF_CONFLICT_MODE): SelectSelector(
                            SelectSelectorConfig(
                                options=[CONFLICT_RENAME, CONFLICT_OVERWRITE],
                                mode=SelectSelectorMode.DROPDOWN,
                                translation_key=CONF_CONFLICT_MODE,
                            )
                        ),
                        opt(CONF_DRY_RUN): BooleanSelector(),
                    }
                ),
                {"collapsed": False},
            ),
            vol.Required(SECTION_CACHE): section(
                vol.Schema(
                    {
                        opt(CONF_USE_LOCAL_CACHE): BooleanSelector(),
                        opt(CONF_CACHE_PATH): text,
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_SOURCES): section(
                vol.Schema({opt(CONF_FRIGATE_URL): text}),
                {"collapsed": True},
            ),
            vol.Required(SECTION_ADVANCED): section(
                vol.Schema(
                    {
                        opt(CONF_MAX_FILES_PER_RUN): _number(1, 5000),
                        opt(CONF_MAX_PARALLEL_UPLOADS): _number(1, 4),
                        opt(CONF_UPLOAD_TIMEOUT): _number(5, 1440, "min"),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _flatten(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Turn the sectioned form input into flat options."""
    flat: dict[str, Any] = {CONF_REMOTE_FOLDER: user_input[CONF_REMOTE_FOLDER]}
    for section_key in SECTION_KEYS:
        flat.update(user_input.get(section_key, {}))
    for key in INT_KEYS:
        if key in flat:
            flat[key] = int(flat[key])
    for key in (
        CONF_WATCH_FOLDERS,
        CONF_INCLUDE_PATTERNS,
        CONF_EXCLUDE_PATTERNS,
        CONF_CACHE_PATH,
        CONF_FRIGATE_URL,
    ):
        flat[key] = str(flat.get(key) or "").strip()
    return flat


def _validate_local_paths(
    hass: HomeAssistant, options: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate watch folders and the cache path (blocking, run in executor).

    Returns (errors, description placeholders).
    """
    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {"path": ""}
    try:
        folders = parse_watch_folders(options[CONF_WATCH_FOLDERS])
    except UnsafePathError as err:
        errors["base"] = "invalid_watch_folder"
        placeholders["path"] = str(err)
        return errors, placeholders

    for folder in folders:
        if not os.path.isdir(folder.local):
            errors["base"] = "watch_folder_missing"
            placeholders["path"] = folder.local
            return errors, placeholders
        if not hass.config.is_allowed_path(folder.local):
            errors["base"] = "watch_folder_not_allowed"
            placeholders["path"] = folder.local
            return errors, placeholders

    if options[CONF_USE_LOCAL_CACHE]:
        cache = options[CONF_CACHE_PATH]
        if not cache or not os.path.isabs(cache):
            errors["base"] = "invalid_cache_path"
            placeholders["path"] = cache
            return errors, placeholders
        if not (is_within(cache, hass.config.config_dir) or hass.config.is_allowed_path(cache)):
            errors["base"] = "cache_path_not_allowed"
            placeholders["path"] = cache
            return errors, placeholders
        if any(is_within(cache, f.local) or is_within(f.local, cache) for f in folders):
            errors["base"] = "cache_overlaps_watch_folder"
            placeholders["path"] = cache
            return errors, placeholders
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            errors["base"] = "cache_path_not_writable"
            placeholders["path"] = cache
    return errors, placeholders


class PCloudSyncOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Set up pCloud Sync via OAuth2."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._oauth_data: dict[str, Any] = {}
        self._account = ""

    @property
    def logger(self) -> logging.Logger:
        """Return the logger."""
        return _LOGGER

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Verify the token, then ask for the remote folder."""
        token = data["token"]
        host = token.get("pcloud_host", API_HOST_US)

        async def _token() -> str:
            return token["access_token"]

        api = PCloudApi(async_get_clientsession(self.hass), host, _token)
        try:
            info = await api.async_userinfo(retry=False)
        except PCloudAuthError:
            return self.async_abort(reason="invalid_auth")
        except PCloudError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(str(info["userid"]))
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured()

        self._oauth_data = {**data, CONF_REGION: token.get("pcloud_region", "us")}
        self._account = str(info.get("email") or info["userid"])
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the target folder in pCloud."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                remote = join_remote(user_input[CONF_REMOTE_FOLDER])
            except UnsafePathError:
                errors[CONF_REMOTE_FOLDER] = "invalid_remote_folder"
            else:
                if remote == "/":
                    errors[CONF_REMOTE_FOLDER] = "invalid_remote_folder"
                else:
                    options = default_options(self.hass)
                    options[CONF_REMOTE_FOLDER] = remote
                    return self.async_create_entry(
                        title=f"pCloud ({self._account})",
                        data=self._oauth_data,
                        options=options,
                    )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {vol.Required(CONF_REMOTE_FOLDER, default=DEFAULT_REMOTE_FOLDER): TextSelector()}
            ),
            description_placeholders={"account": self._account},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication (token revoked)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return PCloudSyncOptionsFlow()


class PCloudSyncOptionsFlow(OptionsFlow):
    """Configure what is synced and how."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and validate the options form."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"path": ""}
        values = {**default_options(self.hass), **self.config_entry.options}

        if user_input is not None:
            options = _flatten(user_input)
            values = {**values, **options}
            try:
                options[CONF_REMOTE_FOLDER] = join_remote(options[CONF_REMOTE_FOLDER])
                if options[CONF_REMOTE_FOLDER] == "/":
                    raise UnsafePathError("root")
            except UnsafePathError:
                errors[CONF_REMOTE_FOLDER] = "invalid_remote_folder"

            frigate = options[CONF_FRIGATE_URL]
            if frigate and urlparse(frigate).scheme not in ("http", "https"):
                errors["base"] = "invalid_frigate_url"

            if not errors:
                path_errors, placeholders = await self.hass.async_add_executor_job(
                    _validate_local_paths, self.hass, options
                )
                errors.update(path_errors)

            if not errors:
                options[CONF_FRIGATE_URL] = frigate.rstrip("/")
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(values),
            errors=errors,
            description_placeholders=placeholders,
        )
