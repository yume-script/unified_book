# -*- coding: utf-8 -*-
"""
국립중앙도서관 서지정보 유통지원시스템(seoji) Open API 연동 모듈
공식 신청: https://www.nl.go.kr (Open API > ISBN 서지정보)
엔드포인트: https://www.nl.go.kr/seoji/SearchApi.do
"""
import json
import re
import urllib.request
import urllib.parse

NL_ENDPOINT = "https://www.nl.go.kr/seoji/SearchApi.do"


def _request_nl(params, timeout=8):
    url = f"{NL_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode('utf-8', errors='ignore')
    return json.loads(raw)


def _parse_docs(data):
    # 응답 스키마가 문서화되어 있지 않아 흔히 쓰이는 키(docs/DOCS)를 순서대로 탐색
    docs = data.get('docs') or data.get('DOCS') or data.get('result') or []
    if not isinstance(docs, list):
        return []
    return docs


def _normalize_item(doc):
    title = doc.get('TITLE') or doc.get('title') or ''
    author = doc.get('AUTHOR') or doc.get('author') or ''
    publisher = doc.get('PUBLISHER') or doc.get('publisher') or ''
    pub_date = doc.get('PUBLISH_PREDATE') or doc.get('PUBLISH_DATE') or doc.get('publishPredate') or ''
    raw_isbn = doc.get('EA_ISBN') or doc.get('EA_ISBN13') or doc.get('isbn') or ''
    cover = doc.get('TITLE_URL') or doc.get('title_url') or ''
    description = doc.get('SUBJECT') or doc.get('BOOK_INTRODUCTION') or ''

    return {
        'title': re.sub('<[^<]+?>', '', title).strip(),
        'author': author.strip() if author else '',
        'publisher': publisher.strip() if publisher else '',
        'pubDate': pub_date,
        'cover': cover,
        'description': description,
        'link': '',
        'source': '국립중앙도서관',
        'isbn': re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
    }


def search_nl_isbn(isbn, cert_key):
    """ISBN 정밀 검색"""
    if not cert_key:
        return []
    params = {
        'cert_key': cert_key,
        'result_style': 'json',
        'page_no': 1,
        'page_size': 5,
        'isbn': isbn
    }
    try:
        data = _request_nl(params)
        docs = _parse_docs(data)
        if docs and not (docs[0].get('TITLE') or docs[0].get('title')):
            # 필드명이 예상과 다를 경우 콘솔에 원본 키를 남겨 빠른 진단을 돕는다
            print(f"[국립중앙도서관] 응답 필드명 확인 필요 - 수신된 키: {list(docs[0].keys())}")
        return [_normalize_item(doc) for doc in docs]
    except Exception as e:
        print(f"[국립중앙도서관 API 에러] isbn='{isbn}' 사유: {e}")
        return []


def search_nl(query, cert_key, author=''):
    """제목(+저자) 기반 검색"""
    if not cert_key:
        return []
    params = {
        'cert_key': cert_key,
        'result_style': 'json',
        'page_no': 1,
        'page_size': 10,
        'title': query
    }
    if author:
        params['author'] = author
    try:
        data = _request_nl(params)
        docs = _parse_docs(data)
        if docs and not (docs[0].get('TITLE') or docs[0].get('title')):
            print(f"[국립중앙도서관] 응답 필드명 확인 필요 - 수신된 키: {list(docs[0].keys())}")
        return [_normalize_item(doc) for doc in docs]
    except Exception as e:
        print(f"[국립중앙도서관 API 에러] query='{query}' 사유: {e}")
        return []
