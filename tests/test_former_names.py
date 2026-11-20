"""회사의 옛 이름을 공시 원문에서 읽어 별칭으로 넣는 규칙 (company_graph/former_names.py)."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from company_graph import former_names, query
from company_graph.db import Base, Company, CompanyAlias, Document
from company_graph.names import normalize


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for cid, code, name, cls in [(1, "00503668", "LIG디펜스앤에어로스페이스", "Y"), (2, "00100001", "동아쏘시오홀딩스", "Y"),
                                     (3, None, "동아제약", None), (4, "00200001", "분할한가", "K"), (5, "00200002", "분할한나", "K")]:
            s.add(Company(company_id=cid, corp_code=code, name=name, corp_cls=cls, in_scope=True))
            s.add(CompanyAlias(alias=normalize(name), company_id=cid, source="auto"))
        s.flush()
        s.add(Document(rcept_no="20240102800082", company_id=1, report_nm="단일판매ㆍ공급계약체결", doc_type="supply_contract",
                       rcept_dt=date(2024, 1, 2), fetched_at=datetime.now()))
        s.commit()
        yield s


def test_old_names_become_aliases_only_when_one_company_used_them(db):
    by_code = {"00503668": {"엘아이지넥스원 주식회사"}, "00100001": {"동아제약(주)"},   # 동아제약은 지금 다른 회사 이름이다
               "00200001": {"(주)옛이름"}, "00200002": {"옛이름 주식회사"}}             # 둘이 나눠 쓴 이름
    added = former_names.add(db, by_code, {"20240102800082": "LIG넥스원", "20240102800999": "모르는회사"})
    assert sorted(added) == [("LIG디펜스앤에어로스페이스", "LIG넥스원"), ("LIG디펜스앤에어로스페이스", "엘아이지넥스원 주식회사")]
    assert [c.company_id for c in query.find_companies(db, "LIG넥스원")] == [1]
    assert [c.company_id for c in query.find_companies(db, "동아제약")] == [3]
    assert former_names.add(db, by_code, {"20240102800082": "LIG넥스원"}) == []   # 두 번 돌려도 같다
