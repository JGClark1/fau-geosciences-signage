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

ABSTRACT_TARGET_WORDS = 120
ABSTRACT_MAX_WORDS = 145
ABSTRACT_MIN_WORDS = 75

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

STORY_STOP_HEADINGS = {
    "downloads",
    "image details",
    "references",
    "references & resources",
    "references and resources",
    "you may also be interested in",
}

INTRODUCTION_TERMS = {
    "acquired",
    "animation",
    "captured",
    "features",
    "image",
    "located",
    "observed",
    "region",
    "shows",
    "shown",
}

EXPLANATION_TERMS = {
    "according",
    "because",
    "caused",
    "come from",
    "data",
    "due to",
    "enabled",
    "explains",
    "formed",
    "generated",
    "incorporates",
    "influenced",
    "model",
    "produced",
    "resulted",
    "reveals",
    "tracked",
    "using",
}

SIGNIFICANCE_TERMS = {
    "affect",
    "allows",
    "benefit",
    "contributes",
    "important",
    "indicates",
    "matters",
    "provides",
    "reveals",
    "scientists",
    "shows",
    "significance",
    "supports",
    "therefore",
    "understand",
}

LOCATION_TERMS = {
    "africa",
    "america",
    "antarctica",
    "asia",
    "canada",
    "city",
    "coast",
    "europe",
    "florida",
    "island",
    "north america",
    "ocean",
    "region",
    "state",
    "united states",
    "u.s.",
}

TIME_TERMS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "day",
    "week",
    "month",
    "year",
    "during",
    "since",
}

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

REJECTED_SENTENCE_PHRASES = {
    "astronaut photography from the international space station",
    "consider upgrading to a web browser",
    "download this image",
    "enable javascript",
    "for captioning to other images taken by astronauts",
    "help astronauts take pictures of earth",
    "images freely available on the internet",
    "make those images freely available",
    "page last updated",
    "responsible nasa official",
    "supports html5 video",
    "the international space station program supports",
    "to view this video",
}

WEAK_DETAIL_PHRASES = {
    "camera using a focal length",
    "digital camera using a focal length",
    "focal length of",
    "image was acquired",
    "photograph was acquired",
    "was acquired on",
}

SENTENCE_ABBREVIATIONS = {
    "U.S.": "U§S§",
    "D.C.": "D§C§",
    "B.C.": "B§C§",
    "U.K.": "U§K§",
    "Dr.": "Dr§",
    "Mr.": "Mr§",
    "Mrs.": "Mrs§",
    "Ms.": "Ms§",
    "St.": "St§",
    "No.": "No§",
    "Fig.": "Fig§",
    "e.g.": "e§g§",
    "i.e.": "i§e§",
}


class ArticleParser(HTMLParser):
    """Extract metadata, story text, images, and media links."""

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

    MEDIA_TAGS = {
        "a",
        "source",
        "video",
    }

    def __init__(self) -> None:
        super().__init__()

        self.metadata: dict[str, str] = {}
        self.blocks: list[tuple[str, str]] = []
        self.image_urls: list[str] = []
        self.media_urls: list[str] = []

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
            for attribute_name in (
                "src",
                "data-src",
                "data-lazy-src",
            ):
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

        if tag in self.MEDIA_TAGS:
            for attribute_name in (
                "href",
                "src",
                "data-src",
            ):
                media_url = attributes.get(
                    attribute_name,
                    "",
                ).strip()

                if media_url:
                    self.media_urls.append(media_url)

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
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
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


def split_sentences(value: str) -> list[str]:
    """Split prose into complete sentences."""

    protected = value

    for abbreviation, replacement in (
        SENTENCE_ABBREVIATIONS.items()
    ):
        protected = protected.replace(
            abbreviation,
            replacement,
        )

    parts = re.split(
        r"(?<=[.!?])\s+(?=[A-Z0-9“\"'])",
        protected,
    )

    sentences: list[str] = []

    for part in parts:
        restored = part

        for abbreviation, replacement in (
            SENTENCE_ABBREVIATIONS.items()
        ):
            restored = restored.replace(
                replacement,
                abbreviation,
            )

        restored = clean_text(restored)

        if restored:
            sentences.append(restored)

    return sentences


