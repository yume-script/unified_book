# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import socket
import threading
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
    elif source == '교보문고':
        # og:image URL의 리사이즈 파라미터(예: fname=...&pat=... 뒤 크기 지정)는
        # 이미 원본 상세페이지에서 제공하는 값이라 별도 치환 없이 그대로 사용한다.
        pass
    elif source == '리디북스':
        # ridi.py에서 이미 최고해상도(xxlarge) CDN URL로 직접 구성해서 넘기므로
        # 별도 치환이 필요 없다.
        pass
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

def _do_http_request(url, payload, headers, timeout, result_box):
    """실제 HTTP 요청 1회를 수행하는 내부 함수. 데몬 스레드에서 실행되어,
    DNS 조회처럼 urllib의 timeout 파라미터가 못 미치는 지연이 발생해도
    메인 흐름(search()/apply())이 함께 묶여서 멈추지 않게 한다."""
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result_box['data'] = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as he:
        result_box['error'] = ('http', he.code, he.read().decode('utf-8', errors='ignore'))
    except Exception as e:
        result_box['error'] = ('other', e)


def _llm_post_with_retry(url, payload, headers, timeout, label, max_attempts=2, backoff_base=1.0):
    """LLM 엔드포인트 공통 HTTP POST 재시도 헬퍼.

    - 타임아웃/연결 실패/DNS 오류 등 '일시적' 통신 오류나 5xx 서버 오류는
      짧은 대기 후 재시도한다 (기본 최대 2회, 시도마다 대기시간 증가).
    - 401/403/429 등 4xx 오류(인증 실패, 요청 형식 오류, 레이트리밋 등)는
      재시도해도 결과가 달라지지 않으므로 즉시 중단한다.
    - 응답 JSON 파싱 실패 등 코드/데이터 문제도 재시도 없이 즉시 중단한다.
    - ⚠️ search()/apply()는 사용자 요청에 동기적으로 응답해야 하므로, 이 함수의
      "타임아웃 × 시도횟수 + 대기시간" 합계가 nginx/gunicorn 등 리버스 프록시나
      WSGI 워커의 요청 타임아웃(보통 30~60초)을 넘지 않도록 기본값을 보수적으로
      유지한다.
    - ⚠️⚠️ urllib.request.urlopen(timeout=N)의 timeout은 소켓이 만들어진 *이후*
      (연결/응답 대기)에만 적용되고, 그 이전의 DNS 조회(getaddrinfo)는 전혀
      제어하지 못한다. 도커 환경에서 DNS가 응답을 안 하거나 방화벽이 요청을
      조용히 버리면 이 단계에서 Python 예외도, 타임아웃도 없이 무한정 멈출 수
      있다. 이를 막기 위해 실제 요청은 데몬 스레드에서 실행하고, 메인 스레드는
      `timeout + 3초`가 지나도 응답이 없으면 그 스레드를 기다리지 않고 그냥
      포기한다(스레드 자체는 백그라운드에 남아 있다가 언젠가 끝나거나, 데몬이므로
      프로세스 종료를 막지도 않는다). 이 하드 타임아웃이 바로 "AI판독: 호출 시작"
      로그는 찍혔는데 그 다음이 한참 안 나오는 증상에 대한 안전장치다.
    반환값: (성공 시 파싱된 JSON, 실패 사유 문자열 또는 None)
    """
    last_err = None
    hard_budget = timeout + 3  # DNS 조회 지연까지 흡수할 최종 강제 포기 시한

    for attempt in range(1, max_attempts + 1):
        result_box = {}
        th = threading.Thread(target=_do_http_request, args=(url, payload, headers, timeout, result_box), daemon=True)
        th.start()
        th.join(timeout=hard_budget)

        if th.is_alive():
            # hard_budget초가 지나도 스레드가 안 끝남 -> DNS 조회 등에서 멈춘 것으로 보고 강제 포기
            # (스레드는 데몬이라 여기서 join을 그만둬도 프로세스 종료를 막지 않음)
            print(f"[UnifiedBook] AI판독({label}) {hard_budget}초 내에 응답이 없어 강제로 포기합니다 "
                  f"(DNS 조회 지연 등 urllib timeout이 못 막는 구간에서 멈춘 것으로 추정) (시도 {attempt}/{max_attempts})",
                  file=sys.stderr)
            last_err = f"{hard_budget}초 하드 타임아웃 초과 (DNS/연결 지연 의심)"
        elif 'data' in result_box:
            return result_box['data'], None
        else:
            kind = result_box.get('error', ('other', RuntimeError('알 수 없는 오류')))[0]
            if kind == 'http':
                _, code, body = result_box['error']
                print(f"[UnifiedBook] AI판독({label}) HTTP 에러 {code} (시도 {attempt}/{max_attempts}): {body[:300]}", file=sys.stderr)
                last_err = f"HTTP {code}"
                if 400 <= code < 500:
                    # 인증/요청 형식/레이트리밋 등은 재시도해도 소용없으므로 즉시 중단
                    return None, last_err
            else:
                _, e = result_box['error']
                if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
                    # DNS 실패, 연결 거부/리셋, 타임아웃 등 네트워크/통신 계층 오류 -> 재시도 대상
                    print(f"[UnifiedBook] AI판독({label}) 통신 오류 (시도 {attempt}/{max_attempts}): {e}", file=sys.stderr)
                    last_err = f"통신 오류: {e}"
                else:
                    # JSON 파싱 실패 등 응답/코드 문제는 재시도해도 동일하게 실패할 가능성이 높음
                    print(f"[UnifiedBook] AI판독({label}) 예기치 않은 오류 (시도 {attempt}/{max_attempts}): {e}", file=sys.stderr)
                    return None, str(e)

        if attempt < max_attempts:
            wait = backoff_base * attempt
            print(f"[UnifiedBook] AI판독({label}) {wait:.1f}초 후 재시도 ({attempt}/{max_attempts})...")
            time.sleep(wait)

    print(f"[UnifiedBook] AI판독({label}) {max_attempts}회 재시도 모두 실패: {last_err}")
    return None, last_err


