#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    import cairosvg
except ImportError as error:
    raise RuntimeError(
        "CairoSVG is required. Install it with: "
        "python -m pip install cairosvg"
    ) from error


# ------------------------------------------------------------
# Video settings
# ------------------------------------------------------------

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
FRAME_RATE = 30

NUMBER_OF_STORIES = 7
STORY_DURATION_SECONDS = 15
TOTAL_DURATION_SECONDS = (
    NUMBER_OF_STORIES
    * STORY_DURATION_SECONDS
)

LEFT_PANEL_WIDTH = 710
RIGHT_PANEL_WIDTH = (
    OUTPUT_WIDTH
    - LEFT_PANEL_WIDTH
)

ACCENT = (103, 183, 232)
PANEL_TOP = (16, 31, 45)
PANEL_BOTTOM = (7, 16, 25)
WHITE = (255, 255, 255)
BODY_TEXT = (224, 229, 234)
MUTED_TEXT = (170, 180, 190)
BLACK = (0, 0, 0)


# ------------------------------------------------------------
# Repository paths
# ------------------------------------------------------------

ROOT_DIRECTORY = (
    Path(__file__).resolve().parent.parent
)

DATA_PATH = (
    ROOT_DIRECTORY
    / "data"
    / "nasa-earth-observatory.json"
)

BUILD_DIRECTORY = (
    ROOT_DIRECTORY
    / "nasa-video-build"
)

ASSET_DIRECTORY = (
    BUILD_DIRECTORY
    / "assets"
)

SEGMENT_DIRECTORY = (
    BUILD_DIRECTORY
    / "segments"
)

SITE_DIRECTORY = (
    ROOT_DIRECTORY
    / "site"
)

OUTPUT_VIDEO = (
    SITE_DIRECTORY
    / "nasa-earth-observatory.mp4"
)

SOURCE_PLAYER_PAGE = (
    ROOT_DIRECTORY
    / "nasa-earth-observatory.html"
)

DEPLOYED_PLAYER_PAGE = (
    SITE_DIRECTORY
    / "nasa-earth-observatory.html"
)

NASA_LOGO_SVG = (
    ASSET_DIRECTORY
    / "nasa-logo.svg"
)

NASA_LOGO_PNG = (
    ASSET_DIRECTORY
    / "nasa-logo.png"
)

CONCAT_LIST = (
    BUILD_DIRECTORY
    / "segments.txt"
)

USER_AGENT = (
    "FAU-Geosciences-Digital-Signage/1.0"
)

NASA_LOGO_URLS = (
    (
        "https://www.nasa.gov/wp-content/"
        "themes/nasa/assets/images/nasa-logo.svg"
    ),
    (
        "https://upload.wikimedia.org/"
        "wikipedia/commons/e/e5/NASA_logo.svg"
    ),
)


# ------------------------------------------------------------
# Fonts
# ------------------------------------------------------------

REGULAR_FONT_PATHS = (
    Path(
        "/usr/share/fonts/truetype/"
        "dejavu/DejaVuSans.ttf"
    ),
    Path(
        "/usr/share/fonts/truetype/"
        "liberation2/LiberationSans-Regular.ttf"
    ),
)

BOLD_FONT_PATHS = (
    Path(
        "/usr/share/fonts/truetype/"
        "dejavu/DejaVuSans-Bold.ttf"
    ),
    Path(
        "/usr/share/fonts/truetype/"
        "liberation2/LiberationSans-Bold.ttf"
    ),
)


def log(message: str) -> None:
    print(message, flush=True)


def find_font(
    candidates: tuple[Path, ...],
) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No suitable system font was found."
    )


REGULAR_FONT_PATH = find_font(
    REGULAR_FONT_PATHS
)

BOLD_FONT_PATH = find_font(
    BOLD_FONT_PATHS
)


def font(
    size: int,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        str(
            BOLD_FONT_PATH
            if bold
            else REGULAR_FONT_PATH
        ),
        size=size,
    )


# ------------------------------------------------------------
# Download helpers
# ------------------------------------------------------------

def safe_download_url(
    url: str,
) -> str:
    """
    Percent-encode Unicode and other unsafe characters in URLs
    while preserving normal URL syntax and existing escapes.
    """

    parsed = urllib.parse.urlsplit(url)

    safe_path = urllib.parse.quote(
        parsed.path,
        safe="/%:@-._~!$&'()*+,;=",
    )

    safe_query = urllib.parse.quote(
        parsed.query,
        safe="=&;%:+,/?@-._~!$'()*",
    )

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            safe_path,
            safe_query,
            parsed.fragment,
        )
    )


