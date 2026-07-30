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

LOG_PREFIX = "[통합 도서 검색]"

# 소스별 서지정보/표지 우선순위 (4단계 메타데이터 합성에서 사용)
BIB_PRIORITY_ORDER = ['국립중앙도서관', '알라딘', '네이버', '구글']
DEFAULT_COVER_PRIORITY = '알라딘'


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
    from .nl import search_nl, search_nl_isbn
    from .utils_unified import (
        format_date, get_high_res_url, validate_isbn13, validate_isbn10,
        compare_isbns, get_row_val, parse_bool,
        extract_url_from_text, extract_isbn_from_link
    )
except ImportError:
    _aladin_mod = _import_local_module("aladin")
    _naver_mod = _import_local_module("naver")
    _google_mod = _import_local_module("google")
    _nl_mod = _import_local_module("nl")
    _utils_mod = _import_local_module("utils_unified")

    search_aladin = _aladin_mod.search_aladin
    search_aladin_isbn = _aladin_mod.search_aladin_isbn
    search_naver = _naver_mod.search_naver
    search_naver_isbn = _naver_mod.search_naver_isbn
    search_google = _google_mod.search_google
    search_nl = _nl_mod.search_nl
    search_nl_isbn = _nl_mod.search_nl_isbn

    format_date = _utils_mod.format_date
    get_high_res_url = _utils_mod.get_high_res_url
    validate_isbn13 = _utils_mod.validate_isbn13
    validate_isbn10 = _utils_mod.validate_isbn10
    compare_isbns = _utils_mod.compare_isbns
    get_row_val = _utils_mod.get_row_val
    parse_bool = _utils_mod.parse_bool
    extract_url_from_text = _utils_mod.extract_url_from_text
    extract_isbn_from_link = _utils_mod.extract_isbn_from_link


