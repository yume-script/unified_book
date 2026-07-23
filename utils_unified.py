# -*- coding: utf-8 -*-
import os
import re
import zipfile
import xml.etree.ElementTree as ET

def parse_bool(val, default=False):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ('true', '1', 't', 'y', 'yes', 'on')
    return default

def get_row_val(row, key):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        if hasattr(row, 'keys') and key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        if hasattr(row, '_mapping') and key in row._mapping:
            return row._mapping[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return None

def format_date(date_str):
    if not date_str:
        return ""
    clean = str(date_str).strip()
    match = re.search(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})', clean)
    if match:
        return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
    match_year = re.search(r'(\d{4})', clean)
    if match_year:
        return match_year.group(1)
    return clean

def get_high_res_url(cover_url, source_name):
    if not cover_url:
        return ""
    if source_name == '알라딘':
        cover_url = cover_url.replace('coversum', 'cover500').replace('sum', '500')
    return cover_url

def validate_isbn13(isbn):
    if not isbn:
        return False
    clean = re.sub(r'[^0-9]', '', str(isbn))
    if len(clean) != 13 or not clean.startswith(('978', '979')):
        return False
    try:
        digits = [int(c) for c in clean]
        total = sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:-1]))
        check = (10 - (total % 10)) % 10
        return check == digits[-1]
    except:
        return False

def validate_isbn10(isbn):
    if not isbn:
        return False
    clean = re.sub(r'[^0-9X]', '', str(isbn).upper())
    if len(clean) != 10:
        return False
    try:
        total = sum((10 - i) * (int(c) if c != 'X' else 10) for i, c in enumerate(clean))
        return total % 11 == 0
    except:
        return False

def compare_isbns(q_isbn, item_isbn):
    q_clean = re.sub(r'[^0-9X]', '', str(q_isbn).upper())
    item_clean = re.sub(r'[^0-9X]', '', str(item_isbn).upper())
    if not q_clean or not item_clean:
        return False
    if q_clean == item_clean:
        return True
    if len(q_clean) == 13 and len(item_clean) == 10:
        if q_clean.startswith('978'):
            core = q_clean[3:12]
            return core in item_clean
    if len(q_clean) == 10 and len(item_clean) == 13:
        if item_clean.startswith('978'):
            core = item_clean[3:12]
            return core in q_clean
    return False

def extract_isbn_from_epub(file_path):
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            namelist = z.namelist()
            opf_path = None
            for name in namelist:
                if name.endswith('.opf'):
                    opf_path = name
                    break
            if opf_path:
                with z.open(opf_path) as opf_file:
                    tree = ET.parse(opf_file)
                    root = tree.getroot()
                    for elem in root.iter():
                        if 'identifier' in elem.tag.lower():
                            text = elem.text
                            if text:
                                clean = re.sub(r'[^0-9X]', '', text.strip().upper())
                                if validate_isbn13(clean) or validate_isbn10(clean):
                    return clean, "LOCAL"
            for name in namelist[:20]:
                if any(name.endswith(ext) for ext in ['.html', '.xhtml', '.xml', '.opf', '.htm']):
                    try:
                        with z.open(name) as f:
                            content = f.read().decode('utf-8', errors='ignore')
                            matches = re.findall(r'(?:ISBN[-:._\s]*)?(?:97[89][-\s]*)?\d{1,5}[-\s]*\d+[-\s]*\d+[-\s]*[\dX]', content, re.IGNORECASE)
                            for m in matches:
                                clean = re.sub(r'[^0-9X]', '', m.upper())
                                if validate_isbn13(clean) or validate_isbn10(clean):
                                    return clean, "LOCAL"
                    except:
                        continue
    except:
        pass
    return None, None

def extract_isbn_from_pdf(file_path):
    try:
        with open(file_path, 'rb') as f:
            header = f.read(2048)
            matches = re.findall(r'(?:ISBN[-:._\s]*)?(?:97[89][-\s]*)?\d{1,5}[-\s]*\d+[-\s]*\d+[-\s]*[\dX]', header.decode('utf-8', errors='ignore'), re.IGNORECASE)
            for m in matches:
                clean = re.sub(r'[^0-9X]', '', m.upper())
                if validate_isbn13(clean) or validate_isbn10(clean):
                    return clean, "LOCAL"
    except:
        pass
    return None, None