def download_bytes(
    url: str,
    timeout: int = 60,
) -> bytes:
    safe_url = safe_download_url(url)

    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:
        return response.read()


def download_to_path(
    url: str,
    output_path: Path,
) -> None:
    data = download_bytes(url)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_bytes(data)


def prepare_logo() -> None:
    """Download and rasterize the official NASA insignia."""

    for logo_url in NASA_LOGO_URLS:
        try:
            log(
                f"Downloading NASA logo from "
                f"{logo_url}"
            )

            download_to_path(
                logo_url,
                NASA_LOGO_SVG,
            )

            cairosvg.svg2png(
                bytestring=NASA_LOGO_SVG.read_bytes(),
                write_to=str(NASA_LOGO_PNG),
                output_width=260,
                output_height=260,
            )

            with Image.open(
                NASA_LOGO_PNG
            ) as image:
                image.verify()

            return

        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            ValueError,
        ) as error:
            log(
                f"Logo source failed: {error}"
            )

    raise RuntimeError(
        "The NASA logo could not be downloaded."
    )


# ------------------------------------------------------------
# Image and text rendering
# ------------------------------------------------------------

def prepare_directories() -> None:
    if BUILD_DIRECTORY.exists():
        shutil.rmtree(
            BUILD_DIRECTORY
        )

    ASSET_DIRECTORY.mkdir(
        parents=True
    )

    SEGMENT_DIRECTORY.mkdir(
        parents=True
    )

    SITE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def vertical_gradient(
    width: int,
    height: int,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    image = Image.new(
        "RGB",
        (width, height),
    )

    pixels = image.load()

    for y in range(height):
        ratio = (
            y
            / max(
                height - 1,
                1,
            )
        )

        color = tuple(
            round(
                top[channel]
                + (
                    bottom[channel]
                    - top[channel]
                )
                * ratio
            )
            for channel in range(3)
        )

        for x in range(width):
            pixels[x, y] = color

    return image


def fit_cover(
    image: Image.Image,
    target_size: tuple[int, int],
) -> Image.Image:
    source_width, source_height = (
        image.size
    )

    target_width, target_height = (
        target_size
    )

    scale = max(
        target_width / source_width,
        target_height / source_height,
    )

    resized = image.resize(
        (
            math.ceil(
                source_width * scale
            ),
            math.ceil(
                source_height * scale
            ),
        ),
        Image.Resampling.LANCZOS,
    )

    left = (
        resized.width
        - target_width
    ) // 2

    top = (
        resized.height
        - target_height
    ) // 2

    return resized.crop(
        (
            left,
            top,
            left + target_width,
            top + target_height,
        )
    )


def wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    selected_font: ImageFont.FreeTypeFont,
    maximum_width: int,
) -> list[str]:
    words = (
        text.replace("\n", " \n ")
        .split()
    )

    lines: list[str] = []
    current = ""

    for word in words:
        if word == "\n":
            if current:
                lines.append(current)
                current = ""

            lines.append("")
            continue

        candidate = (
            word
            if not current
            else f"{current} {word}"
        )

        width = draw.textlength(
            candidate,
            font=selected_font,
        )

        if (
            width <= maximum_width
            or not current
        ):
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    selected_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    maximum_width: int,
    line_spacing: int,
    maximum_lines: int | None = None,
) -> int:
    x, y = position

    lines = wrap_lines(
        draw,
        text,
        selected_font,
        maximum_width,
    )

    if (
        maximum_lines is not None
        and len(lines) > maximum_lines
    ):
        lines = lines[:maximum_lines]

        final = lines[-1].rstrip(
            " .,;:"
        )

        while (
            final
            and draw.textlength(
                final + "…",
                font=selected_font,
            )
            > maximum_width
        ):
            final = " ".join(
                final.split()[:-1]
            )

        lines[-1] = final + "…"

    ascent, descent = (
        selected_font.getmetrics()
    )

    line_height = (
        ascent
        + descent
        + line_spacing
    )

    for line in lines:
        draw.text(
            (x, y),
            line,
            font=selected_font,
            fill=fill,
        )

        y += line_height

    return y


