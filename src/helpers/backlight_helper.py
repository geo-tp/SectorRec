#!/usr/bin/python3 -I
"""Restricted Cardputer backlight writer for root-owned sysfs nodes.

Only accepts an existing kernel backlight class name and a level within the
kernel-advertised range. Never accepts a path, shell command or device node.
"""
import os
import re
import sys
from pathlib import Path


def main():
    if os.geteuid()!=0 or len(sys.argv)!=3:
        return 2
    name, value=sys.argv[1:]
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',name) or not re.fullmatch(r'[0-9]{1,5}',value):
        return 2
    sysfs=Path('/sys/class/backlight')/name
    try:
        maximum=int((sysfs/'max_brightness').read_text().strip())
        requested=int(value)
        if not (1<=maximum<=65535 and 0<=requested<=maximum):
            return 2
        (sysfs/'brightness').write_text(str(requested))
        return 0
    except (OSError,ValueError):
        return 1


if __name__=='__main__':
    sys.exit(main())
