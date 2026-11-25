"""Application credentials (OAuth2) platform for pCloud Sync.

This integration ships **no** client id / client secret. Every user registers
their own pCloud app and enters the credentials in Home Assistant
(Settings -> Devices & services -> ... -> Application credentials).
That way nobody else's app has access to your pCloud account.

pCloud's OAuth2 differs from the standard in a few ways which are handled here:

* the token endpoint depends on the data region (US / EU) of the account,
* tokens never expire and there is no refresh token,
* the token request takes ``client_id``, ``client_secret`` and ``code``.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_HOST_EU,
    API_HOST_US,
    API_HOSTS,
    LOCATION_IDS,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN_PATH,
    TOKEN_LIFETIME_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class PCloudOAuth2Implementation(AuthImplementation):
    """pCloud flavoured OAuth2 implementation."""

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Exchange the authorization code for an access token.

        Home Assistant only forwards ``code`` and ``state`` from the redirect,
        not pCloud's ``locationid``. The code is therefore tried against the US
        endpoint first and then the EU endpoint; the endpoint that accepts it
        tells us the account's region.
        """
        code = external_data["code"]
        session = async_get_clientsession(self.hass)
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
        }
        last_error = "unknown error"
        for region, host in (("us", API_HOST_US), ("eu", API_HOST_EU)):
            try:
                async with session.post(
                    f"{host}{OAUTH2_TOKEN_PATH}",
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    result = await response.json(content_type=None)
            except (TimeoutError, aiohttp.ClientError) as err:
                last_error = str(err) or type(err).__name__
                continue
            if (
                isinstance(result, dict)
                and result.get("result") == 0
                and result.get("access_token")
            ):
                region = LOCATION_IDS.get(result.get("locationid"), region)
                _LOGGER.debug("pCloud OAuth token obtained (region %s)", region)
                return {
                    "access_token": result["access_token"],
                    "token_type": "bearer",
                    "expires_in": TOKEN_LIFETIME_SECONDS,
                    "pcloud_region": region,
                    "pcloud_host": API_HOSTS[region],
                    "pcloud_uid": result.get("uid") or result.get("userid"),
                }
            if isinstance(result, dict):
                last_error = f"{result.get('result')}: {result.get('error')}"
        _LOGGER.error("pCloud token exchange failed: %s", last_error)
        # A ClientError makes Home Assistant abort the flow with "oauth_failed"
        # instead of showing an unknown error.
        raise aiohttp.ClientError(f"pCloud token exchange failed ({last_error})")

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """pCloud tokens do not expire - just extend the local expiry."""
        return {**token, "expires_in": TOKEN_LIFETIME_SECONDS}


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the authorization server (used for display / defaults)."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=f"{API_HOST_US}{OAUTH2_TOKEN_PATH}",
    )


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the pCloud specific OAuth2 implementation."""
    return PCloudOAuth2Implementation(
        hass,
        auth_domain,
        credential,
        AuthorizationServer(
            authorize_url=OAUTH2_AUTHORIZE,
            token_url=f"{API_HOST_US}{OAUTH2_TOKEN_PATH}",
        ),
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Placeholders shown in the 'add application credentials' dialog."""
    return {
        "developer_url": "https://docs.pcloud.com/my_apps/",
        "redirect_url": "https://my.home-assistant.io/redirect/oauth",
    }
