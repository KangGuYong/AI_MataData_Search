"""MCP 서버 전용 설정.

app/config.py 와 아무것도 공유하지 않는다. 이 프로세스만 BIZ_DSN 을 안다.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env.mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    biz_dsn: str
    biz_schema: str = "biz"

    sql_row_limit: int = 100
    sql_max_limit: int = 1000
    sql_timeout_sec: int = Field(default=10, gt=0)

    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100
    # 빈 토큰은 인증을 통째로 무력화하므로 기동 자체를 막는다.
    mcp_auth_token: str = Field(min_length=1)


settings = Settings()
