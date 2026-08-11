#!/usr/bin/env python3

from __future__ import annotations

import shutil
from pathlib import Path


ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
SITE_DIRECTORY = ROOT_DIRECTORY / "site"

EXCLUDED_NAMES = {
    ".git",
    ".github",
    "scripts",
    "site",
    "__pycache__",
    "goes-build",
    "global-geostationary-build",
    "nasa-video-build",
}


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return True

    if path.name.endswith("-build"):
        return True

    return False


def main() -> int:
    if SITE_DIRECTORY.exists():
        shutil.rmtree(SITE_DIRECTORY)

    SITE_DIRECTORY.mkdir(parents=True)

    for item in ROOT_DIRECTORY.iterdir():
        if should_skip(item):
            continue

        destination = SITE_DIRECTORY / item.name

        if item.is_dir():
            shutil.copytree(
                item,
                destination,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                ),
            )
        else:
            shutil.copy2(
                item,
                destination,
            )

    print(
        "Prepared clean site directory "
        "from repository static content."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
