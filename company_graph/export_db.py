"""조회용 사본을 다른 DB(배포용 Postgres 등)로 옮긴다. 원본은 건드리지 않는다.

옮기는 것: 기업, 별칭, 공시 문서 목록, 지금 유효한 관계 줄(내려진 줄은 빼고), 사업 내용, 제품. 품질 기록과 원문은 옮기지 않는다.
대상 DB의 같은 표는 비우고 다시 채운다. 다만 대상에서 날마다 채워지는 표(함께 오른 무리, 그 계산에 쓰는 최근 시세)는 비우지 않고 없는 날만 채운다. 대상 주소는 환경 변수 TARGET_DB_URL 로 준다 (명령줄에 적으면 기록에 남는다).

실행: TARGET_DB_URL=postgresql+psycopg://... python -m company_graph.export_db
"""
import os
import sys

from sqlalchemy import create_engine, func, select, text

from datetime import timedelta

from .db import Base, BusinessSection, Company, CompanyAlias, Document, HotDay, PriceDaily, Product, ProductCode, Relation, get_engine

TABLES = (Company, CompanyAlias, Document, Relation, BusinessSection, Product, ProductCode)
# 대상 DB에서 날마다 채워지는 표(.github/workflows/daily.yml). 비우면 그동안 쌓인 날이 사라지므로, 대상에 없는 날만 채운다
DAILY = ((HotDay, None), (PriceDaily, 90))   # (표, 최근 며칠 치만 옮길지). 시세는 무리 계산에 쓰는 최근 것만 둔다
CHUNK = 5000


def fill_missing_days(source, target):
    for model, recent in DAILY:
        table = model.__table__
        day = table.c.trade_date
        Base.metadata.create_all(target, tables=[table])
        with source.connect() as read, target.begin() as write:
            have = set(write.execute(select(day).distinct()).scalars())
            query = select(table)
            if recent:
                query = query.where(day >= read.execute(select(func.max(day))).scalar() - timedelta(days=recent))
            rows = [dict(row) for row in read.execute(query).mappings() if row["trade_date"] not in have]
            for start in range(0, len(rows), CHUNK):
                write.execute(table.insert(), rows[start:start + CHUNK])
            print(f"{table.name}: 대상에 없던 {len({row['trade_date'] for row in rows})}일, {len(rows)}줄", flush=True)


def main():
    target_url = os.environ.get("TARGET_DB_URL")
    if not target_url:
        sys.exit("환경 변수 TARGET_DB_URL 이 없습니다")
    source, target = get_engine(), create_engine(target_url)
    wanted = [model.__table__ for model in TABLES]
    Base.metadata.drop_all(target, tables=list(reversed(wanted)))
    Base.metadata.create_all(target, tables=wanted)
    with source.connect() as read, target.begin() as write:
        for model in TABLES:
            table = model.__table__
            query = select(table).order_by(*table.primary_key.columns)
            if model is Relation:
                query = query.where(Relation.retired_at.is_(None))
            rows, total = [], 0
            for row in read.execution_options(stream_results=True).execute(query).mappings():
                rows.append(dict(row))
                if len(rows) == CHUNK:
                    write.execute(table.insert(), rows)
                    total, rows = total + len(rows), []
            if rows:
                write.execute(table.insert(), rows)
                total += len(rows)
            print(f"{table.name}: {total}줄", flush=True)
        if target.dialect.name == "postgresql":
            # 번호를 그대로 옮겼으므로 자동 증가 값을 맞춰 둔다
            for model in TABLES:
                key = list(model.__table__.primary_key.columns)[0]
                if key.name.endswith("_id"):   # 번호가 아닌 열쇠(접수번호, 이름)는 자동 증가가 없다
                    write.execute(text(f"select setval(pg_get_serial_sequence('{model.__table__.name}', '{key.name}'), "
                                       f"(select coalesce(max({key.name}), 1) from {model.__table__.name}))"))
    fill_missing_days(source, target)
    with target.connect() as check:
        size = check.execute(text("select pg_size_pretty(pg_database_size(current_database()))")).scalar() \
            if target.dialect.name == "postgresql" else "?"
        print("대상 DB 크기:", size, "| 관계 줄:", check.execute(select(func.count()).select_from(Relation.__table__)).scalar())


if __name__ == "__main__":
    main()
