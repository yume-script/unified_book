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
from .aladin import search_aladin
from .naver import search_naver
from .google import search_google
from .utils import format_date, get_high_res_url

class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "Unified BOOK Search"  # 요청하신 대로 변경
    is_searchable = True

    # 자동 업데이트를 위한 매니페스트 선언 (가이드 3절)
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
        if not query: return []
        config = self.get_plugin_config(db_type, default={})
        strict_match = config.get("STRICT_MATCH", False)
        
        results = []
        titles_seen = set()
        norm_query = "".join(re.findall(r'\w+', query.replace('_', ''))).lower()

        # 개별 import된 함수들을 리스트에 매핑
        sources = [
            ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
            ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
        ]

        for source_name, func, args in sources:
            if source_name != '구글' and not all(args): continue
            
            for item in func(query, *args):
                original_title = item.get('title', '')
                if strict_match and norm_query:
                    if norm_query not in "".join(re.findall(r'\w+', original_title.replace('_', ''))).lower():
                        continue

                norm = "".join(re.findall(r'\w+', original_title)).lower()
                if norm and norm not in titles_seen:
                    # 유틸리티 함수 사용
                    item['cover'] = get_high_res_url(item.get('cover'), source_name)
                    item['pubDate'] = format_date(item.get('pubDate'))
                    item['title'] = f"[{source_name}] {original_title}"
                    
                    if 'description' in item:
                        item['description'] = re.sub(r'^\[.*?\]\s*', '', item['description'])

                    results.append(item)
                    titles_seen.add(norm)
        return results

    def apply(self, db_type, book_id, item_data):
        if Image is None: return False, "Pillow 라이브러리가 필요합니다."
        gateway = self.get_db_gateway(db_type)
        try:
            book = gateway.fetch_one("SELECT file_path, library_id FROM books WHERE id = ?", (book_id,))
            if not book: return False, "도서를 찾을 수 없습니다."

            file_path = book['file_path']; library_id = book['library_id']
            cover_url = item_data.get('cover'); cover_filename = None

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
                        img_data = response.read()
                        with Image.open(io.BytesIO(img_data)) as img:
                            img.save(dest_path, "WEBP", quality=95)
                    cover_filename = f"{library_id}/{cover_filename}"
                except: cover_filename = None

            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))
            
            gateway.execute(
                """UPDATE books SET author = ?, publisher = ?, summary = ?, link = ?, 
                   release_date = ?, cover_image = COALESCE(NULLIF(?, ''), cover_image),
                   cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (item_data.get('author'), item_data.get('publisher'), final_summary, 
                 item_data.get('link'), item_data.get('pubDate'), cover_filename, book_id)
            )
            return True, f"[{item_data.get('source')}] 정보가 성공적으로 적용되었습니다."
        except Exception as e:
            return False, f"적용 오류: {str(e)}"
        except Exception as e:
            return False, f"적용 오류: {str(e)}"
