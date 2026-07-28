from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

DATA_PATH = Path("data/nasa-earth-observatory.json")

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

IOTD_PATH = "/eo/images/iotd/"

TOPIC_STOPWORDS = {
    "about",
    "above",
    "across",
    "after",
    "again",
    "against",
    "along",
    "also",
    "among",
    "around",
    "because",
    "before",
    "being",
    "below",
    "between",
    "both",
    "could",
    "during",
    "each",
    "from",
    "have",
    "into",
    "itself",
    "more",
    "most",
    "other",
    "over",
    "same",
    "some",
    "such",
    "than",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "very",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "earth",
    "image",
    "images",
    "observatory",
    "nasa",
}

DIRECT_DEPICTION_TERMS = {
    "aerial",
    "close-up",
    "closeup",
    "depicts",
    "drone",
    "photograph",
    "photographed",
    "shows",
    "visible",
}

BROAD_CONTEXT_TERMS = {
    "context",
    "locator",
    "location",
    "map",
    "overview",
    "regional",
    "wide view",
}

COMPARISON_CONTROL_TERMS = {
    "2-up",
    "before",
    "comparison",
    "curtain",
    "toggle",
}

REJECTED_IMAGE_TERMS = {
    "banner",
    "fallback",
    "generic",
    "icon",
    "locatormap",
    "location-map",
    "logo",
    "map-placeholder",
    "poster",
    "thumbnail",
}

MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def clean_text(value: str) -> str:
    """Convert HTML or irregular whitespace into clean text."""

    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_url(url: str, base_url: str) -> str:
    """Normalize escaped and relative media URLs."""

    normalized = html.unescape((url or "").strip())

    normalized = normalized.replace("\\/", "/")
    normalized = normalized.replace("\\u002F", "/")
    normalized = normalized.replace("\\u002f", "/")
    normalized = normalized.replace("\\u003A", ":")
    normalized = normalized.replace("\\u003a", ":")

    return urllib.parse.urljoin(base_url, normalized)


