from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mintel"
    environment: str = "local"
    secret_key: str = "insecure-mintel-dev-session-key"
    bootstrap_admin_email: str = "admin@mintel.local"
    bootstrap_admin_password: str = "admin123"
    bootstrap_admin_name: str = "Mintel Admin"
    database_url: str = Field(default="postgresql+psycopg://mintel:mintel@localhost:5437/mintel")
    database_pool_size: int = 20
    database_max_overflow: int = 40
    database_pool_timeout: int = 10
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    maas_base_url: str = ""
    maas_api_key: str = ""
    maas_database_url: str = ""
    maas_job_links_csv_path: str = ""
    maas_job_description_cache_path: str = ""
    allowed_hosts: str = "localhost,127.0.0.1"
    session_cookie_secure: bool = False
    session_cookie_same_site: str = "lax"
    behind_proxy: bool = False

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
