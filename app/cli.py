import sys
from pathlib import Path

import typer
from rich.console import Console

from app.config import settings
from app.db import biz_conn_readonly, dsn_user, mask_dsn, meta_conn

app_cli = typer.Typer(help="AI 메타데이터 검색 CLI")
console = Console()

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_sql_file(name: str) -> None:
    path = SQL_DIR / name
    sql = path.read_text(encoding="utf-8")
    try:
        with meta_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
    except Exception as e:
        # 파일 전체를 한 문장으로 보내므로 psycopg 오류만으로는 어느 파일에서
        # 실패했는지 알 수 없다. 파일명을 붙여 다시 던진다.
        console.print(f"[red]FAIL[/] {name} 실행 실패: {e}")
        raise
    console.print(f"[green]OK[/] {name} 실행 완료")


@app_cli.callback()
def _callback() -> None:
    """서브커맨드가 하나뿐이어도 `doctor` 이름을 명시하도록 강제한다 (Typer 단일 명령 축약 방지)."""


@app_cli.command()
def doctor() -> None:
    """DB / Ollama 연결과 확장 설치 상태를 점검한다."""
    console.print(f"[bold]META_DSN[/] {mask_dsn(settings.meta_dsn)}")
    console.print(f"[bold]BIZ_DSN [/] {mask_dsn(settings.biz_dsn)}")

    if dsn_user(settings.meta_dsn) == dsn_user(settings.biz_dsn):
        console.print(
            "[yellow]경고[/] meta와 biz가 동일 계정입니다. "
            "읽기전용 롤 분리는 보류 상태이며 애플리케이션 방어선에만 의존합니다."
        )

    ok = True

    try:
        with meta_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')")
            exts = {r[0] for r in cur.fetchall()}
        console.print(f"[green]OK[/] meta DB 연결. 확장: {sorted(exts) or '없음'}")
        for need in ("vector", "pg_trgm"):
            if need not in exts:
                console.print(f"[red]FAIL[/] 확장 '{need}' 미설치 - sql/01_extensions.sql 실행 필요")
                ok = False
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] meta DB 연결 실패: {e}")
        ok = False

    try:
        with biz_conn_readonly() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            user, db = cur.fetchone()
        console.print(f"[green]OK[/] biz DB 연결 (READ ONLY). user={user} db={db}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] biz DB 연결 실패: {e}")
        ok = False

    import httpx

    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=10)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        console.print(f"[green]OK[/] Ollama 연결. 모델 {len(names)}개")
        if settings.llm_model not in names:
            console.print(f"[yellow]경고[/] LLM_MODEL '{settings.llm_model}' 미존재")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]FAIL[/] Ollama 연결 실패: {e}")
        ok = False

    raise typer.Exit(0 if ok else 1)


@app_cli.command("init-db")
def init_db() -> None:
    """확장 설치 + meta 스키마 생성. meta 테이블을 전부 재생성한다."""
    console.print("[yellow]경고[/] meta 스키마의 7개 테이블을 모두 삭제 후 재생성합니다")
    _run_sql_file("01_extensions.sql")
    _run_sql_file("02_meta_schema.sql")


@app_cli.command()
def fixture() -> None:
    """biz 테스트 테이블과 더미 데이터를 생성한다. 기존 biz 테이블을 삭제한다."""
    _run_sql_file("03_biz_fixture.sql")


@app_cli.command("embed-test")
def embed_test() -> None:
    """임베딩 클라이언트가 살아있는지 확인한다."""
    from app.embedding.base import get_embedding_client

    client = get_embedding_client()
    vecs = client.embed(["매출액", "고객 지역", "서울"])
    console.print(f"[green]OK[/] provider={settings.embed_provider} "
                  f"count={len(vecs)} dim={len(vecs[0])}")


@app_cli.command()
def collect() -> None:
    """biz 스키마를 읽어 meta에 테이블/컬럼/관계와 값 프로파일을 적재한다."""
    from app.collect.introspect import collect_schema
    from app.collect.profile import profile_columns

    console.print(f"[green]OK[/] 스키마 수집: {collect_schema()}")
    console.print(f"[green]OK[/] 값 프로파일: {profile_columns()}")


@app_cli.command()
def enrich() -> None:
    """LLM으로 업무명/설명/동의어를 생성해 meta에 채운다."""
    from app.collect.enrich import enrich_all

    console.print(f"[green]OK[/] enrich 완료: {enrich_all()}")


@app_cli.command()
def embed() -> None:
    """search_text를 다시 만들고 테이블/컬럼/값 임베딩을 채운다."""
    from app.collect.embed import embed_all
    from app.collect.search_text import rebuild_search_text

    console.print(f"[green]OK[/] search_text: {rebuild_search_text()}")
    console.print(f"[green]OK[/] embedding: {embed_all()}")


