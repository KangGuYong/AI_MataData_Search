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
    # tests/questions.yaml 8문항 eval --retrieval-only 로 5(사실상 미작동) ->
    # 2로 낮췄을 때 Precision 0.375 -> 0.688 (Recall 0.875 -> 0.833). 테이블이
    # 4개뿐인 고정 세트에서만 유효한 값이며, 테이블 수가 늘면 재측정이 필요하다.
    top_tables: int = 2
    score_cutoff_ratio: float = 0.2
    # 융합 점수의 절대 하한. 상대 컷오프와 달리 무관한 질문 자체를 거를 수
    # 있지만, 테이블이 적으면 점수가 촘촘히 붙어 효과가 없다. 실측 결과
    # 이 4테이블 픽스처에서는 무관 질문(0.04892)이 정상 질문(0.04840,
    # 0.04865)보다 오히려 높아 분리가 불가능하므로 기본값은 0.0(비활성)이다.
    min_table_score: float = 0.0
    # 질문 토큰의 최대 IDF가 이 값 미만이면 무관한 질문으로 보고 검색을
    # 중단한다. log(전체테이블수 / 토큰이_매칭된_테이블수) 기준이다.
    # 실측: 무관 질문 0.29 vs 정상 질문 전부 >= 0.69.
    min_token_idf: float = 0.5
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
