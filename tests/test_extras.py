import json

import httpx

from voice_assistant.config import Config
from voice_assistant.extras import Notes, build_extra_tools, weather


def tools(cfg=None, keys=None, calls=None, notes=None):
    cfg = cfg or Config()
    ts = build_extra_tools(cfg, lambda p: True, (lambda n, a: calls.append((n, a)) or "ok") if calls is not None else (lambda n, a: "ok"),
                           notes, keys or (lambda k, n=1: None))
    return {t.name: t.handler for t in ts}


def test_media_and_volume_keys():
    pressed = []
    t = tools(keys=lambda k, n=1: pressed.append((k, n)))
    t["media_control"]("play_pause")
    t["set_volume"]("up")
    t["set_volume"]("", 40)
    assert pressed == [("play_pause", 1), ("volume_up", 5), ("volume_down", 50), ("volume_up", 20)]
    assert t["media_control"]("explode").startswith("Error")


def test_notes_roundtrip(tmp_path):
    t = tools(notes=Notes(tmp_path / "n.json"))
    assert "#1" in t["remember_note"]("wifi code is blue")
    assert "wifi code" in t["list_notes"]("wifi")
    assert t["list_notes"]("zzz") == "No matching notes."
    assert t["delete_note"](1).startswith("Deleted") and t["delete_note"](1) == "No such note."


def test_routine_runs_steps_and_skips_nesting():
    calls = []
    cfg = Config(routines={"good night": [{"tool": "set_lights", "args": {"on": False}}, {"tool": "run_routine", "args": {}}]}, confirm_routines=False)
    t = tools(cfg, calls=calls)
    t["run_routine"]("Good Night")
    assert calls == [("set_lights", json.dumps({"on": False}))]
    assert "run_routine" not in tools()


def test_weather_parses_open_meteo():
    def handler(req):
        if "geocoding" in str(req.url):
            return httpx.Response(200, json={"results": [{"name": "Austin", "latitude": 30.2, "longitude": -97.7}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 71.6, "weather_code": 2, "wind_speed_10m": 6.2},
                                         "daily": {"temperature_2m_max": [80.1], "temperature_2m_min": [62.4], "precipitation_probability_max": [10]}})

    out = weather("Austin", httpx.Client(transport=httpx.MockTransport(handler)))
    assert out.startswith("Austin: 72") and "partly cloudy" in out and "62 to 80" in out
