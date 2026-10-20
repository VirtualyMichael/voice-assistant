import json

import httpx

from voice_assistant.config import Config
from voice_assistant.smart_home import GoveeLan, HomeAssistant, build_home_tools, parse_color


def test_parse_color():
    assert parse_color("Red") == ((255, 0, 0), None)
    assert parse_color("#ff8800") == ((255, 136, 0), None)
    assert parse_color("10, 20, 30") == ((10, 20, 30), None)
    assert parse_color("warm white") == (None, 2700)
    assert parse_color("3000K") == (None, 3000)
    assert parse_color("plaid") == (None, None)


def fake_govee():
    g = GoveeLan({"10.0.0.21": "desk lamp"})
    g.devices = {"10.0.0.21": {"sku": "H6008", "device": "AA:BB:CC:DD:EE:FF"}, "10.0.0.22": {"sku": "H6159", "device": "11:22:33:44:55:66"}}
    g.sent = []
    g.send = lambda ip, cmd, data: g.sent.append((ip, cmd, data))
    return g


def test_govee_commands():
    g = fake_govee()
    assert g.pick("desk") == ["10.0.0.21"] and len(g.pick("all")) == 2 and g.pick("garage") == []
    g.apply(["10.0.0.21"], None, 150, "blue")
    assert ("10.0.0.21", "colorwc", {"color": {"r": 0, "g": 0, "b": 255}, "colorTemInKelvin": 0}) in g.sent
    assert g.sent[-1] == ("10.0.0.21", "brightness", {"value": 100})  # clamped
    assert g.apply(["10.0.0.21"], None, None, "plaid").startswith("Error")


def test_tools_registered_by_config():
    cfg = Config(govee_enabled=True)
    names = [t.name for t in build_home_tools(cfg, lambda p: True, fake_govee())]
    assert names == ["list_lights", "set_lights"]
    assert build_home_tools(Config(), lambda p: True) == []


def test_home_assistant_list_and_call():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.content))
        if req.url.path == "/api/states":
            return httpx.Response(200, json=[{"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk"}},
                                             {"entity_id": "sun.sun", "state": "up", "attributes": {}}])
        return httpx.Response(200, json=[])

    ha = HomeAssistant("http://ha:8123/", "t", httpx.Client(transport=httpx.MockTransport(handler)))
    assert ha.list() == "light.desk (Desk) = on"
    assert "Called light.turn_on" in ha.call("light", "turn_on", "light.desk", {"brightness_pct": 40})
    assert json.loads(seen[-1][2]) == {"entity_id": "light.desk", "brightness_pct": 40}


def test_ha_locks_ask_first():
    asked = []
    cfg = Config(ha_url="http://ha")
    ha = HomeAssistant("http://ha", "t", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))))
    tools = {t.name: t for t in build_home_tools(cfg, lambda p: asked.append(p) or False, None, ha)}
    assert "declined" in tools["ha_call"].handler("lock", "unlock", "lock.front")
    assert asked
