import argparse
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout, sync_playwright

from main import (
    DEFAULT_USER_AGENT,
    SSSTIK_URL,
    collect_album_assets,
    find_download_links,
    launch_chromium,
    wait_for_download_buttons,
)


class ExtractRequest(BaseModel):
    url: str = Field(..., description="TikTok post URL (video/photo)")
    timeout_seconds: int = Field(default=60, ge=20, le=180)


class ExtractResponse(BaseModel):
    ok: bool
    timestamp_utc: str
    source: str
    requested_url: str
    validated_url: bool
    tiktok: dict
    page: dict
    video: dict
    album: dict
    download_buttons: dict
    preferred_download: dict
    timing: dict


app = FastAPI(
    title="SSSTik TikTok Extractor API",
    version="1.1.0",
    description="Recebe link do TikTok e retorna links dos botoes do ssstik + metadados do post em JSON.",
)


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def parse_compact_number(value: str):
    if not value:
        return None
    token = normalize_text(value).lower().replace(" ", "")
    token = token.replace(",", ".")
    match = re.match(r"^(\d+(?:\.\d+)?)([kmb])?$", token)
    if match:
        base = float(match.group(1))
        suffix = match.group(2)
        scale = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
        return int(base * scale)

    digits = re.sub(r"[^\d]", "", token)
    return int(digits) if digits else None


def validate_tiktok_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False

    host = parsed.netloc.lower().split(":")[0]
    if not host.endswith("tiktok.com"):
        return False

    # Aceita formato completo de video/photo e links compartilhados/encurtados.
    if "/video/" in parsed.path or "/photo/" in parsed.path or host in {"vm.tiktok.com", "vt.tiktok.com"}:
        return True
    return False


def extract_tiktok_identifiers(url: str) -> dict:
    parsed = urlparse(url)
    result = {
        "host": parsed.netloc,
        "path": parsed.path,
        "username": None,
        "video_id": None,
        "post_type": None,
        "post_id": None,
    }
    match = re.search(r"/@([^/]+)/(video|photo)/(\d+)", parsed.path)
    if match:
        result["username"] = match.group(1)
        result["post_type"] = match.group(2)
        result["post_id"] = match.group(3)
        if result["post_type"] == "video":
            result["video_id"] = result["post_id"]
        return result

    video_match = re.search(r"/video/(\d+)", parsed.path)
    if video_match:
        result["video_id"] = video_match.group(1)
        result["post_type"] = "video"
        result["post_id"] = video_match.group(1)
        return result

    photo_match = re.search(r"/photo/(\d+)", parsed.path)
    if photo_match:
        result["post_type"] = "photo"
        result["post_id"] = photo_match.group(1)
    return result


def _extract_video_details_from_page(page) -> dict:
    data = page.evaluate(
        """
        () => {
          const root = document.querySelector('#mainpicture, .result');
          if (!root) return null;

          const authorName = (root.querySelector('h2')?.textContent || '').trim() || null;
          const description = (root.querySelector('p.maintext')?.textContent || '').trim() || null;
          const avatarEl = root.querySelector('img.result_author');
          const avatarUrl = avatarEl?.getAttribute('src') || null;
          const avatarAlt = avatarEl?.getAttribute('alt') || null;

          const statNodes = Array.from(
            root.querySelectorAll('#trending-actions .d-flex > div:last-child')
          );
          const statsRaw = statNodes.map(n => (n.textContent || '').trim());

            const buttons = {};
            const links = root.querySelectorAll('#dl_btns a.download_link, a.download_link');
            links.forEach((a) => {
              const cls = a.className || '';
              const txt = (a.textContent || '').toLowerCase();
              let key = null;
              if (cls.includes('without_watermark_hd')) key = 'hd';
              else if (cls.includes('without_watermark')) key = 'nowm';
              else if (cls.includes('music')) key = 'mp3';
              else if (cls.includes('slide') || txt.includes('slide')) key = 'slide';
              if (!key) return;

              buttons[key] = {
              text: (a.textContent || '').trim().replace(/\\s+/g, ' '),
              href: a.getAttribute('href') || null,
              data_directurl: a.getAttribute('data-directurl') || null,
              css_class: cls || null
            };
          });

          return {
            author_name: authorName,
            description: description,
            avatar_url: avatarUrl,
            avatar_alt: avatarAlt,
            stats_raw: statsRaw,
            buttons: buttons
          };
        }
        """
    )

    if not data:
        return {
            "author_name": None,
            "description": None,
            "avatar_url": None,
            "avatar_alt": None,
            "stats_raw": [],
            "buttons": {},
        }
    return data