def is_complete_sentence(value: str) -> bool:
    """Return whether text ends as a complete sentence."""

    return value.rstrip().endswith(
        (
            ".",
            "!",
            "?",
            ".”",
            "!”",
            "?”",
        )
    )


def normalized_words(value: str) -> list[str]:
    """Return normalized meaningful words."""

    words = re.findall(
        r"[a-z0-9][a-z0-9'-]*",
        value.lower(),
    )

    return [
        word
        for word in words
        if (
            len(word) >= 4
            and word not in TOPIC_STOPWORDS
        )
    ]


def build_topic_terms(
    title: str,
    short_description: str,
) -> set[str]:
    """Build terms describing the article's central subject."""

    return set(
        normalized_words(
            f"{title} {short_description}"
        )
    )


def topic_overlap(
    sentence: str,
    topic_terms: set[str],
) -> int:
    """Count central article terms appearing in a sentence."""

    sentence_words = set(
        normalized_words(sentence)
    )

    return len(
        sentence_words & topic_terms
    )


def contains_rejected_phrase(value: str) -> bool:
    """Identify credits, warnings, and institutional boilerplate."""

    lowered = value.lower()

    return any(
        phrase in lowered
        for phrase in REJECTED_SENTENCE_PHRASES
    )


def contains_weak_detail(value: str) -> bool:
    """Identify low-value acquisition or camera details."""

    lowered = value.lower()

    return any(
        phrase in lowered
        for phrase in WEAK_DETAIL_PHRASES
    )


def looks_like_story_paragraph(value: str) -> bool:
    """Reject captions, credits, warnings, and boilerplate."""

    if word_count(value) < 10:
        return False

    lowered = value.lower().strip()

    rejected_starts = (
        "accessed ",
        "download ",
        "image of the day",
        "image:",
        "jpeg",
        "nasa earth observatory animation by",
        "nasa earth observatory image by",
        "nasa earth observatory images by",
        "nasa earth observatory video by",
        "references",
        "story by ",
        "to view this video",
        "view more images",
    )

    if lowered.startswith(rejected_starts):
        return False

    if contains_rejected_phrase(value):
        return False

    return True


def looks_like_story_sentence(value: str) -> bool:
    """Determine whether a sentence is suitable for an abstract."""

    if word_count(value) < 7:
        return False

    if not is_complete_sentence(value):
        return False

    if contains_rejected_phrase(value):
        return False

    return True


def extract_story_paragraphs(
    blocks: list[tuple[str, str]],
) -> list[str]:
    """Extract substantive article paragraphs before references."""

    paragraphs: list[str] = []
    seen: set[str] = set()

    for tag, text in blocks:
        normalized_heading = (
            text.lower()
            .strip()
            .rstrip(":")
        )

        if (
            tag.startswith("h")
            and normalized_heading in STORY_STOP_HEADINGS
        ):
            break

        if tag != "p":
            continue

        if not looks_like_story_paragraph(text):
            continue

        normalized = text.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        paragraphs.append(text)

    return paragraphs


def keyword_hits(
    sentence: str,
    terms: set[str],
) -> int:
    """Count useful terms found in a sentence."""

    lowered = sentence.lower()

    return sum(
        1
        for term in terms
        if term in lowered
    )


