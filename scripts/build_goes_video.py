#!/usr/bin/env python3

from __future__ import annotations

import concurrent.futures
import datetime as dt
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ------------------------------------------------------------
# NOAA source settings
# ------------------------------------------------------------

SATELLITE = "GOES19"
PRODUCT_PATH = "GOES19/ABI/FD/GEOCOLOR"
SOURCE_SIZE = 1808

# One frame every 10 minutes for approximately 24 hours.
IMAGE_INTERVAL_MINUTES = 10
NUMBER_OF_FRAMES = 144

# Search farther back to accommodate occasional missing frames.
NUMBER_OF_CANDIDATES = 175

# The most recent nominal NOAA frame may still be processing.
LATEST_FRAME_DELAY_MINUTES = 30


# ------------------------------------------------------------
# Download settings
# ------------------------------------------------------------

DOWNLOAD_WORKERS = 8
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_RETRIES = 3


# ------------------------------------------------------------
# Video settings
# ------------------------------------------------------------

# 150 milliseconds per source frame:
# 1000 / 150 = 6.6667 frames per second.
VIDEO_FRAME_RATE = "20/3"

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080


# ------------------------------------------------------------
# Repository paths
# ------------------------------------------------------------

ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
BUILD_DIRECTORY = ROOT_DIRECTORY / "goes-build"
FRAME_DIRECTORY = BUILD_DIRECTORY / "frames"
SITE_DIRECTORY = ROOT_DIRECTORY / "site"

SOURCE_PLAYER_PAGE = ROOT_DIRECTORY / "goes-east-video.html"
DEPLOYED_PLAYER_PAGE = SITE_DIRECTORY / "goes-east-video.html"
OUTPUT_VIDEO = SITE_DIRECTORY / "goes-east-24h.mp4"

BASE_URL = (
    "https://cdn.star.nesdis.noaa.gov/"
    f"{PRODUCT_PATH}"
)


def log(message: str) -> None:
    print(message, flush=True)


def round_down_to_interval(
    value: dt.datetime,
) -> dt.datetime:
    minute = (
        value.minute // IMAGE_INTERVAL_MINUTES
    ) * IMAGE_INTERVAL_MINUTES

    return value.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def build_image_url(timestamp: dt.datetime) -> str:
    source_timestamp = timestamp.strftime("%Y%j%H%M")

    filename = (
        f"{source_timestamp}_"
        f"{SATELLITE}-ABI-FD-GEOCOLOR-"
        f"{SOURCE_SIZE}x{SOURCE_SIZE}.jpg"
    )

    return f"{BASE_URL}/{filename}"


def looks_like_jpeg(data: bytes) -> bool:
    return (
        len(data) > 10_000
        and data.startswith(b"\xff\xd8")
        and data.endswith(b"\xff\xd9")
    )


def download_candidate(
    candidate: tuple[int, dt.datetime],
) -> tuple[dt.datetime, bytes] | None:
    index, timestamp = candidate
    url = build_image_url(timestamp)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "FAU-Geosciences-Digital-Signage/1.0"
        },
    )

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                data = response.read()

                if not looks_like_jpeg(data):
                    return None

                log(
                    f"Found candidate {index + 1:03d}: "
                    f"{timestamp:%Y-%m-%d %H:%M UTC}"
                )

                return timestamp, data

        except urllib.error.HTTPError as error:
            # A missing timestamp is normal and need not be retried.
            if error.code == 404:
                return None

            if attempt == DOWNLOAD_RETRIES:
                log(
                    f"Skipped {timestamp:%Y-%m-%d %H:%M UTC}: "
                    f"HTTP {error.code}"
                )
                return None

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as error:
            if attempt == DOWNLOAD_RETRIES:
                log(
                    f"Skipped {timestamp:%Y-%m-%d %H:%M UTC}: "
                    f"{error}"
                )
                return None

        time.sleep(attempt * 2)

    return None


def prepare_directories() -> None:
    """
    Recreate temporary build folders and assemble a clean
    GitHub Pages deployment directory.
    """

    if BUILD_DIRECTORY.exists():
        shutil.rmtree(BUILD_DIRECTORY)

    FRAME_DIRECTORY.mkdir(parents=True)

    if SITE_DIRECTORY.exists():
        shutil.rmtree(SITE_DIRECTORY)

    SITE_DIRECTORY.mkdir(parents=True)

    excluded_items = {
        ".git",
        ".github",
        "scripts",
        "goes-build",
        "site",
    }

    for item in ROOT_DIRECTORY.iterdir():
        if item.name in excluded_items:
            continue

        destination = SITE_DIRECTORY / item.name

        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


