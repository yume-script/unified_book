import urllib.request, urllib.parse, json, re

def search_naver(query, cid, csecret):
    url = "https://openapi.naver.com/v1/search/book_adv.json"
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode({'d_titl': query, 'display': 10})}")
    req.add_header("X-Naver-Client-Id", cid); req.add_header("X-Naver-Client-Secret", csecret)
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode('utf-8'))
            return [{'title': re.sub('<[^<]+?>', '', i.get('title', '')), 'author': i.get('author'),
                     'publisher': i.get('publisher'), 'pubDate': i.get('pubdate'), 
                     'cover': i.get('image'), 'description': i.get('description', ''), 'link': i.get('link'), 'source': '네이버'} 
                    for i in data.get('items', [])]
    except: return []