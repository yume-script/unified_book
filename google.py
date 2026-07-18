import urllib.request, urllib.parse, json

def search_google(query, api_key):
    params = {'q': query, 'maxResults': 10, 'langRestrict': 'ko'}
    if api_key: params['key'] = api_key
    url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
    
    # 구글 특유의 복잡한 ISBN 목록에서 깨끗한 단일 값을 추출하는 내부 함수
    def extract_isbn(volume_info):
        identifiers = volume_info.get('industryIdentifiers', [])
        isbn = ''
        for identifier in identifiers:
            # 13자리 ISBN이 존재하면 즉시 반환
            if identifier.get('type') == 'ISBN_13':
                return identifier.get('identifier', '')
            # 10자리 ISBN이 존재하면 후보군으로 보관
            elif identifier.get('type') == 'ISBN_10':
                isbn = identifier.get('identifier', '')
        return isbn

    try:
        with urllib.request.urlopen(url, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': i.get('volumeInfo', {}).get('title'), 
                     'author': ", ".join(i.get('volumeInfo', {}).get('authors', [])),
                     'publisher': i.get('volumeInfo', {}).get('publisher'), 
                     'pubDate': i.get('volumeInfo', {}).get('publishedDate'),
                     'cover': i.get('volumeInfo', {}).get('imageLinks', {}).get('thumbnail'), 
                     'description': i.get('volumeInfo', {}).get('description', ''),
                     'link': i.get('volumeInfo', {}).get('previewLink'), 'source': '구글',
                     'isbn': extract_isbn(i.get('volumeInfo', {}))} # ISBN 수집 키 추가
                    for i in data.get('items', [])]
    except: return []