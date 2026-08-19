"""Plex2iPod \u2014 sync Plex playlists and library to a Rockbox iPod.

The package is split by concern; importing it re-exports the pieces that
callers and tests reach for most often.
"""

from .version import APP_VERSION
from .audio import AudioConverter
from .config import CONFIG_DEFAULTS, ConfigManager
from .naming import (MAX_COMPONENT_LEN, find_path_collisions, ipod_rel_path,
                     sanitize_component, sort_key)
from .paths import app_dir, resource_dirs
from .platform_io import (IS_WINDOWS, detect_ipod_roots, disk_usage,
                          eject_volume,
                          list_ipod_roots, looks_like_ipod,
                          music_folder_name)
from .plexapi import (PlexClient, plex_check_pin, plex_create_pin,
                      plex_list_servers, plex_pick_connection)
from .sync import SyncEngine
from .theme import THEMES
from .widgets import (CapacityBar, GlassCard, StyledButton,
                      StyledCheckbutton, StyledEntry)
from .app import App

__all__ = [
    "APP_VERSION", "App", "AudioConverter", "CONFIG_DEFAULTS", "CapacityBar",
    "ConfigManager", "GlassCard", "IS_WINDOWS", "MAX_COMPONENT_LEN",
    "PlexClient", "StyledButton", "StyledCheckbutton", "StyledEntry",
    "SyncEngine", "THEMES", "app_dir", "detect_ipod_roots", "eject_volume",
    "find_path_collisions", "ipod_rel_path", "list_ipod_roots",
    "disk_usage", "looks_like_ipod", "music_folder_name", "plex_check_pin",
    "plex_create_pin", "plex_list_servers", "plex_pick_connection",
    "resource_dirs", "sanitize_component", "sort_key",
]
