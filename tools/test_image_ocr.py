# test_image_ocr.py
# -*- coding: utf-8 -*-
import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error
import base64

# --------------------------------------------------
# 🔑 [설정] 본인의 API 키를 여기에 입력해 주세요
# --------------------------------------------------
GEMINI_API_KEY = ""  # 구글 AI Studio에서 발급받은 Gemini API Key
# --------------------------------------------------

def extract_isbn_from_image_source(image_source, api_key):
    """
    💡 제미나이 3.5 Flash-Lite 멀티모달(Vision) 기능을 활용해 
       이미지(URL 또는 로컬 파일)에 적힌 ISBN을 다이렉트로 판독합니다.
    """
    if not api_key:
        print("❌ 오류: GEMINI_API_KEY가 설정되지 않았습니다.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
    
    # 1. 이미지 데이터 준비 (URL 또는 로컬 파일 경로 판단)
    image_bytes = None
    mime_type = "image/jpeg"
    
    try:
        if image_source.startswith("http://") or image_source.startswith("https://"):
            print(f"🌐 웹 이미지 다운로드 중: {image_source}")
            req = urllib.request.Request(image_source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as res:
                image_bytes = res.read()
                if ".png" in image_source.lower():
                    mime_type = "image/png"
                elif ".webp" in image_source.lower():
                    mime_type = "image/webp"
        else:
            print(f"📂 로컬 이미지 읽는 중: {image_source}")
            if not os.path.exists(image_source):
                print(f"❌ 오류: 파일이 존재하지 않습니다 -> {image_source}")
                return None
            with open(image_source, "rb") as f:
                image_bytes = f.read()
            lower_path = image_source.lower()
            if lower_path.endswith(".png"):
                mime_type = "image/png"
            elif lower_path.endswith(".webp"):
                mime_type = "image/webp"
                
    except Exception as e:
        print(f"❌ 이미지 로드 실패 사유: {e}")
        return None

    # Base64 인코딩
    encoded_image = base64.b64encode(image_bytes).decode('utf-8')

    # 2. 제미나이 비전 프롬프트 설계
    prompt = (
        "이 이미지(도서 표지, 판권지 또는 본문 캡처)에 적혀 있는 ISBN 번호를 찾아줘.\n"
        "출력은 반드시 다른 미사여구 없이 JSON 형식으로만 해야 하며, 구조는 반드시 다음 스키마를 따를 것:\n"
        "{\"isbn\": \"공백이나 하이픈을 제거한 오직 10자리 또는 13자리 숫자(마지막 X 허용) 문자열 (이미지 내에 ISBN이 없다면 빈 문자열)\"}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded_image
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 100
        }
    }

    print("🤖 제미나이 3.5 Flash-Lite 비전(Vision) 모델에 분석 요청 중...")
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            candidates = res_data.get('candidates', [])
            if candidates:
                parts = candidates[0].get('content', {}).get('parts', [])
                if parts:
                    raw_text = parts[0].get('text', '').strip()
                    res_json = json.loads(raw_text)
                    raw_isbn = res_json.get('isbn', '')
                    clean_isbn = re.sub(r'[^0-9X]', '', str(raw_isbn).upper())
                    return clean_isbn
    except urllib.error.HTTPError as he:
        error_msg = he.read().decode('utf-8', errors='ignore')
        print(f"❌ [Gemini API HTTP 에러 {he.code}] 이유: {error_msg}")
    except Exception as e:
        print(f"❌ [Gemini API 에러] 사유: {str(e)}")
        
    return None

def main():
    print("=" * 60)
    print("🖼️ 제미나이 멀티모달(이미지) ISBN 판독 테스트기")
    print("=" * 60)
    
    if not GEMINI_API_KEY.strip():
        print("⚠️ 주의: 스크립트 상단의 GEMINI_API_KEY 변수에 본인의 키를 입력해 주세요.")
        
    source = input("\n테스트할 이미지 주소(URL) 또는 로컬 파일 경로를 입력해 주세요:\n👉 ").strip()
    source = source.strip('\'"')
    
    if not source:
        print("❌ 입력값이 비어 있습니다.")
        return
        
    isbn = extract_isbn_from_image_source(source, GEMINI_API_KEY)
    
    print("-" * 60)
    if isbn and len(isbn) in (10, 13):
        print(f"🎉 이미지 판독 성공!")
        print(f"👉 추출된 ISBN 번호: {isbn}")
    else:
        print("❌ 이미지 판독 실패: 해당 이미지에서 유효한 ISBN을 찾아내지 못했습니다.")
    print("=" * 60)

if __name__ == "__main__":
    main()
