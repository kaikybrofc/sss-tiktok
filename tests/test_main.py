import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_normalize_text_removes_accents_and_spaces():
    value = "  Sem   marca d'água  "
    assert main.normalize_text(value) == "sem marca d'agua"


def test_classify_without_watermark_button():
    kind = main.classify_ssstik_button(
        text="Sem marca d'água",
        class_name=(
            "pure-button pure-button-primary is-center u-bl dl-button "
            "download_link without_watermark vignette_active notranslate"
        ),
        href="https://tikcdn.io/ssstik/123?st=abc",
    )
    assert kind == "nowm"


def test_classify_mp3_button():
    kind = main.classify_ssstik_button(
        text="Download MP3",
        class_name="pure-button download_link music",
        href="https://tikcdn.io/ssstik/m/encoded",
    )
    assert kind == "mp3"


def test_classify_slide_button():
    kind = main.classify_ssstik_button(
        text="Download this slide",
        class_name="pure-button download_link slide",
        href="https://tikcdn.io/ssstik/aHR0cHM6Ly9leGFtcGxlLmpwZw==",
    )
    assert kind == "slide"
