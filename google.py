# -*- coding: utf-8 -*-
import sys
import re
import urllib.request
import urllib.parse
import urllib.error
import json


def _strip_tags(text):
    """title/author/publisher 등 완전 이스케이프 대상 필드에서 잔존 HTML 태그 제거"""
    return re.sub('<[^<]+?>', '', text) if text else text


def extract_isbn(volume_info):
    """구글 도서 구조에서 가용성 높은 ISBN을 추출하는 내부 보조 함수"""
    identifiers = volume_info.get('industryIdentifiers', [])
    isbn = ''
    for identifier in identifiers:
        if identifier.get('type') in ('ISBN_13', 'ISBN_10'):
            isbn = identifier.get('identifier', '')
            if identifier.get('type') == 'ISBN_13':
                break
    return isbn


def search_google(query, api_key):
    """구글 도서 API 검색 (일반 및 ISBN 인덱스 병행 매칭)"""
    params = {'q': query, 'maxResults': 10, 'langRestrict': 'ko'}
    if api_key:
        params['key'] = api_key
    url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            results = []
            for i in data.get('items', []):
                vol = i.get('volumeInfo', {})
                results.append({
                    'title': _strip_tags(vol.get('title')),
                    'author': _strip_tags(", ".join(vol.get('authors', []))),
                    'publisher': _strip_tags(vol.get('publisher')),
                    'pubDate': vol.get('publishedDate'),
                    'cover': vol.get('imageLinks', {}).get('thumbnail'),
                    'description': vol.get('description', ''),
                    'link': vol.get('previewLink'),
                    'source': '구글',
                    'isbn': extract_isbn(vol)
                })
            return results
    except urllib.error.HTTPError as he:
        error_body = he.read().decode('utf-8', errors='ignore')
        print(f"[구글 Books API HTTP 에러 {he.code}] query='{query}' 이유: {error_body}", file=sys.stderr)
    except urllib.error.URLError as ue:
        print(f"[구글 Books API 연결 실패] query='{query}' 이유: {ue.reason}", file=sys.stderr)
    except json.JSONDecodeError as je:
        print(f"[구글 Books API 응답 파싱 실패] query='{query}' 이유: {je}", file=sys.stderr)
    except Exception as e:
        print(f"[구글 Books API 알 수 없는 에러] query='{query}' 사유: {str(e)}", file=sys.stderr)
    return []
