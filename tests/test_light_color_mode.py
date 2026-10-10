"""
Issue #50: a dual-mode bulb must report the hub's colour mode after a restart.

The hub sends "colorMode": "temperature" | "color" on every light. Before this
fix the library model dropped the key, and set_state() picked hs for every
bulb that supports hs. A bulb the hub held in colour-temperature mode then
reported color_mode hs and color_temp_kelvin None until its next colour event.

Two things are guarded here.

1. The model. HubX.get_lights() and get_light_by_id() must build LightX, and
   LightX must keep colorMode as attributes.color_mode. ikea_gateway calls the
   first at startup and async_update() calls the second, so both are tested.

2. set_state(). It must follow color_mode when present, and keep the old
   hs-first rule when the field is absent. A light from runtime discovery is
   still a plain library Light with no color_mode field, so the fallback is
   load-bearing, not dead code.

As in test_matter_filtering.py, the code under test is lifted out of the
source with ast, so the real bodies run without importing homeassistant.
The model tests need the dirigera library and skip without it.
"""
import ast
import enum
import logging
import os
import sys
from types import SimpleNamespace

import pytest

COMPONENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "dirigera_platform",
)
PATCH = os.path.join(COMPONENT, "dirigera_lib_patch.py")
LIGHT = os.path.join(COMPONENT, "light.py")


def _parse(path):
    with open(path) as fh:
        return ast.parse(fh.read())


