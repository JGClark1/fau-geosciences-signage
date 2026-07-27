from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
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

ARCHIVE_URL = (
    "https://science.nasa.gov/earth/"
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
    """Extract Open Graph metadata from a NASA article."""

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


class ArchiveLinkParser(HTMLParser):
    """Collect Earth Observatory article links from the archive."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        if tag.lower() != "a":
            return

        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }

        href = attributes.get("href", "").strip()

        if not href:
            return

        absolute_url = urllib.parse.urljoin(
            self.base_url,
            href,
        )

        parsed = urllib.parse.urlparse(absolute_url)
        path = parsed.path.rstrip("/") + "/"

        if EARTH_OBSERVATORY_PATH not in path:
            return

        excluded_endings = {
            "/earth/earth-observatory/",
            "/earth/earth-observatory/image-of-the-day/",
            "/earth/earth-observatory/subscribe/",
            "/earth/earth-observatory/subscribe/feeds/",
            "/earth/earth-observatory/explorer/",
        }

        if path in excluded_endings:
            return

        normalized_url = urllib.parse.urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or "science.nasa.gov",
                path,
                "",
                "",
                "",
            )
        )

        if normalized_url not in self.links:
            self.links.append(normalized_url)


def download_text(url: str) -> str:
    """Download a webpage or RSS feed as text."""

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
        encoding = (
            response.headers.get_content_charset()
            or "utf-8"
        )

        return response.read().decode(
            encoding,
            errors="replace",
        )


def clean_text(value: Optional[str]) -> str:
    """Convert encoded or HTML text into clean plain text."""

    if not value:
        return ""

    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def remove_nasa_boilerplate(value: str) -> str:
    """Remove the automatic WordPress footer from summaries."""

    cleaned = re.sub(
        r"\s*The post .*? appeared first on NASA Science\s*\.?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return cleaned.strip()


def local_name(tag: str) -> str:
    """Remove an XML namespace from an element name."""

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


def format_rss_date(raw_date: str) -> tuple[str, str]:
    """Convert an RSS date into ISO and display forms."""

    parsed = parsedate_to_datetime(raw_date)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    parsed = parsed.astimezone(timezone.utc)

    return (
        parsed.date().isoformat(),
        parsed.strftime("%B %-d, %Y"),
    )


def parse_iso_date(value: str) -> Optional[datetime]:
    """Parse an ISO date or datetime from article metadata."""

    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def extract_article_metadata(
    article_url: str,
) -> dict[str, str]:
    """Read title, image, description, and date from an article."""

    article_html = download_text(article_url)

    parser = ArticleMetadataParser()
    parser.feed(article_html)

    metadata = parser.metadata

    image_url = (
        metadata.get("og:image")
        or metadata.get("twitter:image")
        or ""
    ).strip()

    title = clean_text(
        metadata.get("og:title")
        or metadata.get("twitter:title")
        or ""
    )

    description = clean_text(
        metadata.get("og:description")
        or metadata.get("description")
        or metadata.get("twitter:description")
        or ""
    )

    image_alt = clean_text(
        metadata.get("og:image:alt")
        or metadata.get("twitter:image:alt")
        or ""
    )

    raw_date = (
        metadata.get("article:published_time")
        or metadata.get("date")
        or metadata.get("datepublished")
        or ""
    )

    parsed_date = parse_iso_date(raw_date)

    publication_date = ""
    display_date = ""

    if parsed_date is not None:
        publication_date = parsed_date.date().isoformat()
        display_date = parsed_date.strftime("%B %-d, %Y")

    return {
        "title": title,
        "description": description,
        "image_url": image_url,
        "image_alt": image_alt,
        "publication_date": publication_date,
        "display_date": display_date,
    }


def make_record(
    *,
    article_url: str,
    fallback_title: str = "",
    fallback_description: str = "",
    fallback_date: str = "",
) -> Optional[dict[str, str]]:
    """Create one complete slideshow record."""

    print(f"Reading: {fallback_title or article_url}")
    print(f"  {article_url}")

    metadata = extract_article_metadata(article_url)

    image_url = metadata["image_url"]

    if not image_url:
        print("  Skipped: no primary image found.")
        return None

    title = metadata["title"] or fallback_title

    description = (
        metadata["description"]
        or remove_nasa_boilerplate(fallback_description)
    )

    publication_date = metadata["publication_date"]
    display_date = metadata["display_date"]

    if not publication_date and fallback_date:
        publication_date, display_date = format_rss_date(
            fallback_date
        )

    if not title or not publication_date:
        print("  Skipped: title or publication date missing.")
        return None

    return {
        "title": title,
        "publication_date": publication_date,
        "display_date": display_date,
        "description": description,
        "image_url": image_url,
        "image_alt": metadata["image_alt"] or title,
        "article_url": article_url,
        "source": "NASA Earth Observatory",
    }


def collect_feed_records(
    feed_xml: str,
) -> list[dict[str, str]]:
    """Collect qualifying records from NASA's mixed RSS feed."""

    root = ET.fromstring(feed_xml)

    items = [
        element
        for element in root.iter()
        if local_name(element.tag) in {"item", "entry"}
    ]

    records: list[dict[str, str]] = []

    for item in items:
        article_url = extract_article_link(item)

        if EARTH_OBSERVATORY_PATH not in article_url:
            continue

        title = first_child_text(item, {"title"})

        raw_date = first_child_text(
            item,
            {
                "pubdate",
                "published",
                "updated",
                "date",
            },
        )

        description = first_child_text(
            item,
            {
                "description",
                "summary",
                "encoded",
                "content",
            },
        )

        record = make_record(
            article_url=article_url,
            fallback_title=title,
            fallback_description=description,
            fallback_date=raw_date,
        )

        if record is not None:
            records.append(record)

    return records


def collect_archive_links() -> list[str]:
    """Collect article links from the IOTD archive page."""

    archive_html = download_text(ARCHIVE_URL)

    parser = ArchiveLinkParser(ARCHIVE_URL)
    parser.feed(archive_html)

    return parser.links


def supplement_from_archive(
    records: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Add older archive entries until seven records exist."""

    existing_urls = {
        record["article_url"]
        for record in records
    }

    for article_url in collect_archive_links():
        if len(records) >= NUMBER_OF_ITEMS:
            break

        if article_url in existing_urls:
            continue

        record = make_record(
            article_url=article_url,
        )

        if record is None:
            continue

        records.append(record)
        existing_urls.add(article_url)

    return records


def sort_and_trim(
    records: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Deduplicate, sort newest first, and keep seven."""

    unique: dict[str, dict[str, str]] = {}

    for record in records:
        unique[record["article_url"]] = record

    sorted_records = sorted(
        unique.values(),
        key=lambda record: record["publication_date"],
        reverse=True,
    )

    return sorted_records[:NUMBER_OF_ITEMS]


def write_json(records: list[dict[str, str]]) -> None:
    """Write the clean slideshow dataset."""

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
        "source_archive": ARCHIVE_URL,
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
        records = collect_feed_records(feed_xml)

        print()
        print(
            f"Feed supplied {len(records)} qualifying entries."
        )

        if len(records) < NUMBER_OF_ITEMS:
            print(
                "Checking the Image of the Day archive "
                "for additional entries…"
            )

            records = supplement_from_archive(records)

        records = sort_and_trim(records)

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
