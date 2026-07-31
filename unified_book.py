# -*- coding: utf-8 -*-
import os
import re
import urllib.request
import urllib.parse
import hashlib
import io
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
    from .utils_unified import (
        format_date, get_high_res_url, validate_isbn13, validate_isbn10, is_valid_isbn,
        compare_isbns, extract_isbn_from_epub, extract_isbn_from_pdf, extract_isbn_from_text,
        merge_book_sources, get_row_val, parse_bool
    )
except ImportError:
    _aladin_mod = _import_local_module("aladin")
    _naver_mod = _import_local_module("naver")
    _google_mod = _import_local_module("google")
    _utils_mod = _import_local_module("utils_unified")

    search_aladin = _aladin_mod.search_aladin
    search_aladin_isbn = _aladin_mod.search_aladin_isbn
    search_naver = _naver_mod.search_naver
    search_naver_isbn = _naver_mod.search_naver_isbn
    search_google = _google_mod.search_google

    format_date = _utils_mod.format_date
    get_high_res_url = _utils_mod.get_high_res_url
    validate_isbn13 = _utils_mod.validate_isbn13
    validate_isbn10 = _utils_mod.validate_isbn10
    is_valid_isbn = _utils_mod.is_valid_isbn
    compare_isbns = _utils_mod.compare_isbns
    extract_isbn_from_epub = _utils_mod.extract_isbn_from_epub
    extract_isbn_from_pdf = _utils_mod.extract_isbn_from_pdf
    extract_isbn_from_text = _utils_mod.extract_isbn_from_text
    merge_book_sources = _utils_mod.merge_book_sources
    get_row_val = _utils_mod.get_row_val
    parse_bool = _utils_mod.parse_bool


# 서지 정보 병합 시 기본 우선순위 (알라딘 > 네이버 > 구글)
INFO_SOURCE_PRIORITY = ('알라딘', '네이버', '구글')
# 표지(Cover) 우선순위 설정에서 선택 가능한 값 목록
COVER_SOURCE_OPTIONS = ('알라딘', '네이버', '구글')

# ISBN 판독 출처 태그 -> 검색 결과 라벨 표기용 매핑
DETECTION_TAG_LABEL = {"INPUT": "ISBN", "DB": "DB", "LOCAL": "LOCAL", "AI": "AI"}


