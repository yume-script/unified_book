# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json
import re

def search_naver(query, cid, csecret):
    """네이버 일반 도서 검색"""
    url = "https://openapi.naver.com/v1/search/book.json" # 일반 검색은 book.json이 더 정확합니다
    params = {'query': query, 'display': 10}
    return _call_naver(url, params, cid, csecret)

def search_naver_isbn(isbn, cid, csecret):
    """네이버 ISBN 검색"""
    # ISBN 검색은 d_isbn 파라미터를 사용하되, book_adv.json을 사용합니다
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    params = {'d_isbn': isbn, 'display': 1}
    return _call_naver(url, params, cid, csecret)

def _call_naver(url, params, cid, csecret):
    """네이버 API 공통 호출 로직"""
    encoded_params = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{encoded_params}")
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csecret)
    
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            results = []
            for i in data.get('items', []):
                # ISBN 처리: "ISBN10 ISBN13" 형태이므로 가장 긴 것을 찾거나 마지막을 선택
                isbn_raw = i.get('isbn', '')
                isbn_list = isbn_raw.split()
                # 13자리가 우선, 없으면 마지막 값
                isbn_final = next((s for s in isbn_list if len(s) == 13), isbn_list[-1] if isbn_list else '')
                
                results.append({
                    'title': re.sub('<[^<]+?>', '', i.get('title', '')),
                    'author': i.get('author', '').replace('^', ', '),
                    'publisher': i.get('publisher', ''),
                    'pubDate': i.get('pubdate', ''),
                    'cover': i.get('image', ''),
                    'description': i.get('description', ''),
                    'link': i.get('link', ''),
                    'source': '네이버',
                    'isbn': isbn_final
                })
            return results
    except Exception as e:
        print(f"[Naver API Error] {e}")
        return []