def extract_isbn_via_llm(text, api_key, endpoint=None, model=None):
    """구글 Gemini API 및 LiteLLM(OpenAI 호환) 프록시를 모두 지원하는 통합 지능형 판독 엔진.
    타임아웃/연결 오류 등 일시적 통신 장애에 대해서는 짧은 대기 후 최대 2회까지 자동 재시도한다
    (search()/apply()가 동기 응답해야 하므로, 전체 소요시간이 웹서버 타임아웃을 넘지 않도록
    타임아웃/재시도 횟수를 보수적으로 제한한다 — 아래 REQUEST_TIMEOUT 참고)."""
    if not text.strip():
        return None

    REQUEST_TIMEOUT = 8  # 이 값 × 재시도 횟수 + 대기시간이 전체 예산. 함부로 늘리지 말 것.

    use_litellm = bool(endpoint and endpoint.strip())
    _t0 = time.time()
    print(f"[UnifiedBook] AI판독: 호출 시작 (경로={'LiteLLM' if use_litellm else 'Gemini 공식'}, "
          f"모델={model or ('gemini/gemini-3.5-flash-lite' if use_litellm else 'gemini-3.5-flash-lite')}, "
          f"입력 텍스트 길이={len(text)}자)")

    prompt = (
        "다음 도서 판권지/본문 텍스트에서 ISBN 번호만 추출해줘.\n"
        "출력은 반드시 다른 미사여구 없이 JSON 형식으로만 해야 하며, 그 구조는 반드시 다음 스키마를 따라야 해:\n"
        "{\"isbn\": \"공백이나 하이픈을 제거한 오직 10자리 또는 13자리 숫자(마지막 X 허용) 문자열 (발견되지 않으면 빈 문자열)\"}\n\n"
        f"[텍스트 본문]\n{text}"
    )

    if use_litellm:
        url = endpoint.strip()
        target_model = model.strip() if model and model.strip() else "gemini/gemini-3.5-flash-lite"

        payload = {
            "model": target_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        headers = {'Content-Type': 'application/json'}
        if api_key and api_key.strip():
            headers['Authorization'] = f"Bearer {api_key.strip()}"

        res_data, err = _llm_post_with_retry(url, payload, headers, timeout=REQUEST_TIMEOUT, label="LiteLLM")
        print(f"[UnifiedBook] AI판독(LiteLLM) 통신 소요시간: {time.time() - _t0:.1f}초")
        if res_data is None:
            print(f"[UnifiedBook] AI판독(LiteLLM) 호출 최종 실패: {err}")
            return None

        choices = res_data.get('choices', [])
        if choices:
            raw_content = choices[0].get('message', {}).get('content', '').strip()
            try:
                res_json = json.loads(raw_content)
            except (ValueError, TypeError):
                print(f"[UnifiedBook] AI판독(LiteLLM) 응답 JSON 파싱 실패 (원본: {raw_content[:200]!r})")
                return None
            raw_isbn = res_json.get('isbn', '')
            clean = re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
            if validate_isbn13(clean) or validate_isbn10(clean):
                print(f"[UnifiedBook] AI판독(LiteLLM) 판독 성공 -> {clean}")
                return clean
            print(f"[UnifiedBook] AI판독(LiteLLM) 응답에서 유효한 ISBN을 찾지 못함 (원본 응답값: {raw_isbn!r})")
        else:
            print("[UnifiedBook] AI판독(LiteLLM) 응답에 choices가 없음")
        return None

    else:
        if not api_key:
            print("[UnifiedBook] AI판독: Gemini API Key가 설정 안 되어 있어 AI 호출 건너뜀")
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
                "maxOutputTokens": 100
            }
        }

        res_data, err = _llm_post_with_retry(url, payload, {'Content-Type': 'application/json'}, timeout=REQUEST_TIMEOUT, label="Gemini")
        print(f"[UnifiedBook] AI판독(Gemini) 통신 소요시간: {time.time() - _t0:.1f}초")
        if res_data is None:
            print(f"[UnifiedBook] AI판독(Gemini) 호출 최종 실패: {err}")
            return None

        candidates = res_data.get('candidates', [])
        if candidates:
            parts = candidates[0].get('content', {}).get('parts', [])
            if parts:
                raw_text = parts[0].get('text', '').strip()
                try:
                    res_json = json.loads(raw_text)
                except (ValueError, TypeError):
                    print(f"[UnifiedBook] AI판독(Gemini) 응답 JSON 파싱 실패 (원본: {raw_text[:200]!r})")
                    return None
                raw_isbn = res_json.get('isbn', '')
                clean = re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
                if validate_isbn13(clean) or validate_isbn10(clean):
                    print(f"[UnifiedBook] AI판독(Gemini) 판독 성공 -> {clean}")
                    return clean
                print(f"[UnifiedBook] AI판독(Gemini) 응답에서 유효한 ISBN을 찾지 못함 (원본 응답값: {raw_isbn!r})")
            else:
                print("[UnifiedBook] AI판독(Gemini) 응답에 parts가 없음")
        else:
            print("[UnifiedBook] AI판독(Gemini) 응답에 candidates가 없음")
        return None

