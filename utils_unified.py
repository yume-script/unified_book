# -*- coding: utf-8 -*-
import os
import re
import sys
import zipfile
import html
import json
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

# pypdf 라이브러리 탑재 여부 감지
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


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

def extract_isbn_via_llm(text, api_key, endpoint=None, model=None):
    """구글 Gemini API 및 LiteLLM(OpenAI 호환) 프록시를 모두 지원하는 통합 지능형 판독 엔진"""
    if not text.strip():
        return None

    # gemini-3.5-flash-lite 모델을 활용해 속도 및 비용 최적화 (2026년 7월 21일 출시 모델)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
    
    # 잡담을 배제하고 오직 규정된 스키마의 JSON 오브젝트만 반환하도록 기계적 설계
    prompt = (
        "다음 도서 판권지/본문 텍스트에서 ISBN 번호만 추출해줘.\n"
        "출력은 반드시 다른 미사여구 없이 JSON 형식으로만 해야 하며, 그 구조는 반드시 다음 스키마를 따라야 해:\n"
        "{\"isbn\": \"공백이나 하이픈을 제거한 오직 10자리 또는 13자리 숫자(마지막 X 허용) 문자열 (발견되지 않으면 빈 문자열)\"}\n\n"
        f"[텍스트 본문]\n{text}"
    )
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json", # 구글 공식 JSON 모드 활성화로 수다스러운 답변 완전 차단
            "temperature": 0.1,
            "maxOutputTokens": 100
        }
    }
    
    # 1. LiteLLM / OpenAI 호환 모드 연동
    if endpoint and endpoint.strip():
        url = endpoint.strip()
        target_model = model.strip() if model and model.strip() else "gemini/gemini-3.5-flash-lite"

        payload = {
            "model": target_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}  # OpenAI JSON 모드 명시
        }

        headers = {'Content-Type': 'application/json'}
        if api_key and api_key.strip():
            headers['Authorization'] = f"Bearer {api_key.strip()}"

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                choices = res_data.get('choices', [])
                if choices:
                    raw_content = choices[0].get('message', {}).get('content', '').strip()
                    res_json = json.loads(raw_content)
                    raw_isbn = res_json.get('isbn', '')
                    clean = re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        return clean
        except urllib.error.HTTPError as he:
            error_msg = he.read().decode('utf-8', errors='ignore')
            print(f"[LiteLLM API HTTP 에러 {he.code}] 이유: {error_msg}", file=sys.stderr)
        except Exception as e:
            print(f"[LiteLLM API 에러] 사유: {str(e)}", file=sys.stderr)

    # 2. 순수 Google Gemini 공식 API 모드 연동
    else:
        if not api_key:
            return None
        try:
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get('candidates', [])
                if candidates:
                    parts = candidates[0].get('content', {}).get('parts', [])
                    if parts:
                        raw_text = parts[0].get('text', '').strip()
                        res_json = json.loads(raw_text)
                        raw_isbn = res_json.get('isbn', '')
                        clean = re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
                        if validate_isbn13(clean) or validate_isbn10(clean):
       except urllib.error.HTTPError as he:
            error_msg = he.read().decode('utf-8', errors='ignore')
            print(f"[Gemini API HTTP 에러 {he.code}] 이유: {error_msg}", file=sys.stderr)
        except Exception as e:
            print(f"[Gemini API 에러] 사유: {str(e)}", file=sys.stderr)

    return None

