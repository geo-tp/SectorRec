#!/usr/bin/env python3
"""Render documentation artwork from the current native desktop UI and demo data.

Requires Python, Tkinter, Pillow and an X11 display (Xvfb is sufficient).
Run from any directory: xvfb-run -a /usr/bin/python3 docs/generate_previews.py
Use --output-root /tmp/sectorrec-previews to inspect exports before replacing them.
No device discovery, recovery engine or hardware diagnostic is run.
"""

import argparse
from contextlib import ExitStack
from io import BytesIO
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont, ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.controllers.controller import Controller
from src.services.device_diagnostics import DriveReport
from src.views.desktop_native import DesktopWindow

GIB = 1024**3
INK = '#20344D'
MUTED = '#52677D'
TEAL = '#15836D'
BLUE = '#2874BA'
AMBER = '#A46B15'
RED = '#AA4353'
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
IMAGE_FOLDER = '/media/Backups/Images'
IMAGE_PATH = IMAGE_FOLDER + '/photo-usb.img'
RECOVERY_FOLDER = '/media/Backups/Recovered'
WORKFLOW_SIZE = (1100, 760)


class PreviewPlatform:
    name = 'linux'
    raw_drives = True

    def devices(self):
        raise AssertionError('Documentation previews must not discover real devices')

    def unmount(self, _path):
        raise AssertionError('Documentation previews must not unmount devices')


def disk(label, path, size, transport, filesystems, **extra):
    return dict(label=label, path=path, name=Path(path).name, disk_path=path,
                size=int(size * GIB), fs='DISK', partition_disk=True,
                supported=False, transport=transport, mounted=False,
                child_filesystems=filesystems, **extra)


def volume(label, path, size, fs, parent, *, mounted=False):
    return dict(label=label, path=path, name=Path(path).name,
                disk_path=parent['path'], size=int(size * GIB), fs=fs,
                supported=True, partition_disk=False, mounted=mounted,
                transport=parent['transport'], is_system=parent.get('is_system', False))


def source_devices():
    usb = disk('Photo USB', '/dev/sdb', 64, 'USB', ['EXFAT', 'EXFAT'])
    backup = disk('Backup SSD', '/dev/sdc', 512, 'SATA', ['EXFAT'], media_type='ssd', model='Backup SSD')
    return [usb,
            volume('Photos', '/dev/sdb1', 48, 'EXFAT', usb),
            volume('Documents', '/dev/sdb2', 15, 'EXFAT', usb),
            backup,
            volume('Backups', '/dev/sdc1', 511, 'EXFAT', backup)]


def system_devices():
    usb = disk('Backup USB', '/dev/sdb', 128, 'USB', ['EXFAT'])
    system = disk('Computer SSD', '/dev/nvme0n1', 512, 'NVME', ['EXT4', 'EXT4'], is_system=True)
    return [usb,
            volume('Backups', '/dev/sdb1', 127, 'EXFAT', usb),
            system,
            volume('System', '/dev/nvme0n1p1', 503, 'EXT4', system, mounted=True),
            volume('Recovery', '/dev/nvme0n1p2', 8, 'EXT4', system)]


