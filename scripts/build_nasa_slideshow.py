from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional


FEED_URL = (
    "https://science.nasa.gov/feed/"
    "earth-observatory/image-of-the-day/"
)

OUTPUT_PATH = Path("data/nasa-earth-observatory.json")

NUMBER_OF_ITEMS = 7

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

EARTH_OBSERVATORY_PATH = "/earth/earth-observatory/"


class ArticleMetadataParser(HTMLParser):
    """Extract useful Open Graph metadata from a NASA article."""

    def __init__(self) -> None:
        super().__init__()

        self.metadata: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        if tag.lower() != "meta":
            return

        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }

        property_name = (
            attributes.get("property")
            or attributes.get("name")
            or ""
        ).lower()

        content = attributes.get("content", "").strip()

        if property_name and content:
            self.metadata[property_name] = html.unescape(content)


def download_text(url: str) -> str:
    """Download a UTF-8 webpage or feed."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/rss+xml,"
                "application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:
        encoding = response.headers.get_content_charset() or "utf-8"

        return response.read().decode(
            encoding,
            errors="replace",
        )


def clean_text(value: Optional[str]) -> str:
    """Convert HTML or encoded text into clean plain text."""

    if not value:
        return ""

    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def remove_nasa_boilerplate(value: str) -> str:
    """Remove the automatic WordPress sentence from feed summaries."""

    cleaned = re.sub(
        r"\s*The post .*? appeared first on NASA Science\s*\.?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return cleaned.strip()


def local_name(tag: str) -> str:
    """Remove any XML namespace from an element name."""

    return tag.split("}")[-1].lower()


def first_child_text(
    element: ET.Element,
    names: set[str],
) -> str:
    """Return text from the first matching direct child."""

    for child in element:
        if local_name(child.tag) in names:
            return clean_text(child.text)

    return ""


def extract_article_link(item: ET.Element) -> str:
    """Extract an article URL from an RSS or Atom entry."""

    for child in item:
        if local_name(child.tag) != "link":
            continue

        if child.text and child.text.strip():
            return child.text.strip()

        href = child.attrib.get("href", "").strip()

        if href:
            return href

    return ""


def format_publication_date(raw_date: str) -> tuple[str, str]:
    """
    Return an ISO date and a display-friendly date.

    Example:
    2026-07-27
    July 27, 2026
    """

    parsed = parsedate_to_datetime(raw_date)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    parsed = parsed.astimezone(timezone.utc)

    return (
        parsed.date().isoformat(),
        parsed.strftime("%B %-d, %Y"),
    )


def extract_article_metadata(
    article_url: str,
) -> dict[str, str]:
    """Read the article's primary image and description."""

    article_html = download_text(article_url)

    parser = ArticleMetadataParser()
    parser.feed(article_html)

    image_url = (
        parser.metadata.get("og:image")
        or parser.metadata.get("twitter:image")
        or ""
    )

    description = (
        parser.metadata.get("og:description")
        or parser.metadata.get("description")
        or parser.metadata.get("twitter:description")
        or ""
    )

    image_alt = (
        parser.metadata.get("og:image:alt")
        or parser.metadata.get("twitter:image:alt")
        or ""
    )

    return {
        "image_url": image_url.strip(),
        "description": clean_text(description),
        "image_alt": clean_text(image_alt),
    }


def collect_feed_entries(feed_xml: str) -> list[dict[str, str]]:
    """Collect Earth Observatory records from NASA's mixed feed."""

    root = ET.fromstring(feed_xml)

    items = [
        element
        for element in root.iter()
        if local_name(element.tag) in {"item", "entry"}
    ]

    records: list[dict[str, str]] = []

    for item in items:
        title = first_child_text(item, {"title"})
        article_url = extract_article_link(item)

        if EARTH_OBSERVATORY_PATH not in article_url:
            continue

        raw_date = first_child_text(
            item,
            {
                "pubdate",
                "published",
                "updated",
                "date",
            },
        )

        feed_description = first_child_text(
            item,
            {
                "description",
                "summary",
                "encoded",
                "content",
            },
        )

        if not title or not article_url or not raw_date:
            continue

        iso_date, display_date = format_publication_date(raw_date)

        print(f"Reading: {title}")
        print(f"  {article_url}")

        metadata = extract_article_metadata(article_url)

        image_url = metadata["image_url"]

        if not image_url:
            print("  Skipped: no primary image found.")
            continue

        description = (
            metadata["description"]
            or remove_nasa_boilerplate(feed_description)
        )

        records.append(
            {
                "title": title,
                "publication_date": iso_date,
                "display_date": display_date,
                "description": description,
                "image_url": image_url,
                "image_alt": (
                    metadata["image_alt"]
                    or title
                ),
                "article_url": article_url,
                "source": "NASA Earth Observatory",
            }
        )

        if len(records) == NUMBER_OF_ITEMS:
            break

    return records


def write_json(records: list[dict[str, str]]) -> None:
    """Write the slideshow records to the repository."""

    if len(records) < NUMBER_OF_ITEMS:
        raise RuntimeError(
            f"Only {len(records)} qualifying Earth Observatory "
            f"entries were found; {NUMBER_OF_ITEMS} are required."
        )

    payload = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "source_feed": FEED_URL,
        "item_count": len(records),
        "items": records,
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        f"Wrote {len(records)} entries to "
        f"{OUTPUT_PATH}"
    )


def main() -> int:
    try:
        print("Downloading NASA Earth Observatory feed…")

        feed_xml = download_text(FEED_URL)
        records = collect_feed_entries(feed_xml)

        write_json(records)

        print()
        print("Newest slideshow entries:")

        for index, record in enumerate(
            records,
            start=1,
        ):
            print(
                f"{index}. {record['title']} "
                f"({record['display_date']})"
            )

        return 0

    except Exception as error:
        print(
            f"NASA slideshow update failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
