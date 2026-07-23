# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json
import re

def search_naver(query, cid, csecret):
    """네이버 일반 도서 상세 검색 API"""
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

