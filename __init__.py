# -*- coding: utf-8 -*-

# unified_book.py에 정의된 원본 클래스를 안전하게 임포트
try:
    from .unified_book import UnifiedBookMetadataProvider
except ImportError:
    from unified_book import UnifiedBookMetadataProvider

# BookOasis 메인 시스템이 플러그인 클래스를 로드할 때 호출하는 표준 진입점
def get_plugin_class():
    return UnifiedBookMetadataProvider

# 일부 메인 시스템 버전 호환성을 위한 별칭 등록
Plugin = UnifiedBookMetadataProvider
