# -*- coding: utf-8 -*-
"""
리디북스(RIDI Books) 검색 연동 모듈.

unified_book 플러그인의 aladin.py / nlk.py / google.py / kyobo.py와 동일한
인터페이스(search_ridi / search_ridi_isbn -> item dict 리스트)를 따른다.

이전 버전은 검색결과 페이지 안의 `__NEXT_DATA__`(Next.js) JSON을 직접 파싱하는
방식이었는데, 실제로는 페이지 구조가 이미 바뀌었거나 봇 차단(WAF)에 걸려 거의 항상
0건만 반환하고 있었을 가능성이 크다. 이번 버전은 사용자가 검증한 참고 스크립트
(ridibooks_search.py)를 기반으로, 검색결과 HTML을 requests(가능하면 curl_cffi로
브라우저 TLS 핑거프린트까지 흉내)로 받아 BeautifulSoup으로 직접 파싱하고, 상위
후보 몇 건에 대해서만 상세페이지를 추가로 열어 표지/소개/ISBN 등을 보강하는
2단계 구조를 쓴다 (kyobo.py와 동일한 패턴).

동작 방식:
  1. https://ridibooks.com/search?q=...&tab=BOOK 에서 검색결과 HTML을 받아,
     책 상세 링크(/books/{id})를 기준으로 카드를 식별하고 제목/저자/출판사 요약을 읽는다.
  2. 목록에서 확보한 상세페이지 링크를 최대 MAX_DETAIL_FETCH개까지 열어
     표지·소개·ISBN·전자책 출간일 등을 보강한다.
  3. 상세페이지 파싱이 실패해도 목록에서 이미 확보한 정보만으로 최소한의
     항목을 구성해 반환한다.

봇 차단(WAF) 대응: `curl_cffi`가 설치되어 있으면 브라우저 TLS 핑거프린트를 흉내내는
curl_cffi를 우선 사용하고(권장, requirements.txt에 포함됨), 없으면 requests
세션으로 폴백하되 홈페이지를 먼저 방문해 쿠키를 확보한 뒤 검색을 시도한다.
그래도 403이 뜬다면 요청 빈도가 너무 잦거나(서버 IP 평판 문제) 리디북스 쪽
페이지 구조가 또 바뀐 것이니 이 모듈의 파싱 로직을 다시 점검해야 한다.

도서 결정 로직 상, 리디북스는 ISBN 검색 소스로 참여하지 않는다 — 알라딘/구글만
ISBN 정밀조회에 참여한다 (기존 대화에서 확정된 스펙). `search_ridi_isbn()`은
이 규칙에 따라 항상 빈 리스트를 즉시 반환해, 불필요한 스크래핑 요청을 만들지 않는다.
"""
import re
import time
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# 403(WAF/봇 차단) 대응: curl_cffi가 설치되어 있으면 브라우저 TLS 핑거프린트를
# 흉내내는 curl_cffi를 우선 사용하고, 없으면 requests.Session으로 폴백한다.
try:
    from curl_cffi import requests as _http
    _USING_CURL_CFFI = True
except ImportError:
    import requests as _http
    _USING_CURL_CFFI = False

BASE_URL = "https://ridibooks.com"
SEARCH_URL = BASE_URL + "/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Referer": BASE_URL + "/",
}

REQUEST_TIMEOUT = 15
# tab 파라미터: COMIC(만화)/WEBTOON/WEBNOVEL/BOOK(도서)/LIGHT_NOVEL. unified_book은
# 일반 전자책 서재 관리 플러그인이므로 도서(BOOK) 탭을 기본으로 사용한다.
DEFAULT_TAB = "BOOK"
# 검색 결과 중 상세페이지까지 조회할 최대 개수 (전부 열면 느려짐, kyobo.py와 동일한 상한)
MAX_DETAIL_FETCH = 5

_session = None


def _log(msg):
    print(f"[Ridi] {msg}")


def _get_session():
    global _session
    if _session is not None:
        return _session

    if _USING_CURL_CFFI:
        _session = _http.Session(impersonate="chrome124")
        _log("curl_cffi 세션 사용 (브라우저 TLS 핑거프린트 흉내 - 봇 차단 회피)")
    else:
        _session = _http.Session()
        _session.headers.update(HEADERS)
        # 홈페이지를 먼저 방문해 쿠키(클리어런스 등)를 확보한 뒤 실제 검색을 시도
        try:
            _session.get(BASE_URL + "/", timeout=REQUEST_TIMEOUT)
            time.sleep(0.5)
        except Exception:
            pass
        _log("requests 세션 사용 (curl_cffi 미설치 - pip install curl_cffi 로 봇 차단 회피율을 높일 수 있음)")
    return _session


