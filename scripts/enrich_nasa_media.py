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

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

IOTD_PATH = "/eo/images/iotd/"

COMPARISON_CONTAINER_TERMS = {
    "before-after",
    "before_after",
    "comparison",
    "curtain",
    "image-compare",
    "image_compare",
    "juxtapose",
    "twentytwenty",
}

BEFORE_TERMS = {
    "before",
    "earlier",
    "previous",
    "prior",
}

AFTER_TERMS = {
    "after",
    "later",
    "current",
    "recent",
}

REJECTED_ASSET_TERMS = {
    "eo_image_map_",
    "fallback",
    "icon",
    "locatormap",
    "location-map",
    "logo",
    "map-placeholder",
    "poster",
    "thumbnail",
}


def clean_text(value: str) -> str:
    """Normalize HTML entities and whitespace."""

    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_url(
    url: str,
    base_url: str,
) -> str:
    """Normalize escaped and relative media URLs."""

    normalized = html.unescape(
        (url or "").strip()
    )

    normalized = normalized.replace("\\/", "/")
    normalized = normalized.replace("\\u002F", "/")
    normalized = normalized.replace("\\u002f", "/")
    normalized = normalized.replace("\\u003A", ":")
    normalized = normalized.replace("\\u003a", ":")

    return urllib.parse.urljoin(
        base_url,
        normalized,
    )


def download_text(url: str) -> str:
    """Download one NASA article page."""

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


def is_story_asset(url: str) -> bool:
    """Retain substantive Earth Observatory image assets."""

    lowered = urllib.parse.unquote(
        url or ""
    ).lower()

    if IOTD_PATH not in lowered:
        return False

    if any(
        term in lowered
        for term in REJECTED_ASSET_TERMS
    ):
        return False

    return bool(
        re.search(
            r"\.(?:jpe?g|png|webp)(?:\?|$)",
            lowered,
        )
    )


def canonical_image_key(url: str) -> str:
    """Group resized variants of one underlying image."""

    parsed = urllib.parse.urlparse(url)
    filename = Path(parsed.path).name.lower()

    filename = re.sub(
        r"_lrg(?=\.)",
        "",
        filename,
    )

    filename = re.sub(
        r"_th(?=\.)",
        "",
        filename,
    )

    return filename


def image_quality_score(url: str) -> float:
    """Estimate relative image quality from the URL."""

    lowered = urllib.parse.unquote(url).lower()
    score = 0.0

    if "_lrg." in lowered:
        score += 10000.0

    if "/content/dam/" in lowered:
        score += 3000.0

    if "/dynamicimage/" in lowered:
        score += 1000.0

    for pattern in (
        r"(?:[?&]w=)(\d{2,5})",
        r"(?:[?&]width=)(\d{2,5})",
        r"cq5dam\.web\.(\d{2,5})",
    ):
        match = re.search(pattern, lowered)

        if match:
            score += min(
                int(match.group(1)),
                10000,
            )

    return score


def best_variant(urls: list[str]) -> str:
    """Return the highest-quality variant of an image."""

    valid = [
        url
        for url in urls
        if is_story_asset(url)
    ]

    if not valid:
        return ""

    return max(
        valid,
        key=image_quality_score,
    )


