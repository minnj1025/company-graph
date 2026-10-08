"""Vercel이 서버리스 함수로 띄우는 진입점. 서버 본체는 company_graph/api.py 다."""
from company_graph.api import app  # noqa: F401
