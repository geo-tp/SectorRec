"""SectorRec's responsive, high-DPI desktop view.

Tk widgets render at native resolution. No enlarged Cardputer bitmap or
ImageTk is used. This is strictly an adapter: controller and both engines
remain independent of Tk; the Cardputer frontend is untouched.
"""
import os
import json
import threading
import subprocess
import sys
import shutil
import webbrowser
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from importlib.resources import files
from src import __version__ as APP_VERSION
from tkinter import filedialog, ttk

from src.models.files import human_size, EXTENSIONS
from src.services.device_diagnostics import report_lines, smart_attribute_lines, smart_operational_lines, detailed_smart
from src.views.scan_guidance import scan_tip
from src.views.elapsed import format_elapsed
from src.controllers.imaging import ImagingJob
from src.services.source_assessment import imaging_option_available as imaging_option_available

BACKGROUND = '#0C1321'
SURFACE = '#141F30'
SURFACE_2 = '#1B2A3F'
BORDER = '#304157'
FOREGROUND = '#F0F6FC'
MUTED = '#9EADC1'
TEAL = '#56DFC0'
TEAL_DARK = '#08312B'
BLUE = '#81B9FF'
AMBER = '#F4C47E'
DANGER = '#FF7E87'
# Disk-image rows have a subtle cool tint; SYSTEM DISK remains the only alarm color.
IMAGE_IDLE = '#252D44'
IMAGE_SELECTED = '#30465A'
IMAGE_BORDER = '#526588'
GITHUB_PAGES_URL = 'https://geo-tp.github.io/SectorRec/'
GITHUB_RELEASES_URL = 'https://github.com/geo-tp/SectorRec/releases'
GITHUB_REPO_URL = 'https://github.com/geo-tp/SectorRec'

FORMAT_NAMES = {
    'all': 'All supported formats',
    'photos': 'Photos — common image signatures',
    'video': 'Video — common video signatures',
    'documents': 'Documents — PDF and ZIP/Office families',
    'audio': 'Audio — common audio signatures',
    'archives': 'Archives — ZIP, 7z, RAR, GZIP',
}


class _HoverTip:
    """Delayed, lightweight Tk tooltip; only created while the mouse hovers.

    Tk has no native tooltips. A borderless Toplevel is used here rather than
    extra UI dependencies. Every timer/window is cancelled when its source
    widget is left, clicked, or destroyed during a page transition.
    """
    def __init__(self, widget, message, delay_ms=550):
        self.widget = widget
        self.message = message
        self.delay_ms = delay_ms
        self._pending = None
        self._popup = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def _schedule(self, _event=None):
        self.hide()
        try:
            if 'state' in self.widget.keys() and self.widget.cget('state') == 'disabled':
                return
            self._pending = self.widget.after(self.delay_ms, self._show)
        except tk.TclError:
            pass  # Source widget was destroyed between enter and timer setup.

    def _show(self):
        self._pending = None
        try:
            if not self.widget.winfo_exists():
                return
            popup = tk.Toplevel(self.widget)
            self._popup = popup
            popup.withdraw()
            popup.overrideredirect(True)
            popup.configure(bg='#547489', padx=1, pady=1)
            label = tk.Label(popup, text=self.message, justify='left',
                             wraplength=330, font=('DejaVu Sans', 10),
                             bg='#243448', fg=FOREGROUND, padx=12, pady=8)
            label.pack()
            popup.update_idletasks()
            # Keep the tooltip *outside* the clickable widget: otherwise the
            # popup may steal the pointer, trigger Leave, and flicker.
            x = self.widget.winfo_pointerx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 7
            if y + popup.winfo_reqheight() > self.widget.winfo_screenheight() - 5:
                y = self.widget.winfo_rooty() - popup.winfo_reqheight() - 7
            x = max(5, min(x, self.widget.winfo_screenwidth() - popup.winfo_reqwidth() - 5))
            y = max(5, y)
            popup.geometry(f'+{x}+{y}')
            popup.deiconify()
            popup.lift()
        except tk.TclError:
            self.hide()

    def hide(self, _event=None):
        if self._pending is not None:
            try: self.widget.after_cancel(self._pending)
            except tk.TclError: pass
            self._pending = None
        if self._popup is not None:
            try: self._popup.destroy()
            except tk.TclError: pass
            self._popup = None


