"""표 정의. db/schema.sql(MySQL)과 같은 구조이며, 설계 근거는 DESIGN.md 6장."""
from functools import lru_cache
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric,
                        SmallInteger, String, Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import DATA_DIR, DB_URL

REL_TYPES = ("affiliate", "equity", "supply_contract", "supply_termination", "major_customer",
             "stake_acquisition", "stake_disposal", "merger", "split", "business_transfer")
DOC_TYPES = ("annual", "half", "quarter", "supply_contract", "event", "other")
QUALITY_CHECKS = ("correction", "duplicate", "unit", "range", "cross_check", "unlinked_name")


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"
    company_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str | None] = mapped_column(String(8), unique=True)
    jurir_no: Mapped[str | None] = mapped_column(String(20), index=True)
    biz_no: Mapped[str | None] = mapped_column(String(20))
    stock_code: Mapped[str | None] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(200))
    corp_cls: Mapped[str | None] = mapped_column(String(1))
    induty_code: Mapped[str | None] = mapped_column(String(10))
    ftc_group: Mapped[str | None] = mapped_column(String(100))
    in_scope: Mapped[bool] = mapped_column(Boolean, default=False)


class CompanyAlias(Base):
    __tablename__ = "company_alias"
    __table_args__ = (UniqueConstraint("alias", "company_id"),)
    alias_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(200), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.company_id"))
    source: Mapped[str] = mapped_column(Enum("auto", "manual", name="alias_source"))


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (Index("ix_document_company_date", "company_id", "rcept_dt"),)
    rcept_no: Mapped[str] = mapped_column(String(14), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.company_id"))
    report_nm: Mapped[str] = mapped_column(String(300))
    rcept_dt: Mapped[date] = mapped_column(Date)
    doc_type: Mapped[str] = mapped_column(Enum(*DOC_TYPES, name="doc_type"))
    bsns_year: Mapped[int | None] = mapped_column(SmallInteger)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    corrects_rcept_no: Mapped[str | None] = mapped_column(String(14))
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class Relation(Base):
    __tablename__ = "relation"
    __table_args__ = (
        Index("ix_relation_subject", "subject_company_id", "rel_type", "disclosed_date"),
        Index("ix_relation_object", "object_company_id", "rel_type", "disclosed_date"),
    )
    relation_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                             autoincrement=True)
    subject_company_id: Mapped[int | None] = mapped_column(ForeignKey("company.company_id"))
    subject_name_raw: Mapped[str | None] = mapped_column(String(300))
    object_company_id: Mapped[int | None] = mapped_column(ForeignKey("company.company_id"))
    object_name_raw: Mapped[str] = mapped_column(String(300))
    rel_type: Mapped[str] = mapped_column(Enum(*REL_TYPES, name="rel_type"))
    value_num: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    value_unit: Mapped[str | None] = mapped_column(Enum("pct", "krw", name="value_unit"))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    disclosed_date: Mapped[date] = mapped_column(Date)
    invalidated_date: Mapped[date | None] = mapped_column(Date)
    rcept_no: Mapped[str] = mapped_column(ForeignKey("document.rcept_no"))
    extract_method: Mapped[str] = mapped_column(Enum("api", "rule", "llm", name="extract_method"))
    trust_tier: Mapped[int] = mapped_column(SmallInteger)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    attrs: Mapped[dict | None] = mapped_column(JSON)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime)
    extractor_version: Mapped[str | None] = mapped_column(String(40))


class PriceDaily(Base):
    __tablename__ = "price_daily"
    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    volume: Mapped[int] = mapped_column(BigInteger)


class QualityLog(Base):
    __tablename__ = "quality_log"
    log_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                        autoincrement=True)
    check_name: Mapped[str] = mapped_column(Enum(*QUALITY_CHECKS, name="quality_check"), index=True)
    rcept_no: Mapped[str | None] = mapped_column(String(14))
    detail: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AskLog(Base):
    """화면에서 Agent에게 한 질문의 기록. 하루 질문 수 상한과 방문자별 제한을 세는 데 쓴다."""
    __tablename__ = "ask_log"
    ask_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    visitor: Mapped[str] = mapped_column(String(16), index=True)   # 접속 주소를 한 방향으로 줄인 값. 주소 자체는 남기지 않는다
    question: Mapped[str] = mapped_column(String(300))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float | None] = mapped_column(Numeric(6, 1))


@lru_cache(maxsize=1)
def get_engine():
    """프로세스에 엔진은 하나. 요청마다 새로 만들면 연결이 쌓인다."""
    if DB_URL.startswith("sqlite"):
        DATA_DIR.mkdir(exist_ok=True)
        return create_engine(DB_URL)
    options = {"pool_pre_ping": True, "pool_recycle": 300}
    if DB_URL.startswith("postgresql"):
        # 배포 환경(서버리스 함수 + 연결 풀러): 함수 하나가 연결을 조금만 쥐고, 풀러가 싫어하는 준비된 구문을 쓰지 않는다
        options.update(pool_size=1, max_overflow=2, connect_args={"prepare_threshold": None})
    return create_engine(DB_URL, **options)


def init_db(engine=None):
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    return engine


def session(engine=None) -> Session:
    return Session(engine or get_engine())
