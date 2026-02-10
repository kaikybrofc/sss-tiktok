import sys
import pathlib
import argparse
import shlex
import subprocess
import time
import unicodedata
from urllib.parse import urljoin, urlparse
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

SSSTIK_URL = "https://ssstik.io/pt-1"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
DOWNLOAD_BUTTON_SELECTORS = {
    "hd": [
        "#dl_btns > a.download_link.without_watermark_hd",
        "a.download_link.without_watermark_hd",
        "a.without_watermark_hd",
        "a[href][class*='without_watermark'][class*='hd']",
    ],
    "nowm": [
        "#dl_btns > a.download_link.without_watermark.vignette_active.notranslate",
        "#dl_btns > a.download_link.without_watermark",
        "a.download_link.without_watermark",
        "a.without_watermark",
        "a[href][class*='without_watermark']",
    ],
    "mp3": [
        "a.download_link.music",
        "a.music",
        "a[href*='mp3']",
    ],
    "slide": [
        "a.download_link.slide",
        "a.slide",
        "a[href][class*='slide']",
    ],
}

def download_file(url: str, out_path: pathlib.Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

def launch_chromium(pw, headless: bool):
    launch_args = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        return pw.chromium.launch(**launch_args)
    except PWError as e:
        msg = str(e)
        if "Executable doesn't exist" not in msg:
            raise

        install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        install_cmd_str = " ".join(shlex.quote(part) for part in install_cmd)
        print("Chromium do Playwright nao encontrado. Instalando automaticamente...")
        result = subprocess.run(install_cmd, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "Falha ao instalar o Chromium do Playwright.\n"
                f"Execute manualmente: {install_cmd_str}"
            ) from e

        return pw.chromium.launch(**launch_args)

def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.lower().split())

def classify_ssstik_button(text: str, class_name: str, href: str) -> str | None:
    text_n = normalize_text(text)
    class_n = normalize_text(class_name.replace("_", " "))
    href_n = normalize_text(href)
    combined = f"{text_n} {class_n} {href_n}"

    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return None

    if "mp3" in combined or "music" in combined:
        return "mp3"

    has_slide_signal = (
        "download this slide" in text_n
        or "baixar este slide" in text_n
        or " slide " in f" {class_n} "
        or "photomode" in href_n
    )
    if has_slide_signal:
        return "slide"

    has_watermark_signal = (
        "sem marca d'agua" in text_n
        or "sem marca dagua" in text_n
        or "without watermark" in combined
        or "without watermark" in class_n
    )
    if has_watermark_signal and "hd" in combined:
        return "hd"
    if has_watermark_signal:
        return "nowm"

    return None

