from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.routes import fix_encoding
from app.services.iett_scraper import fetch_official_announcements


def test_fix_encoding_mojibake():
    # Double encoded text from upstream
    corrupted = "Sefer Â· KayÄ±t Saati"
    fixed = fix_encoding(corrupted)
    assert fixed == "Sefer · Kayıt Saati"


def test_fix_encoding_valid_text():
    # Text that is already valid shouldn't be mangled unless it matches the signature.
    # Actually, the fix_encoding specifically targets mojibake characters.
    # If the text is valid ASCII or simple Turkish without mojibake signs:
    valid = "Normal durak duyurusu: Bekleniyor."
    assert fix_encoding(valid) == valid


def test_fix_encoding_null():
    assert fix_encoding(None) is None
    assert fix_encoding("") == ""


class TestOfficialAnnouncements:
    SAMPLE_HTML = """
    <html>
        <body>
            <input type="hidden" id="totalItems" value="42" />
            <a class="mini-news-row" href="/tr/Announcement/Detail/101">
                <span class="date">15.10.2026</span>
                <span class="info">Metrobüs Hattında Bakım Çalışması</span>
            </a>
            <a class="mini-news-row" href="https://iett.istanbul/tr/Announcement/Detail/102">
                <span class="date">14.10.2026</span>
                <span class="info">500T Güzergah Değişikliği</span>
            </a>
        </body>
    </html>
    """

    @pytest.mark.asyncio
    async def test_fetch_official_announcements_parsing(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = self.SAMPLE_HTML

        cm = AsyncMock()
        cm.__aenter__.return_value = mock_resp
        cm.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = cm

        result = await fetch_official_announcements(page=0, session=mock_session)
        assert result["page"] == 0
        assert result["total_items"] == 42
        assert len(result["items"]) == 2
        assert result["items"][0]["id"] == "101"
        assert result["items"][0]["title"] == "Metrobüs Hattında Bakım Çalışması"
        assert result["items"][0]["date"] == "15.10.2026"
        assert result["items"][0]["url"] == "https://iett.istanbul/tr/Announcement/Detail/101"
        assert result["items"][1]["id"] == "102"
        assert result["items"][1]["url"] == "https://iett.istanbul/tr/Announcement/Detail/102"

    def test_get_official_announcements_endpoint(self) -> None:
        from app.deps import get_session

        client = TestClient(app)
        app.dependency_overrides[get_session] = lambda: MagicMock()
        try:
            mock_data = {
                "page": 0,
                "total_items": 1,
                "items": [
                    {
                        "id": "1",
                        "title": "Test Başlık",
                        "date": "16.10.2026",
                        "url": "https://iett.istanbul/tr/Announcement/Detail/1",
                    }
                ],
            }
            with patch(
                "app.routers.announcements.fetch_official_announcements",
                new_callable=AsyncMock,
            ) as mock_fetch:
                mock_fetch.return_value = mock_data
                resp = client.get("/v1/announcements/official?page=0")
                assert resp.status_code == 200
                body = resp.json()
                assert body["page"] == 0
                assert body["total_items"] == 1
                assert len(body["items"]) == 1
                assert body["items"][0]["title"] == "Test Başlık"
        finally:
            app.dependency_overrides.pop(get_session, None)
