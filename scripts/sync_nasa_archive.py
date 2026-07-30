from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

ARCHIVE_URL = (
    "https://science.nasa.gov/earth/"
    "earth-observatory/image-of-the-day/"
)

OUTPUT_PATH = Path(
    "data/nasa-earth-observatory.json"
)

NUMBER_OF_ITEMS = 7

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

EARTH_OBSERVATORY_PATH = (
    "/earth/earth-observatory/"
)

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

DATE_PATTERNS = (
    "%b %d, %Y",
    "%B %d, %Y",
)

STOP_HEADINGS = {
    "downloads",
    "image details",
    "references",
    "references & resources",
    "references and resources",
    "you may also be interested in",
}

REJECTED_PARAGRAPH_PHRASES = {
    "page last updated",
    "responsible nasa official",
    "the international space station program supports",
    "to view this video",
}

INSTRUMENT_PATTERNS = (
    (
        re.compile(
            r"\bLandsat\s*9\b",
            re.IGNORECASE,
        ),
        "Landsat 9",
    ),
    (
        re.compile(
            r"\bLandsat\s*8\b",
            re.IGNORECASE,
        ),
        "Landsat 8",
    ),
    (
        re.compile(
            r"\bLandsat\b",
            re.IGNORECASE,
        ),
        "Landsat",
    ),
    (
        re.compile(
            r"\bInternational Space Station\b|\bISS\b",
            re.IGNORECASE,
        ),
        "ISS",
    ),
    (
        re.compile(
            r"\bMODIS\b",
            re.IGNORECASE,
        ),
        "MODIS",
    ),
    (
        re.compile(
            r"\bVIIRS\b",
            re.IGNORECASE,
        ),
        "VIIRS",
    ),
    (
        re.compile(
            r"\bSentinel[- ]?2\b",
            re.IGNORECASE,
        ),
        "Sentinel-2",
    ),
    (
        re.compile(
            r"\bNISAR\b",
            re.IGNORECASE,
        ),
        "NISAR",
    ),
    (
        re.compile(
            r"\bdrone\b|\buncrewed aerial\b",
            re.IGNORECASE,
        ),
        "Drone photography",
    ),
    (
        re.compile(
            r"\bphotograph\b|\bphotography\b",
            re.IGNORECASE,
        ),
        "Photography",
    ),
    (
        re.compile(
            r"\bmodel\b|\bmodeling\b",
            re.IGNORECASE,
        ),
        "Model",
    ),
)


