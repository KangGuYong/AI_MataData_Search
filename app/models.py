from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchHit:
    """검색 경로 하나가 만들어낸 히트 1건."""
    source: str                 # 'v_col' | 'v_tbl' | 'keyword' | 'value'
    table_id: int
    column_id: int | None
    rank: int                   # 1-based
    raw_score: float
    detail: str = ""            # trace용. 예: "region='서울'"


@dataclass(frozen=True)
class TableScore:
    table_id: int
    score: float
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True)
class Edge:
    """무방향 조인 간선 1개."""
    from_table_id: int
    from_column: str
    to_table_id: int
    to_column: str


@dataclass(frozen=True)
class JoinPath:
    tables: tuple[int, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None
    reason: str | None


@dataclass
class AskResult:
    question: str
    table_ids: list[int] = field(default_factory=list)
    table_names: list[str] = field(default_factory=list)
    context: str = ""
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None
    trace: dict = field(default_factory=dict)