def image_devices():
    image = dict(path=IMAGE_PATH, label='photo-usb.img', name='photo-usb.img',
                 size=64 * GIB, fs='IMAGE', transport='IMAGE', mounted=False,
                 supported=True, is_image=True, partition_disk=True,
                 image_layout='partitioned')
    return [image, *source_devices()[3:]]


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def configure_scene(scene, ui, window, stack):
    """Inject deterministic data at the I/O boundary, then use normal UI actions."""
    if scene == 'methods':
        ui.stage = 'desktop_methods'
        window.show(force=True)
        window.select_method('metadata')
    elif scene == 'image-setup':
        window._image_source = dict(ui.device)
        ui.stage = 'desktop_image_setup'
        stack.enter_context(patch.object(window, 'pick_destination_directory', return_value=IMAGE_FOLDER))
        stack.enter_context(patch('src.services.image_rescue.plan_image', return_value=Path(IMAGE_PATH)))
        stack.enter_context(patch('src.views.desktop_native.shutil.disk_usage',
                                  return_value=SimpleNamespace(total=511 * GIB, used=111 * GIB, free=400 * GIB)))
        window.show(force=True)
        # Run the actual folder-selection callback with a fictional preflight.
        browse = [widget for widget in descendants(window.body)
                  if widget.winfo_class() == 'Button' and widget.cget('text') == 'Browse…']
        assert len(browse) == 1
        browse[0].invoke()
    elif scene == 'results':
        ui.demo_results = [
            (1, '101', 'Photos/Beach/holiday.jpg', 3 * 1024**2, 'PHOTOS'),
            (2, '102', 'Photos/Beach/sunset.jpg', 4 * 1024**2, 'PHOTOS'),
            (3, '103', 'Photos/Family/birthday.jpg', 5 * 1024**2, 'PHOTOS'),
            (4, '104', 'Photos/Family/picnic.jpg', 2 * 1024**2, 'PHOTOS'),
            (5, '105', 'Photos/Garden/flowers.png', 6 * 1024**2, 'PHOTOS'),
            (6, '106', 'Photos/Trip/mountains.jpg', 4 * 1024**2, 'PHOTOS'),
            (7, '107', 'Documents/travel-notes.pdf', 512 * 1024, 'DOCS'),
            (8, '108', 'Videos/beach.mp4', 32 * 1024**2, 'VIDEOS'),
        ]
        ui.metadata_destination = RECOVERY_FOLDER
        ui.build_result_filters()
        ui.category_filter_index = ui.category_options.index('PHOTOS')
        ui.rebuild_extensions()
        ui.stage = 'results'
        window.show(force=True)
    elif scene == 'signature-setup':
        ui.engine = 'signature'
        ui.signature_destination = RECOVERY_FOLDER
        ui.signature_format = 'all'
        stack.enter_context(patch.object(ui, 'signature_preflight',
                                         return_value={'free': 400 * GIB, 'source_bytes': 48 * GIB, 'low': False}))
        ui.stage = 'signature_setup'
        window.show(force=True)


def capture(devices, selection, *, scene='sources', size=None):
    """Capture real Tk widgets; only the input data and window size are set here."""
    ui = Controller(demo=True, desktop=True, platform=PreviewPlatform())
    ui.devices = devices
    ui.selection = selection
    size = size or ((1210, 865) if scene == 'methods' else (960, 820))
    window = None
    try:
        with ExitStack() as stack:
            # A fixed unavailable SMART report keeps the preview deterministic.
            stack.enter_context(patch.object(ui, 'request_diagnostics', return_value=None))
            stack.enter_context(patch.object(DesktopWindow, 'default_window_size', return_value=size))
            # Fail closed if future UI changes introduce a native tool invocation.
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No native tools in documentation previews')))
            ui.diagnostic_report = DriveReport(model=devices[selection]['label'], health='unavailable')
            window = DesktopWindow(ui)
            callback_errors = []
            window.root.report_callback_exception = lambda kind, value, trace: callback_errors.append(value)
            window.root.after_cancel(window._tick_after_id)
            window.root.overrideredirect(True)
            window.root.geometry(f'{size[0]}x{size[1]}+0+0')
            configure_scene(scene, ui, window, stack)
            window.root.update()
            window.root.update_idletasks()
            assert not callback_errors, callback_errors
            x, y = window.root.winfo_rootx(), window.root.winfo_rooty()
            rows = [(row.winfo_rootx() - x, row.winfo_rooty() - y,
                     row.winfo_width(), row.winfo_height())
                    for row, _ in window._device_rows] if scene == 'sources' else []
            if rows:
                bottom = window.device_canvas.winfo_rooty() - y + window.device_canvas.winfo_height()
                assert rows[-1][1] + rows[-1][3] <= bottom, 'Source list must fit without scrolling'
            image = ImageGrab.grab(bbox=(x, y, x + size[0], y + size[1]),
                                   xdisplay=window.root.winfo_screen()).convert('RGB')
            assert image.size == size
            return image, rows
    finally:
        if window is not None:
            window.close()
        else:
            ui.close()


def font(size, bold=False):
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    return ImageFont.truetype(str(FONT_DIR / name), size)


