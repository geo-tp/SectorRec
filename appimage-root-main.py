"""Fixed-operation root entrypoint for the self-contained AppImage.

Uses /usr/bin/python3 -I. Never runs the UI as root and never installs sudoers.
"""
import sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit('Expected root helper role and fixed operation')
    role = sys.argv[1]
    if role not in ('reader', 'photo', 'image'):
        raise SystemExit('Unsupported privileged operation')
    # The existing helpers read sys.argv in their normal installed-package form.
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    if role == 'reader':
        from src.helpers.reader import main as run
        return run()
    if role == 'photo':
        from src.helpers.photo_reader import run
        return run(sys.argv)
    from src.helpers.image_reader import main as run
    return run()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print('SectorRec privileged operation: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
