"""LLM 응답에서 SQL을 뽑아내는 순수 함수 모듈."""

import re

import sqlglot
from sqlglot import exp

from app.llm.base import LLMClient
from app.sqlgen.prompt import RETRY_TEMPLATE, SYSTEM

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# 첫 문장 뒤에 이런 노드가 오면 잡음이 아니라 실제 SQL 문이다.
_REAL_STATEMENTS = (
    exp.Select, exp.Union, exp.Except, exp.Intersect,
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
    exp.TruncateTable, exp.Grant, exp.Copy, exp.Merge, exp.Command,
)


def extract_sql(text: str) -> str:
    """코드펜스나 앞뒤 설명이 섞인 응답에서 SQL만 뽑는다.

    gemma4는 수다스러워서 SQL 뒤에 인사말을 붙이는 경우가 있다. 문자열
    자르기로는 따옴표 안의 세미콜론과 구분할 수 없으므로 sqlglot으로
    파싱해 첫 문장만 취한다. 파싱이 안 되면 원문을 그대로 넘겨
    guard가 판단하게 둔다.
    """
    m = _FENCE.search(text)
    body = m.group(1) if m else text
    body = body.strip()
    m = re.search(r"\b(WITH|SELECT)\b", body, re.IGNORECASE)
    if m:
        body = body[m.start() :]
    body = body.strip().rstrip(";").strip()

    try:
        statements = [s for s in sqlglot.parse(body, read="postgres") if s is not None]
    except Exception:  # noqa: BLE001
        return body
    if not statements:
        return body

    # 뒤에 붙은 것이 진짜 SQL 문이면 잘라내지 않는다. LLM이 DDL/DML을 뱉은
    # 사건은 운영자에게 보여야 하므로 원문을 그대로 넘겨 guard가 거부하게 둔다.
    # 인사말 같은 잡음이면 첫 문장만 취한다.
    for extra in statements[1:]:
        if isinstance(extra, _REAL_STATEMENTS):
            return body

    return statements[0].sql(dialect="postgres")


def looks_like_sql(text: str) -> bool:
    """SELECT/WITH 로 시작하는지만 본다. 상세 검증은 guard가 한다."""
    return bool(re.match(r"^\s*(WITH|SELECT)\b", text or "", re.IGNORECASE))


def generate(llm: LLMClient, context: str) -> str:
    return extract_sql(llm.complete(context, system=SYSTEM))


def regenerate(llm: LLMClient, context: str, failed_sql: str, error: str) -> str:
    prompt = RETRY_TEMPLATE.format(context=context, sql=failed_sql, error=error)
    return extract_sql(llm.complete(prompt, system=SYSTEM))