def draw_source_header(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    display_date: str,
) -> int:
    """
    Draw the NASA insignia to the left of the two-line header.

    The insignia's visible height is matched to the combined heading block.
    """

    heading_font = font(
        23,
        bold=True,
    )

    heading_lines = [
        "EARTH OBSERVATORY",
        f"IMAGE OF THE DAY: {display_date.upper()}",
    ]

    heading_line_height = 31
    heading_block_height = (
        heading_line_height
        * len(heading_lines)
    )

    logo_size = heading_block_height

    with Image.open(
        NASA_LOGO_PNG
    ) as logo_source:
        logo = logo_source.convert(
            "RGBA"
        )

        logo.thumbnail(
            (
                logo_size,
                logo_size,
            ),
            Image.Resampling.LANCZOS,
        )

        logo_x = 50
        logo_y = 39

        canvas.paste(
            logo,
            (
                logo_x,
                logo_y,
            ),
            logo,
        )

    heading_x = (
        50
        + logo_size
        + 20
    )

    heading_y = 40

    for line in heading_lines:
        draw.text(
            (
                heading_x,
                heading_y,
            ),
            line,
            font=heading_font,
            fill=ACCENT,
        )

        heading_y += (
            heading_line_height
        )

    rule_y = (
        39
        + heading_block_height
        + 15
    )

    draw.rectangle(
        (
            50,
            rule_y,
            LEFT_PANEL_WIDTH - 50,
            rule_y + 4,
        ),
        fill=ACCENT,
    )

    return rule_y + 4


def render_left_panel(
    story: dict[str, Any],
    story_index: int,
) -> Image.Image:
    panel = vertical_gradient(
        LEFT_PANEL_WIDTH,
        OUTPUT_HEIGHT,
        PANEL_TOP,
        PANEL_BOTTOM,
    )

    draw = ImageDraw.Draw(panel)

    draw.rectangle(
        (
            0,
            0,
            7,
            OUTPUT_HEIGHT,
        ),
        fill=ACCENT,
    )

    header_bottom = (
        draw_source_header(
            panel,
            draw,
            str(
                story.get(
                    "display_date",
                    "",
                )
            ),
        )
    )

    title_y = header_bottom + 40

    title_y = draw_wrapped_text(
        draw,
        (
            50,
            title_y,
        ),
        str(
            story.get(
                "title",
                "",
            )
        ),
        font(
            42,
            bold=True,
        ),
        WHITE,
        LEFT_PANEL_WIDTH - 100,
        line_spacing=4,
        maximum_lines=4,
    )

    abstract_y = (
        title_y
        + 28
    )

    abstract = str(
        story.get(
            "abstract",
            ""
        )
        or story.get(
            "short_description",
            "",
        )
    )

    draw_wrapped_text(
        draw,
        (
            50,
            abstract_y,
        ),
        abstract,
        font(20),
        BODY_TEXT,
        LEFT_PANEL_WIDTH - 100,
        line_spacing=8,
        maximum_lines=18,
    )

    metadata_y = 910

    draw.text(
        (
            50,
            metadata_y,
        ),
        "IMAGE SOURCE",
        font=font(
            14,
            bold=True,
        ),
        fill=ACCENT,
    )

    draw_wrapped_text(
        draw,
        (
            50,
            metadata_y + 25,
        ),
        str(
            story.get(
                "image_source",
                "NASA Earth observation",
            )
        ),
        font(18),
        WHITE,
        LEFT_PANEL_WIDTH - 100,
        line_spacing=4,
        maximum_lines=2,
    )

    footer_y = 1020

    draw.line(
        (
            50,
            footer_y - 17,
            LEFT_PANEL_WIDTH - 50,
            footer_y - 17,
        ),
        fill=(54, 68, 80),
        width=1,
    )

    draw.text(
        (
            50,
            footer_y,
        ),
        "NASA EARTH OBSERVATORY",
        font=font(15),
        fill=MUTED_TEXT,
    )

    counter_text = (
        f"{story_index + 1} OF "
        f"{NUMBER_OF_STORIES}"
    )

    counter_width = draw.textlength(
        counter_text,
        font=font(
            17,
            bold=True,
        ),
    )

    draw.text(
        (
            LEFT_PANEL_WIDTH
            - 50
            - counter_width,
            footer_y,
        ),
        counter_text,
        font=font(
            17,
            bold=True,
        ),
        fill=WHITE,
    )

    return panel


