# -*- coding: utf-8 -*-
import os
import re
import urllib.request
import urllib.parse
import hashlib
import io
import zipfile
import json
import html  # HTML 엔티티 디코딩용 내장 라이브러리
import xml.etree.ElementTree as ET
try:
    from PIL import Image
except ImportError:
    Image = None

from plugins.metadata.base import BaseMetadataProvider

# 💡 임포트 섀도잉(Import Shadowing) 원천 차단: 절대 경로 기준 동적 모듈 로드 함수 정의
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
    from .aladin import search_aladin
    from .naver import search_naver
    from .google import search_google
    from .utils import format_date, get_high_res_url
except ImportError:
    _aladin_mod = _import_local_module("aladin")
    _naver_mod = _import_local_module("naver")
    _google_mod = _import_local_module("google")
    _utils_mod = _import_local_module("utils")
    
    search_aladin = _aladin_mod.search_aladin
    search_naver = _naver_mod.search_naver
    search_google = _google_mod.search_google
    format_date = _utils_mod.format_date
    get_high_res_url = _utils_mod.get_high_res_url


# ==========================================
# 💡 문서(EPUB/PDF) 기반 ISBN 정밀 추출용 내부 함수 및 검증기
# ==========================================

def validate_isbn13(isbn):
    """ISBN-13 체크디지트 검사 (Mod 10 방식)"""
    if len(isbn) != 13:
        return False
    try:
        digits = [int(char) for char in isbn]
        checksum = sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
        return checksum % 10 == 0
    except ValueError:
        return False

def validate_isbn10(isbn):
    """ISBN-10 체크디지트 검사 (Mod 11 방식)"""
    if len(isbn) != 10:
        return False
    try:
        val = 0
        for i in range(9):
            val += int(isbn[i]) * (10 - i)
        last = isbn[9]
        if last == 'X':
            val += 10
        else:
            val += int(last)
        return val % 11 == 0
    except ValueError:
        return False

def compare_isbns(isbn_a, isbn_b):
    """10자리와 13자리 ISBN의 형식을 정규화하여 상호 교차 대조"""
    clean_a = re.sub(r'[^0-9X]', '', str(isbn_a or '').upper())
    clean_b = re.sub(r'[^0-9X]', '', str(isbn_b or '').upper())
    
    if not clean_a or not clean_b:
        return False
    if clean_a == clean_b:
        return True
        
    # 10자리와 13자리가 섞여 들어왔을 때 핵심 서지 번호(9자리) 일치 여부 판별
    if len(clean_a) == 13 and len(clean_b) == 10:
        return clean_a[3:12] == clean_b[0:9]
    if len(clean_a) == 10 and len(clean_b) == 13:
        return clean_a[0:9] == clean_b[3:12]
        
    return False

