# -*- coding: utf-8 -*-
import os
import re
import urllib.request
import urllib.parse
import hashlib
import io
try:
    from PIL import Image
except ImportError:
    Image = None

from plugins.metadata.base import BaseMetadataProvider

# 💡 임포트 섀도잉(Import Shadowing) 원천 차단 및 새로운 utils_unified 동적 로드 지원
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
        format_date, get_high_res_url, validate_isbn13, validate_isbn10, 
        compare_isbns, extract_isbn_from_epub, extract_isbn_from_pdf, get_row_val
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
    compare_isbns = _utils_mod.compare_isbns
    extract_isbn_from_epub = _utils_mod.extract_isbn_from_epub
    extract_isbn_from_pdf = _utils_mod.extract_isbn_from_pdf
    get_row_val = _utils_mod.get_row_val


class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "Unified BOOK Search"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/unified_book",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "utils_unified.py", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }

    config_schema = [
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False}
    ]

    def search(self, db_type, query):
        if not query:
            return []
            
        config = self.get_plugin_config(db_type, default={})
        strict_match = config.get("STRICT_MATCH", False)
        norm_query = "".join(re.findall(r'\w+', query.replace('_', ''))).lower()
        
        # 1. 입력받은 기본 검색어가 이미 유효한 ISBN 구성인지 우선 감지
        clean_query = re.sub(r'[^0-9X]', '', query.upper())
        is_isbn = validate_isbn13(clean_query) or validate_isbn10(clean_query)
        search_query = clean_query if is_isbn else query

        # 2. ISBN이 아닐 경우, 로컬 DB 추적 및 파일 실시간 파싱을 통한 ISBN 추적 가동
        if not is_isbn:
            gateway = self.get_db_gateway(db_type)
            
            # 이관된 get_row_val 보조 함수 기반 안전 조회 장치 작동
            book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE title = ? LIMIT 1", (query,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{query}%",))
                
            # 유연한 부분일치 검색 추가 가동
            if not book:
                words = [w for w in query.split() if len(w) > 1]
                if len(words) >= 2:
                    sub_query = " ".join(words[:2])
                    book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE title LIKE ? LIMIT 1", (f"%{sub_query}%",))
                
            if book:
                db_isbn = get_row_val(book, 'isbn')
                clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''
                
                if validate_isbn13(clean_db_isbn) or validate_isbn10(clean_db_isbn):
                    is_isbn = True
                    search_query = clean_db_isbn
                else:
                    file_path = get_row_val(book, 'file_path')
                    extracted_isbn = None
                    if file_path and os.path.exists(file_path):
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext == '.epub':
                            extracted_isbn = extract_isbn_from_epub(file_path)
                        elif ext == '.pdf':
                            extracted_isbn = extract_isbn_from_pdf(file_path)
                            
                    if extracted_isbn:
                        is_isbn = True
                        search_query = extracted_isbn

        # 3. 내부 검색 수행 전용 헬퍼 함수
        def _execute_search(sources, s_query, is_isbn_mode):
            res = []
            titles_seen = set()
            for source_name, func, args in sources:
                if source_name != '구글' and not all(args): 
                    continue
                
                for item in func(s_query, *args):
                    if is_isbn_mode:
                        item_isbn = item.get('isbn', '')
                        if not compare_isbns(s_query, item_isbn):
                            continue
                    
                    original_title = item.get('title', '')
                    if not is_isbn_mode and strict_match and norm_query:
                        if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                            continue

                    norm = "".join(re.findall(r'\w+', original_title)).lower()
                    if norm and norm not in titles_seen:
                        item['cover'] = get_high_res_url(item.get('cover'), source_name)
                        
                        formatted_date = format_date(item.get('pubDate'))
                        isbn = item.get('isbn', '')
                        if isbn:
                            item['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                        else:
                            item['pubDate'] = formatted_date
                        
                        if is_isbn_mode:
                            item['title'] = f"[{source_name}/ISBN] {original_title} *"
                        else:
                            item['title'] = f"[{source_name}] {original_title}"
                            
                        item['description'] = re.sub(r'^\[.*?\]\s*', '', item.get('description', '')) if 'description' in item else ''

                        res.append(item)
                        titles_seen.add(norm)
            return res

        results = []

        # 1차 검색: ISBN이 확인된 경우 정밀 ISBN 검색 시도
        if is_isbn:
            sources_isbn = [
                ('알라딘', search_aladin_isbn, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_isbn, search_query, is_isbn_mode=True)

        # 2차 백업 검색 (Fallback):
        # ISBN 검색 결과가 0건이거나 실패한 경우 즉시 원래 책 제목 검색으로 Fallback 전환
        if not results:
            sources_title = [
                ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_title, query, is_isbn_mode=False)

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

            # ISBN 표준화 (특수 문자 및 하이픈 제거 후 대문자 X 정렬)
            raw_isbn = item_data.get('isbn', '')
            clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper()) if raw_isbn else ''

            # 본문 가공 제거를 위한 클리닝
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))

            # 안전 조치: DB 테이블 정보 조회하여 'isbn' 컬럼 존재 여부 동적 체크
            columns_info = gateway.fetch_all("PRAGMA table_info(books)")
            columns = [col['name'].lower() for col in columns_info] if columns_info else []
            has_isbn_column = 'isbn' in columns

            if has_isbn_column:
                gateway.execute(
                    """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, isbn = COALESCE(NULLIF(?, ''), isbn), cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, clean_isbn, cover_filename, book_id)
                )
            else:
                gateway.execute(
                    """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, cover_filename, book_id)
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
