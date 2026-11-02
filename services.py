from __future__ import annotations

import base64
import json
import os
import re
import textwrap
import time
import urllib.parse
from typing import Any

import requests


POLLINATIONS_MODEL_VARIANTS: tuple[str, ...] = ("flux", "sdxl", "turbo")
POLLINATIONS_MAX_ATTEMPTS_PER_MODEL = 5
POLLINATIONS_INTER_PANEL_DELAY_SECONDS = 3.0
POLLINATIONS_MAX_RETRY_SLEEP_SECONDS = 16.0
POLLINATIONS_MIN_VALID_IMAGE_BYTES = 2048
GEMINI_DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_MAX_ATTEMPTS = 1
GEMINI_MAX_RETRY_SLEEP_SECONDS = 16.0
GEMINI_TIMEOUT_SECONDS = 60


class ValidationError(ValueError):
    """Raised when user input fails validation."""


class StoryboardServiceError(RuntimeError):
    """Raised when external AI services fail."""


class StoryboardService:
    def generate_storyboard(self, prompt: str, panel_count: int) -> dict[str, Any]:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise ValidationError("Prompt cannot be empty.")

        if panel_count < 1 or panel_count > 6:
            raise ValidationError("Panel count must be between 1 and 6.")

        title, panels = self._generate_panels_with_ai(cleaned_prompt, panel_count)

        return {
            "title": title,
            "panel_count": len(panels),
            "panels": panels,
        }

    def _generate_panels_with_ai(
        self, prompt: str, panel_count: int
    ) -> tuple[str, list[dict[str, Any]]]:
        instruction = textwrap.dedent(
            f"""
            You are a professional storyboard artist and visual director for film pre-production teams.

            Convert the user's idea into exactly {panel_count} storyboard panels that tell a cohesive visual story. Output strict JSON only.
            The output must match this schema exactly:
            {{
              "title": "short title",
              "panels": [
                {{
                  "panel_number": 1,
                  "scene_description": "Visual action and setting for this panel",
                  "dialogue": "Short line of dialogue or narration",
                  "image_prompt": "Detailed cinematic prompt for an image model"
                }}
              ]
            }}

            Rules for narrative:
            - Return exactly {panel_count} panels that flow as a sequence.
            - Keep each scene_description between 20 and 60 words.
            - Keep dialogue concise and natural.
            - Maintain character continuity across panels (same clothing, appearance, setting).

            Rules for image_prompt (CRITICAL - this drives image generation):
            - Structure: [SUBJECT] + [ACTION/POSE] + [ENVIRONMENT] + [CAMERA FRAMING] + [LIGHTING] + [MOOD] + [ART STYLE]
            - SUBJECT: Describe main character/object clearly (e.g., "young woman with red hair wearing leather jacket")
            - ACTION: What they're doing (e.g., "standing confidently", "running urgently")
            - ENVIRONMENT: Setting details (e.g., "abandoned warehouse interior", "rain-soaked city street at night")
            - CAMERA FRAMING: Shot type (e.g., "medium close-up shot", "wide cinematic angle", "low angle perspective", "over-the-shoulder view")
            - LIGHTING: Specific lighting (e.g., "dramatic side lighting", "golden hour sunlight", "neon-lit shadows", "soft diffused morning light")
            - MOOD: Emotional tone (e.g., "tense atmosphere", "peaceful serenity", "mysterious ambiance")
            - ART STYLE: Visual style (e.g., "cinematic concept art", "photorealistic digital painting", "film still from thriller movie")
            - Keep image_prompt between 40-120 words.
            - Be specific and visual - avoid vague terms like "nice" or "beautiful".
            - Include color palette hints when relevant (e.g., "cool blue tones", "warm orange and teal grading").

            JSON only. No markdown. No extra text.
            """
        ).strip()

        full_prompt = f"{instruction}\n\nUser idea:\n{prompt}"
        parsed = self._request_valid_storyboard_payload(full_prompt)
        title = self._safe_text(
            parsed.get("title"), fallback="Untitled Storyboard", max_length=80
        )

        raw_panels = parsed.get("panels")
        if not isinstance(raw_panels, list) or not raw_panels:
            raise StoryboardServiceError("AI did not return a valid panel list.")

        normalized_panels: list[dict[str, Any]] = []
        for idx in range(panel_count):
            source = (
                raw_panels[idx]
                if idx < len(raw_panels) and isinstance(raw_panels[idx], dict)
                else {}
            )
            scene_description = self._safe_text(
                source.get("scene_description"),
                fallback=f"Scene {idx + 1} based on: {prompt[:80]}",
                max_length=360,
            )
            dialogue = self._safe_text(
                source.get("dialogue"),
                fallback="(No dialogue)",
                max_length=220,
            )
            image_prompt = self._safe_text(
                source.get("image_prompt"),
                fallback=f"Cinematic storyboard frame of: {scene_description}",
                max_length=600,
            )

            image_url = self._build_pollinations_image_url(
                image_prompt=image_prompt,
                panel_index=idx,
                model_variant="",
                attempt=1,
            )

            normalized_panels.append(
                {
                    "panel_number": idx + 1,
                    "scene_description": scene_description,
                    "dialogue": dialogue,
                    "image_prompt": image_prompt,
                    "image_url": image_url,
                }
            )

        self._attach_embedded_images_to_panels(normalized_panels)

        return title, normalized_panels

    def _request_valid_storyboard_payload(self, full_prompt: str) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(1, 4):
            attempt_prompt = full_prompt
            if attempt > 1:
                attempt_prompt = (
                    f"{full_prompt}\n\n"
                    "CRITICAL RETRY RULES:\n"
                    "- Return strict valid JSON only.\n"
                    "- Escape quotes inside dialogue and descriptions with \\\".\n"
                    "- No trailing commas.\n"
                    "- Keep exactly the required schema and panel count."
                )

            raw_text = self._request_vexa_query(attempt_prompt).strip()
            try:
                return self._extract_json(raw_text)
            except StoryboardServiceError as exc:
                last_error = exc

        if last_error is not None:
            raise StoryboardServiceError(
                f"AI returned invalid JSON after retries: {self._summarize_exception(last_error)}"
            ) from last_error

        raise StoryboardServiceError("AI returned invalid JSON after retries.")

    def _attach_embedded_images_to_panels(self, panels: list[dict[str, Any]]) -> None:
        import logging

        logger = logging.getLogger(__name__)

        gemini_api_key = self._get_gemini_api_key()
        use_gemini = bool(gemini_api_key)
        gemini_failed = False

        for idx, panel in enumerate(panels):
            image_prompt = self._safe_text(
                panel.get("image_prompt"),
                fallback=panel.get("scene_description", "Cinematic storyboard frame"),
                max_length=600,
            )

            if use_gemini and not gemini_failed:
                try:
                    data_url = self._generate_gemini_data_url(
                        image_prompt=image_prompt,
                        panel_index=idx,
                    )
                    panel["image_url"] = ""
                    panel["image_data_url"] = data_url
                    panel["image_provider"] = "gemini"
                    panel["image_status"] = "ready"
                    panel["image_status_message"] = "Gemini image ready"
                    logger.info(
                        "Panel %s/%s image generated with Gemini",
                        idx + 1,
                        len(panels),
                    )
                    continue
                except StoryboardServiceError as exc:
                    failure_summary = self._summarize_exception(exc)
                    logger.warning(
                        "Panel %s Gemini image failed, falling back to Pollinations: %s",
                        idx + 1,
                        failure_summary,
                    )
                    if self._is_permanent_quota_failure(failure_summary):
                        gemini_failed = True

            image_url = self._build_pollinations_image_url(
                image_prompt=image_prompt,
                panel_index=idx,
                model_variant=POLLINATIONS_MODEL_VARIANTS[0],
                attempt=1,
            )

            logger.info(f"Panel {idx + 1}/{len(panels)} image URL generated")

            panel["image_url"] = image_url
            panel["image_provider"] = "pollinations"
            panel["image_status"] = "ready"
            panel["image_data_url"] = ""

    def _generate_gemini_data_url(self, image_prompt: str, panel_index: int) -> str:
        api_key = self._get_gemini_api_key()
        if not api_key:
            raise StoryboardServiceError(
                "Gemini API key is missing. Set GEMINI_API_KEY (or GAMINI_API_KEY)."
            )

        model = (
            os.getenv("GEMINI_IMAGE_MODEL", GEMINI_DEFAULT_IMAGE_MODEL).strip()
            or GEMINI_DEFAULT_IMAGE_MODEL
        )

        is_imagen = model.startswith("imagen")
        endpoint_action = "predict" if is_imagen else "generateContent"

        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(model, safe='')}:{endpoint_action}"
        )

        prompt_with_style = (
            "Generate one cinematic storyboard frame image. "
            "Do not return markdown. Focus on visual clarity and continuity. "
            f"Scene: {image_prompt}"
        )

        if is_imagen:
            payload = {
                "instances": [{"prompt": prompt_with_style}],
                "parameters": {"sampleCount": 1},
            }
        else:
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt_with_style}],
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                },
            }
        params = {"key": api_key}

        last_error = "unknown Gemini failure"
        for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
            try:
                response = requests.post(
                    endpoint,
                    params=params,
                    json=payload,
                    timeout=GEMINI_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                last_error = f"request failed: {self._summarize_exception(exc)}"
                if attempt < GEMINI_MAX_ATTEMPTS:
                    time.sleep(min(2 ** attempt, GEMINI_MAX_RETRY_SLEEP_SECONDS))
                    continue
                break

            if response.ok:
                try:
                    parsed = response.json()
                except ValueError as exc:
                    last_error = (
                        "Gemini returned non-JSON response: "
                        f"{self._summarize_exception(exc)}"
                    )
                    if attempt < GEMINI_MAX_ATTEMPTS:
                        time.sleep(min(2 ** attempt, GEMINI_MAX_RETRY_SLEEP_SECONDS))
                        continue
                    break

                data_url = self._extract_gemini_inline_image_data_url(parsed)
                if data_url:
                    return data_url

                text_preview = self._extract_gemini_text(parsed)
                last_error = (
                    "Gemini returned no image data"
                    + (f": {text_preview[:180]}" if text_preview else "")
                )
                if attempt < GEMINI_MAX_ATTEMPTS:
                    time.sleep(min(2 ** attempt, GEMINI_MAX_RETRY_SLEEP_SECONDS))
                    continue
                break

            error_body = (response.text or "").strip()[:400]
            last_error = f"Gemini error {response.status_code}: {error_body or 'no details'}"

            if self._is_permanent_quota_error(error_body):
                raise StoryboardServiceError(
                    "Gemini image generation quota exceeded (free tier). "
                    f"{error_body[:200]}"
                )

            if self._is_retryable_gemini_error(response.status_code, error_body):
                if attempt < GEMINI_MAX_ATTEMPTS:
                    time.sleep(min(2 ** attempt, GEMINI_MAX_RETRY_SLEEP_SECONDS))
                    continue

            if 400 <= response.status_code < 500:
                raise StoryboardServiceError(
                    "Gemini image generation request was rejected "
                    f"({response.status_code}). {error_body or 'Check API key/model.'}"
                )

        raise StoryboardServiceError(
            "Gemini image generation is currently busy. "
            f"Please wait a moment and retry. {last_error}"
        )

    def _get_gemini_api_key(self) -> str:
        return (
            (os.getenv("GEMINI_API_KEY", "") or os.getenv("GAMINI_API_KEY", ""))
            .strip()
        )

    def _extract_gemini_inline_image_data_url(self, payload: dict[str, Any]) -> str:
        # Check for Imagen 3 format
        predictions = payload.get("predictions")
        if isinstance(predictions, list) and predictions:
            for pred in predictions:
                if not isinstance(pred, dict):
                    continue
                encoded = pred.get("bytesBase64Encoded")
                mime_type = pred.get("mimeType") or "image/jpeg"
                if isinstance(encoded, str) and encoded.strip():
                    return f"data:{mime_type};base64,{encoded.strip()}"

        # Otherwise fallback to Gemini generateContent format
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return ""

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue

            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline_data = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline_data, dict):
                    continue

                encoded = inline_data.get("data")
                mime_type = inline_data.get("mimeType") or inline_data.get("mime_type")
                if isinstance(encoded, str) and encoded.strip():
                    resolved_mime = (
                        mime_type.strip()
                        if isinstance(mime_type, str) and mime_type.strip()
                        else "image/png"
                    )
                    return f"data:{resolved_mime};base64,{encoded.strip()}"

        return ""

    def _extract_gemini_text(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return ""

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"].strip()
        return ""

    def _is_permanent_quota_failure(self, error_text: str) -> bool:
        lowered = error_text.lower()
        return any(
            phrase in lowered
            for phrase in ("exceeded your current quota", "limit: 0", "only available on paid plans")
        )

    def _is_permanent_quota_error(self, body: str) -> bool:
        lowered = body.lower()
        permanent_phrases = (
            "exceeded your current quota",
            "limit: 0",
            "only available on paid plans",
        )
        return any(phrase in lowered for phrase in permanent_phrases)

    def _is_retryable_gemini_error(self, status_code: int, body: str) -> bool:
        if status_code in (429, 500, 502, 503, 504):
            return True

        lowered = body.lower()
        retryable_phrases = (
            "wait a moment",
            "temporarily unavailable",
            "overloaded",
            "resource exhausted",
            "quota exceeded",
            "rate limit",
            "try again",
        )
        return any(phrase in lowered for phrase in retryable_phrases)

    def _looks_like_busy_gemini_error(self, error_text: str) -> bool:
        lowered = error_text.lower()
        busy_phrases = (
            "wait a moment",
            "temporarily unavailable",
            "overloaded",
            "resource exhausted",
            "quota exceeded",
            "rate limit",
            "try again",
            "currently busy",
        )
        return any(phrase in lowered for phrase in busy_phrases)

    def _generate_pollinations_data_url(self, image_prompt: str, panel_index: int) -> str:
        last_error = "Unknown image provider failure"

        for model_variant in POLLINATIONS_MODEL_VARIANTS:
            for attempt in range(1, POLLINATIONS_MAX_ATTEMPTS_PER_MODEL + 1):
                image_url = self._build_pollinations_image_url(
                    image_prompt=image_prompt,
                    panel_index=panel_index,
                    model_variant=model_variant,
                    attempt=attempt,
                )

                try:
                    response = requests.get(
                        image_url,
                        timeout=150,  # Increased timeout for larger images
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/123.0.0.0 Safari/537.36"
                            ),
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Encoding": "gzip, deflate, br",
                        },
                    )
                except requests.RequestException as exc:
                    last_error = f"request failed: {self._summarize_exception(exc)}"
                    if attempt < POLLINATIONS_MAX_ATTEMPTS_PER_MODEL:
                        # Exponential backoff: 2s, 4s, 8s
                        delay = min(2 ** attempt, POLLINATIONS_MAX_RETRY_SLEEP_SECONDS)
                        time.sleep(delay)
                    continue

                content_type = response.headers.get("content-type", "")
                if (
                    response.ok
                    and content_type.startswith("image/")
                    and len(response.content) >= POLLINATIONS_MIN_VALID_IMAGE_BYTES
                ):
                    mime_type = content_type.split(";", 1)[0].strip() or "image/jpeg"
                    encoded = base64.b64encode(response.content).decode("ascii")
                    return f"data:{mime_type};base64,{encoded}"

                # Handle specific error cases
                if response.status_code == 429:
                    last_error = "provider rate limited image generation (429)"
                    if attempt < POLLINATIONS_MAX_ATTEMPTS_PER_MODEL:
                        delay = max(self._retry_delay_seconds(response, attempt), 3.0 * attempt)
                        time.sleep(min(delay, POLLINATIONS_MAX_RETRY_SLEEP_SECONDS))
                        continue
                elif response.status_code in (500, 502, 503, 504):
                    last_error = f"provider server error ({response.status_code})"
                    if attempt < POLLINATIONS_MAX_ATTEMPTS_PER_MODEL:
                        delay = min(2 ** attempt, POLLINATIONS_MAX_RETRY_SLEEP_SECONDS)
                        time.sleep(delay)
                        continue
                else:
                    error_preview = (response.text or "").strip()[:200]
                    last_error = (
                        f"provider error ({response.status_code}): {error_preview or 'no details'}"
                    )
                    if attempt < POLLINATIONS_MAX_ATTEMPTS_PER_MODEL:
                        time.sleep(min(1.5 * attempt, 4.0))

        raise StoryboardServiceError(
            f"Public image provider is busy after multiple attempts. {last_error}. Please try again in a few moments."
        )

    def _build_pollinations_image_url(
        self,
        image_prompt: str,
        panel_index: int,
        model_variant: str,
        attempt: int,
    ) -> str:
        # Enhance prompt with quality boosters (removed --no syntax which breaks URL)
        enhanced_prompt = f"{image_prompt}, high quality, detailed, professional composition"
        
        encoded_prompt = urllib.parse.quote(enhanced_prompt[:500], safe="")
        
        # Use unique seeds per panel and attempt to ensure variety
        seed = (panel_index * 1000) + (attempt * 73) + 42
        
        query_params = {
            "nologo": "true",
            "width": "1280",
            "height": "720",
            "seed": str(seed),
            "enhance": "true",  # Pollinations prompt enhancement
        }
        
        if model_variant:
            query_params["model"] = model_variant
            
        query = urllib.parse.urlencode(query_params)
        return f"https://image.pollinations.ai/prompt/{encoded_prompt}?{query}"

    def _retry_delay_seconds(self, response: requests.Response, attempt: int) -> float:
        retry_after_raw = (response.headers.get("retry-after") or "").strip()
        if retry_after_raw.isdigit():
            return min(float(retry_after_raw), POLLINATIONS_MAX_RETRY_SLEEP_SECONDS)
        return min(1.3 * attempt, POLLINATIONS_MAX_RETRY_SLEEP_SECONDS)

    def _request_vexa_query(self, full_prompt: str) -> str:
        endpoint = f"https://vexa-ai.pages.dev/query?q={urllib.parse.quote(full_prompt[:4000], safe='')}"
        try:
            response = requests.get(endpoint, timeout=60)
        except requests.RequestException as exc:
            raise StoryboardServiceError(
                f"Vexa AI request failed: {self._summarize_exception(exc)}"
            ) from exc

        if not response.ok:
            raise StoryboardServiceError(
                f"Vexa AI error ({response.status_code}): {(response.text or '').strip()[:200]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise StoryboardServiceError(
                f"Vexa AI returned non-JSON output: {(response.text or '').strip()[:200]}"
            ) from exc

        if not data.get("success"):
            raise StoryboardServiceError(
                f"Vexa AI returned error: {data.get('error', 'unknown')}"
            )

        text = (data.get("response") or "").strip()
        if not text:
            raise StoryboardServiceError("Vexa AI returned an empty response.")
        return text

    def _summarize_exception(self, exc: Exception) -> str:
        summary = " ".join(str(exc).split())
        if summary:
            return summary[:280]
        return exc.__class__.__name__

    def _extract_json(self, text: str) -> dict[str, Any]:
        if not text:
            raise StoryboardServiceError("AI returned an empty response.")

        fence_match = re.search(
            r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE
        )
        candidate = fence_match.group(1).strip() if fence_match else text

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise StoryboardServiceError("AI response was not valid JSON.")

        json_blob = candidate[start : end + 1]
        try:
            parsed = json.loads(json_blob)
        except json.JSONDecodeError as exc:
            raise StoryboardServiceError(f"Could not parse JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            raise StoryboardServiceError("JSON root must be an object.")

        return parsed

    def _safe_text(self, value: Any, fallback: str, max_length: int) -> str:
        if not isinstance(value, str):
            return fallback
        cleaned = " ".join(value.split())
        if not cleaned:
            return fallback
        return cleaned[:max_length]
