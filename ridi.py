# -*- coding: utf-8 -*-
"""
리디북스(RIDI Books) 검색 연동 모듈.

unified_book 플러그인의 aladin.py / nlk.py / google.py / kyobo.py와 동일한
인터페이스(search_ridi / search_ridi_isbn -> item dict 리스트)를 따른다.

리디북스 검색결과 페이지(https://ridibooks.com/search?...)는 Next.js 앱이며,
페이지 안의 `<script id="__NEXT_DATA__" type="application/json">`에 그
페이지가 필요로 하는 데이터가 통째로 JSON으로 박혀 있다. HTML을 CSS
클래스로 긁는 대신 이 JSON을 직접 파싱하므로(파싱 로직은
yume-script/ridi_book, BookOasis 이식판 기준), 사이트의 시각적 마크업이
바뀌어도 이 JSON 스키마 자체가 유지되는 한 계속 동작한다.

알려진 한계: 리디북스 검색결과 JSON에는 ISBN과 출간일(pubDate)이 포함되어
있지 않다(전자책/웹소설/웹툰 위주 플랫폼이라 종이책만큼 서지 데이터가
표준화되어 있지 않음). 이 모듈은 얻을 수 있는 정보(제목/저자(역할 포함)/
출판사/책소개/가격/표지/링크)만으로 항목을 구성하며 isbn/pubDate는 빈 값
으로 둔다. 표지는 책 ID로 CDN URL을 직접 구성한다:
https://img.ridicdn.net/cover/<ID>/xxlarge

ISBN이 없다는 특성상 search_ridi_isbn()은 unified_book.py의 ISBN 모드
필터(compare_isbns)를 통과할 수 없어 항상 빈 결과로 걸러지므로, 불필요한
요청을 줄이기 위해 곧바로 빈 리스트를 반환한다.
"""
import json
import re
from urllib.parse import quote

import requests

SEARCH_BASE_URL = "https://ridibooks.com/search"
COVER_URL_TEMPLATE = "https://img.ridicdn.net/cover/{book_id}/{size}"
BOOK_LINK_TEMPLATE = "https://ridibooks.com/books/{book_id}"

REQUEST_TIMEOUT = 10
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}

# 검색결과 중 실제로 item으로 변환할 최대 개수
MAX_RESULTS = 10
# 검색 대상 탭 (BOOK=도서, ALL=전체, COMIC=만화, WEBTOON=웹툰,
# WEBNOVEL=웹소설, LIGHT_NOVEL=라이트노벨)
SEARCH_TAB = "BOOK"
# 성인 콘텐츠 포함 여부
INCLUDE_ADULT = False

PRIMARY_AUTHOR_ROLES = ("AUTHOR",)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL)


def _log(msg):
    print(f"[Ridi] {msg}")


# ----------------------------------------------------------------------
# __NEXT_DATA__ 추출 및 book -> item 변환
# ----------------------------------------------------------------------

def _extract_next_data(html):
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (ValueError, TypeError):
        return None


def _extract_books(next_data):
    """__NEXT_DATA__에서 SearchBookListWithTab 셀의 books 배열을 찾는다."""
    try:
        cells = (next_data["props"]["pageProps"]["gridData"]
                  ["riGrid"]["grid"]["cells"])
    except (KeyError, TypeError):
        return []

    for cell in cells:
        if cell.get("type") == "SearchBookListWithTab":
            payload = cell.get("cell__SearchBookListWithTab") or {}
            return payload.get("books") or []
    return []


