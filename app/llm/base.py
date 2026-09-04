from functools import lru_cache
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str, system: str | None = None) -> str:
        """프롬프트를 보내고 응답 텍스트를 받는다."""
        ...


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """프로세스당 하나만 만든다.

    FastAPI/Streamlit은 요청마다 이 함수를 부르는데, 매번 새 httpx.Client를
    만들면 커넥션 풀이 닫히지 않고 쌓여 파일 디스크립터가 샌다.
    """
    from app.llm.ollama_client import OllamaLLM

    return OllamaLLM()
