# -*- coding: utf-8 -*-
import os
import re
import sys
import zipfile
import html
import json
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

# ==========================================
# 💡 [Self-Healing] 필수 의존성 자동 감지 및 무중단 자동 설치 모듈
# ==========================================

def _auto_install_dependencies():
    """파이썬 및 시스템 패키지(poppler-utils 등)가 없을 경우 자동으로 설치를 시도합니다."""
    # 1. pypdf 자동 설치 확인
    try:
        import pypdf
    except ImportError:
        print("[자동 설치] 'pypdf' 패키지가 감지되지 않아 설치를 진행합니다...", file=sys.stderr)
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pypdf"])
        except Exception as e:
            print(f"[자동 설치 실패] pypdf 설치 중 에러 발생: {e}", file=sys.stderr)

    # 2. pdf2image 자동 설치 확인
    try:
        import pdf2image
    except ImportError:
        print("[자동 설치] 'pdf2image' 패키지가 감지되지 않아 설치를 진행합니다...", file=sys.stderr)
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pdf2image"])
        except Exception as e:
            print(f"[자동 설치 실패] pdf2image 설치 중 에러 발생: {e}", file=sys.stderr)

    # 3. 시스템 바이너리(pdftoppm - poppler-utils 소속) 감지 및 자동 설치 시도
    # (주의: root 권한이 아닐 경우 apt-get 실행이 거부될 수 있으므로 예외 처리를 동반합니다)
    try:
        from pdf2image.exceptions import PDFPopplerNotFoundException
        # 간단한 유효성 체크를 통해 poppler 바이너리 존재 여부 확인 가능
    except ImportError:
        pass

    # poppler-utils 유무 체크 (pdftoppm 명령어 존재 여부)
    poppler_exists = any(
        os.path.exists(os.path.join(path, "pdftoppm"))
        for path in os.environ["PATH"].split(os.pathsep)
    )
    if not poppler_exists and os.geteuid() == 0:  # root 권한일 때만 apt 자동 시도
        print("[자동 설치] 시스템 패키지 'poppler-utils'가 감지되지 않아 apt-get으로 설치를 시도합니다...", file=sys.stderr)
        try:
            subprocess.check_call(["apt-get", "update", "-qq"])
            subprocess.check_call(["apt-get", "install", "-y", "-qq", "poppler-utils"])
            print("[자동 설치 성공] poppler-utils 설치가 완료되었습니다.", file=sys.stderr)
        except Exception as e:
            print(f"[자동 설치 안내] system apt-get 설치 권한이 없거나 실패했습니다. 수동 설치가 필요할 수 있습니다: {e}", file=sys.stderr)

# 모듈 로드 시점에 자동 설치 루틴 1회 가동
_auto_install_dependencies()

# ------------------------------------------
# 라이브러리 최종 로드 상태 확정
# ------------------------------------------
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    from pdf2image import convert_from_path, pdfinfo_from_path
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False


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
        
    if len(clean_a) == 13 and len(clean_b) == 10:
        return clean_a[3:12] == clean_b[0:9]
    if len(clean_a) == 10 and len(clean_b) == 13:
        return clean_a[0:9] == clean_b[3:12]
        
    return False

