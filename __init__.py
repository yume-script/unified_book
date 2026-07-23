# -*- coding: utf-8 -*-
from .unified_book import UnifiedBookMetadataProvider

# BookOasis 메인 시스템이 플러그인을 인식하도록 클래스를 반환하거나 내보냄
def get_plugin_class():
    return UnifiedBookMetadataProvider

# 일부 버전의 BookOasis 호환성을 위해 클래스 자체를 직접 노출
Plugin = UnifiedBookMetadataProvider
