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
        "raw_base_url": https://raw.githubusercontent.com/yume-script/unified_book/refs/heads/main/",
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
                    
                    # 1. 날짜 표준화 및 ISBN 정보 확보
                    formatted_date = format_date(item.get('pubDate'))
                    isbn = item.get('isbn', '')
                    
                    # 💡 ISBN 값이 있을 때만 뒤에 결합하고, 없을 때는 깔끔하게 출시 날짜만 단독 노출
                    if isbn:
                        item['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                    else:
                        item['pubDate'] = formatted_date
                    
                    # 2. 제목 및 소개글은 잡다한 정보가 추가되지 않은 원래의 깨끗한 텍스트 상태 유지
                    item['title'] = f"[{source_name}] {original_title}"
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

            # UI 노출용으로 뒤에 붙였던 ' | ISBN: ...' 부분에서 순수한 출간일만 분리
            pub_date_raw = item_data.get('pubDate', '')
            clean_pub_date = pub_date_raw.split(" | ISBN:")[0].strip() if pub_date_raw else ''

            # ISBN 표준화 (특수 문자 및 하이픈 제거 후 대문자 X 정렬)
            raw_isbn = item_data.get('isbn', '')
            clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper()) if raw_isbn else ''

            # 본문 가공 제거를 위한 클리닝
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))

            # 💡 [보완된 핵심 쿼리]: 
            # isbn = COALESCE(NULLIF(?, ''), isbn) 구문을 사용하여, 
            # 이번 검색 결과에 ISBN이 없을 경우 기존 DB에 저장되어 있던 소중한 기존 ISBN 데이터를 덮어씌워 지우지 않고 안전하게 보존합니다.
            gateway.execute(
                """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                   release_date = ?, isbn = COALESCE(NULLIF(?, ''), isbn), cover_image = COALESCE(NULLIF(?, ''), cover_image),
                   cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (item_data.get('author'), item_data.get('publisher'), final_summary, 
                 item_data.get('link'), clean_pub_date, clean_isbn, cover_filename, book_id)
            )

            return True, f"[{item_data.get('source')}] 정보 및 ISBN이 성공적으로 적용되었습니다."
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
