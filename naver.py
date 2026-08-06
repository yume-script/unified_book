# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json
import re

def _fetch_naver(params, cid, csecret):
    """네이버 API 공통 호출 함수"""
    url = "https://openapi.naver.com/v1/search/book.json"
    # 파라미터를 URL 인코딩하여 쿼리 스트링 생성
    query_string = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query_string}")
    request.add_header("X-Naver-Client-Id", cid)
    request.add_header("X-Naver-Client-Secret", csecret)
    
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            res = response.read().decode('utf-8')
            data = json.loads(res)
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 
                     'author': i.get('author'),
                     'publisher': i.get('publisher'), 
                     'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 
                     'description': i.get('description', ''), 
                     'link': i.get('link'), 
                     'source': '네이버',
                     'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''} 
                    for i in data.get('items', [])]
    except Exception as e:
        print(f"네이버 API 에러: {e}")
        return []

def search_naver(query, cid, csecret):
    """제목 검색 (query 사용)"""
    return _fetch_naver({'query': query, 'display': 10}, cid, csecret)

def search_naver_author(query, cid, csecret):
    """저자 검색"""
    return _fetch_naver({'query': query, 'display': 10}, cid, csecret)

def search_naver_isbn(isbn, cid, csecret):
    """ISBN 검색 (query 파라미터로 통합 검색)"""
    return _fetch_naver({'query': isbn, 'display': 5}, cid, csecret)
