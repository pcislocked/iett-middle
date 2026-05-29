"""New JSON Mobiett Client for iett-middle."""
import asyncio
import logging
from typing import Any
import time

import aiohttp
from app.config import settings

logger = logging.getLogger(__name__)

MOBIETT_AUTH_URL = "https://ntcapi.iett.istanbul/oauth2/v2/auth"
MOBIETT_SERVICE_URL = "https://ntcapi.iett.istanbul/service"


class MobiettApiError(Exception):
    """Raised when an API call fails."""


class MobiettClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._auth_lock = asyncio.Lock()
        
        # Cache for hat_kodu -> hat_id
        self._hat_id_cache: dict[str, int] = {}

    async def _ensure_token(self) -> str:
        """Fetch and cache OAuth2 token if missing or expired."""
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        async with self._auth_lock:
            # Check again inside lock
            if self._access_token and time.monotonic() < self._token_expires_at:
                return self._access_token

            payload = {
                "client_id": settings.ntcapi_client_id,
                "client_secret": settings.ntcapi_client_secret,
                "grant_type": "client_credentials",
                "scope": settings.ntcapi_scope
            }
            try:
                async with self._session.post(
                    MOBIETT_AUTH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    self._access_token = data["access_token"]
                    # Token expires in 3600 seconds, refresh a bit early (3500)
                    expires_in = data.get("expires_in", 3600)
                    self._token_expires_at = time.monotonic() + (expires_in - 100)
                    return self._access_token
            except Exception as e:
                raise MobiettApiError(f"OAuth2 failed: {e}") from e

    async def _post_service(self, alias: str, data: dict[str, Any] = None) -> Any:
        """Make a POST request to the Mobiett /service endpoint."""
        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = {"alias": alias}
        if data:
            payload["data"] = data

        try:
            async with self._session.post(
                MOBIETT_SERVICE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            raise MobiettApiError(f"Mobiett API ({alias}) failed: {e}") from e

    async def get_hat_id(self, hat_kodu: str) -> int | None:
        """Get the numeric HAT_ID for a route (e.g. 14M -> 497)."""
        hat_kodu_upper = hat_kodu.upper().strip()
        if hat_kodu_upper in self._hat_id_cache:
            return self._hat_id_cache[hat_kodu_upper]

        # Use mainGetRoute to find the HAT_ID
        res = await self._post_service(
            "mainGetRoute", 
            {"HATYONETIM.GUZERGAH.YON": "119", "HATYONETIM.HAT.HAT_KODU": hat_kodu_upper}
        )
        
        if not res or not isinstance(res, list):
            return None
            
        for item in res:
            if "HAT_ID" in item and item["HAT_ID"]:
                hat_id = int(item["HAT_ID"])
                self._hat_id_cache[hat_kodu_upper] = hat_id
                return hat_id
                
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
                    "username": settings.ntcapi_ybs_username
                }
            }
        )
        
        return res if isinstance(res, list) else []

    async def get_stop_detail(self, dcode: str) -> dict[str, Any] | None:
        """Get stop details (name, coordinates) using mainGetBusStop."""
        res = await self._post_service(
            "mainGetBusStop", 
            {"HATYONETIM.DURAK.DURAK_KODU": dcode}
        )
        
        if not res or not isinstance(res, list):
            return None
            
        return res[0]