def sentence_score(
    sentence: str,
    index: int,
    total: int,
    topic_terms: set[str],
) -> float:
    """Score a sentence for relevance and information value."""

    if not looks_like_story_sentence(sentence):
        return -100.0

    words = word_count(sentence)
    overlap = topic_overlap(
        sentence,
        topic_terms,
    )

    score = 0.0

    score += min(words, 36) * 0.06
    score += overlap * 3.8

    score += keyword_hits(
        sentence,
        INTRODUCTION_TERMS,
    ) * 0.8

    score += keyword_hits(
        sentence,
        EXPLANATION_TERMS,
    ) * 1.8

    score += keyword_hits(
        sentence,
        SIGNIFICANCE_TERMS,
    ) * 1.8

    score += keyword_hits(
        sentence,
        LOCATION_TERMS,
    ) * 0.5

    score += keyword_hits(
        sentence,
        TIME_TERMS,
    ) * 0.4

    if total > 1:
        relative_position = index / (total - 1)
    else:
        relative_position = 0.0

    if relative_position <= 0.25:
        score += 1.5

    if 0.25 < relative_position < 0.75:
        score += 0.5

    if relative_position >= 0.70:
        score += 1.8

    if overlap == 0:
        score -= 3.0

    if contains_weak_detail(sentence):
        score -= 7.0

    if sentence.endswith(":"):
        score -= 4.0

    return score


def normalized_sentence(value: str) -> str:
    """Normalize a sentence for duplicate comparison."""

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def sentence_similarity(
    first: str,
    second: str,
) -> float:
    """Estimate similarity using shared normalized words."""

    first_words = set(
        normalized_sentence(first).split()
    )

    second_words = set(
        normalized_sentence(second).split()
    )

    if not first_words or not second_words:
        return 0.0

    overlap = len(first_words & second_words)
    smaller = min(
        len(first_words),
        len(second_words),
    )

    return overlap / smaller


def append_if_useful(
    selected: set[int],
    candidate_index: int,
    sentences: list[str],
    maximum_words: int,
    minimum_topic_overlap: int = 0,
    topic_terms: Optional[set[str]] = None,
) -> bool:
    """Add a candidate when relevant, nonrepetitive, and within length."""

    if candidate_index in selected:
        return False

    candidate = sentences[candidate_index]

    if not looks_like_story_sentence(candidate):
        return False

    if topic_terms is not None:
        if (
            topic_overlap(candidate, topic_terms)
            < minimum_topic_overlap
        ):
            return False

    for existing_index in selected:
        if sentence_similarity(
            candidate,
            sentences[existing_index],
        ) >= 0.72:
            return False

    proposed_indices = sorted(
        selected | {candidate_index}
    )

    proposed_text = " ".join(
        sentences[index]
        for index in proposed_indices
    )

    if word_count(proposed_text) > maximum_words:
        return False

    selected.add(candidate_index)
    return True


