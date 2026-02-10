from api import extract_tiktok_identifiers, parse_compact_number, validate_tiktok_url


def test_validate_tiktok_url_accepts_full_video_url():
    url = "https://www.tiktok.com/@user/video/1234567890?is_from_webapp=1&sender_device=pc"
    assert validate_tiktok_url(url) is True


def test_validate_tiktok_url_rejects_non_tiktok():
    assert validate_tiktok_url("https://example.com/video/123") is False


def test_extract_tiktok_identifiers_from_full_url():
    data = extract_tiktok_identifiers("https://www.tiktok.com/@john/video/7592044461266423047")
    assert data["username"] == "john"
    assert data["video_id"] == "7592044461266423047"


def test_parse_compact_number():
    assert parse_compact_number("1.2K") == 1200
    assert parse_compact_number("745") == 745