def _build_download_buttons_payload(found_links: dict, page_buttons: dict) -> dict:
    mapping = {
        "nowm": "without_watermark",
        "hd": "without_watermark_hd",
        "mp3": "mp3",
        "slide": "slide",
    }
    payload = {}
    for source_key, public_key in mapping.items():
        details = page_buttons.get(source_key, {})
        payload[public_key] = {
            "label": details.get("text"),
            "url": found_links.get(source_key),
            "raw_href": details.get("href"),
            "data_directurl": details.get("data_directurl"),
            "css_class": details.get("css_class"),
            "available": source_key in found_links,
        }
    return payload


def scrape_ssstik_summary(tiktok_url: str, timeout_seconds: int = 60) -> dict:
    started = time.perf_counter()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()

        page.goto(SSSTIK_URL, wait_until="domcontentloaded")
        input_box = page.locator("#main_page_text, input[name='id'], input[type='text']").first
        input_box.wait_for(state="visible", timeout=15_000)
        input_box.fill(tiktok_url)

        submit_btn = page.locator("#submit, form button[type='submit']").first
        if submit_btn.count() > 0:
            submit_btn.click()
        else:
            input_box.press("Enter")

        try:
            page.wait_for_timeout(1500)
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass

        wait_for_download_buttons(page, timeout_ms=timeout_seconds * 1000)
        links = find_download_links(page, timeout_ms=timeout_seconds * 1000)
        album_assets = collect_album_assets(page, timeout_ms=min(12_000, timeout_seconds * 1000))
        page_data = _extract_video_details_from_page(page)

        browser.close()

    stats_raw = page_data.get("stats_raw", [])
    stats = {
        "likes": parse_compact_number(stats_raw[0]) if len(stats_raw) > 0 else None,
        "comments": parse_compact_number(stats_raw[1]) if len(stats_raw) > 1 else None,
        "shares": parse_compact_number(stats_raw[2]) if len(stats_raw) > 2 else None,
        "raw": stats_raw,
    }

    button_payload = _build_download_buttons_payload(links, page_data.get("buttons", {}))
    preferred_key = "mp3"
    for candidate in ["without_watermark", "without_watermark_hd", "slide", "mp3"]:
        if button_payload[candidate]["available"]:
            preferred_key = candidate
            break

    slide_download_urls = album_assets.get("slide_links", [])
    slide_image_urls = album_assets.get("image_urls", [])
    if not slide_download_urls and button_payload["slide"]["url"]:
        slide_download_urls = [button_payload["slide"]["url"]]

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    identifiers = extract_tiktok_identifiers(tiktok_url)

    return {
        "ok": True,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": "ssstik.io",
        "requested_url": tiktok_url,
        "validated_url": True,
        "tiktok": identifiers,
        "page": {
            "url": SSSTIK_URL,
            "language": "pt-BR",
        },
        "video": {
            "author_name": page_data.get("author_name"),
            "author_avatar_url": page_data.get("avatar_url"),
            "author_avatar_alt": page_data.get("avatar_alt"),
            "description": page_data.get("description"),
            "stats": stats,
        },
        "album": {
            "count": max(len(slide_download_urls), len(slide_image_urls)),
            "slide_download_urls": slide_download_urls,
            "slide_image_urls": slide_image_urls,
        },
        "download_buttons": button_payload,
        "preferred_download": {
            "kind": preferred_key,
            "url": button_payload[preferred_key]["url"],
        },
        "timing": {
            "elapsed_ms": elapsed_ms,
            "timeout_seconds": timeout_seconds,
        },
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "sss-tiktok-api"}


@app.get("/extract", response_model=ExtractResponse)
def extract_get(url: str = Query(...), timeout_seconds: int = Query(default=60, ge=20, le=180)):
    if not validate_tiktok_url(url):
        raise HTTPException(status_code=422, detail="URL invalida. Informe um link de video/photo do TikTok.")
    try:
        return scrape_ssstik_summary(url, timeout_seconds=timeout_seconds)
    except PWError as exc:
        raise HTTPException(status_code=502, detail=f"Erro Playwright/SSSTik: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha inesperada: {exc}") from exc


@app.post("/extract", response_model=ExtractResponse)
def extract_post(payload: ExtractRequest):
    if not validate_tiktok_url(payload.url):
        raise HTTPException(status_code=422, detail="URL invalida. Informe um link de video/photo do TikTok.")
    try:
        return scrape_ssstik_summary(payload.url, timeout_seconds=payload.timeout_seconds)
    except PWError as exc:
        raise HTTPException(status_code=502, detail=f"Erro Playwright/SSSTik: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha inesperada: {exc}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roda a API HTTP do sss-tiktok.")
    parser.add_argument("--host", default="0.0.0.0", help="Host para bind da API.")
    parser.add_argument("--port", type=int, default=8000, help="Porta da API.")
    parser.add_argument("--reload", action="store_true", help="Ativa reload em desenvolvimento.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    uvicorn.run("api:app", host=args.host, port=args.port, reload=args.reload)
