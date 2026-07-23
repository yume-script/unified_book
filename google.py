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

def search_google(query, api_key):
    """구글 도서 API 검색 (일반 및 ISBN 인덱스 병행 매칭)"""
    params = {'q': query, 'maxResults': 10, 'langRestrict': 'ko'}
    if api_key: params['key'] = api_key
    url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
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
    except: return []