def extract_isbn_from_epub(epub_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """EPUB 내부 컨테이너 구조 및 본문 파일 분석 후 ISBN 추출 (지능형 LLM 듀얼 분기 가동)"""
    print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB 판권지 스캔 시작: {epub_path!r}")
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
            
            for elem in opf_root.iter():
                if elem.tag.endswith('identifier') and elem.text:
                    clean = re.sub(r'[^0-9X]', '', elem.text.upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB OPF 메타데이터(identifier)에서 ISBN 발견 -> {clean}")
                        return clean, "LOCAL"
            
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
            
            num_spines = len(spine_item_ids)
            target_spines = list(range(min(8, num_spines)))
            if num_spines > 8:
                target_spines.extend(list(range(max(8, num_spines - 8), num_spines)))
            target_spines = sorted(list(set(target_spines)))
            
            opf_dir = os.path.dirname(opf_path)
            
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
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB 본문 샘플이 너무 짧아(20자 미만) 스캔 중단")
                return None, None
            
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
                                print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB 본문 정규식 스캔에서 ISBN 발견 -> {clean}")
                                return clean, "LOCAL"
                            elif validate_isbn10(clean):
                                isbn10_candidates.append(clean)
                    except Exception:
                        pass
                        
            if isbn10_candidates:
                print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB 정규식 완전일치는 없었지만 ISBN-10 후보 채택 -> {isbn10_candidates[0]}")
                return isbn10_candidates[0], "LOCAL"
                
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): EPUB 정규식 스캔 실패 -> AI(LLM)에게 판독 위탁")
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): AI 위탁도 실패 -> ISBN 추출 최종 실패")
            elif compiled_texts:
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): 정규식 스캔 실패했지만 LLM API Key/엔드포인트 미설정 -> AI 위탁 건너뜀")
                    
    except Exception:
        pass
    return None, None

def extract_isbn_from_pdf(pdf_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """PDF 메타데이터 및 전후면 판권 페이지 고속 스캔 (지능형 LLM 듀얼 분기 가동)"""
    print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): PDF 판권지 스캔 시작: {pdf_path!r}")
    if not PYPDF_AVAILABLE:
        print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): pypdf 미설치 -> PDF 스캔 건너뜀")
        return None, None
        
    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            num_pages = len(reader.pages)
            if num_pages == 0:
                return None, None
                
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
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): PDF 본문 샘플 추출 실패(빈 텍스트) -> 스캔 중단")
                return None, None
                
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
                
                text = re.sub(r'[\u2012-\u2015\u00ad.]', '-', text)
                
                if text.strip():
                    compiled_texts.append(text)
                
                for match in isbn_pat.findall(text):
                    clean = re.sub(r'[^0-9X]', '', match.upper())
                    if validate_isbn13(clean):
                        print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): PDF 본문 정규식 스캔에서 ISBN 발견 -> {clean}")
                        return clean, "LOCAL"
                    elif validate_isbn10(clean):
                        isbn10_candidates.append(clean)
                        
            if isbn10_candidates:
                print(f"[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): PDF 정규식 완전일치는 없었지만 ISBN-10 후보 채택 -> {isbn10_candidates[0]}")
                return isbn10_candidates[0], "LOCAL"
                
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): PDF 정규식 스캔 실패 -> AI(LLM)에게 판독 위탁")
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): AI 위탁도 실패 -> ISBN 추출 최종 실패")
            elif compiled_texts:
                print("[UnifiedBook] 3단계(책 파일에서 ISBN 직접 스캔): 정규식 스캔 실패했지만 LLM API Key/엔드포인트 미설정 -> AI 위탁 건너뜀")
                    
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