def build_extractive_abstract(
    paragraphs: list[str],
    title: str,
    short_description: str,
) -> str:
    """
    Build an extractive abstract grounded in the article's topic.

    The NASA short description supplies the opening summary.
    Additional sentences are chosen for explanation, context,
    and a relevant concluding takeaway.
    """

    topic_terms = build_topic_terms(
        title,
        short_description,
    )

    article_sentences: list[str] = []

    for paragraph in paragraphs:
        for sentence in split_sentences(paragraph):
            if looks_like_story_sentence(sentence):
                article_sentences.append(sentence)

    anchor_sentences = [
        sentence
        for sentence in split_sentences(
            short_description
        )
        if looks_like_story_sentence(sentence)
    ]

    if not anchor_sentences and is_complete_sentence(
        short_description
    ):
        anchor_sentences = [short_description]

    sentences: list[str] = []
    anchor_count = 0

    for sentence in anchor_sentences:
        if not any(
            sentence_similarity(sentence, existing) >= 0.72
            for existing in sentences
        ):
            sentences.append(sentence)
            anchor_count += 1

    for sentence in article_sentences:
        if not any(
            sentence_similarity(sentence, existing) >= 0.72
            for existing in sentences
        ):
            sentences.append(sentence)

    if not sentences:
        return short_description

    total = len(sentences)

    scores = [
        sentence_score(
            sentence,
            index,
            total,
            topic_terms,
        )
        for index, sentence in enumerate(sentences)
    ]

    selected: set[int] = set(
        range(anchor_count)
    )

    article_indices = list(
        range(anchor_count, total)
    )

    relevant_indices = [
        index
        for index in article_indices
        if (
            topic_overlap(
                sentences[index],
                topic_terms,
            ) >= 1
            or keyword_hits(
                sentences[index],
                EXPLANATION_TERMS,
            ) >= 2
        )
    ]

    if not relevant_indices:
        relevant_indices = article_indices

    explanation_ranked = sorted(
        relevant_indices,
        key=lambda index: (
            keyword_hits(
                sentences[index],
                EXPLANATION_TERMS,
            ),
            topic_overlap(
                sentences[index],
                topic_terms,
            ),
            scores[index],
        ),
        reverse=True,
    )

    for candidate_index in explanation_ranked:
        if append_if_useful(
            selected,
            candidate_index,
            sentences,
            ABSTRACT_MAX_WORDS,
            minimum_topic_overlap=1,
            topic_terms=topic_terms,
        ):
            break

    later_relevant = [
        index
        for index in relevant_indices
        if index >= max(
            anchor_count,
            total // 2,
        )
    ]

    conclusion_ranked = sorted(
        later_relevant,
        key=lambda index: (
            keyword_hits(
                sentences[index],
                SIGNIFICANCE_TERMS,
            ),
            topic_overlap(
                sentences[index],
                topic_terms,
            ),
            scores[index],
            index,
        ),
        reverse=True,
    )

    for candidate_index in conclusion_ranked:
        if append_if_useful(
            selected,
            candidate_index,
            sentences,
            ABSTRACT_MAX_WORDS,
            minimum_topic_overlap=1,
            topic_terms=topic_terms,
        ):
            break

    ranked_all = sorted(
        relevant_indices,
        key=lambda index: (
            topic_overlap(
                sentences[index],
                topic_terms,
            ),
            scores[index],
        ),
        reverse=True,
    )

    for candidate_index in ranked_all:
        current_text = " ".join(
            sentences[index]
            for index in sorted(selected)
        )

        if word_count(current_text) >= ABSTRACT_TARGET_WORDS:
            break

        append_if_useful(
            selected,
            candidate_index,
            sentences,
            ABSTRACT_MAX_WORDS,
            minimum_topic_overlap=1,
            topic_terms=topic_terms,
        )

    final_text = " ".join(
        sentences[index]
        for index in sorted(selected)
    ).strip()

    if word_count(final_text) < ABSTRACT_MIN_WORDS:
        fallback_ranked = sorted(
            article_indices,
            key=lambda index: scores[index],
            reverse=True,
        )

        for candidate_index in fallback_ranked:
            append_if_useful(
                selected,
                candidate_index,
                sentences,
                ABSTRACT_MAX_WORDS,
                topic_terms=topic_terms,
            )

            final_text = " ".join(
                sentences[index]
                for index in sorted(selected)
            ).strip()

            if word_count(final_text) >= ABSTRACT_MIN_WORDS:
                break

    return final_text


def extract_instruments_from_html(
    article_html: str,
) -> list[str]:
    """Extract NASA's visible Instruments list."""

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


def normalize_url(
    url: str,
    article_url: str,
) -> str:
    """Normalize an escaped or relative media address."""

    cleaned = html.unescape(url.strip())

    cleaned = cleaned.replace("\\/", "/")
    cleaned = cleaned.replace("\\u002F", "/")
    cleaned = cleaned.replace("\\u002f", "/")
    cleaned = cleaned.replace("\\u003A", ":")
    cleaned = cleaned.replace("\\u003a", ":")

    return urllib.parse.urljoin(
        article_url,
        cleaned,
    )


def extract_raw_mp4_urls(
    article_html: str,
    article_url: str,
) -> list[str]:
    """Find MP4 addresses embedded in markup or page JSON."""

    prepared = html.unescape(article_html)

    prepared = prepared.replace("\\/", "/")
    prepared = prepared.replace("\\u002F", "/")
    prepared = prepared.replace("\\u002f", "/")
    prepared = prepared.replace("\\u003A", ":")
    prepared = prepared.replace("\\u003a", ":")

    patterns = (
        r'https?://[^"\'<>\s]+?\.mp4(?:\?[^"\'<>\s]*)?',
        r'["\']([^"\']+?\.mp4(?:\?[^"\']*)?)["\']',
    )

    urls: list[str] = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            prepared,
            flags=re.IGNORECASE,
        ):
            normalized = normalize_url(
                match,
                article_url,
            )

            if normalized not in urls:
                urls.append(normalized)

    return urls


