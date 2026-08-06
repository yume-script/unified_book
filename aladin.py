# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json

# 공통 함수: URL 호출 및 JSON 파싱
def _fetch_aladin(url, params):
    try:
        # SearchTarget=Book을 기본값으로 추가
        if 'SearchTarget' not in params:
            params['SearchTarget'] = 'Book'
            
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(full_url, timeout=7) as response:
            res = response.read().decode('utf-8')
            if res.endswith(';'): res = res[:-1]
            data = json.loads(res)
            return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                     'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                     'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
                     'isbn': i.get('isbn13') or i.get('isbn', '')} 
                    for i in data.get('item', [])]
    except Exception as e:
        print(f"Error: {e}")
        return []

def search_aladin(query, ttbkey):
    """제목 검색"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': query, 'QueryType': 'Title', 'MaxResults': 10, 'output': 'js', 'Version': '20131101'}
    return _fetch_aladin(url, params)

def search_aladin_author(query, ttbkey):
    """저자 검색"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': query, 'QueryType': 'Author', 'MaxResults': 10, 'output': 'js', 'Version': '20131101'}
    return _fetch_aladin(url, params)

def search_aladin_isbn(isbn, ttbkey):
    """ISBN 검색 - ItemLookUp API 사용으로 변경"""
    url = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    # ItemLookUp은 Query 대신 ItemId를 사용하며, ItemIdType을 명시해야 함
    params = {
        'ttbkey': ttbkey, 
        'ItemId': isbn, 
        'ItemIdType': 'ISBN13', # ISBN10 또는 ISBN13 선택
        'output': 'js', 
        'Version': '20131101'
    }
    return _fetch_aladin(url, params)
