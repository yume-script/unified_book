# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json

def search_aladin(query, ttbkey):
    """알라딘 일반 도서 검색 API"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': query, 'QueryType': 'Title', 'MaxResults': 10, 'output': 'js', 'Version': '20131101'}
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
            res = response.read().decode('utf-8')
            if res.endswith(';'): res = res[:-1]
            data = json.loads(res)
            return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                     'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                     'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
                     'isbn': i.get('isbn13') or i.get('isbn', '')} 
                    for i in data.get('item', [])]
    except: return []

def search_aladin_isbn(isbn, ttbkey):
    """알라딘 ISBN 일치 전용 검색 API"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': isbn, 'QueryType': 'ISBN', 'MaxResults': 1, 'output': 'js', 'Version': '20131101'}
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
            res = response.read().decode('utf-8')
            if res.endswith(';'): res = res[:-1]
            data = json.loads(res)
            return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                     'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                     'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
                     'isbn': i.get('isbn13') or i.get('isbn', '')} 
                    for i in data.get('item', [])]
    except: return []