def download_text(url: str) -> str:
    """Download an article page as text."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*;q=0.8",
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


def normalized_words(value: str) -> list[str]:
    """Return meaningful lowercase terms for topical comparison."""

    words = re.findall(
        r"[a-z0-9][a-z0-9'-]*",
        (value or "").lower(),
    )

    return [
        word
        for word in words
        if (
            len(word) >= 4
            and word not in TOPIC_STOPWORDS
        )
    ]


def topic_terms(
    title: str,
    short_description: str,
) -> set[str]:
    """Build a compact vocabulary describing the story."""

    return set(
        normalized_words(
            f"{title} {short_description}"
        )
    )


class ArticleMediaParser(HTMLParser):
    """
    Collect images, captions, alt text, nearby prose, and MP4 URLs.

    Figure captions are associated with every image inside the same
    <figure>. This is more reliable than using only text appearing before
    an image, because NASA often places the useful caption after the image.
    """

    TEXT_TAGS = {
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "p",
    }

    def __init__(self, base_url: str) -> None:
        super().__init__()

        self.base_url = base_url
        self.images: list[dict[str, str]] = []
        self.videos: list[str] = []

        self.recent_text: list[str] = []

        self.current_text_tag: Optional[str] = None
        self.current_text_parts: list[str] = []

        self.figure_stack: list[list[int]] = []
        self.current_caption_targets: list[int] = []

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

        if tag == "figure":
            self.figure_stack.append([])

        if tag in self.TEXT_TAGS:
            self.current_text_tag = tag
            self.current_text_parts = []

            if tag == "figcaption" and self.figure_stack:
                self.current_caption_targets = list(
                    self.figure_stack[-1]
                )

        if tag == "img":
            self._collect_image(attributes)

        if tag in {"a", "source", "video"}:
            self._collect_video_urls(attributes)

    def handle_data(self, data: str) -> None:
        if self.current_text_tag:
            self.current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag == self.current_text_tag:
            text = clean_text(
                " ".join(self.current_text_parts)
            )

            if text:
                if tag == "figcaption":
                    for index in self.current_caption_targets:
                        self.images[index]["caption"] = clean_text(
                            " ".join(
                                [
                                    self.images[index].get(
                                        "caption",
                                        "",
                                    ),
                                    text,
                                ]
                            )
                        )
                else:
                    self.recent_text.append(text)
                    self.recent_text = self.recent_text[-8:]

            self.current_text_tag = None
            self.current_text_parts = []
            self.current_caption_targets = []

        if tag == "figure" and self.figure_stack:
            self.figure_stack.pop()

    def _collect_image(
        self,
        attributes: dict[str, str],
    ) -> None:
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
                urls.append(raw_url)

        srcset = attributes.get("srcset", "")

        if srcset:
            for candidate in srcset.split(","):
                raw_url = candidate.strip().split(" ")[0]

                if raw_url:
                    urls.append(raw_url)

        nearby_text = clean_text(
            " ".join(self.recent_text[-4:])
        )

        alt_text = clean_text(
            attributes.get("alt", "")
        )

        title_text = clean_text(
            attributes.get("title", "")
        )

        for raw_url in urls:
            url = normalize_url(
                raw_url,
                self.base_url,
            )

            if not url:
                continue

            image_index = len(self.images)

            self.images.append(
                {
                    "url": url,
                    "alt": alt_text,
                    "title": title_text,
                    "caption": "",
                    "nearby_text": nearby_text,
                }
            )

            if self.figure_stack:
                self.figure_stack[-1].append(
                    image_index
                )

    def _collect_video_urls(
        self,
        attributes: dict[str, str],
    ) -> None:
        for attribute_name in (
            "href",
            "src",
            "data-src",
        ):
            raw_url = attributes.get(
                attribute_name,
                "",
            ).strip()

            if not raw_url:
                continue

            if ".mp4" not in raw_url.lower():
                continue

            url = normalize_url(
                raw_url,
                self.base_url,
            )

            if url not in self.videos:
                self.videos.append(url)


def is_story_image(url: str) -> bool:
    """Reject page graphics and retain Earth Observatory story assets."""

    lowered = urllib.parse.unquote(
        url
    ).lower()

    if IOTD_PATH not in lowered:
        return False

    if "_th." in lowered:
        return False

    return not any(
        term in lowered
        for term in REJECTED_IMAGE_TERMS
    )


def canonical_image_key(url: str) -> str:
    """
    Build a key that groups differently sized versions of one NASA image.

    NASA often exposes the same underlying asset through src, srcset, and
    transformed URLs. The filename is usually stable across those variants.
    """

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


def image_quality_score(
    image: dict[str, str],
) -> float:
    """Estimate the technical quality of an image candidate."""

    lowered_url = urllib.parse.unquote(
        image["url"]
    ).lower()

    score = 0.0

    if is_story_image(image["url"]):
        score += 1000.0

    if "_lrg." in lowered_url:
        score += 350.0

    if "cq5dam.web.1280" in lowered_url:
        score += 130.0

    if "fit=clip" in lowered_url:
        score += 30.0

    width_match = re.search(
        r"(?:[?&]w=|width=)(\d{3,5})",
        lowered_url,
    )

    if width_match:
        score += min(
            int(width_match.group(1)),
            3000,
        ) / 20.0

    return score


def deduplicate_images(
    images: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Keep the best version of each underlying story image."""

    best: dict[str, dict[str, str]] = {}

    for image in images:
        if not is_story_image(image["url"]):
            continue

        key = canonical_image_key(
            image["url"]
        )

        if not key:
            continue

        existing = best.get(key)

        if (
            existing is None
            or image_quality_score(image)
            > image_quality_score(existing)
        ):
            best[key] = image

    return list(best.values())


def image_descriptive_text(
    image: dict[str, str],
) -> str:
    """Combine the text most likely to describe what the image depicts."""

    return clean_text(
        " ".join(
            [
                image.get("alt", ""),
                image.get("title", ""),
                image.get("caption", ""),
                image.get("nearby_text", ""),
            ]
        )
    )