class DesktopWindow:
    """Native UI for desktop only. Never used by the Cardputer Zero."""
    def __init__(self, controller, resume_device=None):
        self.ui = controller
        self.ui.show_devices()  # Desktop starts with the SOURCE, not engine.
        self.root = tk.Tk()
        self.root.title('SectorRec — File Recovery')
        # A little more space for the running catalog, navigation and tooltips.
        # Clamp to the actual desktop so small laptops still display the footer.
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        initial_w, initial_h = self.default_window_size(screen_w, screen_h)
        # Explicit placement avoids platform/window-manager-dependent startup
        # locations (notably a bottom-right initial position on Linux/X11).
        # Keep the original window size and all subsequent resize behavior.
        self.root.geometry(self.centered_window_geometry(initial_w, initial_h, screen_w, screen_h))
        # Native minimum is deliberately smaller than the comfortable default.
        # Only sub-compact windows receive reduced padding and typography.
        self.root.minsize(760, 540)
        self._compact = self.compact_required(initial_w, initial_h)
        self._compact_after_id = None
        self.root.configure(bg=BACKGROUND)
        self.root.option_add('*Font', ('DejaVu Sans', 11))
        self.root.option_add('*TCombobox*Listbox.background', SURFACE_2)
        self.root.option_add('*TCombobox*Listbox.foreground', FOREGROUND)
        self._setup_styles()
        self._closed = False
        self._resume_device = resume_device
        self._stage = None
        self._page = 0
        self._page_size = 150
        self._device_rows = []
        self._device_snapshot = ()
        self.dynamic = {}
        self._method_selection = None
        self._method_source_path = None
        self._fade_after_ids = []
        self._image_mount = None
        self._image_mount_busy = False
        self._live_sort = 'id'
        self._live_sort_desc = True
        self._partition_sort = None
        self._partition_sort_desc = False
        self._imaging_job = None
        self._resume_check = None
        # Windows destination-to-physical-disk lookup may require PowerShell.
        # Never interpret an unavailable quick lookup as a safe destination.
        self._destination_validation = None
        self._native_picker_busy = False

        self.top = tk.Frame(self.root, bg=BACKGROUND,
                            padx=12 if self._compact else 22,
                            pady=4 if self._compact else 3)
        # Keep the title's top inset on full-size desktops, but remove the
        # redundant empty space below the single-row navigation header.
        self.top.pack(fill='x', pady=(0, 0) if self._compact else (5, 0))
        # One shared navigation strip for *all* desktop sizes.  No separate
        # progress row can flash during the first paint or a resize.
        self._header_steps_strip = None
        self._brand()
        self.rule = tk.Frame(self.root, bg=BORDER, height=1)
        self.rule.pack(fill='x', pady=(3, 0) if self._compact else (2, 0))
        self.body = tk.Frame(self.root, bg=BACKGROUND,
                             padx=12 if self._compact else 22,
                             pady=5 if self._compact else 9)
        self.body.pack(fill='both', expand=True)
        self.root.bind('<KeyPress>', self.on_key)
        self.root.bind('<Configure>', self._on_window_configure, add='+')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.show(force=True)
        self._tick_after_id=self.root.after(120, self.tick)

    @staticmethod
    def default_window_size(screen_w, screen_h):
        """Same full-size default on every desktop architecture.

        The existing screen-margin clamping and reversible compact UI remain
        available to users who resize the window or have a small display.
        """
        return min(1210, max(760, screen_w - 65)), min(865, max(540, screen_h - 85))

    @staticmethod
    def centered_window_geometry(width, height, screen_width, screen_height):
        """Center the initial desktop window inside the available screen."""
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        return f'{width}x{height}+{left}+{top}'

    @staticmethod
    def compact_required(width, height):
        """A small desktop window needs a short brand bar and denser widgets.

        The resize callback is debounced: it never regenerates the active
        recovery page for every pixel change.
        """
        return width <= 900 or height <= 700

    def _on_window_configure(self, event):
        if event.widget is not self.root or self._closed:
            return
        # Avoid rebuilding the header during each step of a resize drag.
        requested = self.compact_required(event.width, event.height)
        if requested == self._compact:
            return
        if self._compact_after_id is not None:
            self.root.after_cancel(self._compact_after_id)
        self._compact_after_id = self.root.after(120, self._refresh_compact_mode)

    def _refresh_compact_mode(self):
        self._compact_after_id = None
        if self._closed:
            return
        wanted = self.compact_required(self.root.winfo_width(), self.root.winfo_height())
        if wanted == self._compact:
            return
        self._compact = wanted
        for widget in self.top.winfo_children():
            widget.destroy()
        self._header_steps_strip = None
        self.top.configure(padx=12 if wanted else 22, pady=4 if wanted else 3)
        self.top.pack_configure(pady=(0, 0) if wanted else (5, 0))
        self.body.configure(padx=12 if wanted else 22, pady=5 if wanted else 9)
        self.rule.pack_configure(pady=(3, 0) if wanted else (2, 0))
        self._brand()
        self.steps()
        self._setup_styles()
        # Do not recreate the page: doing so would lose scan callbacks, scroll
        # positions and active worker controls. Only modify visual properties.
        self._apply_density(self.body)

    @staticmethod
    def _scaled_spacing(value, factor):
        values = value if isinstance(value, (tuple, list)) else str(value).split()
        if not values:
            return value
        try:
            numbers = [max(0, int(round(float(number) * factor))) for number in values]
        except ValueError:
            return value
        return numbers[0] if len(numbers) == 1 else tuple(numbers)

    def _apply_density(self, parent):
        """Reversible visual compacting without changing the page hierarchy."""
        factor = 0.88 if self._compact else 1.0
        def visit(widget):
            if not widget.winfo_exists():
                return
            base = getattr(widget, '_sector_base_density', None)
            if base is None:
                base = {}
                for option in ('padx', 'pady', 'wraplength'):
                    try:
                        value = widget.cget(option)
                        if str(value) not in ('', '0'):
                            base[option] = value
                    except tk.TclError:
                        pass
                try:
                    f = tkfont.Font(root=self.root, font=widget.cget('font'))
                    family, size, weight, slant = (f.actual(key) for key in ('family', 'size', 'weight', 'slant'))
                    if size > 0:
                        base['font'] = (family, size, weight, slant)
                except (tk.TclError, ValueError, TypeError):
                    pass
                try:
                    if widget.winfo_manager() == 'pack':
                        pack = widget.pack_info()
                        base['pack'] = {key: pack[key] for key in ('padx','pady')
                                        if key in pack and str(pack[key]) not in ('', '0')}
                except tk.TclError:
                    pass
                widget._sector_base_density = base
            options = {}
            for key in ('padx', 'pady', 'wraplength'):
                if key in base:
                    options[key] = self._scaled_spacing(base[key], factor)
            if 'font' in base:
                family, size, weight, slant = base['font']
                options['font'] = (family, max(8, round(size * factor)), weight, slant)
            if options:
                try: widget.configure(**options)
                except tk.TclError: pass
            if base.get('pack'):
                try: widget.pack_configure(**{k:self._scaled_spacing(v, factor)
                                             for k,v in base['pack'].items()})
                except tk.TclError: pass
            for child in widget.winfo_children():
                visit(child)
        visit(parent)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Sector.Treeview', font=('DejaVu Sans', 9 if self._compact else 10), rowheight=29 if self._compact else 35,
                        background=SURFACE, fieldbackground=SURFACE,
                        foreground=FOREGROUND, borderwidth=0, relief='flat',
                        bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                        focuscolor=SURFACE)
        # Clam's default Treeview.field draws a light rectangle on all 4 sides,
        # even with borderwidth=0. Remove that *element*, not just its color.
        # The tree-area and heading still render and scroll normally.
        style.layout('Sector.Treeview', [
            ('Treeview.padding', {'sticky':'nswe', 'children': [
                ('Treeview.treearea', {'sticky':'nswe'})]})])
        style.configure('Sector.Treeview.Heading', background=SURFACE_2, foreground=MUTED,
                        font=('DejaVu Sans', 9 if self._compact else 10, 'bold'), padding=(9, 7) if self._compact else (13, 11), relief='flat')
        # The default clam active-heading style flashes white on hover.
        style.map('Sector.Treeview.Heading',
                  background=[('active', SURFACE_2), ('pressed', SURFACE_2)],
                  foreground=[('active', MUTED), ('pressed', MUTED)])
        style.map('Sector.Treeview', background=[('selected', '#285D62')],
                  foreground=[('selected', '#FFFFFF')])
        # Clam's beveled field element draws a pale white outline in readonly
        # comboboxes. Use a flat arrow + text area and match the app palette.
        style.layout('Sector.TCombobox', [
            ('Combobox.downarrow', {'side':'right', 'sticky':'ns'}),
            ('Combobox.padding', {'sticky':'nswe', 'children': [
                ('Combobox.textarea', {'sticky':'nswe'})]})])
        style.configure('Sector.TCombobox', padding=(7,5) if self._compact else (10,7), relief='flat', borderwidth=0,
                        fieldbackground=SURFACE_2, background=SURFACE_2,
                        foreground=FOREGROUND, arrowcolor=TEAL, selectbackground=SURFACE_2,
                        selectforeground=FOREGROUND, lightcolor=SURFACE_2, darkcolor=SURFACE_2,
                        bordercolor=SURFACE_2, focuscolor=SURFACE_2)
        style.map('Sector.TCombobox', foreground=[('readonly', FOREGROUND)],
                  fieldbackground=[('readonly', SURFACE_2)],
                  background=[('readonly', SURFACE_2)],
                  bordercolor=[('focus', SURFACE_2)], lightcolor=[('focus', SURFACE_2)])
        style.configure('Sector.Vertical.TScrollbar', background='#426079',
                        troughcolor=SURFACE, bordercolor=SURFACE, darkcolor='#426079',
                        lightcolor='#426079', arrowcolor=TEAL, arrowsize=10 if self._compact else 12, width=10 if self._compact else 12,
                        gripcount=0)
        style.map('Sector.Vertical.TScrollbar', background=[('active', '#62849E'), ('pressed', TEAL)],
                  arrowcolor=[('active', FOREGROUND)])
        style.configure('Sector.Horizontal.TProgressbar', troughcolor=SURFACE_2,
                        background=TEAL, bordercolor=SURFACE_2, lightcolor=TEAL,
                        darkcolor=TEAL, thickness=11)

    def label(self, parent, text, size=11, color=FOREGROUND, bold=False,
              wrap=None, **kwargs):
        widget = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                          font=('DejaVu Sans', size, 'bold' if bold else 'normal'),
                          wraplength=wrap, justify='left', anchor='w', **kwargs)
        # Tk Labels don't support text selection. Offer a consistent Copy text
        # context menu for any descriptive label, without stealing left clicks.
        def copy_text(event):
            menu=tk.Menu(widget, tearoff=0, bg=SURFACE_2, fg=FOREGROUND)
            def copy():
                widget.clipboard_clear()
                widget.clipboard_append(widget.cget('text'))
            menu.add_command(label='Copy text', command=copy)
            try:menu.tk_popup(event.x_root,event.y_root)
            finally:menu.grab_release()
        widget.bind('<Button-3>', copy_text, add='+')
        return widget

    def _brand(self):
        compact = getattr(self, '_compact', False)
        row = tk.Frame(self.top, bg=BACKGROUND)
        row.pack(fill='x')
        # One permanent three-column layout at both sizes:
        # brand/title | centered short step tracker | GitHub aligned right.
        # The steps are never packed into a separate second row.
        row.grid_columnconfigure(1, weight=1)
        brand_parent = tk.Frame(row, bg=BACKGROUND)
        brand_parent.grid(row=0, column=0, sticky='w')
        # Keep the on-page HDD/recovery logo separate from the OS window icon.
        # On some Linux window managers iconphoto() can fail even when the PNG
        # loaded correctly. It must never replace the brand with a magnifier.
        assets = files('src').joinpath('assets')
        self._brand_image = None
        brand_widget = None
        for name in ('sectorrec_desktop_mark.png', 'sectorrec_desktop_icon.png'):
            try:
                image = tk.PhotoImage(file=str(assets.joinpath(name)))
                if name == 'sectorrec_desktop_icon.png':
                    image = image.subsample(8 if compact else 4, 8 if compact else 4)
                if compact and name == 'sectorrec_desktop_mark.png':
                    image = image.subsample(2, 2)
                self._brand_image = image
                brand_widget = tk.Label(brand_parent, image=image, bg=BACKGROUND, bd=0,
                                        highlightthickness=0)
                brand_widget.pack(side='left', padx=(0, 5 if compact else 10))
                break
            except (tk.TclError, OSError):
                continue
        if self._brand_image is None:
            # If neither PNG can be read, retain a recognizable disk + recover
            # symbol rather than changing SectorRec's identity to a magnifier.
            mark = tk.Canvas(brand_parent, width=32 if compact else 64,
                             height=32 if compact else 64, bg=BACKGROUND,
                             highlightthickness=0, bd=0)
            mark.pack(side='left', padx=(0, 5 if compact else 10))
            brand_widget = mark
            mark.create_rectangle(10, 8, 52, 53, fill=SURFACE_2,
                                  outline=BLUE, width=2)
            mark.create_oval(18, 15, 43, 40, outline=FOREGROUND, width=2)
            mark.create_oval(27, 24, 34, 31, fill=FOREGROUND, outline='')
            mark.create_arc(31, 31, 59, 59, start=135, extent=245,
                            style=tk.ARC, outline=TEAL, width=4)
            mark.create_polygon(53, 34, 61, 39, 53, 45,
                                fill=TEAL, outline=TEAL)
            if compact:
                mark.scale('all', 0, 0, 0.5, 0.5)
        # Window-manager icon failure is independent of the logo above.
        try:
            self._window_icon = tk.PhotoImage(
                file=str(assets.joinpath('sectorrec_desktop_icon.png')))
            self.root.iconphoto(True, self._window_icon)
        except (tk.TclError, OSError):
            pass
        text = tk.Frame(brand_parent, bg=BACKGROUND)
        text.pack(side='left')
        try:
            from tkinter import font as tkfont
            families=set(tkfont.families(self.root))
            brand_family=next((family for family in ('Oxanium','Rajdhani','Exo 2','Orbitron','Cabin','DejaVu Sans') if family in families),'DejaVu Sans')
        except (tk.TclError, RuntimeError):
            brand_family='DejaVu Sans'
        title_row = tk.Frame(text, bg=BACKGROUND)
        title_row.pack(anchor='w')
        brand_title = tk.Label(title_row, text='SectorRec', bg=BACKGROUND, fg=FOREGROUND,
                               font=(brand_family, 17 if compact else 24, 'bold'), anchor='w')
        brand_title.pack(side='left')
        for widget in (brand_widget, brand_title):
            widget.configure(cursor='hand2')
            widget.bind('<Button-1>', lambda _e: webbrowser.open(GITHUB_PAGES_URL))
            self.hover_tip(widget, 'Visit the SectorRec project website (GitHub Pages).')
        # The version badge opens this project's GitHub releases.
        # Use the package version so the badge stays accurate in every build.
        badge_border = tk.Frame(title_row, bg='#326C68', padx=1, pady=1)
        if not compact:
            badge_border.pack(side='left', padx=(11, 0))
        version_badge = tk.Label(badge_border, text=f'v{APP_VERSION}', bg='#193A40', fg=TEAL,
                                 font=('DejaVu Sans', 9, 'bold'), padx=7, pady=3,
                                 bd=0, cursor='hand2')
        version_badge.pack()
        for widget in (version_badge, badge_border):
            widget.configure(cursor='hand2')
            widget.bind('<Button-1>', lambda _e: webbrowser.open(GITHUB_RELEASES_URL))
            self.hover_tip(widget, 'See SectorRec releases on GitHub.')
        # Removing the old subtitle saves vertical space at every width.
        self._header_steps_strip = tk.Frame(row, bg=BACKGROUND, name='headersteps')
        self._header_steps_strip.grid(row=0, column=1, padx=(12, 12))
        # A compact outlined link, in the same palette as the version badge.
        # The entire frame is clickable, not just the text.
        self.github_border = tk.Frame(row, bg='#326C68', padx=1, pady=1,
                                      cursor='hand2')
        self.github_border.grid(row=0, column=2, sticky='e', padx=(8, 2))
        self.github_link = self.label(self.github_border, 'GitHub',
                                      9 if compact else 10, TEAL, True,
                                      cursor='hand2')
        self.github_link.configure(bg='#193A40', padx=8 if compact else 9,
                                   pady=4 if compact else 5)
        self.github_link.pack()
        for widget in (self.github_border, self.github_link):
            widget.bind('<Button-1>', lambda _event: webbrowser.open(GITHUB_REPO_URL))

    def steps(self):
        # The same short labels occupy the center column on every screen.
        target = self._header_steps_strip
        for child in target.winfo_children(): child.destroy()
        stage = self.ui.stage
        step = 0 if stage == 'devices' else 1 if stage == 'desktop_methods' else (
            2 if stage in ('scan_ready', 'signature_setup','partition_ready','desktop_image_setup') else 3)
        names = ('1 Source', '2 Method', '3 Configure', '4 Review')
        for i, name in enumerate(names):
            block = tk.Frame(target, bg=BACKGROUND)
            block.grid(row=0, column=i, sticky='w', padx=(0, 7 if i < 3 else 0))
            self.label(block, name, 9 if self._compact else 10, TEAL if step == i else
                       FOREGROUND if i < step else MUTED, i == step).pack(anchor='w')
            indicator = tk.Frame(block, bg=TEAL if step == i else
                                 BORDER if i > step else '#276258',
                                 height=2, width=70)
            indicator.pack(anchor='w', pady=(2, 0) if self._compact else (3, 0))

    def clear(self, title, description):
        # Stop the old indeterminate search timer before destroying its widget.
        previous_bar = self.dynamic.get('partition')
        if previous_bar is not None:
            try: previous_bar.stop()
            except tk.TclError: pass
        for child in self.body.winfo_children(): child.destroy()
        self.dynamic.clear()
        # Reserve space for primary action before expandable page content.
        self.footer = tk.Frame(self.body, bg=BACKGROUND)
        self.footer.pack(side='bottom', fill='x', pady=(7, 15))
        head = tk.Frame(self.body, bg=BACKGROUND)
        head.pack(fill='x', pady=(0, 8))
        self.label(head, title, 18, FOREGROUND, True).pack(anchor='w')
        self.label(head, description, 10, MUTED, wrap=900).pack(anchor='w', pady=(3, 0))
        return head

    def card(self, parent=None, padx=23, pady=20):
        target = parent or self.body
        outside = tk.Frame(target, bg=BORDER, padx=1, pady=1)
        inside = tk.Frame(outside, bg=SURFACE, padx=padx, pady=pady)
        inside.pack(fill='both', expand=True)
        return outside, inside

    @staticmethod
    def hover_tip(widget, message, delay_ms=550):
        # Retain the controller for the widget's lifetime. Its Destroy binding
        # also stops pending timers when Tk recreates a page.
        widget._sector_hover_tip = _HoverTip(widget, message, delay_ms)
        return widget

    def button(self, parent, text, command, primary=False, enabled=True, width=None,
               secondary=False, emphasis=False, tooltip=None):
        background = ('#24464A' if not enabled and primary else TEAL if primary else
                      '#304D65' if emphasis else '#26384F' if secondary else SURFACE_2)
        active = ('#7EEBD2' if primary else '#416780' if emphasis else
                  '#39546D' if secondary else '#34465E')
        fg = TEAL_DARK if primary else '#D6ECFF' if emphasis else BLUE if secondary else FOREGROUND
        btn = tk.Button(parent, text=text, command=command,
                        state='normal' if enabled else 'disabled',
                        font=('DejaVu Sans', 10, 'bold'),
                        bg=background, fg=fg, activebackground=active,
                        activeforeground=fg, disabledforeground=MUTED,
                        relief='solid' if emphasis else 'flat', bd=1 if emphasis else 0,
                        padx=14, pady=7, cursor='hand2' if enabled else 'arrow',
                        highlightthickness=0, width=width)
        if tooltip:
            self.hover_tip(btn, tooltip)
        return btn

    @staticmethod
    def set_button_enabled(button, enabled):
        """Set BOTH visual appearance and Tk state on an existing button.

        Tk never re-applies constructor colors after changing ``state`` alone.
        A button constructed disabled thus looked grey even while clickable.
        """
        button.configure(
            state='normal' if enabled else 'disabled',
            bg=TEAL if enabled else '#24464A',
            fg=TEAL_DARK if enabled else MUTED,
            activebackground='#7EEBD2' if enabled else '#24464A',
            activeforeground=TEAL_DARK if enabled else MUTED,
            cursor='hand2' if enabled else 'arrow',
        )

    def bottom(self, back=None, primary=None, enabled=True):
        row = self.footer
        for child in row.winfo_children(): child.destroy()
        if back:
            self.button(row, back[0], back[1]).pack(side='left')
        if primary:
            self.button(row, primary[0], primary[1], primary=True,
                        enabled=enabled).pack(side='right')
        return row

    def selected_source(self, parent, item=None, compact=False):
        item = item or self.ui.device or {}
        shell, inner = self.card(parent, 20, 10 if compact else 20)
        shell.pack(fill='x', pady=(0, 13 if compact else 17))
        self.label(inner, 'SELECTED SOURCE', 9, TEAL, True).pack(anchor='w', pady=(0, 3 if compact else 9))
        self.label(inner, item.get('label', 'Unknown source'), 13 if compact else 16, FOREGROUND, True).pack(anchor='w')
        details = f"{human_size(item.get('size', 0))}   ·   {item.get('fs', '?')}   ·   {item.get('transport', 'Drive')}"
        self.label(inner, details, 10, MUTED).pack(anchor='w', pady=(3 if compact else 6, 0))
        if not compact: self.label(inner, item.get('path', ''), 9, MUTED).pack(anchor='w', pady=(5, 0))

    def dispatch(self, action):
        if action=='BACK' and self.ui.stage in ('scanning','results') and self.ui.engine!='signature':
            if self.ui.stage=='scanning':
                title='Stop metadata scan?'
                question=('Stop scanning now? SectorRec will keep the files indexed so far. '
                          'The catalog will be partial, but you can review the results '
                          'and recover files once the scan has stopped.')
            else:
                title='Leave scan results?'
                question=('Return to the previous screen? Recovered files already saved '
                          'to the destination will not be deleted.')
            if not self.confirm_action(title, question, 'Stop scan' if self.ui.stage=='scanning' else 'Leave results'):
                return
        # A single existing partition found in an image did not use TestDisk;
        # don't send Back to the TestDisk-style partition results screen.
        if (action=='BACK' and self.ui.stage=='results' and self.ui.engine=='partition'
                and self.ui.partition_status.get('source')=='image_metadata'
                and len(self.ui.partition_status.get('candidates',[]))==1):
            self.ui.stage='desktop_methods'
            self.show(force=True)
            return
        # On the desktop, TestDisk's setup and partition results are reached
        # from Choose method.  Cached candidates must not redirect Back to the
        # very results page the user is trying to leave. The on-device
        # framebuffer UI deliberately retains its original navigation.
        if action=='BACK' and self.ui.desktop and self.ui.stage in ('partition_ready','partition_results'):
            self.return_to_method_selection()
            return
        if action=='BACK' and self.ui.desktop and self.ui.stage=='scan_ready':
            self.back_to_methods()
            return
        # Only the PC exposes live system volumes. Never enter the methods
        # screen without explicitly acknowledging the live-disk limitation.
        if (action == 'OK' and self.ui.stage == 'devices' and self.ui.desktop
                and self.ui.device and self.ui.device.get('is_system')):
            if not self.confirm_action('Active system disk',
                    'This disk is in use. Files deleted here may already be overwritten, '
                    'and live scans may be incomplete. Save recovered files and the scan '
                    'index on a DIFFERENT physical disk. For best results, use another PC '
                    'or a bootable USB instead.', 'Continue read-only'):
                return
        old_stage=self.ui.stage
        if old_stage != 'exit':self.ui.handle(action)
        # Demo scans finish synchronously; avoid rendering a transient scan
        # screen until the 125 ms UI timer fires (also stabilizes GUI tests).
        if getattr(self.ui,'demo',False) and self.ui.stage=='scanning' and self.ui.status.get('state')=='done':
            self.ui.tick()
        if old_stage=='devices' and self.ui.stage=='devices' and action in ('UP','DOWN','REFRESH'):
            self._paint_device_selection()
            return
        self.show(force=True)

    def confirm_action(self, title, question, confirm_label='Continue'):
        """Small in-theme confirmation; no extra native messagebox window."""
        dialog=tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=BACKGROUND)
        dialog.resizable(False,False)
        dialog.transient(self.root)
        result={'confirmed':False}
        shell=tk.Frame(dialog,bg=BORDER,padx=1,pady=1)
        shell.pack(fill='both',expand=True,padx=16,pady=16)
        contents=tk.Frame(shell,bg=SURFACE,padx=22,pady=19)
        contents.pack(fill='both',expand=True)
        self.label(contents,title,15,AMBER,True).pack(anchor='w',pady=(0,11))
        self.label(contents,question,11,FOREGROUND,wrap=425).pack(anchor='w')
        controls=tk.Frame(contents,bg=SURFACE)
        controls.pack(fill='x',pady=(24,0))
        def finish(confirmed=False):
            result['confirmed']=confirmed
            if dialog.winfo_exists():dialog.destroy()
        self.button(controls,'Cancel',lambda:finish()).pack(side='right',padx=(10,0))
        self.button(controls,confirm_label,lambda:finish(True),primary=True).pack(side='right')
        dialog.bind('<Escape>',lambda _e:finish())
        dialog.protocol('WM_DELETE_WINDOW',finish)
        dialog.update_idletasks()
        width,height=490, max(208,contents.winfo_reqheight()+34)
        x=self.root.winfo_rootx()+max(0,(self.root.winfo_width()-width)//2)
        y=self.root.winfo_rooty()+max(0,(self.root.winfo_height()-height)//2)
        dialog.geometry(f'{width}x{height}+{x}+{y}')
        dialog.deiconify()
        dialog.grab_set()
        dialog.focus_set()
        self.root.wait_window(dialog)
        return result['confirmed']

    @staticmethod
    def _is_same_disk_error(error):
        detail = str(error).lower()
        return any(word in detail for word in (
            'source physical disk', 'destination is on the source disk',
            'same physical disk', 'same underlying disk'))

    @staticmethod
    def _explain_destination_error(error):
        """Use an actionable warning for a destination on the source disk."""
        detail = str(error)
        same_disk = DesktopWindow._is_same_disk_error(detail)
        if same_disk:
            return ('SAME PHYSICAL DISK: This folder is on the source disk.\n'
                    'Choose a different physical disk (USB drive, external SSD). '
                    'Choosing another partition can overwrite recoverable files.')
        if 'cannot establish the source disk' in detail.lower():
            return ('SectorRec cannot identify the selected USB/SD source disk. '
                    'The destination folder may be valid; this is a source-device '
                    'identification failure. Check that the source is still connected.')
        if any(marker in detail.lower() for marker in (
                'physical disk cannot be verified', 'destination filesystem',
                'disk identity is inconsistent')):
            return ('SectorRec cannot confirm which physical disk contains this folder. '
                    'Try a mounted USB drive or another local disk. '
                    'Recovery stays blocked until the disks can be identified safely.')
        return detail

    def show_modal_notice(self, title, message, *, warning=False):
        """Display a themed, single-action notice instead of a native Tk messagebox.

        Use the same palette, border and button style as confirm_action().
        Long UDisks2 diagnostics and mount-point lists remain readable: the
        text is selectable and scrolls instead of stretching off-screen.
        """
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=BACKGROUND)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        shell = tk.Frame(dialog, bg=BORDER, padx=1, pady=1)
        shell.pack(fill='both', expand=True, padx=16, pady=16)
        contents = tk.Frame(shell, bg=SURFACE, padx=22, pady=19)
        contents.pack(fill='both', expand=True)
        self.label(contents, title, 15, AMBER if warning else TEAL, True).pack(
            anchor='w', pady=(0, 11))

        # A read-only Text widget offers selection/copy (useful for failed
        # mount diagnostics) and an optional scrollbar for long mount lists.
        lines = str(message).splitlines() or ['']
        approx_lines = sum(max(1, (len(line) + 65) // 66) for line in lines)
        max_lines = max(5, min(13, (self.root.winfo_screenheight() - 230) // 20))
        body = tk.Frame(contents, bg=SURFACE)
        body.pack(fill='both', expand=True)
        content = tk.Text(body, width=56, height=min(max_lines, max(3, approx_lines)),
                          font=('DejaVu Sans', 11), bg=SURFACE, fg=FOREGROUND,
                          insertbackground=FOREGROUND, selectbackground='#285D62',
                          selectforeground='#FFFFFF', wrap='word', relief='flat',
                          borderwidth=0, highlightthickness=0, padx=0, pady=0,
                          cursor='xterm', takefocus=1)
        content.insert('1.0', str(message))
        content.configure(state='disabled')
        content.pack(side='left', fill='both', expand=True)
        if approx_lines > max_lines:
            scrollbar = ttk.Scrollbar(body, style='Sector.Vertical.TScrollbar',
                                      orient='vertical', command=content.yview)
            scrollbar.pack(side='right', fill='y')
            content.configure(yscrollcommand=scrollbar.set)
        controls = tk.Frame(contents, bg=SURFACE)
        controls.pack(fill='x', pady=(21, 0))

        def close_notice(_event=None):
            if dialog.winfo_exists():
                dialog.destroy()
            return 'break'

        ok_button = self.button(controls, 'OK', close_notice, primary=True)
        ok_button.pack(side='right')
        dialog.bind('<Escape>', close_notice)
        dialog.bind('<Return>', close_notice)
        dialog.protocol('WM_DELETE_WINDOW', close_notice)
        dialog.update_idletasks()
        width = 526
        height = min(self.root.winfo_screenheight() - 30,
                     max(215, contents.winfo_reqheight() + 34))
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f'{width}x{height}+{x}+{y}')
        dialog.deiconify()
        dialog.grab_set()
        ok_button.focus_set()
        self.root.wait_window(dialog)

    def return_to_source_selection(self):
        """Reset controller navigation before rendering the device picker."""
        self.ui.show_devices()
        self.show(force=True)

    def show_sources(self):
        self._method_selection = None
        self._method_source_path = None
        self._image_source = None
        self.clear('Select a device',
                   'Select a volume to find deleted files or a whole disk to locate missing partitions.')
        toolbar = tk.Frame(self.body, bg=BACKGROUND)
        toolbar.pack(fill='x', pady=(0, 9))
        self.label(toolbar, 'AVAILABLE SOURCES', 10, MUTED, True).pack(side='left')
        self.button(toolbar, 'Open disk image…', self.open_image,
                    tooltip='Select an existing disk image to inspect or recover files without accessing the original device.').pack(side='right', padx=(9, 0))
        panel, interior = self.card(self.body, 11, 7)
        panel.pack(fill='both', expand=True)
        self.device_canvas = tk.Canvas(interior, bg=SURFACE, highlightthickness=0)
        scroller = ttk.Scrollbar(interior, style='Sector.Vertical.TScrollbar', orient='vertical', command=self.device_canvas.yview)
        scroller.pack(side='right', fill='y')
        self.device_canvas.pack(side='left', fill='both', expand=True)
        self.device_canvas.configure(yscrollcommand=scroller.set)
        self.device_list = tk.Frame(self.device_canvas, bg=SURFACE)
        interior_id = self.device_canvas.create_window((0,0), window=self.device_list, anchor='nw')
        self.device_list.bind('<Configure>',lambda e:self.device_canvas.configure(scrollregion=self.device_canvas.bbox('all')))
        self.device_canvas.bind('<Configure>',lambda e:self.device_canvas.itemconfig(interior_id,width=e.width))
        def wheel(event):
            if event.num==4:step=-1
            elif event.num==5:step=1
            else:step=(-1 if event.delta>0 else 1)*max(1,abs(event.delta)//120)
            self.device_canvas.yview_scroll(step,'units')
            return 'break'
        self._device_wheel=wheel
        for seq in ('<MouseWheel>','<Button-4>','<Button-5>'):
            self.device_canvas.bind(seq,wheel)
            self.device_list.bind(seq,wheel)
        self._render_devices()
        self.bottom(primary=('Continue  →', lambda:self.dispatch('OK')), enabled=self.ui.device is not None)
        self.source_continue=self.footer.winfo_children()[-1]

    @staticmethod
    def _device_row_palette(dev, selected):
        """System is high-visibility; image files get only a quiet distinction."""
        if dev.get('is_system'):
            return ('#4B382D' if selected else '#332B2B', AMBER)
        if dev.get('is_image'):
            return (IMAGE_SELECTED if selected else IMAGE_IDLE, TEAL if selected else IMAGE_BORDER)
        return ('#23443F' if selected else SURFACE_2, TEAL if selected else BORDER)

    @staticmethod
    def device_snapshot(devices):
        """Only physical changes should rebuild the list, not selection changes."""
        return tuple((d.get('path'),d.get('label'),d.get('size'),d.get('fs'),
                      d.get('transport'),bool(d.get('mounted')),
                      bool(d.get('supported',True)),tuple(d.get('child_filesystems',())),
                      bool(d.get('is_system')), bool(d.get('is_image')),
                      bool(d.get('partition_disk'))) for d in devices)

    def _paint_device_selection(self):
        """Update existing widgets in place; avoid a black-frame page recreation."""
        for index, (item, caption) in enumerate(self._device_rows):
            active = index == self.ui.selection
            dev = self.ui.devices[index]
            system = bool(dev.get('is_system'))
            color, border = self._device_row_palette(dev, active)
            item.configure(highlightbackground=border)
            for child in self._device_backgrounds(item):
                # Tk labels do NOT inherit frame backgrounds after creation.
                # Recolor both frame and labels to avoid the mismatched text boxes.
                if isinstance(child,tk.Label) and getattr(child,'_sector_badge',False):
                    continue
                child.configure(bg=color)
            caption.configure(text='✓  Selected' if active else 'Select  →',
                              fg=AMBER if system else TEAL if active else BLUE)
        if getattr(self, 'source_continue', None) is not None:
            self.set_button_enabled(self.source_continue, self.ui.device is not None)

    @staticmethod
    def _device_backgrounds(parent):
        # Tk does not propagate mouse events to parent Frames. We bind every
        # descendant separately, and recolor only the frames on selection.
        widgets = [parent]
        for child in parent.winfo_children():
            if isinstance(child, tk.Frame):
                widgets.extend(DesktopWindow._device_backgrounds(child))
            elif isinstance(child, (tk.Label, tk.Canvas)):
                widgets.append(child)
        return widgets

    def select_source(self, index, advance=False):
        if not 0 <= index < len(self.ui.devices):
            return
        self.ui.selection=index
        self._paint_device_selection()
        if advance:
            self.dispatch('OK')

    def _render_devices(self):
        if not hasattr(self,'device_list') or not self.device_list.winfo_exists():
            return
        scroll_y=self.device_canvas.yview()[0]
        for child in self.device_list.winfo_children():child.destroy()
        self._device_rows=[]
        self._device_snapshot=self.device_snapshot(self.ui.devices)
        self._device_refresh_rendered=self.ui.device_refresh_completed
        if not self.ui.devices:
            first_scan = not self.ui.device_refresh_completed
            failed = bool(self.ui.device_refresh_error)
            title = ('Scanning connected devices…' if first_scan else
                     'Device scan unavailable' if failed else 'No external devices found')
            detail = ('Checking connected storage. This may take a few seconds.' if first_scan else
                      'Could not refresh devices. Try again with F5.' if failed else
                      'Connect a drive or open a disk image. New devices are detected automatically.')
            self.label(self.device_list, title, 16, FOREGROUND, True).pack(anchor='w', pady=(28, 9), padx=10)
            self.label(self.device_list, detail, 11, MUTED, wrap=610).pack(anchor='w', padx=10)
            return
        for index,dev in enumerate(self.ui.devices):
            selected = index == self.ui.selection
            system = bool(dev.get('is_system'))
            color, border = self._device_row_palette(dev, selected)
            item = tk.Frame(self.device_list, bg=color, padx=12, pady=7,
                            highlightbackground=border,
                            highlightcolor=TEAL, highlightthickness=1,cursor='hand2')
            item.pack(fill='x', padx=6, pady=3)
            row = tk.Frame(item, bg=color)
            row.pack(fill='x')
            left = tk.Frame(row, bg=color)
            left.pack(side='left', padx=(0, 5))
            whole = bool(dev.get('partition_disk'))
            # Children are identified by their actual parent path, not merely
            # by the preceding row: the second/third partition must also indent.
            nested = self.is_nested_volume(self.ui.devices, index)
            if nested:
                tk.Frame(left, bg=color, width=26, height=1).pack(side='left')
                self.label(left, '└', 13, MUTED).pack(side='left', padx=(0, 7))
            icon_type = self.device_icon_type(dev, nested=nested)
            icon = self._device_icon(left, icon_type, color)
            icon.pack(side='left', padx=(0, 12))
            detail = tk.Frame(left, bg=color)
            detail.pack(side='left')
            # Preserve source labels in the controller (including the Cardputer UI),
            # but present the whole-disk marker as a badge on desktop.
            title = self.device_display_title(dev)
            self.label(detail, title, 11, FOREGROUND, True).pack(anchor='w')
            badges=tk.Frame(detail,bg=color)
            badges.pack(anchor='w',pady=(3,0))
            # For disk images, fs and transport both read IMAGE: showing
            # these generic pills would repeat IMAGE twice beside DISK IMAGE.
            badge_data = [(human_size(dev.get('size',0)),'#29394D',FOREGROUND)]
            if not dev.get('is_image'):
                if whole:
                    # Replace the generic DISK filesystem pill instead of
                    # showing DISK and WHOLE DISK side by side.
                    badge_data.append(('WHOLE DISK', '#345573', FOREGROUND))
                else:
                    badge_data.append((str(dev.get('fs','?')), '#28445B', BLUE))
                badge_data.append(((str(dev.get('transport') or '').strip().upper() or 'DISK'), '#20473F', TEAL))
            for data,background,tone in badge_data:
                pill=tk.Label(badges,text=data,font=('DejaVu Sans',9,'bold'),bg=background,fg=tone,
                              padx=7,pady=2,bd=0)
                pill._sector_badge=True
                pill.pack(side='left',padx=(0,5))
            if dev.get('is_image'):
                image_badge=tk.Label(badges,text='DISK IMAGE',font=('DejaVu Sans',9,'bold'),
                          bg='#33455B',fg='#ACBED2',padx=7,pady=2)
                image_badge._sector_badge=True
                image_badge.pack(side='left',padx=(0,5))
            if system:
                sysbadge=tk.Label(badges,text='SYSTEM DISK',font=('DejaVu Sans',9,'bold'),
                          bg='#654729',fg=AMBER,padx=7,pady=2)
                sysbadge._sector_badge=True
                sysbadge.pack(side='left',padx=(0,5))
            if dev.get('mounted'):
                mounted_badge=tk.Label(badges,text='Mounted',font=('DejaVu Sans',9,'bold'),
                         bg='#634528',fg=AMBER,padx=7,pady=2)
                mounted_badge._sector_badge=True
                mounted_badge.pack(side='left')
            self.label(detail, ('Read-only recovery source' if dev.get('is_image') else
                                'Whole disk · partition search or file-signature recovery' if whole else
                                'Filesystem volume · recover deleted files'),9,MUTED).pack(anchor='w',pady=(2,0))
            self.label(detail, dev.get('path',''), 9, MUTED).pack(anchor='w', pady=(2,0))
            caption=self.label(row, '✓  Selected' if selected else 'Select  →', 10,
                               AMBER if system else TEAL if selected else BLUE, True)
            caption.pack(side='right', padx=(6,0))
            self._device_rows.append((item,caption))
            # Never destroy a row on the first click. Both clicks of a double
            # click must hit the same widget for Tk to emit Double-Button-1.
            def bind_tree(widget):
                for seq in ('<MouseWheel>','<Button-4>','<Button-5>'):
                    widget.bind(seq,self._device_wheel)
                widget.bind('<Button-1>',lambda e,i=index:self.select_source(i))
                widget.bind('<Double-Button-1>',lambda e,i=index:self.select_source(i,True))
                try:widget.configure(cursor='hand2')
                except tk.TclError:pass
                for child in widget.winfo_children():bind_tree(child)
            bind_tree(item)
        # Apply compact dimensions before the first Tk idle/layout flush.
        # update_idletasks() can paint the new rows, briefly showing large
        # cards on a compact screen when returning to Device Selection.
        if self._compact:
            self._apply_density(self.device_list)
        self.device_list.update_idletasks()
        self.device_canvas.yview_moveto(scroll_y)

    @staticmethod
    def device_display_title(dev):
        """Show WHOLE DISK as a status badge without changing source identity."""
        label = str(dev.get('label', 'Unknown volume'))
        if dev.get('partition_disk') and label.endswith('[WHOLE DISK]'):
            return label[:-len('[WHOLE DISK]')].rstrip()
        return label

    @staticmethod
    def is_nested_volume(devices, index):
        """Partition hierarchy is based on an explicit whole-disk ancestor."""
        dev = devices[index]
        parent = dev.get('disk_path')
        if not parent or dev.get('partition_disk'):
            return False
        return any(i < index and item.get('partition_disk') and
                   item.get('path') == parent for i, item in enumerate(devices))

    @staticmethod
    def device_icon_type(dev, nested=False):
        """Avoid asserting a storage technology when the OS cannot tell."""
        if dev.get('is_image'):
            return 'image'
        # Only filesystem volumes receive the partition symbol. Keep the
        # original USB/SD/HDD/SSD pictograms on WHOLE DISK rows.
        if nested or (not dev.get('partition_disk') and dev.get('disk_path')
                      and dev.get('disk_path') != dev.get('path')):
            return 'partition'
        transport = str(dev.get('transport') or '').upper()
        media = str(dev.get('media_type') or '').lower()
        model = str(dev.get('model') or '').lower()
        path = str(dev.get('path') or '')
        if transport in ('SD', 'MMC', 'SDSTOR', 'SDCARD') or path.startswith('/dev/mmcblk') or 'sd card' in model:
            return 'sd'
        # lsblk may report ROTA=1 for USB flash drives; on Linux use the
        # actual USB transport before inferring a spinning HDD from ROTA.
        # Keep the already validated Windows icon classification unchanged.
        if sys.platform.startswith('linux') and transport == 'USB':
            return 'usb'
        if media == 'hdd' or dev.get('rotational') is True:
            return 'hdd'
        if transport == 'NVME' or 'nvme' in model or 'ssd' in model:
            return 'ssd'
        if transport == 'USB':
            return 'usb'  # Includes adapters of unknown type; not proof of flash.
        return 'disk'

    def _device_icon(self, parent, kind, background):
        """Small crisp vector devices; no dependence on emoji fonts."""
        canvas = tk.Canvas(parent, width=31, height=31, bg=background,
                           highlightthickness=0, bd=0)
        ink = TEAL if kind in ('sd','usb','ssd') else BLUE
        if kind == 'partition':
            # One filesystem volume, shown in front of its parent disk.
            # Large strokes keep the symbol legible at 31x31 on Linux/Windows.
            canvas.create_rectangle(4,4,23,23,outline='#536F8D',width=2)
            canvas.create_rectangle(10,11,28,28,fill=background,outline=BLUE,width=2)
            canvas.create_line(13,17,25,17,fill=BLUE,width=2)
            canvas.create_rectangle(13,21,19,25,fill=TEAL,outline='')
            canvas.create_line(22,23,25,23,fill=BLUE,width=2)
        elif kind == 'sd':
            canvas.create_polygon(8,5,21,5,25,10,25,27,7,27,7,11,
                                  outline=ink, fill='',width=2)
            for x in (11,15,19):
                canvas.create_line(x,7,x,12,fill=ink,width=1)
        elif kind == 'usb':
            canvas.create_rectangle(11,11,21,25,outline=ink,width=2)
            canvas.create_rectangle(13,5,19,11,fill=ink,outline=ink)
            canvas.create_line(15,7,15,10,fill=background,width=1)
            canvas.create_oval(14,19,18,23,fill=ink,outline='')
        elif kind == 'hdd':
            # Exposed platter + actuator, like a real hard disk. The older
            # rectangle/circle was easily confused with the generic drive.
            # Broad outlines remain legible at the native 31px icon size.
            canvas.create_polygon(7,3,24,3,28,7,28,24,24,28,7,28,3,24,3,7,
                                  fill='#243B54',outline=BLUE,width=2)
            canvas.create_oval(7,7,22,22,fill='#365875',outline='#A4D0FF',width=2)
            canvas.create_oval(13,13,17,17,fill='#D9EFFF',outline='')
            # Read/write head and its separate pivot at bottom right.
            canvas.create_line(23,24,19,20,19,18,fill=TEAL,width=2,
                               capstyle=tk.ROUND,joinstyle=tk.ROUND)
            canvas.create_oval(21,22,25,26,fill=TEAL,outline='')
        elif kind == 'ssd':
            # A 2.5-inch drive enclosure with an inset label.
            # The tall body reads as a disk rather than a bare controller chip.
            canvas.create_polygon(8,2,23,2,25,4,25,25,23,27,8,27,6,25,6,4,
                                  fill='#173F43',outline=TEAL,width=2)
            canvas.create_rectangle(9,6,22,18,fill='#2B5558',
                                    outline='#5E9D99',width=1)
            canvas.create_line(11,9,20,9,fill='#BBF9E9',width=2)
            canvas.create_line(11,13,18,13,fill='#8DBDB5',width=1)
            for x,y in ((8,4),(23,4),(8,23),(23,23)):
                canvas.create_line(x,y,x+1,y,fill='#9AF2DD',width=1)
        elif kind == 'image':
            # A document with a folded corner and a disk inside: disk image,
            # not an ordinary document or an installed physical drive.
            canvas.create_polygon(7,3,20,3,25,8,25,27,7,27,
                                  fill='#293B54',outline=BLUE,width=2)
            canvas.create_line(20,4,20,8,24,8,fill=BLUE,width=1)
            canvas.create_oval(11,13,21,23,fill='#355E72',outline=TEAL,width=2)
            canvas.create_oval(15,17,17,19,fill='#D6F9EF',outline='')
        else:
            # Unknown disk: neutral dual-bay drive, instead of an HDD-like
            # rectangle with a dot. Do not imply flash or spinning platters.
            canvas.create_rectangle(4,5,27,15,fill='#254058',outline=BLUE,width=2)
            canvas.create_line(8,10,18,10,fill='#A4D0FF',width=2)
            canvas.create_oval(22,9,25,12,fill=TEAL,outline='')
            canvas.create_rectangle(4,17,27,27,fill='#254058',outline=BLUE,width=2)
            canvas.create_line(8,22,18,22,fill='#A4D0FF',width=2)
            canvas.create_oval(22,21,25,24,fill=TEAL,outline='')
        return canvas

    def _ensure_windows_raw_access(self, source):
        from src.infrastructure.windows_raw import windows_device_path
        if sys.platform != 'win32' or not windows_device_path(source):
            return True
        from src.infrastructure.windows_elevation import is_elevated, restart_elevated_for_source
        if is_elevated():
            return True
        if not self.confirm_action('Administrator access required',
            'Windows needs administrator permission to read a physical drive. '
            'Restart SectorRec with these rights now? Disk images do not require elevation.',
            'Restart as Administrator'):
            return False
        try:
            restart_elevated_for_source(source)
        except (OSError, PermissionError, RuntimeError) as exc:
            self.show_modal_notice('Elevation unavailable', str(exc), warning=True)
            return False
        self.close()
        return False

    def choose_method(self, engine):
        if engine == 'image' and self.ui.device.get('is_system'):
            self.show_modal_notice('System imaging unavailable',
                'Creating a consistent image of the running system disk is not supported. '
                'Use another computer or bootable OS.',warning=True)
            return
        if not self._ensure_windows_raw_access(self.ui.device['path']):
            return
        check=self.ui.assess_selected_source()
        if not check.allows(engine):
            self.show_modal_notice('Not suitable for this source', check.description)
            return
        try:self.ui.desktop_select_method(engine)
        except (ValueError, RuntimeError, PermissionError) as exc:self.show_modal_notice('Recovery method',str(exc),warning=True);return
        self.show(force=True)

    def _method_icon(self, parent, kind):
        """Compact rounded, layered vector symbols (no raster dependencies)."""
        c=tk.Canvas(parent,width=64,height=64,bg=SURFACE,highlightthickness=0,bd=0)
        c.create_oval(3,3,61,61,fill='#22374A',outline='#46677D',width=2)
        if kind=='metadata':
            c.create_polygon(21,14,37,14,45,22,45,48,21,48,
                             fill='#294C56',outline=TEAL,width=2,smooth=False)
            c.create_line(37,14,37,22,45,22,fill=TEAL,width=2)
            for y,length in ((29,15),(35,12),(41,16)):
                c.create_line(26,y,26+length,y,fill='#BDDBE6',width=2,capstyle='round')
            c.create_oval(41,43,49,51,fill=TEAL,outline=SURFACE,width=1)
        elif kind=='partition' or kind=='image':
            c.create_rectangle(14,19,50,44,fill='#294C56',outline=BLUE,width=2)
            c.create_line(21,27,43,27,fill='#BDDBE6',width=2,capstyle='round')
            c.create_line(21,33,34,33,fill='#BDDBE6',width=2,capstyle='round')
            c.create_oval(40,34,45,39,fill=TEAL,outline='')
            c.create_line(17,48,47,48,fill=TEAL,width=2,capstyle='round')
        else:
            c.create_oval(17,15,43,41,fill='#294C56',outline=TEAL,width=2)
            c.create_line(39,38,51,50,fill=BLUE,width=4,capstyle='round')
            for x,y in ((24,24),(32,28),(31,20)):
                c.create_oval(x,y,x+4,y+4,fill='#BDDBE6',outline='')
        return c

    def show_drive_details(self):
        """One read-only device-information dialog with two switchable views."""
        if self.ui.diagnostic_loading:
            self.show_modal_notice('Device information','Reading available device information. Try again shortly.')
            return
        report=self.ui.diagnostic_report
        if report is None:
            self.show_modal_notice('Device information','Device information is not available yet.')
            return
        selected=dict(self.ui.device or {})
        key=selected.get('disk_path') or selected.get('path')
        previous=getattr(self,'_smart_window',None)
        if previous is not None and previous.winfo_exists():
            if getattr(previous,'_sector_source',None)==key:
                previous.lift()
                previous.focus_set()
                return
            previous.destroy()
        window=tk.Toplevel(self.root)
        self._smart_window=window
        window._sector_source=key
        window.title('SectorRec — Device information')
        window.geometry('820x650')
        window.minsize(570,410)
        window.configure(bg=BACKGROUND)
        wrapper=tk.Frame(window,bg=BACKGROUND,padx=21,pady=17)
        wrapper.pack(fill='both',expand=True)
        title=self.label(wrapper,'Device information',19,FOREGROUND,True)
        title.pack(anchor='w')
        summary_color=AMBER if report.health in ('warning','failed','unavailable') else TEAL
        subtitle=self.label(wrapper,report.note,10,summary_color,wrap=700)
        subtitle.pack(anchor='w',pady=(8,12))

        area=tk.Frame(wrapper,bg=BACKGROUND)
        area.pack(fill='both',expand=True)
        overview=tk.Frame(area,bg=SURFACE)
        details=tk.Frame(area,bg=SURFACE)
        canvas=tk.Canvas(overview,bg=SURFACE,highlightthickness=0)
        scroll=ttk.Scrollbar(overview,style='Sector.Vertical.TScrollbar',orient='vertical',command=canvas.yview)
        scroll.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        grid=tk.Frame(canvas,bg=SURFACE,padx=18,pady=12)
        grid_id=canvas.create_window((0,0),window=grid,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(grid_id,width=e.width))
        grid.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        def wheel(event):
            step=-1 if event.num==4 or (event.num not in (4,5) and event.delta>0) else 1
            canvas.yview_scroll(step*3,'units')
            return 'break'
        def bind_wheel(widget):
            for event in ('<MouseWheel>','<Button-4>','<Button-5>'):
                widget.bind(event,wheel)
            for child in widget.winfo_children():bind_wheel(child)
        for field,value in report_lines(report)+smart_attribute_lines(report)+smart_operational_lines(report):
            row=tk.Frame(grid,bg=SURFACE,pady=5)
            row.pack(fill='x')
            self.label(row,field,10,MUTED).pack(side='left')
            self.label(row,str(value),10,FOREGROUND,True,wrap=360).pack(side='right')
        bind_wheel(grid)
        canvas.bind('<MouseWheel>',wheel)
        canvas.bind('<Button-4>',wheel)
        canvas.bind('<Button-5>',wheel)

        content=tk.Text(details,bg=SURFACE,fg=FOREGROUND,insertbackground=TEAL,
                        selectbackground='#285D62',font=('DejaVu Sans Mono',10),
                        wrap='none',relief='flat',bd=0,padx=12,pady=10)
        vertical=ttk.Scrollbar(details,style='Sector.Vertical.TScrollbar',orient='vertical',command=content.yview)
        horizontal=ttk.Scrollbar(details,orient='horizontal',command=content.xview)
        content.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        vertical.pack(side='right',fill='y')
        horizontal.pack(side='bottom',fill='x')
        content.pack(fill='both',expand=True)
        content.insert('1.0','Extended report not requested.')
        content.configure(state='disabled')
        actions=tk.Frame(wrapper,bg=BACKGROUND)
        actions.pack(fill='x',pady=(13,0))
        toggle=self.button(actions,'Full SMART report  ↗',lambda:show_view('full'),
                           enabled=bool(selected and not selected.get('is_image')),
                           tooltip='Switch to the full read-only smartctl output. No disk test or repair is started.')
        toggle.pack(side='left')
        self.button(actions,'Close',window.destroy).pack(side='right')
        state={'loaded':False}
        def show_view(which):
            overview.pack_forget()
            details.pack_forget()
            if which=='full':
                details.pack(fill='both',expand=True)
                title.configure(text='Full SMART report')
                subtitle.configure(text='Extended read-only smartctl report · No self-test, disk write or repair.',fg=MUTED)
                toggle.configure(text='‹  Device overview',command=lambda:show_view('overview'))
                if not state['loaded']:
                    state['loaded']=True
                    content.configure(state='normal')
                    content.delete('1.0','end')
                    content.insert('1.0','Reading SMART attributes and logs…')
                    content.configure(state='disabled')
                    def finish(data,error):
                        if not window.winfo_exists():return
                        message=(json.dumps(data,indent=2,ensure_ascii=False) if data else
                            'Extended SMART unavailable: '+(error or 'The device returned no data.'))
                        content.configure(state='normal')
                        content.delete('1.0','end')
                        content.insert('1.0',message)
                        content.configure(state='disabled')
                    def read():
                        data,error=(None,'No hardware SMART query in demo mode.') if self.ui.demo else detailed_smart(selected)
                        try:self.root.after(0,lambda:finish(data,error))
                        except (RuntimeError,tk.TclError):pass
                    threading.Thread(target=read,daemon=True,name='sectorrec-smart-report').start()
            else:
                overview.pack(fill='both',expand=True)
                title.configure(text='Device information')
                subtitle.configure(text=report.note,fg=summary_color)
                toggle.configure(text='Full SMART report  ↗',command=lambda:show_view('full'))
        window._sector_views=(overview,details)
        window._sector_show_view=show_view
        show_view('overview')

    def _update_drive_summary(self):
        if not hasattr(self,'diagnostic_caption') or not self.diagnostic_caption.winfo_exists():
            return
        report=self.ui.diagnostic_report
        if self.ui.diagnostic_loading:
            title='Reading available SMART information…'
            tone=BLUE
        elif report:
            states={'passed':'SMART passed (not a guarantee)',
                    'warning':'SMART: attention needed',
                    'failed':'SMART: drive warning',
                    'unavailable':'SMART unavailable · Not a health verdict'}
            title=states.get(report.health,'SMART unavailable')
            if report.temperature_c is not None:title+=f'  ·  {report.temperature_c} °C'
            tone=AMBER if report.health!='passed' else TEAL
        else:
            title='SMART unavailable · Not a health verdict'
            tone=MUTED
        if self.diagnostic_caption.cget('text')!=title:
            self.diagnostic_caption.configure(text=title,fg=tone)
        if hasattr(self,'diagnostic_button'):
            self.diagnostic_button.configure(state='normal' if report else 'disabled')

    def show_methods(self):
        """Short device check, then only the applicable recovery actions.

        Assessment uses the OS-reported filesystem/layout; this screen does
        not claim an expensive filesystem or TestDisk scan has already run.
        """
        check = self.ui.assess_selected_source()
        self.clear('What can SectorRec do here?',
                   'Quick device check complete. Choose a recovery action based on what was detected.')
        badge = tk.Frame(self.body, bg=TEAL_DARK, padx=12, pady=4)
        badge.pack(fill='x', pady=(0, 4))
        dev=self.ui.device or {}
        summary = f"{dev.get('label','Selected source')}   ·   {human_size(dev.get('size',0))}   ·   {dev.get('fs','?')}"
        self.label(badge, summary, 11, FOREGROUND, True).pack(anchor='w',pady=(0,1))
        self.label(badge, 'DEVICE CHECK  ·  ' + check.headline.upper(), 10, TEAL, True).pack(anchor='w')
        self.label(badge, check.description, 10, FOREGROUND, wrap=850).pack(anchor='w', pady=(1, 0))
        if dev.get('is_system'):
            self._info_banner('LIVE SYSTEM DISK · IMPORTANT',
                'SectorRec will only READ this source and will never unmount it. '
                'Meanwhile your operating system may overwrite deleted data, and files '
                'can change during scanning. Select a different PHYSICAL disk for the '
                'recovered files and SQLite index; a different partition is not enough. '
                'For the safest recovery, scan from another PC or a bootable USB.')
        elif dev.get('mounted'):
            self.label(badge, '●  Mounted · Will unmount when you start the selected scan',
                       10, AMBER, wrap=860).pack(anchor='w', pady=(2, 0))
        self.ui.request_diagnostics()
        diag_row=tk.Frame(badge,bg=TEAL_DARK)
        diag_row.pack(fill='x',pady=(3,0))
        self.diagnostic_caption=self.label(diag_row,'',10,MUTED)
        self.diagnostic_caption.pack(side='left')
        self.diagnostic_button=self.button(diag_row,'Device details  ↗',self.show_drive_details,
            emphasis=True, tooltip='Inspect disk information and available SMART health data. SMART status does not guarantee the drive is healthy.')
        self.diagnostic_button.pack(side='right',pady=(0,6))
        self._update_drive_summary()
        # Mounted status belongs in the summary above, not a separate row.
        # Put the actions in a scrollable pane; the fixed footer never clips.
        viewport, viewport_inner = self.card(self.body, 11, 8)
        viewport.pack(fill='both', expand=True)
        canvas = tk.Canvas(viewport_inner, bg=SURFACE, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(viewport_inner, style='Sector.Vertical.TScrollbar', orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        columns = tk.Frame(canvas, bg=SURFACE)
        window_id = canvas.create_window((0,0), window=columns, anchor='nw')
        self._method_wrap_labels=[]
        def resize_cards(event):
            canvas.itemconfigure(window_id, width=event.width)
            for label in self._method_wrap_labels:
                label.configure(wraplength=max(235, event.width-225))
        canvas.bind('<Configure>', resize_cards)
        columns.bind('<Configure>', lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<MouseWheel>', lambda e:canvas.yview_scroll(-1 if e.delta>0 else 1, 'units'))
        canvas.bind('<Button-4>', lambda e:canvas.yview_scroll(-1, 'units'))
        canvas.bind('<Button-5>', lambda e:canvas.yview_scroll(1, 'units'))
        self.method_cards=[]
        self._method_selection=None
        self._method_source_path=(self.ui.device or {}).get('path')
        self._method_available={m.key for m in check.methods}
        for method in check.methods:
            outer, inner = self.card(columns, 13, 8)
            outer.pack(fill='x', pady=(0,5))
            content=tk.Frame(inner,bg=SURFACE)
            content.pack(fill='x')
            icon=self._method_icon(content,method.key)
            icon.pack(side='left',anchor='n',padx=(0,14),pady=(3,0))
            words=tk.Frame(content,bg=SURFACE)
            words.pack(side='left',fill='x',expand=True)
            short=method.caption
            self.label(words,short,9,TEAL,True).pack(anchor='w',pady=(0,2))
            title = {'metadata':'Recover deleted files by metadata',
                     'signature':'Recover deleted files by signatures'}.get(method.key, method.title)
            heading=self.label(words,title,13,FOREGROUND,True)
            heading.pack(anchor='w')
            self._method_wrap_labels.append(heading)
            body=self.label(words,method.benefit,10,FOREGROUND)
            body.pack(anchor='w',pady=(3,3))
            self._method_wrap_labels.append(body)
            limitation=self.label(words,'LIMIT  '+method.limitation,9,MUTED)
            limitation.pack(anchor='w')
            self._method_wrap_labels.append(limitation)
            select_badge=self.label(content,'Select  →',10,BLUE,True)
            select_badge.pack(side='right',anchor='n',padx=(16,5),pady=(8,0))
            self.method_cards.append((outer,method.key,select_badge))
            def bind_card(widget, key):
                widget.bind('<Button-1>',lambda _e,k=key:self.select_method(k))
                widget.bind('<Double-Button-1>',lambda _e,k=key:self.select_method(k,True))
                widget.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-1 if e.delta>0 else 1,'units'))
                widget.bind('<Button-4>',lambda e:canvas.yview_scroll(-1,'units'))
                widget.bind('<Button-5>',lambda e:canvas.yview_scroll(1,'units'))
                try:widget.configure(cursor='hand2')
                except tk.TclError:pass
                for child in widget.winfo_children():bind_card(child,key)
            bind_card(outer,method.key)
        if imaging_option_available(dev, self.ui.platform.name):
            outside, inside=self.card(columns,13,9)
            outside.pack(fill='x',pady=(4,5))
            imaging_icon=self._method_icon(inside,'image')
            imaging_icon.pack(side='left',padx=(0,14))
            label_col=tk.Frame(inside,bg=SURFACE)
            label_col.pack(side='left',fill='x',expand=True)
            self.label(label_col,'OPTIONAL SAFETY COPY',9,BLUE,True).pack(anchor='w')
            partition_image=(self.ui.platform.name == 'win32' and not dev.get('partition_disk'))
            title='Create a partition image (GNU ddrescue)' if partition_image else 'Create a disk image (GNU ddrescue)'
            detail=('Save only this partition as .img with a resumable .map on another disk.'
                    if partition_image else
                    'Save a complete .img and resumable .map on another disk before recovery.')
            self.label(label_col,title,12,FOREGROUND,True).pack(anchor='w')
            self.label(label_col,detail,10,MUTED).pack(anchor='w')
            # Entire optional card is actionable, matching the recovery-method cards.
            def click_image(widget):
                widget.bind('<Button-1>', lambda _e:self.select_method('image'))
                widget.bind('<Double-Button-1>', lambda _e:self.select_method('image',True))
                widget.configure(cursor='hand2')
                for child in widget.winfo_children():
                    # Buttons use their own Tk command; avoid duplicate invocation.
                    if not isinstance(child, tk.Button):click_image(child)
            image_badge=self.label(inside,'Select  →',10,BLUE,True)
            image_badge.pack(side='right',anchor='n',padx=(16,5))
            self._image_badge=image_badge
            self._image_card=outside
            click_image(outside)
        if sys.platform.startswith('linux') and dev.get('is_image'):
            from src.infrastructure.image_mount import available
            outside, inside=self.card(columns,13,9)
            outside.pack(fill='x',pady=(5,5))
            mount_label=tk.Frame(inside,bg=SURFACE);mount_label.pack(side='left',fill='x',expand=True)
            self.label(mount_label,'OPTIONAL FILESYSTEM ACCESS',9,BLUE,True).pack(anchor='w')
            self.label(mount_label,'Mount image read-only',12,FOREGROUND,True).pack(anchor='w',pady=(2,0))
            self.label(mount_label,'Browse existing files in your file manager. Never changes the image.',
                       10,MUTED,wrap=700).pack(anchor='w',pady=(3,0))
            mounted=(self._image_mount is not None and
                     self._image_mount.image == Path(dev['path']).resolve() and
                     self._image_mount.loop is not None)
            mount_action=self.button(inside,'Unmount image' if mounted else 'Mount image  ↗',
                lambda:self.toggle_image_mount(), emphasis=True,
                enabled=available() and not self._image_mount_busy,
                tooltip='Linux only. Requires UDisks2 and authorization; mounts filesystem partitions without write permission.')
            mount_action.pack(side='right',padx=(14,0))
            self._mount_action_button=mount_action
            if mounted:
                self.label(columns,'Mounted:  '+', '.join(path for _,path in self._image_mount.mounts),
                           9,TEAL,wrap=780).pack(anchor='w',pady=(2,5))
        for reason in check.unavailable:
            note=self.label(columns, 'Not available:  '+reason, 9, MUTED, wrap=760)
            note.pack(anchor='w',pady=(3,5))
            self._method_wrap_labels.append(note)
        if not check.methods:
            self.label(columns, 'No safe recovery action is available for this device.', 11, AMBER).pack(anchor='w')
        if imaging_option_available(dev, self.ui.platform.name):
            self._method_available.add('image')
        self._method_continue = self.bottom(back=('←  Change source',lambda:self.dispatch('BACK')),
                   primary=('Continue  →',self.continue_method), enabled=False).winfo_children()[-1]

    @staticmethod
    def _recolor_card(widget, color):
        # Preserve separately-colored icon glyphs; recolor only card panels and
        # labels inheriting their original surface.
        if isinstance(widget,(tk.Frame,tk.Label)) and widget.cget('bg') in (SURFACE,'#23443F'):
            widget.configure(bg=color)
        for child in widget.winfo_children():
            DesktopWindow._recolor_card(child,color)

    def select_method(self, key, advance=False):
        if (self.ui.stage != 'desktop_methods' or
                (self.ui.device or {}).get('path') != self._method_source_path):
            return
        if key not in getattr(self,'_method_available',set()):
            return
        self._method_selection=key
        for card, name, badge in self.method_cards:
            chosen=name==key
            card.configure(bg=TEAL if chosen else BORDER)
            badge.configure(text='✓  Selected' if chosen else 'Select  →',fg=TEAL if chosen else BLUE)
            # Device-list convention: tinted selected card with teal outline.
            for inside in card.winfo_children():
                self._recolor_card(inside,'#23443F' if chosen else SURFACE)
        if hasattr(self,'_image_card') and self._image_card.winfo_exists():
            chosen=key=='image'
            self._image_card.configure(bg=TEAL if chosen else BORDER)
            for inside in self._image_card.winfo_children():
                self._recolor_card(inside,'#23443F' if chosen else SURFACE)
            if hasattr(self,'_image_badge'):
                self._image_badge.configure(text='✓  Selected' if chosen else 'Select  →',fg=TEAL if chosen else BLUE)
        self.set_button_enabled(self._method_continue, True)
        if advance:
            self.continue_method()

    def continue_method(self):
        # Ignore delayed callbacks from a previous source/method screen.
        if (self.ui.stage != 'desktop_methods' or
                (self.ui.device or {}).get('path') != self._method_source_path):
            return
        key=self._method_selection
        if key=='image':
            self.create_disk_image()
        elif key:
            self.choose_method(key)

    def _release_previous_scan_for_imaging(self):
        """Do not acquire an exclusive image guard while an earlier scan owns it.

        A completed metadata/partition scan can retain ``ui.scan_guard`` while
        its results are displayed.  If the user switches directly to imaging
        the same disk, a second O_EXCL open returns EBUSY *against SectorRec's
        own guard*. Release it only after every reader has stopped.
        """
        active = [self.ui.scan, self.ui.recover, self.ui.signature,
                  self.ui.partition_search, self.ui.unmount_thread]
        if any(worker is not None and worker.is_alive() for worker in active):
            self.show_modal_notice('Disk still in use',
                'Finish or stop the current scan before creating a disk image.',
                warning=True)
            return False
        self.ui.release_source()
        return True

    def create_disk_image(self):
        if self.ui.device and self.ui.device.get('is_system'):
            self.show_modal_notice('System imaging unavailable',
                'Imaging the running OS disk can produce an inconsistent copy. '
                'Use another computer or a bootable OS.',warning=True)
            return
        """Choose destination on a dedicated page before any imaging begins."""
        if self.ui.stage != 'desktop_methods':
            return
        source=dict(self.ui.device or {})
        if not imaging_option_available(source, self.ui.platform.name):
            self.show_modal_notice('Image creation','Select a disk or filesystem partition.',warning=True)
            return
        if not self._ensure_windows_raw_access(source['path']):
            return
        if not self._release_previous_scan_for_imaging():
            return
        self._image_source=source
        self.ui.stage='desktop_image_setup'
        self.show(force=True)

    def show_image_setup(self, source):
        """Show exact source size, destination capacity and free space."""
        self.clear('Create a disk image',
                   'Optional safety copy · Save a complete raw image before recovering files.')
        self.selected_source(self.body,compact=True)
        outer, inner=self.card(self.body, 16, 13)
        outer.pack(fill='x',pady=(8,0))
        self.label(inner,'DESTINATION',10,TEAL,True).pack(anchor='w')
        row=tk.Frame(inner,bg=SURFACE);row.pack(fill='x',pady=(8,6))
        self.label(row,'Choose a destination on a different disk',11,MUTED).pack(side='left')
        location=self.destination_hint(inner,'Choose a folder…',lambda:choose())
        location.pack(fill='x',pady=(1,7))

        bytes_required=int(source.get('size',0))
        required=self.label(inner,'Image size: '+human_size(bytes_required)+
            ' · Additional free space needed: 128 MiB',11,FOREGROUND)
        required.pack(anchor='w',pady=(5,5))
        remaining=self.label(inner,'Available space: choose a destination',11,MUTED)
        remaining.pack(anchor='w')
        warning=self.label(inner,'The output is a .img and companion .map file. '
            'No data is written to your source.',10,MUTED,wrap=750)
        warning.pack(anchor='w',pady=(12,0))
        selected={'path':None,'output':None}
        foot=self.bottom(back=('←  Recovery methods',self.back_to_methods))
        launch=self.button(foot,'Start imaging  →',lambda:self._start_disk_image(source, selected['path'],selected['output']),enabled=False)
        launch.pack(side='right')
        def choose():
            chosen=self.pick_destination_directory()
            if not chosen:return
            selected.update(path=None,output=None)
            self.set_button_enabled(launch,False)
            try:
                from src.services.image_rescue import plan_image
                output=plan_image(source['path'],chosen,bytes_required,**({'source_info':source} if sys.platform == 'win32' else {}))
                free=shutil.disk_usage(chosen).free
            except (OSError,ValueError) as exc:
                # Never retain a green check for a failed/same-disk preflight.
                location.configure(text='✕  '+str(chosen),fg=DANGER,bg='#40252E',
                                   highlightbackground='#91505B')
                remaining.configure(text=self._explain_destination_error(exc),fg=DANGER)
                return
            location.configure(text='✓  '+str(chosen),fg=TEAL,bg='#1A3939',
                               highlightbackground='#39665E')
            remaining.configure(text=f'Available: {human_size(free)}  ·  Estimated after image: '
                f'{human_size(max(0,free-bytes_required))}',fg=TEAL)
            selected.update(path=chosen,output=output)
            self.set_button_enabled(launch,True)
        self.button(row,'Browse…',choose,emphasis=True,
                    tooltip='Select an output folder on a different disk. The image and map will be saved there.').pack(side='right')
        self.button(inner,'Resume an incomplete image…',lambda:self._select_resume_image(source),
                    emphasis=True,tooltip='Choose an existing SectorRec .img with matching .map and .source.json to retry unreadable sectors.').pack(anchor='w',pady=(10,2))
        self._info_banner('ABOUT DISK IMAGING',
            'GNU ddrescue copies readable sectors first and tracks unreadable areas in a .map file. '
            'Select Resume to retry unreadable areas using the same image and map. '
            'The .source.json file verifies the original device. Keep all three files together.')

    def _select_resume_image(self, source):
        """Validate existing imaging sessions without freezing the Tk event loop.

        Some ddrescue maps can be large, and Windows source identity checks can
        take seconds. All filesystem/PowerShell work happens off the UI thread;
        the themed progress window stays responsive and can be dismissed.
        """
        if getattr(self, '_resume_check', None) is not None:
            return
        image=self.pick_source_image(title='Resume a SectorRec disk image',
                                     filetypes=[('Raw disk image','*.img'),('All files','*')])
        if not image:return

        dialog=tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title('Checking recovery image')
        dialog.configure(bg=BACKGROUND)
        dialog.resizable(False,False)
        dialog.transient(self.root)
        shell=tk.Frame(dialog,bg=BORDER,padx=1,pady=1)
        shell.pack(fill='both',expand=True,padx=14,pady=14)
        content=tk.Frame(shell,bg=SURFACE,padx=22,pady=18)
        content.pack(fill='both',expand=True)
        self.label(content,'CHECKING RECOVERY IMAGE',12,TEAL,True).pack(anchor='w',pady=(0,8))
        detail=self.label(content,'Verifying the original disk, image and recovery map…',11,
                          FOREGROUND,wrap=380)
        detail.pack(anchor='w',pady=(0,13))
        spinner=ttk.Progressbar(content,style='Sector.Horizontal.TProgressbar',
                                mode='indeterminate',length=370)
        spinner.pack(fill='x',pady=(0,12))
        spinner.start(12)
        state={'done':False,'cancelled':False,'result':None,'error':None}

        def dismiss():
            state['cancelled']=True
            if state.get('after_id') is not None:
                try:dialog.after_cancel(state['after_id'])
                except tk.TclError:pass
                state['after_id']=None
            spinner.stop()
            self._resume_check=None
            try:dialog.destroy()
            except tk.TclError:pass

        state['cancel']=dismiss
        self.button(content,'Cancel',dismiss).pack(anchor='e')
        dialog.protocol('WM_DELETE_WINDOW',dismiss)
        dialog.bind('<Escape>',lambda _e:dismiss())
        dialog.update_idletasks()
        width,height=465,max(195,dialog.winfo_reqheight())
        dialog.geometry(f'{width}x{height}+'
                        f'{self.root.winfo_rootx()+max(0,(self.root.winfo_width()-width)//2)}+'
                        f'{self.root.winfo_rooty()+max(0,(self.root.winfo_height()-height)//2)}')
        dialog.deiconify()
        dialog.grab_set()
        self._resume_check=state

        def validate():
            try:
                from src.services.image_rescue import plan_resume, rescued_percent_from_map
                output=plan_resume(source['path'],image,int(source.get('size',0)),**({'source_info':source} if sys.platform == 'win32' else {}))
                # plan_resume already performed the strict disk identity and
                # destination checks; parse the map only after those pass.
                # Use the verified size recorded when the image was created,
                # not a possibly stale Windows device-list size.
                verified_size=int(json.loads(Path(str(output)+'.source.json').read_text(encoding='utf-8'))['size'])
                percentage=rescued_percent_from_map(str(output)+'.map',verified_size)
                state['result']=(output,percentage)
            except Exception as exc:
                # Never leave the Tk polling callback with an empty result:
                # even an unexpected validation error must be reported in
                # the UI rather than leaking out of the background thread.
                state['error']=str(exc) or type(exc).__name__
            finally:
                state['done']=True

        threading.Thread(target=validate,daemon=True,name='sectorrec-resume-check').start()

        def poll():
            if state['cancelled'] or self._closed or not dialog.winfo_exists():
                return
            if not state['done']:
                state['after_id']=dialog.after(90,poll)
                return
            result,error=state['result'],state['error']
            dismiss()
            if error:
                self.show_modal_notice('Cannot resume image',self._explain_destination_error(error),warning=True)
                return
            output,percent=result
            note=(f'The map records approximately {percent:.1f}% already recovered. '
                  if percent is not None else '')
            if not self.confirm_action('Resume disk image?',
                note+'Continue the existing image using the SAME original source disk? '
                'GNU ddrescue will skip rescued areas and retry damaged sectors. '
                'The .img and .map will be updated in place.', 'Resume image'):
                return
            self._start_disk_image(source,str(output.parent),output,resume=True)

        state['after_id']=dialog.after(90,poll)

    @staticmethod
    def _set_imaging_progress(progress, percent):
        """Display an actual ddrescue percentage, not the marquee's old value.

        ttk.Progressbar's indeterminate mode increments its internal value
        indefinitely; carrying that value into determinate mode produces an
        apparently full or stuck bar. Explicitly reset on the first reading.
        """
        try:
            value = max(0.0, min(100.0, float(percent)))
            if str(progress.cget('mode')) != 'determinate':
                progress.stop()
                progress.configure(mode='determinate', maximum=100, value=0)
            progress.configure(value=value)
        except (TypeError, ValueError, tk.TclError):
            return

    def _start_disk_image(self, source, folder, output, resume=False):
        """Begin ddrescue only after destination capacity has been reviewed."""
        if not folder or not output:return
        if not self._ensure_windows_raw_access(source['path']):
            return
        if sys.platform == 'win32' and source.get('imaging_size_verified') is False:
            self.show_modal_notice('Cannot verify disk capacity',
                       'Windows Get-Disk did not return an authoritative disk size. '
                       'Reconnect the device and refresh the source list before imaging.', warning=True)
            return
        if self._closed or (self._imaging_job and self._imaging_job.is_alive()):
            return  # One ddrescue operation per window, including preparation.
        # Revalidate immediately before launching in case a device was removed.
        from src.services.image_rescue import plan_image, plan_resume
        try:
            # Windows resume was checked when the image was selected. Do not
            # run slow PowerShell/storage checks a second time on the Tk
            # thread: ImageWorker._run_windows() independently revalidates
            # source identity, capacity and the destination before writing.
            if not (resume and sys.platform == 'win32'):
                refreshed=(plan_resume(source['path'],output,int(source.get('size',0)),**({'source_info':source} if sys.platform == 'win32' else {})) if resume
                           else plan_image(source['path'],folder,int(source.get('size',0)),preferred=output,**({'source_info':source} if sys.platform == 'win32' else {})))
                if Path(refreshed) != Path(output):
                    self.show_modal_notice('Destination changed','The output name is no longer available. Choose again.',warning=True)
                    return
        except (OSError,ValueError) as exc:
            self.show_modal_notice('Cannot create image',self._explain_destination_error(exc),warning=True)
            return
        # Also handle direct entry/retry: never leave a previous scan's
        # exclusive FD open when ImagingJob acquires its own reservation.
        if not self._release_previous_scan_for_imaging():
            return
        # Imaging lives in the main window, just like the other scan engines.
        self.clear('Resuming a disk image' if resume else 'Creating a disk image',
                   ('GNU ddrescue is retrying unreadable areas from the mapfile.' if resume
                    else 'GNU ddrescue is copying your source to an image on another disk.'))
        shell,inner=self.card(self.body,18,14)
        shell.pack(fill='x',pady=(8,0))
        self.label(inner,'DISK IMAGE',10,TEAL,True).pack(anchor='w')
        self.label(inner,'Source: '+str(source.get('label') or source['path']),11,FOREGROUND).pack(anchor='w',pady=(6,3))
        self.label(inner,'Destination: '+str(output),10,MUTED,wrap=820).pack(anchor='w')
        progress=ttk.Progressbar(inner,style='Sector.Horizontal.TProgressbar',length=520,mode='indeterminate')
        progress.pack(fill='x',pady=(18,7));progress.start(15)
        status_label=self.label(inner,'Preparing source…',12,FOREGROUND,True)
        status_label.pack(anchor='w',pady=(6,5))
        tip=self.label(inner,'The .map file preserves copy progress if imaging is interrupted.',10,MUTED,wrap=820)
        tip.pack(anchor='w')
        state={'state':'preparing','output':str(output),'resume':resume}
        alive={'yes':True}
        job=ImagingJob(source,output,state,resume=resume)
        self._imaging_job=job  # Register BEFORE preparation begins.
        job.start()
        footer=self.bottom()
        def stop():
            current=state.get('state')
            if current in ('running','preparing'):
                job.cancel()
                tip.configure(text='Stopping safely; the existing image and .map are preserved.')
            elif current in ('done','cancelled','error'):
                alive['yes']=False
                self.return_to_source_selection()
        button=self.button(footer,'Stop early · keep partial image',stop)
        button.pack(side='right')
        def retry():
            # Revalidate the source inside the imaging worker, with visible
            # "Preparing source" feedback instead of freezing the Tk window.
            if not self.confirm_action('Retry unreadable sectors?',
                'Use the same source disk and .map to retry unreadable sectors? '
                'Previously rescued areas are retained.', 'Retry sectors'):
                return
            alive['yes']=False
            self._start_disk_image(source,str(Path(output).parent),output,resume=True)
        retry_button=self.button(footer,'Retry unreadable sectors',retry,emphasis=True)
        # Imaging diagnostics are not lost when stderr is empty or the dialog
        # is too small to show the complete command failure.
        def copy_image_error():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(str(state.get('error') or 'No error details available.'))
            except tk.TclError:
                pass
        copy_error_button=self.button(footer,'Copy error details',copy_image_error)
        # Only shown after an incomplete or interrupted pass.

        def poll():
            if not alive['yes'] or not status_label.winfo_exists():return
            current=state.get('state','preparing')
            if current=='running':
                elapsed=int(time.monotonic()-state.get('started',time.monotonic()))
                pct=state.get('percent')
                if pct is not None:
                    self._set_imaging_progress(progress,pct)
                status_label.configure(text=(f'Copied: {pct:.1f}% · ' if pct is not None else 'Copying… · ')+
                                      f'{format_elapsed(elapsed)} elapsed'+(f" · {state['rate']}" if state.get('rate') else ''))
            elif current in ('done','incomplete','cancelled','error'):
                progress.stop()
                if current=='done':
                    # A successful image must never end on a random marquee frame.
                    progress.configure(mode='determinate',maximum=100,value=100)
                else:
                    progress.configure(mode='determinate',maximum=100,value=max(0,min(100,float(state.get('percent') or 0))))
                status_label.configure(text={'done':'Image pass finished · verify rescue map',
                                             'incomplete':'Incomplete image · unreadable sectors remain',
                                             'cancelled':'Stopped early · partial image kept',
                                             'error':'Image copy failed'}[current])
                tip.configure(text=state.get('error') or ('Saved: '+str(output)+'\nMap: '+str(output)+'.map'+'\nSource identity: '+str(output)+'.source.json'))
                if current=='error' and not copy_error_button.winfo_manager():
                    copy_error_button.pack(side='right',padx=(0,10))
                button.configure(text='←  Choose another source')
                # Once imaging finishes this becomes the navigation action,
                # so it belongs on the left, opposite any retry/error actions.
                button.pack_forget()
                button.pack(side='left')
                if current in ('incomplete','cancelled','error'):
                    if not retry_button.winfo_manager():
                        retry_button.pack(side='right',padx=(0,10))
                return
            self.root.after(350,poll)
        self.root.after(350,poll)

    def _quick_same_disk_destination(self, destination):
        """UI hint only; never used to authorize a write or bypass preflight."""
        if sys.platform != 'win32' or not self.ui.device:
            return None
        from src.infrastructure.windows_raw import quick_same_windows_disk
        return quick_same_windows_disk(self.ui.device['path'], destination)

    def _windows_destination_validation(self, method, destination):
        """Validate disk identity off the Tk thread; fail closed until confirmed.

        The native lookup can return None for junctions, inaccessible volumes,
        or an API that doesn't expose extents. This is UNKNOWN, not SAFE.
        Never put a green check on a Windows raw-disk destination based on it.
        """
        from src.infrastructure.windows_raw import windows_device_path
        device = self.ui.device or {}
        source = str(device.get('path') or '')
        if sys.platform != 'win32' or not windows_device_path(source):
            return True, None
        key = (method, source, str(destination))
        status = self._destination_validation
        if status is None or status['key'] != key:
            status = {'key': key, 'done': False, 'error': None}
            self._destination_validation = status
            if self._quick_same_disk_destination(destination) is True:
                status.update(done=True,
                              error='Destination is on the source physical disk. Choose another drive.')
            else:
                def validate():
                    from src.services.signature import validate_destination
                    try:
                        # The strict preflight checks physical disks and space.
                        validate_destination(source, destination)
                    except (ValueError, OSError, RuntimeError) as exc:
                        status['error'] = str(exc)
                    finally:
                        status['done'] = True
                threading.Thread(target=validate, daemon=True,
                                 name='sectorrec-destination-verify').start()
                self.root.after(150, lambda:self._poll_destination_validation(status))
        if not status['done']:
            return False, 'Verifying physical destination disk…'
        if status['error']:
            return False, status['error']
        return True, None

    def _poll_destination_validation(self, status):
        if self._closed or self._destination_validation is not status:
            return
        if not status['done']:
            self.root.after(150, lambda:self._poll_destination_validation(status))
            return
        if self.ui.stage in ('scan_ready', 'signature_setup'):
            method = 'metadata' if self.ui.stage == 'scan_ready' else 'signature'
            destination = (self.ui.metadata_destination if method == 'metadata'
                           else self.ui.signature_destination)
            if status['key'] == (method, str((self.ui.device or {}).get('path') or ''), str(destination)):
                self.show(force=True)

    def choose_metadata_destination(self):
        chosen=self.pick_destination_directory()
        if not chosen:return
        try:
            self.ui.set_metadata_destination(chosen)
            verified, detail = self._windows_destination_validation('metadata', chosen)
            if detail and self._is_same_disk_error(detail):
                self.show_modal_notice('Unsafe recovery destination',
                                       self._explain_destination_error(detail),warning=True)
        except (ValueError,OSError) as exc:
            self.show_modal_notice('Destination unavailable',self._explain_destination_error(exc),warning=True)
        self.show(force=True)

    def start_metadata_scan(self):
        if self.ui.metadata_destination:
            verified, detail = self._windows_destination_validation('metadata', self.ui.metadata_destination)
            if not verified:
                self.show_modal_notice('Unsafe recovery destination',
                                       self._explain_destination_error(detail),warning=True)
                return
        try:self.ui.metadata_preflight()
        except (ValueError,OSError) as exc:
            self.show_modal_notice('Destination required',self._explain_destination_error(exc),warning=True)
            return
        if self.ui.engine == 'partition' and self.ui.partition_status.get('candidates'):
            # A discovered partition must retain its TestDisk start offset.
            self.ui.begin_partition_file_scan()
            if self.ui.demo and self.ui.stage == 'scanning' and self.ui.status.get('state') == 'done':
                self.ui.tick()
            self.show(force=True)
        else:
            self.dispatch('OK')

    def show_metadata_setup(self):
        from_image = self.ui.partition_status.get('source') == 'image_metadata'
        self.clear('Configure Sleuth Kit recovery' if from_image else
                   'Configure partition file recovery' if self.ui.engine=='partition' else 'Configure metadata recovery',
            'Select the output folder before starting the read-only metadata scan.')
        self.selected_source(self.body,compact=True)
        shell,inner=self.card()
        shell.pack(fill='x')
        self.label(inner,'RECOVERY DESTINATION',10,TEAL,True).pack(anchor='w',pady=(0,8))
        row=tk.Frame(inner,bg=SURFACE)
        row.pack(fill='x',pady=(0,8))
        chosen=self.ui.metadata_destination
        destination_field=self.destination_hint(row,chosen or 'Choose a folder…',
                                                 self.choose_metadata_destination,wrap=690)
        destination_field.pack(side='left',fill='x',expand=True)
        self.button(row,'Browse…',self.choose_metadata_destination,emphasis=True,
                    tooltip='Choose where recovered files will be saved. No files are written during the initial metadata scan.').pack(side='right',padx=(12,0))
        ready=False
        if chosen:
            try:
                from src.infrastructure.windows_raw import windows_device_path
                if (sys.platform == 'win32' and self.ui.device and
                        windows_device_path(self.ui.device['path'])):
                    verified, detail = self._windows_destination_validation('metadata', chosen)
                    if not verified:
                        raise ValueError(detail)
                    folder=Path(self.ui.metadata_destination).resolve(strict=True)
                    if not folder.is_dir():
                        raise ValueError('Choose a destination directory.')
                    free=shutil.disk_usage(folder).free
                else:
                    folder,free=self.ui.metadata_preflight()
                self.label(inner,f'Available space: {human_size(free)} · recovered files will be saved here after the scan',
                           10,MUTED,wrap=850).pack(anchor='w',pady=(0,9))
                ready=True
            except (ValueError,OSError) as exc:
                pending = str(exc).startswith('Verifying physical destination disk')
                destination_field.configure(text=('◌  ' if pending else '✕  ')+str(chosen),
                                            fg=AMBER if pending else DANGER,
                                            bg='#282E37' if pending else '#40252E',
                                            highlightbackground='#83613D' if pending else '#91505B')
                self.label(inner,('Checking destination: ' if pending else 'Destination unavailable: ')+
                           (str(exc) if pending else self._explain_destination_error(exc)),
                           10,AMBER if pending else DANGER,wrap=850).pack(anchor='w',pady=(0,9))
        self.label(inner,'Read-only scan → review found entries → selectively save files.',
                   11,FOREGROUND).pack(anchor='w',pady=(5,8))
        if self.ui.device and self.ui.device.get('is_system'):
            self._info_banner('SYSTEM RECOVERY · LIMITATIONS',
                'The source remains mounted and active. Deleted sectors may be reused '
                'at any time; TRIM on SSDs can make recovery impossible. Filesystem '
                'metadata can change during the scan. The catalogue will be stored '
                'in a private temporary folder on your selected recovery disk, not '
                'on the system source. Use another computer for best results.')
        self._info_banner('ABOUT SLEUTH KIT',
            'Sleuth Kit reads directory entries and filesystem metadata (FAT, exFAT, NTFS, ext2–4). '
            'Deleted records may retain original filenames, paths and sizes, but overwritten data '
            'cannot be restored. Previewed entries are indexed first; extraction happens only '
            'after you select files.')
        if self.ui.device and self.ui.device.get('mounted') and not self.ui.device.get('is_system'):
            self.label(self.body,'Mounted source: SectorRec will unmount it before scanning.',10,AMBER).pack(anchor='w',pady=(10,0))
        self.bottom(back=('←  Image partitions' if from_image and len(self.ui.partition_status.get('candidates', []))>1 else
                          '←  Recovery methods' if from_image or self.ui.engine!='partition' else '←  Partitions',
                          self.back_to_methods),
                    primary=('Start metadata scan  →',self.start_metadata_scan),enabled=ready)

    def return_to_method_selection(self):
        """Return to the desktop method cards, not cached partition results."""
        self.ui.stage='desktop_methods'
        self.show(force=True)

    def back_to_methods(self):
        # Single-partition disk images return straight to methods; multi-image
        # tables return to their (no-TestDisk) partition picker.
        image_metadata=self.ui.partition_status.get('source')=='image_metadata'
        if image_metadata:
            self.ui.stage=('partition_results' if len(self.ui.partition_status.get('candidates',[]))>1
                           else 'desktop_methods')
        elif self.ui.engine=='partition' and self.ui.partition_status.get('candidates'):
            self.ui.stage='partition_results'
        else:
            self.ui.stage='desktop_methods'
        self.show(force=True)

    def show_partition_ready(self):
        self.clear('Find lost partitions',
                   'Search the disk for missing partition boundaries without repairing the partition table.')
        self.selected_source(self.body, compact=True)
        shell,inner=self.card();shell.pack(fill='x')
        for value in ('1   Scan the selected disk or image for partition candidates.',
                      '2   Review detected partitions and select a readable filesystem.',
                      '3   Configure Sleuth Kit to recover files to another disk.'):
            self.label(inner,value,11,FOREGROUND,wrap=750).pack(anchor='w',pady=6)
        self._info_banner('ABOUT TESTDISK',
            'TestDisk looks for lost or damaged partition boundaries (for example after deletion '
            'or partition-table corruption). SectorRec uses its findings only to locate a filesystem; '
            'Sleuth Kit then reads files from the selected offset. Nothing is written to the source. '
            'If a partition is already visible, metadata recovery is usually the better first choice.')
        self.label(self.body,'A detected boundary is only a candidate; some partitions may be unreadable.',
                   10,AMBER,wrap=740).pack(anchor='w',pady=(9,0))
        if self.ui.device and self.ui.device.get('mounted'):
            self.label(self.body,'Mounted volumes will be unmounted automatically when you start.',
                       10,AMBER,wrap=750).pack(anchor='w',pady=(8,0))
        self.bottom(back=('←  Recovery methods',self.return_to_method_selection) if self.ui.desktop else ('←  Devices',lambda:self.dispatch('BACK')),
                    primary=(('Unmount & search  →' if self.ui.device and self.ui.device.get('mounted')
                              else 'Start partition search  →'),lambda:self.dispatch('OK')))

    def show_partition_results(self):
        candidates=self.ui.partition_status.get('candidates',[])
        existing=sum(getattr(p,'origin','found') == 'existing' for p in candidates)
        additional=len(candidates)-existing
        image_table=self.ui.partition_status.get('source')=='image_metadata'
        self.clear('Choose a filesystem in this image' if image_table else
                   f'{existing} existing · {additional} additional partitions',
                   'Partitions listed directly from the disk-image table; no TestDisk search was started. '
                   'Select the filesystem to inspect with Sleuth Kit.' if image_table else
                   'Existing volumes are shown for reference. Additional locations come from TestDisk. '
                   'Choose one to browse its files without changing the partition table.')
        shell,inner=self.card(self.body,10,10);shell.pack(fill='both',expand=True)
        tree=ttk.Treeview(inner,columns=('status','offset','type','size'),show='headings',selectmode='browse',style='Sector.Treeview')
        tree.heading('status',text='STATUS',anchor='w',command=lambda:self.sort_partitions('status'))
        tree.heading('offset',text='START SECTOR',anchor='w',command=lambda:self.sort_partitions('offset'))
        tree.heading('type',text='PARTITION TYPE',anchor='w',command=lambda:self.sort_partitions('type'))
        tree.heading('size',text='APPROX. SIZE',anchor='e',command=lambda:self.sort_partitions('size'))
        tree.column('status',width=115,stretch=False)
        tree.column('offset',width=140,stretch=False)
        tree.column('type',width=325,stretch=True)
        tree.column('size',width=160,stretch=False,anchor='e')
        for i,item in enumerate(candidates):
            kind='Existing' if getattr(item,'origin','found')=='existing' else 'Detected'
            tree.insert('','end',iid=str(i),values=(kind,item.start,item.label,human_size(item.size)))
        scrollbar=ttk.Scrollbar(inner,style='Sector.Vertical.TScrollbar',orient='vertical',command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');tree.pack(fill='both',expand=True)
        self.partition_tree=tree
        if self._partition_sort:
            self.sort_partitions(self._partition_sort, toggle=False)
        if candidates:
            tree.selection_set(str(min(self.ui.partition_index,len(candidates)-1)))
        tree.bind('<<TreeviewSelect>>',lambda e:self._partition_selected())
        tree.bind('<Double-Button-1>',lambda e:self.open_partition())
        note=self.ui.partition_status.get('note')
        if not candidates:
            source=self.ui.partition_source or self.ui.device or {}
            if source.get('child_filesystems'):
                note=('No additional partition candidates were reported. The disk already has visible '
                      'volumes: go back and select an individual partition to recover deleted files. '
                      'This does not indicate a damaged SD card.')
            else:
                note=(note or 'TestDisk reported no usable candidates. This does not prove the disk is damaged.')
        if note:self.label(self.body,note,10,AMBER,wrap=900).pack(anchor='w',pady=10)
        self.bottom(back=('←  Recovery methods',self.return_to_method_selection) if self.ui.desktop else
                          ('←  Devices',lambda:self.dispatch('BACK')),
            primary=('Configure Sleuth Kit  →' if image_table else 'Recover deleted files  →',
                     self.open_partition),enabled=bool(candidates))

    def sort_partitions(self, column, toggle=True):
        # Sort the displayed candidates without changing their recovery IDs.
        if column not in ('status','offset','type','size'):return
        if toggle:
            self._partition_sort_desc = (not self._partition_sort_desc
                                         if column==self._partition_sort else False)
        self._partition_sort=column
        candidates=self.ui.partition_status.get('candidates',[])
        def key(i):
            part=candidates[i]
            if column=='status':return (getattr(part,'origin','found')!='existing',i)
            if column=='offset':return (int(part.start),i)
            if column=='size':return (int(part.size or 0),i)
            return (str(part.label).casefold(),i)
        for position,index in enumerate(sorted(range(len(candidates)),key=key,
                                               reverse=self._partition_sort_desc)):
            self.partition_tree.move(str(index),'',position)
        headings={'status':'STATUS','offset':'START SECTOR',
                  'type':'PARTITION TYPE','size':'APPROX. SIZE'}
        for field,title in headings.items():
            label=title + ('  ↓' if self._partition_sort_desc else '  ↑') if field==column else title
            self.partition_tree.heading(field,text=label)

    def _partition_selected(self):
        selected=self.partition_tree.selection()
        if selected:self.ui.partition_index=int(selected[0])

    def open_partition(self):
        candidates=self.ui.partition_status.get('candidates',[])
        if candidates:
            self._partition_selected()
            if self.ui.desktop:
                # Don't launch Sleuth Kit until the user has chosen a destination.
                self.ui.partition_selected=candidates[self.ui.partition_index]
                self.ui.engine='partition'
                self.ui.stage='scan_ready'
                self.show(force=True)
            else:
                self.dispatch('OK')

    def _single_native_picker(self, pick):
        """Allow only one file/folder chooser, including queued Tk clicks.

        Zenity and KDialog run synchronously on Linux. While they are open,
        mouse clicks on the Tk window may be queued and replayed *after* the
        subprocess returns. A simple try/finally busy flag is not sufficient:
        keep the gate closed briefly after dismissal so those events cannot
        summon a second chooser. The same gate covers Windows/Tk dialogs and
        prevents simultaneous file and directory pickers.
        """
        if getattr(self, '_native_picker_busy', False):
            return ''
        self._native_picker_busy = True
        try:
            return pick()
        finally:
            # Let pending input events drain before accepting another picker.
            # This is deliberately scheduled, not released synchronously.
            try:
                self.root.after(350, self._release_native_picker)
            except (tk.TclError, AttributeError):
                self._release_native_picker()

    def _release_native_picker(self):
        self._native_picker_busy = False

    def pick_destination_directory(self):
        """Choose a folder at most once per user action (Linux and Windows)."""
        def pick():
            # Cancelling native pickers must never open a second Tk picker.
            parent=str(Path.home())
            if sys.platform.startswith('linux'):
                command=None
                if shutil.which('zenity'):
                    command=['zenity','--file-selection','--directory',
                             '--title=Select recovery destination', '--filename='+parent+'/']
                elif shutil.which('kdialog'):
                    command=['kdialog','--getexistingdirectory', parent,
                             '--title', 'Select recovery destination']
                if command:
                    try:
                        result=subprocess.run(command, text=True, capture_output=True,
                                              timeout=300, check=False)
                        if result.returncode in (0,1):
                            return result.stdout.strip() if result.returncode==0 else ''
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            return filedialog.askdirectory(parent=self.root,
                    title='Where should recovered files be saved?',
                    initialdir=parent, mustexist=True)
        return self._single_native_picker(pick)

    def choose_destination(self):
        dest = self.pick_destination_directory()
        if not dest:
            return
        try:
            self.ui.set_signature_destination(dest)
            verified, detail = self._windows_destination_validation('signature', dest)
            if detail and self._is_same_disk_error(detail):
                self.show_modal_notice('Unsafe recovery destination',
                                       self._explain_destination_error(detail),warning=True)
        except (OSError, ValueError) as exc:
            self.show_modal_notice('Unsafe recovery destination' if self._is_same_disk_error(exc)
                                   else 'Destination not accessible',
                                   self._explain_destination_error(exc), warning=True)
            # Repaint the chosen unsafe Linux path in red rather than leaving
            # an earlier green selection visible after the warning is closed.
            self.show(force=True)
            return
        # Keep selection even if preflight subsequently rejects the destination:
        # the user must see the chosen path AND why it cannot be used.
        self.show(force=True)

    def change_format(self,event=None):
        # The UI picks a named, validated subset of PhotoRec signature families.
        keys=list(FORMAT_NAMES)
        self.ui.signature_format=keys[self.format_combo.current()]
        self.show_format_hint()

    def destination_hint(self, parent, value, command, wrap=650, invalid=False):
        """Show amber=not chosen, teal=verified, red=unsafe/unusable."""
        empty = not value or value.startswith('Choose a folder')
        marker = '✕  ' if invalid else ('▧  ' if empty else '✓  ')
        color = DANGER if invalid else (AMBER if empty else TEAL)
        background = '#40252E' if invalid else ('#282E37' if empty else '#1A3939')
        border = '#91505B' if invalid else ('#83613D' if empty else '#39665E')
        label=tk.Label(parent, text=marker+value,
            font=('DejaVu Sans',10,'bold' if empty or invalid else 'normal'),
            fg=color, bg=background,
            padx=12,pady=10,wraplength=wrap,justify='left',anchor='w',
            highlightthickness=1,highlightbackground=border,
            cursor='hand2' if command else 'arrow')
        if command:
            label.bind('<Button-1>',lambda _e:command())
            self.hover_tip(label, 'Click to select or change the destination folder.')
        return label

    def _info_banner(self, heading, explanation):
        outer=tk.Frame(self.body,bg='#28465A',padx=1,pady=1)
        outer.pack(fill='x',pady=(12,3))
        inner=tk.Frame(outer,bg='#182C3B',padx=15,pady=9)
        inner.pack(fill='x')
        self.label(inner,heading,9,TEAL,True).pack(anchor='w',pady=(0,3))
        self.label(inner,explanation,10,FOREGROUND,wrap=940).pack(anchor='w')

    def show_format_hint(self):
        if not hasattr(self,'format_info') or not self.format_info.winfo_exists():return
        key=self.ui.signature_format
        text = ('All file-signature families supported by PhotoRec are enabled. This is the safest default.'
                if key=='all' else
                'Only common signature families are included. Some related formats share a signature; '+
                'restricting formats may produce incomplete or oversized files.')
        self.format_info.config(text=text)

    @staticmethod
    def _fast_windows_signature_preview(destination):
        """Fast display-only hint. Never authorizes a raw-disk write."""
        from src.services.signature import photorec_binary, RESERVE_BYTES
        folder=Path(destination).expanduser().resolve(strict=True)
        if not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
            raise ValueError('Destination folder is not writable.')
        if not photorec_binary():
            raise RuntimeError('PhotoRec executable is missing.')
        free=shutil.disk_usage(folder).free
        if free <= RESERVE_BYTES:
            raise ValueError('At least 128 MB of free space is required.')
        return (f'Free space: {human_size(free)}  ·  '
                'Physical disk safety check runs before extraction')

    def show_signature_setup(self):
        self.clear('Configure signature recovery',
                   'Choose the destination and file types before starting PhotoRec.')
        self.selected_source(self.body, compact=True)
        shell,inner=self.card()
        shell.pack(fill='x')
        self.label(inner,'1  Choose a destination',12,FOREGROUND,True).pack(anchor='w')
        path=tk.Frame(inner,bg=SURFACE)
        path.pack(fill='x',pady=(12,16))
        dest=self.ui.signature_destination
        destination_field=self.destination_hint(path,dest or 'Choose a folder…',self.choose_destination)
        destination_field.pack(side='left',fill='x',expand=True)
        self.button(path, 'Browse…', self.choose_destination,emphasis=True,
                    tooltip='PhotoRec writes recovered files here while scanning. Choose another disk with enough free space.').pack(side='right',padx=(12,0))
        destination_ok = False
        if dest:
            try:
                from src.infrastructure.windows_raw import windows_device_path
                if not (sys.platform == 'win32' and self.ui.device and
                        windows_device_path(self.ui.device['path'])):
                    estimate=self.ui.signature_preflight()
                    destination_ok=True
                    storage=f'Free space: {human_size(estimate["free"])}'
                    if estimate['low']:
                        storage+='  ·  May be insufficient for a full extraction (confirmation required)'
                else:
                    verified, detail = self._windows_destination_validation('signature', dest)
                    if not verified:
                        raise ValueError(detail)
                    storage=self._fast_windows_signature_preview(dest)
                    destination_ok=True
            except (ValueError, RuntimeError, OSError) as exc:
                storage=self._explain_destination_error(exc)
            if not destination_ok:
                pending = 'Verifying physical destination disk' in storage
                destination_field.configure(text=('◌  ' if pending else '✕  ')+str(dest),
                                            fg=AMBER if pending else DANGER,
                                            bg='#282E37' if pending else '#40252E',
                                            highlightbackground='#83613D' if pending else '#91505B')
            self.label(inner,storage,10,MUTED if destination_ok else
                       AMBER if 'Verifying physical destination disk' in storage else DANGER,
                       wrap=810).pack(anchor='w',pady=(0,12))
        self.label(inner,'2  Which file signatures?',12,FOREGROUND,True).pack(anchor='w')
        keys=list(FORMAT_NAMES)
        self.format_combo=ttk.Combobox(inner,state='readonly',values=list(FORMAT_NAMES.values()),
                                      style='Sector.TCombobox',font=('DejaVu Sans',11),width=50)
        self.format_combo.current(keys.index(self.ui.signature_format))
        self.format_combo.pack(anchor='w',pady=(11,10))
        self.format_combo.bind('<<ComboboxSelected>>',self.change_format)
        self.format_info=self.label(inner,'',10,MUTED,wrap=750)
        self.format_info.pack(anchor='w',pady=(0,11))
        self.show_format_hint()
        self.label(inner,'IMPORTANT  •  Original names and folders are usually not preserved.',
                   10,AMBER).pack(anchor='w',pady=(4,0))
        if self.ui.device and self.ui.device.get('is_system'):
            self._info_banner('SYSTEM RECOVERY · LIMITATIONS',
                'PhotoRec reads the live disk without unmounting it. Ongoing system '
                'activity can replace deleted data, and SSD TRIM may erase it before '
                'recovery. The destination must be another PHYSICAL disk. Carving may '
                'include existing files and usually loses filenames/folders. Prefer '
                'offline recovery whenever possible.')
        else:
            self._info_banner('ABOUT PHOTOREC',
                'PhotoRec carves recognizable file headers from raw sectors without relying on the '
                'filesystem directory. It can work after formatting or damaged metadata, but usually '
                'cannot restore filenames or folder structure. Fragmentation, stale data and '
                'false positives can produce incomplete or unrelated files.')
        self.bottom(back=('←  Recovery methods',self.back_to_methods),
                    primary=('Start extraction  →',self.start_photo), enabled=destination_ok)

    def start_photo(self):
        if self.ui.signature_destination:
            verified, detail = self._windows_destination_validation('signature', self.ui.signature_destination)
            if not verified:
                self.show_modal_notice('Unsafe recovery destination',
                                       self._explain_destination_error(detail),warning=True)
                return
        try:estimate=self.ui.signature_preflight()
        except (ValueError, RuntimeError, OSError) as exc:
            self.show_modal_notice('Cannot start extraction',self._explain_destination_error(exc),warning=True);return
        if estimate['low']:
            if not self.confirm_action('Destination space warning',
                f"Available: {human_size(estimate['free'])}\n"
                f"Source size: {human_size(estimate['source_bytes'])}\n\n"
                'The amount recoverable is unknown. Extraction can exceed the available space.\n'
                'SectorRec monitors free space and requests an early stop if it becomes critically low.\n\n'
                'Continue with this destination?', 'Continue anyway'):
                return
        self.ui.signature_space_approved=True
        try:self.ui.start_signature_after_confirmation()
        except (ValueError,OSError,RuntimeError) as exc:self.show_modal_notice('PhotoRec',str(exc),warning=True);return
        self.show(force=True)

    def show_scanning(self, photorec=False, recovering=False, preparing=False):
        title=('Preparing source volume' if preparing else 'Saving selected files' if recovering
               else 'Extracting files by signatures' if photorec else 'Scanning filesystem metadata')
        desc=('Preparing exclusive read-only disk access; unmounting if needed.' if preparing else
              'Selected files are being saved to the output directory.' if recovering else
              'PhotoRec is saving recovered files now. You can stop early and keep existing output.' if photorec else
              'Searching deleted file entries. Stopping early will show partial results.')
        self.clear(title,desc)
        shell,inner=self.card()
        shell.pack(fill='x',pady=(4,0))
        self.label(inner, ('IN PROGRESS  ·  '+FORMAT_NAMES.get(self.ui.signature_format, 'All supported formats') if photorec else 'IN PROGRESS'), 10, TEAL, True).pack(anchor='w',pady=(0,6))
        progress=ttk.Progressbar(inner,style='Sector.Horizontal.TProgressbar',mode='indeterminate',length=680)
        progress.pack(fill='x',pady=(0,8))
        progress.start(12)
        number=self.label(inner,'',19,FOREGROUND,True)
        number.pack(anchor='w',pady=(0,5))
        secondary=self.label(inner,'',11,MUTED)
        secondary.pack(anchor='w')
        info=self.label(inner,'',10,MUTED,wrap=750)
        info.pack(anchor='w',pady=(6,0))
        self.dynamic={'number':number,'secondary':secondary,'info':info,'progress':progress}
        if (not preparing and not recovering) and self.ui.desktop:
            self._live_category = tk.StringVar(value='ALL')
            self._live_extension = tk.StringVar(value='')
            panel, panel_inner = self.card(self.body, 18, 13)
            panel.pack(fill='both', expand=True, pady=(6, 0))
            toolbar=tk.Frame(panel_inner, bg=SURFACE)
            toolbar.pack(fill='x', pady=(0, 11))
            self.label(toolbar, 'FILES EXTRACTED · live' if photorec else 'FILES FOUND SO FAR · preview',
                       10, TEAL, True).pack(side='left',padx=(4, 14))
            if photorec:
                self.label(toolbar, 'Newest recovered files first · full output is in the destination',
                           9, MUTED).pack(side='right',padx=4)
                # PhotoRec scans one family or all families. For a fixed family,
                # only extension is useful; "all" gets both selectors as Sleuth.
                self._live_category.set({
                    'photos':'PHOTOS', 'video':'VIDEOS', 'documents':'DOCS',
                    'audio':'AUDIO', 'archives':'ARCHIVES',
                }.get(self.ui.signature_format, 'ALL'))
            else:
                self.label(toolbar, 'Scan first · select files to recover when it finishes',
                           9, MUTED).pack(side='right',padx=4)
            filter_row=tk.Frame(panel_inner, bg=SURFACE)
            filter_row.pack(fill='x',pady=(0,9))
            if not photorec or self.ui.signature_format=='all':
                self.label(filter_row,'Category',9,MUTED).pack(side='left',padx=(0,6))
                cat=ttk.Combobox(filter_row,textvariable=self._live_category,state='readonly',width=14,
                                 style='Sector.TCombobox',values=('ALL','PHOTOS','VIDEOS','AUDIO','DOCS','ARCHIVES','OTHER'))
                cat.pack(side='left',padx=(0,14))
                cat.bind('<<ComboboxSelected>>',lambda *_:(self._live_update_extensions(),self._live_reset()))
                self._live_cat_combo=cat
            else:
                self.label(filter_row, 'Category: '+self._live_category.get(),
                           10, TEAL, True).pack(side='left',padx=(0,17))
            self.label(filter_row,'File extension',9,MUTED).pack(side='left',padx=(0,6))
            ext=ttk.Combobox(filter_row,textvariable=self._live_extension,state='readonly',width=16,
                             style='Sector.TCombobox',values=('All extensions',))
            ext.current(0)
            ext.pack(side='left',padx=(0,5))
            ext.bind('<<ComboboxSelected>>',lambda *_:self._live_reset())
            self._live_ext_combo=ext
            self._live_update_extensions()
            # Let the Treeview meet its panel seamlessly; no pale inset frame.
            box=tk.Frame(panel_inner, bg=SURFACE, bd=0, highlightthickness=0)
            box.pack(fill='both', expand=True)
            self.live_tree=ttk.Treeview(box, columns=('name','type','size'), show='headings',
                                       height=7, style='Sector.Treeview', selectmode='browse')
            for col,title,width in (('name','FILE',490),('type','TYPE',90),('size','SIZE',130)):
                self._sort_heading(self.live_tree,col,title,live=True,anchor='center')
                self.live_tree.column(col,width=width,anchor='center',stretch=(col=='name'))
            scrollbar=ttk.Scrollbar(box,orient='vertical',command=self.live_tree.yview,
                                    style='Sector.Vertical.TScrollbar')
            self.live_tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right',fill='y')
            self.live_tree.pack(side='left',fill='both',expand=True)
            self._live_previous=None
            self._live_last_update=0.0
            self._live_page=0
            self._live_size=100
            self._live_generation=0
            self._live_pending=False
            self._live_refresh_queued=False
            self._live_position=self._live_prev=self._live_next=None
            if not photorec:
                controls=tk.Frame(panel_inner,bg=SURFACE)
                controls.pack(fill='x',pady=(5,0))
                self._live_position=self.label(controls,'',9,MUTED)
                self._live_position.pack(side='left')
                self._live_next=self.page_button(controls,'>',lambda:self._live_move(1),
                                                enabled=False)
                self._live_next.pack(side='right',padx=(5,0))
                self._live_prev=self.page_button(controls,'<',lambda:self._live_move(-1),
                                                enabled=False)
                self._live_prev.pack(side='right')
        if not preparing:
            self.bottom(primary=('Stop recovery' if recovering else 'Stop extraction early' if photorec
                                 else 'Stop early · view partial results',
                                 lambda:self.dispatch('BACK')))
        self.update_dynamic()

    def _live_update_extensions(self):
        if not hasattr(self, '_live_ext_combo') or not self._live_ext_combo.winfo_exists():
            return
        category = self._live_category.get()
        suffixes = (set().union(*EXTENSIONS.values()) if category == 'ALL'
                    else set(EXTENSIONS.get(category, set())))
        # PhotoRec outputs real extensions, including families that are not in
        # our small common-suffix taxonomy; offer them as exact choices too.
        if self.ui.stage == 'signature_running':
            from src.models.files import category_of
            for name, _size, _path, _mtime in self.ui.signature_status.get('recent_files', ()):
                if category == 'ALL' or category_of(name) == category:
                    suffixes.add(Path(name).suffix.lower())
        choices = ('All extensions',) + tuple(sorted('NO EXT' if not x else x.lstrip('.') for x in suffixes))
        if tuple(self._live_ext_combo['values']) != choices:
            self._live_ext_combo.configure(values=choices)
        if self._live_extension.get() not in choices:
            self._live_extension.set('All extensions')

    def _live_reset(self):
        self._live_page=0
        self._refresh_live(force=True)

    def _live_move(self, direction):
        self._live_page=max(0,self._live_page+direction)
        self._refresh_live(force=True)

    def _refresh_live(self, force=False):
        """Desktop-only paginated read of the entire SQLite catalog.

        Database queries run off the Tk thread and never read the source disk.
        PhotoRec keeps its existing recent-output preview.
        """
        if not hasattr(self,'live_tree') or not self.live_tree.winfo_exists():
            return
        now=time.monotonic()
        if not force and now-getattr(self,'_live_last_update',0) < 0.95:
            return
        self._live_last_update=now
        term=''  # During scanning, browse by category/extension only.
        cat=self._live_category.get()
        ext=self._live_extension.get().strip().lower().lstrip('.')
        if ext=='all extensions': ext=''
        if self.ui.stage=='signature_running':
            self._live_update_extensions()
            rows=[]
            # Snapshot ordering is based on output file timestamps, not on
            # os.scandir() traversal order; newest files are shown first.
            recent = sorted(self.ui.signature_status.get('recent_files', ()),
                            key=lambda row: (row[3], row[2]), reverse=True)
            for name,size,path,_mtime in recent:
                suffix=Path(name).suffix.lower().lstrip('.')
                if ext == 'no ext':
                    if suffix:continue
                elif ext and suffix != ext:continue
                from src.models.files import category_of
                if name.lower() in ('report.xml','photorec.log','photorec.ses') or name.lower().startswith('photorec-output'): continue
                if cat!='ALL' and category_of(name)!=cat: continue
                rows.append((name,suffix.upper() or 'OTHER',human_size(size)))
            from src.views.table_sort import sorted_preview
            rows=sorted_preview(rows,self._live_sort,self._live_sort_desc)
            self._live_apply(rows,len(rows),False)
            return
        if self.ui.stage!='scanning' or not self.ui.catalog:
            return
        if self._live_pending:
            self._live_refresh_queued=True
            return
        self._live_pending=True
        self._live_generation+=1
        generation=self._live_generation
        page=self._live_page
        catalog=self.ui.catalog
        def query():
            try:
                total,items=catalog.live_page(page*self._live_size,self._live_size,term,cat,ext,
                                             self._live_sort,self._live_sort_desc)
                rows=[(path,extension.upper() or 'OTHER',human_size(size)) for _,path,extension,size in items]
                error=None
            except Exception as exc:
                total,rows,error=0,[],str(exc)
            def apply():
                if generation!=self._live_generation:return
                self._live_pending=False
                if not hasattr(self,'live_tree') or not self.live_tree.winfo_exists():return
                if error:
                    if self._live_position is not None:
                        self._live_position.config(text='Catalog read unavailable: '+error[:65])
                    return
                self._live_apply(rows,total,True)
                if self._live_refresh_queued:
                    self._live_refresh_queued=False
                    self._refresh_live(force=True)
            try:self.root.after(0,apply)
            except (RuntimeError,tk.TclError):pass
        threading.Thread(target=query,daemon=True,name='sectorrec-live-catalog').start()

    def _live_apply(self,rows,total,paginated):
        snapshot=tuple(rows)
        if snapshot!=getattr(self,'_live_previous',None):
            self._live_previous=snapshot
            selection=self.live_tree.selection()
            selected=tuple(self.live_tree.item(i,'values') for i in selection)
            self.live_tree.delete(*self.live_tree.get_children())
            for row in rows:self.live_tree.insert('','end',values=row)
            if selected:
                for item in self.live_tree.get_children():
                    if tuple(self.live_tree.item(item,'values'))==selected[0]:
                        self.live_tree.selection_set(item);break
        if paginated:
            maximum=max(0,(total-1)//self._live_size)
            if self._live_page>maximum:
                self._live_page=maximum
                self._refresh_live(force=True)
                return
            self._live_position.config(text=f'{total:,} matching · page {self._live_page+1} / {maximum+1}')
            self._live_prev.config(state='normal' if self._live_page else 'disabled')
            self._live_next.config(state='normal' if self._live_page<maximum else 'disabled')
        else:
            # PhotoRec is an unpaginated recent-files preview, not a catalog.
            # No redundant caption, and no inactive navigation buttons.
            if self._live_position is not None:
                self._live_position.config(text='')
            if self._live_prev is not None:
                self._live_prev.pack_forget()
            if self._live_next is not None:
                self._live_next.pack_forget()

    def show_partition_scanning(self):
        self.clear('Searching for lost partitions',
                   'TestDisk is looking for partition boundaries. Read activity is not a scan percentage.')
        shell, inner = self.card()
        shell.pack(fill='x', pady=(15, 0))
        header = tk.Frame(inner, bg=SURFACE)
        header.pack(fill='x', pady=(0, 12))
        self.label(header, 'READ-ONLY PARTITION SEARCH', 10, TEAL, True).pack(side='left')
        clock = self.label(header, '0s ELAPSED', 10, BLUE, True)
        clock.pack(side='right')
        progress = ttk.Progressbar(inner, style='Sector.Horizontal.TProgressbar', mode='indeterminate')
        progress.pack(fill='x', pady=(0, 15))
        progress.start(12)

        # Compact, stable diagnostic tiles: changing counters never resize the
        # window or push the Stop button below the screen.
        metrics = tk.Frame(inner, bg=SURFACE)
        metrics.pack(fill='x', pady=(0, 11))
        def metric_tile(title):
            border = tk.Frame(metrics, bg=BORDER, padx=1, pady=1)
            border.pack(side='left', fill='both', expand=True, padx=(0, 8) if title == 'READ ACTIVITY' else (8, 0))
            cell = tk.Frame(border, bg=SURFACE_2, padx=14, pady=10)
            cell.pack(fill='both', expand=True)
            self.label(cell, title, 9, MUTED, True).pack(anchor='w')
            value = self.label(cell, 'Checking…', 17, TEAL, True)
            value.pack(anchor='w', pady=(4, 1))
            detail = self.label(cell, '', 9, MUTED, wrap=380)
            detail.pack(anchor='w')
            return value, detail
        read_value, read_detail = metric_tile('READ ACTIVITY')
        if sys.platform == 'win32':
            log_value, log_detail = metric_tile('TESTDISK LOG')
        else:
            log_value, log_detail = metric_tile('SCAN STATUS')
        tip = self.label(inner, '', 10, MUTED, wrap=800)
        tip.pack(anchor='w', pady=(0, 4))
        caution = self.label(inner, '', 10, AMBER, wrap=800)
        caution.pack(anchor='w')
        self.dynamic = {'partition': progress, 'partition_clock': clock,
                        'partition_info': tip, 'partition_read': read_value,
                        'partition_read_detail': read_detail, 'partition_log': log_value,
                        'partition_log_detail': log_detail, 'partition_caution': caution}
        self.update_dynamic()
        self.bottom(primary=('Stop early · review found partitions', lambda:self.dispatch('BACK')))

    def show_signature_report(self):
        st=self.ui.signature_status
        label={'done':'Extraction finished','cancelled':'Extraction stopped early',
               'error':'Extraction encountered an error'}.get(st.get('state'),'Extraction report')
        self.clear(label,'Review the extraction summary. Existing recovered files stay in the destination.')
        shell,inner=self.card()
        shell.pack(fill='x')
        state=st.get('state')
        glyph=('✓' if state=='done' else '!' if state=='cancelled' else '✕')
        tint=(TEAL if state=='done' else AMBER if state=='cancelled' else '#FF7E87')
        self.label(inner,f"{glyph}  {st.get('files',0):,} files written",22,tint,True).pack(anchor='w')
        self.label(inner,'PhotoRec file counts do not verify whether the original files are intact.',
                   10,MUTED,wrap=760).pack(anchor='w',pady=(6,0))
        self.label(inner,f"{human_size(st.get('written',0))} written   •   {human_size(st.get('free',0))} free",
                   12,MUTED).pack(anchor='w',pady=(12,0))
        destination=self.ui.signature_destination
        actual=self.signature_output_folder(destination,st.get('output')) if destination else None
        self.label(inner,'Recovered files: '+str(actual or '—'),
                   10,MUTED,wrap=740).pack(anchor='w',pady=(12,0))
        if st.get('output'):
            self.label(inner,'Additional PhotoRec folders (.2, .3, …) may sit beside this one.',
                       9,MUTED,wrap=740).pack(anchor='w',pady=(5,0))
        if st.get('error') or st.get('stopped_reason'):
            self.label(inner,st.get('error') or st.get('stopped_reason'),11,AMBER,wrap=700).pack(anchor='w',pady=(12,0))
        self.bottom(back=('←  Choose another source',lambda:self.dispatch('OK')),
                    primary=('Open destination folder  ↗', self.open_signature_destination))

    @staticmethod
    def signature_output_folder(destination, output):
        """Find the first real PhotoRec output dir without escaping destination."""
        root=Path(destination).expanduser().resolve()
        if not output:return root
        prefix=Path(output)
        if prefix.parent != root or not prefix.name.startswith('SectorRec_'):
            return root
        # PhotoRec creates PREFIX.1 (and optionally .2, .3, ...).
        # Never follow a symlink inserted after extraction.
        for child in sorted(root.glob(prefix.name+'.[0-9]*'),
                            key=lambda p:int(p.name[len(prefix.name)+1:])
                            if p.name[len(prefix.name)+1:].isdigit() else 10**9):
            suffix=child.name[len(prefix.name)+1:]
            if suffix.isdigit() and child.is_dir() and not child.is_symlink():
                return child
        return root

    def _open_folder(self, path):
        path=Path(path).expanduser()
        if not path.is_dir():
            self.show_modal_notice('Destination unavailable', 'The recovery folder no longer exists.',warning=True)
            return
        try:
            if sys.platform.startswith('win'):
                os.startfile(str(path))
            else:
                subprocess.Popen(['xdg-open',str(path)],start_new_session=True,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,ValueError) as exc:
            self.show_modal_notice('Could not open destination',str(exc),warning=True)

    def open_signature_destination(self):
        """Open this scan's output, not merely the destination selected earlier."""
        destination=self.ui.signature_destination
        if not destination:
            self.show_modal_notice('Destination unavailable','No recovery destination was selected.',warning=True)
            return
        self._open_folder(self.signature_output_folder(destination,self.ui.signature_status.get('output')))

    def set_category(self,event=None):
        self.ui.category_filter_index=self.category_combo.current()
        self.ui.rebuild_extensions()
        self.ui.result_index=0
        self._page=0
        self.show(force=True)

    def set_extension(self,event=None):
        self.ui.extension_filter_index=self.extension_combo.current()
        self.ui.result_index=0
        self._page=0
        self.show(force=True)

    def show_results(self):
        count,size,unknown=self.ui.found_details()
        # A finished primary fls pass can still report limited *secondary*
        # search coverage; do not imply the user stopped the scan.
        tag=('  ·  ' + self.ui.scan_result_label) if self.ui.scan_partial else ''
        description=(f"Known total size: {human_size(size)}" +
                     ('  ·  Some file sizes are unknown.' if unknown else '') +
                     '  ·  Select a file or filter the results below.')
        if self.ui.scan_partial_reason:
            description += '\n' + self.ui.scan_partial_reason
        self.clear(f'{count:,} files found{tag}', description)
        controls=tk.Frame(self.body,bg=SURFACE,padx=14,pady=11,highlightthickness=1,highlightbackground=BORDER)
        controls.pack(fill='x',pady=(0,14))
        self.label(controls,'Category',10,MUTED,True).pack(side='left',padx=(0,10))
        self.category_combo=ttk.Combobox(controls,state='readonly',style='Sector.TCombobox',width=16,
                                        values=self.ui.category_options)
        self.category_combo.current(self.ui.category_filter_index)
        self.category_combo.pack(side='left',padx=(0,23))
        self.category_combo.bind('<<ComboboxSelected>>',self.set_category)
        self.label(controls,'File extension',10,MUTED,True).pack(side='left',padx=(0,10))
        self.extension_combo=ttk.Combobox(controls,state='readonly',style='Sector.TCombobox',width=18,
                                         values=[('NO EXT' if x=='' else x.upper()) for x in self.ui.extension_options])
        self.extension_combo.current(self.ui.extension_filter_index)
        self.extension_combo.pack(side='left')
        self.extension_combo.bind('<<ComboboxSelected>>',self.set_extension)
        shell,inner=self.card(self.body,12,10)
        shell.pack(fill='both',expand=True)
        content=tk.Frame(inner,bg=SURFACE)
        content.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(content,columns=('path','type','size'),show='headings',
                               selectmode='browse',style='Sector.Treeview')
        self._sort_heading(self.tree,'path','FILE / ORIGINAL PATH',live=False)
        self._sort_heading(self.tree,'type','TYPE',live=False)
        self._sort_heading(self.tree,'size','KNOWN SIZE',live=False)
        self.tree.column('path',width=580,stretch=True,anchor='w')
        self.tree.column('type',width=100,stretch=False,anchor='w')
        self.tree.column('size',width=135,stretch=False,anchor='e')
        sc=ttk.Scrollbar(content,style='Sector.Vertical.TScrollbar',orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=sc.set)
        sc.pack(side='right',fill='y')
        self.tree.pack(side='left',fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.select_result)
        self.tree.bind('<Double-Button-1>',lambda e:self.recover_one())
        self._fill_page()
        navigation=tk.Frame(self.body,bg=BACKGROUND)
        navigation.pack(fill='x',pady=(4,6))
        self.page_button(navigation,'<',lambda:self.change_page(-1),
                         enabled=self._page>0).pack(side='left')
        total_pages=max(1,(count+self._page_size-1)//self._page_size)
        self.label(navigation,f'Page {self._page+1} of {total_pages}   ·   {count:,} matching files',
                   10,MUTED).pack(side='left',padx=12)
        self.page_button(navigation,'>',lambda:self.change_page(1),
                         enabled=self._page+1<total_pages).pack(side='left')
        footer=self.bottom(back=('←  Partitions' if self.ui.engine=='partition' else '←  Change source',lambda:self.dispatch('BACK')))
        self.button(footer,'Recover all filtered files',self.recover_filtered,
                    enabled=count>0).pack(side='right',padx=(12,0))
        self.button(footer,'Recover selected file',self.recover_one,primary=True,
                    enabled=count>0).pack(side='right')

    def page_button(self, parent, glyph, command, enabled=True):
        """Compact square pagination, distinct from action buttons."""
        btn = tk.Button(parent,text=glyph,command=command,
            font=('DejaVu Sans',11,'bold'),width=2,padx=1,pady=0,
            bg='#263D53',fg=BLUE,activebackground='#34536E',
            activeforeground=FOREGROUND,disabledforeground='#526476',
            relief='flat',bd=0,highlightthickness=1,
            highlightbackground='#36576D',cursor='hand2' if enabled else 'arrow',
            state='normal' if enabled else 'disabled')
        self.hover_tip(btn, 'Previous page' if glyph=='<' else 'Next page')
        return btn

    def _fill_page(self):
        if not hasattr(self,'tree'):return
        for x in self.tree.get_children(): self.tree.delete(x)
        start=self._page*self._page_size
        records=self.ui.result_rows(start,self._page_size)
        for offset,row in enumerate(records):
            absolute=start+offset
            self.tree.insert('', 'end',iid=str(absolute),values=(row[2],row[4],human_size(row[3])))
        if records:
            selected=min(max(self.ui.result_index,start),start+len(records)-1)
            self.ui.result_index=selected
            self.tree.selection_set(str(selected))
            self.tree.focus(str(selected))

    def select_result(self,event=None):
        selection=self.tree.selection()
        if selection:self.ui.result_index=int(selection[0])

    def _ensure_metadata_destination(self):
        """Partition scan can enter results without the metadata setup screen."""
        try:
            self.ui.metadata_preflight()
            return True
        except (ValueError,OSError):
            chosen=self.pick_destination_directory()
            if not chosen:
                return False
            try:
                self.ui.set_metadata_destination(chosen)
                self.ui.metadata_preflight()
                return True
            except (ValueError,OSError) as exc:
                self.show_modal_notice('Unsafe destination',self._explain_destination_error(exc),warning=True)
                return False

    def recover_filtered(self):
        if self._ensure_metadata_destination():
            self.dispatch('ALL')

    def recover_one(self):
        if self.ui.result_count() and self._ensure_metadata_destination():
            self.dispatch('OK')

    def change_page(self,delta):
        max_page=max(0,(self.ui.result_count()-1)//self._page_size)
        self._page=max(0,min(max_page,self._page+delta))
        self.ui.result_index=self._page*self._page_size
        self.show(force=True)

    def show_confirm(self):
        self.clear('Confirm file recovery',
                   'SectorRec only writes recovered files to the destination, not to the source.')
        shell,inner=self.card()
        shell.pack(fill='x')
        self.label(inner,self.ui.message,12,FOREGROUND,wrap=780).pack(anchor='w')
        self.label(inner,'Output folder: '+str(self.ui.metadata_destination or 'Not selected'),10,TEAL,wrap=780).pack(anchor='w',pady=(10,0))
        self.bottom(back=('Cancel',lambda:self.dispatch('BACK')),
                    primary=('Recover files  →',lambda:self.dispatch('OK')))

    def show_metadata_report(self):
        self.clear('Recovery report','See what was saved successfully and what needs attention.')
        rows=(('recovered','✓','Recovered',TEAL),
              ('partial','!','Incomplete',AMBER),
              ('unverified','?','Unverified',BLUE),
              ('failed','✕','Failed','#FF7E87'))
        bar=tk.Frame(self.body,bg=BACKGROUND)
        bar.pack(fill='x',pady=(6,12))
        for name,symbol,title,color in rows:
            value=int(self.ui.status.get(name,0) or 0)
            box,inner=self.card(bar,padx=17,pady=13)
            box.pack(side='left',fill='both',expand=True,padx=(0,9))
            self.label(inner,f'{symbol}  {value}',21,color,True).pack(anchor='w')
            self.label(inner,title,10,MUTED).pack(anchor='w',pady=(5,0))
        self.label(self.body,'✓ Complete: extracted bytes match the known size. '
                   '  ! Incomplete: some data was recovered. '
                   '  ? Unverified: original size was unknown. '
                   '  ✕ Failed: no usable file was saved.',
                   10,MUTED,wrap=850).pack(anchor='w',pady=(2,15))
        if self.ui.status.get('error'):
            self.label(self.body,str(self.ui.status['error'])[:250],11,AMBER,wrap=850).pack(anchor='w',pady=(0,10))
        if self.ui.status.get('outdir'):
            self.label(self.body,'Saved to: '+self.ui.status['outdir'],10,FOREGROUND,wrap=850).pack(anchor='w')
        footer=self.bottom(back=('Choose another source',lambda:self.dispatch('BACK')),
                    primary=('Back to found files  →',lambda:self.dispatch('OK')))
        if self.ui.status.get('outdir'):
            self.button(footer,'Open recovered files ↗',
                        lambda:self._open_folder(Path(self.ui.status['outdir']))).pack(side='right',padx=(0,10))

    def show_notice(self):
        self.clear('SectorRec needs your attention','The operation could not continue as requested.')
        shell,inner=self.card()
        shell.pack(fill='x')
        self.label(inner,self.ui.message,12,AMBER,wrap=790).pack(anchor='w')
        self.bottom(primary=('OK',lambda:self.dispatch('OK')))

    def _soft_page_transition(self):
        """Instant transition: never blend previous and current Tk frames."""
        for after_id in self._fade_after_ids:
            try:self.root.after_cancel(after_id)
            except tk.TclError:pass
        self._fade_after_ids=[]
        try:self.root.attributes('-alpha',1.0)
        except tk.TclError:pass

    def show(self,force=False):
        if self._closed:return
        stage=self.ui.stage
        if not force and stage==self._stage:return
        previous=self._stage
        # Every fresh PhotoRec scan starts with the newest files first. A user
        # can still click a column to temporarily sort this preview manually.
        if stage == 'signature_running' and previous != stage:
            self._live_sort = 'id'
            self._live_sort_desc = True
        self._stage=stage
        self.steps()
        if stage=='devices':self.show_sources()
        elif stage=='desktop_methods':self.show_methods()
        elif stage=='scan_ready':self.show_metadata_setup()
        elif stage=='signature_setup':self.show_signature_setup()
        elif stage=='desktop_image_setup':self.show_image_setup(self._image_source)
        elif stage=='partition_ready':self.show_partition_ready()
        elif stage=='partition_scanning':self.show_partition_scanning()
        elif stage=='partition_results':self.show_partition_results()
        elif stage=='scanning':self.show_scanning()
        elif stage=='signature_running':self.show_scanning(photorec=True)
        elif stage=='results':self.show_results()
        elif stage=='signature_summary':self.show_signature_report()
        elif stage=='confirm':self.show_confirm()
        elif stage=='recovering':self.show_scanning(recovering=True)
        elif stage=='complete':self.show_metadata_report()
        elif stage=='notice':self.show_notice()
        elif stage=='unmounting':self.show_scanning(preparing=True)
        elif stage=='exit':self.close()
        else:self.show_sources()
        if self._compact and not self._closed:
            self._apply_density(self.body)
        if not self._closed and previous is not None and previous != stage:
            # Avoid compositor ghosting from translucent overlapping pages.
            self._soft_page_transition()
            self.root.update_idletasks()

    def update_dynamic(self):
        stage=self.ui.stage
        if stage=='partition_scanning' and 'partition_info' in self.dynamic:
            from src.views.partition_activity import partition_activity_display
            status = self.ui.partition_status
            elapsed = max(0, time.monotonic() - status.get('started', time.monotonic()))
            display = partition_activity_display(status, elapsed, sys.platform)
            self.dynamic['partition_clock'].config(text=display['clock'])
            self.dynamic['partition_read'].config(text=display['read_value'])
            self.dynamic['partition_read_detail'].config(text=display['read_detail'])
            self.dynamic['partition_log'].config(text=display['log_value'],
                                                 fg=AMBER if display['log_warn'] else TEAL)
            self.dynamic['partition_log_detail'].config(text=display['log_detail'])
            self.dynamic['partition_info'].config(text=scan_tip('partition', elapsed))
            self.dynamic['partition_caution'].config(text=display['caution'])
            return
        if stage in ('scanning','signature_running'):
            self._refresh_live()
        if 'number' not in self.dynamic:return
        status=self.ui.signature_status if stage=='signature_running' else self.ui.status
        elapsed=max(0,time.monotonic()-status.get('started',time.monotonic()))
        if stage=='signature_running':
            self.dynamic['number'].config(text=f"{status.get('files',0):,} files extracted")
            self.dynamic['secondary'].config(text=f"{human_size(status.get('written',0))} saved  ·  "
                                                   f"{human_size(status.get('free',0))} available  ·  {format_elapsed(elapsed)} elapsed")
            self.dynamic['info'].config(text=scan_tip('signature',elapsed))
        elif stage=='scanning':
            self.dynamic['number'].config(text=f"{status.get('count',0):,} entries found")
            self.dynamic['secondary'].config(text=f'{format_elapsed(elapsed)} elapsed  ·  Metadata scan')
            self.dynamic['info'].config(text=scan_tip('metadata',elapsed))
        elif stage=='unmounting':
            self.dynamic['number'].config(text='Preparing the volume…')
            self.dynamic['secondary'].config(text='Unmounting the selected source before access.')
        else:
            self.dynamic['number'].config(text=f"{status.get('done',0):,} / {status.get('total',0):,} processed")
            self.dynamic['secondary'].config(text=f"{human_size(status.get('written',0))} saved")

    def pick_source_image(self, title='Open disk image', filetypes=None):
        """One non-reentrant native picker for source and resumable images."""
        if filetypes is None:
            filetypes=[('Disk images','*.img *.dd *.raw *.bin'),('All files','*')]
        def pick():
            parent=str(Path.home())
            if sys.platform.startswith('linux'):
                command=None
                if shutil.which('zenity'):
                    command=['zenity','--file-selection','--title='+title,
                             '--filename='+parent+'/',
                             '--file-filter='+filetypes[0][0]+' | '+filetypes[0][1].replace(' ', ' '),
                             '--file-filter=All files | *']
                elif shutil.which('kdialog'):
                    command=['kdialog','--getopenfilename',parent,
                             filetypes[0][0]+' ('+filetypes[0][1]+');;All files (*)',
                             '--title',title]
                if command:
                    try:
                        result=subprocess.run(command,text=True,capture_output=True,
                                              timeout=300,check=False)
                        if result.returncode in (0,1):
                            return result.stdout.strip() if result.returncode==0 else ''
                    except (OSError,subprocess.TimeoutExpired):
                        pass
            return filedialog.askopenfilename(parent=self.root,title=title,filetypes=filetypes)
        return self._single_native_picker(pick)

    def open_image(self):
        value=self.pick_source_image()
        if not value:return
        try:self.ui.add_image(value)
        except (ValueError,OSError) as exc:self.show_modal_notice('Filesystem image',str(exc),warning=True);return
        self.show(force=True)

    def _sort_heading(self, tree, col, caption, live=False, anchor='w'):
        sort=self._live_sort if live else getattr(self.ui,'desktop_sort','id')
        desc=self._live_sort_desc if live else getattr(self.ui,'desktop_sort_desc',False)
        label=caption+('  ↓' if desc else '  ↑') if ('path' if col=='name' else col)==sort else caption
        tree.heading(col,text=label,anchor=anchor,
                     command=lambda c=col,l=live:self.sort_table(c,l))

    def sort_table(self, column, live=False):
        if column not in ('path','name','type','size'):return
        column='path' if column=='name' else column
        if live:
            self._live_sort_desc=(not self._live_sort_desc if self._live_sort==column else False)
            self._live_sort=column
            self._live_page=0
            for col,title in (('name','FILE'),('type','TYPE'),('size','SIZE')):
                self._sort_heading(self.live_tree,col,title,live=True,anchor='center')
            self._refresh_live(force=True)
        else:
            old=getattr(self.ui,'desktop_sort','id')
            self.ui.desktop_sort_desc=(not getattr(self.ui,'desktop_sort_desc',False)
                                       if old==column else False)
            self.ui.desktop_sort=column
            self._page=0
            self.ui.result_index=0
            self.show(force=True)

    def toggle_image_mount(self):
        # Explicit, desktop-only UDisks2 mount, never part of a recovery scan.
        if self._image_mount_busy:return
        from src.infrastructure.image_mount import ImageMountSession, ImageMountError
        if self._image_mount and self._image_mount.loop and (self._image_mount.image == Path((self.ui.device or {}).get('path',self._image_mount.image)).resolve()):
            self._image_mount_busy=True
            try:self._image_mount.unmount()
            except ImageMountError as exc:
                self.show_modal_notice('Could not unmount image', str(exc), warning=True)
            finally:
                if self._image_mount and not self._image_mount.loop:self._image_mount=None
                self._image_mount_busy=False
                self.show(force=True)
            return
        dev=self.ui.device or {}
        if not dev.get('is_image'):return
        if self._image_mount and self._image_mount.loop:
            self.show_modal_notice('Another image is mounted',
                'Unmount the previous image before mounting a different one. '
                'Return to its method page and choose Unmount image.', warning=True)
            return
        if not self.confirm_action('Mount disk image read-only?',
            'This will ask Linux to mount recognized filesystems through UDisks2 in READ-ONLY mode. '
            'You can inspect existing files in your file manager; deleted files may not appear. '
            'For unknown or damaged filesystems use Sleuth/PhotoRec instead. '
            'Malformed filesystems still carry kernel-level risks; use trusted images.',
            'Mount read-only'):
            return
        self._image_mount_busy=True
        if hasattr(self,'_mount_action_button'):
            self.set_button_enabled(self._mount_action_button,False)
        def work():
            session=None
            try:
                session=ImageMountSession(dev['path'])
                folders,failures=session.mount()
                error=None
            except (ImageMountError,OSError) as exc:
                folders,failures,error=[],[],str(exc)
            def done():
                if self._closed:
                    # A completed mount must not survive a window closed mid-job.
                    if session and session.loop:
                        try:session.unmount()
                        except ImageMountError:pass
                    return
                if error:
                    self.show_modal_notice('Mount not available', error, warning=True)
                else:
                    self._image_mount=session
                    summary='Mounted read-only:\n'+'\n'.join(folders)
                    if failures:summary+='\n\nOther partitions could not mount:\n'+'\n'.join(failures)
                    self.show_modal_notice('Image mounted', summary)
                    if folders:self._open_folder(Path(folders[0]))
                self._image_mount_busy=False
                self.show(force=True)
            try:self.root.after(0,done)
            except (RuntimeError,tk.TclError):
                if session and session.loop:
                    try:session.unmount()
                    except ImageMountError:pass
        threading.Thread(target=work,daemon=True,name='sectorrec-image-mount').start()

    def on_key(self,event):
        if isinstance(event.widget,(ttk.Combobox,tk.Entry,ttk.Treeview,tk.Button)):
            return
        mapped={'Escape':'BACK','Return':'OK','F5':'REFRESH','Up':'UP','Down':'DOWN',
                'f':'UP','x':'DOWN','z':'LEFT','c':'RIGHT','a':'ALL'}
        key=mapped.get(event.keysym)
        if key:
            self.dispatch(key)
            return 'break'

    def tick(self):
        if self._closed:return
        # Resume the same selected device after a single, explicit UAC prompt.
        # Never invent a new device or silently switch to a different disk.
        if self._resume_device and self.ui.devices:
            for index, disk in enumerate(self.ui.devices):
                if disk.get('path','').casefold() == self._resume_device.casefold():
                    self.ui.selection=index
                    self.ui.handle('OK')
                    self._resume_device=None
                    self.show(force=True)
                    break
        before=self.ui.stage
        old_key=(tuple((dev.get('path'),dev.get('mounted')) for dev in self.ui.devices),self.ui.selection)
        self.ui.tick()
        if self.ui.stage!=before:
            self.show(force=True)
        elif self.ui.stage=='desktop_methods':
            self._update_drive_summary()
        elif self.ui.stage=='devices':
            # Physical hotplug refreshes only the device rows, not the entire
            # page; a selection change updates highlight without rebuilding.
            if (self.device_snapshot(self.ui.devices)!=self._device_snapshot or
                    self.ui.device_refresh_completed!=getattr(self,'_device_refresh_rendered',None)):
                self._render_devices()
                self._paint_device_selection()
            elif self.ui.selection!=old_key[1]:
                self._paint_device_selection()
        elif self.ui.stage in ('scanning','signature_running','partition_scanning','unmounting','recovering'):
            self.update_dynamic()
        self._tick_after_id=self.root.after(125,self.tick)

    def close(self):
        if self._closed:return
        job=self._imaging_job
        if job and job.is_alive():
            if not getattr(self, '_imaging_exit_confirmed', False):
                if not self.confirm_action('Stop disk imaging?',
                    'The current image and progress map will be kept for later resumption. '
                    'SectorRec will stop preparation or request a clean stop of ddrescue.', 'Stop and exit'):
                    return
                self._imaging_exit_confirmed=True
            job.cancel()
            # Keep Tk alive while the preparation thread is still acquiring or
            # releasing its block-device guard. No worker can start after the
            # window is destroyed, even if close occurs before unmount finishes.
            self.root.after(100, self.close)
            return
        if self._image_mount_busy:
            self.show_modal_notice('Mount operation running',
                'Wait for the read-only mount operation to finish before exiting.', warning=True)
            return
        if self._image_mount and self._image_mount.loop:
            if not self.confirm_action('Unmount image before exit?',
                'SectorRec will unmount and detach the virtual image before exiting. '
                'Close any open file manager window for this image first.', 'Unmount and exit'):
                return
            from src.infrastructure.image_mount import ImageMountError
            try:self._image_mount.unmount()
            except ImageMountError as exc:
                self.show_modal_notice('Unmount failed', str(exc), warning=True)
                return
        if self.ui.stage in ('scanning','partition_scanning','signature_running','recovering','unmounting'):
            if not self.confirm_action('Exit SectorRec?',
                  'An operation is still running. Closing SectorRec will stop it. '
                  'A metadata scan may leave a partial catalog; already saved files are not deleted.',
                  'Stop and exit'):
                return
        self._closed=True
        previous_bar = self.dynamic.get('partition')
        if previous_bar is not None:
            try: previous_bar.stop()
            except tk.TclError: pass
        for after_id in self._fade_after_ids:
            try:self.root.after_cancel(after_id)
            except tk.TclError:pass
        if getattr(self,'_tick_after_id',None) is not None:
            self.root.after_cancel(self._tick_after_id)
            self._tick_after_id=None
        self.ui.close()
        self._destroy_when_workers_stop()

    def _destroy_when_workers_stop(self):
        """Do not tear down the GUI until background catalog cleanup completes."""
        reaper=getattr(self.ui, '_close_reaper', None)
        if reaper is not None and reaper.is_alive():
            self.root.after(100, self._destroy_when_workers_stop)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()