def _fetch(url, params=None):
    """URL을 요청하고 BeautifulSoup 객체로 반환. 403(봇 차단)은 명확한 메시지로 구분해 발생시킨다."""
    session = _get_session()
    if _USING_CURL_CFFI:
        resp = session.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    else:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 403:
        raise RuntimeError(
            "403 Forbidden(봇 차단)"
            + (" - curl_cffi 사용 중에도 차단됨(서버 IP 평판 문제일 수 있음)" if _USING_CURL_CFFI
               else " - pip install curl_cffi 로 브라우저 TLS 핑거프린트를 흉내내면 회피될 수 있음")
        )
    resp.raise_for_status()
    if not _USING_CURL_CFFI:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return BeautifulSoup(resp.text, "html.parser")


# ----------------------------------------------------------------------
# 검색결과 목록 페이지
# ----------------------------------------------------------------------

def _extract_search_results(soup, limit=MAX_DETAIL_FETCH):
    """검색결과 페이지 HTML에서 책 정보를 추출.
    책 상세 링크(/books/{id})를 기준으로 카드를 식별하고,
    같은 카드 안에서 제목/저자/출판사 텍스트를 최대한 근접 요소에서 찾는다."""
    results = {}
    order = []

    book_links = soup.select('a[href^="/books/"]')
    for a in book_links:
        href = a.get("href", "")
        m = re.match(r"^/books/(\d+)", href)
        if not m:
            continue
        book_id = m.group(1)

        text = a.get_text(strip=True)
        if book_id not in results:
            results[book_id] = {
                "book_id": book_id,
                "title": "",
                "author": "",
                "publisher": "",
                "url": urljoin(BASE_URL, f"/books/{book_id}"),
            }
            order.append(book_id)

        # 검색결과 페이지에서는 책 소개 문구도 같은 /books/{id} 링크로 감싸져 있는
        # 경우가 있어, "가장 먼저 나오는 비어있지 않은 텍스트"를 제목으로 고정한다.
        if text and not results[book_id]["title"]:
            results[book_id]["title"] = text

    # 저자/출판사 정보 보강. 카드 컨테이너를 정확히 특정하기 어려우므로,
    # 같은 <li>/<div> 조상 내에서 최대 4단계까지 위로 탐색한다.
    for book_id in order:
        anchor = soup.select_one(f'a[href^="/books/{book_id}"]')
        if not anchor:
            continue
        container = anchor.find_parent(["li", "div"])
        depth = 0
        while container is not None and depth < 4:
            all_links = container.find_all("a", href=True)

            authors = []
            publisher = ""
            for link in all_links:
                href = link.get("href", "")
                text = unescape(link.get_text(strip=True))
                if not text:
                    continue

                if href.startswith("/author/"):
                    authors.append(text)
                elif href.startswith("/search?q="):
                    is_publisher = "출판사" in href or "%EC%B6%9C%ED%8C%90%EC%82%AC" in href
                    if is_publisher and not publisher:
                        publisher = text
                    elif not is_publisher:
                        authors.append(text)

            if authors or publisher:
                if authors and not results[book_id]["author"]:
                    results[book_id]["author"] = ", ".join(dict.fromkeys(authors))
                if publisher and not results[book_id]["publisher"]:
                    results[book_id]["publisher"] = publisher
                break
            container = container.find_parent(["li", "div"])
            depth += 1

    ordered_results = [results[bid] for bid in order if results[bid]["title"]]
    return ordered_results[:limit]


# ----------------------------------------------------------------------
# 상세 페이지 (ridibooks.com/books/{id})
# ----------------------------------------------------------------------

