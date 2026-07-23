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
from .aladin import search_aladin, search_aladin_isbn
from .naver import search_naver, search_naver_isbn
from .google import search_google
from .utils_unified import (
    format_date, get_high_res_url, validate_isbn13, validate_isbn10, 
    compare_isbns, extract_isbn_from_epub, extract_isbn_from_pdf, get_row_val, parse_bool
)

class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "통합 도서 검색"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/refs/heads/main/",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "utils_unified.py", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    
    config_schema = [
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False, "description": "알라딘 Open API 개인 TTBKey를 입력하세요."},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False, "description": "네이버 개발자 센터 Client ID를 입력하세요."},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False, "description": "네이버 개발자 센터 Client Secret을 입력하세요."},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False, "description": "Google Books API Key를 입력하세요 (선택 사항)."},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False, "description": "체크 시, 검색어와 제목이 일치하는 결과만 필터링합니다."},
        {"key": "ISBN_FILE_SCAN", "label": "도서 파일(EPUB/PDF) 내부 ISBN 검출 시도", "type": "checkbox", "required": False, "description": "체크 시, 실제 도서 파일을 열어 판권지 속 ISBN을 추적합니다."}
    ]

    def search(self, db_type, query):
        if not query:
            return []
            
        config = self.get_plugin_config(db_type, default={})
        strict_match = parse_bool(config.get("STRICT_MATCH", False), default=False)
        isbn_file_scan = parse_bool(config.get("ISBN_FILE_SCAN", True), default=True)
        
        clean_query_base = re.sub(r'\.(epub|pdf|txt|zip|cbz|mobi|azw3|djvu|html)$', '', query, flags=re.IGNORECASE)
        clean_query_base = re.sub(r'\[.*?\]|\(.*?\)', '', clean_query_base).strip()
        if not clean_query_base:
            clean_query_base = query

        norm_query = "".join(re.findall(r'\w+', clean_query_base.replace('_', ''))).lower()
        
        clean_query = re.sub(r'[^0-9X]', '', query.upper())
        is_isbn = validate_isbn13(clean_query) or validate_isbn10(clean_query)
        search_query = clean_query if is_isbn else query
        detection_source = "INPUT" if is_isbn else None

        if not is_isbn:
            gateway = self.get_db_gateway(db_type)
            book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE title = ? LIMIT 1", (clean_query_base,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, isbn FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{clean_query_base}%",))
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
                    detection_source = "DB"
                else:
                    if isbn_file_scan:
                        file_path = get_row_val(book, 'file_path')
                        extracted_isbn, method = None, None
                        if file_path and os.path.exists(file_path):
                            ext = os.path.splitext(file_path)[1].lower()
                            if ext == '.epub':
                                extracted_isbn, method = extract_isbn_from_epub(file_path)
                            elif ext == '.pdf':
                                extracted_isbn, method = extract_isbn_from_pdf(file_path)
                        if extracted_isbn:
                            is_isbn = True
                            search_query = extracted_isbn
                            detection_source = method

        def _execute_search(sources, s_query, is_isbn_mode):
            res = []
            titles_seen = set()
            futures = {}
            with ThreadPoolExecutor(max_workers=len(sources)) as executor:
                for source_name, func, args in sources:
                    if source_name != '구글' and not all(args): 
                        continue
                    futures[executor.submit(func, s_query, *args)] = source_name
                
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
                                item['title'] = f"[{source_name}/{detection_source or 'ISBN'}] {original_title} *"
                            else:
                                item['title'] = f"[{source_name}] {original_title}"
                            
                            item['description'] = re.sub(r'^\[.*?\]\s*', '', item.get('description', '')) if 'description' in item else ''
                            res.append(item)
                            titles_seen.add(norm)
            return res

        results = []
        if is_isbn:
            sources_isbn = [
                ('알라딘', search_aladin_isbn, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_isbn, search_query, is_isbn_mode=True)

        if not results:
            sources_title = [
                ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            results = _execute_search(sources_title, clean_query_base, is_isbn_mode=False)

        return results

    def apply(self, db_type, book_id, item_data):
        gateway = self.get_db_gateway(db_type)
        try:
            book = gateway.fetch_one("SELECT file_path, library_id FROM books WHERE id = ?", (book_id,))
            if not book:
                return False, '대상 도서를 찾을 수 없습니다.'

            file_path = get_row_val(book, 'file_path')
            library_id = get_row_val(book, 'library_id')
            cover_url = item_data.get('cover')
            cover_filename = None

            if cover_url and Image:
                try:
                    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
                    covers_dir = os.path.join(base_dir, 'covers', str(library_id))
                    os.makedirs(covers_dir, exist_ok=True)
                    book_hash = hashlib.md5(os.path.basename(file_path).encode('utf-8')).hexdigest()
                    cover_filename = f"book_{book_hash}.webp"
                    dest_path = os.path.join(covers_dir, cover_filename)
                    
                    req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        img_data = response.read()
                        with Image.open(io.BytesIO(img_data)) as img:
                            img.save(dest_path, "WEBP", quality=95)
                    cover_filename = f"{library_id}/{cover_filename}"
                except:
                    cover_filename = None

            pub_date_raw = item_data.get('pubDate', '')
            clean_pub_date = pub_date_raw.split(" | ISBN:")[0].replace(" *", "").strip() if pub_date_raw else ''
            raw_isbn = item_data.get('isbn', '')
            clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper()) if raw_isbn else ''
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))

            columns_info = gateway.fetch_all("PRAGMA table_info(books)")
            columns = [col['name'].lower() for col in columns_info] if columns_info else []
            has_isbn_column = 'isbn' in columns

            if has_isbn_column:
                gateway.execute(
                    """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, isbn = COALESCE(NULLIF(?, ''), isbn), cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CASE WHEN ? IS NOT NULL AND ? != '' THEN CURRENT_TIMESTAMP ELSE cover_updated_at END
                       WHERE id = ?""",
                    (item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, clean_isbn, cover_filename, cover_filename, cover_filename, book_id)
                )
            else:
                gateway.execute(
                    """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, cover_image = COALESCE(NULLIF(?, ''), cover_image),
                       cover_updated_at = CASE WHEN ? IS NOT NULL AND ? != '' THEN CURRENT_TIMESTAMP ELSE cover_updated_at END
                       WHERE id = ?""",
                    (item_data.get('author'), item_data.get('publisher'), final_summary, 
                     item_data.get('link'), clean_pub_date, cover_filename, cover_filename, cover_filename, book_id)
                )

            return True, f'"{item_data.get("title")}" 정보가 성공적으로 적용되었습니다.'
        except Exception as e:
            return False, f'DB 업데이트 오류: {str(e)}'

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