def extract_date_label(value: str) -> str:
    """Extract a readable date already present in NASA markup."""

    cleaned = clean_text(value)

    match = re.search(
        r"\b("
        r"January|February|March|April|May|June|"
        r"July|August|September|October|November|December"
        r")\s+\d{1,2},\s+\d{4}\b",
        cleaned,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(0)

    compact = re.search(
        r"(?<!\d)"
        r"((?:19|20)\d{2})"
        r"[-_/]?"
        r"(0[1-9]|1[0-2])"
        r"[-_/]?"
        r"(0[1-9]|[12]\d|3[01])"
        r"(?!\d)",
        cleaned,
    )

    if not compact:
        return ""

    year, month, day = compact.groups()

    month_names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    return (
        f"{month_names[int(month)]} "
        f"{int(day)}, "
        f"{year}"
    )


class VideoParser(HTMLParser):
    """Collect MP4 URLs from article markup."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        if tag.lower() not in {
            "a",
            "source",
            "video",
        }:
            return

        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }

        for attribute_name in (
            "href",
            "src",
            "data-src",
        ):
            raw_url = attributes.get(
                attribute_name,
                "",
            ).strip()

            if ".mp4" not in raw_url.lower():
                continue

            url = normalize_url(
                raw_url,
                self.base_url,
            )

            if url not in self.urls:
                self.urls.append(url)


def extract_video_urls(
    article_html: str,
    article_url: str,
) -> list[str]:
    """Collect MP4 URLs from markup and embedded page JSON."""

    parser = VideoParser(article_url)
    parser.feed(article_html)

    prepared = html.unescape(article_html)
    prepared = prepared.replace("\\/", "/")
    prepared = prepared.replace("\\u002F", "/")
    prepared = prepared.replace("\\u002f", "/")
    prepared = prepared.replace("\\u003A", ":")
    prepared = prepared.replace("\\u003a", ":")

    for match in re.findall(
        r'https?://[^"\'<>\s]+?'
        r'\.mp4'
        r'(?:\?[^"\'<>\s]*)?',
        prepared,
        flags=re.IGNORECASE,
    ):
        url = normalize_url(
            match,
            article_url,
        )

        if url not in parser.urls:
            parser.urls.append(url)

    return parser.urls


def video_score(url: str) -> int:
    """Prefer NASA Earth Observatory article videos."""

    lowered = urllib.parse.unquote(url).lower()
    score = 0

    if "assets.science.nasa.gov" in lowered:
        score += 200

    if "/eo/" in lowered:
        score += 150

    if "/iotd/" in lowered:
        score += 150

    if "thumbnail" in lowered:
        score -= 500

    if "preview" in lowered:
        score -= 40

    return score


class ComparisonParser(HTMLParser):
    """
    Find images only inside explicit comparison widgets.

    A generic mention of "before" or "comparison" elsewhere on the page is
    insufficient. A container must identify itself through a comparison-
    specific class, id, or data attribute and contain both images.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()

        self.base_url = base_url
        self.depth = 0
        self.container_stack: list[dict] = []
        self.completed: list[dict] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        self.depth += 1

        attributes = {
            key.lower(): value
            for key, value in attrs
            if value is not None
        }

        marker_text = " ".join(
            [
                attributes.get("class", ""),
                attributes.get("id", ""),
                attributes.get("data-component", ""),
                attributes.get("data-block", ""),
                attributes.get("data-module", ""),
                attributes.get("data-testid", ""),
            ]
        ).lower()

        is_comparison_container = any(
            term in marker_text
            for term in COMPARISON_CONTAINER_TERMS
        )

        if is_comparison_container:
            self.container_stack.append(
                {
                    "start_depth": self.depth,
                    "marker_text": marker_text,
                    "images": [],
                    "text_parts": [],
                }
            )

        if not self.container_stack:
            return

        current = self.container_stack[-1]

        if tag.lower() == "img":
            urls: list[str] = []

            for attribute_name in (
                "src",
                "data-src",
                "data-lazy-src",
            ):
                raw_url = attributes.get(
                    attribute_name,
                    "",
                ).strip()

                if raw_url:
                    urls.append(
                        normalize_url(
                            raw_url,
                            self.base_url,
                        )
                    )

            srcset = attributes.get("srcset", "")

            for candidate in srcset.split(","):
                raw_url = candidate.strip().split(" ")[0]

                if raw_url:
                    urls.append(
                        normalize_url(
                            raw_url,
                            self.base_url,
                        )
                    )

            valid_urls = [
                url
                for url in urls
                if is_story_asset(url)
            ]

            if valid_urls:
                current["images"].append(
                    {
                        "urls": valid_urls,
                        "alt": clean_text(
                            attributes.get("alt", "")
                        ),
                        "title": clean_text(
                            attributes.get("title", "")
                        ),
                    }
                )

    def handle_data(self, data: str) -> None:
        if self.container_stack:
            cleaned = clean_text(data)

            if cleaned:
                self.container_stack[-1][
                    "text_parts"
                ].append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        if self.container_stack:
            current = self.container_stack[-1]

            if self.depth == current["start_depth"]:
                self.completed.append(
                    self.container_stack.pop()
                )

        self.depth -= 1


def classify_comparison_role(
    image: dict,
    container_text: str,
) -> str:
    """Classify an image only when its own metadata supplies a role."""

    descriptive = clean_text(
        " ".join(
            [
                image.get("alt", ""),
                image.get("title", ""),
            ]
        )
    ).lower()

    before = any(
        re.search(
            rf"\b{re.escape(term)}\b",
            descriptive,
        )
        for term in BEFORE_TERMS
    )

    after = any(
        re.search(
            rf"\b{re.escape(term)}\b",
            descriptive,
        )
        for term in AFTER_TERMS
    )

    if before and not after:
        return "before"

    if after and not before:
        return "after"

    return ""


def detect_explicit_comparison(
    article_html: str,
    article_url: str,
) -> Optional[list[dict[str, str]]]:
    """
    Return a comparison only when one explicit widget contains two distinct
    images and supplies unambiguous BEFORE and AFTER roles.
    """

    parser = ComparisonParser(article_url)
    parser.feed(article_html)

    for container in parser.completed:
        images = container.get("images", [])

        if len(images) < 2:
            continue

        deduplicated: list[dict] = []
        seen: set[str] = set()

        for image in images:
            url = best_variant(
                image.get("urls", [])
            )

            if not url:
                continue

            key = canonical_image_key(url)

            if key in seen:
                continue

            seen.add(key)

            deduplicated.append(
                {
                    **image,
                    "url": url,
                }
            )

        if len(deduplicated) != 2:
            continue

        container_text = clean_text(
            " ".join(
                container.get(
                    "text_parts",
                    [],
                )
            )
        )

        roles = [
            classify_comparison_role(
                image,
                container_text,
            )
            for image in deduplicated
        ]

        if set(roles) != {
            "before",
            "after",
        }:
            continue

        before_image = deduplicated[
            roles.index("before")
        ]

        after_image = deduplicated[
            roles.index("after")
        ]

        before_date = extract_date_label(
            " ".join(
                [
                    before_image.get("alt", ""),
                    before_image.get("title", ""),
                    before_image.get("url", ""),
                ]
            )
        )

        after_date = extract_date_label(
            " ".join(
                [
                    after_image.get("alt", ""),
                    after_image.get("title", ""),
                    after_image.get("url", ""),
                ]
            )
        )

        return [
            {
                "role": "before",
                "url": before_image["url"],
                "label": "BEFORE",
                "date": before_date,
            },
            {
                "role": "after",
                "url": after_image["url"],
                "label": "AFTER",
                "date": after_date,
            },
        ]

    return None


def enrich_record(record: dict) -> dict:
    """
    Preserve the archive-selected hero unless the article clearly supplies
    a primary video or a genuine explicit comparison widget.
    """

    article_url = str(
        record.get("article_url", "")
    )

    if not article_url:
        return record

    article_html = download_text(article_url)

    video_urls = extract_video_urls(
        article_html,
        article_url,
    )

    if video_urls:
        selected_video = max(
            video_urls,
            key=video_score,
        )

        record["media_type"] = "video"
        record["media_url"] = selected_video
        record["video_url"] = selected_video
        record["media_items"] = []

        print(
            f"{record.get('title')}: "
            f"video -> {selected_video}"
        )

        return record

    comparison = detect_explicit_comparison(
        article_html,
        article_url,
    )

    if comparison:
        record["media_type"] = "comparison"
        record["media_items"] = comparison
        record["media_url"] = comparison[0]["url"]
        record["image_url"] = comparison[0]["url"]
        record["video_url"] = ""

        print(
            f"{record.get('title')}: "
            "explicit comparison preserved"
        )

        return record

    # The hero resolver has already made the archive-selected image the
    # default. Do not run semantic image ranking here.
    record["media_type"] = "image"
    record["video_url"] = ""
    record["media_items"] = []

    if not record.get("media_url"):
        record["media_url"] = record.get(
            "image_url",
            "",
        )

    if not record.get("image_url"):
        record["image_url"] = record.get(
            "media_url",
            "",
        )

    print(
        f"{record.get('title')}: "
        "archive hero preserved"
    )

    return record


def main() -> None:
    """Apply only high-confidence media upgrades."""

    payload = json.loads(
        DATA_PATH.read_text(
            encoding="utf-8"
        )
    )

    payload["items"] = [
        enrich_record(
            dict(record)
        )
        for record
        in payload.get(
            "items",
            [],
        )
    ]

    DATA_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
