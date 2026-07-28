# === utils_unified.py의 기존 extract_isbn_from_link 함수를 아래 내용으로 교체하세요 ===
# 필요한 import가 파일 상단에 없다면 추가:
# import re
# import sys
# import urllib.request

def extract_isbn_from_link(url, timeout=6):
    """
    books.link 컬럼에 저장된 도서 상세 페이지 URL의 HTML을 가져와 ISBN을 탐색합니다.
    1) '<라벨>ISBN</라벨>...<값>' 처럼 라벨 바로 옆에 값이 붙어있는 구조를 우선 탐색 (네이버/교보 등)
    2) 실패 시 페이지 전체에서 숫자열 후보를 뽑아 체크섬으로 검증 (폴백)
    """
    if not url or not str(url).lower().startswith(('http://', 'https://')):
        return None

    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (compatible; UnifiedBookBot/1.0)'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get('Content-Type', '')
            if content_type and 'text/html' not in content_type:
                return None
            raw = response.read(1_500_000)  # 대형 SSR 페이지(쇼핑몰 등) 대응을 위해 1.5MB로 상향
            page_text = raw.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[통합 도서 검색] 링크 ISBN 파싱 실패: {url} 사유: {e}", file=sys.stderr)
        return None

    # 스크립트/스타일 블록은 노이즈(해시 파일명, 리다이렉트 파라미터 등)만 늘리므로 먼저 제거
    cleaned = re.sub(r'<script\b[^>]*>.*?</script>', ' ', page_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<style\b[^>]*>.*?</style>', ' ', cleaned, flags=re.DOTALL | re.IGNORECASE)

    def _validate_candidates(cands):
        for cand in cands:
            clean = re.sub(r'[^0-9Xx]', '', cand).upper()
            if validate_isbn13(clean):
                return clean
        for cand in cands:
            clean = re.sub(r'[^0-9Xx]', '', cand).upper()
            if validate_isbn10(clean):
                return clean
        return None

    # 1단계: 'ISBN'이라는 라벨 뒤 200자 이내에서 값 추출 (네이버/교보/알라딘 등 상세페이지 공통 패턴)
    label_matches = re.findall(
        r'ISBN[^0-9Xx]{0,200}?([\dXx][\dXx\-\s]{8,17}[\dXx])',
        cleaned, flags=re.IGNORECASE
    )
    result = _validate_candidates(label_matches)
    if result:
        return result

    # 2단계 (폴백): 페이지 전체에서 ISBN처럼 생긴 숫자열을 모두 뽑아 체크섬으로 검증
    candidates = re.findall(
        r'(?:97[89][-\s]?)?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,6}[-\s]?[\dXx]', cleaned
    )
    return _validate_candidates(candidates)
