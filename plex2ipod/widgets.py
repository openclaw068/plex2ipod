"""Hand-drawn Tk widgets: rounded cards, buttons and checkboxes.

Tk has no rounded corners, so these are canvases that paint themselves
from the active theme dict.
"""

import tkinter as tk


class GlassCard(tk.Canvas):
    """A rounded-rectangle card with subtle border glow."""

    def __init__(self, parent, theme, radius=14, pad=16, **kw):
        super().__init__(parent, highlightthickness=0, bd=0, **kw)
        self.radius = radius
        self.pad = pad
        self.theme = theme
        self.inner = tk.Frame(self, bg=theme["bg_card"])
        self._window = self.create_window(
            pad, pad, window=self.inner, anchor="nw"
        )
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        w, h = event.width, event.height
        self.delete("bg")
        r = self.radius
        t = self.theme
        # outer glow
        self._rounded_rect(1, 1, w - 1, h - 1, r, t["glass_border"], "bg")
        # inner fill
        self._rounded_rect(2, 2, w - 2, h - 2, r - 1, t["bg_card"], "bg")
        self.tag_lower("bg")
        self.itemconfigure(
            self._window,
            width=w - self.pad * 2,
            height=h - self.pad * 2,
        )

    def _rounded_rect(self, x1, y1, x2, y2, r, fill, tag):
        self.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r,
                        start=90, extent=90, fill=fill, outline=fill, tags=tag)
        self.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r,
                        start=0, extent=90, fill=fill, outline=fill, tags=tag)
        self.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2,
                        start=270, extent=90, fill=fill, outline=fill, tags=tag)
        self.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2,
                        start=180, extent=90, fill=fill, outline=fill, tags=tag)
        self.create_rectangle(x1 + r, y1, x2 - r, y2,
                              fill=fill, outline=fill, tags=tag)
        self.create_rectangle(x1, y1 + r, x1 + r, y2 - r,
                              fill=fill, outline=fill, tags=tag)
        self.create_rectangle(x2 - r, y1 + r, x2, y2 - r,
                              fill=fill, outline=fill, tags=tag)


# ---------------------------------------------------------------------------
# Custom styled widgets
# ---------------------------------------------------------------------------


class StyledEntry(tk.Entry):
    def __init__(self, parent, theme, **kw):
        super().__init__(
            parent,
            bg=theme["bg_input"],
            fg=theme["fg"],
            insertbackground=theme["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=theme["border"],
            highlightcolor=theme["accent"],
            font=("Segoe UI", 10),
            **kw,
        )


class StyledButton(tk.Canvas):
    """A modern rounded button drawn on canvas."""

    def __init__(self, parent, text, theme, command=None,
                 bg=None, hover_bg=None, fg=None, width=120, height=28,
                 radius=8, font=("Segoe UI", 10, "bold"), **kw):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0,
                         bg=parent.cget("bg") if isinstance(parent, tk.Widget) else theme["bg"],
                         **kw)
        self._text = text
        self._command = command
        self._bg = bg or theme["accent"]
        self._hover_bg = hover_bg or theme["accent_hover"]
        self._fg = fg or theme["sync_btn_fg"]
        # Disabled colors come from the theme rather than being hardcoded,
        # so a greyed-out button reads correctly in light mode too.
        self._disabled_bg = theme["check_off"]
        self._disabled_fg = theme["fg_dim"]
        self._radius = radius
        self._font = font
        self._disabled = False
        self._draw(self._bg)
        self.bind("<Enter>", lambda e: self._draw(self._hover_bg) if not self._disabled else None)
        self.bind("<Leave>", lambda e: self._draw(self._bg) if not self._disabled else None)
        self.bind("<Button-1>", self._on_click)

    def _draw(self, color):
        self.delete("all")
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        r = self._radius
        # rounded rect
        self.create_arc(0, 0, 2 * r, 2 * r, start=90, extent=90,
                        fill=color, outline=color)
        self.create_arc(w - 2 * r, 0, w, 2 * r, start=0, extent=90,
                        fill=color, outline=color)
        self.create_arc(w - 2 * r, h - 2 * r, w, h, start=270, extent=90,
                        fill=color, outline=color)
        self.create_arc(0, h - 2 * r, 2 * r, h, start=180, extent=90,
                        fill=color, outline=color)
        self.create_rectangle(r, 0, w - r, h, fill=color, outline=color)
        self.create_rectangle(0, r, r, h - r, fill=color, outline=color)
        self.create_rectangle(w - r, r, w, h - r, fill=color, outline=color)
        # text
        fg = self._disabled_fg if self._disabled else self._fg
        self.create_text(w // 2, h // 2, text=self._text,
                         fill=fg, font=self._font)

    def _on_click(self, event):
        if not self._disabled and self._command:
            self._command()

    def set_state(self, enabled):
        self._disabled = not enabled
        self._draw(self._bg if enabled else self._disabled_bg)


