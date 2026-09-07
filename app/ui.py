import httpx
import streamlit as st

from app.config import settings

st.set_page_config(page_title="AI 메타데이터 검색", layout="wide")
st.title("AI 메타데이터 검색")

question = st.text_input("질문", placeholder="서울 고객의 2025년 판매 실적")

if st.button("질의", type="primary") and question:
    with st.spinner("검색 및 SQL 생성 중... (LLM 응답까지 최대 1분(최대 5분) 정도 걸릴 수 있습니다)"):
        try:
            resp = httpx.post(
                f"{settings.api_url}/ask",
                json={"question": question},
                timeout=settings.llm_timeout_sec + 60,
            )
            resp.raise_for_status()
            r = resp.json()
        except Exception as e:  # noqa: BLE001
            st.error(f"백엔드 호출 실패: {type(e).__name__}: {e}")
            st.stop()

    st.subheader("선정 테이블")
    st.write(r["tables"] or "(없음)")

    if r["sql"]:
        st.subheader("생성된 SQL")
        st.code(r["sql"], language="sql")

    if r["error"]:
        st.error(r["error"])
    elif r["rows"]:
        st.subheader(f"결과 ({len(r['rows'])}행)")
        st.dataframe([dict(zip(r["columns"], row)) for row in r["rows"]])

    with st.expander("검색 점수 / 히트"):
        st.json(r["trace"])
    with st.expander("LLM 컨텍스트"):
        st.text(r["context"])
