# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.parse

def search_naver(query, client_id, client_secret):
    if not client_id or not client_secret:
        return []
    url = f"https://openapi.naver.com/v1/search/book.json?query={urllib.parse.quote(query)}&display=10"
    req = urllib.request.Request(url, headers={
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret,
        'User-Agent': 'Mozilla/5.0'
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('items', [])
            results = []
            for item in items:
                results.append({
                    'title': item.get('title'),
                    'author': item.get('author'),
                    'isbn': item.get('isbn', '').split(' ')[-1],  # 네이버는 종종 공백으로 isbn을 구분함
                    'publisher': item.get('publisher'),
                    'pubDate': item.get('pubdate'),
                    'cover': item.get('image'),
                    'description': item.get('description', ''),
                    'link': item.get('link')
                })
            return results
    except Exception as e:
        print(f"[Naver Error] {e}")
        return []

def search_naver_isbn(isbn, client_id, client_secret):
    return search_naver(isbn, client_id, client_secret)
