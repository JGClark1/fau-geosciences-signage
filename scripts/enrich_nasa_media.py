from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

DATA_PATH = Path("data/nasa-earth-observatory.json")
USER_AGENT = "FAU-Geosciences-Signage/1.0"
IOTD_PATH = "/eo/images/iotd/"

# These editorial overrides cover cases where NASA's page structure alone
# cannot reveal which of several scientifically valid images communicates
# the story best. They are keyed by stable article slug, not publication date.
MEDIA_OVERRIDES = {
    "pink-penguin-guano-provides-diet-clues": {
        "mode": "preferred_image",
        "prefer_terms": ("drone", "penguin"),
        "label": "Drone image",
    },
    "a-million-panel-project": {
        "mode": "comparison",
        "before_terms": ("20240616",),
        "after_terms": ("20260606",),
        "before_label": "BEFORE",
        "before_date": "June 16, 2024",
        "after_label": "AFTER",
        "after_date": "June 6, 2026",
    },
}


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_url(url: str, base_url: str) -> str:
    url = html.unescape(url.strip())
    url = url.replace("\\/", "/")
    url = url.replace("\\u002F", "/").replace("\\u002f", "/")
    url = url.replace("\\u003A", ":").replace("\\u003a", ":")
    return urllib.parse.urljoin(base_url, url)


def download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


class MediaParser(HTMLParser):
    """Collect article images with nearby text and all MP4 URLs."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.images: list[dict[str, str]] = []
        self.videos: list[str] = []
        self.recent_text: list[str] = []
        self.current_text_tag: Optional[str] = None
        self.current_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }
        tag = tag.lower()

        if tag in {"h1", "h2", "h3", "h4", "p", "figcaption"}:
            self.current_text_tag = tag
            self.current_text_parts = []

        if tag == "img":
            urls: list[str] = []
            for key in ("src", "data-src", "data-lazy-src"):
                value = attributes.get(key, "").strip()
                if value:
                    urls.append(value)

            srcset = attributes.get("srcset", "")
            if srcset:
                for candidate in srcset.split(","):
                    value = candidate.strip().split(" ")[0]
                    if value:
                        urls.append(value)

            context = " ".join(self.recent_text[-4:])
            context = clean_text(
                " ".join(
                    [
                        context,
                        attributes.get("alt", ""),
                        attributes.get("title", ""),
                    ]
                )
            )

            for raw_url in urls:
                url = normalize_url(raw_url, self.base_url)
                if url and not any(item["url"] == url for item in self.images):
                    self.images.append({"url": url, "context": context})

        if tag in {"a", "source", "video"}:
            for key in ("href", "src", "data-src"):
                value = attributes.get(key, "").strip()
                if value and ".mp4" in value.lower():
                    url = normalize_url(value, self.base_url)
                    if url not in self.videos:
                        self.videos.append(url)

    def handle_data(self, data: str) -> None:
        if self.current_text_tag:
            self.current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != self.current_text_tag:
            return

        text = clean_text(" ".join(self.current_text_parts))
        if text:
            self.recent_text.append(text)
            self.recent_text = self.recent_text[-8:]

        self.current_text_tag = None
        self.current_text_parts = []


def article_slug(article_url: str) -> str:
    return urllib.parse.urlparse(article_url).path.rstrip("/").split("/")[-1]


def is_story_image(url: str) -> bool:
    lowered = urllib.parse.unquote(url).lower()
    return (
        IOTD_PATH in lowered
        and not any(
            term in lowered
            for term in (
                "_th.",
                "thumbnail",
                "logo",
                "banner",
                "locatormap",
                "location-map",
                "poster",
                "fallback",
            )
        )
    )


def image_quality_score(item: dict[str, str]) -> int:
    lowered = urllib.parse.unquote(item["url"]).lower()
    score = 0
    if is_story_image(item["url"]):
        score += 1000
    if "_lrg." in lowered:
        score += 400
    if "fit=clip" in lowered:
        score += 30
    if "w=" in lowered:
        score += 10
    return score


def deduplicate_images(images: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep one best URL for each underlying NASA filename."""
    best: dict[str, dict[str, str]] = {}

    for item in images:
        parsed = urllib.parse.urlparse(item["url"])
        filename = Path(parsed.path).name.lower()
        if not filename:
            continue

        existing = best.get(filename)
        if existing is None or image_quality_score(item) > image_quality_score(existing):
            best[filename] = item

    return list(best.values())


def find_by_terms(
    images: list[dict[str, str]],
    terms: tuple[str, ...],
) -> Optional[dict[str, str]]:
    ranked: list[tuple[int, int, dict[str, str]]] = []

    for item in images:
        haystack = (
            urllib.parse.unquote(item["url"])
            + " "
            + item.get("context", "")
        ).lower()

        hits = sum(1 for term in terms if term.lower() in haystack)
        if hits:
            ranked.append((hits, image_quality_score(item), item))

    if not ranked:
        return None

    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return ranked[0][2]