class UnifiedBookMetadataProvider(BaseMetadataProvider):
    id = "unified_book"
    name = "통합 도서 검색"
    is_searchable = True

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/yume-script/unified_book/refs/heads/main/",
        "files": ["unified_book.py", "aladin.py", "naver.py", "google.py", "nl.py", "utils_unified.py", "index.html", "style.css", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    # 설정화면 레이아웃은 실제 처리 순서(로컬 판정 → 링크 판정 → API 호출 → 합성)를 따라 배치됩니다.
    # API 키는 4단계 합성 우선순위와 동일한 순서(국립중앙도서관 > 알라딘 > 네이버 > 구글)로 나열합니다.
    config_schema = [
        {"key": "NL_API_KEY", "label": "국립중앙도서관 인증키 (cert_key)", "type": "text", "required": False},
        {"key": "ALADIN_KEY", "label": "알라딘 TTBKey", "type": "text", "required": False},
        {"key": "NAVER_ID", "label": "네이버 Client ID", "type": "text", "required": False},
        {"key": "NAVER_SECRET", "label": "네이버 Client Secret", "type": "text", "required": False},
        {"key": "GOOGLE_API_KEY", "label": "Google API Key", "type": "text", "required": False},
        {"key": "COVER_PRIORITY", "label": "표지(Cover) 우선 소스", "type": "select",
         "options": ["알라딘", "국립중앙도서관", "네이버", "구글"], "default": "알라딘", "required": False},
        {"key": "STRICT_MATCH", "label": "검색 결과 엄격한 필터링", "type": "checkbox", "required": False}
    ]

    def search(self, db_type, query):
        if not query:
            return []

        print(f"{LOG_PREFIX} search() 호출됨 | db_type: '{db_type}' | 원본 검색어: '{query}'")

        config = self.get_plugin_config(db_type, default={})
        strict_match = parse_bool(config.get("STRICT_MATCH", False), default=False)
        cover_priority = (config.get("COVER_PRIORITY") or DEFAULT_COVER_PRIORITY).strip()

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
        matched_author = ""  # DB에서 찾은 저자명 (3단계 '제목+저자' API 검색에 사용)

        if is_isbn:
            print(f"{LOG_PREFIX} 검색어 유형 판정: ISBN (입력값 자체가 유효 ISBN) | '{query}' -> {search_query}")

        book = None
        if not is_isbn:
            gateway = self.get_db_gateway(db_type)

            # 가공된 clean_query_base를 사용하여 DB를 검색하므로 매칭 확률과 인덱스 속도가 대폭 향상됩니다.
            book = gateway.fetch_one("SELECT file_path, isbn, link, author FROM books WHERE title = ? LIMIT 1", (clean_query_base,))
            if not book:
                book = gateway.fetch_one("SELECT file_path, isbn, link, author FROM books WHERE file_path LIKE ? LIMIT 1", (f"%{clean_query_base}%",))

            # 유연한 부분일치 검색 추가 가동
            if not book:
                words = [w for w in clean_query_base.split() if len(w) > 1]
                if len(words) >= 2:
                    sub_query = " ".join(words[:2])
                    book = gateway.fetch_one("SELECT file_path, isbn, link, author FROM books WHERE title LIKE ? LIMIT 1", (f"%{sub_query}%",))

            if book:
                matched_author = get_row_val(book, 'author')

                # 1단계: 로컬 판정 - DB에 이미 저장된 isbn 컬럼값 확인 (네트워크/파일 접근 없는 최우선 무료 판정)
                db_isbn = get_row_val(book, 'isbn')
                clean_db_isbn = re.sub(r'[^0-9X]', '', str(db_isbn).upper()) if db_isbn else ''

                if validate_isbn13(clean_db_isbn) or validate_isbn10(clean_db_isbn):
                    is_isbn = True
                    search_query = clean_db_isbn
                    detection_source = "LOCAL"  # 감지출처: 로컬(DB에 이미 저장된 값)
                    print(f"{LOG_PREFIX} 로컬 판정으로 ISBN 감지(DB isbn 컬럼): '{query}' -> {search_query} (출처: LOCAL)")

            # 2단계: 링크 파싱 - 검색어 자체의 URL을 우선 시도하고, 없으면 DB의 link 컬럼(HTML)을 파싱
            if not is_isbn:
                link_in_query = extract_url_from_text(query)
                if link_in_query:
                    link_isbn = extract_isbn_from_link(link_in_query)
                    if link_isbn:
                        is_isbn = True
                        search_query = link_isbn
                        detection_source = "LINK"
                        print(f"{LOG_PREFIX} LINK 파싱으로 ISBN 감지(검색어 내 URL): '{query}' -> {search_query} (출처: {link_in_query})")

                if not is_isbn and book:
                    db_link = get_row_val(book, 'link')
                    if db_link:
                        link_isbn = extract_isbn_from_link(db_link)
                        if link_isbn:
                            is_isbn = True
                            search_query = link_isbn
                            detection_source = "LINK"
                            print(f"{LOG_PREFIX} LINK 파싱으로 ISBN 감지(DB link 컬럼): '{query}' -> {search_query} (출처: {db_link})")

            if not is_isbn:
                print(f"{LOG_PREFIX} 검색어 유형 판정: 제목 (ISBN 미검출) | 검색어: '{clean_query_base}'")

        # 내부 검색 수행 전용 헬퍼 함수 (3단계: API 호출)
        def _execute_search(sources, s_query, is_isbn_mode):
            mode_label = 'ISBN' if is_isbn_mode else '제목'
            res = []
            titles_seen = set()

            # 워커 스레드를 할당하여 API를 동시 다발적으로 호출
            futures = {}
            with ThreadPoolExecutor(max_workers=len(sources)) as executor:
                for source_name, func, args in sources:
                    if not all(args):
                        print(f"{LOG_PREFIX} {source_name}({mode_label}) - API 키 미설정으로 건너뜀 (바이패스)")
                        continue
                    print(f"{LOG_PREFIX} {source_name}({mode_label}) - 검색 요청 전송")
                    # 비동기 백그라운드 쿼리 등록
                    future = executor.submit(func, s_query, *args)
                    futures[future] = source_name

                # 먼저 완성되는 결과부터 실시간 데이터 정합성 검증 적용
                for future in as_completed(futures):
                    source_name = futures[future]
                    try:
                        items = future.result()
                        print(f"{LOG_PREFIX} {source_name}({mode_label}) - 원본 응답 {len(items)}건 수신")
                    except Exception as e:
                        print(f"{LOG_PREFIX} {source_name}({mode_label}) - 요청 실패: {e}")
                        continue

                    before_count = len(res)
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
                            item['_source_name'] = source_name  # 4단계 합성 시 우선순위 판별용 원본 소스명 보존

                            formatted_date = format_date(item.get('pubDate'))
                            isbn = item.get('isbn', '')
                            if isbn:
                                item['pubDate'] = f"{formatted_date} | ISBN: {isbn}"
                            else:
                                item['pubDate'] = formatted_date

                            # 깔끔한 출처 레이블과 매칭 표시용 별표(*)만 타이틀 끝에 부여
                            if is_isbn_mode:
                                label = detection_source if detection_source in ("INPUT", "LOCAL", "LINK") else "ISBN"
                                item['title'] = f"[{source_name}/{label}] {original_title} *"
                            else:
                                item['title'] = f"[{source_name}] {original_title}"

                            item['description'] = re.sub(r'^\[.*?\]\s*', '', item.get('description', '')) if 'description' in item else ''

                            res.append(item)
                            titles_seen.add(norm)
                    print(f"{LOG_PREFIX} {source_name}({mode_label}) - 중복/필터 제외 후 {len(res) - before_count}건 채택")
            return res

        # 4단계: 메타데이터 합성 - 여러 소스의 결과를 우선순위에 따라 하나로 병합
        def _synthesize(raw_items, is_isbn_mode):
            if len(raw_items) < 2:
                return None  # 소스가 1개뿐이면 병합할 실익이 없어 생략

            def pick_field(field):
                for src in BIB_PRIORITY_ORDER:
                    for it in raw_items:
                        if it.get('_source_name') == src:
                            val = it.get(field)
                            if val:
                                return val
                # 우선순위 목록에 없는 소스라도 값이 있으면 사용
                for it in raw_items:
                    val = it.get(field)
                    if val:
                        return val
                return ''

            def pick_cover():
                for it in raw_items:
                    if it.get('_source_name') == cover_priority and it.get('cover'):
                        return it.get('cover')
                for src in BIB_PRIORITY_ORDER:
                    for it in raw_items:
                        if it.get('_source_name') == src and it.get('cover'):
                            return it.get('cover')
                return ''

            merged_title = pick_field('title')
            merged_title = re.sub(r'^\[.*?\]\s*', '', merged_title).replace(' *', '').strip()

            merged = {
                'title': f"[통합/합성] {merged_title} *" if is_isbn_mode else f"[통합/합성] {merged_title}",
                'author': pick_field('author'),
                'publisher': pick_field('publisher'),
                'pubDate': pick_field('pubDate'),
                'cover': pick_cover(),
                'description': pick_field('description'),
                'link': pick_field('link'),
                'isbn': pick_field('isbn'),
                'source': '통합(합성)',
                '_source_name': '통합(합성)'
            }
            used_sources = sorted({it.get('_source_name') for it in raw_items if it.get('_source_name')})
            print(f"{LOG_PREFIX} 메타데이터 합성 완료 | 참여 소스: {', '.join(used_sources)} | 표지 우선순위: {cover_priority}")
            return merged

        results = []

        # 3단계 - ISBN이 확인된 경우: ISBN 기준으로 API 키가 있는 모든 소스를 호출
        if is_isbn:
            print(f"{LOG_PREFIX} ISBN 정밀검색 시작 | 검색어: '{search_query}'")
            sources_isbn = [
                ('국립중앙도서관', search_nl_isbn, (config.get("NL_API_KEY"),)),
                ('알라딘', search_aladin_isbn, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver_isbn, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            raw_results = _execute_search(sources_isbn, search_query, is_isbn_mode=True)
            print(f"{LOG_PREFIX} ISBN 정밀검색 종료 | 최종 결과 {len(raw_results)}건")

            synthesized = _synthesize(raw_results, is_isbn_mode=True)
            results = ([synthesized] if synthesized else []) + raw_results

        # 3단계 - ISBN이 없는 경우(또는 ISBN 검색이 0건인 안전장치): 제목+저자 기준으로 전체 소스 호출
        if not results:
            title_author_query = f"{clean_query_base} {matched_author}".strip() if matched_author else clean_query_base
            print(f"{LOG_PREFIX} 제목+저자 검색 시작 | 검색어: '{title_author_query}'")
            sources_title = [
                ('국립중앙도서관', search_nl, (config.get("NL_API_KEY"), matched_author)),
                ('알라딘', search_aladin, (config.get("ALADIN_KEY"),)),
                ('네이버', search_naver, (config.get("NAVER_ID"), config.get("NAVER_SECRET"))),
                ('구글', search_google, (config.get("GOOGLE_API_KEY"),))
            ]
            raw_results = _execute_search(sources_title, title_author_query, is_isbn_mode=False)
            print(f"{LOG_PREFIX} 제목+저자 검색 종료 | 최종 결과 {len(raw_results)}건")

            synthesized = _synthesize(raw_results, is_isbn_mode=False)
            results = ([synthesized] if synthesized else []) + raw_results

        query_type_label = "ISBN" if is_isbn else "제목"
        print(f"{LOG_PREFIX} search() 종료 | 원본 검색어: '{query}' | 검색어 유형: {query_type_label} | 감지출처: {detection_source or 'NONE'} | 총 반환 {len(results)}건")

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
