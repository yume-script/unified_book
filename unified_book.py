# -*- coding: utf-8 -*-
import os
import re
import urllib.request
import urllib.parse
import hashlib
import io
import zipfile
import json
import html
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
try:
    from PIL import Image
except ImportError:
    Image = None

from plugins.metadata.base import BaseMetadataProvider

# 임포트 섀도잉(Import Shadowing) 원천 차단 및 새로운 utils_unified 동적 로드 지원
def _import_local_module(module_name):
    import importlib.util
    current_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(current_dir, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# 임포트 안정성 확보 (패키지 로드 실패 시 경로 우회 동적 임포트 실행)
try:
    from .aladin import search_aladin, search_aladin_isbn, search_aladin_author
    from .naver import search_naver, search_naver_isbn, search_naver_author
    from .google import search_google
    from .nlk import search_nlk, search_nlk_isbn
    from .utils_unified import (
        format_date, get_high_res_url, validate_isbn13, validate_isbn10,
        compare_isbns, extract_isbn_from_epub, extract_isbn_from_pdf, get_row_val, parse_bool
    )
except ImportError:
    _aladin_mod = _import_local_module("aladin")
    _naver_mod = _import_local_module("naver")
    _google_mod = _import_local_module("google")
    _nlk_mod = _import_local_module("nlk")
    _utils_mod = _import_local_module("utils_unified")

    search_aladin = _aladin_mod.search_aladin
    search_aladin_isbn = _aladin_mod.search_aladin_isbn
    search_aladin_author = _aladin_mod.search_aladin_author
    search_naver = _naver_mod.search_naver
    search_naver_isbn = _naver_mod.search_naver_isbn
    search_naver_author = _naver_mod.search_naver_author
    search_google = _google_mod.search_google
    search_nlk = _nlk_mod.search_nlk
    search_nlk_isbn = _nlk_mod.search_nlk_isbn

    format_date = _utils_mod.format_date
    get_high_res_url = _utils_mod.get_high_res_url
    validate_isbn13 = _utils_mod.validate_isbn13
    validate_isbn10 = _utils_mod.validate_isbn10
    compare_isbns = _utils_mod.compare_isbns
    extract_isbn_from_epub = _utils_mod.extract_isbn_from_epub
    extract_isbn_from_pdf = _utils_mod.extract_isbn_from_pdf
    get_row_val = _utils_mod.get_row_val
    parse_bool = _utils_mod.parse_bool


# 서지정보(텍스트 필드) 및 표지 병합 우선순위: 알라딘 > 국립중앙도서관 > 네이버 > 구글
INFO_PRIORITY = ["알라딘", "국립중앙도서관", "네이버", "구글"]


def _normalize_title(title):
    return "".join(re.findall(r"\w+", title or "")).lower()


def _title_similar(title_a, title_b, threshold=0.35):
    """저자 축(author axis) 검색 결과 필터링용: 두 제목이 같은 책일 가능성이 있을 만큼
    비슷한지 판단한다. 한쪽 제목이 다른쪽에 포함되거나(부제/부분 표제 차이 등),
    문자 단위 유사도가 threshold 이상이면 True. 저자는 같지만 제목이 전혀 다른
    '동명 저자의 다른 책'을 걸러내기 위한 용도."""
    a = _normalize_title(title_a)
    b = _normalize_title(title_b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _clean_author_field(raw):
    """국립중앙도서관/알라딘 등의 AUTHOR 필드는 '저자 : 홍길동 ; 옮긴이 : 김철수 ;' 처럼
    역할 라벨 + 세미콜론(또는 콤마) 구분자로 오는 경우가 있어, 검색 axis에 그대로 넘기면
    다른 소스에서 저자명으로 인식하지 못한다. 역할 라벨을 떼어내고 순수 이름만
    콤마로 이어붙여 DB 저장 형식(예: '백현기,김영철')과 동일하게 정규화한다.
    번역/삽화/감수 등 저자가 아닌 역할은 제외한다."""
    if not raw:
        return ''
    exclude_roles = ('옮긴이', '역자', '번역', '그림', '삽화', '감수', '해설', '편집')
    names = []
    for part in re.split(r'[;,]', raw):
        part = part.strip().rstrip(';').strip()
        if not part:
            continue
        m = re.match(r'^([가-힣A-Za-z]+)\s*[:：]\s*(.+)$', part)
        if m:
            role, name = m.group(1).strip(), m.group(2).strip()
            if role in exclude_roles:
                continue
        else:
            name = part
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return ','.join(names)


def _isbn13_of(item):
    """item의 isbn을 정규화된 13자리로 변환(가능한 경우). 그룹핑 매칭용."""
    raw = re.sub(r"[^0-9X]", "", str(item.get("isbn") or "").upper())
    if validate_isbn13(raw):
        return raw
    if validate_isbn10(raw):
        core = "978" + raw[:9]
        total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(core))
        check = (10 - (total % 10)) % 10
        return core + str(check)
    return None


def _group_items(all_items):
    """서로 다른 소스에서 온 결과 중 같은 책을 가리키는 항목들을 하나의 그룹으로 묶는다.
    ISBN이 일치하면 최우선으로 병합하고, ISBN이 없으면 정규화된 제목 일치로 병합한다."""
    groups = []
    for item in all_items:
        if not item.get("title"):
            continue
        isbn13 = _isbn13_of(item)
        norm_t = _normalize_title(item.get("title"))

        matched = None
        if isbn13:
            for g in groups:
                if g["isbn"] == isbn13:
                    matched = g
                    break
        if not matched:
            for g in groups:
                if g["title"] == norm_t:
                    matched = g
                    break

        if matched:
            matched["items"].append(item)
            if isbn13 and not matched["isbn"]:
                matched["isbn"] = isbn13
        else:
            groups.append({"isbn": isbn13, "title": norm_t, "items": [item]})
    return groups


def _pick_by_priority(items, field, priority_order):
    """priority_order 순서대로 값이 채워진 소스를 찾아 (값, 출처)를 반환. 없으면 아무 소스에서나."""
    by_source = {}
    for it in items:
        by_source.setdefault(it.get("source"), it)
    for src in priority_order:
        it = by_source.get(src)
        if it and it.get(field):
            return it.get(field), src
    for it in items:
        if it.get(field):
            return it.get(field), it.get("source")
    return "", None


def _merge_group(items, cover_priority_order, info_priority_order):
    """한 그룹(동일 도서로 판정된 여러 소스 결과)을 우선순위에 따라 하나로 합성.
    - 표지(cover): cover_priority_order (옵션에 따라 알라딘 최우선)
    - 그 외 서지정보(title/author/publisher/pubDate/isbn/description/link): info_priority_order
      (국립중앙도서관 > 알라딘 > 네이버 > 구글)
    """
    merged = {}
    for field in ("title", "author", "publisher", "pubDate", "isbn", "description", "link"):
        val, _src = _pick_by_priority(items, field, info_priority_order)
        merged[field] = val

    cover_val, cover_src = _pick_by_priority(items, "cover", cover_priority_order)
    merged["cover"] = get_high_res_url(cover_val, cover_src) if cover_val else ""

    contributing = [src for src in info_priority_order if any(it.get("source") == src for it in items)]
    for it in items:
        src = it.get("source")
        if src and src not in contributing:
            contributing.append(src)
    merged["_sources"] = contributing
    merged["source"] = "+".join(contributing) if contributing else ""
    return merged


class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "통합 도서 검색"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/refs/heads/main/",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "nlk.py", "utils_unified.py", "settings.html", "style.css", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    config_schema = [
        {"key": "NLK_CERT_KEY", "label": "국립중앙도서관 Seoji 인증키", "type": "password", "required": False},
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        #{"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        #{"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "GEMINI_API_KEY", "label": "Gemini/LiteLLM API Key", "type": "text", "required": False},
        {"key": "LITELLM_ENDPOINT", "label": "LiteLLM API 주소 (선택)", "type": "text", "required": False},
        {"key": "LITELLM_MODEL", "label": "LiteLLM 모델명 (선택)", "type": "text", "required": False},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False},
        {"key": "ISBN_FILE_SCAN", "label": "도서 파일(EPUB/PDF) 내부에서 ISBN 검출 시도", "type": "checkbox", "required": False}
    ]

    def search(self, db_type, query):
        if not query:
            return []

        print(f"[UnifiedBook] ===== 검색 시작 | query={query!r} db_type={db_type!r} =====")

        config = self.get_plugin_config(db_type, default={})
        strict_match = parse_bool(config.get("STRICT_MATCH", False), default=False)
        isbn_file_scan = parse_bool(config.get("ISBN_FILE_SCAN", True), default=True)
        gemini_key = config.get("GEMINI_API_KEY", "").strip()
        llm_endpoint = config.get("LITELLM_ENDPOINT", "").strip()
        llm_model = config.get("LITELLM_MODEL", "").strip()

        # 검색어 정밀 전처리 전개 (파일 확장자 및 대괄호/소괄호 노이즈 제거)
        clean_query_base = re.sub(r'\.(epub|pdf|txt|zip|cbz|mobi|azw3|djvu|html)$', '', query, flags=re.IGNORECASE)
        clean_query_base = re.sub(r'\[.*?\]|\(.*?\)', '', clean_query_base).strip()
        if not clean_query_base:
            clean_query_base = query

        norm_query = "".join(re.findall(r'\w+', clean_query_base.replace('_', ''))).lower()

        # 입력받은 기본 검색어가 이미 유효한 ISBN 구성인지 우선 감지
        clean_query = re.sub(r'[^0-9X]', '', query.upper())
        is_isbn = validate_isbn13(clean_query) or validate_isbn10(clean_query)
        search_query = clean_query if is_isbn else query

        # 시각화 개선: ISBN 매칭이 출발한 소스 위치를 추적하기 위한 변수 정의
        detection_source = "INPUT" if is_isbn else None

        print(f"[UnifiedBook] [1단계:로컬 파싱] 입력값 ISBN 여부={is_isbn} "
              f"{'(search_query=' + search_query + ')' if is_isbn else '(ISBN 아님, 2단계로 진행)'}")

        # ISBN이 아닐 경우, 로컬 DB 추적 및 파일 실시간 파싱을 통한 ISBN 추적 가동
        gateway = self.get_db_gateway(db_type)
        book = None

        if is_isbn:
            # 입력값 자체가 ISBN인 경우에도, 제목/저자를 함께 쓰기 위해 DB에서 해당 ISBN 도서를 조회
            book = gateway.fetch_one("SELECT file_path, title, author, isbn FROM books WHERE isbn = ? LIMIT 1", (clean_query,))
            print(f"[UnifiedBook] [1단계:로컬 파싱] DB에서 동일 ISBN 도서 조회={'있음' if book else '없음'} (제목/저자 axis 보강용)")
        else:
            print(f"[UnifiedBook] [2단계:DB/파일 파싱] clean_query_base={clean_query_base!r} 로 books 테이블 조회 시작")

            # 가공된 clean_query_base를 사용하여 DB를 검색하므로 매칭 확률과 인덱스 속도가 대폭 향상됩니다.
            book = gateway.fetch_one("SELECT file_path, title, author, isbn FROM books WHERE title = ? LIMIT 1", (clean_query_base,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, title, author, isbn FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{clean_query_base}%",))

            # 유연한 부분일치 검색 추가 가동
            if not book:
                words = [w for w in clean_query_base.split() if len(w) > 1]
                if len(words) >= 2:
                    sub_query = " ".join(words[:2])
                    book = gateway.fetch_one("SELECT file_path, title, author, isbn FROM books WHERE title LIKE ? LIMIT 1", (f"%{sub_query}%",))

            print(f"[UnifiedBook] [2단계:DB/파일 파싱] DB 매칭 도서={'있음' if book else '없음'}")

            if book:
                db_isbn = get_row_val(book, 'isbn')
                clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''

                if validate_isbn13(clean_db_isbn) or validate_isbn10(clean_db_isbn):
                    is_isbn = True
                    search_query = clean_db_isbn
                    detection_source = "DB"  # 감지출처: 데이터베이스
                    print(f"[UnifiedBook] [2단계:DB/파일 파싱] DB에 저장된 ISBN 발견 -> search_query={search_query}")
                else:
                    # 파일 실시간 스캔 옵션이 켜져 있을 때만 EPUB/PDF의 무거운 헤더 디코딩을 진행함
                    if isbn_file_scan:
                        file_path = get_row_val(book, 'file_path')
                        print(f"[UnifiedBook] [2단계:DB/파일 파싱] DB에 ISBN 없음 -> 파일 스캔 시도: {file_path!r}")
                        extracted_isbn, method = None, None
                        if file_path and os.path.exists(file_path):
                            ext = os.path.splitext(file_path)[1].lower()
                            if ext == '.epub':
                                extracted_isbn, method = extract_isbn_from_epub(file_path, gemini_key=gemini_key, llm_endpoint=llm_endpoint, llm_model=llm_model)
                            elif ext == '.pdf':
                                extracted_isbn, method = extract_isbn_from_pdf(file_path, gemini_key=gemini_key, llm_endpoint=llm_endpoint, llm_model=llm_model)

                        if extracted_isbn:
                            is_isbn = True
                            search_query = extracted_isbn
                            detection_source = method  # 감지출처: LOCAL 또는 AI
                            print(f"[UnifiedBook] [2단계:DB/파일 파싱] 파일 스캔으로 ISBN 추출 성공 (경로: {method}) -> {extracted_isbn}")
                        else:
                            print("[UnifiedBook] [2단계:DB/파일 파싱] 파일 스캔에서도 ISBN을 찾지 못함")
                    else:
                        print("[UnifiedBook] [2단계:DB/파일 파싱] ISBN_FILE_SCAN 옵션 꺼짐 -> 파일 스캔 건너뜀")

        # ISBN이 확정된 경우, 국립중앙도서관 -> 알라딘 순으로 ISBN 정밀 조회를 선행하여
        # 이후 제목/저자 축(axis) 검색에 사용할 "정본" 제목/저자를 확보한다.
        # - 국립중앙도서관에서 ISBN이 검출되면 그 결과의 제목/저자를 정본으로 사용
        # - 국립중앙도서관에서 못 찾으면 알라딘의 ISBN 검색 결과의 제목/저자로 대체
        # - 둘 다 못 찾으면 기존처럼 DB의 title/author를 사용 (아래에서 폴백 처리)
        canonical_title, canonical_author, canonical_route = '', '', None
        if is_isbn:
            nlk_key = config.get("NLK_CERT_KEY")
            try:
                nlk_hits = search_nlk_isbn(search_query, nlk_key) if nlk_key else []
            except Exception as e:
                print(f"[UnifiedBook] [2단계:정본 조회] 국립중앙도서관 ISBN 조회 실패: {e}")
                nlk_hits = []

            if nlk_hits and nlk_hits[0].get('title'):
                canonical_title = nlk_hits[0].get('title', '')
                canonical_author = _clean_author_field(nlk_hits[0].get('author', ''))
                canonical_route = '국립중앙도서관'
                print(f"[UnifiedBook] [2단계:정본 조회] 국립중앙도서관 ISBN 검출 성공 -> "
                      f"title={canonical_title!r} author={canonical_author!r}")
            else:
                print("[UnifiedBook] [2단계:정본 조회] 국립중앙도서관 ISBN 미검출 -> 알라딘 ISBN 조회로 폴백")
                aladin_key = config.get("ALADIN_KEY")
                try:
                    aladin_hits = search_aladin_isbn(search_query, aladin_key) if aladin_key else []
                except Exception as e:
                    print(f"[UnifiedBook] [2단계:정본 조회] 알라딘 ISBN 조회 실패: {e}")
                    aladin_hits = []

                if aladin_hits and aladin_hits[0].get('title'):
                    canonical_title = aladin_hits[0].get('title', '')
                    canonical_author = _clean_author_field(aladin_hits[0].get('author', ''))
                    canonical_route = '알라딘'
                    print(f"[UnifiedBook] [2단계:정본 조회] 알라딘 ISBN 검출 성공 -> "
                          f"title={canonical_title!r} author={canonical_author!r}")
                else:
                    print("[UnifiedBook] [2단계:정본 조회] 알라딘 ISBN도 미검출 -> DB/파일 기반 제목·저자 사용")

        # 검색 축(axis) 구성: ISBN이 있으면 [ISBN, 제목, 저자] 3개, 없으면 [제목, 저자] 2개
        # (저자는 DB/정본 조회에서 값이 있을 때만 axis로 추가됨)
        raw_db_title = get_row_val(book, 'title') if book else ''
        raw_db_author = get_row_val(book, 'author') if book else ''

        # DB의 title 컬럼에 파일명 유래 태그(예: "[유민주]")가 안 지워진 채 남아있을 수 있으므로,
        # clean_query_base와 동일한 정제(대괄호/소괄호 제거)를 한 번 더 적용해서 검색 축을 보호한다.
        def _strip_brackets(text):
            cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', text or '').strip()
            return cleaned or (text or '')

        # 정본 제목/저자(국립중앙도서관 또는 알라딘 ISBN 조회 결과)가 있으면 최우선으로 사용하고,
        # 없으면 기존처럼 DB에 저장된 title/author를 사용한다.
        if canonical_title:
            title_query = _strip_brackets(canonical_title)
            author_query = _strip_brackets(canonical_author).strip() if canonical_author else ''
        else:
            title_query = _strip_brackets(raw_db_title) if raw_db_title else clean_query_base
            author_query = _clean_author_field(_strip_brackets(raw_db_author)) if raw_db_author else ''

        print(f"[UnifiedBook] [2단계:DB/파일 파싱] DB 원본 title={raw_db_title!r} author={raw_db_author!r} "
              f"정본 출처={canonical_route or '없음'} -> 정제 후 title_query={title_query!r} author_query={author_query!r}")

        # 저자가 여러 명(콤마로 구분)인 경우, 하나의 문자열로 합쳐서 검색하면
        # 각 소스 API가 "백현기,김영철" 자체를 저자명으로 인식해 매칭에 실패하므로,
        # 저자 1명당 별도의 author axis를 만들어 각각 검색한다.
        author_names = [a.strip() for a in author_query.split(',') if a.strip()] if author_query else []

        axes = []
        if is_isbn:
            axes.append(('isbn', search_query))
        axes.append(('title', title_query))
        for name in author_names:
            axes.append(('author', name))

        print(f"[UnifiedBook] [3단계 진입] 검색 축={[a[0] for a in axes]} "
              f"(ISBN={'있음' if is_isbn else '없음'}, 제목={title_query!r}, 저자={author_names or '없음'})")

        # 소스별 (제목검색함수, 저자검색함수, ISBN전용검색함수) 정의
        # 💡 예전에는 title/author 축이 둘 다 'general'(=제목검색) 함수를 공유해서,
        #    저자 축 검색조차 저자명을 "제목"으로 검색하는 바람에 결과가 안 나오는 버그가 있었다.
        #    소스별로 title/author/isbn 세 갈래를 명확히 분리해서 각 API의 저자 전용 파라미터를 사용한다.
        source_defs = {
            '알라딘': {
                'title': (search_aladin, (config.get("ALADIN_KEY"),)),
                'author': (search_aladin_author, (config.get("ALADIN_KEY"),)),
                'isbn': (search_aladin_isbn, (config.get("ALADIN_KEY"),)),
            },
            #'네이버': {
            #    'title': (search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            #    'author': (search_naver_author, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            #    'isbn': (search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            #},
            '구글': {
                'title': (search_google, (config.get("GOOGLE_API_KEY"), 'intitle')),
                'author': (search_google, (config.get("GOOGLE_API_KEY"), 'inauthor')),
                'isbn': (search_google, (config.get("GOOGLE_API_KEY"), 'isbn')),
            },
            '국립중앙도서관': {
                # search_nlk는 title로 먼저 시도하고 결과가 없으면 자동으로 author로 재시도하므로
                # author 축에 그대로 써도 저자명 검색이 정상 동작한다.
                'title': (search_nlk, (config.get("NLK_CERT_KEY"),)),
                'author': (search_nlk, (config.get("NLK_CERT_KEY"),)),
                'isbn': (search_nlk_isbn, (config.get("NLK_CERT_KEY"),)),
            },
        }

        # 내부 검색 수행 전용 헬퍼 함수: 여러 축(axis) x 여러 소스의 조합을 한 번에 병렬 호출하여 합성
        def _execute_search(axes, has_isbn):
            tasks = []
            for axis_type, axis_query in axes:
                if not axis_query:
                    continue
                for source_name, funcs in source_defs.items():
                    routed = funcs.get(axis_type)
                    if routed is None:
                        continue
                    func, args = routed
                    if source_name != '구글' and not all(args):
                        continue
                    tasks.append((source_name, axis_type, func, args, axis_query))

            requested_srcs = sorted({t[0] for t in tasks})
            print(f"[UnifiedBook] [3단계:API 병렬 호출] 총 {len(tasks)}개 작업(소스x축 조합) 실행 예정 | 참여 소스={requested_srcs}")

            all_items = []
            futures = {}
            with ThreadPoolExecutor(max_workers=max(len(tasks), 1)) as executor:
                for source_name, axis_type, func, args, axis_query in tasks:
                    future = executor.submit(func, axis_query, *args)
                    futures[future] = (source_name, axis_type, axis_query)

                for future in as_completed(futures):
                    source_name, axis_type, axis_query = futures[future]
                    try:
                        items = future.result()
                    except Exception as e:
                        print(f"[UnifiedBook] [3단계:API 병렬 호출] {source_name}({axis_type}:{axis_query!r}) 호출 실패: {e}")
                        continue

                    print(f"[UnifiedBook] [3단계:API 병렬 호출] {source_name}({axis_type}:{axis_query!r}) 원본 결과 {len(items)}건 수신")

                    kept_count = 0
                    for item in items:
                        if axis_type == 'isbn':
                            item_isbn = item.get('isbn', '')
                            if not compare_isbns(axis_query, item_isbn):
                                continue

                        original_title = item.get('title', '')
                        if not original_title:
                            continue
                        if axis_type == 'title' and strict_match and norm_query:
                            if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                                continue

                        # 저자 축(author axis)으로 찾은 결과는 저자만 같고 책 제목은 전혀 다른
                        # "동명 저자의 다른 책"이 섞여 들어오기 쉬우므로, 찾고 있는 책 제목과
                        # 너무 동떨어진 제목은 결과에서 제외한다.
                        if axis_type == 'author' and title_query:
                            if not _title_similar(original_title, title_query):
                                continue

                        all_items.append(item)
                        kept_count += 1
                    print(f"[UnifiedBook] [3단계:API 병렬 호출] {source_name}({axis_type}) 필터 통과 {kept_count}/{len(items)}건")

            print(f"[UnifiedBook] [3단계:API 병렬 호출] 전체 소스x축 합산 원본 아이템 {len(all_items)}건")

            if not all_items:
                print("[UnifiedBook] [4단계:메타데이터 합성] 수집된 아이템이 없어 종료")
                return []

            # 2) [메타데이터 합성 및 정렬] 같은 책을 가리키는 결과들을 그룹으로 묶고,
            #    표지/서지정보 모두 알라딘 > 국립중앙도서관 > 네이버 > 구글 순으로 합성
            cover_priority_order = INFO_PRIORITY

            groups = _group_items(all_items)
            print(f"[UnifiedBook] [4단계:메타데이터 합성] {len(all_items)}건 -> {len(groups)}개 그룹으로 병합 "
                  f"(표지 우선순위={cover_priority_order}, 서지 우선순위={INFO_PRIORITY})")

            res = []
            for g_idx, g in enumerate(groups):
                sources_in_group = sorted({it.get('source') for it in g['items']})
                merged = _merge_group(g["items"], cover_priority_order, INFO_PRIORITY)
                if not merged.get("title"):
                    continue
                print(f"[UnifiedBook] [4단계:메타데이터 합성] 그룹[{g_idx}] 제목={merged.get('title')!r} "
                      f"참여소스={sources_in_group} -> 채택 소스 조합={merged.get('source')!r}")

                formatted_date = format_date(merged.get('pubDate'))
                isbn = merged.get('isbn', '')
                if isbn:
                    merged['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                else:
                    merged['pubDate'] = formatted_date

                label = merged.get('source', '') or "출처 미상"
                original_title = merged.get('title', '')
                item_isbn_clean = re.sub(r'[^0-9X]', '', str(isbn or '').upper())
                is_isbn_match = has_isbn and item_isbn_clean and compare_isbns(search_query, item_isbn_clean)

                # 💡 피드백 반영: 깔끔한 출처 레이블과 매칭 표시용 별표(*)만 타이틀 끝에 부여하도록 정리
                if is_isbn_match:
                    if detection_source == "INPUT":
                        merged['title'] = f"[{label}/ISBN] {original_title} *"
                    elif detection_source == "DB":
                        merged['title'] = f"[{label}/DB] {original_title} *"
                    elif detection_source == "LOCAL":
                        merged['title'] = f"[{label}/LOCAL] {original_title} *"
                    elif detection_source == "AI":
                        merged['title'] = f"[{label}/AI] {original_title} *"
                    else:
                        merged['title'] = f"[{label}/ISBN] {original_title} *"
                else:
                    merged['title'] = f"[{label}] {original_title}"

                merged['description'] = re.sub(r'^\[.*?\]\s*', '', merged.get('description', '')) if merged.get('description') else ''
                merged['_isbn_match'] = is_isbn_match
                merged.pop('_sources', None)
                res.append(merged)

            # 3) 정렬 우선순위: ISBN 일치 그룹 우선 -> 그다음 책 제목 완전일치 순
            def _sort_key(it):
                isbn_rank = 0 if it.pop('_isbn_match', False) else 1
                title_rank = 0 if norm_query and norm_query == _normalize_title(re.sub(r'^\[.*?\]\s*', '', it.get('title', ''))) else 1
                return (isbn_rank, title_rank)
            res.sort(key=_sort_key)
            print(f"[UnifiedBook] [4단계:메타데이터 합성] 정렬 적용 (ISBN 일치 우선 -> 제목 완전일치 우선, 기준어={norm_query!r})")

            print(f"[UnifiedBook] [4단계:메타데이터 합성] 최종 결과 {len(res)}건 반환")
            return res

        results = _execute_search(axes, has_isbn=is_isbn)

        print(f"[UnifiedBook] ===== 검색 종료 | 최종 반환 {len(results)}건 =====")
        return results

    def apply(self, db_type, book_id, item_data):
        print(f"[UnifiedBook] [5단계:저장] apply 호출 | book_id={book_id!r} title={item_data.get('title')!r} source={item_data.get('source')!r}")
        if Image is None:
            print("[UnifiedBook] [5단계:저장] Pillow(PIL) 미설치로 저장 중단")
            return False, "Pillow 라이브러리가 필요합니다."

        gateway = self.get_db_gateway(db_type)
        try:
            book = gateway.fetch_one("SELECT file_path, library_id FROM books WHERE id = ?", (book_id,))
            if not book:
                print(f"[UnifiedBook] [5단계:저장] book_id={book_id} 에 해당하는 도서를 찾지 못함")
                return False, "도서를 찾을 수 없습니다."

            file_path = get_row_val(book, 'file_path')
            library_id = get_row_val(book, 'library_id')
            cover_url, cover_filename = item_data.get('cover'), None

            if cover_url:
                print(f"[UnifiedBook] [5단계:저장] 표지 다운로드 시작: {cover_url}")
                try:
                    import os
                    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
                    covers_dir = os.path.join(base_dir, 'covers', str(library_id))
                    os.makedirs(covers_dir, exist_ok=True)
                    book_hash = hashlib.md5(os.path.basename(file_path).encode('utf-8')).hexdigest()
                    cover_filename = f"book_{book_hash}.webp"
                    dest_path = os.path.join(covers_dir, cover_filename)

                    req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15) as response:
                        with Image.open(io.BytesIO(response.read())) as img:
                            img.save(dest_path, "WEBP", quality=95)
                    cover_filename = f"{library_id}/{cover_filename}"
                    print(f"[UnifiedBook] [5단계:저장] 표지 저장 완료: {cover_filename}")
                except Exception as e:
                    print(f"[UnifiedBook] [5단계:저장] 표지 저장 실패: {e}")
                    cover_filename = None

            # DB 저장용 정리 (UI용으로 임시 처리했던 ' | ISBN: ...' 및 별표(*) 정제)
            pub_date_raw = item_data.get('pubDate', '')
            clean_pub_date = pub_date_raw.split(" | ISBN:")[0].replace(" *", "").strip() if pub_date_raw else ''

            # 💡 [추가] UI용 접두사 및 별표(*)를 제거하여 순수 책 이름(title)만 추출
            raw_title = item_data.get('title', '')
            clean_title = re.sub(r'^\[.*?\]\s*', '', raw_title).replace(' *', '').strip()
            if not clean_title:
                clean_title = raw_title

            # ISBN 표준화 (특수 문자 및 하이픈 제거 후 대문자 X 정렬)
            raw_isbn = item_data.get('isbn', '')
            clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper()) if raw_isbn else ''

            # 본문 가공 제거를 위한 클리닝
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))

            # 안전 조치: DB 테이블 정보 조회하여 'isbn' 컬럼 존재 여부 동적 체크
            columns_info = gateway.fetch_all("PRAGMA table_info(books)")
            columns = [col['name'].lower() for col in columns_info] if columns_info else []
            has_isbn_column = 'isbn' in columns

            # CASE WHEN 조건문을 적용하여, 새로운 커버 이미지가 실제로 성공적으로 반영되었을 때만 cover_updated_at 갱신
            if has_isbn_column:
                gateway.execute(
                    """UPDATE books SET title = ?, author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, isbn = COALESCE(NULLIF(?, ''), isbn), cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CASE WHEN ? IS NOT NULL AND ? != '' THEN CURRENT_TIMESTAMP ELSE cover_updated_at END
                       WHERE id = ?""",
                    (clean_title, item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, clean_isbn, cover_filename, cover_filename, cover_filename, book_id)
                )
            else:
                gateway.execute(
                    """UPDATE books SET title = ?, author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CASE WHEN ? IS NOT NULL AND ? != '' THEN CURRENT_TIMESTAMP ELSE cover_updated_at END
                       WHERE id = ?""",
                    (clean_title, item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, cover_filename, cover_filename, cover_filename, book_id)
                )

            print(f"[UnifiedBook] [5단계:저장] books 테이블 UPDATE 실행 (title={clean_title!r}, isbn컬럼존재={has_isbn_column})")

            return True, f"[{item_data.get('source')}] 정보가 성공적으로 적용되었습니다."
        except Exception as e:
            print(f"[UnifiedBook] [5단계:저장] 저장 중 예외 발생: {e}")
            return False, f"적용 오류: {str(e)}"

    #def get_context_menu_items(self, db_type, context):
    #    return [
    #        {
    #            'id': 'unified_search_link',
    #            'label': '통합 검색 결과 열기',
    #            'icon': 'fa-solid fa-magnifying-glass',
    #        }
    #    ]

    #def run_context_menu_action(self, db_type, action_id, context):
    #    if action_id == 'unified_search_link':
    #        query = context.get('book_title')
    #        url = f"https://search.naver.com/search.naver?where=book&query={urllib.parse.quote(query)}"
    #        return {
    #            'success': True, 
    #            'message': '통합 검색 페이지를 엽니다.', 
    #            'open_url': url
    #        }
    #    return {'success': False, 'error': '알 수 없는 액션입니다.'}
