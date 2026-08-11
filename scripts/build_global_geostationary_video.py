#!/usr/bin/env python3

from __future__ import annotations

import concurrent.futures
import datetime as dt
import io
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont



# ------------------------------------------------------------
# Shared animation settings
# ------------------------------------------------------------

IMAGE_INTERVAL_MINUTES = 10
NUMBER_OF_FRAMES = 288

# Search a little beyond 48 hours so occasional missing
# timestamps do not make the build fail.
NUMBER_OF_CANDIDATES = 330

# Both services can lag the wall clock. Start safely behind
# real time, then look backward for exact common timestamps.
LATEST_FRAME_DELAY_MINUTES = 30

DOWNLOAD_WORKERS = 8
DOWNLOAD_TIMEOUT_SECONDS = 45
DOWNLOAD_RETRIES = 3

# Same pacing as the existing GOES animation:
# 150 ms per source frame = 6.6667 fps.
VIDEO_FRAME_RATE = "20/3"

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080

PANEL_SIZE = 941
CENTER_GAP = 4
PANEL_TOP = (OUTPUT_HEIGHT - PANEL_SIZE) // 2
LEFT_PANEL_X = (
    OUTPUT_WIDTH
    - (PANEL_SIZE * 2 + CENTER_GAP)
) // 2
RIGHT_PANEL_X = (
    LEFT_PANEL_X
    + PANEL_SIZE
    + CENTER_GAP
)

# ------------------------------------------------------------
# GOES-19 source
# ------------------------------------------------------------

GOES_SATELLITE = "GOES19"
GOES_PRODUCT_PATH = "GOES19/ABI/FD/GEOCOLOR"
GOES_SOURCE_SIZE = 1808

GOES_BASE_URL = (
    "https://cdn.star.nesdis.noaa.gov/"
    f"{GOES_PRODUCT_PATH}"
)

# ------------------------------------------------------------
# Meteosat-12 / EUMETSAT source
# ------------------------------------------------------------

METEOSAT_WMS_ENDPOINT = (
    "https://view.eumetsat.int/geoserver/wms"
)

METEOSAT_LAYER = "mtg_fd:rgb_geocolour"

METEOSAT_WMS_PARAMETERS = {
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetMap",
    "layers": METEOSAT_LAYER,
    "bbox": "-6500000,-6500000,6500000,6500000",
    "width": "1800",
    "height": "1800",
    "srs": "AUTO:97004,9001,0,0",
    "styles": "",
    "format": "image/jpeg",
    "bgcolor": "0x000000",
}

# Final visual settings derived from the test page.
METEOSAT_SCALE = 1.20
METEOSAT_BRIGHTNESS = 1.24
METEOSAT_CONTRAST = 1.03
METEOSAT_SATURATION = 1.07

METEOSAT_FOOTER_HEIGHT = 11
METEOSAT_FONT_SIZE = 10

# Matched to the approved test-page proportions.
EUMETSAT_LOGO_SIZE = 94
EUMETSAT_LOGO_LEFT = 8
EUMETSAT_LOGO_BOTTOM = 0




# ------------------------------------------------------------
# Frame text overlay settings
# ------------------------------------------------------------

HEADER_TOP = 18
HEADER_LEFT = 20
HEADER_RIGHT = 14
HEADER_LEFT_TEXT_OFFSET = 12
ACCENT_BAR_WIDTH = 4
ACCENT_BAR_HEIGHT = 42

TITLE_LINE_1 = "Earth in Motion"
TITLE_LINE_2 = "The past 48 hours"

DESCRIPTION_LINES = (
    "Watch weather evolve across Earth",
    "through the changing cycle",
    "of day and night",
)

TITLE_LINE_1_SIZE = 22
TITLE_LINE_2_SIZE = 17
DESCRIPTION_SIZE = 15
TITLE_FILL = (255, 255, 255)
SUBTITLE_FILL = (232, 232, 232)
DESCRIPTION_FILL = (230, 230, 230)
ACCENT_BAR_FILL = (77, 179, 255)

# ------------------------------------------------------------
# Repository paths
# ------------------------------------------------------------

ROOT_DIRECTORY = (
    Path(__file__).resolve().parent.parent
)