def _book_to_item(entry, get_all_authors=False):
    book = entry.get("book") or {}
    book_id = str(book.get("id") or entry.get("id") or "").strip()
    if not book_id:
        return None

    title = ((book.get("title") or {}).get("main") or "").strip()
    if not title:
        return None

    authors = book.get("authors") or []
    if get_all_authors:
        names = [a.get("name", "").strip() for a in authors if a.get("name")]
    else:
        names = [a.get("name", "").strip() for a in authors
                  if a.get("name") and (a.get("role") in PRIMARY_AUTHOR_ROLES)]
        if not names and authors:
            # 전부 번역가/삽화가 등 부차 역할뿐이면 첫 번째 인물로 대체
            first_name = authors[0].get("name", "").strip()
            if first_name:
                names = [first_name]

    publisher = ((book.get("publicationInfo") or {}).get("name") or "").strip()
    description = ((book.get("introduction") or {}).get("description") or "").strip()

    price_info = ((book.get("priceInfo") or {}).get("purchase") or {})
    selling_price = price_info.get("sellingPrice")
    full_price = price_info.get("fullPrice")
    if selling_price is not None:
        price_line = f"정가 {full_price}원" if full_price and full_price != selling_price else ""
        price_text = f"판매가 {selling_price}원" + (f" ({price_line})" if price_line else "")
        description = (description + "\n\n[가격] " + price_text) if description else "[가격] " + price_text

    cover_url = COVER_URL_TEMPLATE.format(book_id=book_id, size="xxlarge")
    link = BOOK_LINK_TEMPLATE.format(book_id=book_id)

    return {
        "title": title,
        "author": ", ".join(names),
        "publisher": publisher,
        "description": description,
        "isbn": "",  # 리디북스 검색결과에는 ISBN이 노출되지 않음
        "cover": cover_url,
        "link": link,
        "source": "리디북스",
    }


# ----------------------------------------------------------------------
# 검색 요청
# ----------------------------------------------------------------------

def _fetch_search_page(query, search_tab=SEARCH_TAB, include_adult=INCLUDE_ADULT):
    params = {
        "q": query,
        "adult_exclude": "n" if include_adult else "y",
        "page": "1",
    }
    if search_tab and search_tab != "ALL":
        params["tab"] = search_tab

    query_string = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"{SEARCH_BASE_URL}?{query_string}"
    _log(f"검색 요청: {url}")

    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    raw = resp.text
    _log(f"검색 응답 status={resp.status_code} 본문 길이={len(raw)}자")
    return raw


# ----------------------------------------------------------------------
# 공개 인터페이스: aladin.py / nlk.py / google.py / kyobo.py와 동일한 계약
# ----------------------------------------------------------------------

def search_ridi(query, api_key=None):
    """제목/키워드 검색. api_key는 다른 소스와 인터페이스를 맞추기 위한
    자리이며 실제로 사용하지 않는다 (리디북스는 별도 인증키가 필요한
    공개 검색 API가 없어 페이지 내 __NEXT_DATA__ JSON을 직접 읽는다)."""
    if not query:
        return []

    try:
        html = _fetch_search_page(query)
    except Exception as e:
        _log(f"검색 요청 실패: {e}")
        return []

    if not html:
        _log("검색 응답이 비어 있음")
        return []

    next_data = _extract_next_data(html)
    if next_data is None:
        _log("__NEXT_DATA__ 추출/파싱 실패 — 페이지 구조가 바뀌었을 수 있음")
        return []

    books = _extract_books(next_data)
    _log(f"__NEXT_DATA__에서 책 {len(books)}건 발견")

    items = []
    for entry in books:
        if len(items) >= MAX_RESULTS:
            break
        book = entry.get("book") or {}
        if not INCLUDE_ADULT and book.get("isAdultOnly"):
            continue
        item = _book_to_item(entry)
        if item:
            items.append(item)

    _log(f"최종 반환 {len(items)}건")
    return items


def search_ridi_isbn(isbn, api_key=None):
    """ISBN 검색. 리디북스 검색결과 JSON에는 ISBN이 없어 unified_book.py의
    ISBN 모드 필터(compare_isbns)를 통과할 수 없으므로, 불필요한 요청을
    줄이기 위해 곧바로 빈 리스트를 반환한다."""
    return []