def _extract_book_detail(soup, url):
    """책 상세 페이지에서 메타데이터를 추출."""

    def meta(name, attr="name"):
        tag = soup.find("meta", attrs={attr: name})
        return unescape(tag["content"].strip()) if tag and tag.get("content") else ""

    title = meta("og:title", "property") or meta("title")
    title = re.sub(r'\s*[-|]\s*리디(북스)?\s*$', '', title).strip() if title else title
    description = meta("og:description", "property") or meta("description")
    cover = meta("og:image", "property")
    isbn = meta("books:isbn")
    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag["href"] if canonical_tag and canonical_tag.get("href") else url

    # 저자 (상세페이지의 /author/{id} 링크들)
    author_links = soup.select('a[href^="/author/"]')
    authors = list(dict.fromkeys(unescape(a.get_text(strip=True)) for a in author_links if a.get_text(strip=True)))

    # 출판사 (검색 링크 중 "출판사:" 포함)
    publisher = ""
    for a in soup.select('a[href^="/search?q="]'):
        href = a.get("href", "")
        if "%EC%B6%9C%ED%8C%90%EC%82%AC" in href or "출판사:" in href:
            publisher = unescape(a.get_text(strip=True))
            break

    # 본문 텍스트 전체에서 ISBN/전자책 출간일 등 보조 추출 (meta 태그에 없을 때 대비)
    page_text = soup.get_text("\n", strip=True)

    def find_after(label):
        m = re.search(re.escape(label) + r"\s*\n?\s*([^\n]+)", page_text)
        return m.group(1).strip() if m else ""

    isbn = isbn or find_after("ISBN")

    ebook_date_match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s*전자책\s*출간", page_text)
    ebook_release_date = f"{ebook_date_match.group(1)}-{ebook_date_match.group(2)}-{ebook_date_match.group(3)}" if ebook_date_match else ""

    return {
        "title": title,
        "authors": authors,
        "publisher": publisher,
        "isbn": isbn,
        "description": description,
        "cover_image": cover,
        "ebook_release_date": ebook_release_date,
        "source_url": canonical_url,
    }


def _candidate_to_item(cand):
    """상세페이지 조회/파싱이 실패했을 때, 검색결과 목록 정보만으로 최소 항목을 구성한다."""
    return {
        "title": cand["title"],
        "author": cand.get("author", ""),
        "publisher": cand.get("publisher", ""),
        "description": "",
        "isbn": "",
        "cover": "",
        "link": cand["url"],
        "source": "리디북스",
    }


def _detail_to_item(detail, cand):
    """상세페이지 파싱 결과(detail)를 최종 item으로 변환하고,
    비어 있는 필드는 검색결과 목록에서 확보한 값(cand)으로 보완한다."""
    item = {
        "title": detail.get("title") or cand["title"],
        "author": ", ".join(detail.get("authors") or []) or cand.get("author", ""),
        "publisher": detail.get("publisher") or cand.get("publisher", ""),
        "description": detail.get("description", ""),
        "isbn": detail.get("isbn", ""),
        "cover": detail.get("cover_image", ""),
        "link": detail.get("source_url") or cand["url"],
        "source": "리디북스",
    }
    if detail.get("ebook_release_date"):
        item["pubDate"] = detail["ebook_release_date"]
    return item


# ----------------------------------------------------------------------
# 공개 인터페이스: aladin.py / nlk.py / google.py / kyobo.py와 동일한 계약
# ----------------------------------------------------------------------

def _search_internal(query, tab=DEFAULT_TAB, max_detail=MAX_DETAIL_FETCH):
    if not query:
        return []

    params = {"q": query, "page": 1}
    if tab and tab.upper() != "ALL":
        params["tab"] = tab.upper()

    _log(f"검색 요청: {SEARCH_URL} params={params}")
    try:
        soup = _fetch(SEARCH_URL, params=params)
    except Exception as e:
        _log(f"검색 요청 실패: {e}")
        return []

    candidates = _extract_search_results(soup, limit=max_detail)
    _log(f"검색결과 후보 {len(candidates)}건 발견")
    if not candidates:
        return []

    items = []
    for cand in candidates:
        try:
            detail_soup = _fetch(cand["url"])
            detail = _extract_book_detail(detail_soup, cand["url"])
            items.append(_detail_to_item(detail, cand))
        except Exception as e:
            _log(f"상세페이지 조회 실패({cand['url']}): {e}")
            items.append(_candidate_to_item(cand))

    _log(f"최종 반환 {len(items)}건")
    return items


def search_ridi(query, api_key=None):
    """제목/키워드 검색. api_key는 다른 소스와 인터페이스를 맞추기 위한
    자리이며 실제로 사용하지 않는다 (리디북스는 별도 인증키가 필요한
    공개 검색 API가 없어 사이트 스크래핑 방식을 사용한다)."""
    return _search_internal(query)


def search_ridi_isbn(isbn, api_key=None):
    """도서 결정 로직 상, 리디북스는 ISBN 정밀조회 소스로 참여하지 않는다
    (알라딘 -> 구글 순으로만 ISBN 검색 — 이전 대화에서 확정된 스펙).
    불필요한 요청을 만들지 않기 위해 항상 빈 리스트를 즉시 반환한다."""
    return []
