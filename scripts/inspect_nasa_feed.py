from __future__ import annotations

import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional


FEED_URL = (
    "https://science.nasa.gov/feed/"
    "earth-observatory/image-of-the-day/"
)

NUMBER_OF_ITEMS = 7

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory feed inspection)"
)


def clean_html(value: Optional[str]) -> str:
    """Convert basic HTML content into readable plain text."""
    if not value:
        return ""

    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def shorten(value: str, limit: int = 300) -> str:
    """Shorten long text for readable workflow logs."""
    if len(value) <= limit:
        return value

    return value[: limit - 1].rstrip() + "…"


def child_text(
    element: ET.Element,
    names: list[str],
) -> str:
    """Return the text from the first matching child element."""
    for child in element:
        local_name = child.tag.split("}")[-1].lower()

        if local_name in names:
            return clean_html(child.text)

    return ""


def find_image_urls(element: ET.Element) -> list[str]:
    """Collect likely image URLs contained in an RSS item."""
    urls: list[str] = []

    for child in element.iter():
        local_name = child.tag.split("}")[-1].lower()

        if local_name in {
            "enclosure",
            "content",
            "thumbnail",
        }:
            url = child.attrib.get("url", "").strip()

            if url:
                urls.append(url)

        for attribute_name in ("href", "src"):
            url = child.attrib.get(attribute_name, "").strip()

            if url and re.search(
                r"\.(?:jpg|jpeg|png|webp)(?:\?|$)",
                url,
                flags=re.IGNORECASE,
            ):
                urls.append(url)

    combined_text = " ".join(
        part
        for part in element.itertext()
        if part
    )

    urls.extend(
        re.findall(
            r'https?://[^"\'\s<>]+'
            r'\.(?:jpg|jpeg|png|webp)'
            r'(?:\?[^"\'\s<>]*)?',
            combined_text,
            flags=re.IGNORECASE,
        )
    )

    unique_urls: list[str] = []

    for url in urls:
        url = html.unescape(url)

        if url not in unique_urls:
            unique_urls.append(url)

    return unique_urls


def download_feed() -> bytes:
    """Download the official NASA Earth Observatory RSS feed."""
    request = urllib.request.Request(
        FEED_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml,"
                "application/xml,"
                "text/xml;q=0.9,"
                "*/*;q=0.8"
            ),
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:
        print(f"HTTP status: {response.status}")
        print(
            "Content type:",
            response.headers.get("Content-Type", "unknown"),
        )
        print("Final URL:", response.geturl())

        return response.read()


def inspect_feed(xml_data: bytes) -> None:
    """Print the most useful fields from recent feed entries."""
    root = ET.fromstring(xml_data)

    entries = [
        element
        for element in root.iter()
        if element.tag.split("}")[-1].lower()
        in {"item", "entry"}
    ]

    if not entries:
        raise RuntimeError(
            "The feed did not contain any RSS items "
            "or Atom entries."
        )

    print()
    print(f"Entries detected: {len(entries)}")
    print(
        f"Inspecting newest {min(NUMBER_OF_ITEMS, len(entries))}:"
    )

    for index, entry in enumerate(
        entries[:NUMBER_OF_ITEMS],
        start=1,
    ):
        title = child_text(entry, ["title"])

        link = child_text(entry, ["link"])

        if not link:
            for child in entry:
                if child.tag.split("}")[-1].lower() == "link":
                    link = child.attrib.get("href", "").strip()

                    if link:
                        break

        date = child_text(
            entry,
            [
                "pubdate",
                "published",
                "updated",
                "date",
            ],
        )

        description = child_text(
            entry,
            [
                "description",
                "summary",
                "encoded",
                "content",
            ],
        )

        image_urls = find_image_urls(entry)

        print()
        print("=" * 78)
        print(f"ENTRY {index}")
        print("=" * 78)
        print("Title:", title or "[missing]")
        print("Date:", date or "[missing]")
        print("Article:", link or "[missing]")
        print(
            "Description:",
            shorten(description) or "[missing]",
        )

        if image_urls:
            print("Image candidates:")

            for image_url in image_urls:
                print(f"  - {image_url}")
        else:
            print("Image candidates: [none found]")


def main() -> int:
    try:
        xml_data = download_feed()
        inspect_feed(xml_data)
        return 0

    except Exception as error:
        print(
            f"NASA feed inspection failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
