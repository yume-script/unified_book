# -*- coding: utf-8 -*-
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

# 상대 경로 임포트 예외 처리 (코어가 파일을 직접 dynamic load 할 때 발생하는 임포트 에러 해결)
try:
    from .aladin import search_aladin
    from .naver import search_naver
    from .google import search_google
    from .utils import format_date, get_high_res_url
except ImportError:
    import sys
    import os
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    if _current_dir not in sys.path:
        sys.path.append(_current_dir)
    from aladin import search_aladin
    from naver import search_naver
    from google import search_google
    from utils import format_date, get_high_res_url

class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "Unified BOOK Search"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/unified_book",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "utils.py", "__init__.py", "VERSION"],
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
        
        results = []
        titles_seen = set()
        norm_query = "".join(re.findall(r'\w+', query.replace('_', ''))).lower()

        sources = [
            ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
            ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
        ]

        for source_name, func, args in sources:
            if source_name != '구글' and not all(args): 
                continue
            
            for item in func(query, *args):
                original_title = item.get('title', '')
                if strict_match and norm_query:
                    if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                        continue

                norm = "".join(re.findall(r'\w+', original_title)).lower()
                if norm and norm not in titles_seen:
                    item['cover'] = get_high_res_url(item.get('cover'), source_name)
                    
                    # 1. 날짜 정밀 표준화 및 ISBN 획득
                    formatted_date = format_date(item.get('pubDate'))
                    isbn = item.get('isbn', '')
                    if isbn:
                        item['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                    else:
                        item['pubDate'] = formatted_date
                    
                    # 2. 제목은 불필요한 메타데이터 없이 원본 유지
                    item['title'] = f"[{source_name}] {original_title}"
                    
                    # 3. 소개글 본문은 중복 정보나 태그를 모두 뺀 '순수한 원본 설명글' 상태를 유지
                    item['description'] = re.sub(r'^\[.*?\]\s*', '', item.get('description', '')) if 'description' in item else ''

                    results.append(item)
                    titles_seen.add(norm)
        
        return results

    def apply(self, db_type, book_id, item_data):
        if Image is None:
            return False, "Pillow 라이브러리가 필요합니다."
            
        gateway = self.get_db_gateway(db_type)
        try:
            book = gateway.fetch_one("SELECT file_path, library_id FROM books WHERE id = ?", (book_id,))
            if not book:
                return False, "도서를 찾을 수 없습니다."

            file_path, library_id = book['file_path'], book['library_id']
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

            # 💡 DB 클리닝 1: UI용으로 덧붙여둔 ' | ISBN: ...' 문자열을 지우고 깨끗한 출간일 날짜만 추출
            pub_date_raw = item_data.get('pubDate', '')
            clean_pub_date = pub_date_raw.split(" | ISBN:")[0].strip() if pub_date_raw else ''

            # ISBN 표준화 (하이픈 제거 및 대문자 X 정렬)
            raw_isbn = item_data.get('isbn', '')
            clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper()) if raw_isbn else ''

            # 💡 DB 클리닝 2: 소개글은 원본 그대로 저장 (더 이상 메타데이터가 중복 저장되지 않음)
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))

            # 안전 조치: DB 테이블 정보 조회하여 'isbn' 컬럼 존재 여부 동적 체크
            columns_info = gateway.fetch_all("PRAGMA table_info(books)")
            columns = [col['name'].lower() for col in columns_info] if columns_info else []
            has_isbn_column = 'isbn' in columns

            if has_isbn_column:
                gateway.execute(
                    """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                       release_date = ?, isbn = ?, cover_image = COALESCE(NULLIF(?, ''), cover_image),
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