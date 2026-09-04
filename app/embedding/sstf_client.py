from app.config import settings


class SentenceTransformerEmbedding:
    """Ollama를 쓸 수 없을 때의 폴백. pip install '.[sstf]' 필요.

    주의: 기본 경로(Ollama bge-m3)와 **다른 모델**(KURE-v1)을 쓴다. 두 모델은
    차원이 1024로 같아 차원 가드에 걸리지 않으므로, 이미 bge-m3로 임베딩을
    채운 DB에서 provider만 바꾸면 오류 없이 검색 품질만 조용히 망가진다.
    provider를 바꾼 뒤에는 반드시 `python -m app.cli embed`로 전체를 다시
    임베딩해야 한다.
    """

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
