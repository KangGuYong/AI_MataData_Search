"""eval 명령이 백엔드 호출 실패 한 건으로 전체 평가를 중단하지 않는지 검증한다."""

import httpx
from typer.testing import CliRunner

from app import cli

runner = CliRunner()


def test_백엔드가_계속_실패해도_8문항을_끝까지_평가한다(monkeypatch):
    def _fail(question: str) -> dict:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cli, "_ask_api", _fail)

    result = runner.invoke(cli.app_cli, ["eval"])

    # app/pipeline.py의 ask()가 LLM 실패를 AskResult.error로 흡수해 다음 문항을
    # 계속 진행하는 것과 같은 이유로, 백엔드 호출 실패도 해당 문항만 실패로 적고
    # 루프를 끝까지 돈다. 중간에 typer.Exit(1)로 멈췄다면 exit_code가 1이고
    # 8행 중 일부만 출력됐을 것이다.
    assert result.exit_code == 0, result.output

    # 단언은 ASCII 조각으로만 한다. Windows 콘솔 인코딩에서는 CliRunner가 캡처한
    # 한글이 깨지고, rich 테이블은 폭에 따라 줄바꿈되므로 서식도 믿을 수 없다.
    assert "0/8" in result.output          # SQL 성공 집계
    assert result.output.count("ConnectError") == 8