BUILD_DIRECTORY = (
    ROOT_DIRECTORY / "global-geostationary-build"
)

FRAME_DIRECTORY = (
    BUILD_DIRECTORY / "frames"
)

ASSET_DIRECTORY = (
    BUILD_DIRECTORY / "assets"
)

SITE_DIRECTORY = (
    ROOT_DIRECTORY / "site"
)

SOURCE_PLAYER_PAGE = (
    ROOT_DIRECTORY
    / "global-geostationary-video.html"
)

DEPLOYED_PLAYER_PAGE = (
    SITE_DIRECTORY
    / "global-geostationary-video.html"
)

OUTPUT_VIDEO = (
    SITE_DIRECTORY
    / "global-geostationary-48h.mp4"
)

EUMETSAT_LOGO_PNG = (
    ROOT_DIRECTORY
    / "assets"
    / "eumetsat-logo.png"
)

USER_AGENT = (
    "FAU-Geosciences-Digital-Signage/1.0"
)


def log(message: str) -> None:
    print(message, flush=True)


def round_down_to_interval(
    value: dt.datetime,
) -> dt.datetime:
    minute = (
        value.minute
        // IMAGE_INTERVAL_MINUTES
    ) * IMAGE_INTERVAL_MINUTES

    return value.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def build_goes_url(
    timestamp: dt.datetime,
) -> str:
    source_timestamp = timestamp.strftime(
        "%Y%j%H%M"
    )

    filename = (
        f"{source_timestamp}_"
        f"{GOES_SATELLITE}-ABI-FD-GEOCOLOR-"
        f"{GOES_SOURCE_SIZE}x"
        f"{GOES_SOURCE_SIZE}.jpg"
    )

    return (
        f"{GOES_BASE_URL}/{filename}"
    )


def build_meteosat_url(
    timestamp: dt.datetime,
) -> str:
    parameters = dict(
        METEOSAT_WMS_PARAMETERS
    )

    parameters["time"] = (
        timestamp.strftime(
            "%Y-%m-%dT%H:%M:00.000Z"
        )
    )

    return (
        METEOSAT_WMS_ENDPOINT
        + "?"
        + urllib.parse.urlencode(
            parameters
        )
    )


def looks_like_jpeg(
    data: bytes,
) -> bool:
    return (
        len(data) > 10_000
        and data.startswith(b"\xff\xd8")
        and data.endswith(b"\xff\xd9")
    )


