"""New JSON Mobiett Client for iett-middle."""

import logging
import time
from typing import Any

import aiohttp

from app.config import settings
from app.utils.lock import LazyLock

logger = logging.getLogger(__name__)

MOBIETT_AUTH_URL = "https://ntcapi.iett.istanbul/oauth2/v2/auth"
MOBIETT_SERVICE_URL = "https://ntcapi.iett.istanbul/service"


class MobiettApiError(Exception):
    """Raised when an API call fails."""


class MobiettClient:
    _access_token: str | None = None
    _token_expires_at: float = 0.0
    _auth_lock = LazyLock()
    _hat_id_cache: dict[str, int | None] = {}

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _ensure_token(self) -> str:
        """Fetch and cache OAuth2 token if missing or expired."""
        if (
            MobiettClient._access_token
            and time.monotonic() < MobiettClient._token_expires_at
        ):
            return MobiettClient._access_token

        async with MobiettClient._auth_lock:
            # Check again inside lock
            if (
                MobiettClient._access_token
                and time.monotonic() < MobiettClient._token_expires_at
            ):
                return MobiettClient._access_token

            payload = {
                "client_id": settings.ntcapi_client_id,
                "client_secret": settings.ntcapi_client_secret,
                "grant_type": "client_credentials",
                "scope": settings.ntcapi_scope,
            }
            try:
                async with self._session.post(
                    MOBIETT_AUTH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    MobiettClient._access_token = data["access_token"]
                    # Token expires in 3600 seconds, refresh a bit early (3500)
                    expires_in = data.get("expires_in", 3600)
                    MobiettClient._token_expires_at = time.monotonic() + (
                        expires_in - 100
                    )
                    return MobiettClient._access_token  # type: ignore
            except Exception as e:
                raise MobiettApiError(f"OAuth2 failed: {e}") from e

    async def _post_service(self, alias: str, data: dict[str, Any] = None) -> Any:  # type: ignore
        """Make a POST request to the Mobiett /service endpoint."""
        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = {"alias": alias}
        if data:
            payload["data"] = data  # type: ignore

        try:
            async with self._session.post(
                MOBIETT_SERVICE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                MobiettClient._access_token = None
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e
        except Exception as e:
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e

    async def get_hat_id(self, hat_kodu: str) -> int | None:
        """Get the numeric HAT_ID for a route (e.g. 14M -> 497)."""
        hat_kodu_upper = hat_kodu.upper().strip()
        if hat_kodu_upper in MobiettClient._hat_id_cache:
            return MobiettClient._hat_id_cache[hat_kodu_upper]

        # Bound cache size to prevent memory leak
        if len(MobiettClient._hat_id_cache) >= 2000:
            MobiettClient._hat_id_cache.pop(next(iter(MobiettClient._hat_id_cache)))

        # Use mainGetRoute to find the HAT_ID
        res = await self._post_service(
            "mainGetRoute",
            {
                "HATYONETIM.GUZERGAH.YON": "119",
                "HATYONETIM.HAT.HAT_KODU": hat_kodu_upper,
            },
        )

        if not res or not isinstance(res, list):
            self._hat_id_cache[hat_kodu_upper] = None
            return None

        for item in res:
            if "HAT_ID" in item and item["HAT_ID"]:
                hat_id = int(item["HAT_ID"])
                MobiettClient._hat_id_cache[hat_kodu_upper] = hat_id
                return hat_id

        MobiettClient._hat_id_cache[hat_kodu_upper] = None
        return None

    async def get_live_fleet(self, hat_kodu: str) -> list[dict[str, Any]]:
        """Get live locations of all buses on a route via ybs point-passing."""
        hat_id = await self.get_hat_id(hat_kodu)
        if not hat_id:
            logger.warning(f"Could not resolve HAT_ID for route {hat_kodu}")
            return []

        res = await self._post_service(
            "ybs",
            {
                "method": "POST",
                "path": ["real-time-information", "point-passing", str(hat_id)],
                "data": {
                    "password": settings.ntcapi_ybs_password,
                    "username": settings.ntcapi_ybs_username,
                },
            },
        )

        return res if isinstance(res, list) else []

    async def get_stop_detail(self, dcode: str) -> dict[str, Any] | None:
        """Get stop details (name, coordinates) using mainGetBusStop."""
        res = await self._post_service(
            "mainGetBusStop", {"HATYONETIM.DURAK.DURAK_KODU": dcode}
        )

        if not res or not isinstance(res, list):
            return None

        return res[0]

    async def get_stop_announcements(self, dcode: str) -> list[dict[str, Any]]:
        """Get stop-status traffic announcements from ybs."""
        res = await self._post_service(
            "ybs",
            {
                "method": "POST",
                "path": ["real-time-information", "stop-status", str(dcode)],
                "data": {
                    "password": settings.ntcapi_ybs_password,
                    "username": settings.ntcapi_ybs_username,
                },
            },
        )

        if not res or not isinstance(res, dict):
            return []

        data = res.get(str(dcode), {})
        return data.get("duyuru") or []