def wrapped(draw, text, x, y, width, *, size=20, fill=INK, bold=False, gap=7):
    face = font(size, bold)
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if line and draw.textlength(candidate, font=face) > width:
                draw.text((x, y), line, font=face, fill=fill)
                y += size + gap
                line = word
            else:
                line = candidate
        draw.text((x, y), line, font=face, fill=fill)
        y += size + gap
    return y


def card(draw, x, y, width, title, body, color, background):
    # Calculate the box using the same wrapping as the painted text.
    scratch = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    end = wrapped(scratch, title, x + 17, y + 16, width - 34, size=22, bold=True)
    end = wrapped(scratch, body, x + 17, end + 10, width - 34)
    assert end + 14 <= 960, f'Annotation reaches the footer: {title}'
    draw.rounded_rectangle((x, y, x + width, end + 14), radius=12, fill=background, outline=color, width=2)
    title_end = wrapped(draw, title, x + 17, y + 16, width - 34, size=22, bold=True, fill=color)
    wrapped(draw, body, x + 17, title_end + 10, width - 34)
    return end + 14


def highlight(draw, row, color, *, offset=(288, 128)):
    x, y, width, height = row
    x += offset[0]
    y += offset[1]
    draw.rounded_rectangle((x - 3, y - 3, x + width + 2, y + height + 2), radius=7, outline=color, width=3)
    return x, y, x + width, y + height


def connector(draw, start, end, color):
    middle = (start[0] + end[0]) // 2
    draw.line([start, (middle, start[1]), (middle, end[1]), end], fill=color, width=3)
    x, y = end
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)


def canvas(screenshot, title, subtitle):
    image = Image.new('RGB', (1536, 1024), '#FFFFFF')
    draw = ImageDraw.Draw(image)
    draw.text((32, 24), title, fill=INK, font=font(34, True))
    draw.text((32, 76), subtitle, fill=MUTED, font=font(20))
    draw.rounded_rectangle((283, 123, 1253, 953), radius=12, fill='#E3EAF1')
    image.paste(screenshot, (288, 128))
    draw.text((32, 981), 'Actual SectorRec interface · Fictional devices and capacities · No real disk scanned',
              font=font(18), fill=MUTED)
    return image, draw


