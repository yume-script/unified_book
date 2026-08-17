# -*- coding: utf-8 -*-
"""
교보문고(KyoboBook) 검색 연동 모듈.

unified_book 플러그인의 aladin.py / nlk.py / google.py와 동일한 인터페이스
(search_kyobo / search_kyobo_isbn -> item dict 리스트)를 따른다.

교보문고에는 인증키가 필요한 공개 서지검색 API가 없어(2026-08 기준),
사이트 스크래핑 방식으로 구현했다. 검색결과 목록 파싱 및 상세페이지
파싱 로직은 yume-script/kyobobook (Calibre "KyoboBook Metadata Source
Plugin"의 BookOasis 이식판, product.kyobobook.co.kr Next.js 상세페이지
기준)을 그대로 가져와 unified_book의 함수형 인터페이스에 맞게 옮겼다.

동작 방식:
  1. https://search.kyobobook.co.kr/search?keyword=...&gbCode=TOT&target=total
     에서 검색결과 목록(제목/ISBN/저자/출판사/발행일 요약, 상세페이지 링크)을 읽는다.
     (eBook/sam/핫트랙스 등은 도메인이 달라 걸러내고 종이책만 대상으로 한다.)
  2. 목록에서 확보한 상세페이지 링크를 최대 MAX_DETAIL_FETCH개까지 열어
     표지·소개·정확한 ISBN/발행일 등을 보강한다. (전부 열면 느려지므로 제한)
  3. 상세페이지 파싱이 실패해도 목록에서 이미 확보한 정보만으로 최소한의
     항목을 구성해 반환한다 (사이트 개편으로 상세 파싱이 깨져도 검색 자체는 유지).

주의: 상품 식별자가 ISBN이 아니라 내부 상품코드(예: S000220308313)이므로,
ISBN은 상세페이지의 "기본정보" 표에서 별도로 읽는다. 이 모듈은 requests와
lxml이 필요하다 (requirements.txt 참고).
"""
import re
import contextlib
from datetime import datetime
from urllib.parse import quote, urljoin

import requests
from lxml.html import fromstring

PRODUCT_BASE_URL = 'https://product.kyobobook.co.kr'
SEARCH_BASE_URL = 'https://search.kyobobook.co.kr'
SEARCH_URL = SEARCH_BASE_URL + '/search?keyword=%s&gbCode=TOT&target=total'

REQUEST_TIMEOUT = 10
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0.0.0 Safari/537.36')
HEADERS = {'User-Agent': USER_AGENT, 'Accept-Language': 'ko-KR,ko;q=0.9'}

# 검색 결과 중 상세페이지까지 조회할 최대 개수 (전부 열면 느려짐)
MAX_DETAIL_FETCH = 5

# 저자 역할 라벨 중 "주 저자"로 취급할 것들.
PRIMARY_AUTHOR_ROLES = ('저자', '지음', '글', '저', '원작')


def _log(msg):
    print(f"[Kyobo] {msg}")


# ----------------------------------------------------------------------
# 검색결과 목록 페이지
# ----------------------------------------------------------------------

