# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.parse

def search_google(query, api_key=None):
    url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=10"
    if api_key:
        url += f"&key={api_key}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('items', [])
            results = []
            for item in items:
                info = item.get('volumeInfo', {})
                isbn = ''
                for identifier in info.get('industryIdentifiers', []):
                    if identifier.get('type') in ['ISBN_13', 'ISBN_10']:
                        isbn = identifier.get('identifier')
                        break
                image_links = info.get('imageLinks', {})
                cover = image_links.get('thumbnail') or image_links.get('smallThumbnail', '')
                if cover.startswith('http://'):
                    cover = cover.replace('http://', 'https://')
                
                results.append({
                    'title': info.get('title'),
                    'author': ', '.join(info.get('authors', [])),
                    'isbn': isbn,
                    'publisher': info.get('publisher'),
                    'pubDate': info.get('publishedDate'),
                    'cover': cover,
                    'description': info.get('description', ''),
                    'link': info.get('infoLink')
                })
            return results
    except Exception as e:
        print(f"[Google Error] {e}")
        return []