def topical_overlap_score(
    image: dict[str, str],
    central_terms: set[str],
) -> float:
    """
    Score how directly an image depicts the central subject.

    Caption, alt text, and title receive substantially more weight than
    nearby prose or filenames. This favors a specific subject photograph
    over a broad contextual view when NASA supplies both.
    """

    alt_words = set(
        normalized_words(
            image.get("alt", "")
        )
    )

    title_words = set(
        normalized_words(
            image.get("title", "")
        )
    )

    caption_words = set(
        normalized_words(
            image.get("caption", "")
        )
    )

    nearby_words = set(
        normalized_words(
            image.get("nearby_text", "")
        )
    )

    url_words = set(
        normalized_words(
            urllib.parse.unquote(
                image.get("url", "")
            )
        )
    )

    score = 0.0

    score += len(
        alt_words & central_terms
    ) * 16.0

    score += len(
        title_words & central_terms
    ) * 14.0

    score += len(
        caption_words & central_terms
    ) * 20.0

    score += len(
        nearby_words & central_terms
    ) * 3.0

    score += len(
        url_words & central_terms
    ) * 2.0

    descriptive_text = (
        image_descriptive_text(image)
        .lower()
    )

    direct_hits = sum(
        1
        for term in DIRECT_DEPICTION_TERMS
        if term in descriptive_text
    )

    broad_hits = sum(
        1
        for term in BROAD_CONTEXT_TERMS
        if term in descriptive_text
    )

    score += direct_hits * 5.0
    score -= broad_hits * 3.0

    # Reward descriptive metadata itself. A well-captioned image is a more
    # defensible editorial choice than an equally relevant unlabeled asset.
    if image.get("caption"):
        score += 8.0

    if image.get("alt"):
        score += 4.0

    return score


def select_best_story_image(
    images: list[dict[str, str]],
    title: str,
    short_description: str,
    current_url: str = "",
) -> Optional[dict[str, str]]:
    """
    Select the image that best communicates the article's central subject.

    The current builder choice receives only a small stability bonus. It will
    remain selected when candidates are similar, but a much more topically
    specific image can replace it.
    """

    if not images:
        return None

    central_terms = topic_terms(
        title,
        short_description,
    )

    ranked: list[
        tuple[
            float,
            float,
            dict[str, str],
        ]
    ] = []

    current_key = canonical_image_key(
        current_url
    ) if current_url else ""

    for image in images:
        editorial_score = topical_overlap_score(
            image,
            central_terms,
        )

        quality_score = image_quality_score(
            image
        )

        if (
            current_key
            and canonical_image_key(image["url"])
            == current_key
        ):
            editorial_score += 6.0

        ranked.append(
            (
                editorial_score,
                quality_score,
                image,
            )
        )

    ranked.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    return ranked[0][2]


def extract_compact_dates(
    value: str,
) -> list[str]:
    """Extract YYYYMMDD dates from URLs and captions."""

    matches = re.findall(
        r"(?<!\d)"
        r"((?:19|20)\d{2})"
        r"[-_/]?"
        r"(0[1-9]|1[0-2])"
        r"[-_/]?"
        r"(0[1-9]|[12]\d|3[01])"
        r"(?!\d)",
        value or "",
    )

    return [
        f"{year}{month}{day}"
        for year, month, day in matches
    ]


def display_date(
    compact_date: str,
) -> str:
    """Convert YYYYMMDD into a display date."""

    parsed = datetime.strptime(
        compact_date,
        "%Y%m%d",
    )

    return (
        f"{MONTH_NAMES[parsed.month]} "
        f"{parsed.day}, "
        f"{parsed.year}"
    )


def comparison_signal_score(
    article_html: str,
) -> int:
    """Measure whether NASA presents the article as a comparison."""

    lowered = article_html.lower()

    return sum(
        1
        for term in COMPARISON_CONTROL_TERMS
        if term in lowered
    )


def detect_comparison(
    article_html: str,
    images: list[dict[str, str]],
) -> Optional[list[dict[str, str]]]:
    """
    Detect a before-and-after image pair without story-specific rules.

    The article must contain multiple comparison-interface signals and two
    distinct dated story images. The earliest image becomes BEFORE and the
    latest becomes AFTER.
    """

    if comparison_signal_score(article_html) < 2:
        return None

    candidates_by_date: dict[
        str,
        dict[str, str],
    ] = {}

    for image in images:
        descriptive_text = image_descriptive_text(
            image
        )

        search_text = (
            urllib.parse.unquote(
                image["url"]
            )
            + " "
            + descriptive_text
        )

        for compact_date in extract_compact_dates(
            search_text
        ):
            existing = candidates_by_date.get(
                compact_date
            )

            if (
                existing is None
                or image_quality_score(image)
                > image_quality_score(existing)
            ):
                candidates_by_date[
                    compact_date
                ] = image

    if len(candidates_by_date) < 2:
        return None

    ordered_dates = sorted(
        candidates_by_date
    )

    before_date = ordered_dates[0]
    after_date = ordered_dates[-1]

    before_image = candidates_by_date[
        before_date
    ]

    after_image = candidates_by_date[
        after_date
    ]

    if (
        canonical_image_key(
            before_image["url"]
        )
        == canonical_image_key(
            after_image["url"]
        )
    ):
        return None

    return [
        {
            "role": "before",
            "url": before_image["url"],
            "label": "BEFORE",
            "date": display_date(before_date),
        },
        {
            "role": "after",
            "url": after_image["url"],
            "label": "AFTER",
            "date": display_date(after_date),
        },
    ]


