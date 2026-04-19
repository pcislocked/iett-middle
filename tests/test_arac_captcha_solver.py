"""Unit tests for app.services.arac_captcha_solver."""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

import app.services.arac_captcha_solver as solver
from app.services.arac_client import AracApiError


class _FakeNP:
    uint8 = object()

    @staticmethod
    def frombuffer(value: bytes, _dtype: object) -> bytes:
        return value

    @staticmethod
    def array(value: list[int]) -> list[int]:
        return value

    @staticmethod
    def ones(shape: tuple[int, int], _dtype: object) -> list[list[int]]:
        return [[1] * shape[1] for _ in range(shape[0])]


class _FakeCV2:
    IMREAD_COLOR = 1
    COLOR_BGR2HSV = 40
    COLOR_BGR2GRAY = 41
    MORPH_OPEN = 42
    THRESH_BINARY = 43
    THRESH_OTSU = 44

    @staticmethod
    def imdecode(_arr: bytes, _flag: int) -> str:
        return "image-bgr"

    @staticmethod
    def cvtColor(_image: str, code: int) -> str:
        if code == _FakeCV2.COLOR_BGR2GRAY:
            return "gray-image"
        return "hsv-image"

    @staticmethod
    def inRange(_hsv: str, _lower: object, _upper: object) -> str:
        return "mask"

    @staticmethod
    def morphologyEx(_mask: str, _op: int, _kernel: object) -> str:
        return "opened"

    @staticmethod
    def bitwise_not(_opened: str) -> str:
        return "masked-image"

    @staticmethod
    def threshold(_gray: str, _a: int, _b: int, _flags: int) -> tuple[None, str]:
        return None, "thresholded-image"


class _FakeCV2DecodeNone(_FakeCV2):
    @staticmethod
    def imdecode(_arr: bytes, _flag: int) -> None:
        return None


class TestHelperFns:
    def test_get_reader_cached(self) -> None:
        solver._OCR_READER = None

        class _EasyOcr:
            calls = 0

            class Reader:  # type: ignore[override]
                def __init__(self, _langs: list[str], gpu: bool = False) -> None:
                    _ = gpu
                    _EasyOcr.calls += 1

                def readtext(self, _image: object, detail: int, allowlist: str) -> list[object]:
                    _ = (detail, allowlist)
                    return []

        first = solver._get_reader(_EasyOcr)
        second = solver._get_reader(_EasyOcr)
        assert first is second
        assert _EasyOcr.calls == 1
        solver._OCR_READER = None

    def test_normalize_guess(self) -> None:
        assert solver._normalize_guess(" a-b c!d ") == "ABCD"

    def test_extract_ordered_text_sorts_by_x(self) -> None:
        class _Reader:
            def readtext(self, _image: object, detail: int, allowlist: str) -> list[object]:
                _ = (detail, allowlist)
                return [
                    ([[10, 0], [11, 0], [11, 1], [10, 1]], "B", 0.8),
                    ([[1, 0], [2, 0], [2, 1], [1, 1]], "A", 0.9),
                ]

        assert solver._extract_ordered_text(_Reader(), "img") == "AB"

    def test_extract_ordered_text_empty_returns_empty(self) -> None:
        class _Reader:
            def readtext(self, _image: object, detail: int, allowlist: str) -> list[object]:
                _ = (detail, allowlist)
                return []

        assert solver._extract_ordered_text(_Reader(), "img") == ""

    def test_expand_guess_variants(self) -> None:
        assert solver._expand_guess_variants("ABC", 10) == []
        variants = solver._expand_guess_variants("O1S8", 5)
        assert len(variants) == 5
        assert "O1S8" in variants

    def test_load_dependencies_missing_raises(self) -> None:
        import builtins

        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name in {"cv2", "easyocr", "numpy"}:
                raise ModuleNotFoundError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with pytest.raises(AracApiError, match="Auto-solve requires") as exc_info:
                solver._load_ocr_dependencies()
        assert exc_info.value.status_code == 503

    def test_load_dependencies_success_with_fake_modules(self) -> None:
        import builtins

        original_import = builtins.__import__

        class _DummyCv2:
            pass

        class _DummyEasyOcr:
            pass

        class _DummyNp:
            pass

        def _fake_import(name, *args, **kwargs):
            if name == "cv2":
                return _DummyCv2
            if name == "easyocr":
                return _DummyEasyOcr
            if name == "numpy":
                return _DummyNp
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cv2, easyocr, np = solver._load_ocr_dependencies()
        assert cv2 is _DummyCv2
        assert easyocr is _DummyEasyOcr
        assert np is _DummyNp


class TestCollectCaptchaCandidates:
    def test_collect_candidates_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(solver, "_load_ocr_dependencies", lambda: (_FakeCV2, object(), _FakeNP))
        monkeypatch.setattr(solver, "_get_reader", lambda _easyocr: object())

        def _fake_extract(_reader: object, image: object) -> str:
            mapping = {
                "masked-image": "ABCD",
                "image-bgr": "ABOD",
                "thresholded-image": "A8CD",
            }
            return mapping.get(str(image), "")

        monkeypatch.setattr(solver, "_extract_ordered_text", _fake_extract)

        candidates = solver.collect_captcha_candidates(b"abcd", max_candidates=6)
        assert candidates
        assert candidates[0] == "ABCD"
        assert len(candidates) <= 6

    def test_collect_candidates_returns_empty_when_no_4_char_guess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(solver, "_load_ocr_dependencies", lambda: (_FakeCV2, object(), _FakeNP))
        monkeypatch.setattr(solver, "_get_reader", lambda _easyocr: object())
        monkeypatch.setattr(solver, "_extract_ordered_text", lambda _reader, _image: "ABC")

        assert solver.collect_captcha_candidates(b"abcd", max_candidates=4) == []

    def test_collect_candidates_normalizes_non_positive_max_and_returns_tail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(solver, "_load_ocr_dependencies", lambda: (_FakeCV2, object(), _FakeNP))
        monkeypatch.setattr(solver, "_get_reader", lambda _easyocr: object())

        def _fake_extract(_reader: object, image: object) -> str:
            if str(image) == "masked-image":
                return "ABCD"
            return ""

        monkeypatch.setattr(solver, "_extract_ordered_text", _fake_extract)

        one_candidate = solver.collect_captcha_candidates(b"abcd", max_candidates=0)
        assert len(one_candidate) == 1

        many_candidates = solver.collect_captcha_candidates(b"abcd", max_candidates=10)
        assert many_candidates
        assert len(many_candidates) < 10

    def test_collect_candidates_invalid_image_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(solver, "_load_ocr_dependencies", lambda: (_FakeCV2DecodeNone, object(), _FakeNP))
        with pytest.raises(AracApiError, match="Invalid captcha image"):
            solver.collect_captcha_candidates(b"abcd", max_candidates=4)


class TestCollectFromBase64:
    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(AracApiError, match="Invalid captchaImageBase64"):
            solver.collect_captcha_candidates_from_base64("***bad***")

    def test_delegates_to_collect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(solver, "collect_captcha_candidates", lambda _image, max_candidates=8: ["ABCD", str(max_candidates)])
        encoded = base64.b64encode(b"image-bytes").decode("utf-8")
        result = solver.collect_captcha_candidates_from_base64(encoded, max_candidates=3)
        assert result[0] == "ABCD"
        assert result[1] == "3"