def _fetch_search_page(query):
    url = SEARCH_URL % quote(query)
    _log(f"검색 요청: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    raw = resp.text.strip()
    _log(f"검색 응답 status={resp.status_code} 본문 길이={len(raw)}자")
    if not raw:
        return None
    return fromstring(raw)


def _parse_search_results(root):
    # <main>...<ul class="prod_list"><li class="prod_item">...</li>...
    found = []
    seen_urls = set()

    scope = root.xpath('//main') or [root]
    for s in scope:
        items = s.xpath('.//ul[contains(@class,"prod_list")]/li[contains(@class,"prod_item")]')
        for li in items:
            info_links = li.xpath(
                './/div[contains(@class,"prod_name_group")]//a[@class="prod_info"]')
            if not info_links:
                continue
            a = info_links[0]
            href = a.get('href')
            if not href:
                continue
            detail_url = urljoin(PRODUCT_BASE_URL, href)
            if 'product.kyobobook.co.kr/detail/' not in detail_url:
                # eBook(ebook-product.kyobobook.co.kr) 등은 건너뜀 (종이책만 대상)
                continue
            if detail_url in seen_urls:
                continue

            title_node = a.xpath('.//span[starts-with(@id,"cmdtName_")]')
            title = re.sub(
                r'\s{2,}', ' ',
                (title_node[0].text_content() if title_node else a.text_content()).strip())
            if not title:
                continue
            seen_urls.add(detail_url)

            checkbox = li.xpath('.//input[contains(@class,"result_checkbox")]')
            isbn = checkbox[0].get('data-bid') if checkbox else None

            authors_rep, authors_all = [], []
            author_block = li.xpath('.//div[@class="prod_author_info"]')
            if author_block:
                for aa in author_block[0].xpath('.//a[contains(@class,"author")]'):
                    name = aa.text_content().strip()
                    if not name:
                        continue
                    authors_all.append(name)
                    if 'rep' in (aa.get('class') or '').split():
                        authors_rep.append(name)

            publisher, pubdate_text = None, None
            pub_node = li.xpath('.//div[@class="prod_publish"]')
            if pub_node:
                plink = pub_node[0].xpath('.//a')
                publisher = plink[0].text_content().strip() if plink else None
                dnode = pub_node[0].xpath('.//span[@class="date"]')
                pubdate_text = dnode[0].text_content().strip() if dnode else None

            found.append({
                'title': title,
                'detail_url': detail_url,
                'isbn': isbn,
                'authors_rep': authors_rep,
                'authors_all': authors_all,
                'publisher': publisher,
                'pubdate_text': pubdate_text,
            })
    return found


def _fill_from_search_hint(item, cand):
    """상세페이지 파싱 결과(item)에서 비어 있는 필드를 검색결과
    목록에서 확보한 값(cand)으로 보완한다."""
    if not item.get('author'):
        names = cand['authors_rep'] or cand['authors_all'][:1]
        if names:
            item['author'] = ', '.join(names)
    if not item.get('isbn') and cand.get('isbn'):
        item['isbn'] = cand['isbn']
    if not item.get('publisher') and cand.get('publisher'):
        item['publisher'] = cand['publisher']
    if not item.get('pubDate') and cand.get('pubdate_text'):
        pub_date = _parse_korean_date(cand['pubdate_text'])
        if pub_date:
            item['pubDate'] = pub_date.strftime('%Y-%m-%d')


def _item_from_search_hint(cand):
    """상세페이지 조회/파싱이 완전히 실패했을 때, 검색결과 목록에서
    확보한 정보만으로 최소한의 항목을 구성한다."""
    names = cand['authors_rep'] or cand['authors_all'][:1]
    item = {
        'title': cand['title'],
        'author': ', '.join(names) if names else '',
        'publisher': cand.get('publisher') or '',
        'description': '',
        'isbn': cand.get('isbn') or '',
        'cover': '',
        'link': cand['detail_url'],
        'source': '교보문고',
    }
    if cand.get('pubdate_text'):
        pub_date = _parse_korean_date(cand['pubdate_text'])
        if pub_date:
            item['pubDate'] = pub_date.strftime('%Y-%m-%d')
    return item


# ----------------------------------------------------------------------
# 상세 페이지 (product.kyobobook.co.kr/detail/<상품코드>)
# ----------------------------------------------------------------------

def _fetch_detail(url):
    _log(f"상세페이지 요청: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    raw = resp.text.strip()
    if not raw:
        return None
    root = fromstring(raw)

    book_id = _parse_book_id(url, root)
    title = _parse_title(root)
    if not title or not book_id:
        _log(f"상세 파싱 실패(title/book_id 없음): {url}")
        return None

    authors = _parse_authors(root)
    isbn, pubdate_from_table = _parse_basic_info_table(root)
    publisher, pubdate = _parse_publisher_and_date(root)
    if not pubdate:
        pubdate = pubdate_from_table
    comments = _parse_comments(root)
    cover_url = _parse_cover(root)

    item = {
        'title': title,
        'author': ', '.join(authors) if authors else '',
        'publisher': publisher or '',
        'description': comments or '',
        'isbn': isbn or '',
        'cover': cover_url or '',
        'link': url,
        'source': '교보문고',
    }
    if pubdate:
        item['pubDate'] = pubdate.strftime('%Y-%m-%d')

    _log(f"상세 파싱 성공: title={title!r} isbn={isbn!r} publisher={publisher!r}")
    return item


def _parse_book_id(url, root):
    # 상품코드는 canonical 링크 또는 URL 경로의 /detail/<코드> 부분.
    canon = root.xpath('//link[@rel="canonical"]/@href')
    for candidate_url in ([canon[0]] if canon else []) + [url]:
        match = re.search(r'/detail/([^/?#]+)', candidate_url)
        if match:
            return match.group(1)
    return None


def _parse_title(root):
    title_node = root.xpath('//span[@class="prod_title"]')
    if title_node:
        text = title_node[0].text_content().strip()
        if text:
            return text

    # og:title / <title> 폴백 ("... - 교보문고" 접미사 제거)
    og_title = root.xpath('//meta[@property="og:title"]/@content')
    if og_title:
        return re.sub(r'\s*-\s*교보문고\s*$', '', og_title[0]).strip()

    title_node = root.xpath('//title')
    if title_node:
        text = title_node[0].text_content().strip()
        return re.sub(r'\s*-\s*교보문고\s*$', '', text).strip()

    return None


def _parse_authors(root):
    # <div id="author-info"> 안의 각 span이 "이름 + 역할(저자(글)/번역/그림 등)"을 이룬다.
    author_spans = root.xpath(
        '//div[@id="author-info"]'
        '//span[contains(@class,"inline-flex") and contains(@class,"gap-1")'
        ' and .//a]')

    pairs, seen = [], set()
    for span in author_spans:
        name_nodes = span.xpath('.//a')
        if not name_nodes:
            continue
        name = re.sub(r'\s{2,}', ' ', name_nodes[0].text_content().strip())
        if not name or name in seen:
            continue
        role_nodes = span.xpath('.//span[contains(@class,"text-gray-800")]')
        role = role_nodes[0].text_content().strip() if role_nodes else ''
        seen.add(name)
        pairs.append((name, role))

    if not pairs:
        return []

    primary = [name for name, role in pairs
               if not role or any(r in role for r in PRIMARY_AUTHOR_ROLES)]
    if primary:
        return primary
    # 역할이 전부 부차 기여자뿐이면(예: 편역자만 표기) 첫 번째 인물만 사용
    return [pairs[0][0]]


def _parse_basic_info_table(root):
    # <div id="bookBasicInfo"> 표에서 ISBN / 발행(출시)일자를 읽는다.
    isbn, pubdate = None, None
    rows = root.xpath('//div[@id="bookBasicInfo"]//table//tr')
    for row in rows:
        th, td = row.xpath('.//th'), row.xpath('.//td')
        if not th or not td:
            continue
        key = th[0].text_content().strip()
        val = re.sub(r'\s{2,}', ' ', td[0].text_content().strip())
        if key == 'ISBN':
            isbn = re.sub(r'[^0-9Xx]', '', val) or val
        elif '발행' in key or '출시' in key:
            pubdate = _parse_korean_date(val)
    return isbn, pubdate


def _parse_publisher_and_date(root):
    publisher, pub_date = None, None
    pub_div = root.xpath('//div[@id="publisher-info"]')
    if pub_div:
        pub_link = pub_div[0].xpath('.//a')
        if pub_link:
            publisher = pub_link[0].text_content().strip()
        for span in pub_div[0].xpath('.//span'):
            text = span.text_content().strip()
            if re.search(r'\d{4}년', text):
                pub_date = _parse_korean_date(text)
                break
    return publisher, pub_date


def _parse_korean_date(date_text):
    if not date_text:
        return None
    year_match = re.search(r'(\d{4})년', date_text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    month, day = 1, 1
    month_match = re.search(r'(\d{1,2})월', date_text)
    if month_match:
        month = int(month_match.group(1))
        day_match = re.search(r'(\d{1,2})일', date_text)
        if day_match:
            day = int(day_match.group(1))
    with contextlib.suppress(Exception):
        return datetime(year, month, day)
    return None


def _parse_comments(root):
    # #bookSimpleIntro(짧은 홍보문구, 있을 수도/없을 수도) +
    # #bookDescription(본문 소개)을 이어붙인다. (일반 텍스트로 정제)
    parts = []
    simple = root.xpath('//div[@id="bookSimpleIntro"]')
    if simple:
        text = simple[0].text_content().strip()
        if text:
            parts.append(text)

    desc = root.xpath('//div[@id="bookDescription"]')
    if desc:
        text = desc[0].text_content().strip()
        if text:
            parts.append(text)

    comments = '\n\n'.join(parts)
    while '  ' in comments:
        comments = comments.replace('  ', ' ')
    return comments


def _parse_cover(root):
    og_image = root.xpath('//meta[@property="og:image"]/@content')
    if og_image and 'noimage' not in og_image[0]:
        return og_image[0]
    return None


# ----------------------------------------------------------------------
# 공개 인터페이스: aladin.py / nlk.py / google.py와 동일한 계약
# ----------------------------------------------------------------------

def _search_internal(query, max_detail=MAX_DETAIL_FETCH):
    """검색어(제목 또는 ISBN)로 교보문고를 검색해 item dict 리스트를 반환."""
    if not query:
        return []
    try:
        root = _fetch_search_page(query)
    except Exception as e:
        _log(f"검색 요청 실패: {e}")
        return []
    if root is None:
        _log("검색 응답이 비어 있음")
        return []

    candidates = _parse_search_results(root)
    _log(f"검색결과 후보 {len(candidates)}건 발견")
    if not candidates:
        _log("후보 0건 — 검색결과 페이지 마크업이 바뀌었거나 차단되었을 수 있음")
        return []

    items = []
    for cand in candidates:
        if len(items) >= max_detail:
            break
        detail_url = cand['detail_url']
        item = None
        try:
            item = _fetch_detail(detail_url)
        except Exception as e:
            _log(f"상세페이지 조회 실패({detail_url}): {e}")

        if item:
            _fill_from_search_hint(item, cand)
            items.append(item)
        else:
            # 상세 파싱이 실패해도 목록 정보만으로 최소 항목을 만들어 반환
            items.append(_item_from_search_hint(cand))

    _log(f"최종 반환 {len(items)}건")
    return items


def search_kyobo(query, api_key=None):
    """제목/키워드 검색. api_key는 aladin.py/nlk.py와 인터페이스를 맞추기 위한
    자리이며 실제로 사용하지 않는다 (교보문고는 별도 인증키가 필요한 공개
    서지검색 API가 없어 사이트 스크래핑 방식을 사용한다)."""
    return _search_internal(query)


def search_kyobo_isbn(isbn, api_key=None):
    """ISBN 검색. 교보문고 검색창은 ISBN도 그대로 키워드로 검색되므로 동일한
    내부 검색 함수를 사용하고, 결과의 ISBN 일치 여부는 unified_book.py 쪽의
    compare_isbns()가 최종적으로 걸러낸다."""
    return _search_internal(isbn, max_detail=3)