def video_score(url: str) -> int:
    """Rank likely NASA story videos."""

    lowered = urllib.parse.unquote(url).lower()

    if ".mp4" not in lowered:
        return -10_000

    score = 0

    if "assets.science.nasa.gov" in lowered:
        score += 200

    if "/eo/" in lowered:
        score += 150

    if "/iotd/" in lowered:
        score += 150

    if "download" in lowered:
        score += 20

    if "preview" in lowered:
        score -= 30

    if "thumbnail" in lowered:
        score -= 100

    return score


def select_primary_video(
    parser: ArticleParser,
    article_html: str,
    article_url: str,
) -> str:
    """Select the best available MP4 for a video story."""

    candidates: list[str] = []

    for url in parser.media_urls:
        normalized = normalize_url(
            url,
            article_url,
        )

        if ".mp4" in normalized.lower():
            candidates.append(normalized)

    candidates.extend(
        extract_raw_mp4_urls(
            article_html,
            article_url,
        )
    )

    unique_candidates: list[str] = []

    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    ranked = sorted(
        unique_candidates,
        key=video_score,
        reverse=True,
    )

    if not ranked:
        return ""

    if video_score(ranked[0]) < 0:
        return ""

    return ranked[0]


def image_score(url: str) -> int:
    """Rank likely lead images above posters and page graphics."""

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

    if any(
        term in lowered
        for term in (
            "fallback",
            "generic",
            "location",
            "locatormap",
            "map-placeholder",
            "poster",
            "thumbnail",
        )
    ):
        score -= 1_000

    if "logo" in lowered or "banner" in lowered:
        score -= 1_000

    return score


def select_primary_image(
    parser: ArticleParser,
    article_url: str,
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
            normalize_url(
                social_image,
                article_url,
            )
        )

    candidates.extend(
        normalize_url(
            url,
            article_url,
        )
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

    if not ranked:
        return ""

    if image_score(ranked[0]) < 0:
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

    video_url = select_primary_video(
        parser,
        article_html,
        article_url,
    )

    image_url = select_primary_image(
        parser,
        article_url,
    )

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

    paragraphs = extract_story_paragraphs(
        parser.blocks
    )

    abstract = build_extractive_abstract(
        paragraphs,
        title,
        short_description,
    )

    if video_url:
        media_type = "video"
        media_url = video_url
    else:
        media_type = "image"
        media_url = image_url

    return {
        "title": title,
        "short_description": short_description,
        "abstract": abstract,
        "media_type": media_type,
        "media_url": media_url,
        "video_url": video_url,
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

    media_type = str(metadata["media_type"])
    media_url = str(metadata["media_url"])
    image_url = str(metadata["image_url"])
    video_url = str(metadata["video_url"])

    if not media_url:
        print("  Skipped: no usable primary media found.")
        return None

    if (
        media_type == "image"
        and not is_iotd_image(image_url)
    ):
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

    abstract = str(metadata["abstract"]).strip()

    if not abstract:
        abstract = short_description

    if abstract.endswith("…"):
        raise RuntimeError(
            f"Abstract for '{title}' ends with an ellipsis."
        )

    if not is_complete_sentence(abstract):
        raise RuntimeError(
            f"Abstract for '{title}' does not end "
            "with a complete sentence."
        )

    return {
        "title": title,
        "publication_date": publication_date,
        "display_date": display_date,
        "short_description": short_description,
        "abstract": abstract,
        "media_type": media_type,
        "media_url": media_url,
        "video_url": video_url,
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
                f"   Media type: "
                f"{record['media_type']}"
            )
            print(
                f"   Image source: "
                f"{record['image_source']}"
            )
            print(
                f"   Abstract words: "
                f"{word_count(str(record['abstract']))}"
            )
            print(
                f"   Abstract: "
                f"{record['abstract']}"
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