def download_text(url: str) -> str:
    """Download a NASA page as UTF-8 text."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*;q=0.8",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        encoding = (
            response.headers.get_content_charset()
            or "utf-8"
        )

        return response.read().decode(
            encoding,
            errors="replace",
        )


def clean_text(value: str | None) -> str:
    """Normalize HTML entities and whitespace."""

    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_article_url(url: str) -> str:
    """Return the canonical NASA Science article URL."""

    absolute = urllib.parse.urljoin(
        ARCHIVE_URL,
        url,
    )

    parsed = urllib.parse.urlparse(
        absolute
    )

    path = parsed.path.rstrip("/") + "/"

    return (
        "https://science.nasa.gov"
        + path
    )


def article_slug(url: str) -> str:
    """Return the single Earth Observatory path segment."""

    path = urllib.parse.urlparse(
        url
    ).path.rstrip("/")

    if EARTH_OBSERVATORY_PATH not in path:
        return ""

    relative = path.split(
        EARTH_OBSERVATORY_PATH,
        maxsplit=1,
    )[1]

    parts = [
        part
        for part in relative.split("/")
        if part
    ]

    if len(parts) != 1:
        return ""

    return parts[0].lower()


def is_story_url(url: str) -> bool:
    """Identify a one-page Earth Observatory story URL."""

    parsed = urllib.parse.urlparse(
        url
    )

    if parsed.netloc not in {
        "",
        "science.nasa.gov",
        "www.science.nasa.gov",
    }:
        return False

    slug = article_slug(
        normalize_article_url(url)
    )

    return bool(
        slug
        and slug not in EXCLUDED_SLUGS
    )


def parse_visible_date(value: str) -> datetime | None:
    """Parse a visible NASA card date."""

    cleaned = clean_text(value)

    for pattern in DATE_PATTERNS:
        match = re.search(
            r"\b[A-Z][a-z]{2,8}\s+"
            r"\d{1,2},\s+\d{4}\b",
            cleaned,
        )

        if not match:
            continue

        try:
            return datetime.strptime(
                match.group(0),
                pattern,
            ).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

    return None


def largest_srcset_url(
    srcset: str,
    base_url: str,
) -> str:
    """Select the largest candidate from an image srcset."""

    candidates: list[
        tuple[int, str]
    ] = []

    for raw_candidate in (
        srcset or ""
    ).split(","):
        parts = raw_candidate.strip().split()

        if not parts:
            continue

        raw_url = parts[0]
        size = 0

        if len(parts) > 1:
            descriptor = parts[1].lower()

            match = re.match(
                r"(\d+)(?:w|x)",
                descriptor,
            )

            if match:
                size = int(
                    match.group(1)
                )

        candidates.append(
            (
                size,
                urllib.parse.urljoin(
                    base_url,
                    html.unescape(
                        raw_url
                    ),
                ),
            )
        )

    if not candidates:
        return ""

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def image_url_from_tag(
    image: Tag,
    base_url: str,
) -> str:
    """Get the best URL exposed by an archive image element."""

    srcset_url = largest_srcset_url(
        str(image.get("srcset", "")),
        base_url,
    )

    if srcset_url:
        return srcset_url

    for attribute_name in (
        "data-src",
        "data-lazy-src",
        "src",
    ):
        raw_url = clean_text(
            str(
                image.get(
                    attribute_name,
                    "",
                )
            )
        )

        if raw_url:
            return urllib.parse.urljoin(
                base_url,
                html.unescape(
                    raw_url
                ),
            )

    return ""


def find_story_card(
    link: Tag,
) -> Tag | None:
    """
    Find the archive card containing the story title, date, and thumbnail.

    The search walks upward only far enough to avoid capturing the entire
    results grid.
    """

    current: Tag | None = link

    for _ in range(8):
        if current is None:
            return None

        text = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        if (
            parse_visible_date(text)
            and current.find("img")
        ):
            return current

        parent = current.parent

        current = (
            parent
            if isinstance(parent, Tag)
            else None
        )

    return None


def archive_records(
    archive_html: str,
) -> list[dict[str, Any]]:
    """Read dated story cards from the IOTD archive."""

    soup = BeautifulSoup(
        archive_html,
        "html.parser",
    )

    records_by_url: dict[
        str,
        dict[str, Any],
    ] = {}

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = str(
            link.get("href", "")
        ).strip()

        if not is_story_url(href):
            continue

        article_url = normalize_article_url(
            href
        )

        card = find_story_card(link)

        if card is None:
            continue

        publication_datetime = (
            parse_visible_date(
                card.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if publication_datetime is None:
            continue

        image = card.find("img")

        thumbnail_url = (
            image_url_from_tag(
                image,
                ARCHIVE_URL,
            )
            if isinstance(image, Tag)
            else ""
        )

        title = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not title and isinstance(
            image,
            Tag,
        ):
            title = clean_text(
                str(
                    image.get(
                        "alt",
                        "",
                    )
                )
            )

        card_text = clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )

        date_text = (
            f"{publication_datetime.strftime('%b')} "
            f"{publication_datetime.day}, "
            f"{publication_datetime.year}"
        )

        short_description = card_text

        for removable in (
            title,
            date_text,
            "1 min read",
            "2 min read",
            "3 min read",
            "4 min read",
            "5 min read",
            "6 min read",
            "7 min read",
            "8 min read",
            "9 min read",
            "10 min read",
        ):
            if removable:
                short_description = (
                    short_description.replace(
                        removable,
                        " ",
                    )
                )

        short_description = clean_text(
            short_description
        )

        candidate = {
            "title": title,
            "publication_datetime":
                publication_datetime,
            "publication_date":
                publication_datetime.strftime(
                    "%Y-%m-%d"
                ),
            "display_date":
                f"{publication_datetime.strftime('%B')} "
                f"{publication_datetime.day}, "
                f"{publication_datetime.year}",
            "short_description":
                short_description,
            "article_url": article_url,
            "archive_thumbnail_url":
                thumbnail_url,
        }

        existing = records_by_url.get(
            article_url
        )

        if (
            existing is None
            or (
                not existing.get(
                    "archive_thumbnail_url"
                )
                and thumbnail_url
            )
        ):
            records_by_url[
                article_url
            ] = candidate

    records = list(
        records_by_url.values()
    )

    records.sort(
        key=lambda item:
            item["publication_datetime"],
        reverse=True,
    )

    return records


def metadata_value(
    soup: BeautifulSoup,
    *names: str,
) -> str:
    """Read a metadata content field by property or name."""

    lowered_names = {
        name.lower()
        for name in names
    }

    for tag in soup.find_all("meta"):
        key = clean_text(
            str(
                tag.get("property")
                or tag.get("name")
                or ""
            )
        ).lower()

        if key not in lowered_names:
            continue

        content = clean_text(
            str(
                tag.get(
                    "content",
                    "",
                )
            )
        )

        if content:
            return content

    return ""


def article_title(
    soup: BeautifulSoup,
    fallback: str,
) -> str:
    """Extract a clean article title."""

    title = metadata_value(
        soup,
        "og:title",
        "twitter:title",
    )

    if not title:
        heading = soup.find("h1")

        title = (
            clean_text(
                heading.get_text(
                    " ",
                    strip=True,
                )
            )
            if isinstance(
                heading,
                Tag,
            )
            else ""
        )

    title = re.sub(
        r"\s*[-|]\s*NASA Science\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title or fallback


def article_description(
    soup: BeautifulSoup,
    fallback: str,
) -> str:
    """Extract NASA's short article description."""

    description = metadata_value(
        soup,
        "description",
        "og:description",
        "twitter:description",
    )

    return description or fallback


