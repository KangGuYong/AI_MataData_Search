from typing import Protocol

from app.config import settings


class EmbeddingClient(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트를 같은 순서의 벡터 리스트로 변환한다."""
        ...


def get_embedding_client() -> EmbeddingClient:
    provider = settings.embed_provider.lower()
    if provider == "ollama":
        from app.embedding.ollama_client import OllamaEmbedding

        return OllamaEmbedding()
    if provider == "sentence_transformers":
        from app.embedding.sstf_client import SentenceTransformerEmbedding

        return SentenceTransformerEmbedding()
    raise ValueError(f"알 수 없는 EMBED_PROVIDER: {settings.embed_provider}")
