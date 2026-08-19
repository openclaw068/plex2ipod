"""Reading and writing config.json."""

import json
import os

from .paths import app_dir

CONFIG_DEFAULTS = {
    "plex_url": "http://localhost:32400",
    "plex_token": "",
    # Full iPod root path: 'E:\\' on Windows, '/media/you/IPOD' on Linux.
    # Empty means "auto-detect on first run".
    "ipod_root": "",
    # Stable client id for plex.tv sign-in (generated on first use).
    "client_id": "",
    "downsample_on_sync": True,
    "theme": "dark",
}


class ConfigManager:
    def __init__(self):
        self.path = os.path.join(app_dir(), "config.json")

    def load(self):
        if not os.path.exists(self.path):
            self.save(CONFIG_DEFAULTS)
            return dict(CONFIG_DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Migrate the old Windows-only "ipod_drive": "E" key to the
            # cross-platform "ipod_root" path.
            if not data.get("ipod_root") and data.get("ipod_drive"):
                letter = str(data["ipod_drive"]).strip().rstrip(":\\")
                if letter:
                    data["ipod_root"] = letter[0].upper() + ":\\"
            data.pop("ipod_drive", None)
            for k, v in CONFIG_DEFAULTS.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError):
            self.save(CONFIG_DEFAULTS)
            return dict(CONFIG_DEFAULTS)

    def save(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
