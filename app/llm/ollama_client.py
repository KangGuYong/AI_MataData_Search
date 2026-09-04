import httpx

from app.config import settings


class OllamaLLM:
    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_sec
        )

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        r = self._client.post(
            "/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        r.raise_for_status()
        payload = r.json()
        message = payload.get("message")
        if not isinstance(message, dict) or "content" not in message:
            # message/message.content가 없으면 KeyError만 뜬다.
            # 무엇이 왔는지 보여줘야 원인을 짚을 수 있다.
            raise RuntimeError(
                f"Ollama LLM 응답에 'message.content'가 없습니다. "
                f"model={settings.llm_model} 응답키={sorted(payload)} "
                f"본문={str(payload)[:200]}"
            )
        return message["content"]
