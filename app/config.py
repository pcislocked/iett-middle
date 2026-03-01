"""Application configuration via environment variables / .env file."""
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

    # On-demand fleet refresh — stale threshold (seconds)
    fleet_poll_interval: int = 30   # max age before a background refresh is triggered
    fleet_trail_minutes: int = 5    # how many minutes of trail to keep per bus

    # ntcapi.iett.istanbul — private IETT API
    ntcapi_client_id: str = "pLwqtobYHTBshBWRrEZdSWsngOywQvHa"
    ntcapi_client_secret: str = "JERLUJgaZSygMTqoCtrhrVnvqeVGGVznktlwuOfHqmQTzjnC"
    ntcapi_scope: str = "VLCn2qErUdrr1Ehg0yxWObMW9krFb7RC service"
    ntcapi_ybs_username: str = "netuce"
    ntcapi_ybs_password: str = "n1!t8c7M1"

    log_level: str = "info"
    port: int = 8000


settings = Settings()
