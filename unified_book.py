# -*- coding: utf-8 -*-
import os
import json
import urllib.request
import urllib.request as request
import urllib.parse
import re
import hashlib
import io
try:
    from PIL import Image
except ImportError:
    Image = None

from plugins.metadata.base import BaseMetadataProvider

class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "Unified BOOK Search (V20260715_05)"
    version = "V20260715_05"
    is_searchable = True

    config_schema = [
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False}
    ]

    def _format_date(self, date_str):
        """날짜 형식을 YYYY-MM-DD로 표준화"""
        if not date_str: return ""
        # 숫자만 추출
        digits = re.sub(r'\D', '', date_str)
        if len(digits) >= 8: # 20240715...
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        elif len(digits) >= 6: # 240715
            prefix = "20" if int(digits[:2]) < 50 else "19"
            return f"{prefix}{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
        elif len(digits) == 4: # 2024
            return f"{digits}-01-01"
        return date_str

    def _get_high_res_url(self, url, source):
        if not url: return url
        if source == '알라딘':
            url = url.replace('coversum.jpg', 'cover500.jpg').replace('covermid.jpg', 'cover500.jpg')
        elif source == '네이버':
            if '?' in url: url = url.split('?')[0]
        elif source == '구글':
            url = url.replace('zoom=1', 'zoom=3').replace('zoom=5', 'zoom=3')
            if 'edge=curl' in url: url = url.replace('edge=curl', '')
        return url

    def search(self, db_type, query):
        if not query: return []
        config = self.get_plugin_config(db_type, default={})
        strict_match = config.get("STRICT_MATCH", False)
        
        results = []
        titles_seen = set()
        norm_query = "".join(re.findall(r'\w+', query.replace('_', ''))).lower()

        sources = [
            ('알라딘', self._search_aladin, (config.get("ALADIN_KEY"),)),
            ('네이버', self._search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
            ('구글', self._search_google, (config.get("GOOGLE_API_KEY"),))
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
                    item['cover'] = self._get_high_res_url(item.get('cover'), source_name)
                    
                    # UI 표시용 필드 설정 (핵심: pubDate 사용)
                    item['pubDate'] = self._format_date(item.get('pubDate'))
                    item['title'] = f"[{source_name}] {original_title}"
                    
                    # 설명글에서 출처 제거 (이미 제목에 있으므로)
                    if 'description' in item:
                        item['description'] = re.sub(r'^\[.*?\]\s*', '', item['description'])

                    results.append(item)
                    titles_seen.add(norm)
        return results

    def _search_aladin(self, query, ttbkey):
        url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
        params = {'ttbkey': ttbkey, 'Query': query, 'QueryType': 'Title', 'MaxResults': 10, 'output': 'js', 'Version': '20131101'}
        try:
            with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
                res = response.read().decode('utf-8')
                if res.endswith(';'): res = res[:-1]
                data = json.loads(res)
                return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                         'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                         'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘'} 
                        for i in data.get('item', [])]
        except: return []

    def _search_naver(self, query, cid, csecret):
        url = "https://openapi.naver.com/v1/search/book_adv.json"
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_titl': query, 'display': 10})}")
        req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
        try:
            with urllib.request.urlopen(req, timeout=7) as response:
                data = json.loads(response.read().decode('utf-8'))
                return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                         'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                         'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버'} 
                        for i in data.get('items', [])]
        except: return []

    def _search_google(self, query, api_key):
        params = {'q': query, 'maxResults': 10, 'langRestrict': 'ko'}
        if api_key: params['key'] = api_key
        url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=7) as response:
                data = json.loads(response.read().decode('utf-8'))
                return [{'title': i.get('volumeInfo', {}).get('title'), 
                         'author': ", ".join(i.get('volumeInfo', {}).get('authors', [])),
                         'publisher': i.get('volumeInfo', {}).get('publisher'), 
                         'pubDate': i.get('volumeInfo', {}).get('publishedDate'),
                         'cover': i.get('volumeInfo', {}).get('imageLinks', {}).get('thumbnail'), 
                         'description': i.get('volumeInfo', {}).get('description', ''),
                         'link': i.get('volumeInfo', {}).get('previewLink'), 'source': '구글'} 
                        for i in data.get('items', [])]
        except: return []

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

            # DB 저장용 정리
            final_summary = re.sub('<[^<]+?>', '', item_data.get('description', ''))
            final_title = re.sub(r'^\[.*?\]\s*', '', item_data.get('title', ''))

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