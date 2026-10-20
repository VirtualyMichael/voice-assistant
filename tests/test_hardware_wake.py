from pathlib import Path

import pytest

from voice_assistant.hardware import recommend, tier
from voice_assistant.models import ModelsMissing, write_keywords


@pytest.mark.parametrize("vram,expected", [(6, "under 8 GB"), (8, "8-12 GB"), (12, "12-20 GB"), (24, "20+ GB")])
def test_tiers(vram, expected):
    assert tier(vram) == expected


def test_recommend_respects_reserve():
    assert "12-14B" in recommend(16, 4)
    assert "26-35B" in recommend(24, 4)
    assert "7-9B" in recommend(24, 17)
    assert "cloud" in recommend(8, 7)


def test_write_keywords_needs_bpe(tmp_path):
    with pytest.raises(ModelsMissing):
        write_keywords(tmp_path, "hey assistant")


def test_write_keywords_with_real_model():
    bpe = Path.home() / "AppData/Roaming/VoiceAssistant/models/kws/bpe.model"
    if not bpe.exists():
        pytest.skip("KWS model not downloaded")
    out = write_keywords(bpe.parent, "Hey  Assistant")
    line = out.read_text(encoding="utf-8").strip()
    assert line.startswith("▁HE Y ▁AS") and line.endswith("@HEY_ASSISTANT")


def test_one_word_wake_phrase_gets_hey_and_ok_variants():
    from voice_assistant.models import wake_variants
    assert wake_variants("Jarvis") == ["JARVIS", "HEY JARVIS", "OK JARVIS"]
    assert wake_variants("hey  assistant") == ["HEY ASSISTANT"]
