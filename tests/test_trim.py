import numpy as np

from voice_assistant.session import trim_leading_silence

SR = 16000


def tone(seconds, amp=0.1):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_long_silence_is_cut_but_pre_roll_kept():
    rng = np.random.default_rng(0)
    quiet = (0.001 * rng.standard_normal(SR * 6)).astype(np.float32)  # 6 s of room noise
    x = np.concatenate([quiet, tone(2.0), quiet[: SR // 2]])
    y = trim_leading_silence(x)
    assert len(x) - len(y) >= SR * 5  # most of the silence is gone
    assert len(x) - len(y) <= SR * 6 - int(0.4 * SR)  # about half a second before the speech is kept
    assert abs(y).max() > 0.05 and len(y) >= SR * 2  # the speech itself is intact


def test_speech_at_the_very_start_is_untouched():
    x = np.concatenate([tone(2.0), np.zeros(SR // 2, np.float32)])
    assert len(trim_leading_silence(x)) == len(x)


def test_all_quiet_and_tiny_inputs_are_returned_unchanged():
    quiet = np.zeros(SR * 3, np.float32)
    assert len(trim_leading_silence(quiet)) == len(quiet)
    tiny = np.ones(100, np.float32)
    assert trim_leading_silence(tiny) is tiny
