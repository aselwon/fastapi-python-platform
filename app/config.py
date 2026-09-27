from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://opsticket:opsticket@localhost:5432/opsticket"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = Field(default=30, ge=1, le=1440)
    login_rate_limit: int = Field(default=10, ge=1)
    login_window_seconds: int = Field(default=60, ge=1)
