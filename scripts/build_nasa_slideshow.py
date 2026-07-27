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
ABSTRACT_WORD_LIMIT = 105

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

EARTH_OBSERVATORY_PATH = "/earth/earth-observatory/"
IOTD_IMAGE_PATH = "/eo/images/iotd/"

EXCLUDED_SLUGS = {
    "",
    "about",
    "about-the-eo",
    "blue-marble-next-generation",
    "collections",
    "contact-the-eo",
    "explorer",
    "feature-articles",
    "global-maps",
    "image-of-the-day",
    "search",
    "subscribe",
    "topics",
    "world-of-change",
}


class ArticleParser(HTMLParser):
    """Extract metadata, text blocks, and image addresses."""

    BLOCK_TAGS = {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
    }

    def __init__(self) -> None:
        super().__init__()

        self.metadata: dict[str, str] = {}
        self.blocks: list[tuple[str, str]] = []
        self.image_urls: list[str] = []

        self.current_tag: Optional[str] = None
        self.current_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()

        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }

        if tag == "meta":
            property_name = (
                attributes.get("property")
                or attributes.get("name")
                or ""
            ).lower()

            content = attributes.get("content", "").strip()

            if property_name and content:
                self.metadata[property_name] = html.unescape(
                    content
                )

        if tag == "img":
            for attribute_name in ("src", "data-src"):
                image_url = attributes.get(
                    attribute_name,
                    "",
                ).strip()

                if image_url:
                    self.image_urls.append(image_url)

            srcset = attributes.get("srcset", "")

            if srcset:
                for candidate in srcset.split(","):
                    image_url = candidate.strip().split(" ")[0]

                    if image_url:
                        self.image_urls.append(image_url)

        if tag in self.BLOCK_TAGS:
            self.current_tag = tag
            self.current_parts = []

    def handle_data(self, data: str) -> None:
        if self.current_tag:
            self.current_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag != self.current_tag:
            return

        text = clean_text(
            " ".join(self.current_parts)
        )

        if text:
            self.blocks.append(
                (
                    self.current_tag,
                    text,
                )
            )

        self.current_tag = None
        self.current_parts = []


