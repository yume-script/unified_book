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
    """알라딘 ISBN 일치 전용 검색 API (ItemLookUp 사용)"""
    # ItemSearch 대신 ItemLookUp 사용
    url = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    
    params = {
        'ttbkey': ttbkey, 
        'ItemId': isbn,          # ISBN 값을 ItemId로 전달
        'ItemIdType': 'ISBN13',  # ISBN13임을 명시
        'output': 'js', 
        'Version': '20131101'
    }
    
    try:
        # URL 구성 및 요청
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(full_url, timeout=7) as response:
            res = response.read().decode('utf-8')
            # 알라딘 JS 출력은 가끔 끝에 세미콜론이 붙음
            if res.endswith(';'): res = res[:-1]
            data = json.loads(res)
            
            # ItemLookUp 결과는 'item' 키 안에 리스트 형태로 들어옴
            items = data.get('item', [])
            return [{'title': i.get('title'), 'author': i.get('author'), 'publisher': i.get('publisher'),
                     'pubDate': i.get('pubDate'), 'cover': i.get('cover'), 
                     'description': i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
                     'isbn': i.get('isbn13') or i.get('isbn', '')} 
                    for i in items]
    except Exception as e:
        print(f"[Aladin API Error] {e}") # 에러 로그를 출력해서 확인해보세요
        return []
