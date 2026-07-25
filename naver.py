# -*- coding: utf-8 -*-
import sys
import urllib.request
import urllib.parse
import urllib.error
import json
import re


def _strip_tags(text):
    """title/author/publisher 등 완전 이스케이프 대상 필드에서 잔존 HTML 태그 제거"""
    return re.sub('<[^<]+?>', '', text) if text else text


def _map_items(data):
    return [{'title': _strip_tags(i.get('title', '')), 'author': _strip_tags(i.get('author', '')),
             'publisher': _strip_tags(i.get('publisher', '')), 'pubDate': i.get('pubdate'),
             'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버',
             'isbn': i.get('isbn', '').split()[-1] if i.get('isbn') else ''}
            for i in data.get('items', [])]


def search_naver(query, cid, csecret):
    """네이버 일반 도서 상세 검색 API"""
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_titl': query, 'display': 10})}")
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return _map_items(data)
    except urllib.error.HTTPError as he:
        error_body = he.read().decode('utf-8', errors='ignore')
        print(f"[네이버 API HTTP 에러 {he.code}] query='{query}' 이유: {error_body}", file=sys.stderr)
    except urllib.error.URLError as ue:
        print(f"[네이버 API 연결 실패] query='{query}' 이유: {ue.reason}", file=sys.stderr)
    except json.JSONDecodeError as je:
        print(f"[네이버 API 응답 파싱 실패] query='{query}' 이유: {je}", file=sys.stderr)
    except Exception as e:
        print(f"[네이버 API 알 수 없는 에러] query='{query}' 사유: {str(e)}", file=sys.stderr)
    return []


def search_naver_isbn(isbn, cid, csecret):
    """네이버 ISBN 상세 일치 검색 API"""
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_isbn': isbn, 'display': 1})}")
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return _map_items(data)
    except urllib.error.HTTPError as he:
        error_body = he.read().decode('utf-8', errors='ignore')
        print(f"[네이버 ISBN API HTTP 에러 {he.code}] isbn='{isbn}' 이유: {error_body}", file=sys.stderr)
    except urllib.error.URLError as ue:
        print(f"[네이버 ISBN API 연결 실패] isbn='{isbn}' 이유: {ue.reason}", file=sys.stderr)
    except json.JSONDecodeError as je:
        print(f"[네이버 ISBN API 응답 파싱 실패] isbn='{isbn}' 이유: {je}", file=sys.stderr)
    except Exception as e:
        print(f"[네이버 ISBN API 알 수 없는 에러] isbn='{isbn}' 사유: {str(e)}", file=sys.stderr)
    return []
