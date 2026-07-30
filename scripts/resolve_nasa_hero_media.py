from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

DATA_PATH = Path(
    "data/nasa-earth-observatory.json"
)

USER_AGENT = (
    "FAU-Geosciences-Signage/1.0 "
    "(NASA Earth Observatory slideshow)"
)

IOTD_PATH = "/eo/images/iotd/"

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


def download_text(url: str) -> str:
    """Download a NASA article page."""

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


def clean_url(
    url: str,
    base_url: str,
) -> str:
    """Normalize escaped and relative NASA asset URLs."""

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


def largest_srcset_urls(
    srcset: str,
    base_url: str,
) -> list[str]:
    """Return srcset candidates in descending size order."""

    candidates: list[
        tuple[int, str]
    ] = []

    for raw_candidate in (
        srcset or ""
    ).split(","):
        parts = raw_candidate.strip().split()

        if not parts:
            continue

        descriptor_score = 0

        if len(parts) > 1:
            match = re.match(
                r"(\d+)(?:w|x)",
                parts[1].lower(),
            )

            if match:
                descriptor_score = int(
                    match.group(1)
                )

        candidates.append(
            (
                descriptor_score,
                clean_url(
                    parts[0],
                    base_url,
                ),
            )
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        url
        for _, url
        in candidates
    ]


def asset_basename(url: str) -> str:
    """Return a stable filename for matching resized variants."""

    parsed = urllib.parse.urlparse(
        html.unescape(url or "")
    )

    return Path(
        urllib.parse.unquote(
            parsed.path
        )
    ).name.lower()


def asset_stem(url: str) -> str:
    """Return a canonical stem without common size suffixes."""

    stem = Path(
        asset_basename(url)
    ).stem.lower()

    stem = re.sub(
        r"(?:_|-)(?:lrg|large|medium|small|thumb|thumbnail)$",
        "",
        stem,
    )

    stem = re.sub(
        r"(?:_|-)\d{2,5}x\d{2,5}$",
        "",
        stem,
    )

    return stem


def is_story_asset(url: str) -> bool:
    """Retain substantive Earth Observatory story assets."""

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


def candidate_quality(url: str) -> float:
    """Estimate available image resolution from an asset URL."""

    lowered = urllib.parse.unquote(
        url
    ).lower()

    score = 0.0

    if "_lrg." in lowered:
        score += 10000.0

    if "/content/dam/" in lowered:
        score += 3500.0

    if "/dynamicimage/" in lowered:
        score += 1000.0

    for pattern in (
        r"(?:[?&]w=)(\d{2,5})",
        r"(?:[?&]width=)(\d{2,5})",
        r"cq5dam\.web\.(\d{2,5})",
    ):
        match = re.search(
            pattern,
            lowered,
        )

        if match:
            score += min(
                int(match.group(1)),
                10000,
            )

    dimensions = re.search(
        r"(\d{3,5})x(\d{3,5})",
        lowered,
    )

    if dimensions:
        score += (
            int(dimensions.group(1))
            * int(dimensions.group(2))
        ) / 10000.0

    return score


def collect_article_images(
    article_html: str,
    article_url: str,
) -> list[str]:
    """Collect full-resolution candidates from markup and downloads."""

    soup = BeautifulSoup(
        article_html,
        "html.parser",
    )

    urls: list[str] = []

    def add(raw_url: str) -> None:
        url = clean_url(
            raw_url,
            article_url,
        )

        if (
            url
            and is_story_asset(url)
            and url not in urls
        ):
            urls.append(url)

    for meta in soup.find_all("meta"):
        property_name = str(
            meta.get("property")
            or meta.get("name")
            or ""
        ).lower()

        if property_name in {
            "og:image",
            "og:image:url",
            "twitter:image",
        }:
            add(
                str(
                    meta.get(
                        "content",
                        "",
                    )
                )
            )

    for image in soup.find_all("img"):
        if not isinstance(
            image,
            Tag,
        ):
            continue

        for url in largest_srcset_urls(
            str(
                image.get(
                    "srcset",
                    "",
                )
            ),
            article_url,
        ):
            add(url)

        for attribute_name in (
            "data-src",
            "data-lazy-src",
            "src",
        ):
            add(
                str(
                    image.get(
                        attribute_name,
                        "",
                    )
                )
            )

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = str(
            link.get(
                "href",
                "",
            )
        )

        if re.search(
            r"\.(?:jpe?g|png|webp)(?:\?|$)",
            href,
            flags=re.IGNORECASE,
        ):
            add(href)

    prepared = html.unescape(
        article_html
    )

    prepared = prepared.replace("\\/", "/")
    prepared = prepared.replace("\\u002F", "/")
    prepared = prepared.replace("\\u002f", "/")

    for raw_url in re.findall(
        r'https?://[^"\'<>\s]+?'
        r'\.(?:jpg|jpeg|png|webp)'
        r'(?:\?[^"\'<>\s]*)?',
        prepared,
        flags=re.IGNORECASE,
    ):
        add(raw_url)

    return urls


