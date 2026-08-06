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
    """구글 도서 API 검색 (일반 및 필드 한정 검색 병행 지원).

    💡 이전에는 title/author/isbn 축 모두 field 없이 'q=검색어'로만 질의해서,
    저자명으로 검색해도 구글이 이를 저자 필드로 한정하지 못해 정확도가 떨어졌다.
    field에 Google Books 쿼리 연산자를 지정하면 'inauthor:홍길동'처럼 필드를 한정해 검색한다.

    field: None(자유검색, 기본값) | 'intitle'(제목) | 'inauthor'(저자) | 'isbn'
    """
    q_value = f"{field}:{query}" if field and query else query
    params = {'q': q_value, 'maxResults': 10, 'langRestrict': 'ko'}
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
