import pytest

from sqlmcp.config import Settings


def test_필수값이_있으면_기본값이_채워진다(tmp_path):
    env = tmp_path / ".env.mcp"
    env.write_text(
        "BIZ_DSN=postgresql://u:p@127.0.0.1:5432/db\nMCP_AUTH_TOKEN=t\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=str(env))
    assert s.biz_dsn == "postgresql://u:p@127.0.0.1:5432/db"
    assert s.mcp_auth_token == "t"
    assert s.mcp_host == "127.0.0.1"
    assert s.mcp_port == 8100
    assert s.sql_row_limit == 100
    assert s.sql_max_limit == 1000
    assert s.sql_timeout_sec == 10


def test_토큰이_비면_거부한다(tmp_path):
    env = tmp_path / ".env.mcp"
    env.write_text("BIZ_DSN=postgresql://u:p@h/db\nMCP_AUTH_TOKEN=\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings(_env_file=str(env))