def extract_isbn_from_epub(epub_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """EPUB 내부 컨테이너 구조 및 본문 파일 분석 후 ISBN 추출 (지능형 LLM 듀얼 분기 가동)"""
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
                return None, None

            opf_content = epub.read(opf_path)
            opf_root = ET.fromstring(opf_content)

            # 1단계: 표준 메타데이터 태그(<dc:identifier>)에서 ISBN 탐색
            for elem in opf_root.iter():
                if elem.tag.endswith('identifier') and elem.text:
                    clean = re.sub(r'[^0-9X]', '', elem.text.upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        return clean, "LOCAL"

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

            # [초고속 조기 종료 필터 1]: 만화책/스캔본 전용 EPUB 판별
            # 앞쪽 3장의 텍스트 정보가 공백 제외 20자 미만인 경우 이미지 중심의 도서로 간주하고 즉시 조기 종료
            sample_epub_text = ""
            check_spines = target_spines[:3]
            for idx in check_spines:
                spine_id = spine_item_ids[idx]
                href = manifest.get(spine_id)
                if href:
                    href = urllib.parse.unquote(href)
                    full_href = os.path.join(opf_dir, href) if opf_dir else href
                    full_href = full_href.replace('\\', '/')
                    try:
                        html_data = epub.read(full_href).decode('utf-8', errors='ignore')
                        text_data = re.sub('<[^<]+?>', '', html.unescape(html_data))
                        sample_epub_text += text_data.strip()
                    except Exception:
                        pass
            if len(re.sub(r'\s', '', sample_epub_text)) < 20:
                return None, None  # 이미지 전용책이므로 실시간 수색 종료

            isbn_pat = re.compile(r'\b(?:97[89][-\s.]?)?\d{1,5}[-\s.]?\d{1,7}[-\s.]?\d{1,6}[-\s.]?[\dX]\b')
            isbn10_candidates = []
            compiled_texts = []

            for idx in target_spines:
                spine_id = spine_item_ids[idx]
                href = manifest.get(spine_id)
                if href:
                    href = urllib.parse.unquote(href)
                    full_href = os.path.join(opf_dir, href) if opf_dir else href
                    full_href = full_href.replace('\\', '/')

                    try:
                        raw_data = epub.read(full_href).decode('utf-8', errors='ignore')
                        html_content = html.unescape(raw_data)
                        text_content = re.sub('<[^<]+?>', '', html_content)
                        text_content = re.sub(r'[\u2012-\u2015\u00ad.]', '-', text_content)

                        if text_content.strip():
                            compiled_texts.append(text_content)

                        for match in isbn_pat.findall(text_content):
                            clean = re.sub(r'[^0-9X]', '', match.upper())
                            if validate_isbn13(clean) or validate_isbn10(clean):
                                return clean, "LOCAL"
                            elif validate_isbn10(clean):
                                isbn10_candidates.append(clean)
                    except Exception:
                        pass

            if isbn10_candidates:
                return isbn10_candidates[0], "LOCAL"

            # 3단계 백업: 로컬 정규식 매칭 실패 시 수집된 텍스트 본문 LLM 전송 판독
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"

    except Exception:
        pass
    return None, None

def extract_isbn_from_pdf(pdf_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """PDF 메타데이터 및 전후면 판권 페이지 고속 스캔 (지능형 LLM 듀얼 분기 가동)"""
    if not PYPDF_AVAILABLE:
        return None, None

    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            num_pages = len(reader.pages)
            if num_pages == 0:
                return None, None

            # 💡 [초고속 조기 종료 필터 2]: 스캔본(통 이미지) 전용 PDF 판별 전방 5p, 후방 5p 확장 감지
            # 표지를 제외한 본문 초입부(1~5페이지)와 맨 뒷부분(끝에서 5페이지)에서 임시 텍스트 추출을 먼저 시도합니다.
            # 전/후방 양쪽 구역 모두 글자 데이터가 아예 없는 경우에만 스캔본(통 이미지)으로 판정하고 즉시 스캔을 중단합니다.
            check_indices = list(range(1, min(6, num_pages)))
            if num_pages > 5:
                check_indices.extend(list(range(max(5, num_pages - 5), num_pages)))

            check_indices = sorted(list(set(check_indices)))
            if not check_indices:
                check_indices = [0]

            sample_text = ""
            for idx in check_indices:
                try:
                    p_text = reader.pages[idx].extract_text()
                    if p_text:
                        sample_text += p_text.strip()
                except Exception:
                    pass
            if not sample_text.strip():
                return None, None # 전후방 모두 글자가 전혀 긁히지 않는 스캔 도서이므로 실시간 수색 조기 종료

            pages_to_scan = list(range(min(30, num_pages)))
            if num_pages > 30:
                pages_to_scan.extend(list(range(max(30, num_pages - 30), num_pages)))

            pages_to_scan = sorted(list(set(pages_to_scan)))
            isbn_pat = re.compile(r'\b(?:97[89][-\s.]?)?\d{1,5}[-\s.]?\d{1,7}[-\s.]?\d{1,6}[-\s.]?[\dX]\b')
            isbn10_candidates = []
            compiled_texts = []

            for page_idx in pages_to_scan:
                text = reader.pages[page_idx].extract_text()
                if not text:
                    continue

                # PDF 특유의 인코딩 문제로 인한 유니코드 대시 기호를 표준 하이픈(-)으로 표준화
                text = re.sub(r'[\u2012-\u2015\u00ad.]', '-', text)

                if text.strip():
                    compiled_texts.append(text)

                for match in isbn_pat.findall(text):
                    clean = re.sub(r'[^0-9X]', '', match.upper())
                    if validate_isbn13(clean):
                        return clean, "LOCAL"
                    elif validate_isbn10(clean):
                        isbn10_candidates.append(clean)

            if isbn10_candidates:
                return isbn10_candidates[0], "LOCAL"

            # 3단계 백업: 로컬 정규식 매칭 실패 시 수집된 텍스트 본문 LLM 전송 판독
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"

    except Exception:
        pass
    return None, None

def get_row_val(row, key, default=''):
    """sqlite3.Row 및 dict 호환을 위해 에러 없이 안전하게 값을 추출하는 헬퍼"""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, TypeError, IndexError):
        return default