def story_paragraphs(
    soup: BeautifulSoup,
) -> list[str]:
    """Collect article prose before references and download sections."""

    paragraphs: list[str] = []

    for element in soup.find_all(
        [
            "h2",
            "h3",
            "h4",
            "p",
        ]
    ):
        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        if (
            element.name
            in {"h2", "h3", "h4"}
            and text.lower()
            in STOP_HEADINGS
        ):
            break

        lowered = text.lower()

        if any(
            phrase in lowered
            for phrase
            in REJECTED_PARAGRAPH_PHRASES
        ):
            continue

        if len(
            text.split()
        ) < 8:
            continue

        if text not in paragraphs:
            paragraphs.append(text)

    return paragraphs


def normalize_sentence(value: str) -> str:
    """Normalize text for duplicate comparison."""

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def build_abstract(
    short_description: str,
    paragraphs: list[str],
    target_words: int = 125,
    maximum_words: int = 145,
) -> str:
    """Build a compact abstract without repeating the description."""

    selected: list[str] = []
    seen: set[str] = set()

    for candidate in (
        [short_description]
        + paragraphs
    ):
        cleaned = clean_text(candidate)

        if not cleaned:
            continue

        normalized = normalize_sentence(
            cleaned
        )

        if not normalized:
            continue

        if any(
            normalized == prior
            or normalized in prior
            or prior in normalized
            for prior in seen
        ):
            continue

        candidate_words = cleaned.split()
        current_words = sum(
            len(item.split())
            for item in selected
        )

        if (
            selected
            and current_words
            + len(candidate_words)
            > maximum_words
        ):
            continue

        selected.append(cleaned)
        seen.add(normalized)

        if sum(
            len(item.split())
            for item in selected
        ) >= target_words:
            break

    abstract = clean_text(
        " ".join(selected)
    )

    words = abstract.split()

    if len(words) > maximum_words:
        abstract = " ".join(
            words[:maximum_words]
        ).rstrip(
            ",;:"
        )

        if not abstract.endswith(
            (".", "!", "?")
        ):
            abstract += "."

    return abstract


def infer_instruments(
    article_text: str,
) -> list[str]:
    """Infer a compact source list from article prose."""

    instruments: list[str] = []

    for pattern, label in INSTRUMENT_PATTERNS:
        if pattern.search(
            article_text
        ):
            if label not in instruments:
                instruments.append(label)

    return instruments


def source_label(
    instruments: list[str],
) -> str:
    """Create the panel's image-source text."""

    if not instruments:
        return "NASA Earth observation"

    return " • ".join(
        instruments[:3]
    )


