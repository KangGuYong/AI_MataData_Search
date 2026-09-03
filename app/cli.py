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


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass  # 리다이렉트된 스트림 등 reconfigure 불가한 경우는 무시한다
    app_cli()


if __name__ == "__main__":
    main()
