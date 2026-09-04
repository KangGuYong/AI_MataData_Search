from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # DB
    meta_dsn: str
    biz_dsn: str
    biz_schema: str = "biz"
    meta_schema: str = "meta"

    # LLM
    ollama_base_url: str = "http://192.168.0.169:11434"
    llm_model: str = "gemma4:26b-a4b-it-q4_K_M"
    llm_timeout_sec: int = 60

    # Embedding
    embed_provider: str = "ollama"          # ollama | sentence_transformers
    embed_model: str = "bge-m3:latest"
    embed_dim: int = 1024
    embed_batch: int = 16

    # 수집
    value_distinct_max: int = 50
    sample_value_count: int = 5

    # 검색 튜닝
    rrf_k: int = 60
    w_value: float = 3.0
    w_vector_col: float = 1.0
    w_vector_tbl: float = 1.0
    w_keyword: float = 0.7
    top_tables: int = 5
    score_cutoff_ratio: float = 0.2
    max_hits_per_table: int = 3
    max_context_tables: int = 8
    join_max_depth: int = 3
    # keyword 경로는 word_similarity를 쓴다. 토큰이 그대로 들어있으면 1.0이
    # 나오므로 similarity() 기준이던 0.2보다 높게 잡아야 쓰레기가 걸러진다.
    trgm_min_similarity: float = 0.7

    # SQL 실행
    sql_row_limit: int = 100
    sql_max_limit: int = 1000
    sql_timeout_sec: int = Field(default=10, gt=0)
    collect_timeout_sec: int = Field(default=120, gt=0)

    @property
    def weights(self) -> dict[str, float]:
        return {
            "value": self.w_value,
            "v_col": self.w_vector_col,
            "v_tbl": self.w_vector_tbl,
            "keyword": self.w_keyword,
        }


settings = Settings()
