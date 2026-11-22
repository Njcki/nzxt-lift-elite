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
from ..store import ProfileStore  # noqa: E402
from . import i18n  # noqa: E402
from .diagram import W as DIAGRAM_W, H as DIAGRAM_H, draw as draw_mouse, widget_hit  # noqa: E402
from .i18n import _  # noqa: E402
from .model import (  # noqa: E402
    BUTTON_LABELS,
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

    def _draw(self, _area, cr, width, height) -> None:
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba() if hasattr(
            Adw.StyleManager.get_default(), "get_accent_color_rgba") else rgba("#3584e4")
        self._xf = draw_mouse(
            cr, width, height,
            led=self.led,
            selected=self.selected,
            hover=self.hover,
            changed=self.changed,
            fg=fg,
            accent=accent,
            underside_label=_("Underside"),
        )

    def _hit(self, x: float, y: float) -> str | None:
        return widget_hit(x, y, self._xf)

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
        view.add_top_bar(header)

        # pages
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.loading_page = Adw.StatusPage(title=_("Reading settings…"), icon_name=APP_ID)
        self.loading_page.set_description(_("Nothing is written on startup. Changes are sent only when you press Apply."))
        self.stack.add_named(self.loading_page, "loading")
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_main_page(), "main")
        view.set_content(self.stack)
        self.stack.set_visible_child_name("loading")

        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self.device_present = False
        self._monitor: Monitor | None = None
        self._hotplug_refreshing = False
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
        theme = Gio.Menu()
        theme.append(_("Follow system"), "app.theme::default")
        theme.append(_("Light"), "app.theme::light")
        theme.append(_("Dark"), "app.theme::dark")
        s3.append_submenu(_("Appearance"), theme)
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
        note = Gtk.Label(label=_("Nothing is written on startup. Changes are sent only when you press Apply."), wrap=True)
        note.add_css_class("footer-note")
        content.append(note)
        clamp.set_child(content)
        scroller.set_child(clamp)
        root.append(scroller)
        return root

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=20, margin_bottom=20, margin_start=18, margin_end=18)
        side.set_size_request(340, -1)
        side.add_css_class("sidebar-pane")
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
        # The long-press hint stays until 0x45 is confirmed. When
        # ENABLE_ACTIVE_PROFILE_APPLY is turned on, Apply will also send 0x45
        # for the selected slot (see confirm_apply / apply below).
        hint_text = (
            _("Scegli il profilo da modificare e da attivare sul mouse con Applica.")
            if ENABLE_ACTIVE_PROFILE_APPLY
            else _("Scegli il profilo da modificare. Per attivarlo sul mouse tieni premuto a lungo il tasto viola.")
        )
        self.slot_hint = Gtk.Label(label=hint_text, xalign=0, wrap=True)
        self.slot_hint.add_css_class("footer-note")
        slot_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        slot_box.append(switcher)
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

    def _build_performance_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=_("Performance"))
        self.polling_row = Adw.ComboRow(title=_("Polling rate"))
        self.polling_row.set_model(Gtk.StringList.new([_("{hz} Hz", hz=hz) for hz in POLLING_CHOICES]))
        self.polling_row.connect("notify::selected", self._on_polling)
        group.add(self.polling_row)
        self.liftoff_row = Adw.ComboRow(title=_("Lift-off distance"))
        self.liftoff_row.set_model(Gtk.StringList.new([_("{mm} mm", mm=mm) for mm in LIFTOFF_CHOICES]))
        self.liftoff_row.connect("notify::selected", self._on_liftoff)
        group.add(self.liftoff_row)
        self.motion_row = Adw.SwitchRow(title=_("Motion sync"),
                                        subtitle=_("Syncs sensor frames with USB reports (slightly smoother, tiny extra latency)"))
        self.motion_row.connect("notify::active", self._on_motion)
        group.add(self.motion_row)
        self.color_row = Adw.ActionRow(
            title=_("Profile LED colour"),
            subtitle=_("si vede per ~2 s quando cambi profilo tenendo premuto il tasto viola"),
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
        self.stage_rows: list[Adw.ActionRow] = []
        self.stage_checks: list[Gtk.CheckButton] = []
        self.stage_scales: list[Gtk.Scale] = []
        self.stage_spins: list[Gtk.SpinButton] = []
        for i in range(5):
            row = Adw.ActionRow(title=_("Stage {n}", n=i + 1))
            row.add_css_class("stage-row")
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text=_("Make this the active stage"))
            if self.stage_checks:
                check.set_group(self.stage_checks[0])
            check.connect("toggled", self._on_stage_toggled, i + 1)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, Gtk.Adjustment(lower=0, upper=SLIDER_MAX, step_increment=1, page_increment=50))
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_size_request(240, -1)
            scale.set_valign(Gtk.Align.CENTER)
            for mark in (400, 1600, 6400, 26000):
                scale.add_mark(dpi_to_slider(mark), Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self._on_scale, i + 1)
            spin = Gtk.SpinButton.new(Gtk.Adjustment(lower=DPI_VALUE_MIN, upper=DPI_VALUE_MAX,
                                                     step_increment=DPI_STEP, page_increment=500), DPI_STEP, 0)
            spin.set_numeric(True)
            spin.set_width_chars(6)
            spin.set_valign(Gtk.Align.CENTER)
            spin.add_css_class("dpi-spin")
            spin.connect("value-changed", self._on_spin, i + 1)
            row.add_suffix(scale)
            row.add_suffix(spin)
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
        self.apply_btn.set_sensitive(not on and self.model.any_dirty and self.device_present)
        if text:
            self.title.set_subtitle(text)

    def _sync_header(self) -> None:
        dirty = self.model.loaded and self.model.any_dirty
        self.apply_btn.set_sensitive(bool(dirty) and self.device_present and not self.spinner.get_spinning())
        self.discard_btn.set_visible(bool(dirty))
        if self.model.loaded:
            if not self.device_present and not self.session.is_mock:
                sub = _("Disconnected")
            else:
                sub = transport_label(self.model.transport)
                if self.session.is_mock:
                    sub = _("Demo (emulated mouse)") + " · " + sub
            if dirty:
                sub += " · " + _("modified")
            self.title.set_subtitle(sub)
        for i, mark in enumerate(self.slot_marks):
            mark.set_visible(self.model.loaded and self.model.is_dirty(i))

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
            onboard_active = m.onboard_active_profile if ENABLE_ACTIVE_PROFILE_APPLY else None
            for i, (btn, dot) in enumerate(zip(self.slot_buttons, self.slot_dots, strict=True)):
                btn.set_active(i == m.selected)
                if i in m.drafts:
                    dot.set_rgb(rgb565_to_rgb(m.drafts[i].color))
                # Prepared (disabled until 0x44/0x45 are confirmed): mark the
                # profile currently active on the mouse.
                if onboard_active is not None and i == onboard_active:
                    btn.add_css_class("onboard-active")
                    btn.set_tooltip_text(_("Profile {n} (active on the mouse)", n=i + 1))
                else:
                    btn.remove_css_class("onboard-active")
                    btn.set_tooltip_text(_("Profile {n}", n=i + 1))
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
                active = (i + 1) == p.stage
                self.stage_checks[i].set_active(active)
                row = self.stage_rows[i]
                (row.add_css_class if active else row.remove_css_class)("active-stage")
                changed = value != orig.dpi[i]
                row.set_subtitle(
                    (_("active") if active else "") + ((" · " if active else "") + f"{orig.dpi[i]} → {value}" if changed else "")
                )
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
        p, orig = self.model.current(), self.model.original()
        i = stage - 1
        active = stage == p.stage
        changed = p.dpi[i] != orig.dpi[i]
        self.stage_rows[i].set_subtitle(
            (_("active") if active else "") + ((" · " if active else "") + f"{orig.dpi[i]} → {p.dpi[i]}" if changed else "")
        )
        self._sync_header()

    def open_picker(self, button: str) -> None:
        if not self.model.loaded:
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
            self.toast(_("Factory buttons set — press Apply to write them"))

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
                                  body=_("Some changes were not written to the mouse. Quit anyway?").replace(
                                      _("Quit anyway?"), "").strip() or None)
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
        if not self.model.any_dirty and not (ENABLE_ACTIVE_PROFILE_APPLY and self.model.activation_pending):
            self.toast(_("No changes to apply"))
            return
        self.apply()

    def apply(self) -> None:
        targets = [self.model.drafts[i] for i in self.model.dirty_slots()]
        activate = self.model.selected if (ENABLE_ACTIVE_PROFILE_APPLY and self.model.activation_pending) else None
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
        dialog.set_initial_name("lift-elite-profili.json" if all_profiles else f"lift-elite-profilo-{self.model.selected + 1}.json")
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
        self._stop_monitor()
        self.session.close()
        return False


# --- application ---------------------------------------------------------------------------


class LiftEliteApp(Adw.Application):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE if args.mock or args.screenshot else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.args = args
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
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_a: self._about())
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_a: self.window and self.window.close())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_theme(self, action, value) -> None:
        action.set_state(value)
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
    p.add_argument("--mock", action="store_true", help="use an emulated mouse (demo)")
    p.add_argument("--mock-wireless", action="store_true", help="emulated mouse through the receiver")
    p.add_argument("--device", metavar="PATH", help="hidraw node (default: auto-detect)")
    p.add_argument("--transport", choices=("auto", "wired", "wireless"), default="auto")
    p.add_argument("--theme", choices=("default", "light", "dark"), default="default")
    p.add_argument("--lang", choices=sorted(i18n.CATALOGS), help="UI language (default: Italian)")
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