class ArchiveLinkParser(HTMLParser):
    """Collect likely article links from the NASA archive."""

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

        if parsed.netloc not in {
            "",
            "science.nasa.gov",
            "www.science.nasa.gov",
        }:
            return

        path = parsed.path.rstrip("/") + "/"

        if EARTH_OBSERVATORY_PATH not in path:
            return

        relative_part = path.split(
            EARTH_OBSERVATORY_PATH,
            maxsplit=1,
        )[1].strip("/")

        path_parts = [
            part
            for part in relative_part.split("/")
            if part
        ]

        if len(path_parts) != 1:
            return

        slug = path_parts[0].lower()

        if slug in EXCLUDED_SLUGS:
            return

        normalized_url = (
            "https://science.nasa.gov"
            + path
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


def clean_title(value: str) -> str:
    """Remove NASA's site-name suffix from article titles."""

    return re.sub(
        r"\s*[-–—|]\s*NASA Science\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def remove_nasa_boilerplate(value: str) -> str:
    """Remove the automatic WordPress footer from summaries."""

    return re.sub(
        r"\s*The post .*? appeared first on NASA Science\s*\.?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


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


def word_count(value: str) -> int:
    """Return the number of words in a string."""

    return len(value.split())


def truncate_words(
    value: str,
    limit: int,
) -> str:
    """Limit text without cutting a word in half."""

    words = value.split()

    if len(words) <= limit:
        return value

    shortened = " ".join(words[:limit]).rstrip(
        " ,;:"
    )

    final_punctuation = shortened[-1:]

    if final_punctuation in {".", "!", "?"}:
        return shortened

    return shortened + "…"


def looks_like_story_paragraph(value: str) -> bool:
    """Reject captions, credits, navigation, and boilerplate."""

    if word_count(value) < 18:
        return False

    lowered = value.lower()

    rejected_starts = (
        "accessed ",
        "image of the day",
        "image:",
        "jpeg",
        "nasa earth observatory image",
        "nasa earth observatory images",
        "references",
        "story by ",
        "view more images",
    )

    if lowered.startswith(rejected_starts):
        return False

    rejected_phrases = (
        "page last updated",
        "responsible nasa official",
        "download this image",
    )

    return not any(
        phrase in lowered
        for phrase in rejected_phrases
    )


def build_abstract(
    blocks: list[tuple[str, str]],
    fallback_description: str,
) -> str:
    """
    Build a short editorial paragraph from the article body.

    The first two useful article paragraphs are normally enough
    to explain what is shown and why it matters.
    """

    paragraphs: list[str] = []
    seen: set[str] = set()

    for tag, text in blocks:
        if tag != "p":
            continue

        if not looks_like_story_paragraph(text):
            continue

        normalized = text.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        paragraphs.append(text)

        combined = " ".join(paragraphs)

        if word_count(combined) >= 70:
            break

    if paragraphs:
        return truncate_words(
            " ".join(paragraphs),
            ABSTRACT_WORD_LIMIT,
        )

    return truncate_words(
        fallback_description,
        ABSTRACT_WORD_LIMIT,
    )


def extract_instruments_from_html(
    article_html: str,
) -> list[str]:
    """
    Extract NASA's Instruments list directly from the article HTML.

    NASA places platform and instrument names inside nested links,
    so parsing the complete HTML section is more reliable than
    relying on individual text blocks.
    """

    heading_match = re.search(
        r"<(?:h[1-6]|div|span|p)\b[^>]*>"
        r"\s*Instruments\s*:?\s*"
        r"</(?:h[1-6]|div|span|p)>",
        article_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not heading_match:
        heading_match = re.search(
            r">\s*Instruments\s*:?\s*<",
            article_html,
            flags=re.IGNORECASE,
        )

    if not heading_match:
        return []

    section_start = heading_match.end()

    section_end_match = re.search(
        r">\s*(?:Collections|Topics|Downloads|"
        r"References\s*&\s*Resources|Image Details)\s*:?\s*<",
        article_html[section_start:],
        flags=re.IGNORECASE,
    )

    if section_end_match:
        section_html = article_html[
            section_start:
            section_start + section_end_match.start()
        ]
    else:
        section_html = article_html[
            section_start:
            section_start + 8000
        ]

    list_items = re.findall(
        r"<li\b[^>]*>(.*?)</li>",
        section_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    instruments: list[str] = []

    for item_html in list_items:
        cleaned = clean_text(item_html)

        cleaned = re.sub(
            r"\s*[—–-]\s*",
            " — ",
            cleaned,
        )

        cleaned = re.sub(
            r"\s+",
            " ",
            cleaned,
        ).strip()

        if not cleaned:
            continue

        if len(cleaned) > 120:
            continue

        lowered = cleaned.lower()

        if lowered in {
            "collections",
            "topics",
            "downloads",
            "references & resources",
            "image details",
        }:
            continue

        if cleaned not in instruments:
            instruments.append(cleaned)

    if instruments:
        return instruments

    link_texts = re.findall(
        r"<a\b[^>]*>(.*?)</a>",
        section_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for link_html in link_texts:
        cleaned = clean_text(link_html)

        cleaned = re.sub(
            r"\s*[—–-]\s*",
            " — ",
            cleaned,
        )

        cleaned = re.sub(
            r"\s+",
            " ",
            cleaned,
        ).strip()

        if not cleaned:
            continue

        if len(cleaned) > 120:
            continue

        if cleaned not in instruments:
            instruments.append(cleaned)

    return instruments


def format_image_source(
    instruments: list[str],
) -> str:
    """Create a concise source label for the signage page."""

    if not instruments:
        return "NASA Earth observation"

    formatted: list[str] = []

    for instrument in instruments:
        cleaned = instrument.replace(" — ", " • ")

        if cleaned.lower() == "photograph":
            cleaned = "Photography"

        if cleaned not in formatted:
            formatted.append(cleaned)

    return " · ".join(formatted)


def normalize_image_url(url: str) -> str:
    """Convert a relative NASA image address into an absolute URL."""

    return urllib.parse.urljoin(
        "https://science.nasa.gov/",
        html.unescape(url),
    )


def image_score(url: str) -> int:
    """Rank likely lead images above thumbnails and page graphics."""

    lowered = urllib.parse.unquote(url).lower()

    if IOTD_IMAGE_PATH not in lowered:
        return -10_000

    score = 0

    if "_lrg." in lowered:
        score += 500

    if "_th." in lowered:
        score -= 250

    if "cq5dam.web.1280" in lowered:
        score += 100

    if "fit=clip" in lowered:
        score += 30

    if "logo" in lowered or "banner" in lowered:
        score -= 1_000

    return score


def select_primary_image(
    parser: ArticleParser,
) -> str:
    """Select the best available Earth Observatory story image."""

    candidates: list[str] = []

    social_image = (
        parser.metadata.get("og:image")
        or parser.metadata.get("twitter:image")
        or ""
    ).strip()

    if social_image:
        candidates.append(
            normalize_image_url(social_image)
        )

    candidates.extend(
        normalize_image_url(url)
        for url in parser.image_urls
    )

    unique_candidates: list[str] = []

    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    ranked = sorted(
        unique_candidates,
        key=image_score,
        reverse=True,
    )

    if not ranked or image_score(ranked[0]) < 0:
        return ""

    return ranked[0]


def extract_article_metadata(
    article_url: str,
    fallback_description: str,
) -> dict[str, object]:
    """Read all slideshow fields from one NASA article."""

    article_html = download_text(article_url)

    parser = ArticleParser()
    parser.feed(article_html)

    metadata = parser.metadata

    image_url = select_primary_image(parser)

    title = clean_title(
        clean_text(
            metadata.get("og:title")
            or metadata.get("twitter:title")
            or ""
        )
    )

    short_description = clean_text(
        metadata.get("og:description")
        or metadata.get("description")
        or metadata.get("twitter:description")
        or fallback_description
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

    instruments = extract_instruments_from_html(
        article_html
    )

    abstract = build_abstract(
        parser.blocks,
        short_description,
    )

    return {
        "title": title,
        "short_description": short_description,
        "abstract": abstract,
        "image_url": image_url,
        "image_alt": image_alt,
        "publication_date": publication_date,
        "display_date": display_date,
        "instruments": instruments,
        "image_source": format_image_source(instruments),
    }


def is_iotd_image(image_url: str) -> bool:
    """Confirm that the image belongs to the IOTD collection."""

    decoded_url = urllib.parse.unquote(
        image_url
    ).lower()

    return IOTD_IMAGE_PATH in decoded_url


def make_record(
    *,
    article_url: str,
    fallback_title: str = "",
    fallback_description: str = "",
    fallback_date: str = "",
) -> Optional[dict[str, object]]:
    """Create one complete slideshow record."""

    print(f"Reading: {fallback_title or article_url}")
    print(f"  {article_url}")

    metadata = extract_article_metadata(
        article_url,
        fallback_description,
    )

    image_url = str(metadata["image_url"])

    if not image_url:
        print("  Skipped: no primary image found.")
        return None

    if not is_iotd_image(image_url):
        print(
            "  Skipped: primary image is not from "
            "the Earth Observatory IOTD collection."
        )
        return None

    title = (
        str(metadata["title"])
        or clean_title(fallback_title)
    )

    publication_date = str(
        metadata["publication_date"]
    )

    display_date = str(
        metadata["display_date"]
    )

    if not publication_date and fallback_date:
        publication_date, display_date = format_rss_date(
            fallback_date
        )

    if not title or not publication_date:
        print("  Skipped: title or publication date missing.")
        return None

    short_description = (
        str(metadata["short_description"])
        or remove_nasa_boilerplate(fallback_description)
    )

    return {
        "title": title,
        "publication_date": publication_date,
        "display_date": display_date,
        "short_description": short_description,
        "abstract": metadata["abstract"],
        "image_url": image_url,
        "image_alt": (
            str(metadata["image_alt"])
            or title
        ),
        "instruments": metadata["instruments"],
        "image_source": metadata["image_source"],
        "article_url": article_url,
        "source": "NASA Earth Observatory",
    }


def collect_feed_records(
    feed_xml: str,
) -> list[dict[str, object]]:
    """Collect qualifying records from NASA's mixed RSS feed."""

    root = ET.fromstring(feed_xml)

    items = [
        element
        for element in root.iter()
        if local_name(element.tag) in {"item", "entry"}
    ]

    records: list[dict[str, object]] = []

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
    """Collect candidate article links from the IOTD archive."""

    archive_html = download_text(ARCHIVE_URL)

    parser = ArchiveLinkParser(ARCHIVE_URL)
    parser.feed(archive_html)

    return parser.links


def supplement_from_archive(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Add older archive entries until seven records exist."""

    existing_urls = {
        str(record["article_url"])
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
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Deduplicate, sort newest first, and keep seven."""

    unique: dict[str, dict[str, object]] = {}

    for record in records:
        unique[str(record["article_url"])] = record

    sorted_records = sorted(
        unique.values(),
        key=lambda record: str(
            record["publication_date"]
        ),
        reverse=True,
    )

    return sorted_records[:NUMBER_OF_ITEMS]


def write_json(
    records: list[dict[str, object]],
) -> None:
    """Write the enriched slideshow dataset."""

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
            print(
                f"   Image source: "
                f"{record['image_source']}"
            )
            print(
                f"   Abstract words: "
                f"{word_count(str(record['abstract']))}"
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