@app_cli.command()
def search(
    question: str,
    path: str = typer.Option("all", help="vector | keyword | value | all"),
) -> None:
    """검색 경로별 히트를 확인한다."""
    from rich.table import Table

    from app.db import meta_conn
    from app.embedding.base import get_embedding_client
    from app.search import keyword, value, vector
    from app.search.tokenize import tokenize

    tokens = tokenize(question)
    console.print(f"토큰: {tokens}")

    hits = []
    with meta_conn() as conn, conn.cursor() as cur:
        if path in ("vector", "all"):
            qvec = get_embedding_client().embed([question])[0]
            hits += vector.search_columns(cur, qvec)
            hits += vector.search_tables(cur, qvec)
        if path in ("keyword", "all"):
            hits += keyword.search(cur, tokens, settings.trgm_min_similarity)
        if path in ("value", "all"):
            hits += value.search(cur, tokens, settings.trgm_min_similarity)

    tbl = Table("source", "rank", "table_id", "score", "detail")
    for h in hits:
        tbl.add_row(h.source, str(h.rank), str(h.table_id), f"{h.raw_score:.3f}", h.detail)
    console.print(tbl)


@app_cli.command()
def context(question: str) -> None:
    """질문에 대한 LLM 컨텍스트를 만들어 출력한다."""
    from app.db import meta_conn
    from app.embedding.base import get_embedding_client
    from app.search import context as ctx
    from app.search import keyword, value, vector
    from app.search.fusion import fuse
    from app.search.graph import find_join_paths, load_edges
    from app.search.tokenize import tokenize

    tokens = tokenize(question)
    qvec = get_embedding_client().embed([question])[0]

    with meta_conn() as conn, conn.cursor() as cur:
        hits = (
            vector.search_columns(cur, qvec)
            + vector.search_tables(cur, qvec)
            + keyword.search(cur, tokens, settings.trgm_min_similarity)
            + value.search(cur, tokens, settings.trgm_min_similarity)
        )
        scores = fuse(
            hits,
            k=settings.rrf_k,
            weights=settings.weights,
            max_hits_per_table=settings.max_hits_per_table,
            top_tables=settings.top_tables,
            cutoff_ratio=settings.score_cutoff_ratio,
        )
        for s in scores:
            console.print(f"  table_id={s.table_id} score={s.score:.5f}")
        ids = [s.table_id for s in scores]
        paths = find_join_paths(load_edges(cur), ids, settings.join_max_depth)
        text, _, names = ctx.build(cur, question, ids, paths)

    console.print(f"[bold]선정 테이블[/] {names}")
    console.print(text)


@app_cli.command()
def ask(question: str, show_context: bool = typer.Option(False, "--show-context")) -> None:
    """질문에 대해 SQL을 생성하고 실행한다."""
    from rich.table import Table

    from app.pipeline import ask as run_ask

    r = run_ask(question)
    console.print(f"[bold]선정 테이블[/] {r.table_names}")
    console.print(f"[bold]점수[/] {r.trace.get('scores')}")
    if show_context:
        console.print(r.context)
    if r.sql:
        console.print(f"[bold]SQL[/]\n{r.sql}")
    if r.error:
        console.print(f"[red]{r.error}[/]")
        raise typer.Exit(1)

    if r.columns:
        tbl = Table(*r.columns)
        for row in r.rows[:20]:
            tbl.add_row(*["" if v is None else str(v) for v in row])
        console.print(tbl)
    console.print(f"{len(r.rows)}행")


@app_cli.command("eval")
def eval_cmd(
    retrieval_only: bool = typer.Option(False, "--retrieval-only", help="LLM 호출 없이 검색만 평가")
) -> None:
    """평가 세트를 일괄 실행하고 지표를 표로 출력한다."""
    import yaml
    from rich.table import Table

    from app.pipeline import ask as run_ask
    from app.pipeline import retrieve

    cases = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "tests" / "questions.yaml").read_text(
            encoding="utf-8"
        )
    )

    tbl = Table("id", "질문", "기대", "실제", "R", "P", "SQL", "비고")
    recalls, precisions, sql_ok = [], [], 0

    for case in cases:
        expected = set(case["expect_tables"])
        if retrieval_only:
            _, _, names, _ = retrieve(case["question"])
            actual = {n.split(".")[-1] for n in names}
            sql_mark, note = "-", ""
        else:
            r = run_ask(case["question"])
            actual = {n.split(".")[-1] for n in r.table_names}
            # 무관 질문은 SQL을 만들지 않는 것이 정답이다.
            if expected:
                ok = r.error is None and r.sql is not None
            else:
                ok = r.sql is None
            sql_ok += int(ok)
            sql_mark = "O" if ok else "X"
            note = (r.error or "")[:40]

        if not expected:
            recall = 1.0 if not actual else 0.0
            precision = recall
        else:
            hit = expected & actual
            recall = len(hit) / len(expected)
            precision = len(hit) / len(actual) if actual else 0.0
        recalls.append(recall)
        precisions.append(precision)

        tbl.add_row(
            str(case["id"]), case["question"][:22],
            ",".join(sorted(expected)) or "(없음)",
            ",".join(sorted(actual)) or "(없음)",
            f"{recall:.2f}", f"{precision:.2f}", sql_mark, note,
        )

    console.print(tbl)
    n = len(cases)
    console.print(
        f"[bold]평균 Recall[/] {sum(recalls)/n:.3f}   "
        f"[bold]평균 Precision[/] {sum(precisions)/n:.3f}   "
        f"[bold]SQL 성공[/] {sql_ok}/{n}"
    )


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass  # 리다이렉트된 스트림 등 reconfigure 불가한 경우는 무시한다
    app_cli()


if __name__ == "__main__":
    main()