def download_bytes(
    url: str,
) -> bytes | None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/jpeg,*/*;q=0.8",
        },
    )

    for attempt in range(
        1,
        DOWNLOAD_RETRIES + 1,
    ):
        try:
            with urllib.request.urlopen(
                request,
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                data = response.read()

                if looks_like_jpeg(data):
                    return data

                return None

        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None

            if attempt == DOWNLOAD_RETRIES:
                log(
                    f"HTTP {error.code}: {url}"
                )
                return None

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as error:
            if attempt == DOWNLOAD_RETRIES:
                log(
                    f"Download failed: "
                    f"{error}: {url}"
                )
                return None

        time.sleep(attempt * 2)

    return None


def download_pair(
    candidate: tuple[
        int,
        dt.datetime,
    ],
) -> tuple[
    dt.datetime,
    bytes,
    bytes,
] | None:
    index, timestamp = candidate

    goes_url = build_goes_url(
        timestamp
    )

    meteosat_url = (
        build_meteosat_url(
            timestamp
        )
    )

    goes_data = download_bytes(
        goes_url
    )

    if goes_data is None:
        return None

    meteosat_data = download_bytes(
        meteosat_url
    )

    if meteosat_data is None:
        return None

    log(
        f"Paired candidate "
        f"{index + 1:03d}: "
        f"{timestamp:%Y-%m-%d %H:%M UTC}"
    )

    return (
        timestamp,
        goes_data,
        meteosat_data,
    )


def prepare_directories() -> None:
    if BUILD_DIRECTORY.exists():
        shutil.rmtree(
            BUILD_DIRECTORY
        )

    FRAME_DIRECTORY.mkdir(
        parents=True
    )

    ASSET_DIRECTORY.mkdir(
        parents=True
    )

    SITE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def prepare_eumetsat_logo() -> None:
    if not EUMETSAT_LOGO_PNG.exists():
        raise FileNotFoundError(
            "Local EUMETSAT logo asset was not found: "
            f"{EUMETSAT_LOGO_PNG}"
        )

    log(
        "Using local EUMETSAT logo asset."
    )


def select_paired_frames() -> list[
    tuple[
        dt.datetime,
        bytes,
        bytes,
    ]
]:
    now = dt.datetime.now(
        dt.timezone.utc
    )

    latest_candidate = (
        round_down_to_interval(now)
        - dt.timedelta(
            minutes=(
                LATEST_FRAME_DELAY_MINUTES
            )
        )
    )

    candidates = [
        (
            index,
            latest_candidate
            - dt.timedelta(
                minutes=(
                    index
                    * IMAGE_INTERVAL_MINUTES
                )
            ),
        )
        for index
        in range(
            NUMBER_OF_CANDIDATES
        )
    ]

    log(
        f"Searching "
        f"{NUMBER_OF_CANDIDATES} "
        f"timestamps for "
        f"{NUMBER_OF_FRAMES} "
        "exact GOES/Meteosat pairs…"
    )

    paired: list[
        tuple[
            dt.datetime,
            bytes,
            bytes,
        ]
    ] = []

    with (
        concurrent.futures
        .ThreadPoolExecutor(
            max_workers=DOWNLOAD_WORKERS
        )
    ) as executor:
        for result in executor.map(
            download_pair,
            candidates,
        ):
            if result is not None:
                paired.append(result)

    if len(paired) < NUMBER_OF_FRAMES:
        raise RuntimeError(
            f"Only {len(paired)} exact "
            "GOES/Meteosat timestamp pairs "
            f"were found; "
            f"{NUMBER_OF_FRAMES} are required."
        )

    paired.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    selected = paired[
        :NUMBER_OF_FRAMES
    ]

    selected.sort(
        key=lambda item: item[0]
    )

    return selected


def load_font(
    size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:
    if bold:
        candidates = (
            Path(
                "/usr/share/fonts/truetype/"
                "liberation2/"
                "LiberationSans-Bold.ttf"
            ),
            Path(
                "/usr/share/fonts/truetype/"
                "dejavu/"
                "DejaVuSans-Bold.ttf"
            ),
        )
    else:
        candidates = (
            Path(
                "/usr/share/fonts/truetype/"
                "liberation2/"
                "LiberationSans-Regular.ttf"
            ),
            Path(
                "/usr/share/fonts/truetype/"
                "dejavu/"
                "DejaVuSans.ttf"
            ),
        )

    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(
                str(candidate),
                size=size,
            )

    raise FileNotFoundError(
        "No suitable font found."
    )


def resize_cover(
    image: Image.Image,
    width: int,
    height: int,
) -> Image.Image:
    source_width, source_height = (
        image.size
    )

    scale = max(
        width / source_width,
        height / source_height,
    )

    resized = image.resize(
        (
            round(
                source_width * scale
            ),
            round(
                source_height * scale
            ),
        ),
        Image.Resampling.LANCZOS,
    )

    left = (
        resized.width - width
    ) // 2

    top = (
        resized.height - height
    ) // 2

    return resized.crop(
        (
            left,
            top,
            left + width,
            top + height,
        )
    )


def render_goes_panel(
    data: bytes,
) -> Image.Image:
    with Image.open(
        io.BytesIO(data)
    ) as source:
        source = source.convert(
            "RGB"
        )

        return source.resize(
            (
                PANEL_SIZE,
                PANEL_SIZE,
            ),
            Image.Resampling.LANCZOS,
        )


def render_meteosat_panel(
    data: bytes,
    timestamp: dt.datetime,
    logo: Image.Image,
) -> Image.Image:
    with Image.open(
        io.BytesIO(data)
    ) as source:
        source = source.convert(
            "RGB"
        )

        enlarged_size = round(
            PANEL_SIZE
            * METEOSAT_SCALE
        )

        source = resize_cover(
            source,
            enlarged_size,
            enlarged_size,
        )

        left = (
            source.width
            - PANEL_SIZE
        ) // 2

        top = (
            source.height
            - PANEL_SIZE
        ) // 2

        panel = source.crop(
            (
                left,
                top,
                left + PANEL_SIZE,
                top + PANEL_SIZE,
            )
        )

    panel = (
        ImageEnhance.Brightness(
            panel
        ).enhance(
            METEOSAT_BRIGHTNESS
        )
    )

    panel = (
        ImageEnhance.Contrast(
            panel
        ).enhance(
            METEOSAT_CONTRAST
        )
    )

    panel = (
        ImageEnhance.Color(
            panel
        ).enhance(
            METEOSAT_SATURATION
        )
    )

    draw = ImageDraw.Draw(panel)

    footer_top = (
        PANEL_SIZE
        - METEOSAT_FOOTER_HEIGHT
    )

    draw.rectangle(
        (
            0,
            footer_top,
            PANEL_SIZE,
            PANEL_SIZE,
        ),
        fill=(255, 255, 255),
    )

    text = (
        timestamp.strftime(
            "%d %b %Y %H:%MZ"
        )
        + " - EUMETSAT - "
        "METEOSAT-12 - "
        "GEOCOLOUR Composite"
    )

    footer_font = load_font(
        METEOSAT_FONT_SIZE
    )

    bbox = draw.textbbox(
        (0, 0),
        text,
        font=footer_font,
    )

    text_width = (
        bbox[2] - bbox[0]
    )

    text_height = (
        bbox[3] - bbox[1]
    )

    text_x = (
        PANEL_SIZE
        - text_width
    ) // 2

    # Optical vertical centering for the very shallow footer.
    text_y = (
        footer_top
        + (
            METEOSAT_FOOTER_HEIGHT
            - text_height
        ) // 2
        - bbox[1]
    )

    draw.text(
        (
            text_x,
            text_y,
        ),
        text,
        font=footer_font,
        fill=(17, 17, 17),
    )

    logo_copy = logo.copy()

    logo_copy.thumbnail(
        (
            EUMETSAT_LOGO_SIZE,
            EUMETSAT_LOGO_SIZE,
        ),
        Image.Resampling.LANCZOS,
    )

    logo_x = EUMETSAT_LOGO_LEFT

    logo_y = (
        PANEL_SIZE
        - EUMETSAT_LOGO_BOTTOM
        - logo_copy.height
    )

    panel.paste(
        logo_copy,
        (
            logo_x,
            logo_y,
        ),
        logo_copy,
    )

    return panel




def render_header_overlay(
    canvas: Image.Image,
) -> None:
    draw = ImageDraw.Draw(canvas)

    title_font = load_font(
        TITLE_LINE_1_SIZE,
        bold=True,
    )

    subtitle_font = load_font(
        TITLE_LINE_2_SIZE,
        bold=True,
    )

    description_font = load_font(
        DESCRIPTION_SIZE
    )

    # Left accent bar
    draw.rectangle(
        (
            HEADER_LEFT,
            HEADER_TOP + 1,
            HEADER_LEFT + ACCENT_BAR_WIDTH - 1,
            HEADER_TOP + 1 + ACCENT_BAR_HEIGHT - 1,
        ),
        fill=ACCENT_BAR_FILL,
    )

    title_x = HEADER_LEFT + HEADER_LEFT_TEXT_OFFSET
    title_y = HEADER_TOP

    draw.text(
        (title_x, title_y),
        TITLE_LINE_1,
        font=title_font,
        fill=TITLE_FILL,
    )

    title_bbox = draw.textbbox(
        (title_x, title_y),
        TITLE_LINE_1,
        font=title_font,
    )

    subtitle_y = title_bbox[3] + 4

    draw.text(
        (title_x, subtitle_y),
        TITLE_LINE_2,
        font=subtitle_font,
        fill=SUBTITLE_FILL,
    )

    right_edge = OUTPUT_WIDTH - HEADER_RIGHT
    description_y = HEADER_TOP

    for line in DESCRIPTION_LINES:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=description_font,
        )

        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]

        draw.text(
            (
                right_edge - line_width,
                description_y - bbox[1],
            ),
            line,
            font=description_font,
            fill=DESCRIPTION_FILL,
        )

        description_y += line_height + 3


def render_frame(
    index: int,
    timestamp: dt.datetime,
    goes_data: bytes,
    meteosat_data: bytes,
    logo: Image.Image,
) -> None:
    canvas = Image.new(
        "RGB",
        (
            OUTPUT_WIDTH,
            OUTPUT_HEIGHT,
        ),
        (0, 0, 0),
    )

    goes_panel = render_goes_panel(
        goes_data
    )

    meteosat_panel = (
        render_meteosat_panel(
            meteosat_data,
            timestamp,
            logo,
        )
    )

    canvas.paste(
        goes_panel,
        (
            LEFT_PANEL_X,
            PANEL_TOP,
        ),
    )

    canvas.paste(
        meteosat_panel,
        (
            RIGHT_PANEL_X,
            PANEL_TOP,
        ),
    )

    render_header_overlay(canvas)

    output_path = (
        FRAME_DIRECTORY
        / f"frame_{index:03d}.jpg"
    )

    canvas.save(
        output_path,
        format="JPEG",
        quality=94,
        optimize=True,
        subsampling=0,
    )

    log(
        f"Rendered frame "
        f"{index + 1:03d}/"
        f"{NUMBER_OF_FRAMES}: "
        f"{timestamp:%Y-%m-%d %H:%M UTC}"
    )


def save_frames(
    selected: list[
        tuple[
            dt.datetime,
            bytes,
            bytes,
        ]
    ],
) -> None:
    with Image.open(
        EUMETSAT_LOGO_PNG
    ) as logo_source:
        logo = logo_source.convert(
            "RGBA"
        )

    for index, (
        timestamp,
        goes_data,
        meteosat_data,
    ) in enumerate(selected):
        render_frame(
            index,
            timestamp,
            goes_data,
            meteosat_data,
            logo,
        )


def encode_video() -> None:
    input_pattern = str(
        FRAME_DIRECTORY
        / "frame_%03d.jpg"
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",

        "-framerate",
        VIDEO_FRAME_RATE,

        "-start_number",
        "0",

        "-i",
        input_pattern,

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "21",

        "-pix_fmt",
        "yuv420p",

        "-movflags",
        "+faststart",

        str(OUTPUT_VIDEO),
    ]

    log(
        "Encoding paired global "
        "geostationary MP4…"
    )

    subprocess.run(
        command,
        check=True,
    )


def add_build_token() -> None:
    if not SOURCE_PLAYER_PAGE.exists():
        raise FileNotFoundError(
            "global-geostationary-video.html "
            "was not found."
        )

    shutil.copy2(
        SOURCE_PLAYER_PAGE,
        DEPLOYED_PLAYER_PAGE,
    )

    build_token = (
        dt.datetime.now(
            dt.timezone.utc
        ).strftime(
            "%Y%m%d%H%M%S"
        )
    )

    content = (
        DEPLOYED_PLAYER_PAGE
        .read_text(
            encoding="utf-8"
        )
    )

    placeholder = (
        "__BUILD_TOKEN__"
    )

    if placeholder not in content:
        raise RuntimeError(
            f"{placeholder} was not found "
            "in the player page."
        )

    content = content.replace(
        placeholder,
        build_token,
    )

    DEPLOYED_PLAYER_PAGE.write_text(
        content,
        encoding="utf-8",
    )

    log(
        f"Applied build token: "
        f"{build_token}"
    )


def main() -> int:
    prepare_directories()
    prepare_eumetsat_logo()

    selected = select_paired_frames()

    log(
        "Selected paired period: "
        f"{selected[0][0]:%Y-%m-%d %H:%M UTC} "
        "through "
        f"{selected[-1][0]:%Y-%m-%d %H:%M UTC}"
    )

    expected_span = dt.timedelta(
        minutes=(
            (
                NUMBER_OF_FRAMES - 1
            )
            * IMAGE_INTERVAL_MINUTES
        )
    )

    actual_span = (
        selected[-1][0]
        - selected[0][0]
    )

    log(
        "Nominal 288-frame span: "
        f"{expected_span}; "
        "actual paired span: "
        f"{actual_span}"
    )

    save_frames(selected)
    encode_video()
    add_build_token()

    size_mb = (
        OUTPUT_VIDEO.stat().st_size
        / 1_048_576
    )

    log(
        f"Created "
        f"{OUTPUT_VIDEO.name}: "
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
            f"Build failed: {error}"
        )
        sys.exit(1)
