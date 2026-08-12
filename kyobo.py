# -*- coding: utf-8 -*-
"""
교보문고(KYOBO) 연동 자리표시(placeholder) 모듈.

⚠️ 아직 실제 API 연동이 구현되지 않은 더미 파일입니다.
   추후 교보문고 쪽 공개 API(또는 스크래핑 방식)가 확정되면
   aladin.py / nlk.py와 동일한 인터페이스(search_kyobo / search_kyobo_isbn ->
   item dict 리스트)를 채워 넣어 unified_book.py의 소스 목록에 연결하면 됩니다.
   현재는 항상 빈 리스트를 반환하며, unified_book.py의 검색 로직에는
   연결되어 있지 않습니다(자동 업데이트 배포 대상 파일로만 등록됨).
"""


def search_kyobo(query, api_key=None):
    """제목 검색 (미구현 – 항상 빈 리스트)"""
    return []


def search_kyobo_isbn(isbn, api_key=None):
    """ISBN 검색 (미구현 – 항상 빈 리스트)"""
    return []
