"""Lift Elite Control — GTK4 + libadwaita app for the NZXT Lift Elite.

Run ``lift-elite-control`` (installed) or ``./lift-elite-control`` from the
checkout. ``--mock`` / ``--mock-wireless`` use an emulated mouse.

Nothing is written to the mouse on startup: the app reads the five
profiles, keeps edits as drafts and writes only when you press *Applica*,
then reads everything back to verify it.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..actions import FACTORY_ACTIONS, action_by_code  # noqa: E402
from ..errors import DeviceNotFoundError, LiftEliteError  # noqa: E402
from ..hotplug import Monitor, Presence  # noqa: E402
from ..protocol import BUTTON_NAMES, DPI_STEP, DPI_VALUE_MAX, DPI_VALUE_MIN, ENABLE_ACTIVE_PROFILE_APPLY, rgb565_to_rgb  # noqa: E402
from ..settings import load_settings, save_setting  # noqa: E402
from ..store import ProfileStore  # noqa: E402
from . import i18n  # noqa: E402
from . import diagram as vector_diagram  # noqa: E402
from . import svgdiagram  # noqa: E402
from .diagram import W as DIAGRAM_W, H as DIAGRAM_H  # noqa: E402


def preview_art():
    """SVG artwork via librsvg when available, else the built-in vector drawing.

    LIFT_ELITE_DIAGRAM=vector forces the fallback.
    """
    if os.environ.get("LIFT_ELITE_DIAGRAM") != "vector" and svgdiagram.available():
        return svgdiagram
    return vector_diagram
from .i18n import _  # noqa: E402
from .model import (  # noqa: E402
    BUTTON_LABELS,
    POLL_BACKOFF_MS,
    POLL_FAILURES_BEFORE_BACKOFF,
    POLL_INTERVAL_WIRED_MS,
    POLL_INTERVAL_WIRELESS_MS,
    CATEGORY_LABELS,
    LIFTOFF_CHOICES,
    POLLING_CHOICES,
    SLIDER_MAX,
    EditorModel,
    Session,
    action_label,
    action_search,
    dpi_to_slider,
    export_profiles,
    group_actions,
    import_profile,
    slider_to_dpi,
    transport_label,
)

APP_ID = "io.github.liftelite.Control"
HERE = Path(__file__).resolve().parent


def _data_dirs() -> list[Path]:
    """Icon search paths when running from the source tree."""
    return [p for p in (HERE.parent.parent / "data" / "icons",) if p.is_dir()]


def run_in_thread(fn, on_done) -> None:
    """Run ``fn()`` in a worker thread, then ``on_done(result, error)`` on the GTK thread."""

    def worker():
        try:
            result, error = fn(), None
        except Exception as exc:  # noqa: BLE001 - shown to the user
            result, error = None, exc
            if not isinstance(exc, LiftEliteError | OSError):
                traceback.print_exc()
        GLib.idle_add(lambda: (on_done(result, error), False)[1])

    threading.Thread(target=worker, daemon=True).start()


def rgba(hex_or_tuple, alpha: float = 1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if isinstance(hex_or_tuple, str):
        c.parse(hex_or_tuple)
    else:
        r, g, b = hex_or_tuple
        c.red, c.green, c.blue = r / 255, g / 255, b / 255
    c.alpha = alpha
    return c


# --- mouse diagram -------------------------------------------------------------------------


class MouseDiagram(Gtk.DrawingArea):
    """Top view of the Lift Elite plus an underside inset for the purple button."""

    W, H = DIAGRAM_W, DIAGRAM_H

    def __init__(self, on_pick) -> None:
        super().__init__()
        self.on_pick = on_pick
        self.art = preview_art()
        self.selected: str | None = None
        self.hover: str | None = None
        self.changed: set[str] = set()
        self.led = (0, 0, 255)
        self.set_content_width(240)
        self.set_content_height(400)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_a: self._set_hover(None))
        self.add_controller(motion)
        self._xf = (1.0, 0.0, 0.0)
        # Responsive: top and underside side by side (same size) when the
        # panel is wide enough, otherwise stacked. Decided on width only so
        # changing the height request cannot flip it back and forth.
        self.layout = "stacked"
        # True while the grip is dragged: layout locked, cached art drawn scaled
        self.live_resize = False
        self.connect("resize", self._on_resize)

    def _draw(self, _area, cr, width, height) -> None:
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba() if hasattr(
            Adw.StyleManager.get_default(), "get_accent_color_rgba") else rgba("#3584e4")
        if self.layout == "side" and height > self.max_height:
            dy = (height - self.max_height) / 2
            cr.translate(0, dy)
            height = self.max_height
        else:
            dy = 0.0
        self._xf = self.art.draw(
            cr, width, height,
            led=self.led,
            selected=self.selected,
            hover=self.hover,
            changed=self.changed,
            fg=fg,
            accent=accent,
            underside_label=_("Underside"),
            top_label=_("Top") if self.layout == "side" else "",
            layout=self.layout,
            fast=self.live_resize,
        )
        if dy:
            sc, ox, oy, *rest = self._xf
            self._xf = (sc, ox, oy + dy, *rest)

    STACKED_HEIGHT = 400
    # Side by side: never taller than ``max_height``, whatever the window size
    # (the drawing keeps its aspect ratio and is centred horizontally). The
    # user can drag it between MIN_HEIGHT and MAX_HEIGHT (saved in
    # settings.json as "preview_height"); SIDE_MAX_HEIGHT is the default.
    SIDE_MAX_HEIGHT = 280
    MIN_HEIGHT = 180
    MAX_HEIGHT = 420
    max_height = SIDE_MAX_HEIGHT

    @classmethod
    def clamp_height(cls, value) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return cls.SIDE_MAX_HEIGHT
        return max(cls.MIN_HEIGHT, min(cls.MAX_HEIGHT, value))

    def set_max_height(self, value: int) -> int:
        """Preview height limit (side layout); stacked scales with it.

        The content height is set right away (not on idle), so one drag
        frame is one layout pass."""
        self.max_height = self.clamp_height(value)
        want = self.max_height if self.layout == "side" else self.stacked_height()
        if self.get_content_height() != want:
            self.set_content_height(want)
        if not self.live_resize:
            self._on_resize(self, self.get_width(), self.get_height())
        self.queue_draw()
        return self.max_height

    def begin_live_resize(self) -> None:
        self.live_resize = True

    def end_live_resize(self) -> None:
        self.live_resize = False
        self._on_resize(self, self.get_width(), self.get_height())
        self.queue_draw()  # crisp re-render at the final size

    def stacked_height(self) -> int:
        return round(self.STACKED_HEIGHT * self.max_height / self.SIDE_MAX_HEIGHT)

    def width_for_height(self, height: int) -> int:
        """Smallest width at which the side layout reaches ``height``."""
        lo, hi = 1, 4000
        while lo < hi:
            mid = (lo + hi) // 2
            if self.art.side_content_height(mid) >= height:
                hi = mid
            else:
                lo = mid + 1
        return max(lo, int(math.ceil(self.art.SIDE_MIN_WIDTH)))

    @classmethod
    def layout_for_width(cls, width: int) -> str:
        return "side" if width >= preview_art().SIDE_MIN_WIDTH else "stacked"

    def _on_resize(self, _area, width: int, _height: int) -> None:
        if self.live_resize:
            return  # layout and height are driven by the grip until release
        layout = self.layout_for_width(width)
        want = min(self.art.side_content_height(width), self.max_height) if layout == "side" else self.stacked_height()
        if layout != self.layout or self.get_content_height() != want:
            self.layout = layout
            GLib.idle_add(lambda: (self.set_content_height(want), self.queue_draw(), False)[2])

    def _hit(self, x: float, y: float) -> str | None:
        return self.art.widget_hit(x, y, self._xf)

    def _set_hover(self, name: str | None) -> None:
        if name != self.hover:
            self.hover = name
            self.set_cursor(Gdk.Cursor.new_from_name("pointer" if name else "default"))
            self.set_tooltip_text(_(BUTTON_LABELS[name]) if name else _("Click a button on the mouse to remap it"))
            self.queue_draw()

    def _on_motion(self, _ctl, x, y) -> None:
        self._set_hover(self._hit(x, y))

    def _on_click(self, _gesture, _n, x, y) -> None:
        name = self._hit(x, y)
        if name:
            self.on_pick(name)


class ColorDot(Gtk.DrawingArea):
    def __init__(self, size: int = 12) -> None:
        super().__init__()
        self.rgb = (128, 128, 128)
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_rgb(self, rgb) -> None:
        self.rgb = rgb
        self.queue_draw()

    def _draw(self, _a, cr, w, h) -> None:
        r = min(w, h) / 2
        cr.arc(w / 2, h / 2, r - 0.5, 0, 2 * math.pi)
        cr.set_source_rgb(*(c / 255 for c in self.rgb))
        cr.fill()


class FixedWidthBox(Gtk.Box):
    """Vertical box that always takes exactly ``width`` px horizontally.

    Used for the DPI stage label column: whatever the title/subtitle say
    (bold active title, "attivo · 800 → 1150", nothing), the column has
    the same minimum and natural width, so the slider next to it keeps the
    same x and the same width in every row and every state. Labels added
    with :meth:`add_label` ellipsize and report a ~1-char natural width, so
    the size request below is always the larger value and wins.
    """

    def __init__(self, width: int) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.set_size_request(width, -1)
        self.set_hexpand(False)
        self.set_valign(Gtk.Align.CENTER)

    def add_label(self, text: str = "", *css: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0, ellipsize=3, max_width_chars=1, hexpand=False)  # 3 = END
        for c in css:
            label.add_css_class(c)
        self.append(label)
        return label


STAGE_LABEL_WIDTH = 150  # px; fits "Stadio 5" and "attivo · 26000 → 25950" (ellipsized beyond)


# --- action picker -------------------------------------------------------------------------


class ActionPicker(Adw.Dialog):
    def __init__(self, button: str, current: int, on_chosen) -> None:
        super().__init__()
        self.on_chosen = on_chosen
        self.button = button
        self.current = current
        self.set_title(_("Choose action"))
        self.set_content_width(460)
        self.set_content_height(640)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("Choose action"),
                                                subtitle=_("Action for: {button}", button=_(BUTTON_LABELS[button]))))
        view.add_top_bar(header)
        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_box.add_css_class("picker-header")
        self.entry = Gtk.SearchEntry(placeholder_text=_("Search actions"))
        self.entry.set_tooltip_text(_("Type a name, a key or a code (e.g. volume, F5, 0x04)"))
        self.entry.connect("search-changed", lambda *_a: self._refilter())
        self.entry.connect("activate", self._activate_first)
        search_box.append(self.entry)
        view.add_top_bar(search_box)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._row_activated)
        self.rows: list[tuple[Gtk.ListBoxRow, int]] = []
        self._category_of: dict[Gtk.ListBoxRow, str] = {}
        for category, actions in group_actions(action_search("")):
            for act in actions:
                row = Adw.ActionRow(use_markup=False, title=_(act.label), subtitle=f"{act.name}")
                row.set_activatable(True)
                code_lbl = Gtk.Label(label=f"0x{act.code:02x}")
                code_lbl.add_css_class("code-label")
                row.add_suffix(code_lbl)
                if act.code == current:
                    row.add_suffix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
                row.code = act.code  # type: ignore[attr-defined]
                self.listbox.append(row)
                self.rows.append((row, act.code))
                self._category_of[row] = category
        self.listbox.set_header_func(self._header)
        self._visible: set[int] | None = None
        self.listbox.set_filter_func(self._filter)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.listbox)
        self.empty = Adw.StatusPage(icon_name="edit-find-symbolic", title=_("No action found"))
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)

        bottom = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        default_code = FACTORY_ACTIONS[BUTTON_NAMES.index(button)]
        reset = Gtk.Button(label=_("Default") + f": {action_label(default_code)}")
        reset.set_tooltip_text(_("Restore the default action"))
        reset.connect("clicked", lambda *_a: self._choose(default_code))
        bottom.append(reset)
        view.add_bottom_bar(bottom)
        self.set_child(view)
        self.set_focus(self.entry)

    def _header(self, row, before) -> None:
        cat = self._category_of.get(row)
        if before is None or self._category_of.get(before) != cat:
            lbl = Gtk.Label(label=_(CATEGORY_LABELS[cat]).upper(), xalign=0)
            lbl.add_css_class("category-header")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def _filter(self, row) -> bool:
        return self._visible is None or row.code in self._visible

    def _refilter(self) -> None:
        q = self.entry.get_text().strip()
        self._visible = None if not q else {a.code for a in action_search(q)}
        self.listbox.invalidate_filter()
        self.listbox.invalidate_headers()
        self.stack.set_visible_child_name("empty" if self._visible == set() else "list")

    def _activate_first(self, *_a) -> None:
        for row, code in self.rows:
            if self._visible is None or code in self._visible:
                self._choose(code)
                return

    def _row_activated(self, _lb, row) -> None:
        self._choose(row.code)

    def _choose(self, code: int) -> None:
        self.on_chosen(self.button, code)
        self.close()


# --- main window ---------------------------------------------------------------------------


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: LiftEliteApp) -> None:
        super().__init__(application=app, title=_("Lift Elite Control"))
        self.app = app
        self.session: Session = app.session
        self.model = EditorModel()
        self.store: ProfileStore = self.session.store
        self._updating = False
        self.set_default_size(1180, 830)
        self.set_size_request(860, 600)
        self.set_icon_name(APP_ID)
        # «Apply immediately» (persisted in settings.json): debounced writes
        self.auto_apply = bool(app.settings.get("auto_apply", False))
        self._auto_source = 0
        self._auto_inflight = False
        self._auto_sig = None
        self._auto_error = False
        self._slider_held: int | None = None  # stage whose slider is being dragged

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # header
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=_("Lift Elite Control"), subtitle="")
        header.set_title_widget(self.title)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Read the mouse again"))
        self.refresh_btn.connect("clicked", lambda *_a: self.request_refresh())
        header.pack_start(self.refresh_btn)
        self.reset_btn = Gtk.Button(icon_name="edit-clear-all-symbolic",
                                    tooltip_text=_("Reset everything: all 5 profiles to factory defaults"))
        self.reset_btn.connect("clicked", lambda *_a: self.confirm_reset())
        header.pack_start(self.reset_btn)
        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text=_("Main menu"))
        menu_btn.set_menu_model(self._build_menu())
        header.pack_end(menu_btn)
        self.apply_btn = Gtk.Button(label=_("Apply"), tooltip_text=_("Write the changes to the mouse"))
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_a: self.confirm_apply())
        header.pack_end(self.apply_btn)
        self.discard_btn = Gtk.Button(label=_("Discard"), tooltip_text=_("Discard unsaved changes"))
        self.discard_btn.connect("clicked", lambda *_a: self.discard())
        header.pack_end(self.discard_btn)
        # «Apply immediately»: replaces Apply/Discard with a status label
        self.auto_status = Gtk.Label(label="", css_classes=["dim-label", "auto-status"])
        header.pack_end(self.auto_status)
        view.add_top_bar(header)

        # pages
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.loading_page = Adw.StatusPage(title=_("Reading settings…"), icon_name=APP_ID)
        self.loading_page.set_description(self._startup_note())
        self.stack.add_named(self.loading_page, "loading")
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_main_page(), "main")
        self.set_preview_height(app.settings.get("preview_height", MouseDiagram.SIDE_MAX_HEIGHT))
        view.set_content(self.stack)
        self.stack.set_visible_child_name("loading")

        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self.device_present = False
        self._monitor: Monitor | None = None
        self._hotplug_refreshing = False
        # live DPI-stage poll (purple button short press); see _poll_tick
        self._poll_source = 0
        self._poll_inflight = False
        self._poll_failures = 0
        self.connect("notify::is-active", lambda *_a: self._schedule_poll())
        self._sync_header()
        self._start_monitor()

    # --- construction ---------------------------------------------------------------------

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append(_("Save as named profile…"), "win.save-named")
        s1.append(_("Load named profile…"), "win.load-named")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append(_("Import from file…"), "win.import")
        s2.append(_("Export this profile…"), "win.export-one")
        s2.append(_("Export all profiles…"), "win.export-all")
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        s3.append(_("Factory buttons for this profile"), "win.factory-buttons")
        s3.append(_("Apply immediately"), "win.auto-apply")
        theme = Gio.Menu()
        theme.append(_("Follow system"), "app.theme::default")
        theme.append(_("Light"), "app.theme::light")
        theme.append(_("Dark"), "app.theme::dark")
        s3.append_submenu(_("Appearance"), theme)
        lang = Gio.Menu()
        lang.append(_("Automatic (system)"), "app.language::auto")
        lang.append("Italiano", "app.language::it")
        lang.append("English", "app.language::en")
        s3.append_submenu(_("Language"), lang)
        menu.append_section(None, s3)
        s4 = Gio.Menu()
        s4.append(_("About Lift Elite Control"), "app.about")
        menu.append_section(None, s4)
        return menu

    def _install_actions(self) -> None:
        for name, cb in (
            ("save-named", self.save_named),
            ("load-named", self.load_named),
            ("import", self.import_file),
            ("export-one", lambda: self.export_file(all_profiles=False)),
            ("export-all", lambda: self.export_file(all_profiles=True)),
            ("factory-buttons", self.factory_buttons),
            ("apply", self.confirm_apply),
            ("refresh", self.request_refresh),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(act)
        auto = Gio.SimpleAction.new_stateful("auto-apply", None, GLib.Variant.new_boolean(self.auto_apply))
        auto.connect("change-state", self._on_auto_apply)
        self.add_action(auto)
        app = self.get_application()
        app.set_accels_for_action("win.apply", ["<Control>s"])
        app.set_accels_for_action("win.refresh", ["F5", "<Control>r"])

    def _build_empty_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(icon_name="input-mouse-symbolic", title=_("Mouse not found"))
        self.empty_page = page
        page.set_description(_("Connect the NZXT Lift Elite with the cable or plug in its receiver. The app notices it automatically."))
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        retry = Gtk.Button(label=_("Try again"))
        retry.add_css_class("pill")
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_a: self.refresh())
        demo = Gtk.Button(label=_("Demo mode"))
        demo.add_css_class("pill")
        demo.connect("clicked", lambda *_a: self.start_demo())
        box.append(retry)
        box.append(demo)
        page.set_child(box)
        return page

    def _build_main_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(self._build_sidebar())
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                          margin_top=24, margin_bottom=32, margin_start=18, margin_end=18)
        content.append(self._build_performance_group())
        content.append(self._build_dpi_group())
        content.append(self._build_buttons_group())
        self.footer_note = note = Gtk.Label(label=self._startup_note(), wrap=True)
        note.add_css_class("footer-note")
        content.append(note)
        clamp.set_child(content)
        scroller.set_child(clamp)
        root.append(scroller)
        return root

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=20, margin_bottom=20, margin_start=18, margin_end=18)
        side.set_size_request(self.SIDEBAR_MIN_WIDTH, -1)
        side.add_css_class("sidebar-pane")
        self.sidebar = side
        outer = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        outer.set_child(side)
        outer.add_css_class("sidebar-pane")

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.add_css_class("hero-card")
        top = Gtk.Box(spacing=8, margin_start=6, margin_end=6, margin_top=4)
        name = Gtk.Label(label="NZXT Lift Elite", xalign=0, hexpand=True)
        name.add_css_class("app-title")
        self.pill = Gtk.Label(label="")
        self.pill.add_css_class("connection-pill")
        top.append(name)
        top.append(self.pill)
        hero.append(top)
        self.diagram = MouseDiagram(self.open_picker)
        hero.append(self.diagram)
        # Grip under the preview: drag to change its height (180..420 px),
        # double-click for the default (280). The sidebar widens with it.
        self.preview_grip = Gtk.Box(halign=Gtk.Align.CENTER, height_request=12,
                                    tooltip_text=_("Drag to resize the preview (double-click: default size)"))
        self.preview_grip.add_css_class("preview-grip")
        self.preview_grip.append(Gtk.Box(css_classes=["preview-grip-bar"], valign=Gtk.Align.CENTER,
                                         width_request=44, height_request=4))
        self.preview_grip.set_cursor(Gdk.Cursor.new_from_name("ns-resize"))
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_grip_begin)
        drag.connect("drag-update", self._on_grip_update)
        drag.connect("drag-end", self._on_grip_end)
        self.preview_grip.add_controller(drag)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda _g, n, _x, _y: self.set_preview_height(MouseDiagram.SIDE_MAX_HEIGHT, save=True) if n == 2 else None)
        self.preview_grip.add_controller(dbl)
        hero.append(self.preview_grip)
        hint = Gtk.Label(label=_("Click a button on the mouse to remap it"))
        hint.add_css_class("footer-note")
        hero.append(hint)
        side.append(hero)

        # slot switcher
        slot_group = Adw.PreferencesGroup(title=_("Onboard profile"))
        switcher = Gtk.Box(spacing=0, halign=Gtk.Align.FILL, homogeneous=True)
        switcher.add_css_class("linked")
        switcher.add_css_class("slot-switcher")
        self.slot_buttons: list[Gtk.ToggleButton] = []
        self.slot_dots: list[ColorDot] = []
        self.slot_marks: list[Gtk.Label] = []
        for i in range(5):
            box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            dot = ColorDot(11)
            lbl = Gtk.Label(label=str(i + 1))
            mark = Gtk.Label(label="•")
            mark.add_css_class("dirty-mark")
            mark.set_visible(False)
            box.append(dot)
            box.append(lbl)
            box.append(mark)
            btn = Gtk.ToggleButton(child=box, tooltip_text=_("Profile {n}", n=i + 1))
            if self.slot_buttons:
                btn.set_group(self.slot_buttons[0])
            btn.connect("toggled", self._on_slot_toggled, i)
            switcher.append(btn)
            self.slot_buttons.append(btn)
            self.slot_dots.append(dot)
            self.slot_marks.append(mark)
        # The profile buttons choose what to EDIT. Activating is a separate,
        # explicit choice: «Rendi attivo» (written with Applica, 0x45 first)
        # or the purple-button long press on the mouse.
        hint_text = self._slot_hint_text()
        self.slot_hint = Gtk.Label(label=hint_text, xalign=0, wrap=True)
        self.slot_hint.add_css_class("footer-note")
        # Which profile the mouse is using (0x44, read live); accent bar on
        # its button. Next to it the «Rendi attivo» toggle for the edited one.
        self.active_label = Gtk.Label(label="", xalign=0, visible=False, hexpand=True, wrap=True)
        self.active_label.add_css_class("onboard-active-label")
        self.activate_btn = Gtk.ToggleButton(label=_("Make active"), valign=Gtk.Align.CENTER, visible=False)
        self.activate_btn.add_css_class("pill")
        self.activate_btn.add_css_class("small")
        self.activate_btn.connect("toggled", self._on_activate_toggled)
        active_row = Gtk.Box(spacing=8)
        active_row.append(self.active_label)
        active_row.append(self.activate_btn)
        slot_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        slot_box.append(switcher)
        slot_box.append(active_row)
        slot_box.append(self.slot_hint)
        slot_group.add(slot_box)
        side.append(slot_group)

        # device info (connection and firmware versions; no "read-only" subtitle)
        self.dev_group = dev_group = Adw.PreferencesGroup(title=_("Device"))
        self.row_conn = Adw.ActionRow(title=_("Connection"))
        self.row_fw = Adw.ActionRow(title=_("Mouse firmware"))
        self.row_upd = Adw.ActionRow(title=_("Updater"))
        self.row_bridge = Adw.ActionRow(title=_("Bridge"))
        self.row_rx = Adw.ActionRow(title=_("Receiver firmware"))
        for row in (self.row_conn, self.row_fw, self.row_upd, self.row_bridge, self.row_rx):
            row.add_css_class("property")
            dev_group.add(row)
        side.append(dev_group)
        return outer

    def _slot_hint_text(self) -> str:
        if not ENABLE_ACTIVE_PROFILE_APPLY:
            return _("Choose the profile to edit. To change the active one, long-press the purple button.")
        if self.auto_apply:
            return _("Choose the profile to edit. «Make active» activates it on the mouse right away; "
                     "a long press of the purple button also changes profile.")
        return _("Choose the profile to edit. «Make active» activates it on the mouse with Apply; "
                 "a long press of the purple button also changes profile.")

    def _startup_note(self) -> str:
        return (_("Nothing is written on startup. With «Apply immediately» every change is written right away.")
                if self.auto_apply else
                _("Nothing is written on startup. Changes are sent only when you press Apply."))

    SIDEBAR_MIN_WIDTH = 340
    # sidebar width minus the preview width (margins + card padding)
    SIDEBAR_CHROME = 2 * 18 + 2 * 10

    def set_preview_height(self, height: int, *, save: bool = False) -> int:
        """Resize the preview (aspect kept); widen the sidebar so it fits side by side."""
        h = self.diagram.set_max_height(height)
        need = self.diagram.width_for_height(h) + self.SIDEBAR_CHROME
        self.sidebar.set_size_request(max(self.SIDEBAR_MIN_WIDTH, need), -1)
        if save:
            try:
                save_setting("preview_height", h)
            except OSError as exc:
                self.toast(_("Could not save the preference: {error}", error=exc))
        return h

    # Live resize. GestureDrag offsets are relative to the grip, which moves as
    # the preview grows/shrinks, so using them directly feeds back and
    # oscillates. The pointer is converted to window coordinates instead, and
    # the height is start height + total pointer travel. At most one resize per
    # frame (tick callback); the art is drawn scaled from the last crisp render
    # and re-rendered on release; the layout is locked; saved on release only.

    def _grip_window_y(self, gesture, dy: float) -> float | None:
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return None
        pt = Graphene.Point()
        pt.init(sx, sy + dy)
        ok, out = self.preview_grip.compute_point(self, pt)
        return out.y if ok else None

    def _on_grip_begin(self, gesture, _x, _y) -> None:
        self._grip_start = self.diagram.max_height
        self._grip_y0 = self._grip_window_y(gesture, 0.0)
        self._grip_target = self._grip_start
        self.diagram.begin_live_resize()
        if getattr(self, "_grip_tick", None) is None:
            self._grip_tick = self.preview_grip.add_tick_callback(self._grip_on_tick)

    def _grip_target_for(self, gesture, dy: float) -> int:
        y = self._grip_window_y(gesture, dy)
        if y is None or self._grip_y0 is None:
            return self._grip_target
        return MouseDiagram.clamp_height(round(self._grip_start + (y - self._grip_y0)))

    def _on_grip_update(self, gesture, _dx, dy) -> None:
        self._grip_target = self._grip_target_for(gesture, dy)

    def _grip_on_tick(self, _widget, _clock) -> bool:
        if self._grip_target != self.diagram.max_height:
            self.set_preview_height(self._grip_target)
        return True

    def _on_grip_end(self, gesture, _dx, dy) -> None:
        self._grip_target = self._grip_target_for(gesture, dy)
        if getattr(self, "_grip_tick", None) is not None:
            self.preview_grip.remove_tick_callback(self._grip_tick)
            self._grip_tick = None
        self.diagram.end_live_resize()
        self.set_preview_height(self._grip_target, save=True)

    def _build_performance_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=_("Performance"))
        self.polling_row = Adw.ComboRow(title=_("Polling rate"))
        self.polling_row.set_model(Gtk.StringList.new([_("{hz} Hz", hz=hz) for hz in POLLING_CHOICES]))
        self.polling_row.connect("notify::selected", self._on_polling)
        group.add(self.polling_row)
        self.liftoff_row = Adw.ComboRow(
            title=_("Lift-off distance"),
            subtitle=_("Height above which the sensor stops tracking when you lift the mouse."),
        )
        self.liftoff_row.set_model(Gtk.StringList.new([_("{mm} mm", mm=mm) for mm in LIFTOFF_CHOICES]))
        self.liftoff_row.connect("notify::selected", self._on_liftoff)
        group.add(self.liftoff_row)
        self.motion_row = Adw.SwitchRow(title=_("Motion sync"),
                                        subtitle=_("Syncs sensor frames with USB reports (slightly smoother, tiny extra latency)"))
        self.motion_row.connect("notify::active", self._on_motion)
        group.add(self.motion_row)
        self.color_row = Adw.ActionRow(
            title=_("Profile LED colour"),
            subtitle=_("shown for ~2 s when you change profile by holding the purple button"),
        )
        dialog = Gtk.ColorDialog(title=_("Choose the LED colour"), with_alpha=False)
        self.color_btn = Gtk.ColorDialogButton(dialog=dialog, valign=Gtk.Align.CENTER)
        self.color_btn.connect("notify::rgba", self._on_color)
        self.color_row.add_suffix(self.color_btn)
        group.add(self.color_row)
        return group

    def _build_dpi_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title=_("DPI stages"),
            description=_("The purple button under the mouse cycles through these five values. The selected stage is the one stored as active."),
        )
        self.stage_rows: list[Adw.PreferencesRow] = []
        self.stage_checks: list[Gtk.CheckButton] = []
        self.stage_scales: list[Gtk.Scale] = []
        self.stage_spins: list[Gtk.SpinButton] = []
        self.stage_titles: list[Gtk.Label] = []
        self.stage_subtitles: list[Gtk.Label] = []
        for i in range(5):
            # Not an Adw.ActionRow: its title box sizes itself to the
            # title/subtitle text (and the bold active title) and shares the
            # spare width with the slider, so editing the active stage
            # ("attivo · 1000 → 1350") used to shrink the slider under the
            # pointer. Here the label column has a fixed width and the slider
            # takes all the remaining space: same x and width in every row.
            row = Adw.PreferencesRow(activatable=True)
            row.add_css_class("stage-row")
            box = Gtk.Box(spacing=12)
            box.add_css_class("stage-box")
            row.set_child(box)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text=_("Make this the active stage"))
            if self.stage_checks:
                check.set_group(self.stage_checks[0])
            check.connect("toggled", self._on_stage_toggled, i + 1)
            box.append(check)
            row.connect("activate", lambda _r, c=check: c.set_active(True))
            column = FixedWidthBox(STAGE_LABEL_WIDTH)
            column.add_css_class("stage-labels")
            title = column.add_label(_("Stage {n}", n=i + 1), "stage-title")
            sub = column.add_label("", "stage-subtitle", "dim-label", "caption")
            sub.set_visible(False)
            box.append(column)
            row.set_title(_("Stage {n}", n=i + 1))  # accessibility / search only
            self.stage_titles.append(title)
            self.stage_subtitles.append(sub)
            scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, Gtk.Adjustment(lower=0, upper=SLIDER_MAX, step_increment=1, page_increment=50))
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_size_request(240, -1)
            scale.set_valign(Gtk.Align.CENTER)
            for mark in (400, 1600, 6400, 26000):
                scale.add_mark(dpi_to_slider(mark), Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self._on_scale, i + 1)
            # «Apply immediately»: a slider dragged with the mouse/touch is
            # written only when the button/finger is released (keyboard and
            # scroll changes use the ~400 ms debounce).
            legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            legacy.connect("event", self._on_scale_event, i + 1)
            scale.add_controller(legacy)
            spin = Gtk.SpinButton.new(Gtk.Adjustment(lower=DPI_VALUE_MIN, upper=DPI_VALUE_MAX,
                                                     step_increment=DPI_STEP, page_increment=500), DPI_STEP, 0)
            spin.set_numeric(True)
            spin.set_width_chars(6)
            spin.set_valign(Gtk.Align.CENTER)
            spin.add_css_class("dpi-spin")
            spin.connect("value-changed", self._on_spin, i + 1)
            box.append(scale)
            box.append(spin)
            group.add(row)
            self.stage_rows.append(row)
            self.stage_checks.append(check)
            self.stage_scales.append(scale)
            self.stage_spins.append(spin)
        return group

    def _build_buttons_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title=_("Buttons"),
            description=_("Choose what each button does in this profile. Media keys work on Linux without drivers."),
        )
        self.buttons_group = group
        self.button_rows: dict[str, Adw.ActionRow] = {}
        self.button_chips: dict[str, Gtk.Label] = {}
        for name in BUTTON_NAMES:
            row = Adw.ActionRow(title=_(BUTTON_LABELS[name]))
            default = FACTORY_ACTIONS[BUTTON_NAMES.index(name)]
            row.set_subtitle(f"{_('Default')}: {action_label(default)}")
            chip = Gtk.Label(label="")
            chip.add_css_class("action-chip")
            inner = Gtk.Box(spacing=6)
            inner.append(chip)
            inner.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
            btn = Gtk.Button(child=inner, valign=Gtk.Align.CENTER, tooltip_text=_("Choose action"))
            btn.connect("clicked", lambda _b, n=name: self.open_picker(n))
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_a, n=name: self._diagram_hover(n))
            hover.connect("leave", lambda *_a: self._diagram_hover(None))
            row.add_controller(hover)
            group.add(row)
            self.button_rows[name] = row
            self.button_chips[name] = chip
        return group

    # --- state <-> widgets ----------------------------------------------------------------

    def _diagram_hover(self, name) -> None:
        self.diagram.hover = name
        self.diagram.queue_draw()

    def busy(self, on: bool, text: str | None = None) -> None:
        self.spinner.set_spinning(on)
        self.refresh_btn.set_sensitive(not on)
        self.reset_btn.set_sensitive(not on and self.model.loaded and self.device_present)
        self.apply_btn.set_sensitive(not on and self.model.has_pending_apply and self.device_present)
        if text:
            self.title.set_subtitle(text)
        if not on:
            self._schedule_poll()

    def _sync_header(self) -> None:
        pending = self.model.loaded and self.model.has_pending_apply
        dirty = pending
        self.apply_btn.set_sensitive(bool(pending) and self.device_present and not self.spinner.get_spinning())
        self.apply_btn.set_visible(not self.auto_apply)
        self.discard_btn.set_visible(bool(dirty) and not self.auto_apply)
        self.reset_btn.set_sensitive(self.model.loaded and self.device_present and not self.spinner.get_spinning()
                                     and not self._auto_inflight)
        self.auto_status.set_visible(self.auto_apply and self.model.loaded)
        if self.auto_apply:
            if not self.device_present and pending:
                self.auto_status.set_label(_("Not applied (disconnected)"))
            elif pending or self._auto_inflight:
                self.auto_status.set_label(_("Applying…"))
            else:
                self.auto_status.set_label("✓ " + _("Applied"))
        if self.model.loaded:
            if not self.device_present and not self.session.is_mock:
                sub = _("Disconnected")
            else:
                sub = transport_label(self.model.transport)
                if self.session.is_mock:
                    sub = _("Demo (emulated mouse)") + " · " + sub
            if dirty and not self.auto_apply:
                sub += " · " + _("modified")
            self.title.set_subtitle(sub)
        for i, mark in enumerate(self.slot_marks):
            mark.set_visible(self.model.loaded and self.model.is_dirty(i))
        self._schedule_poll()
        self._auto_schedule()

    def populate(self) -> None:
        m = self.model
        state = m.state
        p = m.current()
        orig = m.original()
        self._updating = True
        try:
            # device panel
            transport = m.transport
            for cls in ("wired", "wireless", "demo"):
                self.pill.remove_css_class(cls)
            if self.session.is_mock:
                self.pill.set_label("DEMO")
                self.pill.add_css_class("demo")
            elif not self.device_present:
                self.pill.set_label(_("Disconnected"))
            else:
                self.pill.set_label(transport_label(transport))
                self.pill.add_css_class(transport or "wired")
            if not self.device_present and not self.session.is_mock:
                self.row_conn.set_subtitle(_("Disconnected"))
            else:
                self.row_conn.set_subtitle(transport_label(transport) + (f" · {state.info.path}" if state else ""))
            fw = state.firmware if state else None
            self.row_fw.set_subtitle(f"{fw.name} {fw.fmt(fw.app_version)} ({fw.mode_name})" if fw else "—")
            self.row_upd.set_subtitle(fw.fmt(fw.updater_version) if fw else "—")
            self.row_bridge.set_subtitle(fw.fmt(fw.bridge_version) if fw else "—")
            rx = state.receiver_firmware if state else None
            self.row_rx.set_visible(rx is not None)
            if rx:
                self.row_rx.set_subtitle(f"{rx.fmt(rx.app_version)} ({rx.mode_name})")
            # slots
            for i, (btn, dot) in enumerate(zip(self.slot_buttons, self.slot_dots, strict=True)):
                btn.set_active(i == m.selected)
                if i in m.drafts:
                    dot.set_rgb(rgb565_to_rgb(m.drafts[i].color))
            self._sync_onboard_active()
            # performance
            self.polling_row.set_selected(POLLING_CHOICES.index(p.polling_hz))
            note = m.polling_note()
            self.polling_row.set_subtitle(note or "")
            self.liftoff_row.set_selected(LIFTOFF_CHOICES.index(p.liftoff) if p.liftoff in LIFTOFF_CHOICES else 0)
            self.motion_row.set_active(p.motion_sync)
            self.color_btn.set_rgba(rgba(rgb565_to_rgb(p.color)))
            # dpi
            for i in range(5):
                visible = i < p.stage_count
                self.stage_rows[i].set_visible(visible)
                value = p.dpi[i]
                self.stage_spins[i].set_value(value)
                self.stage_scales[i].set_value(dpi_to_slider(value))
            self._sync_stage_marks()
            # buttons
            changed: set[str] = set()
            for name in BUTTON_NAMES:
                code = (p.buttons or {}).get(name)
                chip = self.button_chips[name]
                chip.set_label(action_label(code) if code is not None else "—")
                is_changed = code != (orig.buttons or {}).get(name)
                (chip.add_css_class if is_changed else chip.remove_css_class)("changed")
                if is_changed:
                    changed.add(name)
                act = action_by_code(code) if code is not None else None
                self.button_chips[name].set_tooltip_text(f"{act.name} · 0x{code:02x}" if act else None)
            self.diagram.changed = changed
            self.diagram.led = rgb565_to_rgb(p.color)
            self.diagram.queue_draw()
        finally:
            self._updating = False
        self._sync_header()

    # --- edit handlers --------------------------------------------------------------------

    def _edit(self, fn) -> None:
        if self._updating or not self.model.loaded:
            return
        try:
            fn()
        except LiftEliteError as exc:
            self.toast(str(exc))
        self.populate()

    def _on_slot_toggled(self, btn, index) -> None:
        if btn.get_active() and not self._updating and self.model.loaded:
            self.model.select(index)
            self.populate()

    def _on_polling(self, row, _pspec) -> None:
        self._edit(lambda: self.model.set_polling(POLLING_CHOICES[row.get_selected()]))

    def _on_liftoff(self, row, _pspec) -> None:
        self._edit(lambda: self.model.set_liftoff(LIFTOFF_CHOICES[row.get_selected()]))

    def _on_motion(self, row, _pspec) -> None:
        self._edit(lambda: self.model.set_motion_sync(row.get_active()))

    def _on_color(self, btn, _pspec) -> None:
        c = btn.get_rgba()
        self._edit(lambda: self.model.set_color_rgb(round(c.red * 255), round(c.green * 255), round(c.blue * 255)))

    def _on_stage_toggled(self, check, stage) -> None:
        if check.get_active():
            self._edit(lambda: self.model.set_active_stage(stage))

    def _on_scale(self, scale, stage) -> None:
        if self._updating:
            return
        value = slider_to_dpi(scale.get_value())
        if value == self.model.current().dpi[stage - 1]:
            return
        self.model.set_dpi(stage, value)
        self._updating = True
        self.stage_spins[stage - 1].set_value(value)
        self._updating = False
        self._light_refresh(stage)

    def _on_spin(self, spin, stage) -> None:
        if self._updating:
            return
        value = int(spin.get_value())
        if value == self.model.current().dpi[stage - 1]:
            return
        self._edit(lambda: self.model.set_dpi(stage, value))

    def _light_refresh(self, stage: int) -> None:
        """While dragging a slider: update subtitle/header without resetting the slider."""
        self._sync_stage_marks()
        self._sync_header()

    def _sync_onboard_active(self) -> None:
        """Mark the profile the mouse is using (read-only 0x44; live-polled)."""
        active = self.model.onboard_active_profile if self.model.loaded else None
        for i, btn in enumerate(self.slot_buttons):
            if active is not None and i == active:
                btn.add_css_class("onboard-active")
                btn.set_tooltip_text(_("Profile {n} (active on the mouse)", n=i + 1))
            else:
                btn.remove_css_class("onboard-active")
                btn.set_tooltip_text(_("Profile {n}", n=i + 1))
        m = self.model
        pending = m.pending_activation if m.activation_pending else None
        if active is not None:
            text = _("Active on the mouse: profile {n}", n=active + 1)
            if pending is not None:
                text += " → " + (_("profile {n}…", n=pending + 1) if self.auto_apply
                                 else _("profile {n} with Apply", n=pending + 1))
            self.active_label.set_label(text)
        self.active_label.set_visible(active is not None)
        # «Rendi attivo» for the edited profile when it is not the active one
        can_activate = ENABLE_ACTIVE_PROFILE_APPLY and m.loaded and active is not None and m.selected != active
        was = self._updating
        self._updating = True
        try:
            self.activate_btn.set_visible(can_activate)
            self.activate_btn.set_active(can_activate and pending == m.selected)
            self.activate_btn.set_tooltip_text(
                None if not can_activate else
                _("Profile {n} becomes the active one on the mouse right away", n=m.selected + 1) if self.auto_apply
                else _("Profile {n} becomes the active one on the mouse when you press Apply", n=m.selected + 1)
            )
        finally:
            self._updating = was
        # The firmware writes button remaps into the ACTIVE profile whatever
        # profile byte is sent: buttons are editable on the active profile or
        # on the one Applica activates first.
        locked = self.buttons_locked()
        for row in self.button_rows.values():
            row.set_sensitive(not locked)
        if locked:
            desc = (
                _("Buttons can only be changed on the profile active on the mouse (now profile {n}). "
                  "Press «Make active» to activate this one.", n=active + 1)
                if ENABLE_ACTIVE_PROFILE_APPLY and self.auto_apply
                else _("Buttons can only be changed on the profile active on the mouse (now profile {n}). "
                       "Press «Make active» to activate this one with Apply.", n=active + 1)
                if ENABLE_ACTIVE_PROFILE_APPLY
                else _("Buttons can only be changed on the profile active on the mouse (now profile {n}). "
                       "Long-press the purple button to activate this one.", n=active + 1)
            )
        else:
            desc = _("Choose what each button does in this profile. Media keys work on Linux without drivers.")
        self.buttons_group.set_description(desc)

    def _on_activate_toggled(self, btn) -> None:
        if self._updating or not self.model.loaded:
            return
        self.model.request_activation(self.model.selected if btn.get_active() else None)
        self._sync_onboard_active()
        self._sync_header()
        if self.auto_apply and self.model.activation_pending:
            self._auto_schedule(0)  # «Make active» in auto mode: activate now

    def buttons_locked(self) -> bool:
        """True when button writes would not land in the edited profile."""
        m = self.model
        return m.loaded and not m.buttons_writable()

    def _stage_subtitle(self, i: int) -> str:
        """Subtitle of DPI stage row ``i`` (0-based): active marker + value change."""
        p, orig = self.model.current(), self.model.original()
        parts: list[str] = []
        if i + 1 == p.stage:
            parts.append(_("active"))
        elif orig.stage != p.stage and i + 1 == orig.stage:
            # The user picked another stage (pending until Applica); this is
            # the one the mouse is using right now.
            parts.append(_("active on the mouse"))
        if p.dpi[i] != orig.dpi[i]:
            parts.append(f"{orig.dpi[i]} → {p.dpi[i]}")
        return " · ".join(parts)

    def _sync_stage_marks(self) -> None:
        """Radio selection, highlight and subtitles of the DPI rows (no slider changes).

        Safe while a slider is being dragged and when the mouse reports a new
        active stage: only labels/checks change, never widths (fixed column).
        """
        if not self.model.loaded:
            return
        p = self.model.current()
        was_updating = self._updating
        self._updating = True
        try:
            for i in range(5):
                active = (i + 1) == p.stage
                if self.stage_checks[i].get_active() != active and active:
                    self.stage_checks[i].set_active(True)
                row = self.stage_rows[i]
                (row.add_css_class if active else row.remove_css_class)("active-stage")
                text = self._stage_subtitle(i)
                sub = self.stage_subtitles[i]
                sub.set_label(text)
                sub.set_tooltip_text(text or None)
                sub.set_visible(bool(text))
        finally:
            self._updating = was_updating

    def open_picker(self, button: str) -> None:
        if not self.model.loaded:
            return
        if self.buttons_locked():
            self.toast(_("Buttons can only be changed on the profile active on the mouse"))
            return
        self.diagram.selected = button
        self.diagram.queue_draw()
        code = (self.model.current().buttons or {}).get(button, FACTORY_ACTIONS[BUTTON_NAMES.index(button)])
        picker = ActionPicker(button, code, self._on_action_chosen)
        picker.connect("closed", lambda *_a: self._picker_closed())
        self.picker = picker
        picker.present(self)

    def _picker_closed(self) -> None:
        self.diagram.selected = None
        self.diagram.queue_draw()

    def _on_action_chosen(self, button: str, code: int) -> None:
        self._edit(lambda: self.model.set_button(button, code))

    def factory_buttons(self) -> None:
        if self.model.loaded:
            self._edit(self.model.factory_buttons)
            self.toast(_("Factory buttons set") if self.auto_apply
                       else _("Factory buttons set — press Apply to write them"))

    # --- device I/O ----------------------------------------------------------------------

    def refresh(self) -> None:
        self.busy(True, _("Reading settings…") if self.model.loaded else _("Looking for the mouse…"))
        if not self.model.loaded:
            self.stack.set_visible_child_name("loading")

        def done(state, error):
            self.busy(False)
            if error is not None:
                if isinstance(error, DeviceNotFoundError | PermissionError):
                    self.device_present = False
                    if self.model.loaded:
                        # Keep unsaved edits; stay on the main page.
                        self.populate()
                    else:
                        self.empty_page.set_description(
                            str(error) if not isinstance(error, DeviceNotFoundError)
                            else _("Connect the NZXT Lift Elite with the cable or plug in its receiver. The app notices it automatically.")
                        )
                        self.stack.set_visible_child_name("empty")
                elif not self.model.loaded:
                    self.empty_page.set_description(str(error))
                    self.stack.set_visible_child_name("empty")
                self.toast(_("Read failed") + f": {error}")
                self._sync_header()
                return
            self.device_present = True
            if self.model.loaded and self.model.any_dirty:
                kept = self.model.adopt_state(state)
                self.stack.set_visible_child_name("main")
                self.populate()
                if kept:
                    self.toast(_("Re-read the mouse. Unsaved changes were kept."))
            else:
                self.model.load_state(state)
                self.stack.set_visible_child_name("main")
                self.populate()
            self.app.on_loaded(self)

        run_in_thread(self.session.read_state, done)

    def request_refresh(self) -> None:
        if self.model.loaded and self.model.any_dirty:
            dlg = Adw.AlertDialog(heading=_("Unapplied changes"),
                                  body=_("Some changes were not written to the mouse. Reading it again discards them."))
            dlg.add_response("cancel", _("Cancel"))
            dlg.add_response("discard", _("Discard"))
            dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_close_response("cancel")
            dlg.connect("response", lambda _d, r: self.refresh() if r == "discard" else None)
            dlg.present(self)
        else:
            self.refresh()

    def start_demo(self) -> None:
        self._stop_monitor()
        self.session.close()
        self.session.mock = "wired"
        self.device_present = True
        self.refresh()

    def discard(self) -> None:
        self.model.revert()
        self.populate()
        self.toast(_("Changes discarded"))

    def confirm_apply(self) -> None:
        """Apply immediately: no confirmation dialog, no backup popup."""
        if not self.model.loaded:
            return
        if not self.device_present:
            self.toast(_("The mouse is disconnected. Changes will be written when you reconnect and press Apply."))
            return
        if not self.model.has_pending_apply:
            self.toast(_("No changes to apply"))
            return
        self.apply()

    def apply(self) -> None:
        targets = [self.model.drafts[i] for i in self.model.dirty_slots()]
        activate = self.model.pending_activation if self.model.activation_pending else None
        if not targets and activate is None:
            self.toast(_("No changes to apply"))
            return
        self.busy(True, _("Writing…"))

        def done(result, error):
            self.busy(False)
            if error is not None:
                self.error_dialog(_("Write failed"), str(error))
                self.populate()
                return
            profiles, _backup = result
            for p in profiles:
                self.model.mark_applied(p)
            if ENABLE_ACTIVE_PROFILE_APPLY and activate is not None:
                self.model.mark_activated(activate)
            self.populate()
            self.toast(_("Applied and verified"))

        run_in_thread(lambda: self.session.apply(targets, activate_profile=activate), done)

    # --- «Apply immediately» ----------------------------------------------------------------

    AUTO_DELAY_MS = 400

    def _on_auto_apply(self, action, value) -> None:
        action.set_state(value)
        self.auto_apply = value.get_boolean()
        try:
            save_setting("auto_apply", self.auto_apply)
        except OSError as exc:
            self.toast(_("Could not save the preference: {error}", error=exc))
        if self.auto_apply:
            self._auto_sig = None
        else:
            self._auto_cancel()
        self.slot_hint.set_label(self._slot_hint_text())
        self.loading_page.set_description(self._startup_note())
        self.footer_note.set_label(self._startup_note())
        self.populate() if self.model.loaded else self._sync_header()

    _PRESS = ("BUTTON_PRESS", "TOUCH_BEGIN")
    _RELEASE = ("BUTTON_RELEASE", "TOUCH_END", "TOUCH_CANCEL")

    def _on_scale_event(self, ctl, event, stage) -> bool:
        # PyGObject passes None for the GdkEvent argument of ::event (checked
        # with PyGObject 3.50); the controller still hands out the event.
        if event is None and ctl is not None:
            event = ctl.get_current_event()
        if event is None:
            return False
        kind = event.get_event_type().value_nick.upper().replace("-", "_")
        if kind in self._PRESS:
            self.slider_pressed(stage)
        elif kind in self._RELEASE:
            self.slider_released(stage)
        return False  # never swallow: the scale handles the drag itself

    def slider_pressed(self, stage: int) -> None:
        self._slider_held = stage
        if self._auto_source:  # nothing goes out while the slider is held
            GLib.source_remove(self._auto_source)
            self._auto_source = 0
        self._auto_sig = None

    def slider_released(self, stage: int) -> None:
        if self._slider_held is None:
            return
        self._slider_held = None
        self._auto_sig = None
        # after the last value-changed of the drag has been handled
        GLib.idle_add(lambda: (self._auto_schedule(0), False)[1])

    def _auto_cancel(self) -> None:
        if self._auto_source:
            GLib.source_remove(self._auto_source)
            self._auto_source = 0
        self._auto_sig = None

    def _auto_schedule(self, delay: int | None = None) -> None:
        """(Re)start the debounce when what would be written changed.

        Sliders call this on every step, so the write goes out ~400 ms after
        the value settles; one batch at a time (see _auto_fire)."""
        if not (self.auto_apply and self.model.loaded and self.device_present):
            return
        if self._slider_held is not None:
            return  # written on release (slider_released)
        if not self.model.has_pending_apply:
            self._auto_cancel()
            return
        sig = self.model.pending_signature()
        if delay is None and sig == self._auto_sig and (self._auto_source or self._auto_inflight):
            return
        self._auto_sig = sig
        if self._auto_source:
            GLib.source_remove(self._auto_source)
        self._auto_source = GLib.timeout_add(self.AUTO_DELAY_MS if delay is None else delay, self._auto_fire)

    def _auto_fire(self) -> bool:
        self._auto_source = 0
        if self._auto_inflight or self._slider_held is not None:
            return False  # rescheduled when the batch is done / the slider is released
        if not (self.auto_apply and self.model.loaded and self.device_present):
            return False
        m = self.model
        # Only button edits that land in the edited profile (rows are locked otherwise).
        targets = [m.drafts[i] for i in m.dirty_slots()]
        activate = m.pending_activation if m.activation_pending else None
        if not targets and activate is None:
            return False
        self._auto_inflight = True
        self.spinner.set_spinning(True)
        self._sync_header()

        def done(result, error):
            self._auto_inflight = False
            self.spinner.set_spinning(False)
            self._auto_sig = None
            if error is not None:
                # Show it and put the controls back to what the mouse has.
                for t in targets:
                    m.revert(t.index)
                if activate is not None:
                    m.request_activation(None)
                self.error_dialog(_("Write failed"), str(error) + "\n\n" + _("The changed settings were set back to the values on the mouse."))
                self.populate()
                return
            profiles, _backup = result
            sent = {t.index: t for t in targets}
            for p in profiles:
                m.mark_applied(p, sent.get(p.index))
            if activate is not None:
                m.mark_activated(activate)
            self.populate()  # edits made meanwhile are still dirty -> next batch

        run_in_thread(lambda: self.session.apply(targets, activate_profile=activate, auto=True), done)
        return False

    # --- «Reset everything» --------------------------------------------------------------

    def confirm_reset(self) -> None:
        if not self.model.loaded or not self.device_present:
            return
        from ..cli import reset_summary

        body = reset_summary() + " " + _("A backup of every profile is saved first.")
        if self.model.any_dirty:
            body += "\n\n" + _("Unapplied changes will be lost.")
        dlg = Adw.AlertDialog(heading=_("Reset everything to factory defaults?"), body=body)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("reset", _("Reset everything"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: self.reset_all() if r == "reset" else None)
        self.reset_dialog = dlg
        dlg.present(self)

    def reset_all(self) -> None:
        """Factory reset: only verified writes plus 0x45, original profile re-activated."""
        self._auto_cancel()
        self.model.revert()
        self.busy(True, _("Resetting all profiles…"))

        def done(result, error):
            self.busy(False)
            if error is not None:
                self.error_dialog(_("Reset failed"), str(error))
            else:
                self.toast(_("All profiles reset to factory defaults and verified"))
            self.model.revert()
            self.refresh()  # show what the mouse has now

        run_in_thread(self.session.reset_defaults, done)

    # --- profiles on disk -----------------------------------------------------------------

    def save_named(self) -> None:
        if not self.model.loaded:
            return
        dlg = Adw.AlertDialog(heading=_("Save as named profile…"))
        entry = Gtk.Entry(placeholder_text=_("Profile name"), activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("save", _("Save"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")
        dlg.set_close_response("cancel")

        def on_response(_d, response):
            name = entry.get_text().strip()
            if response != "save" or not name:
                return
            try:
                self.store.save_named(name, self.model.current())
                self.toast(_("Saved “{name}”", name=name))
            except (LiftEliteError, OSError) as exc:
                self.toast(str(exc))

        dlg.connect("response", on_response)
        dlg.present(self)

    def load_named(self) -> None:
        if not self.model.loaded:
            return
        dialog = Adw.Dialog(title=_("Named profiles"))
        dialog.set_content_width(440)
        dialog.set_content_height(480)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Named profiles"),
                                     description=_("Stored in {path}", path=str(self.store.profiles_dir)))
        page.add(group)
        items = self.store.list_named()
        if not items:
            group.add(Adw.ActionRow(title=_("No named profiles yet")))
        for item in items:
            c = item.config
            sub = []
            if c.polling_hz:
                sub.append(f"{c.polling_hz} Hz")
            if c.dpi_stages:
                sub.append("DPI " + "/".join(map(str, c.dpi_stages)))
            row = Adw.ActionRow(use_markup=False, title=item.name, subtitle=" · ".join(sub))
            load = Gtk.Button(label=_("Load"), valign=Gtk.Align.CENTER)
            load.add_css_class("suggested-action")
            delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Delete profile"))
            delete.add_css_class("flat")

            def do_load(_b, it=item):
                try:
                    self.model.apply_config(it.config)
                except LiftEliteError as exc:
                    self.toast(str(exc))
                    return
                dialog.close()
                self.populate()
                self.toast(_("Loaded “{name}” into profile {n} — press Apply to write it", name=it.name, n=self.model.selected + 1))

            def do_delete(_b, it=item, r=row):
                try:
                    self.store.delete_named(it.name)
                    group.remove(r)
                    self.toast(_("Deleted “{name}”", name=it.name))
                except (LiftEliteError, OSError) as exc:
                    self.toast(str(exc))

            load.connect("clicked", do_load)
            delete.connect("clicked", do_delete)
            row.add_suffix(load)
            row.add_suffix(delete)
            group.add(row)
        view.set_content(page)
        dialog.set_child(view)
        dialog.present(self)

    def _json_filter(self) -> Gio.ListStore:
        f = Gtk.FileFilter(name=_("JSON profiles"))
        f.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        return store

    def export_file(self, *, all_profiles: bool) -> None:
        if not self.model.loaded:
            return
        dialog = Gtk.FileDialog(title=_("Export all profiles…") if all_profiles else _("Export this profile…"))
        dialog.set_filters(self._json_filter())
        dialog.set_initial_name(_("lift-elite-profiles.json") if all_profiles else _("lift-elite-profile-{n}.json", n=self.model.selected + 1))
        profiles = [self.model.drafts[i] for i in sorted(self.model.drafts)] if all_profiles else [self.model.current()]

        def done(d, res):
            try:
                gfile = d.save_finish(res)
            except GLib.Error:
                return
            try:
                path = export_profiles(gfile.get_path(), profiles)
                self.toast(_("Exported to {path}", path=path))
            except (LiftEliteError, OSError) as exc:
                self.toast(str(exc))

        dialog.save(self, None, done)

    def import_file(self) -> None:
        if not self.model.loaded:
            return
        dialog = Gtk.FileDialog(title=_("Import from file…"))
        dialog.set_filters(self._json_filter())

        def done(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                return
            try:
                cfg = import_profile(gfile.get_path(), self.model.selected)
                self.model.apply_config(cfg)
            except (LiftEliteError, OSError) as exc:
                self.error_dialog(_("Import failed"), str(exc))
                return
            self.populate()
            self.toast(_("Imported {name} — press Apply to write it", name=gfile.get_basename()))

        dialog.open(self, None, done)

    # --- misc -----------------------------------------------------------------------------


    # --- live DPI stage ------------------------------------------------------------------
    #
    # A short press of the purple button changes the active DPI stage inside
    # the mouse. The firmware sends nothing to the host for it (app_btn.c:
    # it bumps the stage, reprograms the sensor, blinks and schedules a flash
    # save), so the GUI polls with read-only commands already used at
    # startup: 0x44 (active profile) + one 0x92 profile read. Only while the
    # window is focused, a mouse is connected and nothing else is talking to
    # it (Apply, full read, hotplug re-read); never writes.

    def _poll_wanted(self) -> bool:
        return (
            not getattr(self.app.args, "screenshot", None)
            and self.model.loaded
            and self.device_present
            and self.is_active()
            and not self.spinner.get_spinning()
            and not self._hotplug_refreshing
            and self.stack.get_visible_child_name() == "main"
        )

    def _poll_interval(self) -> int:
        if self._poll_failures >= POLL_FAILURES_BEFORE_BACKOFF:
            return POLL_BACKOFF_MS
        return POLL_INTERVAL_WIRELESS_MS if self.model.transport == "wireless" else POLL_INTERVAL_WIRED_MS

    def _schedule_poll(self, delay: int | None = None) -> None:
        """Arm the next poll if wanted and none is pending (idempotent)."""
        if self._poll_source or self._poll_inflight or not self._poll_wanted():
            return
        self._poll_source = GLib.timeout_add(self._poll_interval() if delay is None else delay, self._poll_tick)

    def _cancel_poll(self) -> None:
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _poll_tick(self) -> bool:
        self._poll_source = 0
        if self._poll_inflight or not self._poll_wanted():
            return False  # re-armed by focus / busy / populate events
        self._poll_inflight = True
        m = self.model
        fallback = m.onboard_active_profile if m.onboard_active_profile is not None else m.selected
        timeout = 0.6 if m.transport == "wireless" else 0.3
        run_in_thread(lambda: self.session.poll_active_stage(fallback, timeout=timeout), self._poll_done)
        return False

    def _poll_done(self, result, error) -> None:
        self._poll_inflight = False
        if error is not None:
            # Quiet on purpose: asleep (wireless), busy (liftctl / plugin) or
            # unplugged (hotplug handles it). Back off after a few failures.
            self._poll_failures += 1
        else:
            self._poll_failures = 0
            if result is not None and self.model.loaded:
                self._on_device_stage(result)
        self._schedule_poll()

    def _on_device_stage(self, result) -> None:
        m = self.model
        change = m.adopt_poll(result.active_profile, result.slot, result.stage)
        if not change:
            return
        if change.switched:
            self.populate()  # now editing the profile the mouse switched to
            return
        if change.active_changed:
            self._sync_onboard_active()
        if change.stage_changed and result.slot == m.selected:
            self._sync_stage_marks()
        self._sync_header()

    # --- hotplug ------------------------------------------------------------------------

    def _start_monitor(self) -> None:
        if self.session.is_mock or getattr(self.app.args, "screenshot", None):
            self.device_present = True  # mock is always "present"
            return
        if self._monitor is not None:
            return
        mon = Monitor(
            transport=self.session.transport,
            path=self.session.device_path,
        )
        self._monitor = mon
        mon.start(lambda prev, cur: GLib.idle_add(self._on_hotplug, prev, cur))

    def _stop_monitor(self) -> None:
        mon = self._monitor
        self._monitor = None
        if mon is not None:
            mon.stop()

    def _on_hotplug(self, previous: Presence, current: Presence) -> bool:
        # Called on the GTK thread via GLib.idle_add.
        if self.session.is_mock:
            return False
        prev_id, cur_id = previous.identity(), current.identity()
        if prev_id == cur_id:
            return False
        was, now = previous.connected, current.connected
        if was and not now:
            self.device_present = False
            self.session.close()  # drop the stale fd; never write
            if self.model.loaded:
                self.populate()
                self.toast(_("Mouse disconnected") + (
                    " — " + _("unsaved changes were kept") if self.model.any_dirty else ""
                ))
            else:
                self.stack.set_visible_child_name("empty")
            self._sync_header()
            return False
        # Plug-in, or switch between cable and receiver (cable wins in scan()).
        if now:
            if self._hotplug_refreshing:
                return False
            self._hotplug_refreshing = True
            kind = "connect" if not was else "switch"
            self.busy(True, _("Reading settings…"))

            def done(state, error, kind=kind):
                self._hotplug_refreshing = False
                self.busy(False)
                if error is not None:
                    self.device_present = False
                    self.toast(_("Read failed") + f": {error}")
                    self._sync_header()
                    return
                self.device_present = True
                kept = False
                if self.model.loaded:
                    kept = self.model.adopt_state(state)
                else:
                    self.model.load_state(state)
                self.stack.set_visible_child_name("main")
                self.populate()
                transport = transport_label(state.info.transport)
                if kind == "switch":
                    msg = _("Switched to {transport}", transport=transport)
                else:
                    msg = _("Mouse connected ({transport})", transport=transport)
                if kept:
                    msg += " — " + _("unsaved changes were kept")
                self.toast(msg)

            # Re-detect (prefer cable). Never write on connect.
            self.session.close()
            run_in_thread(self.session.read_state, done)
        return False

    def toast(self, text: str, timeout: int = 4) -> None:
        t = Adw.Toast(title=GLib.markup_escape_text(text))
        t.set_timeout(timeout)
        self.toasts.add_toast(t)

    def error_dialog(self, heading: str, body: str) -> None:
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("close", _("Close"))
        dlg.present(self)

    def _on_close_request(self, _win) -> bool:
        if self.model.loaded and self.model.any_dirty and not getattr(self, "_force_close", False):
            dlg = Adw.AlertDialog(heading=_("Unapplied changes"),
                                  body=_("Some changes were not written to the mouse. Quit anyway?"))
            dlg.add_response("cancel", _("Cancel"))
            dlg.add_response("quit", _("Quit"))
            dlg.set_response_appearance("quit", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_close_response("cancel")

            def on_response(_d, r):
                if r == "quit":
                    self._force_close = True
                    self.close()

            dlg.connect("response", on_response)
            dlg.present(self)
            return True
        self._cancel_poll()
        self._stop_monitor()
        self.session.close()
        return False


# --- application ---------------------------------------------------------------------------


class LiftEliteApp(Adw.Application):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE if args.mock or args.screenshot else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.args = args
        if getattr(args, "lang", None):
            i18n.set_language(args.lang)
        self.settings = load_settings()
        if args.theme is None:
            args.theme = self.settings.get("theme") if self.settings.get("theme") in ("default", "light", "dark") else "default"
        mock = "wireless" if args.mock_wireless else ("wired" if args.mock else None)
        self.session = Session(mock=mock, device_path=args.device, transport=args.transport)
        self.window: MainWindow | None = None
        self._shot_done = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(HERE / "style.css"))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = Gtk.IconTheme.get_for_display(display)
        for d in _data_dirs():
            theme.add_search_path(str(d))
        scheme = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(
            self.args.theme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        theme_action = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"),
                                                     GLib.Variant.new_string(self.args.theme))
        theme_action.connect("activate", self._on_theme)
        self.add_action(theme_action)
        pref = self.settings.get("language", "auto")
        lang_action = Gio.SimpleAction.new_stateful(
            "language", GLib.VariantType.new("s"),
            GLib.Variant.new_string(pref if pref in ("auto", "it", "en") else "auto"))
        lang_action.connect("activate", self._on_language)
        self.add_action(lang_action)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_a: self._about())
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_a: self.window and self.window.close())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_language(self, action, value) -> None:
        action.set_state(value)
        try:
            save_setting("language", value.get_string())
        except OSError:
            return
        if self.window is not None:
            self.window.toast(_("Language saved: restart Lift Elite Control to use it"))

    def _on_theme(self, action, value) -> None:
        action.set_state(value)
        try:
            save_setting("theme", value.get_string())
        except OSError:
            pass
        scheme = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(
            value.get_string(), Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def _about(self) -> None:
        about = Adw.AboutDialog(
            application_name=_("Lift Elite Control"),
            application_icon=APP_ID,
            version=__version__,
            comments=_("Unofficial configuration tool for the NZXT Lift Elite mouse. Not affiliated with NZXT."),
            license_type=Gtk.License.GPL_3_0,
            developer_name="Lift Elite Control",
        )
        about.present(self.window)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()
        if not self.window.model.loaded:
            self.window.refresh()

    # called by the window after the first successful read
    def on_loaded(self, win: MainWindow) -> None:
        if self.args.screenshot and not self._shot_done:
            self._shot_done = True
            if self.args.select_slot is not None:
                win.model.select(self.args.select_slot)
                win.populate()
            for edit in self.args.demo_edit or []:
                _apply_demo_edit(win, edit)
            if self.args.open_picker:
                GLib.timeout_add(300, lambda: (win.open_picker(self.args.open_picker), False)[1])
            GLib.timeout_add(self.args.screenshot_delay, lambda: (self._screenshot(win), False)[1])

    def _screenshot(self, win: MainWindow) -> None:
        w, h = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        renderer = win.get_native().get_renderer()
        rect = Graphene.Rect()
        rect.init(0, 0, w, h)
        texture = renderer.render_texture(node, rect)
        texture.save_to_png(self.args.screenshot)
        print(f"screenshot: {self.args.screenshot} ({w}x{h})")
        win._force_close = True
        self.quit()


def _apply_demo_edit(win: MainWindow, spec: str) -> None:
    """``--demo-edit`` for screenshots: polling=1000, dpi2=1000, stage=2, forward=volume-up."""
    from ..actions import parse_action

    key, value = spec.split("=", 1)
    m = win.model
    if key == "polling":
        m.set_polling(int(value))
    elif key.startswith("dpi"):
        m.set_dpi(int(key[3:]), int(value))
    elif key == "stage":
        m.set_active_stage(int(value))
    elif key in BUTTON_NAMES:
        m.set_button(key, parse_action(value))
    win.populate()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="lift-elite-control", description=_("Lift Elite Control"))
    p.add_argument("--mock", action="store_true", help=_("use an emulated mouse (demo)"))
    p.add_argument("--mock-wireless", action="store_true", help=_("emulated mouse through the receiver"))
    p.add_argument("--device", metavar="PATH", help=_("hidraw node to use (default: automatic detection)"))
    p.add_argument("--transport", choices=("auto", "wired", "wireless"), default="auto")
    p.add_argument("--theme", choices=("default", "light", "dark"), default=None,
                   help=_("colour scheme (default: the saved preference, else the system one)"))
    p.add_argument("--lang", choices=sorted(i18n.CATALOGS),
                   help=_("UI language (default: LIFT_ELITE_LANG, the saved preference, else the system locale)"))
    p.add_argument("--screenshot", metavar="PNG", help=argparse.SUPPRESS)
    p.add_argument("--screenshot-delay", type=int, default=900, help=argparse.SUPPRESS)
    p.add_argument("--open-picker", choices=BUTTON_NAMES, help=argparse.SUPPRESS)
    p.add_argument("--select-slot", type=int, choices=range(5), help=argparse.SUPPRESS)
    p.add_argument("--demo-edit", action="append", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.mock_wireless:
        args.mock = True
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.lang:
        i18n.set_language(args.lang)
    app = LiftEliteApp(args)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
