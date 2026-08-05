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
    from .aladin import search_aladin, search_aladin_isbn
    from .naver import search_naver, search_naver_isbn
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
    search_naver = _naver_mod.search_naver
    search_naver_isbn = _naver_mod.search_naver_isbn
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


# 서지정보(텍스트 필드) 병합 우선순위: 국립중앙도서관 > 알라딘 > 네이버 > 구글
INFO_PRIORITY = ["국립중앙도서관", "알라딘", "네이버", "구글"]


def _normalize_title(title):
    return "".join(re.findall(r"\w+", title or "")).lower()


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
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "nlk.py", "utils_unified.py", "index.html", "style.css", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    config_schema = [
        {"key": "NLK_CERT_KEY", "label": "국립중앙도서관 Seoji 인증키", "type": "password", "required": False},
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "GEMINI_API_KEY", "label": "Gemini/LiteLLM API Key", "type": "text", "required": False},
        {"key": "LITELLM_ENDPOINT", "label": "LiteLLM API 주소 (선택)", "type": "text", "required": False},
        {"key": "LITELLM_MODEL", "label": "LiteLLM 모델명 (선택)", "type": "text", "required": False},
        {"key": "COVER_PRIORITY_ALADIN", "label": "표지 우선순위: 알라딘 데이터가 있으면 우선 사용", "type": "checkbox", "required": False},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False},
        {"key": "ISBN_FILE_SCAN", "label": "도서 파일(EPUB/PDF) 내부에서 ISBN 검출 시도", "type": "checkbox", "required": False}
    ]

    def search(self, db_type, query):
        if not query:
            return []

        config = self.get_plugin_config(db_type, default={})
        strict_match = parse_bool(config.get("STRICT_MATCH", False), default=False)
        isbn_file_scan = parse_bool(config.get("ISBN_FILE_SCAN", True), default=True)
        cover_priority_aladin = parse_bool(config.get("COVER_PRIORITY_ALADIN", True), default=True)
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

        # ISBN이 아닐 경우, 로컬 DB 추적 및 파일 실시간 파싱을 통한 ISBN 추적 가동
        if not is_isbn:
            gateway = self.get_db_gateway(db_type)

            # 가공된 clean_query_base를 사용하여 DB를 검색하므로 매칭 확률과 인덱스 속도가 대폭 향상됩니다.
            book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE title = ? LIMIT 1", (clean_query_base,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{clean_query_base}%",))

            # 유연한 부분일치 검색 추가 가동
            if not book:
                words = [w for w in clean_query_base.split() if len(w) > 1]
                if len(words) >= 2:
                    sub_query = " ".join(words[:2])
                    book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE title LIKE ? LIMIT 1", (f"%{sub_query}%",))

            if book:
                db_isbn = get_row_val(book, 'isbn')
                clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''

                if validate_isbn13(clean_db_isbn) or validate_isbn10(clean_db_isbn):
                    is_isbn = True
                    search_query = clean_db_isbn
                    detection_source = "DB"  # 감지출처: 데이터베이스
                else:
                    # 파일 실시간 스캔 옵션이 켜져 있을 때만 EPUB/PDF의 무거운 헤더 디코딩을 진행함
                    if isbn_file_scan:
                        file_path = get_row_val(book, 'file_path')
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

        # 내부 검색 수행 전용 헬퍼 함수
        def _execute_search(sources, s_query, is_isbn_mode):
            # 1) 각 소스 API를 병렬 호출하여 원본 결과를 그대로 수집
            all_items = []
            futures = {}
            with ThreadPoolExecutor(max_workers=len(sources)) as executor:
                for source_name, func, args in sources:
                    if source_name != '구글' and not all(args):
                        continue
                    future = executor.submit(func, s_query, *args)
                    futures[future] = source_name

                for future in as_completed(futures):
                    source_name = futures[future]
                    try:
                        items = future.result()
                    except Exception:
                        continue

                    for item in items:
                        if is_isbn_mode:
                            item_isbn = item.get('isbn', '')
                            if not compare_isbns(s_query, item_isbn):
                                continue

                        original_title = item.get('title', '')
                        if not original_title:
                            continue
                        if not is_isbn_mode and strict_match and norm_query:
                            if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                                continue

                        all_items.append(item)

            if not all_items:
                return []

            # 2) [메타데이터 합성 및 정렬] 같은 책을 가리키는 결과들을 그룹으로 묶고,
            #    표지는 (옵션에 따라) 알라딘 우선, 그 외 서지정보는 국립중앙도서관 > 알라딘 > 네이버 > 구글 순으로 합성
            cover_priority_order = (["알라딘"] if cover_priority_aladin else []) + INFO_PRIORITY
            seen_src = set()
            cover_priority_order = [s for s in cover_priority_order if not (s in seen_src or seen_src.add(s))]

            groups = _group_items(all_items)

            res = []
            for g in groups:
                merged = _merge_group(g["items"], cover_priority_order, INFO_PRIORITY)
                if not merged.get("title"):
                    continue

                formatted_date = format_date(merged.get('pubDate'))
                isbn = merged.get('isbn', '')
                if isbn:
                    merged['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                else:
                    merged['pubDate'] = formatted_date

                label = merged.get('source', '') or "출처 미상"
                original_title = merged.get('title', '')
                # 💡 피드백 반영: 깔끔한 출처 레이블과 매칭 표시용 별표(*)만 타이틀 끝에 부여하도록 정리
                if is_isbn_mode:
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
                merged.pop('_sources', None)
                res.append(merged)

            # 3) 정렬 우선순위: ISBN 매칭(이미 is_isbn_mode에서 전량 필터링됨) -> 책 제목 완전일치 순
            if not is_isbn_mode and norm_query:
                res.sort(key=lambda it: 0 if norm_query == _normalize_title(re.sub(r'^\[.*?\]\s*', '', it.get('title', ''))) else 1)

            return res

        results = []

        # 1차 검색: ISBN이 확인된 경우 정밀 ISBN 검색 시도
        if is_isbn:
            sources_isbn = [
                ('알라딘', search_aladin_isbn, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),)),
                ('국립중앙도서관', search_nlk_isbn, (config.get("NLK_CERT_KEY"),)),
            ]
            results = _execute_search(sources_isbn, search_query, is_isbn_mode=True)

        # 2차 백업 검색 (Fallback):
        # ISBN 검색 결과가 없거나 실패한 경우 즉시 전처리 정제된 원래 책 제목 검색으로 Fallback 전환
        if not results:
            sources_title = [
                ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),)),
                ('국립중앙도서관', search_nlk, (config.get("NLK_CERT_KEY"),)),
            ]
            results = _execute_search(sources_title, clean_query_base, is_isbn_mode=False)

        return results

    def apply(self, db_type, book_id, item_data):
        if Image is None:
            return False, "Pillow 라이브러리가 필요합니다."

        gateway = self.get_db_gateway(db_type)
        try:
            book = gateway.fetch_one("SELECT file_path, library_id FROM books WHERE id = ?", (book_id,))
            if not book:
                return False, "도서를 찾을 수 없습니다."

            file_path = get_row_val(book, 'file_path')
            library_id = get_row_val(book, 'library_id')
            cover_url, cover_filename = item_data.get('cover'), None

            if cover_url:
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
                except: cover_filename = None

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

            return True, f"[{item_data.get('source')}] 정보가 성공적으로 적용되었습니다."
        except Exception as e:
            return False, f"적용 오류: {str(e)}"

    def get_context_menu_items(self, db_type, context):
        return [
            {
                'id': 'unified_search_link',
                'label': '통합 검색 결과 열기',
                'icon': 'fa-solid fa-magnifying-glass',
            }
        ]

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id == 'unified_search_link':
            query = context.get('book_title')
            url = f"https://search.naver.com/search.naver?where=book&query={urllib.parse.quote(query)}"
            return {
                'success': True, 
                'message': '통합 검색 페이지를 엽니다.', 
                'open_url': url
            }
        return {'success': False, 'error': '알 수 없는 액션입니다.'}