def download_story_media(
    story: dict[str, Any],
    index: int,
) -> Path:
    url = str(
        story.get(
            "media_url",
            "",
        )
    )

    if not url:
        raise RuntimeError(
            f"Story {index + 1} has no media URL."
        )

    path_suffix = Path(
        urllib.parse.urlparse(
            url
        ).path
    ).suffix.lower()

    if not path_suffix:
        path_suffix = (
            ".mp4"
            if story.get(
                "media_type"
            )
            == "video"
            else ".jpg"
        )

    output_path = (
        ASSET_DIRECTORY
        / f"story_{index:02d}{path_suffix}"
    )

    log(
        f"Downloading story {index + 1}: "
        f"{story.get('title')}"
    )

    download_to_path(
        url,
        output_path,
    )

    return output_path


def make_image_frame(
    story: dict[str, Any],
    story_index: int,
    media_path: Path,
) -> Path:
    canvas = Image.new(
        "RGB",
        (
            OUTPUT_WIDTH,
            OUTPUT_HEIGHT,
        ),
        BLACK,
    )

    left_panel = render_left_panel(
        story,
        story_index,
    )

    canvas.paste(
        left_panel,
        (0, 0),
    )

    with Image.open(
        media_path
    ) as source_image:
        media = fit_cover(
            source_image.convert(
                "RGB"
            ),
            (
                RIGHT_PANEL_WIDTH,
                OUTPUT_HEIGHT,
            ),
        )

    canvas.paste(
        media,
        (
            LEFT_PANEL_WIDTH,
            0,
        ),
    )

    frame_path = (
        ASSET_DIRECTORY
        / f"story_{story_index:02d}_frame.png"
    )

    canvas.save(
        frame_path,
        format="PNG",
        optimize=True,
    )

    return frame_path


def encode_still_segment(
    frame_path: Path,
    segment_path: Path,
) -> None:
    progress_filter = (
        "[1:v]"
        f"scale=w='max(2,iw*min(t/{STORY_DURATION_SECONDS},1))':"
        "h=8:eval=frame[progress];"
        "[0:v][progress]"
        f"overlay=0:{OUTPUT_HEIGHT - 8}:"
        "shortest=1[out]"
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-loop",
        "1",
        "-i",
        str(frame_path),
        "-f",
        "lavfi",
        "-i",
        (
            f"color=c=0x67B7E8:"
            f"s={OUTPUT_WIDTH}x8:"
            f"r={FRAME_RATE}:"
            f"d={STORY_DURATION_SECONDS}"
        ),
        "-filter_complex",
        progress_filter,
        "-map",
        "[out]",
        "-t",
        str(
            STORY_DURATION_SECONDS
        ),
        "-r",
        str(FRAME_RATE),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(segment_path),
    ]

    subprocess.run(
        command,
        check=True,
    )

def make_video_background(
    story: dict[str, Any],
    story_index: int,
) -> Path:
    background = Image.new(
        "RGB",
        (
            OUTPUT_WIDTH,
            OUTPUT_HEIGHT,
        ),
        BLACK,
    )

    left_panel = render_left_panel(
        story,
        story_index,
    )

    background.paste(
        left_panel,
        (0, 0),
    )

    background_path = (
        ASSET_DIRECTORY
        / f"story_{story_index:02d}_background.png"
    )

    background.save(
        background_path,
        format="PNG",
        optimize=True,
    )

    return background_path


def encode_video_segment(
    background_path: Path,
    media_path: Path,
    segment_path: Path,
) -> None:
    video_filter = (
        f"[1:v]"
        f"scale={RIGHT_PANEL_WIDTH}:{OUTPUT_HEIGHT}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={RIGHT_PANEL_WIDTH}:{OUTPUT_HEIGHT}:"
        "(ow-iw)/2:(oh-ih)/2:black,"
        "setsar=1[media];"
        f"[0:v][media]"
        f"overlay={LEFT_PANEL_WIDTH}:0:"
        "shortest=1[composite];"
        "[2:v]"
        f"scale=w='max(2,iw*min(t/{STORY_DURATION_SECONDS},1))':"
        "h=8:eval=frame[progress];"
        "[composite][progress]"
        f"overlay=0:{OUTPUT_HEIGHT - 8}:"
        "shortest=1[out]"
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-loop",
        "1",
        "-i",
        str(background_path),
        "-stream_loop",
        "-1",
        "-i",
        str(media_path),
        "-f",
        "lavfi",
        "-i",
        (
            f"color=c=0x67B7E8:"
            f"s={OUTPUT_WIDTH}x8:"
            f"r={FRAME_RATE}:"
            f"d={STORY_DURATION_SECONDS}"
        ),
        "-filter_complex",
        video_filter,
        "-map",
        "[out]",
        "-t",
        str(
            STORY_DURATION_SECONDS
        ),
        "-r",
        str(FRAME_RATE),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(segment_path),
    ]

    subprocess.run(
        command,
        check=True,
    )


