"""Best-effort captcha solver for ARAC session bootstrap."""
from __future__ import annotations

import base64
import itertools
import threading
from typing import Any

from app.services.arac_client import AracApiError


ALLOW_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_OCR_READER: Any | None = None
_OCR_READER_LOCK = threading.Lock()

AMBIGUITY_MAP: dict[str, list[str]] = {
    "0": ["0", "O", "Q", "D"],
    "O": ["O", "0", "Q", "D"],
    "Q": ["Q", "O", "0"],
    "D": ["D", "0", "O"],
    "1": ["1", "I", "L", "J"],
    "I": ["I", "1", "L"],
    "L": ["L", "1", "I"],
    "J": ["J", "1", "I"],
    "5": ["5", "S"],
    "S": ["S", "5"],
    "2": ["2", "Z"],
    "Z": ["Z", "2"],
    "8": ["8", "B"],
    "B": ["B", "8"],
}


def _load_ocr_dependencies() -> tuple[Any, Any, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
        import easyocr  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise AracApiError(
            "Auto-solve requires easyocr, opencv-python-headless, and numpy",
            status_code=503,
        ) from exc
    return cv2, easyocr, np


def _get_reader(easyocr_module: Any) -> Any:
    global _OCR_READER  # noqa: PLW0603
    if _OCR_READER is None:
        with _OCR_READER_LOCK:
            if _OCR_READER is None:
                _OCR_READER = easyocr_module.Reader(["en"], gpu=False)
    return _OCR_READER


def _normalize_guess(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _extract_ordered_text(reader: Any, image: Any) -> str:
    results = reader.readtext(image, detail=1, allowlist=ALLOW_CHARS)
    if not results:
        return ""
    sorted_results = sorted(results, key=lambda item: item[0][0][0])
    return "".join(part[1] for part in sorted_results).replace(" ", "").upper()


def _expand_guess_variants(guess: str, max_variants: int) -> list[str]:
    if len(guess) != 4:
        return []

    options = [AMBIGUITY_MAP.get(ch, [ch]) for ch in guess]
    variants: list[str] = []
    for combo in itertools.product(*options):
        value = "".join(combo)
        if value not in variants:
            variants.append(value)
        if len(variants) >= max_variants:
            break
    return variants


def _get_hsv_masked_image(cv2: Any, np: Any, image_bgr: Any) -> Any:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    lower_bound = np.array([0, 80, 0])
    upper_bound = np.array([179, 255, 255])
    mask = cv2.inRange(hsv, lower_bound, upper_bound)
    kernel = np.ones((2, 2), np.uint8)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return cv2.bitwise_not(opened)


def collect_captcha_candidates(image_bytes: bytes, max_candidates: int = 8) -> list[str]:
    cv2, easyocr, np = _load_ocr_dependencies()
    if max_candidates < 1:
        max_candidates = 1

    nparr = np.frombuffer(image_bytes, np.uint8)
    image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise AracApiError("Invalid captcha image", status_code=400)

    reader = _get_reader(easyocr)

    base_guesses: list[str] = []
    images_to_try: list[Any] = [
        _get_hsv_masked_image(cv2, np, image_bgr),
        image_bgr,
    ]

    grayscale = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, thresholded = cv2.threshold(grayscale, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    images_to_try.append(thresholded)

    for candidate_image in images_to_try:
        guess = _normalize_guess(_extract_ordered_text(reader, candidate_image))
        if len(guess) == 4 and guess not in base_guesses:
            base_guesses.append(guess)

    if not base_guesses:
        return []

    candidates: list[str] = []
    for base_guess in base_guesses:
        for variant in _expand_guess_variants(base_guess, max_variants=max_candidates):
            if variant not in candidates:
                candidates.append(variant)
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def collect_captcha_candidates_from_base64(captcha_image_base64: str, max_candidates: int = 8) -> list[str]:
    try:
        image_bytes = base64.b64decode(captcha_image_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AracApiError("Invalid captchaImageBase64 payload", status_code=400) from exc
    return collect_captcha_candidates(image_bytes, max_candidates=max_candidates)
