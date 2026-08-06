# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json
import re

def search_naver(query, cid, csecret):
    """네이버 일반 도서 상세 검색 API (d_titl: 제목 전용 필드)"""
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_titl': query, 'display': 10})}")
    req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                     'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버',
                     'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''} 
                    for i in data.get('items', [])]
    except: return []

def search_naver_author(query, cid, csecret):
    """네이버 저자 검색용 함수.
    💡 네이버 도서 상세검색 API(book_adv.json)는 d_titl(제목)/d_isbn(ISBN)/d_publ(출판사) 필드만
    제공하고 저자 전용 필드가 없다. 예전에는 저자 축(author axis)도 search_naver()를 그대로 재사용해서
    저자명을 d_titl(제목)로 검색해버려 결과가 비어 있었다. 저자명은 기본 도서검색 API(book.json)의
    자유검색어(query)에 넣어 제목+저자를 함께 매칭하는 방식으로 우회한다."""
    url = "https://openapi.naver.com/v1/search/book.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'query': query, 'display': 10})}")
    req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                     'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버',
                     'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''} 
                    for i in data.get('items', [])]
    except: return []

def search_naver_isbn(isbn, cid, csecret):
    """네이버 ISBN 상세 일치 검색 API"""
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_isbn': isbn, 'display': 1})}")
    req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                     'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버',
                     'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''} 
                    for i in data.get('items', [])]
    except: return []
