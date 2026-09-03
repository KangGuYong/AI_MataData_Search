from app.config import settings


class SentenceTransformerEmbedding:
    """Ollama GGUF 경로가 막혔을 때의 폴백. pip install '.[sstf]' 필요."""

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("nlpai-lab/KURE-v1")
        self.dim = self._model.get_sentence_embedding_dimension()
        if self.dim != settings.embed_dim:
            raise RuntimeError(
                f"EMBED_DIM={settings.embed_dim} 이지만 모델 차원은 {self.dim} 입니다. "
                ".env의 EMBED_DIM과 DDL의 vector(N)을 맞추세요."
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(
            texts, batch_size=settings.embed_batch, normalize_embeddings=True
        )
        return [v.tolist() for v in vecs]
