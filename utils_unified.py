# -*- coding: utf-8 -*-
import os
import re
import zipfile
import html
import urllib.parse
import xml.etree.ElementTree as ET

# pypdf 라이브러리 탑재 여부 감지
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


def parse_bool(val, default=False):
    """💡 웹 폼에서 유입되는 다양한 형태의 문자열("false", "off", "0" 등)을 실제 불리언 값으로 강제 정제"""
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
    if not date_str: return ""
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
    if not url: return url
    if source == '알라딘':
        url = url.replace('coversum.jpg', 'cover500.jpg').replace('covermid.jpg', 'cover500.jpg')
    elif source == '네이버':
        if '?' in url: url = url.split('?')[0]
    elif source == '구글':
        url = url.replace('zoom=1', 'zoom=3').replace('zoom=5', 'zoom=3')
        if 'edge=curl' in url: url = url.replace('edge=curl', '')
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
                        # HTML 엔티티(&nbsp; &#160; 등)를 표준 공백 문자로 복원 디코딩
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
    if not PYPDF_AVAILABLE:
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

def get_row_val(row, key, default=''):
    """sqlite3.Row 및 dict 호환을 위해 에러 없이 안전하게 값을 추출하는 헬퍼"""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, TypeError, IndexError):
        return default