def thumbnail_match_score(
    thumbnail_url: str,
    candidate_url: str,
) -> float:
    """Score whether an article asset matches the archive thumbnail."""

    thumbnail_name = asset_basename(
        thumbnail_url
    )

    candidate_name = asset_basename(
        candidate_url
    )

    thumbnail_stem = asset_stem(
        thumbnail_url
    )

    candidate_stem = asset_stem(
        candidate_url
    )

    score = 0.0

    if (
        thumbnail_name
        and thumbnail_name
        == candidate_name
    ):
        score += 100000.0

    if (
        thumbnail_stem
        and thumbnail_stem
        == candidate_stem
    ):
        score += 80000.0

    thumbnail_tokens = set(
        re.findall(
            r"[a-z0-9]+",
            thumbnail_stem,
        )
    )

    candidate_tokens = set(
        re.findall(
            r"[a-z0-9]+",
            candidate_stem,
        )
    )

    meaningful_tokens = {
        token
        for token
        in thumbnail_tokens
        if len(token) >= 4
    }

    if meaningful_tokens:
        overlap = len(
            meaningful_tokens
            & candidate_tokens
        )

        score += overlap * 2000.0

        if meaningful_tokens.issubset(
            candidate_tokens
        ):
            score += 12000.0

    score += candidate_quality(
        candidate_url
    )

    return score


def high_resolution_thumbnail_url(
    thumbnail_url: str,
) -> str:
    """Request a large version of a NASA dynamic-image thumbnail."""

    if not thumbnail_url:
        return ""

    parsed = urllib.parse.urlparse(
        thumbnail_url
    )

    query = urllib.parse.parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    query["w"] = ["3840"]
    query.pop("h", None)

    return urllib.parse.urlunparse(
        parsed._replace(
            query=urllib.parse.urlencode(
                query,
                doseq=True,
            )
        )
    )


def select_hero_image(
    thumbnail_url: str,
    article_candidates: list[str],
) -> str:
    """Resolve NASA's archive thumbnail to a large matching asset."""

    if not thumbnail_url:
        return ""

    matching_candidates = [
        candidate
        for candidate
        in article_candidates
        if thumbnail_match_score(
            thumbnail_url,
            candidate,
        )
        >= 12000.0
    ]

    if matching_candidates:
        return max(
            matching_candidates,
            key=lambda candidate:
                thumbnail_match_score(
                    thumbnail_url,
                    candidate,
                ),
        )

    if is_story_asset(
        thumbnail_url
    ):
        return high_resolution_thumbnail_url(
            thumbnail_url
        )

    return ""


def main() -> None:
    """
    Reset every non-video record to NASA's archive-selected hero image.

    This intentionally clears any comparison classification produced by an
    earlier builder. The subsequent media-enrichment step may restore a
    comparison only when it finds an explicit, unambiguous comparison widget.
    """

    payload: dict[str, Any] = json.loads(
        DATA_PATH.read_text(
            encoding="utf-8"
        )
    )

    for record in payload.get(
        "items",
        [],
    ):
        article_url = str(
            record.get(
                "article_url",
                "",
            )
        )

        thumbnail_url = str(
            record.get(
                "archive_thumbnail_url",
                "",
            )
        )

        if not article_url:
            print(
                f"{record.get('title')}: "
                "no article URL"
            )
            continue

        article_html = download_text(
            article_url
        )

        article_candidates = (
            collect_article_images(
                article_html,
                article_url,
            )
        )

        hero_url = select_hero_image(
            thumbnail_url,
            article_candidates,
        )

        if not hero_url:
            print(
                f"{record.get('title')}: "
                "archive hero could not be resolved; "
                "keeping current image"
            )

            record["media_type"] = "image"
            record["video_url"] = ""
            record["media_items"] = []

            continue

        record["media_type"] = "image"
        record["media_url"] = hero_url
        record["image_url"] = hero_url
        record["video_url"] = ""
        record["media_items"] = []

        print(
            f"{record.get('title')}: "
            f"archive hero -> {hero_url}"
        )

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