def _class(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _method(tree, class_name, name):
    nodes = [n for n in _class(tree, class_name).body
             if isinstance(n, ast.FunctionDef) and n.name == name]
    assert nodes, f"{class_name}.{name} not found"
    return nodes[0]


def _exec(nodes, path, namespace):
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace


# --- set_state() ------------------------------------------------------------

class ColorMode(enum.Enum):
    ONOFF = "onoff"
    BRIGHTNESS = "brightness"
    COLOR_TEMP = "color_temp"
    HS = "hs"


SET_STATE = _exec(
    [_method(_parse(LIGHT), "ikea_bulb", "set_state")],
    LIGHT,
    {"ColorMode": ColorMode, "logger": logging.getLogger(__name__)},
)["set_state"]

DUAL_MODE = ["isOn", "lightLevel", "colorTemperature", "colorHue", "colorSaturation"]
CT_ONLY = ["isOn", "lightLevel", "colorTemperature"]
HS_ONLY = ["isOn", "lightLevel", "colorHue", "colorSaturation"]


def _bulb(can_receive, **attributes):
    bulb = SimpleNamespace(_json_data=SimpleNamespace(
        capabilities=SimpleNamespace(can_receive=can_receive),
        attributes=SimpleNamespace(**attributes),
    ))
    SET_STATE(bulb)
    return bulb


def test_temperature_mode_is_reported_as_color_temp():
    assert _bulb(DUAL_MODE, color_mode="temperature")._color_mode == ColorMode.COLOR_TEMP


def test_color_mode_is_reported_as_hs():
    assert _bulb(DUAL_MODE, color_mode="color")._color_mode == ColorMode.HS


def test_plain_light_without_the_field_keeps_hs_first():
    # Runtime discovery still builds a library Light: no color_mode attribute.
    assert _bulb(DUAL_MODE)._color_mode == ColorMode.HS


def test_unknown_value_keeps_hs_first():
    assert _bulb(DUAL_MODE, color_mode=None)._color_mode == ColorMode.HS


def test_unrecognised_string_keeps_hs_first():
    assert _bulb(DUAL_MODE, color_mode="xy")._color_mode == ColorMode.HS


def test_color_on_a_ct_only_bulb_is_ignored():
    assert _bulb(CT_ONLY, color_mode="color")._color_mode == ColorMode.COLOR_TEMP


def test_temperature_on_an_hs_only_bulb_is_ignored():
    assert _bulb(HS_ONLY, color_mode="temperature")._color_mode == ColorMode.HS


def test_supported_modes_are_unchanged():
    bulb = _bulb(DUAL_MODE, color_mode="temperature")
    assert bulb._supported_color_modes == [ColorMode.COLOR_TEMP, ColorMode.HS]


# --- LightX and the HubX getters --------------------------------------------

def _device(color_mode):
    return {
        "id": "light-1", "type": "light", "deviceType": "light",
        "createdAt": "2026-01-01T00:00:00.000Z", "lastSeen": "2026-01-01T00:00:00.000Z",
        "isReachable": True, "customIcon": "lighting_bulb", "isHidden": False,
        "capabilities": {"canSend": [], "canReceive": DUAL_MODE},
        "room": None, "deviceSet": [], "remoteLinks": [],
        "attributes": {
            "customName": "Bulb", "model": "m", "manufacturer": "IKEA",
            "firmwareVersion": "1", "hardwareVersion": "1", "serialNumber": "1",
            "productCode": "p", "isOn": True, "lightLevel": 50,
            "colorTemperature": 2700, "colorTemperatureMin": 4000,
            "colorTemperatureMax": 2202, "colorHue": 30.0,
            "colorSaturation": 0.5, "colorMode": color_mode,
        },
    }


def _import_real_dirigera():
    """Import the real dirigera library, whatever the test order.

    test_reconnect_resync.py and test_registry_per_hub.py put a stub
    "dirigera" into sys.modules. Hide any stub during the import, then put
    the previous entries back, so those files keep their stubs.
    """
    def ours(name):
        return name == "dirigera" or name.startswith("dirigera.")

    saved = {name: sys.modules.pop(name) for name in list(sys.modules) if ours(name)}
    try:
        from dirigera.devices import light
        from dirigera.hub import abstract_smart_home_hub
        return light, abstract_smart_home_hub
    except ImportError:
        return None, None
    finally:
        for name in [name for name in sys.modules if ours(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


from typing import Any, Dict, List, Optional

_light_lib, _hub_lib = _import_real_dirigera()
if _light_lib is not None:
    Light = _light_lib.Light
    LightAttributes = _light_lib.LightAttributes
    dict_to_light = _light_lib.dict_to_light
    AbstractSmartHomeHub = _hub_lib.AbstractSmartHomeHub
    FakeDirigeraHub = _hub_lib.FakeDirigeraHub
else:
    FakeDirigeraHub = None

needs_dirigera = pytest.mark.skipif(FakeDirigeraHub is None, reason="dirigera not installed")


@pytest.fixture(scope="module")
def patch_ns():
    tree = _parse(PATCH)
    wanted = {"LightAttributesX", "LightX", "dict_to_light_x"}
    nodes = [n for n in tree.body
             if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    getters = [_method(tree, "HubX", "get_lights"), _method(tree, "HubX", "get_light_by_id")]
    ns = {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
          "Light": Light, "LightAttributes": LightAttributes,
          "AbstractSmartHomeHub": AbstractSmartHomeHub, "__name__": "lightx_test"}
    _exec(nodes, PATCH, ns)
    _exec(getters, PATCH, ns)
    return ns


def _hub(ns, *devices):
    """The library's fake hub, with the lifted HubX getters bound to it."""
    hub = FakeDirigeraHub()
    hub.get_action_replys["/devices"] = list(devices)
    for device in devices:
        hub.get_action_replys[f"/devices/{device['id']}"] = device
    hub._get_device_data_by_id = lambda id_: hub.get(f"/devices/{id_}")
    hub.get_lights = ns["get_lights"].__get__(hub)
    hub.get_light_by_id = ns["get_light_by_id"].__get__(hub)
    return hub


@needs_dirigera
@pytest.mark.parametrize("mode", ["temperature", "color"])
def test_get_lights_keeps_color_mode(patch_ns, mode):
    lights = _hub(patch_ns, _device(mode)).get_lights()
    assert isinstance(lights[0], patch_ns["LightX"])
    assert lights[0].attributes.color_mode == mode


@needs_dirigera
def test_get_light_by_id_keeps_color_mode(patch_ns):
    light = _hub(patch_ns, _device("temperature")).get_light_by_id("light-1")
    assert isinstance(light, patch_ns["LightX"])
    assert light.attributes.color_mode == "temperature"


@needs_dirigera
def test_get_light_by_id_rejects_other_types(patch_ns):
    other = dict(_device("temperature"), type="outlet")
    with pytest.raises(ValueError):
        _hub(patch_ns, other).get_light_by_id("light-1")


@needs_dirigera
def test_reload_stays_lightx(patch_ns):
    light = _hub(patch_ns, _device("color")).get_light_by_id("light-1")
    reloaded = light.reload()
    assert isinstance(reloaded, patch_ns["LightX"])
    assert reloaded.attributes.color_mode == "color"


@needs_dirigera
def test_color_mode_field_accepts_listener_writes(patch_ns):
    # hub_event_listener stores a live colorMode with a plain attribute write.
    light = _hub(patch_ns, _device("temperature")).get_light_by_id("light-1")
    light.attributes.color_mode = "color"
    assert light.attributes.color_mode == "color"


@needs_dirigera
def test_library_light_drops_the_key():
    # The reason LightX exists. If a future dirigera release keeps colorMode,
    # this fails and LightX may be removable.
    light = dict_to_light(_device("temperature"), FakeDirigeraHub())
    assert not hasattr(light.attributes, "color_mode")
