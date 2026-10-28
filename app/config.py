"""Application configuration via environment variables / .env file."""

import re
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    iett_soap_base: str = "https://api.ibb.gov.tr/iett"
    iett_rest_base: str = "https://iett.istanbul"

    trafik_base: str = "https://trafik.ibb.gov.tr"
    osrm_base: str = "https://router.project-osrm.org"

    cache_ttl_fleet: int = 30
    cache_ttl_arrivals: int = 20
    cache_ttl_stops: int = 86400
    cache_ttl_schedule: int = 3600
    cache_ttl_announcements: int = 300
    cache_ttl_traffic: int = 30
    cache_ttl_search: int = 300
    cache_stale_ttl: int = 60

    # On-demand fleet refresh — stale threshold (seconds)
    fleet_poll_interval: int = 30  # max age before a background refresh is triggered
    fleet_cache_max_age: int = 900  # force refresh every 15 min to prevent 6h+ stale FILO data
    fleet_manual_refresh_cooldown: int = 10  # minimum gap between POST /v1/fleet/refresh calls
    fleet_trail_minutes: int = 5  # how many minutes of trail to keep per bus

    # ntcapi.iett.istanbul — private IETT API
    ntcapi_client_id: str = "pLwqtobYHTBshBWRrEZdSWsngOywQvHa"
    ntcapi_client_secret: str = "JERLUJgaZSygMTqoCtrhrVnvqeVGGVznktlwuOfHqmQTzjnC"
    ntcapi_scope: str = "VLCn2qErUdrr1Ehg0yxWObMW9krFb7RC service"
    ntcapi_ybs_username: str = "netuce"
    ntcapi_ybs_password: str = "n1!t8c7M1"

    # data.ibb.gov.tr — IBB Open Data Portal API Key (passed via env IBB_DATA_API_KEY)
    ibb_data_api_key: str | None = None

    log_level: str = "info"
    enable_outgoing_trace: bool = False
    port: int = 8000

    environment: str = "production"
    allow_test_backdoors: bool = False

    vehicle_cache_path: str = "data/vehicles.db"
    stop_cache_path: str = "data/stops.db"

    app_version: str = "0.4.12"
    proxy_notice_url: str = "https://github.com/pcislocked/iett-middle/blob/master/PROXY_NOTICE.md"

    def user_agent(self, via_proxy: bool = False) -> str:
        """Construct User-Agent header with optional proxy notice reference."""
        via = " (via proxy; " if via_proxy else " ("
        return f"iett-middle/{self.app_version}{via}+{self.proxy_notice_url})"

    # SOAP Upstream Proxy Pool & Adaptive Routing (Bypasses datacenter geo-blocking safely)
    soap_proxy_urls: str | None = None
    soap_proxy_url: str | None = None
    soap_proxy_max_per_window: int = 45
    soap_proxy_window_seconds: float = 120.0
    soap_proxy_min_interval: float = 2.0
    soap_direct_probe_interval: float = 300.0

    @property
    def effective_soap_proxy_urls(self) -> list[str]:
        """Resolve list of SOAP proxy URLs from CLI arguments or environment variables."""
        raw: str | None = None
        for i, arg in enumerate(sys.argv):
            for prefix in ("--soap-proxy-urls=", "--soap-proxy-url=", "--soap-proxy="):
                if arg.startswith(prefix):
                    raw = arg.split("=", 1)[1].strip()
                    break
            if raw is not None:
                break
            if arg in ("--soap-proxy-urls", "--soap-proxy-url", "--soap-proxy") and i + 1 < len(
                sys.argv
            ):
                raw = sys.argv[i + 1].strip()
                break

        if raw is None:
            raw = self.soap_proxy_urls or self.soap_proxy_url

        if not raw:
            return []

        tokens = re.split(r"[,;\s]+", raw.strip())
        return [t.strip() for t in tokens if t.strip()]

    @property
    def effective_soap_proxy_url(self) -> str | None:
        """Backward-compatible single proxy URL accessor."""
        urls = self.effective_soap_proxy_urls
        return urls[0] if urls else None


settings = Settings()
