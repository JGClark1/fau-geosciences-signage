#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
from pathlib import Path


ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
SITE_DIRECTORY = ROOT_DIRECTORY / "site"

PLACEHOLDER = "__BUILD_TOKEN__"


def main() -> int:
    if not SITE_DIRECTORY.exists():
        raise FileNotFoundError(
            "The site directory does not exist."
        )

    build_token = dt.datetime.now(
        dt.timezone.utc
    ).strftime("%Y%m%d%H%M%S")

    replacements = 0

    for html_path in SITE_DIRECTORY.rglob("*.html"):
        content = html_path.read_text(
            encoding="utf-8"
        )

        if PLACEHOLDER not in content:
            continue

        updated = content.replace(
            PLACEHOLDER,
            build_token,
        )

        html_path.write_text(
            updated,
            encoding="utf-8",
        )

        replacements += 1

    print(
        f"Finalized {replacements} HTML file(s) "
        f"with build token {build_token}."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
