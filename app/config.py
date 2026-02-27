"""Application configuration via environment variables / .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    iett_soap_base: str = "https://api.ibb.gov.tr/iett"
    iett_rest_base: str = "https://iett.istanbul"
    trafik_base: str = "https://trafik.ibb.gov.tr"
    osrm_base: str = "https://router.project-osrm.org"

    cache_ttl_fleet: int = 15
    cache_ttl_arrivals: int = 20
    cache_ttl_stops: int = 86400
    cache_ttl_schedule: int = 3600
    cache_ttl_announcements: int = 300
    cache_ttl_traffic: int = 30
    cache_ttl_search: int = 300

    # Background fleet poller
    fleet_poll_interval: int = 30   # seconds between polls
    fleet_trail_minutes: int = 5    # how many minutes of trail to keep per bus

    log_level: str = "info"
    port: int = 8000


settings = Settings()