def extract_isbn_from_epub(epub_path):
    """EPUB 내부 컨테이너 구조 및 본문 파일 분석 후 ISBN 추출 (엔티티 복원 및 듀얼 스캔 고도화)"""
    try:
        with zipfile.ZipFile(epub_path, 'r') as epub:
            container_content = epub.read('META-INF/container.xml')
            root = ET.fromstring(container_content)
            opf_path = ""
            for elem in root.iter():
                if elem.tag.endswith('rootfile'):
                    opf_path = elem.attrib.get('full-path', '')
                    break
            if not opf_path:
                return None
            
            opf_content = epub.read(opf_path)
            opf_root = ET.fromstring(opf_content)
            
            # 1단계: 표준 메타데이터 태그(<dc:identifier>)에서 ISBN 탐색
            for elem in opf_root.iter():
                if elem.tag.endswith('identifier') and elem.text:
                    clean = re.sub(r'[^0-9X]', '', elem.text.upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        return clean
            
            # 2단계 백업: 본문 XHTML 파일 분석 (앞쪽 8장 + 뒤쪽 8장 대역 확장 분석)
            manifest = {}
            for elem in opf_root.iter():
                if elem.tag.endswith('item'):
                    item_id = elem.attrib.get('id')
                    href = elem.attrib.get('href')
                    if item_id and href:
                        manifest[item_id] = href
            
            spine_item_ids = []
            for elem in opf_root.iter():
                if elem.tag.endswith('itemref'):
                    idref = elem.attrib.get('idref')
                    if idref:
                        spine_item_ids.append(idref)
            
            # 판권지가 앞쪽에 조판되었을 경우를 대비해 전방 8장, 후방 8장 대역 수집
            num_spines = len(spine_item_ids)
            target_spines = list(range(min(8, num_spines)))
            if num_spines > 8:
                target_spines.extend(list(range(max(8, num_spines - 8), num_spines)))
            target_spines = sorted(list(set(target_spines)))
            
            opf_dir = os.path.dirname(opf_path)
            isbn_pat = re.compile(r'\b(?:97[89][-\s.]?)?\d{1,5}[-\s.]?\d{1,7}[-\s.]?\d{1,6}[-\s.]?[\dX]\b')
            isbn10_candidates = []
            
            for idx in target_spines:
                spine_id = spine_item_ids[idx]
                href = manifest.get(spine_id)
                if href:
                    href = urllib.parse.unquote(href)
                    full_href = os.path.join(opf_dir, href) if opf_dir else href
                    full_href = full_href.replace('\\', '/')
                    
                    try:
                        raw_data = epub.read(full_href).decode('utf-8', errors='ignore')
                        # HTML 엔티티(&nbsp; &#160; 등)를 표준 공백 문자로 복원 디코딩 [1]
                        html_content = html.unescape(raw_data)
                        
                        # HTML 태그 제거
                        text_content = re.sub('<[^<]+?>', '', html_content)
                        # 유니코드 특수 대시 및 구분 점기호를 표준 하이픈(-)으로 강제 정규화
                        text_content = re.sub(r'[\u2012-\u2015\u00ad.]', '-', text_content)
                        
                        for match in isbn_pat.findall(text_content):
                            clean = re.sub(r'[^0-9X]', '', match.upper())
                            if validate_isbn13(clean) or validate_isbn10(clean):
                                return clean
                            elif validate_isbn10(clean):
                                isbn10_candidates.append(clean)
                    except Exception:
                        pass
                        
            if isbn10_candidates:
                return isbn10_candidates[0]
    except Exception:
        pass
    return None

def extract_isbn_from_pdf(pdf_path):
    """PDF 메타데이터 및 전후면 판권 페이지 고속 타겟 스캔 (맨 뒤 15페이지까지 탐색 범위 확장)"""
    try:
        import pypdf
    except ImportError:
        return None
        
    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            num_pages = len(reader.pages)
            if num_pages == 0:
                return None
                
            # 앞쪽 30페이지 수집 (TOC, 서론 등이 긴 책 대비)
            pages_to_scan = list(range(min(30, num_pages)))
            # 뒤쪽 수집 범위를 30페이지 전까지 늘려 판권지가 광고 뒤에 숨은 책 검출 성공률 향상
            if num_pages > 30:
                pages_to_scan.extend(list(range(max(30, num_pages - 30), num_pages)))
                
            pages_to_scan = sorted(list(set(pages_to_scan)))
            isbn_pat = re.compile(r'\b(?:97[89][-\s.]?)?\d{1,5}[-\s.]?\d{1,7}[-\s.]?\d{1,6}[-\s.]?[\dX]\b')
            isbn10_candidates = []
            
            for page_idx in pages_to_scan:
                text = reader.pages[page_idx].extract_text()
                if not text:
                    continue
                
                # PDF 특유의 인코딩 문제로 인한 유니코드 대시 기호를 표준 하이픈(-)으로 표준화
                text = re.sub(r'[\u2012-\u2015\u00ad.]', '-', text)
                
                for match in isbn_pat.findall(text):
                    clean = re.sub(r'[^0-9X]', '', match.upper())
                    if validate_isbn13(clean):
                        return clean
                    elif validate_isbn10(clean):
                        isbn10_candidates.append(clean)
                        
            if isbn10_candidates:
                return isbn10_candidates[0]
    except Exception:
        pass
    return None

def _get_row_val(row, key, default=''):
    """💡 sqlite3.Row 및 dict 호환을 위해 에러 없이 안전하게 값을 추출하는 헬퍼"""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, TypeError, IndexError):
        return default


# ==========================================
# 💡 ISBN 일치 타겟 전용 정밀 API 검색 함수
# ==========================================

def search_aladin_isbn(isbn, ttbkey):
    """알라딘 ISBN 일치검색 API"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': isbn, 'QueryType': 'ISBN', 'MaxResults': 1, 'output': 'js', 'Version': '20131101'}
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
            res = response.read().decode('utf-8')
            if res.endswith(';'): res = res[:-1]
            data = json.loads(res)
            return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                     'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                     'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
                     'isbn': i.get('isbn13') or i.get('isbn', '')} 
                    for i in data.get('item', [])]
    except: return []

def search_naver_isbn(isbn, cid, csecret):
    """네이버 ISBN 상세 검색 API"""
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_isbn': isbn, 'display': 1})}")
    req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                     'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버',
                     'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''} 
                    for i in data.get('items', [])]
    except: return []


# ==========================================
# 💡 통합 플러그인 메인 코어 클래스
# ==========================================

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
        norm_query = "".join(re.findall(r'\w+', query.replace('_', ''))).lower()
        
        # 1. 입력받은 기본 검색어가 이미 유효한 ISBN 구성인지 우선 감지
        clean_query = re.sub(r'[^0-9X]', '', query.upper())
        is_isbn = validate_isbn13(clean_query) or validate_isbn10(clean_query)
        search_query = clean_query if is_isbn else query

        # 2. ISBN이 아닐 경우, 로컬 DB 추적 및 파일 실시간 파싱을 통한 ISBN 추적 가동
        if not is_isbn:
            gateway = self.get_db_gateway(db_type)
            
            # 💡 sqlite3.Row 호출 시 에러 방지용 안전 헬퍼 적용
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
                db_isbn = _get_row_val(book, 'isbn')
                clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''
                
                if validate_isbn13(clean_db_isbn) or validate_isbn10(clean_db_isbn):
                    is_isbn = True
                    search_query = clean_db_isbn
                else:
                    file_path = _get_row_val(book, 'file_path')
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
        # ISBN 검색 결과가 0건이거나 실패한 경우 즉시 원본 책 제목 검색으로 Fallback 전환
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

            file_path = _get_row_val(book, 'file_path')
            library_id = _get_row_val(book, 'library_id')
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