def build_missing_record(
    archive_record: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete record for a story absent from the RSS-based data."""

    article_html = download_text(
        archive_record["article_url"]
    )

    soup = BeautifulSoup(
        article_html,
        "html.parser",
    )

    title = article_title(
        soup,
        archive_record["title"],
    )

    short_description = (
        article_description(
            soup,
            archive_record[
                "short_description"
            ],
        )
    )

    paragraphs = story_paragraphs(
        soup
    )

    abstract = build_abstract(
        short_description,
        paragraphs,
    )

    article_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    instruments = infer_instruments(
        article_text
    )

    thumbnail_url = archive_record.get(
        "archive_thumbnail_url",
        "",
    )

    return {
        "title": title,
        "publication_date":
            archive_record[
                "publication_date"
            ],
        "display_date":
            archive_record[
                "display_date"
            ],
        "short_description":
            short_description,
        "abstract": abstract,
        "media_type": "image",
        "media_url": thumbnail_url,
        "video_url": "",
        "image_url": thumbnail_url,
        "image_alt": title,
        "instruments": instruments,
        "image_source":
            source_label(
                instruments
            ),
        "article_url":
            archive_record[
                "article_url"
            ],
        "source":
            "NASA Earth Observatory",
        "media_items": [],
        "archive_thumbnail_url":
            thumbnail_url,
    }


def merge_record(
    archive_record: dict[str, Any],
    existing_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preserve existing editorial text while enforcing archive membership."""

    if existing_record is None:
        return build_missing_record(
            archive_record
        )

    record = dict(
        existing_record
    )

    record["title"] = (
        archive_record["title"]
        or record.get(
            "title",
            "",
        )
    )

    record["publication_date"] = (
        archive_record[
            "publication_date"
        ]
    )

    record["display_date"] = (
        archive_record[
            "display_date"
        ]
    )

    record["article_url"] = (
        archive_record[
            "article_url"
        ]
    )

    record["archive_thumbnail_url"] = (
        archive_record.get(
            "archive_thumbnail_url",
            "",
        )
    )

    if not record.get(
        "short_description"
    ):
        record["short_description"] = (
            archive_record[
                "short_description"
            ]
        )

    record.setdefault(
        "media_items",
        [],
    )

    return record


def main() -> None:
    """Make the NASA archive authoritative for slideshow membership and order."""

    print(
        "Reading NASA Earth Observatory "
        "Image of the Day archive…"
    )

    archive_html = download_text(
        ARCHIVE_URL
    )

    records = archive_records(
        archive_html
    )

    if len(records) < NUMBER_OF_ITEMS:
        raise RuntimeError(
            "The NASA archive parser found "
            f"only {len(records)} dated story cards."
        )

    newest = records[
        :NUMBER_OF_ITEMS
    ]

    existing_payload: dict[str, Any] = {}

    if OUTPUT_PATH.exists():
        existing_payload = json.loads(
            OUTPUT_PATH.read_text(
                encoding="utf-8"
            )
        )

    existing_by_url = {
        str(
            item.get(
                "article_url",
                "",
            )
        ): item
        for item
        in existing_payload.get(
            "items",
            [],
        )
    }

    merged_items: list[
        dict[str, Any]
    ] = []

    for index, archive_record in enumerate(
        newest,
        start=1,
    ):
        article_url = archive_record[
            "article_url"
        ]

        existing = existing_by_url.get(
            article_url
        )

        status = (
            "existing"
            if existing
            else "new"
        )

        print(
            f"{index}. "
            f"{archive_record['title']} "
            f"({archive_record['display_date']}) "
            f"[{status}]"
        )

        merged_items.append(
            merge_record(
                archive_record,
                existing,
            )
        )

    payload = dict(
        existing_payload
    )

    payload["generated_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )
    )

    payload["source_feed"] = (
        existing_payload.get(
            "source_feed",
            "",
        )
    )

    payload["source_archive"] = (
        ARCHIVE_URL
    )

    payload["item_count"] = len(
        merged_items
    )

    payload["items"] = merged_items

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

    expected_url = newest[0][
        "article_url"
    ]

    actual_url = payload["items"][0][
        "article_url"
    ]

    if actual_url != expected_url:
        raise RuntimeError(
            "Archive validation failed: "
            "the first JSON record does not "
            "match the first archive story."
        )

    print(
        "Archive membership validation passed."
    )


if __name__ == "__main__":
    main()
