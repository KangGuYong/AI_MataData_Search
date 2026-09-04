# AI 메타데이터 검색 (PoC)

한국어 질문을 받아 PostgreSQL 메타데이터(테이블/컬럼/값/업무 설명)를 검색하고, 관련 테이블을 좁힌 뒤
LLM으로 SQL을 생성해 읽기 전용으로 실행하는 Text-to-SQL 프로토타입입니다. 검증용 4테이블 픽스처
(`biz.customer`, `biz.orders`, `biz.order_detail`, `biz.product`)를 대상으로 동작을 확인했습니다.

이 프로젝트는 PoC입니다. 프로덕션 배포를 염두에 두고 만들지 않았고, 아래 "알려진 한계"에 적은 문제들이
그대로 남아 있습니다.

## 사전 준비

- PostgreSQL. `vector`(pgvector) 확장과 `pg_trgm` 확장이 필요합니다.
  - `vector`는 슈퍼유저 권한으로만 설치할 수 있습니다 (`CREATE EXTENSION vector`).
  - `pg_trgm`은 일반 DB 소유자 권한으로 설치 가능합니다.
- Ollama 호스트. 다음 두 모델이 받아져 있어야 합니다.
  - `gemma4:26b-a4b-it-q4_K_M` (SQL 생성용 LLM, 26B라 응답에 10~40초, 최악의 경우 60초 타임아웃까지 걸립니다)
  - `bge-m3:latest` (임베딩용)

## 설치

```bash
pip install -e ".[dev]"
cp .env.example .env
# .env를 열어 META_DSN, BIZ_DSN, OLLAMA_BASE_URL 등을 실제 값으로 채운다
```

## 초기 구축 순서

아래 순서대로 실행합니다. `enrich`는 LLM으로 모든 컬럼의 업무명/설명/동의어를 생성하므로 4테이블
기준으로도 약 14분이 걸립니다.

```bash
python -m app.cli doctor       # DB 연결, 확장 설치 여부 점검
python -m app.cli init-db      # meta 스키마 생성 (meta 테이블 전체를 재생성하므로 주의)
python -m app.cli fixture      # biz 테스트 테이블/더미 데이터 생성 (기존 biz 테이블 삭제)
python -m app.cli embed-test   # 임베딩 클라이언트 헬스체크
python -m app.cli collect      # biz 스키마를 읽어 meta에 테이블/컬럼/관계/값 프로파일 적재
python -m app.cli enrich       # LLM으로 업무명/설명/동의어 생성 (~14분)
python -m app.cli embed        # search_text 재생성 및 테이블/컬럼/값 임베딩 채우기
```

## 사용법

```bash
python -m app.cli ask "서울 고객의 2025년 판매 실적"     # 질문 -> SQL 생성 -> 실행
python -m app.cli search --path vector "질문"           # 검색 경로별 히트 확인
python -m app.cli context "질문"                        # LLM에 넘어가는 컨텍스트만 출력
python -m app.cli eval                                   # 8문항 평가 세트 일괄 실행, 지표 출력
```

FastAPI로 띄우기:

```bash
uvicorn app.api:api --port 8000
# POST /ask {"question": "..."}
# GET  /metadata/tables
```

Streamlit UI:

```bash
streamlit run app/ui.py
```

## 측정 결과

8문항 평가 세트 기준:

- Recall 0.833
- Precision 0.688
- SQL 실행 성공 7/8

튜닝 실험 결과, 4테이블 픽스처 규모에서는 `TOP_TABLES`(선정 테이블 수, `.env`) 외의 다른 검색 가중치
(`W_VALUE`, `W_VECTOR_*`, `W_KEYWORD`, `SCORE_CUTOFF_RATIO` 등)를 조정해도 지표에 유의미한 차이가
나지 않았습니다. 테이블 수가 적어 랭킹 상위권이 거의 고정되기 때문으로 보입니다.

## 알려진 한계

- **무관한 질문을 걸러내지 못함**: 컷오프가 상대 점수(`SCORE_CUTOFF_RATIO`) 기준이라 절대적인 하한이
  없습니다. 관련 테이블이 전혀 없는 질문도 점수가 낮은 테이블을 그대로 선정해 LLM에 넘기고, 그 결과
  LLM이 응답을 만들지 못해 타임아웃으로 끝나는 경우가 있습니다 (평가 세트 8번 질문이 이 사례입니다).
- **읽기 전용 DB 롤은 작성만 되어 있고 적용되지 않음**: `sql/04_readonly_role.sql`은 존재하지만 실제
  DB에 적용해 SQL 실행을 그 롤로 제한하는 절차는 아직 없습니다. 현재 SQL 실행은 애플리케이션 레벨의
  단일 SELECT 검증에만 의존합니다.
- **토크나이저가 조사 제거만 함**: 형태소 분석기 없이 규칙 기반으로 한국어 조사만 잘라내는 수준이라,
  활용형이 다르거나 조사가 아닌 접미사가 붙은 경우 토큰이 어긋날 수 있습니다.