def extract_isbn_via_llm(text, api_key, endpoint=None, model=None):
    """구글 Gemini API 및 LiteLLM(OpenAI 호환) 프록시를 모두 지원하는 통합 지능형 판독 엔진"""
    if not text.strip():
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
    
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
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 100
        }
    }
    
    if endpoint and endpoint.strip():
        url = endpoint.strip()
        target_model = model.strip() if model and model.strip() else "gemini/gemini-3.5-flash-lite"
        
        payload = {
            "model": target_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
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
        except Exception:
            pass
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
                            return clean
        except Exception:
            pass
            
    return None

def extract_isbn_via_llm_vision(images_bytes_list, api_key, endpoint=None, model=None):
    """비전(Vision) 판독 엔진"""
    if not images_bytes_list:
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
    prompt = (
        "이 도서 표지 또는 판권지 이미지에 적혀 있는 ISBN 번호를 찾아줘.\n"
        "출력은 반드시 다른 미사여구 없이 JSON 형식으로만 해야 하며 구조는 다음과 같아:\n"
        "{\"isbn\": \"공백이나 하이픈을 제거한 오직 10자리 또는 13자리 숫자 문자열 (발견되지 않으면 빈 문자열)\"}"
    )
    
    if endpoint and endpoint.strip():
        url = endpoint.strip()
        target_model = model.strip() if model and model.strip() else "gemini/gemini-3.5-flash-lite"
        
        content_list = [{"type": "text", "text": prompt}]
        for img_bytes in images_bytes_list:
            encoded = base64.b64encode(img_bytes).decode('utf-8')
            content_list.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}
            })
            
        payload = {
            "model": target_model,
            "messages": [{"role": "user", "content": content_list}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        headers = {'Content-Type': 'application/json'}
        if api_key and api_key.strip():
            headers['Authorization'] = f"Bearer {api_key.strip()}"
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req, timeout=25) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                choices = res_data.get('choices', [])
                if choices:
                    content = choices[0].get('message', {}).get('content', '').strip()
                    clean = re.sub(r'[^0-9X]', '', str(json.loads(content).get('isbn', '')).upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        return clean
        except Exception:
            pass
    else:
        if not api_key: return None
        parts_list = [{"text": prompt}]
        for img_bytes in images_bytes_list:
            encoded = base64.b64encode(img_bytes).decode('utf-8')
            parts_list.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": encoded
                }
            })
            
        payload = {
            "contents": [{"role": "user", "parts": parts_list}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1, "maxOutputTokens": 100}
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get('candidates', [])
                if candidates:
                    raw_text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                    clean = re.sub(r'[^0-9X]', '', str(json.loads(raw_text).get('isbn', '')).upper())
                    if validate_isbn13(clean) or validate_isbn10(clean):
                        return clean
        except Exception:
            pass
            
    return None

def extract_isbn_from_epub(epub_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """EPUB 내부 분석 후 ISBN 추출"""
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
                
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"
    except Exception:
        pass
    return None, None

def extract_isbn_from_pdf(pdf_path, gemini_key=None, llm_endpoint=None, llm_model=None):
    """PDF 분석 후 ISBN 추출 (텍스트 -> LLM 텍스트 -> 비전 AI)"""
    if not PYPDF_AVAILABLE:
        return None, None
        
    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            num_pages = len(reader.pages)
            if num_pages == 0:
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
                        return clean, "LOCAL"
                    elif validate_isbn10(clean):
                        isbn10_candidates.append(clean)
                        
            if isbn10_candidates:
                return isbn10_candidates[0], "LOCAL"
                
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and compiled_texts:
                full_text = "\n".join(compiled_texts)[:12000]
                llm_isbn = extract_isbn_via_llm(full_text, gemini_key, endpoint=llm_endpoint, model=llm_model)
                if llm_isbn:
                    return llm_isbn, "AI"
                    
            if (gemini_key or (llm_endpoint and llm_endpoint.strip())) and VISION_AVAILABLE:
                front_p = list(range(1, min(6, num_pages + 1)))
                back_p = list(range(max(6, num_pages - 4), num_pages + 1)) if num_pages > 5 else []
                vision_targets = sorted(list(set(front_p + back_p)))
                
                images_bytes = []
                for p_num in vision_targets:
                    try:
                        imgs = convert_from_path(pdf_path, first_page=p_num, last_page=p_num, dpi=120)
                        if imgs:
                            buffered = io.BytesIO()
                            imgs[0].save(buffered, format="JPEG", quality=80)
                            images_bytes.append(buffered.getvalue())
                    except Exception:
                        pass
                
                if images_bytes:
                    vision_isbn = extract_isbn_via_llm_vision(images_bytes, gemini_key, endpoint=llm_endpoint, model=llm_model)
                    if vision_isbn:
                        return vision_isbn, "AI-Vision"
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
