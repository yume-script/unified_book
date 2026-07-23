# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.parse

def search_aladin(query, ttbkey):
    if not ttbkey:
        return []
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {
        'ttbkey': ttbkey,
        'Query': query,
        'QueryType': 'Keyword',
        'MaxResults': 10,
        'start': 1,
        'SearchTarget': 'Book',
        'output': 'js',
        'Version': '20131101'
    }
    try:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            res_body = response.read().decode('utf-8')
            if res_body.endswith(';'):
                res_body = res_body[:-1]
            data = json.loads(res_body)
            items = data.get('item', [])
            results = []
            for item in items:
                results.append({
                    'title': item.get('title'),
                    'author': item.get('author'),
                    'isbn': item.get('isbn13') or item.get('isbn', ''),
                    'publisher': item.get('publisher'),
                    'pubDate': item.get('pubDate'),
                    'cover': item.get('cover'),
                    'description': item.get('description', ''),
                    'link': item.get('link')
                })
            return results
    except Exception as e:
        print(f"[Aladin Error] {e}")
        return []

def search_aladin_isbn(isbn, ttbkey):
    if not ttbkey or not isbn:
        return []
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {
        'ttbkey': ttbkey,
        'Query': isbn,
        'QueryType': 'ISBN',
        'MaxResults': 5,
        'start': 1,
        'SearchTarget': 'Book',
        'output': 'js',
        'Version': '20131101'
    }
    try:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            res_body = response.read().decode('utf-8')
            if res_body.endswith(';'):
                res_body = res_body[:-1]
            data = json.loads(res_body)
            items = data.get('item', [])
            results = []
            for item in items:
                results.append({
                    'title': item.get('title'),
                    'author': item.get('author'),
                    'isbn': item.get('isbn13') or item.get('isbn', ''),
                    'publisher': item.get('publisher'),
                    'pubDate': item.get('pubDate'),
                    'cover': item.get('cover'),
                    'description': item.get('description', ''),
                    'link': item.get('link')
                })
            return results
    except Exception as e:
        print(f"[Aladin ISBN Error] {e}")
        return []
