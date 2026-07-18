import urllib.request, urllib.parse, json

def search_google(query, api_key):
    params = {'q': query, 'maxResults': 10, 'langRestrict': 'ko'}
    if api_key: params['key'] = api_key
    url = f"https://www.googleapis.com/books/v1/volumes?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': i.get('volumeInfo', {}).get('title'), 
                     'author': ", ".join(i.get('volumeInfo', {}).get('authors', [])),
                     'publisher': i.get('volumeInfo', {}).get('publisher'), 
                     'pubDate': i.get('volumeInfo', {}).get('publishedDate'),
                     'cover': i.get('volumeInfo', {}).get('imageLinks', {}).get('thumbnail'), 
                     'description': i.get('volumeInfo', {}).get('description', ''),
                     'link': i.get('volumeInfo', {}).get('previewLink'), 'source': '구글'} 
                    for i in data.get('items', [])]
    except: return []