def build_segment(
    story: dict[str, Any],
    index: int,
) -> Path:
    media_path = download_story_media(
        story,
        index,
    )

    segment_path = (
        SEGMENT_DIRECTORY
        / f"segment_{index:02d}.mp4"
    )

    if story.get(
        "media_type"
    ) == "video":
        background_path = (
            make_video_background(
                story,
                index,
            )
        )

        encode_video_segment(
            background_path,
            media_path,
            segment_path,
        )

    else:
        frame_path = make_image_frame(
            story,
            index,
            media_path,
        )

        encode_still_segment(
            frame_path,
            segment_path,
        )

    log(
        f"Created segment {index + 1}/"
        f"{NUMBER_OF_STORIES}: "
        f"{segment_path.name}"
    )

    return segment_path


def concatenate_segments(
    segment_paths: list[Path],
) -> None:
    lines = [
        f"file '{path.resolve()}'"
        for path in segment_paths
    ]

    CONCAT_LIST.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(CONCAT_LIST),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(OUTPUT_VIDEO),
    ]

    subprocess.run(
        command,
        check=True,
    )


def add_build_token() -> None:
    if not DEPLOYED_PLAYER_PAGE.exists():
        raise FileNotFoundError(
            "The NASA player page was not "
            "copied into the site directory."
        )

    build_token = str(
        int(
            OUTPUT_VIDEO.stat().st_mtime
        )
    )

    content = (
        DEPLOYED_PLAYER_PAGE.read_text(
            encoding="utf-8"
        )
    )

    placeholder = "__BUILD_TOKEN__"

    if placeholder not in content:
        raise RuntimeError(
            f"{placeholder} was not found in "
            f"{DEPLOYED_PLAYER_PAGE.name}."
        )

    DEPLOYED_PLAYER_PAGE.write_text(
        content.replace(
            placeholder,
            build_token,
        ),
        encoding="utf-8",
    )

    log(
        f"Applied NASA video build token: "
        f"{build_token}"
    )


def validate_duration() -> None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:"
        "nokey=1",
        str(OUTPUT_VIDEO),
    ]

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )

    duration = float(
        result.stdout.strip()
    )

    if abs(
        duration
        - TOTAL_DURATION_SECONDS
    ) > 0.25:
        raise RuntimeError(
            f"Expected a "
            f"{TOTAL_DURATION_SECONDS}-second "
            f"NASA video, but FFprobe reported "
            f"{duration:.3f} seconds."
        )

    log(
        f"Validated duration: "
        f"{duration:.3f} seconds"
    )


def main() -> int:
    prepare_directories()
    prepare_logo()

    payload = json.loads(
        DATA_PATH.read_text(
            encoding="utf-8"
        )
    )

    stories = payload.get(
        "items",
        [],
    )

    if len(stories) != NUMBER_OF_STORIES:
        raise RuntimeError(
            f"Expected {NUMBER_OF_STORIES} stories; "
            f"found {len(stories)}."
        )

    segment_paths = [
        build_segment(
            story,
            index,
        )
        for index, story
        in enumerate(stories)
    ]

    concatenate_segments(
        segment_paths
    )

    validate_duration()
    add_build_token()

    size_mb = (
        OUTPUT_VIDEO.stat().st_size
        / 1_048_576
    )

    log(
        f"Created {OUTPUT_VIDEO.name}: "
        f"{size_mb:.1f} MB"
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())

    except subprocess.CalledProcessError as error:
        log(
            "FFmpeg failed with exit code "
            f"{error.returncode}."
        )

        sys.exit(
            error.returncode
        )

    except Exception as error:
        log(
            f"NASA video build failed: "
            f"{error}"
        )

        sys.exit(1)
