from functools import lru_cache
from typing import Protocol

from app.config import settings


class EmbeddingClient(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트를 같은 순서의 벡터 리스트로 변환한다."""
        ...


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """프로세스당 하나만 만든다.

    FastAPI/Streamlit은 요청마다 이 함수를 부르는데, 매번 새 httpx.Client를
    만들면 커넥션 풀이 닫히지 않고 쌓여 파일 디스크립터가 샌다.
    """
    provider = settings.embed_provider.lower()
    if provider == "ollama":
        from app.embedding.ollama_client import OllamaEmbedding

        return OllamaEmbedding()
    if provider == "sentence_transformers":
        from app.embedding.sstf_client import SentenceTransformerEmbedding

        return SentenceTransformerEmbedding()
    raise ValueError(f"알 수 없는 EMBED_PROVIDER: {settings.embed_provider}")