class StyledCheckbutton(tk.Frame):
    """A modern checkbox with colored indicator."""

    def __init__(self, parent, text, variable, theme, **kw):
        super().__init__(parent, bg=theme["bg_card"], **kw)
        self._var = variable
        self._theme = theme
        self._enabled = True

        self._box = tk.Canvas(self, width=20, height=20, highlightthickness=0,
                              bd=0, bg=theme["bg_card"])
        self._box.pack(side="left", padx=(0, 8))
        self._box.bind("<Button-1>", self._toggle)

        self._label = tk.Label(
            self, text=text, bg=theme["bg_card"], fg=theme["fg"],
            font=("Segoe UI", 10), anchor="w",
        )
        self._label.pack(side="left", fill="x", expand=True)
        self._label.bind("<Button-1>", self._toggle)

        self._var.trace_add("write", lambda *_: self._draw())
        self._draw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _toggle(self, event=None):
        if not self._enabled:
            return
        self._var.set(not self._var.get())

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        fg = self._theme["fg"] if self._enabled else self._theme["fg_dim"]
        self._label.configure(fg=fg)
        self._draw()

    def _draw(self):
        self._box.delete("all")
        t = self._theme
        checked = self._var.get()
        if not getattr(self, "_enabled", True):
            fill = t["check_off"]
        else:
            fill = t["check_on"] if checked else t["check_off"]
        # draw rounded square
        r = 4
        self._box.create_arc(1, 1, 1 + 2 * r, 1 + 2 * r, start=90, extent=90,
                             fill=fill, outline=fill)
        self._box.create_arc(19 - 2 * r, 1, 19, 1 + 2 * r, start=0, extent=90,
                             fill=fill, outline=fill)
        self._box.create_arc(19 - 2 * r, 19 - 2 * r, 19, 19, start=270, extent=90,
                             fill=fill, outline=fill)
        self._box.create_arc(1, 19 - 2 * r, 1 + 2 * r, 19, start=180, extent=90,
                             fill=fill, outline=fill)
        self._box.create_rectangle(1 + r, 1, 19 - r, 19, fill=fill, outline=fill)
        self._box.create_rectangle(1, 1 + r, 1 + r, 19 - r, fill=fill, outline=fill)
        self._box.create_rectangle(19 - r, 1 + r, 19, 19 - r, fill=fill, outline=fill)
        # checkmark
        if checked:
            self._box.create_line(5, 10, 8, 14, fill="white", width=2)
            self._box.create_line(8, 14, 15, 5, fill="white", width=2)

    def _on_enter(self, event):
        self.configure(bg=self._theme["bg_card_hover"])
        self._box.configure(bg=self._theme["bg_card_hover"])
        self._label.configure(bg=self._theme["bg_card_hover"])

    def _on_leave(self, event):
        self.configure(bg=self._theme["bg_card"])
        self._box.configure(bg=self._theme["bg_card"])
        self._label.configure(bg=self._theme["bg_card"])

    def update_theme(self, theme):
        self._theme = theme
        self.configure(bg=theme["bg_card"])
        self._box.configure(bg=theme["bg_card"])
        self._label.configure(bg=theme["bg_card"], fg=theme["fg"])
        self._draw()


class CapacityBar(tk.Canvas):
    """A stacked bar showing how full the iPod is and how much the current
    selection would add.

    Three segments: what is already on the device, what syncing would add,
    and free space. When the selection does not fit, the part that would
    not fit is drawn in the error color so the overflow is visible at a
    glance rather than only being reported at sync time.
    """

    HEIGHT = 14

    def __init__(self, parent, theme, **kw):
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0,
                         bd=0, bg=theme["bg"], **kw)
        self.theme = theme
        self._total = 0
        self._used = 0
        self._selected = 0
        self.bind("<Configure>", lambda _e: self._draw())

    def set_values(self, total, used, selected):
        """total/used/selected in bytes. total of 0 means 'no iPod'."""
        self._total = max(int(total or 0), 0)
        self._used = max(int(used or 0), 0)
        self._selected = max(int(selected or 0), 0)
        self._draw()

    @property
    def overflow(self):
        """Bytes by which the selection exceeds free space, else 0."""
        if not self._total:
            return 0
        return max(self._used + self._selected - self._total, 0)

    def update_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["bg"])
        self._draw()

    def _draw(self):
        self.delete("all")
        t = self.theme
        width = self.winfo_width()
        height = self.HEIGHT
        radius = height // 2
        if width <= 2 * radius:
            return

        self._pill(0, 0, width, height, radius, t["progress_bg"])
        if not self._total:
            return

        # Everything is scaled against the larger of the device size and
        # the projected total, so an overflowing selection still fits on
        # screen and the overflow reads as a proportion.
        scale_max = max(self._total, self._used + self._selected)
        used_w = int(width * self._used / scale_max)
        fits = max(min(self._selected, self._total - self._used), 0)
        fits_w = int(width * fits / scale_max)
        over_w = int(width * (self._selected - fits) / scale_max)

        x = 0
        for segment_w, color in (
            (used_w, t["accent2"]),
            (fits_w, t["progress_fill"]),
            (over_w, t["error"]),
        ):
            if segment_w <= 0:
                continue
            self._pill(x, 0, min(x + segment_w, width), height, radius, color)
            x += segment_w

    def _pill(self, x1, y1, x2, y2, r, fill):
        if x2 - x1 < 2 * r:
            r = max((x2 - x1) // 2, 1)
        self.create_arc(x1, y1, x1 + 2 * r, y2, start=90, extent=180,
                        fill=fill, outline=fill)
        self.create_arc(x2 - 2 * r, y1, x2, y2, start=270, extent=180,
                        fill=fill, outline=fill)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
