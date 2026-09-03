import httpx

from app.config import settings


class OllamaEmbedding:
    def __init__(self) -> None:
        self.dim = settings.embed_dim
        self._client = httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_sec
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), settings.embed_batch):
            chunk = texts[i : i + settings.embed_batch]
            r = self._client.post(
                "/api/embed", json={"model": settings.embed_model, "input": chunk}
            )
            r.raise_for_status()
            vectors = r.json()["embeddings"]
            if len(vectors) != len(chunk):
                raise RuntimeError(
                    f"임베딩 개수 불일치: 요청 {len(chunk)} 응답 {len(vectors)}"
                )
            for v in vectors:
                if len(v) != self.dim:
                    raise RuntimeError(
                        f"임베딩 차원 불일치: EMBED_DIM={self.dim} 실제={len(v)}"
                    )
            out.extend(vectors)
        return out
