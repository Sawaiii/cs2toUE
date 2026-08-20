"""Raise the version number in the two places that hold it.

    python tools/bump_version.py 1.0.6

Doing this by hand through a shell one-liner kept breaking on quoting, and a half
applied bump publishes a release whose contents do not match its tag.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = (("cs2toue/__init__.py", r'__version__ = "(.+?)"', '__version__ = "{}"'),
         ("setup_app.py", r'VERSION = "(.+?)"', 'VERSION = "{}"'))


def current() -> str:
    text = (ROOT / FILES[0][0]).read_text(encoding="utf-8")
    return re.search(FILES[0][1], text).group(1)


def main():
    if len(sys.argv) < 2:
        print(f"current version: {current()}")
        print("usage: python tools/bump_version.py <new version>")
        return 0
    new = sys.argv[1].lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", new):
        print(f"not a version number: {new}")
        return 1
    for name, pattern, template in FILES:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        found = re.search(pattern, text)
        if not found:
            print(f"version line not found in {name}")
            return 1
        path.write_text(text.replace(found.group(0), template.format(new), 1),
                        encoding="utf-8")
        print(f"{name}: {found.group(1)} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
