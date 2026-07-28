# -*- coding: utf-8 -*-
import re
import sys
import urllib.request
import urllib.error


def parse_bool(val, default=False):
    """웹 폼에서 유입되는 다양한 형태의 문자열을 실제 불리언 값으로 강제 정제"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    val_str = str(val).lower().strip()
    if val_str in ('true', 'on', '1', 'yes'):
        return True
    if val_str in ('false', 'off', '0', 'no', ''):
        return False
    return default


def format_date(date_str):
    """날짜 형식을 YYYY-MM-DD로 표준화"""
    if not date_str:
        return ""
    digits = re.sub(r'\D', '', date_str)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    elif len(digits) >= 6:
        prefix = "20" if int(digits[:2]) < 50 else "19"
        return f"{prefix}{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
    elif len(digits) == 4:
        return f"{digits}-01-01"
    return date_str


def get_high_res_url(url, source):
    """서점 API별 커버 이미지 최고해상도 원본 치환 및 파라미터 정제"""
    if not url:
        return url
    if source == '알라딘':
        url = url.replace('coversum.jpg', 'cover500.jpg').replace('covermid.jpg', 'cover500.jpg')
    elif source == '네이버':
        if '?' in url:
            url = url.split('?')[0]
    elif source == '구글':
        url = url.replace('zoom=1', 'zoom=3').replace('zoom=5', 'zoom=3')
        if 'edge=curl' in url:
            url = url.replace('edge=curl', '')
    return url


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


def extract_isbn_from_link(url, timeout=6):
    """
    books.link 컬럼에 저장된 도서 상세 페이지 URL의 HTML을 가져와 ISBN을 탐색합니다.
    1) 'ISBN' 라벨 바로 옆에 값이 붙어있는 구조를 우선 탐색 (네이버/교보 등 대부분의 서점 상세페이지)
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
            raw = response.read(1_500_000)  # 대형 SSR 쇼핑몰 페이지 대응을 위해 1.5MB까지 확인
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


def get_row_val(row, key, default=''):
    """sqlite3.Row 및 dict 호환을 위해 에러 없이 안전하게 값을 추출하는 헬퍼"""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, TypeError, IndexError):
        return default
