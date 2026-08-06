# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json

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

def search_google(query, api_key, field=None):
    """구글 도서 API 검색 (디버깅 및 접근성 강화 버전)"""
    
    # 쿼리 구성
    q_value = f"{field}:{query}" if field and query else query
    params = {'q': q_value, 'maxResults': 10}
    if api_key: 
        params['key'] = api_key
    
    url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
    
    # 디버깅: URL 출력
    #print(f"[DEBUG] Request URL: {url}")
    
    # 브라우저인 것처럼 헤더 설정 (차단 방지)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            # 디버깅: 검색된 아이템 수 출력
            total_items = data.get('totalItems', 0)
            print(f"[DEBUG] Total Items Found: {total_items}")
            
            results = []
            for i in data.get('items', []):
                vol = i.get('volumeInfo', {})
                results.append({
                    'title': vol.get('title'), 
                    'author': ", ".join(vol.get('authors', [])),
                    'publisher': vol.get('publisher'), 
                    'pubDate': vol.get('publishedDate'),
                    'cover': vol.get('imageLinks', {}).get('thumbnail'), 
                    'description': vol.get('description', ''),
                    'link': vol.get('previewLink'), 
                    'source': '구글',
                    'isbn': extract_isbn(vol)
                })
            return results
    except Exception as e:
        print(f"[DEBUG] Error occurred: {e}")
        return []
