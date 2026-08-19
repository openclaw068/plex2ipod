"""The Tk application window and all of its behaviour."""

import os
import subprocess
import threading
import time
import uuid
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree

from .audio import AudioConverter
from .config import CONFIG_DEFAULTS, ConfigManager
from .naming import find_path_collisions, ipod_rel_path, sort_key
from .paths import resource_dirs
from .platform_io import (IS_WINDOWS, detect_ipod_roots, disk_usage,
                          eject_volume, list_ipod_roots, music_folder_name)
from .plexapi import (PlexClient, plex_check_pin, plex_create_pin,
                      plex_list_servers, plex_pick_connection)
from .sync import SyncEngine
from .theme import THEMES
from .version import APP_VERSION
from .widgets import (CapacityBar, GlassCard, StyledButton,
                      StyledCheckbutton, StyledEntry)

# Leave a little headroom rather than filling the volume to the last byte:
# FAT32 needs room for directory entries, and the download writes a .part
# sidecar before renaming it into place.
FREE_SPACE_MARGIN = 32 * 1024 * 1024


class App:
    def __init__(self):
        self.cfg_mgr = ConfigManager()
        self.cfg = self.cfg_mgr.load()
        self.plex = None
        self.sync_engine = None
        self.audio = AudioConverter()
        self._cancel = False
        self._syncing = False
        self._busy = False
        self._log_history = []
        self._section_id = None

        self._playlist_vars = {}
        self._playlist_widgets = []
        self._tree_checked = {}
        self._tree_data = {}
        self._tree_loaded = set()  # item ids whose children have been fetched
        self._tree_pending_check = set()  # items whose children should auto-check on load
        self._artists_loaded = False

        # Capacity accounting. The playlist cache holds track lists fetched
        # purely to size a selection; the iPod index is the set of relative
        # paths already on the device, so those tracks cost no new space.
        self._playlist_track_cache = {}
        self._playlist_fetching = set()
        self._ipod_index = None
        self._indexed_root = None
        self._capacity = None
        self._capacity_after = None

        # Bumped every time the widget tree is torn down and rebuilt. Async
        # library loads carry the value they started with and drop their
        # results if it no longer matches: ttk hands out item ids like
        # "I001" per widget, so a new Treeview reuses them and a late
        # callback would otherwise insert under an unrelated node.
        self._ui_generation = 0

        # Manage iPod tab state
        self._manage_checked = {}  # iid -> bool
        self._manage_data = {}     # iid -> {"type": ..., "path": ..., "size": ...}
        self._manage_loaded = False

        # iPod auto-detect state
        self._ipod_announced = None   # last root we logged as "detected"
        self._signin_in_progress = False
        self._signin_cancel = False
        self._signin_win = None

        self._current_theme = self.cfg.get("theme", "dark")
        self.t = THEMES[self._current_theme]

        self._build_ui()
        # Start watching for an iPod being plugged in.
        self.root.after(800, self._poll_ipod)

    def _client_id(self):
        """Stable per-install id for plex.tv sign-in; created on demand."""
        cid = self.cfg.get("client_id")
        if not cid:
            cid = str(uuid.uuid4())
            self.cfg["client_id"] = cid
            self.cfg_mgr.save(self.cfg)
        return cid

    # ---- UI construction ----

    def _apply_win11_chrome(self):
        """Enable dark title bar, rounded corners, and Mica frosted
        backdrop on Windows 11. No-op on other platforms / older builds."""
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            dwmapi = ctypes.windll.dwmapi
            DwmSetWindowAttribute = dwmapi.DwmSetWindowAttribute
            DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD,
            ]
            DwmSetWindowAttribute.restype = ctypes.c_long

            def set_attr(attr, value):
                v = ctypes.c_int(value)
                DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

            # Dark title bar (Windows 10 20H1+ / Windows 11)
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 — follow active theme
            is_dark = getattr(self, "_current_theme", "dark") == "dark"
            set_attr(20, 1 if is_dark else 0)

            # Rounded window corners (Windows 11)
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33; value 2 = round
            set_attr(33, 2)

            # Mica frosted backdrop (Windows 11 22H2+)
            # DWMWA_SYSTEMBACKDROP_TYPE = 38; value 2 = Mica
            set_attr(38, 2)

            # Keep alpha just under 1.0 so Windows keeps the layered
            # window style active — -transparentcolor only works on
            # layered windows. Cards look fully solid at 0.995.
            try:
                self.root.attributes("-alpha", 0.995)
            except tk.TclError:
                pass
        except Exception:
            # Any failure here is purely cosmetic — never block app launch
            pass

    # ---- custom borderless title bar ----

    RESIZE_GRIP = 6      # pixels of edge that act as resize handles

    def _install_custom_titlebar(self):
        """Remove the native Windows title bar, build a custom one, and
        wire up drag/min/max/close + edge resize handles. Wrapped in a
        try/except so any failure leaves the app with the native bar."""
        if os.name != "nt":
            return
        try:
            self.root.overrideredirect(True)
            self._fix_taskbar_visibility()
            self.root.after(20, self._apply_win11_chrome)
            self.root.bind("<Map>", self._on_root_map)
            self._maximized = False
            self._restore_geom = None
            self._chrome_widgets = []
            self._build_chrome()
        except Exception:
            try:
                self.root.overrideredirect(False)
            except Exception:
                pass

    def _build_chrome(self):
        """Build the title bar + caption buttons + resize grips. Called
        on initial install AND when the theme is toggled."""
        # Title bar
        self._titlebar = tk.Frame(
            self.root, bg=self.t["bg_secondary"], height=32,
        )
        self._titlebar.pack(side="top", fill="x", before=self._main
                            if hasattr(self, "_main") else None)
        self._titlebar.pack_propagate(False)
        self._chrome_widgets.append(self._titlebar)

        self._titledrag = tk.Frame(
            self._titlebar, bg=self.t["bg_secondary"],
        )
        self._titledrag.pack(side="left", fill="both", expand=True)

        tk.Label(
            self._titledrag, text="  Plex2iPod",
            bg=self.t["bg_secondary"], fg=self.t["fg_dim"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0), pady=4)

        for w in (self._titlebar, self._titledrag,
                  *self._titledrag.winfo_children()):
            w.bind("<ButtonPress-1>", self._titlebar_press)
            w.bind("<B1-Motion>", self._titlebar_drag)
            w.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        self._make_caption_buttons()
        self._install_resize_grips()

    def _rebuild_chrome(self):
        """Tear down and rebuild title bar + grips (used on theme switch)."""
        if os.name != "nt":
            return
        for w in getattr(self, "_chrome_widgets", []):
            try:
                w.destroy()
            except Exception:
                pass
        self._chrome_widgets = []
        self._build_chrome()

    def _on_root_map(self, _event=None):
        # Reapply chrome after un-minimize so Mica/round corners stick
        self.root.after(20, self._apply_win11_chrome)

    def _fix_taskbar_visibility(self):
        """After overrideredirect, the window can disappear from the
        taskbar. Toggle WS_EX_APPWINDOW to put it back."""
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            self.root.withdraw()
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            self.root.after(10, self.root.deiconify)
        except Exception:
            pass

    def _make_caption_buttons(self):
        """Min / Max / Close drawn as small canvas widgets on the title bar."""
        bar = self._titlebar
        # Pack right-to-left so order reads min, max, close
        for label, callback, hover in (
            ("\u2715", self._close_window, "#e81123"),    # X
            ("\u25a1", self._toggle_maximize, self.t["border_light"]),  # square
            ("\u2013", self._minimize_window, self.t["border_light"]),  # en dash
        ):
            btn = tk.Label(
                bar, text=label, width=4, bg=self.t["bg_secondary"],
                fg=self.t["fg_dim"], font=("Segoe UI", 11),
                cursor="hand2",
            )
            btn.pack(side="right", fill="y")
            btn.bind("<Enter>",
                     lambda e, b=btn, h=hover: b.configure(
                         bg=h, fg="white"))
            btn.bind("<Leave>",
                     lambda e, b=btn: b.configure(
                         bg=self.t["bg_secondary"], fg=self.t["fg_dim"]))
            btn.bind("<Button-1>", lambda e, c=callback: c())

    def _install_resize_grips(self):
        """Add invisible edge frames that act as resize handles."""
        g = self.RESIZE_GRIP
        bg = self.t["bg"]
        top = tk.Frame(self.root, bg=bg, height=g, cursor="sb_v_double_arrow")
        top.place(x=0, y=0, relwidth=1.0, height=g)
        bot = tk.Frame(self.root, bg=bg, height=g, cursor="sb_v_double_arrow")
        bot.place(x=0, rely=1.0, y=-g, relwidth=1.0, height=g)
        lef = tk.Frame(self.root, bg=bg, width=g, cursor="sb_h_double_arrow")
        lef.place(x=0, y=0, relheight=1.0, width=g)
        rig = tk.Frame(self.root, bg=bg, width=g, cursor="sb_h_double_arrow")
        rig.place(relx=1.0, x=-g, y=0, relheight=1.0, width=g)

        for grip, edge in ((top, "n"), (bot, "s"),
                           (lef, "w"), (rig, "e")):
            grip.bind("<ButtonPress-1>",
                      lambda e, ed=edge: self._resize_press(e, ed))
            grip.bind("<B1-Motion>",
                      lambda e, ed=edge: self._resize_drag(e, ed))
            grip.lift()
            self._chrome_widgets.append(grip)
        self._resize_data = None

    # ---- title bar drag ----

    def _titlebar_press(self, event):
        if self._maximized:
            return
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _titlebar_drag(self, event):
        if self._maximized:
            return
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ---- min / max / close ----

    def _minimize_window(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            self.root.iconify()

    def _work_area(self):
        """The usable desktop rectangle as (x, y, width, height).

        Asks Windows for the work area, which already excludes the taskbar
        wherever the user put it and is correct on scaled displays. Falls
        back to the full screen minus a taskbar-sized strip if the call is
        unavailable.
        """
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [("left", wintypes.LONG),
                                ("top", wintypes.LONG),
                                ("right", wintypes.LONG),
                                ("bottom", wintypes.LONG)]

                rect = RECT()
                SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(
                        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                    width = rect.right - rect.left
                    height = rect.bottom - rect.top
                    if width > 0 and height > 0:
                        return rect.left, rect.top, width, height
            except Exception:
                pass
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        return 0, 0, sw, max(sh - 48, 200)

    def _toggle_maximize(self):
        if not self._maximized:
            self._restore_geom = self.root.geometry()
            x, y, width, height = self._work_area()
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self._maximized = True
        else:
            if self._restore_geom:
                self.root.geometry(self._restore_geom)
            self._maximized = False

    def _close_window(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---- edge resize ----

    def _resize_press(self, event, edge):
        self._resize_data = {
            "edge": edge,
            "x": event.x_root,
            "y": event.y_root,
            "w": self.root.winfo_width(),
            "h": self.root.winfo_height(),
            "wx": self.root.winfo_x(),
            "wy": self.root.winfo_y(),
        }

    def _resize_drag(self, event, edge):
        d = self._resize_data
        if not d:
            return
        dx = event.x_root - d["x"]
        dy = event.y_root - d["y"]
        min_w = 750
        min_h = 550
        new_w, new_h = d["w"], d["h"]
        new_x, new_y = d["wx"], d["wy"]
        if "e" in edge:
            new_w = max(min_w, d["w"] + dx)
        if "s" in edge:
            new_h = max(min_h, d["h"] + dy)
        if "w" in edge:
            new_w = max(min_w, d["w"] - dx)
            new_x = d["wx"] + (d["w"] - new_w)
        if "n" in edge:
            new_h = max(min_h, d["h"] - dy)
            new_y = d["wy"] + (d["h"] - new_h)
        self.root.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Plex2iPod")
        self.root.geometry("900x700")
        self.root.minsize(750, 550)
        # On Windows, paint the window background with a sentinel color set
        # as -transparentcolor, so the gaps around cards become click-through
        # transparent while the cards stay fully opaque. This trick only
        # exists on Windows; elsewhere we just use the theme background.
        self._transparent_key = "#010101"
        self._use_transparency = False
        if IS_WINDOWS:
            try:
                self.root.attributes("-transparentcolor", self._transparent_key)
                self._use_transparency = True
            except tk.TclError:
                self._use_transparency = False
        self.root.configure(bg=self._win_bg())

        # Apply Windows 11 chrome (dark title bar + rounded corners + Mica
        # frosted backdrop). Silently no-ops on older Windows or non-Windows.
        self._apply_win11_chrome()

        # Window icon — search beside the script and inside the PyInstaller bundle
        try:
            for d in resource_dirs():
                p = os.path.join(d, "Plex2iPod.ico")
                if os.path.exists(p):
                    self.root.iconbitmap(p)
                    break
        except Exception:
            pass

        # Remove the native title bar and install our own custom chrome.
        # This is a no-op on non-Windows, but works on Win10/11.
        self._install_custom_titlebar()

        # Main container
        self._main = tk.Frame(self.root, bg=self._win_bg())
        self._main.pack(fill="both", expand=True, padx=16, pady=(4, 12))

        self._build_header()
        self._build_settings_card()
        self._build_tabs()
        self._build_sync_bar()
        self._build_log()

        # populate from config
        self._url_var.set(self.cfg.get("plex_url", CONFIG_DEFAULTS["plex_url"]))
        self._token_var.set(self.cfg.get("plex_token", ""))
        saved_root = self.cfg.get("ipod_root", "")
        if not saved_root or not os.path.exists(saved_root):
            ipods = detect_ipod_roots()
            saved_root = ipods[0] if ipods else ""
            if saved_root:
                self._ipod_announced = saved_root
        self._ipod_root_var.set(saved_root)

    def _build_header(self):
        header = tk.Frame(self._main, bg=self.t["bg"])
        header.pack(fill="x", pady=(0, 12))

        # Title
        title_frame = tk.Frame(header, bg=self.t["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text="Plex2iPod",
            bg=self.t["bg"], fg=self.t["fg_heading"],
            font=("Segoe UI", 20, "bold"),
        ).pack(side="left")

        tk.Label(
            title_frame, text="   Rockbox Manager",
            bg=self.t["bg"], fg=self.t["fg_dim"],
            font=("Segoe UI", 11),
        ).pack(side="left", pady=(6, 0))

        tk.Label(
            title_frame, text=f"  v{APP_VERSION}",
            bg=self.t["bg"], fg=self.t["fg_dim"],
            font=("Segoe UI", 9),
        ).pack(side="left", pady=(9, 0))

        # Theme toggle: [moon] [switch] [sun] — the icon matching the
        # active mode is highlighted. Common pattern on modern web apps.
        is_dark = self._current_theme == "dark"
        self._theme_moon = tk.Label(
            header, text="\U0001F319",
            bg=self.t["bg"],
            fg=self.t["accent"] if is_dark else self.t["fg_dim"],
            font=("Segoe UI Emoji", 11), cursor="hand2",
        )
        self._theme_moon.pack(side="right", padx=(0, 4), pady=(4, 0))
        self._theme_moon.bind("<Button-1>", self._toggle_theme)

        self._theme_btn = tk.Canvas(
            header, width=50, height=26, highlightthickness=0,
            bd=0, bg=self.t["bg"], cursor="hand2",
        )
        self._theme_btn.pack(side="right", padx=(0, 4), pady=(4, 0))
        self._theme_btn.bind("<Button-1>", self._toggle_theme)
        self._draw_theme_toggle()

        self._theme_sun = tk.Label(
            header, text="\u2600",
            bg=self.t["bg"],
            fg=self.t["fg_dim"] if is_dark else self.t["accent"],
            font=("Segoe UI Emoji", 13), cursor="hand2",
        )
        self._theme_sun.pack(side="right", padx=(0, 6), pady=(4, 0))
        self._theme_sun.bind("<Button-1>", self._toggle_theme)

    def _draw_theme_toggle(self):
        c = self._theme_btn
        c.delete("all")
        t = self.t
        is_dark = self._current_theme == "dark"

        # track
        track_color = t["accent"] if is_dark else t["border"]
        c.create_arc(0, 0, 26, 26, start=90, extent=180, fill=track_color, outline=track_color)
        c.create_arc(24, 0, 50, 26, start=270, extent=180, fill=track_color, outline=track_color)
        c.create_rectangle(13, 0, 37, 26, fill=track_color, outline=track_color)

        # knob
        knob_x = 37 if is_dark else 13
        c.create_oval(knob_x - 10, 3, knob_x + 10, 23, fill="white", outline="white")

        # icon
        if is_dark:
            c.create_text(13, 13, text="\u263e", fill="white", font=("Segoe UI", 9))
        else:
            c.create_text(37, 13, text="\u2600", fill=track_color, font=("Segoe UI", 9))

    # ---- iPod location helpers (cross-platform) ----

    def _style_combobox(self):
        s = ttk.Style()
        s.configure(
            "Custom.TCombobox",
            fieldbackground=self.t["bg_input"],
            background=self.t["bg_input"],
            foreground=self.t["fg"],
            arrowcolor=self.t["fg_dim"],
            bordercolor=self.t["border"],
            lightcolor=self.t["border"],
            darkcolor=self.t["border"],
            relief="flat",
        )
        s.map(
            "Custom.TCombobox",
            fieldbackground=[("readonly", self.t["bg_input"])],
            foreground=[("readonly", self.t["fg"])],
        )
        # The dropdown popup is a Tk Listbox styled via the option DB.
        self.root.option_add("*TCombobox*Listbox.background", self.t["bg_input"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.t["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.t["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

    def _refresh_ipod_roots(self):
        ipods = detect_ipod_roots()
        values = ipods or list_ipod_roots()
        self._ipod_combo["values"] = values
        cur = self._ipod_root()
        if ipods and (not cur or not os.path.exists(cur)):
            self._ipod_root_var.set(ipods[0])
        if ipods:
            self._log_msg(f"iPod detected: {', '.join(ipods)}")
        elif values:
            self._log_msg(
                "No iPod recognized, but these drives/mounts exist: "
                f"{', '.join(values)}. Pick one or type the path.")
        else:
            self._log_msg(
                "No removable drives/mounts detected. Plug in the iPod "
                "(and on Linux, open it once in your file manager so it "
                "mounts), then click the refresh button — or type the path.")
        self._update_ipod_status()

    def _update_ipod_status(self):
        root = self._ipod_root()
        if root and os.path.exists(root):
            self._ipod_status_var.set("✓ found")
            return True
        self._ipod_status_var.set("not found" if root else "")
        return False

    # How often the heartbeat runs. While we are still hunting for an iPod
    # we poll briskly so plugging one in feels instant; once one is selected
    # and present there is nothing to discover, so we back right off.
    POLL_SEARCHING_MS = 3000
    POLL_SETTLED_MS = 15000

    def _poll_ipod(self):
        """Background heartbeat: auto-select an iPod when one is plugged in,
        and keep the dropdown / status in sync. Never overrides a valid
        manual selection.

        Scanning is deliberately skipped once an iPod is already selected,
        or while another operation is running. detect_ipod_roots() lists
        every mount point to see whether it looks like an iPod, and doing
        that every few seconds keeps a hard-drive iPod permanently spun up
        and competes for I/O with an in-flight sync. Confirming the current
        selection still exists is a single cheap stat. Use the refresh
        button to re-enumerate on demand.
        """
        delay = self.POLL_SEARCHING_MS
        try:
            cur = self._ipod_root()
            cur_valid = bool(cur) and os.path.exists(cur)

            if cur_valid or self._busy or self._syncing:
                self._update_ipod_status()
                delay = self.POLL_SETTLED_MS
                return

            ipods = detect_ipod_roots()
            try:
                self._ipod_combo["values"] = ipods or list_ipod_roots()
            except tk.TclError:
                pass
            if ipods:
                self._ipod_root_var.set(ipods[0])
                if self._ipod_announced != ipods[0]:
                    self._ipod_announced = ipods[0]
                    self._log_msg(f"iPod detected: {ipods[0]}")
            else:
                self._ipod_announced = None
            self._update_ipod_status()
            self._maybe_refresh_ipod_index()
        finally:
            self.root.after(delay, self._poll_ipod)

    def _ipod_root(self):
        """The selected iPod root path, normalized. Accepts a bare drive
        letter on Windows ('E' -> 'E:\\')."""
        r = self._ipod_root_var.get().strip()
        if not r:
            return ""
        if IS_WINDOWS and len(r) <= 3 and r[0].isalpha():
            return r[0].upper() + ":\\"
        return r

    def _ipod_music_root(self):
        root = self._ipod_root()
        if not root:
            return ""
        return os.path.join(root, music_folder_name(root))

    def _build_settings_card(self):
        self._settings_card = GlassCard(
            self._main, self.t, height=100, bg=self.t["bg"],
        )
        self._settings_card.pack(fill="x", pady=(0, 10))
        inner = self._settings_card.inner

        # Row 1: URL + Token
        row1 = tk.Frame(inner, bg=self.t["bg_card"])
        row1.pack(fill="x", pady=(0, 8))

        tk.Label(row1, text="Plex URL", bg=self.t["bg_card"],
                 fg=self.t["fg_dim"], font=("Segoe UI", 9)).pack(side="left")
        self._url_var = tk.StringVar()
        self._url_entry = StyledEntry(row1, self.t, textvariable=self._url_var, width=32)
        self._url_entry.pack(side="left", padx=(6, 16))

        tk.Label(row1, text="Token", bg=self.t["bg_card"],
                 fg=self.t["fg_dim"], font=("Segoe UI", 9)).pack(side="left")
        self._token_var = tk.StringVar()
        self._token_entry = StyledEntry(row1, self.t, textvariable=self._token_var,
                                        width=26, show="\u2022")
        self._token_entry.pack(side="left", padx=(6, 16))

        self._signin_btn = StyledButton(
            row1, "Sign in to Plex", self.t, command=self._on_plex_signin,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=130, height=30, radius=6,
            font=("Segoe UI", 9),
        )
        self._signin_btn.pack(side="left")

        # Row 2: iPod (auto-detected) + connect
        row2 = tk.Frame(inner, bg=self.t["bg_card"])
        row2.pack(fill="x", pady=(0, 8))

        tk.Label(row2, text="iPod", bg=self.t["bg_card"],
                 fg=self.t["fg_dim"], font=("Segoe UI", 9)).pack(side="left")
        # Editable combobox: auto-detected iPods, or type a path as a
        # safety net if detection misses the mount point.
        self._style_combobox()
        self._ipod_root_var = tk.StringVar()
        self._ipod_combo = ttk.Combobox(
            row2, textvariable=self._ipod_root_var,
            values=detect_ipod_roots() or list_ipod_roots(),
            width=16 if IS_WINDOWS else 34,
            style="Custom.TCombobox", font=("Segoe UI", 10),
        )
        self._ipod_combo.pack(side="left", padx=(6, 4))

        self._ipod_refresh = StyledButton(
            row2, "↻", self.t, command=self._refresh_ipod_roots,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=30, height=30, radius=6,
            font=("Segoe UI", 12),
        )
        self._ipod_refresh.pack(side="left", padx=(0, 8))

        self._ipod_status_var = tk.StringVar(value="")
        tk.Label(row2, textvariable=self._ipod_status_var, bg=self.t["bg_card"],
                 fg=self.t["fg_dim"], font=("Segoe UI", 9)).pack(side="left")

        self._connect_btn = StyledButton(
            row2, "Connect", self.t, command=self._on_connect,
            width=100, height=30, radius=6,
        )
        self._connect_btn.pack(side="right")

        # Row 3: Status
        self._status_var = tk.StringVar(value="Not connected")
        self._status_label = tk.Label(
            inner, textvariable=self._status_var,
            bg=self.t["bg_card"], fg=self.t["fg_dim"],
            font=("Segoe UI", 9), anchor="w",
        )
        self._status_label.pack(fill="x")

    # ---- cross-platform mouse wheel ----

    @staticmethod
    def _wheel_units(event):
        """Normalize a wheel event into a yview_scroll 'units' delta.

        The three platforms report scrolling differently:
          - X11 (Linux): Button-4 / Button-5 presses, no usable .delta
          - Windows:     <MouseWheel> with .delta in multiples of 120
          - macOS:       <MouseWheel> with small .delta values (often 1),
                         which must NOT be divided by 120 or they floor to
                         zero and nothing scrolls
        Returns 0 when the event carries no scroll information.
        """
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        try:
            delta = int(event.delta)
        except (AttributeError, TypeError, ValueError):
            return 0
        if not delta:
            return 0
        if abs(delta) >= 120:
            return -delta // 120
        return -delta

    def _bind_mousewheel(self, widget, canvas, recurse=False):
        """Make `widget` scroll `canvas` with the wheel on every platform.

        Binds <MouseWheel> (Windows/macOS) alongside <Button-4>/<Button-5>
        (X11). With recurse=True, also binds the widget's children — Tk
        delivers the event to the specific widget under the pointer, and
        child widgets are not covered by a parent's binding.
        """
        def on_wheel(event):
            units = self._wheel_units(event)
            if units:
                canvas.yview_scroll(units, "units")
            return "break"

        targets = [widget]
        if recurse:
            targets.extend(widget.winfo_children())
        for target in targets:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                target.bind(seq, on_wheel)

    def _build_tabs(self):
        # Themed scrollbar style — used by both tabs
        sb_style = ttk.Style()
        sb_style.theme_use("default")
        sb_style.layout("Custom.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "children": [("Vertical.Scrollbar.thumb",
                              {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns",
            }),
        ])
        sb_style.configure(
            "Custom.Vertical.TScrollbar",
            background=self.t["scrollbar"],
            troughcolor=self.t["bg_card"],
            bordercolor=self.t["bg_card"],
            arrowcolor=self.t["bg_card"],
            lightcolor=self.t["scrollbar"],
            darkcolor=self.t["scrollbar"],
            gripcount=0,
            relief="flat",
            borderwidth=0,
            width=10,
        )
        sb_style.map(
            "Custom.Vertical.TScrollbar",
            background=[
                ("active", self.t["scrollbar_hover"]),
                ("pressed", self.t["accent"]),
            ],
            troughcolor=[("!disabled", self.t["bg_card"])],
        )

        # Custom tab bar
        self._tab_bar = tk.Frame(self._main, bg=self.t["bg"])
        self._tab_bar.pack(fill="x")

        self._active_tab = tk.IntVar(value=0)
        tab_names = ["Playlists", "Library", "Manage iPod"]
        self._tab_btns = []
        for i, name in enumerate(tab_names):
            btn = tk.Label(
                self._tab_bar, text=f"  {name}  ", cursor="hand2",
                font=("Segoe UI", 9, "bold"),
                padx=10, pady=3,
            )
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, idx=i: self._select_tab(idx))
            self._tab_btns.append(btn)

        # Tab content area
        self._tab_container = GlassCard(
            self._main, self.t, bg=self.t["bg"], radius=12, pad=12,
        )
        self._tab_container.pack(fill="both", expand=True, pady=(0, 10))

        self._tab_frames = []

        # -- Playlists tab --
        pl_frame = tk.Frame(self._tab_container.inner, bg=self.t["bg_card"])
        self._tab_frames.append(pl_frame)

        pl_btn_row = tk.Frame(pl_frame, bg=self.t["bg_card"])
        pl_btn_row.pack(fill="x", pady=(0, 8))
        self._pl_sel_btn = StyledButton(
            pl_btn_row, "Select All", self.t, command=self._pl_select_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=90, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._pl_sel_btn.pack(side="left", padx=(0, 6))
        self._pl_desel_btn = StyledButton(
            pl_btn_row, "Deselect All", self.t, command=self._pl_deselect_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=100, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._pl_desel_btn.pack(side="left")

        # scrollable playlist list
        self._pl_canvas = tk.Canvas(
            pl_frame, bg=self.t["bg_card"], highlightthickness=0, bd=0,
        )
        pl_sb = ttk.Scrollbar(
            pl_frame, orient="vertical", command=self._pl_canvas.yview,
            style="Custom.Vertical.TScrollbar",
        )
        self._pl_inner = tk.Frame(self._pl_canvas, bg=self.t["bg_card"])
        self._pl_inner.bind(
            "<Configure>",
            lambda e: self._pl_canvas.configure(scrollregion=self._pl_canvas.bbox("all")),
        )
        self._pl_canvas.create_window((0, 0), window=self._pl_inner, anchor="nw")
        self._pl_canvas.configure(yscrollcommand=pl_sb.set)
        pl_sb.pack(side="right", fill="y")
        self._pl_canvas.pack(side="left", fill="both", expand=True)

        self._bind_mousewheel(self._pl_canvas, self._pl_canvas)
        self._bind_mousewheel(self._pl_inner, self._pl_canvas)

        # -- Library tab --
        lib_frame = tk.Frame(self._tab_container.inner, bg=self.t["bg_card"])
        self._tab_frames.append(lib_frame)

        lib_btn_row = tk.Frame(lib_frame, bg=self.t["bg_card"])
        lib_btn_row.pack(fill="x", pady=(0, 8))
        self._lib_sel_btn = StyledButton(
            lib_btn_row, "Select All", self.t, command=self._lib_select_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=90, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._lib_sel_btn.pack(side="left", padx=(0, 6))
        self._lib_desel_btn = StyledButton(
            lib_btn_row, "Deselect All", self.t, command=self._lib_deselect_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=100, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._lib_desel_btn.pack(side="left")

        tree_frame = tk.Frame(lib_frame, bg=self.t["bg_card"])
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Custom.Treeview",
            background=self.t["bg_card"],
            foreground=self.t["fg"],
            fieldbackground=self.t["bg_card"],
            borderwidth=0,
            font=("Segoe UI", 10),
            rowheight=28,
        )
        style.configure(
            "Custom.Treeview.Heading",
            background=self.t["bg_secondary"],
            foreground=self.t["fg_dim"],
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Custom.Treeview",
            background=[("selected", self.t["accent2"])],
            foreground=[("selected", self.t["fg"])],
        )
        style.layout("Custom.Treeview", [
            ("Custom.Treeview.treearea", {"sticky": "nswe"}),
        ])

        self._tree = ttk.Treeview(
            tree_frame, columns=("info",), selectmode="none",
            show="tree headings", style="Custom.Treeview",
        )
        self._tree.heading("#0", text="Name", anchor="w")
        self._tree.heading("info", text="Info", anchor="w")
        self._tree.column("#0", width=420)
        self._tree.column("info", width=120)

        tree_sb = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self._tree.yview,
            style="Custom.Vertical.TScrollbar",
        )
        self._tree.configure(yscrollcommand=tree_sb.set)
        tree_sb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        # -- Manage iPod tab --
        manage_frame = tk.Frame(self._tab_container.inner, bg=self.t["bg_card"])
        self._tab_frames.append(manage_frame)

        m_btn_row = tk.Frame(manage_frame, bg=self.t["bg_card"])
        m_btn_row.pack(fill="x", pady=(0, 8))

        self._manage_refresh_btn = StyledButton(
            m_btn_row, "\u21bb  Refresh", self.t,
            command=self._scan_ipod,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=100, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._manage_refresh_btn.pack(side="left", padx=(0, 6))

        self._manage_sel_btn = StyledButton(
            m_btn_row, "Select All", self.t, command=self._manage_select_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=90, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._manage_sel_btn.pack(side="left", padx=(0, 6))

        self._manage_desel_btn = StyledButton(
            m_btn_row, "Deselect All", self.t, command=self._manage_deselect_all,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=100, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._manage_desel_btn.pack(side="left", padx=(0, 6))

        self._rebuild_db_btn = StyledButton(
            m_btn_row, "\u21ba  Rebuild Rockbox DB", self.t,
            command=self._on_rebuild_rockbox_db,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=170, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._rebuild_db_btn.pack(side="left", padx=(0, 6))

        self._downsample_existing_btn = StyledButton(
            m_btn_row, "\u21e9  Downsample 24-bit Tracks", self.t,
            command=self._on_downsample_existing,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=210, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._downsample_existing_btn.pack(side="left", padx=(0, 6))

        self._verify_repair_btn = StyledButton(
            m_btn_row, "\U0001f527  Verify && Repair", self.t,
            command=self._on_verify_repair,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=160, height=28, radius=6,
            font=("Segoe UI", 9),
        )
        self._verify_repair_btn.pack(side="left", padx=(0, 6))

        self._manage_status_var = tk.StringVar(value="")
        tk.Label(
            m_btn_row, textvariable=self._manage_status_var,
            bg=self.t["bg_card"], fg=self.t["fg_dim"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(12, 0))

        # Action row packed FIRST with side="bottom" so it's always reserved
        # at the bottom of the tab even when the tree expands.
        m_action_row = tk.Frame(manage_frame, bg=self.t["bg_card"])
        m_action_row.pack(side="bottom", fill="x", pady=(8, 0))

        m_tree_frame = tk.Frame(manage_frame, bg=self.t["bg_card"])
        m_tree_frame.pack(fill="both", expand=True)

        self._manage_tree = ttk.Treeview(
            m_tree_frame, columns=("info",), selectmode="none",
            show="tree headings", style="Custom.Treeview",
        )
        self._manage_tree.heading("#0", text="On iPod", anchor="w")
        self._manage_tree.heading("info", text="Size", anchor="w")
        self._manage_tree.column("#0", width=420)
        self._manage_tree.column("info", width=120)

        m_tree_sb = ttk.Scrollbar(
            m_tree_frame, orient="vertical", command=self._manage_tree.yview,
            style="Custom.Vertical.TScrollbar",
        )
        self._manage_tree.configure(yscrollcommand=m_tree_sb.set)
        m_tree_sb.pack(side="right", fill="y")
        self._manage_tree.pack(fill="both", expand=True)

        self._manage_tree.bind("<Button-1>", self._on_manage_click)

        # bottom row: option + remove button (frame already created above)
        self._update_m3u_var = tk.BooleanVar(value=True)
        self._update_m3u_cb = StyledCheckbutton(
            m_action_row,
            "Also remove deleted tracks from playlist (.m3u) files",
            self._update_m3u_var, self.t,
        )
        self._update_m3u_cb.pack(side="left")

        self._manage_remove_btn = StyledButton(
            m_action_row, "\u2716  Remove Selected from iPod", self.t,
            command=self._on_remove_from_ipod,
            bg=self.t["accent"], hover_bg=self.t["accent_hover"],
            fg=self.t["sync_btn_fg"],
            width=200, height=28, radius=6,
            font=("Segoe UI", 10, "bold"),
        )
        self._manage_remove_btn.pack(side="right")

        # show first tab
        self._select_tab(0)

    def _select_tab(self, idx):
        self._active_tab.set(idx)
        for i, btn in enumerate(self._tab_btns):
            if i == idx:
                btn.configure(
                    bg=self.t["tab_active"], fg=self.t["accent"],
                )
            else:
                btn.configure(
                    bg=self.t["tab_inactive"], fg=self.t["fg_dim"],
                )
        for i, frame in enumerate(self._tab_frames):
            if i == idx:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        # lazy load artists on first Library tab view
        if idx == 1 and not self._artists_loaded and self._section_id and self.plex:
            self._load_artists()
        # lazy scan iPod on first Manage tab view
        if idx == 2 and not self._manage_loaded:
            self._scan_ipod()

    def _build_sync_bar(self):
        bar = tk.Frame(self._main, bg=self.t["bg"])
        bar.pack(fill="x", pady=(0, 6))

        btn_frame = tk.Frame(bar, bg=self.t["bg"])
        btn_frame.pack(fill="x", pady=(0, 6))

        self._sync_btn = StyledButton(
            btn_frame, "Sync Selected to iPod", self.t,
            command=self._on_sync,
            bg=self.t["sync_btn"], hover_bg=self.t["sync_btn_hover"],
            fg=self.t["sync_btn_fg"],
            width=210, height=30, radius=8,
            font=("Segoe UI", 11, "bold"),
        )
        self._sync_btn.pack(side="left", padx=(0, 10))

        self._cancel_btn = StyledButton(
            btn_frame, "Cancel", self.t,
            command=self._on_cancel,
            bg=self.t["cancel_btn"], hover_bg=self.t["cancel_hover"],
            fg=self.t["fg"], width=90, height=30, radius=8,
            font=("Segoe UI", 10),
        )
        self._cancel_btn.pack(side="left", padx=(0, 10))
        self._cancel_btn.set_state(False)

        self._eject_btn = StyledButton(
            btn_frame, "\u23cf  Eject iPod", self.t,
            command=self._on_eject,
            bg=self.t["accent2"], hover_bg=self.t["border_light"],
            fg=self.t["fg"], width=120, height=30, radius=8,
            font=("Segoe UI", 10),
        )
        self._eject_btn.pack(side="right")

        # Downsample-on-sync checkbox (between cancel and eject).
        # Disabled/unchecked if ffmpeg isn't bundled.
        initial = bool(self.cfg.get("downsample_on_sync", True)) and self.audio.available
        self._downsample_var = tk.BooleanVar(value=initial)
        self._downsample_cb = StyledCheckbutton(
            btn_frame,
            "Downsample 24-bit FLACs to 16-bit",
            self._downsample_var, self.t,
        )
        self._downsample_cb.pack(side="left", padx=(6, 0))
        if not self.audio.available:
            # grey out visually and block clicks
            try:
                self._downsample_cb.set_enabled(False)
            except AttributeError:
                pass

        # iPod capacity: how full the device is and what the selection
        # would add. Sits above the progress bar so the two read as one
        # block about the sync that is about to happen.
        self._capacity_bar = CapacityBar(bar, self.t)
        self._capacity_bar.pack(fill="x", pady=(6, 2))

        self._capacity_var = tk.StringVar(value="")
        self._capacity_label = tk.Label(
            bar, textvariable=self._capacity_var, bg=self.t["bg"],
            fg=self.t["fg_dim"], font=("Segoe UI", 9), anchor="w",
        )
        self._capacity_label.pack(fill="x")

        # progress bar (canvas-drawn)
        self._progress_canvas = tk.Canvas(
            bar, height=10, highlightthickness=0, bd=0, bg=self.t["bg"],
        )
        self._progress_canvas.pack(fill="x", pady=(4, 2))
        self._progress_val = 0

        self._schedule_capacity_update(delay=50)

    def _draw_progress(self):
        c = self._progress_canvas
        c.delete("all")
        w = c.winfo_width()
        h = 10
        r = 5
        t = self.t
        # background track
        self._draw_pill(c, 0, 0, w, h, r, t["progress_bg"])
        # filled portion
        if self._progress_val > 0:
            fw = max(int(w * self._progress_val / 100), 2 * r)
            self._draw_pill(c, 0, 0, fw, h, r, t["progress_fill"])

    def _draw_pill(self, canvas, x1, y1, x2, y2, r, fill):
        if x2 - x1 < 2 * r:
            r = max((x2 - x1) // 2, 1)
        canvas.create_arc(x1, y1, x1 + 2 * r, y2, start=90, extent=180,
                          fill=fill, outline=fill)
        canvas.create_arc(x2 - 2 * r, y1, x2, y2, start=270, extent=180,
                          fill=fill, outline=fill)
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)

    def _build_log(self):
        self._log = tk.Text(
            self._main, height=7, state="disabled", wrap="word",
            bg=self.t["log_bg"], fg=self.t["log_fg"],
            font=("Cascadia Code", 9),
            relief="flat", bd=0, padx=12, pady=8,
            insertbackground=self.t["accent"],
            selectbackground=self.t["accent"],
            selectforeground="white",
            highlightthickness=1,
            highlightbackground=self.t["border"],
            highlightcolor=self.t["border"],
        )
        self._log.pack(fill="x")
        # Replay any accumulated history so the log survives rebuilds
        # (e.g. theme toggle). The UI widget is disposable; history is not.
        if self._log_history:
            self._log.configure(state="normal")
            self._log.insert("end", "\n".join(self._log_history) + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")

    # ---- theme toggle ----

    def _toggle_theme(self, event=None):
        self._current_theme = "light" if self._current_theme == "dark" else "dark"
        self.t = THEMES[self._current_theme]
        self.cfg["theme"] = self._current_theme
        self.cfg_mgr.save(self.cfg)
        self._apply_theme()

    def _win_bg(self):
        """Background for the root window and structural frames. On Windows
        with transparency active this is the click-through sentinel; on
        Linux (and if transparency isn't available) it's the theme color so
        the window looks normal instead of near-black."""
        return self._transparent_key if self._use_transparency else self.t["bg"]

    # ---- capturing and restoring UI state across a rebuild ----

    def _stale(self, generation):
        """True if a callback belongs to a widget tree we have since torn
        down. Its item ids now refer to different rows, so its result has
        to be dropped rather than inserted somewhere arbitrary."""
        return generation is not None and generation != self._ui_generation

    @staticmethod
    def _capture_tree(tree, data_map, checked_map, loaded_set=None):
        """Record a Treeview as nested plain data.

        Keeps the "Loading..." placeholder rows too — they carry no entry
        in data_map, and _trigger_lazy_load recognizes them by text, so a
        restored branch still expands on demand.
        """
        def node(iid):
            return {
                "text": tree.item(iid, "text"),
                "values": tuple(tree.item(iid, "values")),
                "open": bool(tree.item(iid, "open")),
                "info": data_map.get(iid),
                "checked": bool(checked_map.get(iid)),
                "loaded": bool(loaded_set) and iid in loaded_set,
                "children": [node(child) for child in tree.get_children(iid)],
            }
        return [node(iid) for iid in tree.get_children()]

    @staticmethod
    def _restore_tree(tree, nodes, data_map, checked_map, loaded_set=None):
        """Rebuild a Treeview from _capture_tree output, under fresh ids."""
        def insert(parent, node):
            iid = tree.insert(parent, "end", text=node["text"],
                              values=node["values"], open=node["open"])
            if node["info"] is not None:
                data_map[iid] = node["info"]
                checked_map[iid] = node["checked"]
            if node["loaded"] and loaded_set is not None:
                loaded_set.add(iid)
            for child in node["children"]:
                insert(iid, child)
        for node in nodes:
            insert("", node)

    def _capture_ui_state(self):
        """Snapshot everything the user has set up, so switching theme does
        not silently discard it."""
        return {
            "url": self._url_var.get(),
            "token": self._token_var.get(),
            "ipod_root": self._ipod_root_var.get(),
            "ipod_status": self._ipod_status_var.get(),
            "status": self._status_var.get(),
            "status_fg": self._status_label.cget("fg"),
            "downsample": bool(self._downsample_var.get()),
            "update_m3u": bool(self._update_m3u_var.get()),
            "active_tab": self._active_tab.get(),
            "progress": self._progress_val,
            # Playlists: keep the rows and which of them were ticked.
            "playlists": [dict(pl) for _var, pl in self._playlist_vars.values()],
            "playlists_checked": {
                pid for pid, (var, _pl) in self._playlist_vars.items()
                if var.get()
            },
            "library": self._capture_tree(
                self._tree, self._tree_data, self._tree_checked,
                self._tree_loaded),
            "artists_loaded": self._artists_loaded,
            "manage": self._capture_tree(
                self._manage_tree, self._manage_data, self._manage_checked),
            "manage_loaded": self._manage_loaded,
            "manage_status": self._manage_status_var.get(),
        }

    def _restore_ui_state(self, state):
        """Put a _capture_ui_state snapshot back into freshly built widgets."""
        self._url_var.set(state["url"])
        self._token_var.set(state["token"])
        self._ipod_root_var.set(state["ipod_root"])
        self._ipod_status_var.set(state["ipod_status"])
        self._downsample_var.set(state["downsample"])
        self._update_m3u_var.set(state["update_m3u"])

        self._status_var.set(state["status"])
        try:
            self._status_label.configure(fg=state["status_fg"])
        except tk.TclError:
            pass

        if state["playlists"]:
            self._populate_playlists(state["playlists"])
            for pid, (var, _pl) in self._playlist_vars.items():
                var.set(pid in state["playlists_checked"])

        self._restore_tree(self._tree, state["library"], self._tree_data,
                           self._tree_checked, self._tree_loaded)
        self._artists_loaded = state["artists_loaded"]

        self._restore_tree(self._manage_tree, state["manage"],
                           self._manage_data, self._manage_checked)
        self._manage_loaded = state["manage_loaded"]
        self._manage_status_var.set(state["manage_status"])

        # Selecting the tab after _manage_loaded is set, so the Manage tab
        # does not kick off a fresh scan of a device we already listed.
        self._select_tab(state["active_tab"])

        self._progress_val = state["progress"]
        self._draw_progress()
        self._schedule_capacity_update(delay=50)

        # The rebuild handed us default-enabled buttons; an operation may
        # still be running underneath.
        self._set_busy(self._busy or self._syncing)

    def _apply_theme(self):
        # Root + main use the window-background color (transparent sentinel
        # on Windows, theme bg elsewhere); cards paint their own colors.
        self.root.configure(bg=self._win_bg())
        self._main.configure(bg=self._win_bg())

        # header
        for w in self._main.winfo_children():
            if isinstance(w, tk.Frame):
                w.configure(bg=self._win_bg())

        if self._use_transparency:
            try:
                self.root.attributes("-alpha", 0.995)
            except tk.TclError:
                pass

        # Rebuilding is the most reliable way to recolor this many custom
        # canvas widgets, but the widgets are the only disposable part —
        # the user's selections are not. Snapshot, rebuild, put it back.
        state = self._capture_ui_state()
        self._ui_generation += 1

        for w in self._main.winfo_children():
            w.destroy()
        self._playlist_vars.clear()
        self._playlist_widgets.clear()
        self._tree_checked.clear()
        self._tree_data.clear()
        self._tree_loaded.clear()
        # Item ids are about to change, so anything queued against the old
        # ones is meaningless; the generation bump drops those callbacks.
        self._tree_pending_check.clear()
        self._manage_checked.clear()
        self._manage_data.clear()

        self._build_header()
        self._build_settings_card()
        self._build_tabs()
        self._build_sync_bar()
        self._build_log()

        # Refresh custom title bar + resize grips with the new theme colors
        self._rebuild_chrome()
        # Re-apply Win11 chrome (dark/light title bar attribute)
        self._apply_win11_chrome()

        self._restore_ui_state(state)

    # ---- connect ----

    # ---- Plex account sign-in ----

    def _on_plex_signin(self):
        if self._signin_in_progress:
            if self._signin_win is not None:
                try:
                    self._signin_win.lift()
                except tk.TclError:
                    pass
            return
        self._signin_in_progress = True
        self._signin_cancel = False
        self._build_signin_dialog()
        cid = self._client_id()
        threading.Thread(target=self._signin_worker, args=(cid,),
                         daemon=True).start()

    def _build_signin_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Sign in to Plex")
        win.configure(bg=self.t["bg_card"])
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self._signin_cancel_action)
        self._signin_win = win

        tk.Label(win, text="Sign in to your Plex account",
                 bg=self.t["bg_card"], fg=self.t["fg_heading"],
                 font=("Segoe UI", 13, "bold")).pack(padx=28, pady=(20, 4))
        tk.Label(win,
                 text="A browser is opening to plex.tv/link.\n"
                      "Enter this code there:",
                 bg=self.t["bg_card"], fg=self.t["fg_dim"],
                 font=("Segoe UI", 10), justify="center").pack(padx=28)
        self._signin_code_var = tk.StringVar(value="·····")
        tk.Label(win, textvariable=self._signin_code_var,
                 bg=self.t["bg_card"], fg=self.t["accent"],
                 font=("Consolas", 30, "bold")).pack(padx=28, pady=10)
        self._signin_msg_var = tk.StringVar(value="Requesting a code from Plex…")
        tk.Label(win, textvariable=self._signin_msg_var,
                 bg=self.t["bg_card"], fg=self.t["fg_dim"],
                 font=("Segoe UI", 9), wraplength=320,
                 justify="center").pack(padx=28)

        btns = tk.Frame(win, bg=self.t["bg_card"])
        btns.pack(pady=(14, 20))
        StyledButton(btns, "Open plex.tv/link", self.t,
                     command=lambda: webbrowser.open("https://plex.tv/link"),
                     bg=self.t["accent2"], hover_bg=self.t["border_light"],
                     fg=self.t["fg"], width=150, height=30, radius=6,
                     font=("Segoe UI", 9)).pack(side="left", padx=6)
        StyledButton(btns, "Cancel", self.t,
                     command=self._signin_cancel_action,
                     bg=self.t["cancel_btn"], hover_bg=self.t["cancel_hover"],
                     fg=self.t["fg"], width=90, height=30, radius=6,
                     font=("Segoe UI", 9)).pack(side="left", padx=6)

        # Center over the main window
        win.update_idletasks()
        try:
            px, py = self.root.winfo_x(), self.root.winfo_y()
            pw, ph = self.root.winfo_width(), self.root.winfo_height()
            ww, wh = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + (pw - ww) // 2}+{py + (ph - wh) // 2}")
        except tk.TclError:
            pass

    def _signin_cancel_action(self):
        self._signin_cancel = True
        self._log_msg("Plex sign-in cancelled.")
        self._signin_close()

    def _signin_close(self):
        self._signin_in_progress = False
        if self._signin_win is not None:
            try:
                self._signin_win.destroy()
            except tk.TclError:
                pass
            self._signin_win = None

    def _signin_set_code(self, code):
        try:
            self._signin_code_var.set(code)
            self._signin_msg_var.set(
                "Waiting for you to authorize at plex.tv/link…")
        except (tk.TclError, AttributeError):
            pass

    def _signin_set_msg(self, msg):
        try:
            self._signin_msg_var.set(msg)
        except (tk.TclError, AttributeError):
            pass

    def _signin_worker(self, cid):
        try:
            pin_id, code = plex_create_pin(cid)
        except Exception as e:
            self.root.after(0, self._signin_fail,
                            f"Could not start sign-in: {e}")
            return
        self.root.after(0, self._signin_set_code, code)
        try:
            webbrowser.open("https://plex.tv/link")
        except Exception:
            pass

        token = None
        for _ in range(90):          # ~3 minutes at 2s intervals
            if self._signin_cancel:
                return
            time.sleep(2)
            if self._signin_cancel:
                return
            try:
                token = plex_check_pin(cid, pin_id)
            except Exception:
                token = None
            if token:
                break
        if not token:
            self.root.after(0, self._signin_fail,
                            "Timed out waiting for authorization. "
                            "Click Sign in to Plex to try again.")
            return

        self.root.after(0, self._signin_set_msg,
                        "Signed in! Finding your Plex server…")
        url, chosen_token, n = "", token, 0
        try:
            servers = plex_list_servers(cid, token)
            n = len(servers)
            for s in servers:
                u, tok = plex_pick_connection(s)
                if u:
                    url, chosen_token = u, tok
                    break
        except Exception:
            pass
        if self._signin_cancel:
            return
        self.root.after(0, self._signin_success, chosen_token, url, n)

    def _signin_success(self, token, url, n):
        self._signin_close()
        self._token_var.set(token)
        self.cfg["plex_token"] = token
        if url:
            self._url_var.set(url)
            self.cfg["plex_url"] = url
        self.cfg_mgr.save(self.cfg)
        if url:
            self._log_msg(f"Signed in to Plex. Server URL set to {url}.")
            self._status_var.set("Signed in — connecting…")
            self._on_connect()
        else:
            extra = (" No servers were found on this account."
                     if n == 0 else
                     " Found a server but couldn't reach it automatically.")
            msg = ("Signed in to Plex, but the server URL wasn't filled in."
                   + extra +
                   "\n\nType your server's address (e.g. http://192.168.1.50:32400) "
                   "then click Connect.")
            self._log_msg("Signed in to Plex (enter server URL manually).")
            messagebox.showinfo("Signed in to Plex", msg)

    def _signin_fail(self, msg):
        self._signin_close()
        self._log_msg(f"Plex sign-in: {msg}")
        messagebox.showwarning("Plex sign-in", msg)

    def _on_connect(self):
        url = self._url_var.get().strip()
        token = self._token_var.get().strip()
        if not url or not token:
            messagebox.showwarning("Missing info", "Enter both Plex URL and Token.")
            return
        self._status_var.set("Connecting...")
        self.cfg["plex_url"] = url
        self.cfg["plex_token"] = token
        self.cfg["ipod_root"] = self._ipod_root()
        self.cfg_mgr.save(self.cfg)
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            self.plex = PlexClient(self.cfg["plex_url"], self.cfg["plex_token"])
            playlists = self.plex.get_playlists()
            section_id = self.plex.get_music_section_id()
            self.root.after(0, self._on_connected, playlists, section_id)
        except (URLError, HTTPError, ElementTree.ParseError, OSError) as e:
            self.root.after(0, self._status_var.set, f"Connection failed: {e}")

    def _on_connected(self, playlists, section_id):
        self._section_id = section_id
        # Sizes were cached against the previous server/session.
        self._playlist_track_cache.clear()
        self._playlist_fetching.clear()
        count_text = f"{len(playlists)} playlists"
        self._status_var.set(f"Connected  \u2022  {count_text}")
        self._status_label.configure(fg=self.t["success"])
        self._populate_playlists(playlists)
        self._artists_loaded = False
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._tree_checked.clear()
        self._tree_data.clear()
        self._tree_loaded.clear()
        self._tree_pending_check.clear()

        # The artist load normally fires from _select_tab when the user
        # first opens the Library tab. If that tab is *already* the active
        # one when a connection lands, no tab change happens and the tree
        # would sit empty until the user clicked away and back. Kick it off
        # here instead. _load_artists guards on plex/_section_id and flips
        # _artists_loaded itself, so this can't double-load.
        if self._active_tab.get() == 1:   # 1 = Library
            self._load_artists()

    # ---- playlists tab ----

    def _populate_playlists(self, playlists):
        for w in self._pl_inner.winfo_children():
            w.destroy()
        self._playlist_vars.clear()
        self._playlist_widgets.clear()

        playlists = sorted(playlists, key=lambda p: sort_key(p["title"]))
        for pl in playlists:
            var = tk.BooleanVar(value=False)
            smart_tag = "  \u26a1" if pl["smart"] else ""
            text = f"{pl['title']}{smart_tag}   ({pl['leaf_count']} tracks)"
            var.trace_add(
                "write",
                lambda *_a, pid=pl["id"]: self._on_playlist_toggled(pid))
            cb = StyledCheckbutton(self._pl_inner, text, var, self.t)
            cb.pack(fill="x", padx=4, pady=2)
            # recurse=True so the wheel also works with the pointer over the
            # row's checkbox canvas or its label, not just the row frame.
            self._bind_mousewheel(cb, self._pl_canvas, recurse=True)
            self._playlist_vars[pl["id"]] = (var, pl)
            self._playlist_widgets.append(cb)

    def _pl_select_all(self):
        for var, _ in self._playlist_vars.values():
            var.set(True)

    def _pl_deselect_all(self):
        for var, _ in self._playlist_vars.values():
            var.set(False)

    # ---- library tab ----

    def _load_artists(self):
        if not self._section_id or not self.plex:
            return
        self._artists_loaded = True
        self._log_msg("Loading artists from library...")
        threading.Thread(target=self._load_artists_worker,
                         args=(self._ui_generation,), daemon=True).start()

    def _load_artists_worker(self, generation):
        try:
            artists = self.plex.get_artists(self._section_id)
            self.root.after(0, self._populate_artists, artists, generation)
        except (URLError, HTTPError, OSError) as e:
            self.root.after(0, self._log_msg, f"Failed to load artists: {e}")

    def _populate_artists(self, artists, generation=None):
        if self._stale(generation):
            return
        artists = sorted(artists, key=lambda a: sort_key(a["title"]))
        for a in artists:
            iid = self._tree.insert(
                "", "end", text=f"\u2610 {a['title']}", values=("Artist",),
            )
            self._tree_checked[iid] = False
            self._tree_data[iid] = {"type": "artist", "data": a}
            self._tree.insert(iid, "end", text="Loading...")
        self._log_msg(f"Loaded {len(artists)} artists.")

    def _on_tree_open(self, event):
        item = self._tree.focus()
        self._trigger_lazy_load(item)

    def _load_albums_worker(self, parent_iid, artist, generation):
        try:
            albums = self.plex.get_artist_albums(artist["key"])
            self.root.after(0, self._populate_albums, parent_iid, albums,
                            generation)
        except (URLError, HTTPError, OSError) as e:
            self.root.after(0, self._log_msg, f"Failed to load albums: {e}")

    def _populate_albums(self, parent_iid, albums, generation=None):
        if self._stale(generation) or not self._tree.exists(parent_iid):
            return
        propagate = parent_iid in self._tree_pending_check
        albums = sorted(albums, key=lambda a: sort_key(a["title"]))
        for a in albums:
            year = f" ({a['year']})" if a.get("year") else ""
            iid = self._tree.insert(
                parent_iid, "end",
                text=f"\u2610 {a['title']}{year}", values=("Album",),
            )
            self._tree_checked[iid] = False
            self._tree_data[iid] = {"type": "album", "data": a}
            self._tree.insert(iid, "end", text="Loading...")
        self._tree_loaded.add(parent_iid)
        if propagate:
            # check each album, which will trigger their track loads too
            for child in self._tree.get_children(parent_iid):
                self._set_checked(child, True)
            self._tree_pending_check.discard(parent_iid)
            self._schedule_capacity_update()

    def _load_tracks_worker(self, parent_iid, album, generation):
        try:
            tracks = self.plex.get_album_tracks(album["key"])
            self.root.after(0, self._populate_tracks, parent_iid, tracks,
                            generation)
        except (URLError, HTTPError, OSError) as e:
            self.root.after(0, self._log_msg, f"Failed to load tracks: {e}")

    def _populate_tracks(self, parent_iid, tracks, generation=None):
        if self._stale(generation) or not self._tree.exists(parent_iid):
            return
        propagate = parent_iid in self._tree_pending_check
        tracks = sorted(tracks, key=lambda t: sort_key(t["title"]))
        for t in tracks:
            dur_s = t["duration_ms"] // 1000
            mins, secs = divmod(dur_s, 60)
            iid = self._tree.insert(
                parent_iid, "end",
                text=f"\u2610 {t['title']}", values=(f"{mins}:{secs:02d}",),
            )
            self._tree_checked[iid] = False
            self._tree_data[iid] = {"type": "track", "data": t}
        self._tree_loaded.add(parent_iid)
        if propagate:
            for child in self._tree.get_children(parent_iid):
                self._set_checked(child, True)
            self._tree_pending_check.discard(parent_iid)
        self._schedule_capacity_update()

    def _on_tree_click(self, event):
        item = self._tree.identify_row(event.y)
        if not item or item not in self._tree_checked:
            return
        new_state = not self._tree_checked[item]
        self._set_checked(item, new_state)
        self._schedule_capacity_update()

    def _set_checked(self, item, state):
        self._tree_checked[item] = state
        text = self._tree.item(item, "text")
        # strip old checkbox prefix
        for prefix in ("\u2611 ", "\u2610 ", "\u2612 "):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        new_prefix = "\u2611 " if state else "\u2610 "
        self._tree.item(item, text=new_prefix + text)

        info = self._tree_data.get(item)
        item_type = info["type"] if info else None

        # If this is an artist or album that hasn't been loaded yet and we're
        # checking it, trigger lazy load so descendants can be checked too.
        if state and item_type in ("artist", "album") and item not in self._tree_loaded:
            self._tree_pending_check.add(item)
            self._trigger_lazy_load(item)
        elif not state and item in self._tree_pending_check:
            self._tree_pending_check.discard(item)

        # Propagate to currently loaded children
        for child in self._tree.get_children(item):
            if child in self._tree_checked:
                self._set_checked(child, state)

    def _trigger_lazy_load(self, item):
        children = self._tree.get_children(item)
        if not (len(children) == 1 and
                self._tree.item(children[0], "text") == "Loading..."):
            return  # already loaded or no sentinel
        info = self._tree_data.get(item)
        if not info:
            return
        self._tree.delete(children[0])
        if info["type"] == "artist":
            threading.Thread(
                target=self._load_albums_worker,
                args=(item, info["data"], self._ui_generation),
                daemon=True,
            ).start()
        elif info["type"] == "album":
            threading.Thread(
                target=self._load_tracks_worker,
                args=(item, info["data"], self._ui_generation),
                daemon=True,
            ).start()

    def _lib_select_all(self):
        for item in self._tree.get_children():
            self._set_checked(item, True)
        self._schedule_capacity_update()

    def _lib_deselect_all(self):
        for item in self._tree.get_children():
            self._set_checked(item, False)
        self._schedule_capacity_update()

    # ---- Manage iPod tab ----

    AUDIO_EXTS = (".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga",
                  ".opus", ".wav", ".wma", ".aiff", ".aif")

    def _human_size(self, n):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _scan_ipod(self):
        # Another long operation owns the busy state. Bail out rather than
        # taking it over — this is also reached automatically when the user
        # opens the Manage tab, and clearing busy at the end of the scan
        # would wrongly re-enable Sync/Eject mid-operation. _manage_loaded
        # stays False, so the scan runs on the next tab open or Refresh.
        if self._busy or self._syncing:
            self._manage_status_var.set(
                "Busy — finish the current operation, then click Refresh.")
            return

        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            self._manage_status_var.set(f"iPod not accessible: {root or '(none)'}")
            return
        music_root = self._ipod_music_root()
        if not os.path.exists(music_root):
            self._manage_status_var.set(f"{music_root} not found")
            return

        self._manage_status_var.set("Scanning iPod...")
        # clear tree
        for item in self._manage_tree.get_children():
            self._manage_tree.delete(item)
        self._manage_checked.clear()
        self._manage_data.clear()

        playlist_dir = os.path.join(root, "Playlists")
        self._set_busy(True)
        threading.Thread(
            target=self._scan_ipod_worker,
            args=(music_root, playlist_dir), daemon=True,
        ).start()

    @staticmethod
    def _scan_playlists(playlist_dir):
        """Every .m3u in `playlist_dir` as (name, path, size), sorted.

        Takes the directory as an argument rather than reading it from the
        iPod StringVar: this runs on the scan worker thread, and touching a
        Tk variable off the main thread is not safe.
        """
        found = []
        try:
            names = os.listdir(playlist_dir)
        except OSError:
            return found
        for fname in sorted(names, key=sort_key):
            if not fname.lower().endswith((".m3u", ".m3u8")):
                continue
            full = os.path.join(playlist_dir, fname)
            if not os.path.isfile(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            found.append((fname, full, size))
        return found

    def _scan_ipod_worker(self, music_root, playlist_dir):
        try:
            playlists = self._scan_playlists(playlist_dir)
            data = []  # list of (artist, [(album, [(track, path, size)])])
            for artist_name in sorted(os.listdir(music_root),
                                      key=lambda s: sort_key(s)):
                artist_path = os.path.join(music_root, artist_name)
                if not os.path.isdir(artist_path):
                    continue
                albums = []
                for album_name in sorted(os.listdir(artist_path),
                                         key=lambda s: sort_key(s)):
                    album_path = os.path.join(artist_path, album_name)
                    if not os.path.isdir(album_path):
                        continue
                    tracks = []
                    for fname in sorted(os.listdir(album_path),
                                        key=lambda s: sort_key(s)):
                        fpath = os.path.join(album_path, fname)
                        if not os.path.isfile(fpath):
                            continue
                        if fname.lower().endswith(self.AUDIO_EXTS):
                            try:
                                size = os.path.getsize(fpath)
                            except OSError:
                                size = 0
                            tracks.append((fname, fpath, size))
                    if tracks:
                        albums.append((album_name, album_path, tracks))
                if albums:
                    data.append((artist_name, artist_path, albums))
            self.root.after(0, self._populate_manage_tree, data,
                            playlists, playlist_dir)
        except OSError as e:
            self.root.after(0, self._manage_status_var.set, f"Scan error: {e}")
        finally:
            self.root.after(0, self._set_busy, False)

    def _populate_manage_tree(self, data, playlists=(), playlist_dir=None):
        total_files = 0
        total_size = 0

        # Playlists first, as their own group. They live outside the music
        # folder, so they are never caught by the artist/album walk and
        # previously could not be removed from inside the app at all.
        if playlists:
            group_size = sum(s for _, _, s in playlists)
            group_iid = self._manage_tree.insert(
                "", "end",
                text=f"☐ Playlists ({len(playlists)})",
                values=(self._human_size(group_size),),
                open=False,
            )
            self._manage_checked[group_iid] = False
            self._manage_data[group_iid] = {
                "type": "playlist_group",
                "path": playlist_dir,
                "size": group_size,
            }
            for name, path, size in playlists:
                iid = self._manage_tree.insert(
                    group_iid, "end",
                    text=f"☐ {name}",
                    values=(self._human_size(size),),
                )
                self._manage_checked[iid] = False
                self._manage_data[iid] = {
                    "type": "playlist", "path": path, "size": size,
                }

        for artist_name, artist_path, albums in data:
            artist_size = sum(s for _, _, tracks in albums
                              for _, _, s in tracks)
            artist_count = sum(len(tracks) for _, _, tracks in albums)
            total_files += artist_count
            total_size += artist_size
            artist_iid = self._manage_tree.insert(
                "", "end",
                text=f"\u2610 {artist_name}",
                values=(self._human_size(artist_size),),
                open=False,
            )
            self._manage_checked[artist_iid] = False
            self._manage_data[artist_iid] = {
                "type": "artist", "path": artist_path, "size": artist_size,
            }
            for album_name, album_path, tracks in albums:
                album_size = sum(s for _, _, s in tracks)
                album_iid = self._manage_tree.insert(
                    artist_iid, "end",
                    text=f"\u2610 {album_name}",
                    values=(self._human_size(album_size),),
                    open=False,
                )
                self._manage_checked[album_iid] = False
                self._manage_data[album_iid] = {
                    "type": "album", "path": album_path, "size": album_size,
                }
                for track_name, track_path, track_size in tracks:
                    track_iid = self._manage_tree.insert(
                        album_iid, "end",
                        text=f"\u2610 {track_name}",
                        values=(self._human_size(track_size),),
                    )
                    self._manage_checked[track_iid] = False
                    self._manage_data[track_iid] = {
                        "type": "track", "path": track_path, "size": track_size,
                    }
        self._manage_loaded = True
        playlist_note = (f"  \u2022  {len(playlists)} playlists"
                         if playlists else "")
        self._manage_status_var.set(
            f"{len(data)} artists  \u2022  {total_files} tracks"
            f"{playlist_note}  \u2022  {self._human_size(total_size)}"
        )

    def _on_manage_click(self, event):
        item = self._manage_tree.identify_row(event.y)
        if not item or item not in self._manage_checked:
            return
        new_state = not self._manage_checked[item]
        self._set_manage_checked(item, new_state)

    def _set_manage_checked(self, item, state):
        self._manage_checked[item] = state
        text = self._manage_tree.item(item, "text")
        for prefix in ("\u2611 ", "\u2610 "):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        new_prefix = "\u2611 " if state else "\u2610 "
        self._manage_tree.item(item, text=new_prefix + text)
        for child in self._manage_tree.get_children(item):
            if child in self._manage_checked:
                self._set_manage_checked(child, state)

    def _manage_select_all(self):
        for item in self._manage_tree.get_children():
            self._set_manage_checked(item, True)

    def _manage_deselect_all(self):
        for item in self._manage_tree.get_children():
            self._set_manage_checked(item, False)

    def _gather_manage_files(self):
        """Collect file paths to delete based on checked items."""
        files = set()
        leaf_types = ("track", "playlist")

        def walk(iid, inherited):
            # A checked container selects everything beneath it; otherwise
            # each child decides for itself. Depth is not assumed, so the
            # Playlists group sits alongside artist/album/track fine.
            checked = inherited or bool(self._manage_checked.get(iid))
            info = self._manage_data.get(iid)
            if checked and info and info.get("type") in leaf_types:
                files.add(info["path"])
            for child in self._manage_tree.get_children(iid):
                walk(child, checked)

        for iid in self._manage_tree.get_children():
            walk(iid, False)
        return files

    def _on_remove_from_ipod(self):
        if self._busy or self._syncing:
            messagebox.showwarning(
                "Operation in progress",
                "Wait for the current operation to finish before removing "
                "files.")
            return
        files = self._gather_manage_files()
        if not files:
            messagebox.showinfo(
                "Nothing selected",
                "Select tracks, albums, artists, or playlists to remove.")
            return
        total_size = sum(self._safe_size(f) for f in files)
        msg = (
            f"Permanently delete {len(files)} file(s) from the iPod?\n\n"
            f"Space to be freed: {self._human_size(total_size)}\n\n"
            "This cannot be undone (the files are not sent to the Recycle Bin)."
        )
        if not messagebox.askyesno("Confirm removal", msg):
            return

        update_m3u = self._update_m3u_var.get()
        self._cancel = False
        self._set_busy(True)
        threading.Thread(
            target=self._remove_worker, args=(files, update_m3u), daemon=True
        ).start()

    def _safe_size(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _remove_worker(self, files, update_m3u):
        try:
            self.root.after(0, self._clear_log)
            self.root.after(0, self._log_msg,
                            f"Removing {len(files)} file(s) from iPod...")
            # Track what actually left the disk. Only these may be stripped
            # from the .m3u files — a cancelled or failed delete leaves the
            # track on the iPod, so removing its playlist entry would lose
            # the reference to a file that is still there.
            removed_files = []
            failed = 0
            cancelled = False
            for f in files:
                if self._cancel:
                    cancelled = True
                    self.root.after(0, self._log_msg,
                                    "Removal cancelled by user.")
                    break
                try:
                    os.remove(f)
                    removed_files.append(f)
                    self.root.after(0, self._log_msg,
                                    f"Deleted: {os.path.basename(f)}")
                except OSError as e:
                    failed += 1
                    self.root.after(0, self._log_msg, f"Failed: {f} ({e})")

            # Clean up empty album/artist folders
            music_root = self._ipod_music_root()
            removed_dirs = self._cleanup_empty_dirs(music_root)
            if removed_dirs:
                self.root.after(0, self._log_msg,
                                f"Removed {removed_dirs} empty folder(s).")

            # Update m3u files
            if update_m3u and removed_files:
                updated = self._update_m3u_files(removed_files)
                if updated:
                    self.root.after(0, self._log_msg,
                                    f"Updated {updated} playlist file(s).")

            deleted = len(removed_files)
            tail = ""
            if cancelled:
                tail = f", {len(files) - deleted - failed} skipped (cancelled)"
            self.root.after(0, self._log_msg,
                            f"Done. {deleted} deleted, {failed} failed{tail}.")
        except Exception as e:
            self.root.after(0, self._log_msg, f"Removal error: {e}")
        finally:
            # Clear busy first, then rescan — _scan_ipod refuses to run
            # while another operation holds the busy state.
            self.root.after(0, self._remove_finished)

    def _remove_finished(self):
        self._set_busy(False)
        self._scan_ipod()
        self._maybe_refresh_ipod_index(force=True)

    def _cleanup_empty_dirs(self, root_dir):
        removed = 0
        if not os.path.exists(root_dir):
            return 0
        # walk bottom-up so we can remove empty parents after children
        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
            if dirpath == root_dir:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    removed += 1
            except OSError:
                pass
        return removed

    def _update_m3u_files(self, deleted_files):
        """Strip deleted tracks from every .m3u file in <iPod>/Playlists.

        Handles both playlist styles found on an iPod: the #EXTM3U form
        this app writes, where each entry is an #EXTINF line followed by a
        path, and the bare one-path-per-line form Rockbox itself saves.
        Entries are matched on the path line, and an #EXTINF immediately
        above a dropped path goes with it.
        """
        root = self._ipod_root()
        playlist_dir = os.path.join(root, "Playlists")
        if not os.path.exists(playlist_dir):
            return 0

        # Build the set of playlist-style paths for the deleted files.
        # Rockbox is case sensitive about these but Windows is not, so
        # match case-insensitively and on both separators.
        folder = music_folder_name(root)
        music_root = os.path.join(root, folder)
        deleted_paths = set()
        for f in deleted_files:
            try:
                rel = os.path.relpath(f, music_root)
            except ValueError:
                continue
            if rel.startswith(".."):
                # Outside the music folder (a playlist file itself, say).
                continue
            deleted_paths.add(
                ("/" + folder + "/" + rel.replace("\\", "/")).lower())

        def is_deleted(path_line):
            return path_line.replace("\\", "/").lower() in deleted_paths

        updated_count = 0
        for fname in os.listdir(playlist_dir):
            if not fname.lower().endswith(".m3u"):
                continue
            full = os.path.join(playlist_dir, fname)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except (OSError, UnicodeDecodeError):
                continue

            new_lines = []
            modified = False
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") \
                        and is_deleted(stripped):
                    # Drop the entry, plus the #EXTINF that introduced it.
                    if new_lines and new_lines[-1].strip().startswith("#EXTINF"):
                        new_lines.pop()
                    modified = True
                    continue
                new_lines.append(line)

            if modified:
                try:
                    with open(full, "w", encoding="utf-8") as fh:
                        fh.writelines(new_lines)
                    updated_count += 1
                except OSError:
                    pass
        return updated_count

    # ---- rebuild rockbox database ----

    def _on_rebuild_rockbox_db(self):
        """Delete Rockbox's cached database_*.tcd files so it rebuilds on
        next boot. Useful when 'Update now' misses tracks or ghost entries
        linger after deletions."""
        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            messagebox.showwarning(
                "iPod not found", f"iPod not accessible: {root or '(none)'}")
            return

        rockbox_dir = os.path.join(root, ".rockbox")
        if not os.path.isdir(rockbox_dir):
            messagebox.showwarning(
                "Rockbox folder not found",
                f"No .rockbox folder on {root}.\n\n"
                "Is Rockbox installed on this iPod?")
            return

        try:
            tcd_files = [
                os.path.join(rockbox_dir, f)
                for f in os.listdir(rockbox_dir)
                if f.lower().startswith("database_") and f.lower().endswith(".tcd")
            ]
        except OSError as e:
            messagebox.showerror("Error", f"Could not read .rockbox folder:\n{e}")
            return

        if not tcd_files:
            messagebox.showinfo(
                "Nothing to rebuild",
                "No Rockbox database cache files (database_*.tcd) were found.\n\n"
                "Rockbox will build a fresh database next time it boots.")
            return

        total_size = sum(
            (os.path.getsize(f) for f in tcd_files if os.path.exists(f)),
            0,
        )

        confirm = messagebox.askyesno(
            "Rebuild Rockbox Database",
            f"This will delete {len(tcd_files)} Rockbox database cache "
            f"file(s) ({self._human_size(total_size)}) from:\n\n"
            f"{rockbox_dir}\n\n"
            "Your music files will NOT be touched. On next boot, Rockbox "
            "will do a fresh full scan of your library.\n\n"
            "After this finishes:\n"
            "  1. Safely eject the iPod\n"
            "  2. On the iPod: Settings > General Settings > Database > "
            "Initialize now\n\n"
            "Continue?",
        )
        if not confirm:
            return

        deleted = 0
        errors = []
        for f in tcd_files:
            try:
                os.remove(f)
                deleted += 1
            except OSError as e:
                errors.append(f"{os.path.basename(f)}: {e}")

        self._log_msg(
            f"Rebuilt Rockbox DB: removed {deleted} of {len(tcd_files)} "
            f"cache file(s) ({self._human_size(total_size)} freed)."
        )

        if errors:
            messagebox.showwarning(
                "Rebuild completed with errors",
                f"Deleted {deleted} of {len(tcd_files)} files.\n\n"
                "Some files could not be removed:\n" + "\n".join(errors[:5]))
        else:
            messagebox.showinfo(
                "Rockbox database cleared",
                f"Removed {deleted} database cache file(s).\n\n"
                "Next steps:\n"
                "  1. Eject the iPod (use the Eject button)\n"
                "  2. On the iPod: Settings > General Settings > Database "
                "> Initialize now\n"
                "  3. Wait for the scan to finish — your library will be "
                "rebuilt fresh.")

    # ---- shared helpers for Plex-sourced recovery features ----

    def _require_plex(self):
        """Return True if connected to Plex with a known music section,
        else warn and return False. The recovery features re-download
        media straight from Plex, so a live connection is required."""
        if not self.plex or not self._section_id:
            messagebox.showwarning(
                "Not connected",
                "Connect to your Plex server first.\n\n"
                "This feature re-downloads tracks straight from Plex, so it "
                "needs a live connection (no local music folder required).")
            return False
        return True

    def _build_plex_index(self):
        """Fetch every track from Plex and index it by its iPod-relative
        path (Artist/Album/filename, lowercased). Lets the recovery
        features find the right track to re-download for any file on the
        iPod. Returns (index_dict, error_or_None)."""
        try:
            self.root.after(0, self._manage_status_var.set,
                            "Indexing Plex library...")

            def prog(done, total):
                self.root.after(0, self._manage_status_var.set,
                                f"Indexing Plex library... {done}/{total}")

            tracks = self.plex.get_all_tracks(self._section_id, progress_cb=prog)
        except (URLError, HTTPError, ElementTree.ParseError, OSError) as e:
            return None, str(e)
        index = {}
        for t in tracks:
            index[ipod_rel_path(t).lower()] = t
        # Where several library tracks share a destination path only one can
        # be indexed, so a repair could pull down the wrong track for that
        # file. Surface it rather than letting it happen quietly.
        collisions = find_path_collisions(tracks)
        if collisions:
            self.root.after(
                0, self._log_msg,
                f"Note: {len(collisions)} path(s) in your library are shared "
                f"by more than one track; repairs for those files may fetch "
                f"the wrong one.")
        self.root.after(0, self._manage_status_var.set, "")
        return index, None

    def _redownload_track(self, track, dest_path):
        """Download a track from Plex and place it at dest_path,
        downsampling to 16-bit if it's a >16-bit FLAC. Overwrites any
        existing file atomically. Returns (True, None) or (False, err)."""
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        except OSError as e:
            return False, f"mkdir failed: {e}"
        ok, tmp_or_err = self.plex.download_part(
            track["part_key"], dest_path, cancel_check=lambda: self._cancel)
        if not ok:
            return False, tmp_or_err
        tmp = tmp_or_err
        if self.audio.available and dest_path.lower().endswith(".flac"):
            info = self.audio.probe(tmp) or {}
            if (info.get("bit_depth") or 16) > 16:
                ok, err = self.audio.downsample(tmp, dest_path)
                PlexClient._safe_remove(tmp)
                return (True, None) if ok else (False, err)
        try:
            os.replace(tmp, dest_path)
            return True, None
        except OSError as e:
            PlexClient._safe_remove(tmp)
            return False, str(e)

    def _sweep_orphans(self, music_root):
        """Remove leftover .part / .flac.tmp download sidecars from a
        previous interrupted run (they show as dead 'extra copies' on the
        iPod). Logs how many were cleared."""
        removed = 0
        for root, _, files in os.walk(music_root):
            for fname in files:
                low = fname.lower()
                if low.endswith(".part") or low.endswith(".flac.tmp"):
                    try:
                        os.remove(os.path.join(root, fname))
                        removed += 1
                    except OSError:
                        pass
        if removed:
            self.root.after(
                0, self._log_msg,
                f"Cleaned up {removed} leftover temp file(s) from a "
                f"previous run.")
        return removed

    def _ask_yes_no(self, title, message):
        """Show a modal yes/no dialog from a worker thread and block until
        the user answers. Returns True/False."""
        result = {"ok": False}
        event = threading.Event()

        def ask():
            result["ok"] = messagebox.askyesno(title, message)
            event.set()
        self.root.after(0, ask)
        event.wait()
        return result["ok"]

    def _set_busy(self, busy):
        """Enable/disable the long-operation buttons as a group and toggle
        the Cancel button. Keeps the user from launching two iPod
        operations at once."""
        self._busy = busy
        enabled = not busy
        for attr in ("_downsample_existing_btn", "_verify_repair_btn",
                     "_manage_remove_btn", "_manage_refresh_btn",
                     "_sync_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.set_state(enabled)
        if getattr(self, "_cancel_btn", None) is not None:
            self._cancel_btn.set_state(busy)

    # ---- downsample existing 24-bit tracks on iPod ----

    def _on_downsample_existing(self):
        if not self.audio.available:
            messagebox.showwarning(
                "ffmpeg not available",
                "ffmpeg was not found. This feature needs the bundled ffmpeg "
                "binaries next to the app (or ffmpeg on your PATH).")
            return

        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            messagebox.showwarning(
                "iPod not found", f"iPod not accessible: {root or '(none)'}")
            return

        if not self._require_plex():
            return

        music_root = self._ipod_music_root()
        if not os.path.isdir(music_root):
            messagebox.showwarning(
                "No music on iPod", f"{music_root} not found.")
            return

        # Run scan + convert in a background thread
        self._cancel = False
        self._set_busy(True)
        self._log_msg("Scanning iPod for 24-bit FLACs...")
        threading.Thread(
            target=self._downsample_existing_worker,
            args=(music_root,),
            daemon=True,
        ).start()

    def _downsample_existing_worker(self, music_root):
        try:
            # First, sweep up any orphan .part / .flac.tmp files left behind
            # by a previous failed run. These produce the "two copies,
            # neither plays" symptom on the iPod.
            self._sweep_orphans(music_root)

            index, err = self._build_plex_index()
            if err:
                self.root.after(0, self._log_msg, f"Could not index Plex: {err}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Plex error", f"Could not read your Plex library:\n{err}"))
                return

            candidates = []  # (ipod_path, track, bd)
            total_size = 0
            scanned = 0
            for root, _, files in os.walk(music_root):
                for fname in files:
                    if not fname.lower().endswith(".flac"):
                        continue
                    if self._cancel:
                        break
                    scanned += 1
                    if scanned % 25 == 0:
                        self.root.after(
                            0, self._manage_status_var.set,
                            f"Scanning... {scanned} files")
                    ipod_path = os.path.join(root, fname)
                    info = self.audio.probe(ipod_path)
                    if not info:
                        continue
                    bd = info.get("bit_depth") or 16
                    if bd <= 16:
                        continue
                    rel = os.path.relpath(ipod_path, music_root)
                    track = index.get(rel.replace("\\", "/").lower())
                    if not track:
                        self.root.after(
                            0, self._log_msg,
                            f"Skip (not found on Plex): {rel}")
                        continue
                    try:
                        total_size += os.path.getsize(ipod_path)
                    except OSError:
                        pass
                    candidates.append((ipod_path, track, bd))

            self.root.after(0, self._manage_status_var.set, "")

            if not candidates:
                self.root.after(0, self._log_msg,
                                f"Scan complete: no tracks need downsampling "
                                f"({scanned} FLAC(s) checked).")
                self.root.after(0, lambda: messagebox.showinfo(
                    "All clean",
                    f"Scanned {scanned} FLAC file(s) on the iPod.\n\n"
                    "None are above 16-bit — nothing to do."))
                return

            msg = (
                f"Found {len(candidates)} FLAC file(s) above 16-bit "
                f"on the iPod ({self._human_size(total_size)} total).\n\n"
                "Each will be re-downloaded from Plex and replaced with a "
                "fresh 16-bit version (dithered).\n\n"
                "Music files on your Plex server will NOT be modified.\n\n"
                "Continue?"
            )
            if not self._ask_yes_no("Downsample 24-bit Tracks", msg):
                self.root.after(0, self._log_msg, "Downsample cancelled.")
                return

            total = len(candidates)
            done = 0
            errors = []
            for i, (ipod_path, track, bd) in enumerate(candidates):
                if self._cancel:
                    self.root.after(0, self._log_msg, "Cancelled.")
                    break
                rel = os.path.relpath(ipod_path, music_root)
                self.root.after(
                    0, self._log_msg,
                    f"Downsampling ({i+1}/{total}): {rel} [{bd}-bit → 16-bit]")
                ok, err = self._redownload_track(track, ipod_path)
                if ok:
                    done += 1
                else:
                    errors.append(f"{rel}: {err}")
                    self.root.after(0, self._log_msg, f"  Failed: {err}")

            self.root.after(
                0, self._log_msg,
                f"Downsample complete: {done}/{total} replaced, "
                f"{len(errors)} error(s).")

            def show_result():
                if errors:
                    preview = "\n".join(errors[:5])
                    extra = f"\n...and {len(errors) - 5} more" if len(errors) > 5 else ""
                    messagebox.showwarning(
                        "Downsample finished with errors",
                        f"Replaced {done} of {total} files.\n\n"
                        f"Errors:\n{preview}{extra}")
                else:
                    messagebox.showinfo(
                        "Downsample complete",
                        f"Replaced {done} file(s) on the iPod.\n\n"
                        "Tip: click 'Rebuild Rockbox DB' so Rockbox re-scans "
                        "the updated files on next boot.")
            self.root.after(0, show_result)

        except Exception as e:
            self.root.after(0, self._log_msg, f"Downsample error: {e}")
        finally:
            self.root.after(0, self._set_busy, False)

    # ---- verify & repair ----

    def _on_verify_repair(self):
        """Walk every FLAC on the iPod, identify broken/orphan files, and
        re-download them fresh from Plex (downsampled if 24-bit).
        Designed to recover from a previous bad downsample run."""
        if not self.audio.available:
            messagebox.showwarning(
                "ffmpeg not available",
                "ffmpeg was not found. This feature needs the bundled ffmpeg "
                "binaries next to the app (or ffmpeg on your PATH).")
            return

        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            messagebox.showwarning(
                "iPod not found", f"iPod not accessible: {root or '(none)'}")
            return

        if not self._require_plex():
            return

        music_root = self._ipod_music_root()
        if not os.path.isdir(music_root):
            messagebox.showwarning("No music on iPod", f"{music_root} not found.")
            return

        confirm = messagebox.askyesno(
            "Verify & Repair",
            "This will scan every FLAC on the iPod, identify broken or "
            "unplayable files, and re-download them fresh from your Plex "
            "library (downsampled if 24-bit).\n\n"
            "Use this to recover from a previously botched downsample run.\n\n"
            "Music files on your Plex server will NOT be modified.\n\n"
            "This may take a while for large libraries. Continue?",
        )
        if not confirm:
            return

        self._cancel = False
        self._set_busy(True)
        self._log_msg("Verify & Repair: starting scan...")
        threading.Thread(
            target=self._verify_repair_worker,
            args=(music_root,),
            daemon=True,
        ).start()

    def _verify_repair_worker(self, music_root):
        try:
            # Phase 1: sweep .part / .flac.tmp orphans (always safe)
            self._sweep_orphans(music_root)

            # Phase 2: index Plex so we can match iPod files to tracks
            index, err = self._build_plex_index()
            if err:
                self.root.after(0, self._log_msg, f"Could not index Plex: {err}")
                self.root.after(0, lambda: messagebox.showerror(
                    "Plex error", f"Could not read your Plex library:\n{err}"))
                return

            # Phase 3: probe every .flac, collect broken ones
            broken = []          # (ipod_path, track) — repairable
            unplayable = []      # broken but not found on Plex — will be deleted
            scanned = 0
            for root, _, files in os.walk(music_root):
                for fname in files:
                    if not fname.lower().endswith(".flac"):
                        continue
                    if self._cancel:
                        break
                    scanned += 1
                    if scanned % 50 == 0:
                        self.root.after(
                            0, self._manage_status_var.set,
                            f"Scanning... {scanned} files")
                    ipod_path = os.path.join(root, fname)
                    if self._is_flac_broken(ipod_path):
                        rel = os.path.relpath(ipod_path, music_root)
                        track = index.get(rel.replace("\\", "/").lower())
                        if track:
                            broken.append((ipod_path, track))
                        else:
                            unplayable.append(ipod_path)

            self.root.after(0, self._manage_status_var.set, "")
            self.root.after(
                0, self._log_msg,
                f"Scan complete: {scanned} FLAC(s) checked, "
                f"{len(broken)} repairable, {len(unplayable)} broken with "
                f"no Plex match.")

            if not broken and not unplayable:
                self.root.after(0, lambda: messagebox.showinfo(
                    "All clean",
                    f"Scanned {scanned} FLAC file(s).\n\n"
                    "Everything probes correctly. No repairs needed."))
                return

            # Phase 4: confirm repair plan
            msg_parts = [f"Scanned {scanned} FLAC file(s)."]
            if broken:
                msg_parts.append(
                    f"\n{len(broken)} broken file(s) will be re-downloaded "
                    f"from Plex (downsampled if 24-bit).")
            if unplayable:
                msg_parts.append(
                    f"\n{len(unplayable)} broken file(s) have NO match "
                    f"on Plex and will be deleted from the iPod.")
            msg_parts.append("\nProceed?")
            if not self._ask_yes_no("Verify & Repair", "\n".join(msg_parts)):
                self.root.after(0, self._log_msg, "Verify & Repair cancelled.")
                return

            # Phase 5a: delete unrepairable files
            deleted_orphans = 0
            for f in unplayable:
                try:
                    os.remove(f)
                    deleted_orphans += 1
                    rel = os.path.relpath(f, music_root)
                    self.root.after(
                        0, self._log_msg, f"Deleted (no match): {rel}")
                except OSError as e:
                    self.root.after(
                        0, self._log_msg, f"Could not delete {f}: {e}")

            # Phase 5b: re-download broken files from Plex
            total = len(broken)
            repaired = 0
            errors = []
            for i, (ipod_path, track) in enumerate(broken):
                if self._cancel:
                    self.root.after(0, self._log_msg, "Cancelled.")
                    break
                rel = os.path.relpath(ipod_path, music_root)
                self.root.after(
                    0, self._log_msg, f"Repairing ({i+1}/{total}): {rel}")
                ok, err = self._redownload_track(track, ipod_path)
                if ok:
                    repaired += 1
                else:
                    errors.append(f"{rel}: {err}")
                    self.root.after(0, self._log_msg, f"  Failed: {err}")

            self.root.after(
                0, self._log_msg,
                f"Verify & Repair complete: {repaired}/{total} repaired, "
                f"{deleted_orphans} unrepairable deleted, "
                f"{len(errors)} error(s).")

            def show_result():
                summary = (
                    f"Repaired {repaired} of {total} broken file(s).\n"
                    f"Deleted {deleted_orphans} orphan(s) with no match.\n"
                )
                if errors:
                    preview = "\n".join(errors[:5])
                    extra = (f"\n...and {len(errors) - 5} more"
                             if len(errors) > 5 else "")
                    messagebox.showwarning(
                        "Repair finished with errors",
                        summary + f"\nErrors:\n{preview}{extra}\n\n"
                        "Recommended: click 'Rebuild Rockbox DB' next.")
                else:
                    messagebox.showinfo(
                        "Verify & Repair complete",
                        summary + "\nRecommended next steps:\n"
                        "  1. Click 'Rebuild Rockbox DB'\n"
                        "  2. Eject the iPod\n"
                        "  3. On the iPod: Settings > General Settings > "
                        "Database > Initialize now")
            self.root.after(0, show_result)

        except Exception as e:
            self.root.after(0, self._log_msg, f"Verify & Repair error: {e}")
        finally:
            self.root.after(0, self._set_busy, False)

    def _is_flac_broken(self, path):
        """Return True if the file fails basic playability checks.
        A FLAC is considered broken if:
          - it doesn't exist
          - it's smaller than 2 KB (truncated / empty)
          - ffprobe can't read its bit depth or sample rate
          - ffprobe reports zero duration
        """
        try:
            if not os.path.isfile(path):
                return True
            if os.path.getsize(path) < 2048:
                return True
        except OSError:
            return True

        # Quick probe via ffprobe with duration
        try:
            result = subprocess.run(
                [self.audio.ffprobe_path, "-v", "error",
                 "-select_streams", "a:0",
                 "-show_entries",
                 "stream=bits_per_raw_sample,sample_rate:format=duration",
                 "-of", "default=nw=1:nk=0",
                 path],
                timeout=10,
                **AudioConverter._no_console_kwargs(),
            )
        except (subprocess.TimeoutExpired, OSError):
            return True

        if result.returncode != 0:
            return True

        has_sr = False
        has_dur = False
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip()
            if not v or v == "N/A":
                continue
            if k == "sample_rate":
                try:
                    if int(v) > 0:
                        has_sr = True
                except ValueError:
                    pass
            elif k == "duration":
                try:
                    if float(v) > 0.5:   # at least half a second
                        has_dur = True
                except ValueError:
                    pass

        return not (has_sr and has_dur)

    # ---- iPod capacity ----

    def _schedule_capacity_update(self, delay=250):
        """Recompute soon, coalescing bursts.

        Ticking a whole artist fires one call per descendant, and each
        recompute walks the selection, so the work is debounced rather
        than done thousands of times for one click.
        """
        existing = getattr(self, "_capacity_after", None)
        if existing is not None:
            try:
                self.root.after_cancel(existing)
            except (tk.TclError, ValueError):
                pass
        try:
            self._capacity_after = self.root.after(
                delay, self._recompute_capacity)
        except (tk.TclError, AttributeError):
            self._capacity_after = None

    def _track_bytes(self, track, seen):
        """Bytes this track would add, or 0 if it is a duplicate or is
        already on the device."""
        rel = ipod_rel_path(track).lower()
        if rel in seen:
            return 0
        seen.add(rel)
        if self._ipod_index is not None and rel in self._ipod_index:
            return 0
        try:
            return max(int(track.get("size") or 0), 0)
        except (TypeError, ValueError):
            return 0

    def _selection_bytes(self):
        """(bytes the selection would add, whether any size is still unknown).

        Sizes come from Plex, so a 24-bit FLAC queued for downsampling is
        counted at its source size. That overestimates, which is the safe
        direction for a capacity warning.
        """
        seen = set()
        total = 0
        pending = False
        for pid, (var, _pl) in self._playlist_vars.items():
            if not var.get():
                continue
            tracks = self._playlist_track_cache.get(pid)
            if tracks is None:
                pending = True
                continue
            for track in tracks:
                total += self._track_bytes(track, seen)
        for track in self._gather_library_tracks():
            total += self._track_bytes(track, seen)
        return total, pending

    def _recompute_capacity(self):
        self._capacity_after = None
        if not hasattr(self, "_capacity_bar"):
            return
        usage = disk_usage(self._ipod_root())
        if not usage:
            self._capacity = None
            self._capacity_bar.set_values(0, 0, 0)
            self._capacity_var.set("No iPod connected")
            self._capacity_label.configure(fg=self.t["fg_dim"])
            return

        total, used, free = usage
        selected, pending = self._selection_bytes()
        over = max(selected - free, 0)
        self._capacity = {"total": total, "used": used, "free": free,
                          "selected": selected, "over": over}
        self._capacity_bar.set_values(total, used, selected)

        text = (f"{self._human_size(used)} used  \u2022  "
                f"{self._human_size(selected)} selected  \u2022  "
                f"{self._human_size(free)} free of {self._human_size(total)}")
        if pending:
            text += "  \u2022  sizing playlists\u2026"
        if over:
            text += f"    \u26a0 {self._human_size(over)} over capacity"
        self._capacity_var.set(text)
        self._capacity_label.configure(
            fg=self.t["error"] if over else self.t["fg_dim"])

    # -- what is already on the device --

    def _maybe_refresh_ipod_index(self, force=False):
        """Index the iPod's music folder when the device changes, or after
        an operation that altered it. Tracks already present cost no new
        space, and without this every selection would look too big."""
        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            if self._ipod_index is not None or self._indexed_root:
                self._ipod_index = None
                self._indexed_root = None
                self._schedule_capacity_update()
            return
        if not force and self._indexed_root == root:
            return
        # Best-effort: this only feeds the capacity estimate, so it must
        # never take down the callback that triggered it.
        try:
            music_root = self._ipod_music_root()
        except Exception:
            return
        self._indexed_root = root
        threading.Thread(target=self._ipod_index_worker,
                         args=(music_root,), daemon=True).start()

    def _ipod_index_worker(self, music_root):
        index = set()
        try:
            for dirpath, _dirs, files in os.walk(music_root):
                for name in files:
                    full = os.path.join(dirpath, name)
                    try:
                        if os.path.getsize(full) <= 0:
                            continue
                    except OSError:
                        continue
                    rel = os.path.relpath(full, music_root)
                    index.add(rel.replace(os.sep, "/").lower())
        except OSError:
            pass
        self.root.after(0, self._set_ipod_index, index)

    def _set_ipod_index(self, index):
        self._ipod_index = index
        self._schedule_capacity_update()

    # -- playlist sizes --

    def _on_playlist_toggled(self, playlist_id):
        var = self._playlist_vars.get(playlist_id, (None, None))[0]
        if var is not None and var.get():
            self._ensure_playlist_tracks(playlist_id)
        self._schedule_capacity_update()

    def _ensure_playlist_tracks(self, playlist_id):
        """Fetch a playlist's tracks in the background purely to size it.

        A playlist row only carries a track count, not bytes, so the only
        way to know what it would cost is to ask the server.
        """
        if (playlist_id in self._playlist_track_cache
                or playlist_id in self._playlist_fetching
                or not self.plex):
            return
        self._playlist_fetching.add(playlist_id)
        threading.Thread(target=self._playlist_tracks_worker,
                         args=(playlist_id,), daemon=True).start()

    def _playlist_tracks_worker(self, playlist_id):
        try:
            tracks = self.plex.get_playlist_tracks(playlist_id)
        except (URLError, HTTPError, ElementTree.ParseError, OSError) as e:
            self.root.after(0, self._log_msg,
                            f"Could not size playlist: {e}")
            tracks = []
        self.root.after(0, self._store_playlist_tracks, playlist_id, tracks)

    def _store_playlist_tracks(self, playlist_id, tracks):
        self._playlist_fetching.discard(playlist_id)
        self._playlist_track_cache[playlist_id] = tracks
        self._schedule_capacity_update()

    # ---- sync ----

    def _confirm_capacity(self):
        """Ask before starting a sync that cannot finish.

        Returns True to go ahead. Saying no leaves the selection untouched
        so it can be trimmed and the sync retried.
        """
        capacity = self._capacity
        if not capacity or capacity["over"] <= 0:
            return True
        proceed = messagebox.askyesno(
            "Not enough space",
            f"The selection needs about "
            f"{self._human_size(capacity['over'])} more than the iPod has "
            f"free.\n\n"
            f"Selected:  {self._human_size(capacity['selected'])}\n"
            f"Free:      {self._human_size(capacity['free'])}\n\n"
            "Transfer until the iPod is full?\n\n"
            "Tracks are copied in order and the sync stops cleanly when the "
            "device fills up. Playlists will list only the tracks that "
            "actually fit.\n\n"
            "Choose No to cancel and change your selection.")
        if not proceed:
            self._log_msg(
                f"Sync cancelled \u2014 selection is "
                f"{self._human_size(capacity['over'])} larger than the free "
                f"space on the iPod.")
        return proceed

    def _on_sync(self):
        if self._syncing:
            return
        if self._busy:
            messagebox.showwarning(
                "Operation in progress",
                "Wait for the current iPod operation to finish before "
                "syncing.")
            return
        if not self.plex:
            messagebox.showwarning("Not connected", "Connect to Plex first.")
            return
        root = self._ipod_root()
        if not root or not os.path.isdir(root):
            messagebox.showwarning(
                "iPod not found", f"iPod not accessible: {root or '(none)'}")
            return

        # The capacity figures are updated on a short debounce, so bring
        # them up to date before deciding whether to warn.
        self._recompute_capacity()
        if not self._confirm_capacity():
            return

        self.cfg["ipod_root"] = root
        self.cfg["downsample_on_sync"] = bool(self._downsample_var.get())
        self.cfg_mgr.save(self.cfg)

        self.sync_engine = SyncEngine(root)

        # Snapshot all selection state on the main thread so the worker
        # can iterate without colliding with late UI callbacks (e.g.
        # in-flight artist/album loads still mutating _tree_data via
        # root.after). Avoids "dictionary changed size during iteration".
        selected_playlists = [
            (pid, dict(pl)) for pid, (var, pl)
            in list(self._playlist_vars.items()) if var.get()
        ]
        lib_tracks = list(self._gather_library_tracks())
        downsample = bool(self._downsample_var.get()) and self.audio.available

        self._syncing = True
        self._cancel = False
        self._set_busy(True)
        self._progress_val = 0
        self._draw_progress()
        self._log_msg("\u2500" * 40)
        self._log_msg("Sync started.")

        threading.Thread(
            target=self._sync_worker,
            args=(selected_playlists, lib_tracks, downsample),
            daemon=True,
        ).start()

    def _on_cancel(self):
        self._cancel = True
        self._log_msg("Cancelling...")

    def _on_eject(self):
        if self._syncing or self._busy:
            messagebox.showwarning(
                "Operation in progress",
                "Wait for the current operation to finish before ejecting.")
            return
        root = self._ipod_root()
        if not root:
            return
        if not os.path.isdir(root):
            self._log_msg(f"iPod ({root}) is not connected.")
            return

        confirm = messagebox.askyesno(
            "Eject iPod",
            f"Safely eject {root}?\n\n"
            "Make sure no other programs are using the iPod.",
        )
        if not confirm:
            return

        self._log_msg(f"Ejecting {root}...")
        threading.Thread(target=self._eject_worker, args=(root,), daemon=True).start()

    def _eject_worker(self, root):
        try:
            ok, msg = eject_volume(root)
            if ok:
                self.root.after(0, self._log_msg,
                                f"iPod ({root}) ejected safely. You can unplug it now.")
                self.root.after(0, self._status_var.set, "iPod ejected")
                self.root.after(0, lambda: self._status_label.configure(
                    fg=self.t["success"]))
            else:
                self.root.after(0, self._log_msg, f"Eject failed: {msg}")
                self.root.after(0, lambda: messagebox.showwarning(
                    "Eject failed",
                    f"Could not eject {root}.\n\n{msg}\n\n"
                    "Close any program using the iPod (file manager windows, "
                    "media players, this app's Manage tab) and try again."))
        except Exception as e:
            self.root.after(0, self._log_msg, f"Eject error: {e}")

    def _sync_worker(self, selected_playlists, lib_tracks, downsample):
        try:
            self._do_sync(selected_playlists, lib_tracks, downsample)
        except Exception as e:
            self.root.after(0, self._log_msg, f"Sync error: {e}")
        finally:
            self.root.after(0, self._sync_finished)

    def _warn_about_collisions(self, tracks, limit=5):
        """Tell the user when several tracks want the same file on the iPod.

        Only one of them can be written. Without this the loser just never
        appears on the device and its playlist entry silently plays the
        winner, which is very hard to notice among thousands of tracks.
        """
        collisions = find_path_collisions(tracks)
        if not collisions:
            return 0
        self.root.after(
            0, self._log_msg,
            f"Warning: {len(collisions)} destination(s) are claimed by more "
            f"than one track. Only one file can exist at each path, so the "
            f"others will not be synced:")
        for rel in sorted(collisions)[:limit]:
            titles = sorted({t.get("title") or "?" for t in collisions[rel]})
            self.root.after(0, self._log_msg,
                            f"  {rel}  <-  {', '.join(titles)}")
        if len(collisions) > limit:
            self.root.after(0, self._log_msg,
                            f"  ...and {len(collisions) - limit} more")
        return len(collisions)

    def _do_sync(self, selected_playlists, lib_tracks, downsample):
        playlist_tracks = {}
        all_tracks = {}    # dedup key -> track (so a song in two playlists
                           # is only downloaded once)
        selected = []      # every selected track, before deduplication

        def key(t):
            return ipod_rel_path(t).lower()

        for pid, pl in selected_playlists:
            self.root.after(0, self._log_msg, f"Fetching playlist: {pl['title']}...")
            try:
                tracks = self.plex.get_playlist_tracks(pid)
                playlist_tracks[pl["title"]] = tracks
                selected.extend(tracks)
                for t in tracks:
                    all_tracks[key(t)] = t
            except (URLError, HTTPError, OSError) as e:
                self.root.after(0, self._log_msg, f"Error fetching {pl['title']}: {e}")

        for t in lib_tracks:
            selected.append(t)
            all_tracks[key(t)] = t

        if not all_tracks:
            self.root.after(0, self._log_msg, "Nothing selected to sync.")
            return

        self._warn_about_collisions(selected)

        unique_tracks = list(all_tracks.values())
        to_copy, already_exist = self.sync_engine.build_sync_plan(unique_tracks)

        self.root.after(
            0, self._log_msg,
            f"Found {len(unique_tracks)} tracks. "
            f"{len(to_copy)} to download, {len(already_exist)} already on iPod.",
        )

        # Keys of tracks that are actually on the iPod. Seeded with the
        # files that were already there, then extended as downloads land.
        # The .m3u files are built from this set, so a failed, skipped or
        # cancelled track never gets an entry pointing at a missing file.
        on_ipod = {key(t) for t in already_exist}

        total = len(to_copy)
        copied = 0
        converted = 0
        failed = 0
        filled = False
        for i, t in enumerate(to_copy):
            if self._cancel:
                self.root.after(0, self._log_msg, "Sync cancelled by user.")
                break
            rel = ipod_rel_path(t)
            dst = self.sync_engine.dest_path(t)

            # Stop before writing rather than failing every remaining
            # track with a disk-full error. The download needs room for a
            # .part sidecar plus the final file, and FAT32 needs room for
            # directory entries, hence the margin.
            need = 0
            try:
                need = max(int(t.get("size") or 0), 0)
            except (TypeError, ValueError):
                need = 0
            usage = disk_usage(self.sync_engine.ipod_root)
            if usage and usage[2] < need + FREE_SPACE_MARGIN:
                filled = True
                self.root.after(
                    0, self._log_msg,
                    f"iPod is full \u2014 stopping here. {total - i} track(s) "
                    f"were not copied.")
                break

            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
            except OSError as e:
                failed += 1
                self.root.after(0, self._log_msg, f"  Failed (mkdir): {rel}: {e}")
                continue

            # Download the media straight from Plex to a .part sidecar.
            self.root.after(0, self._log_msg,
                            f"Downloading ({i+1}/{total}): {rel}")
            ok, tmp_or_err = self.plex.download_part(
                t["part_key"], dst, cancel_check=lambda: self._cancel)
            if not ok:
                if tmp_or_err == "cancelled":
                    self.root.after(0, self._log_msg, "Sync cancelled by user.")
                    break
                failed += 1
                self.root.after(0, self._log_msg,
                                f"  Download failed: {rel}: {tmp_or_err}")
                continue
            tmp = tmp_or_err

            # Decide: downsample (24-bit FLAC) or place as-is.
            placed = False
            if downsample and dst.lower().endswith(".flac"):
                info = self.audio.probe(tmp) or {}
                bd = info.get("bit_depth") or 16
                if bd > 16:
                    self.root.after(
                        0, self._log_msg,
                        f"  Downsampling: {bd}-bit → 16-bit")
                    ok, err = self.audio.downsample(tmp, dst)
                    PlexClient._safe_remove(tmp)
                    if ok:
                        copied += 1
                        converted += 1
                        on_ipod.add(key(t))
                        placed = True
                    else:
                        failed += 1
                        self.root.after(0, self._log_msg,
                                        f"  Downsample failed: {err}")
                        placed = True  # tmp already cleaned; nothing on disk

            if not placed:
                # Atomically move the finished download into place.
                try:
                    os.replace(tmp, dst)
                    copied += 1
                    on_ipod.add(key(t))
                except OSError as e:
                    PlexClient._safe_remove(tmp)
                    failed += 1
                    self.root.after(0, self._log_msg,
                                    f"  Place failed: {rel}: {e}")

            self.root.after(0, self._set_progress, (i + 1) / total * 100)

        # Write playlists. generate_m3u rewrites each file from scratch, so
        # every entry must point at a track that is really on the device —
        # otherwise Rockbox shows dead entries for downloads that failed or
        # never ran. Tracks are filtered against on_ipod; a playlist with
        # nothing on the device is left alone rather than being clobbered
        # with an empty file.
        written = 0
        for name, tracks in playlist_tracks.items():
            kept = [t for t in tracks if key(t) in on_ipod]
            dropped = len(tracks) - len(kept)
            if not kept:
                self.root.after(
                    0, self._log_msg,
                    f"Playlist skipped: {name}.m3u — none of its "
                    f"{len(tracks)} track(s) are on the iPod.")
                continue
            try:
                self.sync_engine.generate_m3u(name, kept)
                written += 1
                note = (f" ({dropped} track(s) omitted — not on iPod)"
                        if dropped else "")
                self.root.after(
                    0, self._log_msg,
                    f"Playlist saved: {name}.m3u — {len(kept)} track(s){note}")
            except OSError as e:
                self.root.after(0, self._log_msg, f"Error writing {name}.m3u: {e}")

        skipped = len(already_exist)
        conv_suffix = f" ({converted} downsampled)" if converted else ""
        fail_suffix = f", {failed} failed" if failed else ""
        full_suffix = " \u2014 iPod filled up" if filled else ""
        self.root.after(
            0, self._log_msg,
            f"Done. {copied} downloaded{conv_suffix}, {skipped} skipped"
            f"{fail_suffix}, {written} playlist(s) written{full_suffix}.",
        )
        if filled:
            self.root.after(0, lambda: messagebox.showinfo(
                "iPod full",
                f"The iPod filled up during the sync.\n\n"
                f"{copied} track(s) were copied. The playlists list only "
                f"what actually fits on the device.\n\n"
                "Free some space from the Manage iPod tab, then sync again "
                "to continue."))

    def _gather_library_tracks(self):
        tracks = []
        for iid, checked in self._tree_checked.items():
            if not checked:
                continue
            info = self._tree_data.get(iid)
            if not info:
                continue
            if info["type"] == "track":
                tracks.append(info["data"])
            elif info["type"] == "album":
                for child in self._tree.get_children(iid):
                    child_info = self._tree_data.get(child)
                    if child_info and child_info["type"] == "track":
                        tracks.append(child_info["data"])
            elif info["type"] == "artist":
                for album_iid in self._tree.get_children(iid):
                    for track_iid in self._tree.get_children(album_iid):
                        child_info = self._tree_data.get(track_iid)
                        if child_info and child_info["type"] == "track":
                            tracks.append(child_info["data"])
        return tracks

    def _sync_finished(self):
        self._syncing = False
        self._set_busy(False)
        # The device changed underneath us.
        self._maybe_refresh_ipod_index(force=True)

    # ---- log / progress helpers ----

    def _log_msg(self, msg):
        self._log_history.append(msg)
        # Cap history so it doesn't grow unbounded during long sessions
        if len(self._log_history) > 2000:
            self._log_history = self._log_history[-2000:]
        try:
            self._log.configure(state="normal")
            self._log.insert("end", msg + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _clear_log(self):
        self._log_history = []
        try:
            self._log.configure(state="normal")
            self._log.delete("1.0", "end")
            self._log.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _set_progress(self, value):
        self._progress_val = value
        self._draw_progress()

    # ---- run ----

    def run(self):
        self.root.after(100, self._draw_progress)
        self.root.mainloop()
