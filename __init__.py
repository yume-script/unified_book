# -*- coding: utf-8 -*-
from .unified_book import UnifiedBookMetadataProvider

def get_plugin_class():
    return UnifiedBookMetadataProvider

Plugin = UnifiedBookMetadataProvider