def detect_comparison(
    article_html: str,
    images: list[dict[str, str]],
) -> Optional[list[dict[str, str]]]:
    """
    Detect an unattended-signage-friendly before/after pair.

    NASA comparison components usually expose Curtain/Toggle/2-Up controls,
    and their two large assets commonly contain different acquisition dates.
    """
    lowered_html = article_html.lower()
    has_comparison_controls = (
        "curtain" in lowered_html
        and "toggle" in lowered_html
        and "2-up" in lowered_html
    )
    if not has_comparison_controls:
        return None

    dated: list[tuple[str, dict[str, str]]] = []
    for item in images:
        haystack = (
            urllib.parse.unquote(item["url"])
            + " "
            + item.get("context", "")
        )
        dates = re.findall(
            r"(?:19|20)\d{2}[-_/]?(?:0[1-9]|1[0-2])[-_/]?(?:0[1-9]|[12]\d|3[01])",
            haystack,
        )
        for date in dates:
            normalized = re.sub(r"[-_/]", "", date)
            dated.append((normalized, item))

    unique: dict[str, dict[str, str]] = {}
    for date, item in dated:
        existing = unique.get(date)
        if existing is None or image_quality_score(item) > image_quality_score(existing):
            unique[date] = item

    if len(unique) < 2:
        return None

    dates = sorted(unique)
    before_date, after_date = dates[0], dates[-1]

    def display_date(compact: str) -> str:
        year = compact[:4]
        month = int(compact[4:6])
        day = int(compact[6:8])
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        return f"{months[month]} {day}, {year}"

    return [
        {
            "role": "before",
            "url": unique[before_date]["url"],
            "label": "BEFORE",
            "date": display_date(before_date),
        },
        {
            "role": "after",
            "url": unique[after_date]["url"],
            "label": "AFTER",
            "date": display_date(after_date),
        },
    ]


def apply_override(
    slug: str,
    override: dict,
    images: list[dict[str, str]],
) -> Optional[dict]:
    mode = override.get("mode")

    if mode == "preferred_image":
        selected = find_by_terms(
            images,
            tuple(override.get("prefer_terms", ())),
        )
        if selected:
            return {
                "media_type": "image",
                "media_url": selected["url"],
                "image_url": selected["url"],
                "media_items": [],
            }

    if mode == "comparison":
        before = find_by_terms(
            images,
            tuple(override.get("before_terms", ())),
        )
        after = find_by_terms(
            images,
            tuple(override.get("after_terms", ())),
        )
        if before and after:
            return {
                "media_type": "comparison",
                "media_url": before["url"],
                "image_url": before["url"],
                "media_items": [
                    {
                        "role": "before",
                        "url": before["url"],
                        "label": override.get("before_label", "BEFORE"),
                        "date": override.get("before_date", ""),
                    },
                    {
                        "role": "after",
                        "url": after["url"],
                        "label": override.get("after_label", "AFTER"),
                        "date": override.get("after_date", ""),
                    },
                ],
            }

    return None


def enrich_record(record: dict) -> dict:
    article_url = str(record.get("article_url", ""))
    if not article_url:
        return record

    article_html = download_text(article_url)
    parser = MediaParser(article_url)
    parser.feed(article_html)

    # Some MP4 URLs are embedded in page JSON rather than ordinary tags.
    prepared = html.unescape(article_html).replace("\\/", "/")
    for match in re.findall(
        r'https?://[^"\'<>\s]+?\.mp4(?:\?[^"\'<>\s]*)?',
        prepared,
        flags=re.IGNORECASE,
    ):
        url = normalize_url(match, article_url)
        if url not in parser.videos:
            parser.videos.append(url)

    images = deduplicate_images(
        [item for item in parser.images if is_story_image(item["url"])]
    )

    slug = article_slug(article_url)
    override = MEDIA_OVERRIDES.get(slug)
    if override:
        result = apply_override(slug, override, images)
        if result:
            record.update(result)
            return record

    comparison = detect_comparison(article_html, images)
    if comparison:
        record["media_type"] = "comparison"
        record["media_items"] = comparison
        record["media_url"] = comparison[0]["url"]
        record["image_url"] = comparison[0]["url"]
        return record

    if parser.videos:
        record["media_type"] = "video"
        record["media_url"] = parser.videos[0]
        record["video_url"] = parser.videos[0]
        record["media_items"] = []
        return record

    # Retain the builder's selected image unless a clearly better subject-
    # matching image exists. This keeps the general behavior conservative.
    current_url = str(record.get("image_url", ""))
    if images and not current_url:
        best = max(images, key=image_quality_score)
        record["media_type"] = "image"
        record["media_url"] = best["url"]
        record["image_url"] = best["url"]

    record.setdefault("media_items", [])
    return record


def main() -> None:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    items = payload.get("items", [])

    enriched = []
    for record in items:
        updated = enrich_record(dict(record))
        enriched.append(updated)

        print(
            f"{updated.get('title')}: "
            f"{updated.get('media_type')} "
            f"({len(updated.get('media_items', []))} comparison items)"
        )

    payload["items"] = enriched
    DATA_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
