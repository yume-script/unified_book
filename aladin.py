# -*- coding: utf-8 -*-
import sys
import re
import urllib.request
import urllib.parse
import urllib.error
import json


def _strip_tags(text):
    """title/author/publisher 등 완전 이스케이프 대상 필드에서 잔존 HTML 태그 제거"""
    return re.sub('<[^<]+?>', '', text) if text else text


def _parse_aladin_response(res_text):
    """알라딘 JSONP 유사 응답(끝에 세미콜론이 붙는 경우가 있음)을 안전하게 JSON으로 변환"""
    text = res_text.strip()
    if text.endswith(';'):
        text = text[:-1]
    return json.loads(text)


def _map_items(data):
    return [{'title': _strip_tags(i.get('title')), 'author': _strip_tags(i.get('author')),
             'publisher': _strip_tags(i.get('publisher')),
             'pubDate': i.get('pubDate'), 'cover': i.get('cover'),
             'description': i.get('fullDescription') or i.get('description', ''), 'link': i.get('link'), 'source': '알라딘',
             'isbn': i.get('isbn13') or i.get('isbn', '')}
            for i in data.get('item', [])]


def search_aladin(query, ttbkey):
    """알라딘 일반 도서 검색 API"""
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    params = {'ttbkey': ttbkey, 'Query': query, 'QueryType': 'Title', 'MaxResults': 10, 'output': 'js', 'Version': '20131101'}
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
            data = _parse_aladin_response(response.read().decode('utf-8'))
            return _map_items(data)
    except urllib.error.HTTPError as he:
        error_body = he.read().decode('utf-8', errors='ignore')
        print(f"[알라딘 API HTTP 에러 {he.code}] query='{query}' 이유: {error_body}", file=sys.stderr)
    except urllib.error.URLError as ue:
        print(f"[알라딘 API 연결 실패] query='{query}' 이유: {ue.reason}", file=sys.stderr)
    except json.JSONDecodeError as je:
        print(f"[알라딘 API 응답 파싱 실패] query='{query}' 이유: {je}", file=sys.stderr)
    except Exception as e:
        print(f"[알라딘 API 알 수 없는 에러] query='{query}' 사유: {str(e)}", file=sys.stderr)
    return []


def search_aladin_isbn(isbn, ttbkey):
    """알라딘 ISBN 단건조회 전용 API (ItemLookUp) - 검색이 아닌 조회이므로 오탐 없이 정확히 일치"""
    url = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    item_id_type = "ISBN13" if len(re.sub(r'[^0-9X]', '', isbn.upper())) == 13 else "ISBN"
    params = {
        'ttbkey': ttbkey, 'ItemId': isbn, 'ItemIdType': item_id_type,
        'output': 'js', 'Version': '20131101', 'Cover': 'Big', 'OptResult': 'fulldescription'
    }
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=7) as response:
            data = _parse_aladin_response(response.read().decode('utf-8'))
            return _map_items(data)
    except urllib.error.HTTPError as he:
        error_body = he.read().decode('utf-8', errors='ignore')
        print(f"[알라딘 ISBN 단건조회 HTTP 에러 {he.code}] isbn='{isbn}' 이유: {error_body}", file=sys.stderr)
    except urllib.error.URLError as ue:
        print(f"[알라딘 ISBN 단건조회 연결 실패] isbn='{isbn}' 이유: {ue.reason}", file=sys.stderr)
    except json.JSONDecodeError as je:
        print(f"[알라딘 ISBN 단건조회 응답 파싱 실패] isbn='{isbn}' 이유: {je}", file=sys.stderr)
    except Exception as e:
        print(f"[알라딘 ISBN 단건조회 알 수 없는 에러] isbn='{isbn}' 사유: {str(e)}", file=sys.stderr)
    return []