def disk_diagram(screenshot, rows):
    image, draw = canvas(screenshot, 'Disks and partitions',
                         'Choose the physical device or one of its volumes, depending on what you need to recover.')
    whole = highlight(draw, rows[0], TEAL)
    part = highlight(draw, rows[1], BLUE)
    card(draw, 32, 244, 224, '1  Whole disk',
         'Photo USB is the entire 64 GB device.\nSelect it to look for missing partitions or scan file signatures across the disk.',
         TEAL, '#EDF9F4')
    connector(draw, (256, 284), (whole[0] - 4, (whole[1] + whole[3]) // 2), TEAL)
    card(draw, 32, 553, 224, '2  Partition / volume',
         'Photos is one 48 GB volume inside Photo USB.\nSelect it to search its deleted-file records.',
         BLUE, '#EFF6FE')
    connector(draw, (256, 602), (part[0] - 4, (part[1] + part[3]) // 2), BLUE)
    top = 128 + rows[1][1]
    bottom = 128 + rows[2][1] + rows[2][3]
    draw.line([(1232, top), (1240, top), (1240, bottom), (1232, bottom)], fill=AMBER, width=3)
    card(draw, 1280, 385, 224, 'One disk, two volumes',
         'Photos and Documents belong to the same USB device.\nA different partition is not a separate physical disk.',
         AMBER, '#FFFAED')
    connector(draw, (1280, 440), (1240, (top + bottom) // 2), AMBER)
    card(draw, 1280, 714, 224, 'Separate backup',
         'Backup SSD is a separate device. Save recovered files to its Backups volume.',
         TEAL, '#EDF9F4')
    return image


def system_diagram(screenshot, rows):
    image, draw = canvas(screenshot, 'Recovering from a system disk',
                         'The operating system and its partitions share one physical device. Save recovered files elsewhere.')
    backup = highlight(draw, rows[1], TEAL)
    system = highlight(draw, rows[2], AMBER)
    card(draw, 32, 275, 224, 'Separate device',
         'Backup USB is a different physical disk.\nIts Backups volume can hold recovered files and the scan catalogue.',
         TEAL, '#EDF9F4')
    connector(draw, (256, 337), (backup[0] - 4, (backup[1] + backup[3]) // 2), TEAL)
    card(draw, 32, 618, 224, 'One physical disk',
         'System and Recovery are volumes on Computer SSD.\nSave recovered files to another device, never to either volume.',
         AMBER, '#FFFAED')
    card(draw, 1280, 238, 224, 'Live system',
         'The OS can overwrite deleted data while running. Files may change during a scan.',
         RED, '#FFF1F3')
    card(draw, 1280, 477, 224, 'SYSTEM DISK',
         'Computer SSD and its volumes share this badge.\nSectorRec only reads them. Active system volumes stay mounted.',
         AMBER, '#FFFAED')
    connector(draw, (1280, 520), (system[2] + 4, (system[1] + system[3]) // 2), AMBER)
    card(draw, 1280, 775, 224, 'Best approach',
         'Use a bootable USB or connect the source disk to another computer.',
         BLUE, '#EFF6FE')
    return image


def web_variants(image, name, assets):
    """Keep native text lossless; choose the smaller encoding for downsampled views."""
    sizes = []
    for width in (480, 800, image.width):
        height = round(image.height * width / image.width)
        variant = image.resize((width, height), Image.Resampling.LANCZOS)
        lossless = BytesIO()
        variant.save(lossless, format='WEBP', lossless=True, method=6)
        encoded = lossless.getvalue()
        if width < image.width:
            lossy = BytesIO()
            variant.save(lossy, format='WEBP', quality=90, method=6)
            if len(lossy.getvalue()) < len(encoded):
                encoded = lossy.getvalue()
        filename = f'{name}.webp' if width == image.width else f'{name}-{width}.webp'
        (assets / filename).write_bytes(encoded)
        sizes.append(f'{width}px: {len(encoded) / 1024:.1f} KiB')
    print(f'{name}: ' + ', '.join(sizes))


def export(root):
    images = root / 'docs' / 'images'
    assets = root / 'pages' / 'assets'
    images.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    methods, _ = capture(source_devices(), 1, scene='methods')
    sources, source_rows = capture(source_devices(), 1)
    system, system_rows = capture(system_devices(), 3)
    methods.save(images / 'desktop-methods.png', optimize=True)
    web_variants(methods, 'desktop-methods', assets)
    sources.save(images / 'desktop-sources.png', optimize=True)
    system.save(images / 'desktop-system.png', optimize=True)
    for name, web_name, artwork in (
        ('disks', 'disk-and-partition', disk_diagram(sources, source_rows)),
        ('system-disks', 'system-disk-recovery', system_diagram(system, system_rows)),
    ):
        artwork.save(images / f'{name}.png', optimize=True)
        small = artwork.resize((1000, 667), Image.Resampling.LANCZOS)
        small.save(images / f'{name}_s.jpg', quality=94, subsampling=0, optimize=True)
        if name == 'system-disks':
            artwork.save(images / f'{name}.jpg', quality=94, subsampling=0, optimize=True)
        for width in (768, 1536):
            variant = artwork.resize((width, width * 2 // 3), Image.Resampling.LANCZOS)
            variant.save(assets / f'{web_name}-{width}.webp', quality=92, method=6)
    for name, scene, devices, selection in (
        ('desktop-image-setup', 'image-setup', source_devices(), 0),
        ('desktop-image-source', 'sources', image_devices(), 0),
        ('desktop-results', 'results', source_devices(), 1),
        ('desktop-signature-setup', 'signature-setup', source_devices(), 1),
    ):
        screenshot, _ = capture(devices, selection, scene=scene, size=WORKFLOW_SIZE)
        screenshot.save(images / f'{name}.png', optimize=True)
        web_variants(screenshot, name, assets)
    print(f'Generated 7 documentation visuals and their exports under {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT)
    parser.add_argument('--display', help='X11 display to capture, for example :97')
    args = parser.parse_args()
    if args.display:
        os.environ['DISPLAY'] = args.display
    export(args.output_root.resolve())