def collect_known_button_links_in_frame(frame, page_url: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for expected_kind, selectors in DOWNLOAD_BUTTON_SELECTORS.items():
        for selector in selectors:
            try:
                items = frame.eval_on_selector_all(
                    selector,
                    """els => els.map(a => ({
                        href: a.getAttribute('href') || '',
                        direct: a.getAttribute('data-directurl') || '',
                        text: (a.textContent || '').trim(),
                        cls: a.className || ''
                    }))""",
                )
            except PWError:
                continue
            for item in items:
                href = (item.get("href") or "").strip() or (item.get("direct") or "").strip()
                if not href:
                    continue
                kind = classify_ssstik_button(
                    (item.get("text") or "").strip(),
                    str(item.get("cls") or ""),
                    href,
                )
                if kind == expected_kind:
                    found[expected_kind] = urljoin(page_url, href)
                    break
            if expected_kind in found:
                break
    return found

def collect_all_anchors_in_frame(frame, page_url: str) -> list[dict[str, str]]:
    try:
        items = frame.eval_on_selector_all(
            "#dl_btns a, a.download_link",
            """els => els.map(a => ({
                href: a.getAttribute('href') || '',
                direct: a.getAttribute('data-directurl') || '',
                text: (a.textContent || '').trim(),
                cls: a.className || ''
            }))""",
        )
    except PWError:
        return []

    rows: list[dict[str, str]] = []
    for item in items:
        href = (item.get("href") or "").strip() or (item.get("direct") or "").strip()
        if not href:
            continue
        rows.append(
            {
                "href": href,
                "text": (item.get("text") or "").strip(),
                "cls": str(item.get("cls") or ""),
                "abs_href": urljoin(page_url, href),
            }
        )
    return rows

def collect_known_button_links(page) -> dict[str, str]:
    found: dict[str, str] = {}
    for frame in page.frames:
        frame_url = frame.url or page.url
        frame_found = collect_known_button_links_in_frame(frame, frame_url)
        for kind, href in frame_found.items():
            if kind not in found:
                found[kind] = href
    return found


def dedupe_keep_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def collect_slide_links(page) -> list[str]:
    slide_links: list[str] = []
    for frame in page.frames:
        frame_url = frame.url or page.url
        anchors = collect_all_anchors_in_frame(frame, frame_url)
        for item in anchors:
            kind = classify_ssstik_button(item["text"], item["cls"], item["href"])
            if kind == "slide":
                slide_links.append(item["abs_href"])
    return dedupe_keep_order(slide_links)


def collect_slide_images_in_frame(frame, page_url: str) -> list[str]:
    try:
        images = frame.eval_on_selector_all(
            "#mainpicture img[data-splide-lazy], #mainpicture img[src], .splide__slide img[data-splide-lazy], .splide__slide img[src]",
            """els => els.map(img => ({
                lazy: img.getAttribute('data-splide-lazy') || '',
                src: img.getAttribute('src') || ''
            }))""",
        )
    except PWError:
        return []

    result: list[str] = []
    for image in images:
        candidate = (image.get("lazy") or "").strip() or (image.get("src") or "").strip()
        if not candidate:
            continue
        abs_url = urljoin(page_url, candidate)
        if "tikcdn.io/ssstik/s/" in abs_url or "photomode" in abs_url:
            result.append(abs_url)
    return result


def collect_slide_images(page) -> list[str]:
    urls: list[str] = []
    for frame in page.frames:
        frame_url = frame.url or page.url
        urls.extend(collect_slide_images_in_frame(frame, frame_url))
    return dedupe_keep_order(urls)


def collect_album_assets(page, timeout_ms: int = 8_000) -> dict[str, list[str]]:
    deadline = time.time() + (timeout_ms / 1000)
    slide_links: list[str] = []
    image_urls: list[str] = []
    stable_rounds = 0
    previous_total = 0
    while time.time() < deadline:
        slide_links = dedupe_keep_order(slide_links + collect_slide_links(page))
        image_urls = dedupe_keep_order(image_urls + collect_slide_images(page))
        current_total = len(slide_links) + len(image_urls)
        if current_total > 0 and current_total == previous_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_total = current_total
        if stable_rounds >= 2:
            break
        page.wait_for_timeout(400)
    return {
        "slide_links": slide_links,
        "image_urls": image_urls,
    }

def find_download_links(page, timeout_ms: int = 60_000) -> dict[str, str]:
    deadline = time.time() + (timeout_ms / 1000)
    debug_candidates: list[str] = []
    found: dict[str, str] = {}

    while time.time() < deadline:
        known_links = collect_known_button_links(page)
        for kind, href in known_links.items():
            if kind not in found:
                found[kind] = href

        if {"hd", "nowm", "mp3"}.issubset(found):
            return found
        if "slide" in found and not any(kind in found for kind in ("hd", "nowm", "mp3")):
            return found

        anchors: list[dict[str, str]] = []
        for frame in page.frames:
            frame_url = frame.url or page.url
            anchors.extend(collect_all_anchors_in_frame(frame, frame_url))

        for item in anchors:
            kind = classify_ssstik_button(item["text"], item["cls"], item["href"])
            if kind and kind not in found:
                found[kind] = item["abs_href"]

        if {"hd", "nowm", "mp3"}.issubset(found):
            return found
        if "slide" in found and not any(kind in found for kind in ("hd", "nowm", "mp3")):
            return found

        debug_candidates = [
            item["abs_href"]
            for item in anchors
        ][:10]
        page.wait_for_timeout(800)

    if found:
        return found

    debug_text = "\n".join(f"- {u}" for u in debug_candidates) if debug_candidates else "- (nenhum link capturado)"
    raise RuntimeError(
        "Nao consegui encontrar os links dos botoes de download no ssstik dentro do tempo limite.\n"
        f"Links capturados para depuracao:\n{debug_text}"
    )

def wait_for_download_buttons(page, timeout_ms: int = 45_000):
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        count = page.locator("#dl_btns a.download_link, a.download_link").count()
        if count > 0:
            return
        page.wait_for_timeout(500)
    raise RuntimeError(
        "Os botoes de download do ssstik nao apareceram no tempo limite. "
        "O site pode ter acionado anti-bot/reCAPTCHA."
    )


def infer_image_extension(url: str) -> str:
    suffix = pathlib.PurePosixPath(urlparse(url).path.lower()).suffix
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return ".jpg"

def main(tiktok_url: str, headless: bool = True):
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
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

        # Seletores mais estaveis do ssstik.
        input_box = page.locator("#main_page_text, input[name='id'], input[type='text']").first
        input_box.wait_for(state="visible", timeout=15_000)
        input_box.fill(tiktok_url)

        submit_btn = page.locator("#submit, form button[type='submit']").first
        if submit_btn.count() > 0:
            submit_btn.click()
        else:
            # Fallback: envia o form via Enter.
            input_box.press("Enter")

        # aguarda a página/resultado carregar e procura links mp4
        try:
            page.wait_for_timeout(1500)
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
        wait_for_download_buttons(page, timeout_ms=45_000)

        links = find_download_links(page, timeout_ms=60_000)
        album_assets = collect_album_assets(page, timeout_ms=8_000)
        print("Links encontrados:")
        print(" - Sem marca d'agua HD:", links.get("hd", "(nao encontrado)"))
        print(" - Sem marca d'agua:", links.get("nowm", "(nao encontrado)"))
        print(" - Download MP3:", links.get("mp3", "(nao encontrado)"))
        print(" - Download slide:", links.get("slide", "(nao encontrado)"))
        print(" - Total de imagens detectadas no album:", len(album_assets["image_urls"]))

        chosen_kind = (
            "nowm"
            if "nowm" in links
            else "hd"
            if "hd" in links
            else "mp3"
            if "mp3" in links
            else "slide"
        )
        href = links.get(chosen_kind)
        if chosen_kind == "slide":
            album_urls = album_assets["image_urls"] or album_assets["slide_links"]
            if not album_urls and links.get("slide"):
                album_urls = [links["slide"]]
            if not album_urls:
                raise RuntimeError("Conteudo de album detectado, mas nenhuma imagem foi encontrada.")
            href = album_urls[0]
        print("Link escolhido:", href)

        if chosen_kind == "slide":
            for index, album_url in enumerate(album_urls, start=1):
                ext = infer_image_extension(album_url)
                name = f"tiktok_slide_{index:02d}{ext}" if len(album_urls) > 1 else f"tiktok_slide{ext}"
                out = pathlib.Path("downloads") / name
                download_file(album_url, out)
                print(f"Imagem {index}/{len(album_urls)} salva em:", out.resolve())
        elif chosen_kind == "mp3":
            out = pathlib.Path("downloads") / "tiktok.mp3"
            download_file(href, out)
            print("Salvo em:", out.resolve())
        elif chosen_kind == "hd":
            out = pathlib.Path("downloads") / "tiktok_hd.mp4"
            download_file(href, out)
            print("Salvo em:", out.resolve())
        else:
            out = pathlib.Path("downloads") / "tiktok.mp4"
            download_file(href, out)
            print("Salvo em:", out.resolve())

        browser.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa video ou imagens de post TikTok via ssstik.io."
    )
    parser.add_argument("url", help="URL do post TikTok (video/photo; use aspas se tiver '&').")
    return parser.parse_args(argv)

if __name__ == "__main__":
    try:
        args = parse_args(sys.argv[1:])
        main(args.url, headless=True)
    except Exception as e:
        print(f"Erro: {e}")
        print(
            "Exemplo correto:\n"
            "python3 main.py "
            "\"https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc\""
        )
        sys.exit(1)
