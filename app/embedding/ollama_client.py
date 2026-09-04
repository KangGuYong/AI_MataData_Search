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
            payload = r.json()
            if "embeddings" not in payload:
                # 구버전 /api/embeddings 응답이거나 오류 본문이면 KeyError만 뜬다.
                # 무엇이 왔는지 보여줘야 원인을 짚을 수 있다.
                raise RuntimeError(
                    f"Ollama 임베딩 응답에 'embeddings' 키가 없습니다. "
                    f"model={settings.embed_model} 응답키={sorted(payload)} "
                    f"본문={str(payload)[:200]}"
                )
            vectors = payload["embeddings"]
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