def extract_embedded_mp4_urls(
    article_html: str,
    article_url: str,
) -> list[str]:
    """Find MP4 addresses stored in ordinary markup or embedded page JSON."""

    prepared = html.unescape(
        article_html
    )

    prepared = prepared.replace("\\/", "/")
    prepared = prepared.replace("\\u002F", "/")
    prepared = prepared.replace("\\u002f", "/")
    prepared = prepared.replace("\\u003A", ":")
    prepared = prepared.replace("\\u003a", ":")

    matches = re.findall(
        r'https?://[^"\'<>\s]+?'
        r'\.mp4'
        r'(?:\?[^"\'<>\s]*)?',
        prepared,
        flags=re.IGNORECASE,
    )

    urls: list[str] = []

    for match in matches:
        url = normalize_url(
            match,
            article_url,
        )

        if url not in urls:
            urls.append(url)

    return urls


def video_score(url: str) -> int:
    """Rank NASA Earth Observatory story videos."""

    lowered = urllib.parse.unquote(
        url
    ).lower()

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


def enrich_record(
    record: dict,
) -> dict:
    """Add the most suitable image, video, or comparison to one record."""

    article_url = str(
        record.get("article_url", "")
    )

    if not article_url:
        record.setdefault(
            "media_items",
            [],
        )
        return record

    article_html = download_text(
        article_url
    )

    parser = ArticleMediaParser(
        article_url
    )

    parser.feed(article_html)

    images = deduplicate_images(
        parser.images
    )

    comparison = detect_comparison(
        article_html,
        images,
    )

    if comparison:
        record["media_type"] = "comparison"
        record["media_items"] = comparison
        record["media_url"] = comparison[0]["url"]
        record["image_url"] = comparison[0]["url"]
        record["video_url"] = ""

        return record

    video_urls = list(
        parser.videos
    )

    for video_url in extract_embedded_mp4_urls(
        article_html,
        article_url,
    ):
        if video_url not in video_urls:
            video_urls.append(video_url)

    if video_urls:
        selected_video = max(
            video_urls,
            key=video_score,
        )

        record["media_type"] = "video"
        record["media_url"] = selected_video
        record["video_url"] = selected_video
        record["media_items"] = []

        return record

    selected_image = select_best_story_image(
        images=images,
        title=str(
            record.get("title", "")
        ),
        short_description=str(
            record.get(
                "short_description",
                "",
            )
        ),
        current_url=str(
            record.get("image_url", "")
        ),
    )

    if selected_image:
        record["media_type"] = "image"
        record["media_url"] = selected_image["url"]
        record["image_url"] = selected_image["url"]
        record["video_url"] = ""
        record["media_items"] = []

        selected_description = clean_text(
            " ".join(
                [
                    selected_image.get(
                        "alt",
                        "",
                    ),
                    selected_image.get(
                        "caption",
                        "",
                    ),
                ]
            )
        )

        if selected_description:
            record["image_alt"] = selected_description

    else:
        record.setdefault(
            "media_type",
            "image",
        )

        record.setdefault(
            "media_url",
            record.get(
                "image_url",
                "",
            ),
        )

        record.setdefault(
            "media_items",
            [],
        )

    return record


def main() -> None:
    """Enrich the current seven-record slideshow dataset."""

    payload = json.loads(
        DATA_PATH.read_text(
            encoding="utf-8"
        )
    )

    items = payload.get(
        "items",
        [],
    )

    enriched_items: list[dict] = []

    for original_record in items:
        record = enrich_record(
            dict(original_record)
        )

        enriched_items.append(record)

        if record.get("media_type") == "comparison":
            media_summary = "comparison: " + " | ".join(
                (
                    f"{item.get('label', '')} "
                    f"{item.get('date', '')} "
                    f"{item.get('url', '')}"
                ).strip()
                for item in record.get(
                    "media_items",
                    [],
                )
            )
        else:
            media_summary = (
                f"{record.get('media_type')}: "
                f"{record.get('media_url', '')}"
            )

        print(
            f"{record.get('title')}: "
            f"{media_summary}"
        )

    payload["items"] = enriched_items

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
