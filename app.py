from __future__ import annotations

import os
import time

import requests as http_requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, Response

from services import StoryboardService, StoryboardServiceError, ValidationError

load_dotenv()


app = Flask(__name__)


def _get_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


DEFAULT_PANEL_COUNT = _get_int_env(
    "STORYBOARD_PANEL_COUNT", default=4, minimum=1, maximum=6
)
ASSET_VERSION = str(int(time.time()))

storyboard_service = StoryboardService()


@app.get("/")
def index() -> str:
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/image-proxy")
def image_proxy():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith("https://image.pollinations.ai/"):
        return "Invalid URL", 400

    retries = _get_int_env("IMAGE_PROXY_RETRIES", default=5, minimum=1, maximum=10)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }

    app.logger.info(f"Image proxy request: {url[:120]}...")

    last_status = 502
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = http_requests.get(url, timeout=150, headers=headers)
        except http_requests.RequestException as exc:
            last_error = str(exc)
            if attempt < retries:
                delay = min(2**attempt, 8.0)
                app.logger.warning(
                    f"Image fetch attempt {attempt}/{retries} failed: {last_error}, retrying in {delay}s"
                )
                time.sleep(delay)
                continue
            app.logger.error(
                f"Image fetch error after {retries} attempts: {last_error}"
            )
            return f"Image fetch error after {retries} attempts: {last_error}", 502

        content_type = resp.headers.get("content-type", "")
        if resp.ok and content_type.startswith("image/"):
            mime_type = content_type.split(";", 1)[0].strip() or "image/jpeg"
            response = Response(resp.content, status=200, content_type=mime_type)
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["Access-Control-Allow-Origin"] = "*"
            app.logger.info(
                f"Image proxy success on attempt {attempt} ({len(resp.content)} bytes)"
            )
            return response

        last_status = resp.status_code
        app.logger.warning(
            f"Image fetch attempt {attempt}/{retries} returned status {last_status}"
        )
        if resp.status_code == 429 and attempt < retries:
            retry_after_raw = (resp.headers.get("retry-after") or "").strip()
            retry_after = (
                int(retry_after_raw)
                if retry_after_raw.isdigit()
                else min(3 * attempt, 12.0)
            )
            app.logger.info(f"Rate limited (429), retrying in {retry_after}s")
            time.sleep(float(retry_after))
            continue
        elif resp.status_code in (500, 502, 503, 504) and attempt < retries:
            time.sleep(min(2**attempt, 8.0))
            continue

        if attempt < retries:
            time.sleep(min(1.5 * attempt, 4.0))

    if last_status == 429:
        return "Image provider is busy, please retry.", 429
    return f"Image fetch failed with status {last_status} after {retries} attempts", 502


@app.post("/generate")
def generate_storyboard():
    import time

    start_time = time.time()
    app.logger.info("Received /generate request")

    payload = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    app.logger.info(f"Prompt: {prompt[:80]}...")

    try:
        result = storyboard_service.generate_storyboard(
            prompt=prompt,
            panel_count=DEFAULT_PANEL_COUNT,
        )
        elapsed = time.time() - start_time
        app.logger.info(f"✓ Generation completed in {elapsed:.1f}s")
        return jsonify(result), 200
    except ValidationError as exc:
        app.logger.warning(f"Validation error: {exc}")
        return jsonify({"error": str(exc)}), 400
    except StoryboardServiceError as exc:
        app.logger.error(f"Service error: {exc}")
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        app.logger.exception("Unhandled error in /generate")
        debug_mode = os.getenv("FLASK_DEBUG", "false").strip().lower() == "true"
        if debug_mode:
            return jsonify(
                {"error": f"Unexpected server error: {type(exc).__name__}: {exc}"}
            ), 500
        return jsonify(
            {"error": "Unexpected server error while generating storyboard."}
        ), 500


if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    debug_mode = os.getenv("FLASK_DEBUG", "false").strip().lower() == "true"
    app.run(debug=debug_mode)
