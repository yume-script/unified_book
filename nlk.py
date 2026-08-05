# -*- coding: utf-8 -*-
"""
국립중앙도서관(NLK) 서지정보 유통지원시스템(Seoji) Open API 검색 모듈.
unified_book 플러그인의 aladin.py / naver.py / google.py와 동일한 인터페이스
(search_nlk / search_nlk_isbn -> item dict 리스트)를 따른다.
"""
import json
import urllib.parse
import urllib.request

SEOJI_API_URL = "https://www.nl.go.kr/seoji/SearchApi.do"


def _clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text.replace("<span>", "").replace("</span>", "")


def _format_pub_date(yyyymmdd):
    raw = (yyyymmdd or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _doc_to_item(doc):
    title = _clean(doc.get("TITLE"))
    isbn = _clean(doc.get("EA_ISBN") or doc.get("SET_ISBN"))
    intro = _clean(doc.get("BOOK_INTRODUCTION"))

    subject = _clean(doc.get("SUBJECT"))
    kdc = _clean(doc.get("KDC"))
    edition = _clean(doc.get("EDITION_STMT"))
    form = _clean(doc.get("FORM"))
    page_info = _clean(doc.get("PAGE"))
    book_size = _clean(doc.get("BOOK_SIZE"))
    price = _clean(doc.get("PRE_PRICE"))

    biblio_parts = []
    if subject:
        biblio_parts.append(f"주제분류(KDC 대분류): {subject}")
    if kdc:
        biblio_parts.append(f"한국십진분류: {kdc}")
    if edition:
        biblio_parts.append(f"판사항: {edition}")
    if form:
        biblio_parts.append(f"형태: {form}")
    page_size_parts = " / ".join([p for p in [page_info, book_size] if p])
    if page_size_parts:
        biblio_parts.append(f"페이지/책크기: {page_size_parts}")
    if price:
        biblio_parts.append(f"예정가격: {price}")
    biblio_text = " / ".join(biblio_parts)

    if intro:
        description = intro if not biblio_text else f"{intro}\n\n[서지정보] {biblio_text}"
    else:
        description = biblio_text

    link = ""
    if isbn:
        link = "https://www.nl.go.kr/seoji/SearchDetail.do?" + urllib.parse.urlencode({"isbn": isbn})

    return {
        "title": title,
        "author": _clean(doc.get("AUTHOR")),
        "publisher": _clean(doc.get("PUBLISHER")),
        "pubDate": _format_pub_date(doc.get("PUBLISH_PREDATE")),
        "cover": _clean(doc.get("TITLE_URL")),
        "description": description,
        "link": link,
        "source": "국립중앙도서관",
        "isbn": isbn,
    }


def _call(params, timeout=7):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{SEOJI_API_URL}?{query}", headers={"User-Agent": "BookOasis-UnifiedBook/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def search_nlk(query, cert_key):
    """국립중앙도서관 일반 검색: title -> (결과 없으면) author 순으로 시도."""
    if not cert_key or not query:
        return []
    base = {"cert_key": cert_key, "result_style": "json", "page_no": 1, "page_size": 10}
    try:
        data = _call({**base, "title": query})
        docs = data.get("docs") or []
        if not docs:
            data = _call({**base, "author": query})
            docs = data.get("docs") or []
        return [_doc_to_item(doc) for doc in docs if doc.get("TITLE")]
    except Exception:
        return []


def search_nlk_isbn(isbn, cert_key):
    """국립중앙도서관 ISBN 전용 검색: isbn -> (결과 없으면) set_isbn 순으로 시도."""
    if not cert_key or not isbn:
        return []
    base = {"cert_key": cert_key, "result_style": "json", "page_no": 1, "page_size": 5}
    try:
        data = _call({**base, "isbn": isbn})
        docs = data.get("docs") or []
        if not docs:
            data = _call({**base, "set_isbn": isbn})
            docs = data.get("docs") or []
        return [_doc_to_item(doc) for doc in docs if doc.get("TITLE")]
    except Exception:
        return []