class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "통합 도서 검색"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/refs/heads/main/",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "utils_unified.py", "index.html", "style.css", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    config_schema = [
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "GEMINI_API_KEY", "label": "Gemini/LiteLLM API Key", "type": "text", "required": False},
        {"key": "LITELLM_ENDPOINT", "label": "LiteLLM API 주소 (선택)", "type": "text", "required": False},
        {"key": "LITELLM_MODEL", "label": "LiteLLM 모델명 (선택)", "type": "text", "required": False},
        {"key": "COVER_PRIORITY", "label": "표지(Cover) 우선 소스", "type": "select", "required": False, "options": [
            {"value": "알라딘", "label": "알라딘 우선"},
            {"value": "네이버", "label": "네이버 우선"},
            {"value": "구글", "label": "구글 우선"},
        ]},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False},
        {"key": "ISBN_FILE_SCAN", "label": "도서 파일(EPUB/PDF) 내부에서 ISBN 검출 시도", "type": "checkbox", "required": False}
    ]

    def search(self, db_type, query):
        if not query:
            return []

        config = self.get_plugin_config(db_type, default={})
        strict_match = parse_bool(config.get("STRICT_MATCH", False), default=False)
        isbn_file_scan = parse_bool(config.get("ISBN_FILE_SCAN", True), default=True)
        gemini_key = config.get("GEMINI_API_KEY", "").strip()
        llm_endpoint = config.get("LITELLM_ENDPOINT", "").strip()
        llm_model = config.get("LITELLM_MODEL", "").strip()

        cover_pref = (config.get("COVER_PRIORITY") or "알라딘").strip()
        if cover_pref not in COVER_SOURCE_OPTIONS:
            cover_pref = "알라딘"
        cover_priority = [cover_pref] + [s for s in COVER_SOURCE_OPTIONS if s != cover_pref]

        # 검색어 정밀 전처리 전개 (파일 확장자 및 대괄호/소괄호 노이즈 제거)
        clean_query_base = re.sub(r'\.(epub|pdf|txt|zip|cbz|mobi|azw3|djvu|html)$', '', query, flags=re.IGNORECASE)
        clean_query_base = re.sub(r'\[.*?\]|\(.*?\)', '', clean_query_base).strip()
        if not clean_query_base:
            clean_query_base = query

        norm_query = "".join(re.findall(r'\w+', clean_query_base.replace('_', ''))).lower()

        # 입력받은 기본 검색어가 이미 유효한 ISBN 구성인지 우선 감지
        clean_query = re.sub(r'[^0-9X]', '', query.upper())
        is_isbn = is_valid_isbn(clean_query)
        search_query = clean_query if is_isbn else query

        # 시각화 개선: ISBN 매칭이 출발한 소스 위치를 추적하기 위한 변수 정의
        detection_source = "INPUT" if is_isbn else None

        # 입력값 자체가 ISBN이 아닐 경우, 아래 1~2단계를 순서대로 시도하여 ISBN 선확보를 시도
        if not is_isbn:
            gateway = self.get_db_gateway(db_type)

            # 가공된 clean_query_base를 사용하여 DB를 검색하므로 매칭 확률과 인덱스 속도가 대폭 향상됩니다.
            book = gateway.fetch_one("SELECT file_path, isbn, link FROM books WHERE title = ? LIMIT 1", (clean_query_base,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, isbn, link FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{clean_query_base}%",))

            # 유연한 부분일치 검색 추가 가동
            if not book:
                words = [w for w in clean_query_base.split() if len(w) > 1]
                if len(words) >= 2:
                    sub_query = " ".join(words[:2])
                    book = gateway.fetch_one("SELECT file_path, isbn, link FROM books WHERE title LIKE ? LIMIT 1", (f"%{sub_query}%",))

            if book:
                file_path = get_row_val(book, 'file_path')

                # ---- 1단계: [로컬 파싱] 도서 파일(EPUB/PDF) 내부에서 직접 ISBN 추출 ----
                if isbn_file_scan and file_path and os.path.exists(file_path):
                    ext = os.path.splitext(file_path)[1].lower()
                    extracted_isbn, method = None, None
                    if ext == '.epub':
                        extracted_isbn, method = extract_isbn_from_epub(file_path, gemini_key=gemini_key, llm_endpoint=llm_endpoint, llm_model=llm_model)
                    elif ext == '.pdf':
                        extracted_isbn, method = extract_isbn_from_pdf(file_path, gemini_key=gemini_key, llm_endpoint=llm_endpoint, llm_model=llm_model)

                    if extracted_isbn:
                        is_isbn = True
                        search_query = extracted_isbn
                        detection_source = method  # 감지출처: LOCAL 또는 AI

                # ---- 2단계: [기존 DB 링크 파싱] 1단계에서 못 찾았을 경우, DB의 isbn 컬럼 및 link(서지 링크) 컬럼에서 ISBN 역추적 ----
                if not is_isbn:
                    db_isbn = get_row_val(book, 'isbn')
                    clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''

                    if is_valid_isbn(clean_db_isbn):
                        is_isbn = True
                        search_query = clean_db_isbn
                        detection_source = "DB"
                    else:
                        db_link = get_row_val(book, 'link')
                        link_isbn = extract_isbn_from_text(db_link) if db_link else None
                        if link_isbn:
                            is_isbn = True
                            search_query = link_isbn
                            detection_source = "DB"

        # ---- 내부 검색 수행 전용 헬퍼 함수 (3단계 API 호출 + 4단계 합성/정렬을 함께 수행) ----
        def _execute_search(sources, s_query, is_isbn_mode):
            raw_items = []

            # 3단계: [API 호출] API KEY가 있는 소스만 병렬로 호출 (없으면 자동 바이패스, 구글은 키 없이도 호출)
            futures = {}
            with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as executor:
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

                        original_title = item.get('title', '') or ''
                        if not is_isbn_mode and strict_match and norm_query:
                            if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                                continue

                        norm_title = "".join(re.findall(r'\w+', original_title)).lower()
                        if not norm_title:
                            continue

                        item['cover'] = get_high_res_url(item.get('cover'), source_name)
                        item['pubDate'] = format_date(item.get('pubDate'))
                        item['description'] = re.sub(r'^\[.*?\]\s*', '', item.get('description', '')) if item.get('description') else ''
                        item['title'] = original_title
                        item['_source'] = source_name
                        item['_norm_title'] = norm_title
                        raw_items.append(item)

            # ---- 4단계: [메타데이터 합성 및 정렬] (핵심) ----
            # ISBN이 일치하는 항목끼리, 그 다음 책 제목이 일치하는 항목끼리 그룹핑하여 하나의 후보로 합성
            groups = []
            for item in raw_items:
                item_isbn_clean = re.sub(r'[^0-9X]', '', str(item.get('isbn', '')).upper())
                item_isbn_valid = item_isbn_clean if is_valid_isbn(item_isbn_clean) else ''
                item_norm_title = item['_norm_title']

                target_group = None
                for g in groups:
                    if item_isbn_valid and g['isbn'] and compare_isbns(item_isbn_valid, g['isbn']):
                        target_group = g
                        break
                    if item_norm_title == g['norm_title']:
                        target_group = g
                        break

                if target_group is None:
                    target_group = {'items': {}, 'isbn': item_isbn_valid, 'norm_title': item_norm_title}
                    groups.append(target_group)
                elif item_isbn_valid and not target_group['isbn']:
                    target_group['isbn'] = item_isbn_valid

                source_name = item['_source']
                # 동일 출처에서 같은 그룹으로 중복 매칭될 경우 먼저 수집된 데이터를 유지 (선착순 우선)
                if source_name not in target_group['items']:
                    target_group['items'][source_name] = item

            merged_results = []
            for g in groups:
                merged, contributing = merge_book_sources(
                    g['items'], info_priority=INFO_SOURCE_PRIORITY, cover_priority=cover_priority
                )
                if not merged.get('title'):
                    continue

                merged_isbn_clean = re.sub(r'[^0-9X]', '', str(merged.get('isbn', '')).upper())
                has_isbn = is_valid_isbn(merged_isbn_clean)

                label_sources = "·".join(contributing)
                if is_isbn_mode:
                    tag = DETECTION_TAG_LABEL.get(detection_source, "ISBN")
                    merged['title'] = f"[{label_sources}/{tag}] {merged['title']} *"
                else:
                    star = " *" if has_isbn else ""
                    merged['title'] = f"[{label_sources}] {merged['title']}{star}"

                if merged.get('isbn'):
                    merged['pubDate'] = f"{merged.get('pubDate', '')} | ISBN: {merged['isbn']}"

                merged['_has_isbn'] = has_isbn
                merged_results.append(merged)

            # 정렬 우선순위: ISBN 일치 항목 우선, 그 다음 책 제목 일치(검색 완료 순서) 순
            merged_results.sort(key=lambda x: 0 if x.get('_has_isbn') else 1)
            for m in merged_results:
                m.pop('_has_isbn', None)

            return merged_results

        results = []

        # 3-1단계: ISBN이 확보된 경우, ISBN 기준으로 각 API 정밀 조회
        if is_isbn:
            sources_isbn = [
                ('알라딘', search_aladin_isbn, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_isbn, search_query, is_isbn_mode=True)

        # 3-2단계: ISBN이 없거나 ISBN 조회 결과가 없는 경우, 제목(+저자) 기준으로 Fallback 조회
        if not results:
            sources_title = [
                ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_title, clean_query_base, is_isbn_mode=False)

        return results

    def apply(self, db_type, book_id, item_data):
        # ---- 5단계: [사용자 최종 확정 및 DB 저장] ----
        # 표지/서지정보는 이미 search() 4단계에서 우선순위에 따라 합성이 완료된 상태이므로,
        # 여기서는 사용자가 최종 선택한 합성 결과를 그대로 books 테이블에 반영합니다.
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
                except Exception:
                    cover_filename = None

            # DB 저장용 정리 (UI용으로 임시 처리했던 ' | ISBN: ...' 및 별표(*) 정제)
            pub_date_raw = item_data.get('pubDate', '')
            clean_pub_date = pub_date_raw.split(" | ISBN:")[0].replace(" *", "").strip() if pub_date_raw else ''

            # UI용 접두사([출처...]) 및 별표(*)를 제거하여 순수 책 이름(title)만 추출
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