def select_frames() -> list[tuple[dt.datetime, bytes]]:
    now = dt.datetime.now(dt.timezone.utc)

    latest_candidate = (
        round_down_to_interval(now)
        - dt.timedelta(
            minutes=LATEST_FRAME_DELAY_MINUTES
        )
    )

    candidates = [
        (
            index,
            latest_candidate
            - dt.timedelta(
                minutes=index * IMAGE_INTERVAL_MINUTES
            ),
        )
        for index in range(NUMBER_OF_CANDIDATES)
    ]

    log(
        f"Searching {NUMBER_OF_CANDIDATES} timestamps "
        f"for {NUMBER_OF_FRAMES} valid NOAA images…"
    )

    downloaded: list[tuple[dt.datetime, bytes]] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=DOWNLOAD_WORKERS
    ) as executor:
        for result in executor.map(
            download_candidate,
            candidates,
        ):
            if result is not None:
                downloaded.append(result)

    if len(downloaded) < NUMBER_OF_FRAMES:
        raise RuntimeError(
            f"Only {len(downloaded)} valid images were found; "
            f"{NUMBER_OF_FRAMES} are required."
        )

    # Select the newest 144 available images.
    downloaded.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    selected = downloaded[:NUMBER_OF_FRAMES]

    # Play from oldest to newest.
    selected.sort(key=lambda item: item[0])

    return selected


def save_frames(
    selected: list[tuple[dt.datetime, bytes]],
) -> None:
    for index, (timestamp, data) in enumerate(selected):
        output_path = (
            FRAME_DIRECTORY /
            f"frame_{index:03d}.jpg"
        )

        output_path.write_bytes(data)

        log(
            f"Saved frame {index + 1:03d}/"
            f"{NUMBER_OF_FRAMES}: "
            f"{timestamp:%Y-%m-%d %H:%M UTC}"
        )


def encode_video() -> None:
    """
    Scale the square NOAA imagery to 1080 pixels high,
    center it on a 1920 × 1080 black canvas, and encode
    it as an H.264 MP4.
    """

    input_pattern = str(
        FRAME_DIRECTORY / "frame_%03d.jpg"
    )

    video_filter = (
        f"scale={OUTPUT_HEIGHT}:{OUTPUT_HEIGHT}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
        "(ow-iw)/2:"
        "(oh-ih)/2:"
        "black"
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

        "-vf",
        video_filter,

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "22",

        "-pix_fmt",
        "yuv420p",

        "-movflags",
        "+faststart",

        str(OUTPUT_VIDEO),
    ]

    log("Encoding GOES-East MP4…")

    subprocess.run(
        command,
        check=True,
    )


def add_build_token() -> None:
    """
    Replace the placeholder only in the deployed copy.
    The repository source file retains its readable placeholder.
    """

    if not DEPLOYED_PLAYER_PAGE.exists():
        raise FileNotFoundError(
            "The deployed player page was not copied."
        )

    build_token = dt.datetime.now(
        dt.timezone.utc
    ).strftime("%Y%m%d%H%M%S")

    content = DEPLOYED_PLAYER_PAGE.read_text(
        encoding="utf-8"
    )

    placeholder = "__BUILD_TOKEN__"

    if placeholder not in content:
        raise RuntimeError(
            f"{placeholder} was not found in "
            f"{DEPLOYED_PLAYER_PAGE.name}."
        )

    content = content.replace(
        placeholder,
        build_token,
    )

    DEPLOYED_PLAYER_PAGE.write_text(
        content,
        encoding="utf-8",
    )

    log(f"Applied build token: {build_token}")


def main() -> int:
    prepare_directories()

    selected = select_frames()

    log(
        "Selected period: "
        f"{selected[0][0]:%Y-%m-%d %H:%M UTC} through "
        f"{selected[-1][0]:%Y-%m-%d %H:%M UTC}"
    )

    save_frames(selected)
    encode_video()
    add_build_token()

    size_mb = (
        OUTPUT_VIDEO.stat().st_size / 1_048_576
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
        sys.exit(error.returncode)

    except Exception as error:
        log(f"Build failed: {error}")
        sys.exit(1)
