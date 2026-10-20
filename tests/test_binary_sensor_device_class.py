"""
Issue #63: motion sensors had no device_class.

ikea_motion_sensor did not override device_class, so it fell through to
ikea_base_device_sensor, which returns None when no device_class is passed.
Home Assistant then showed the entity as a generic on/off sensor, and the
purpose-specific motion.detected / motion.cleared triggers did not match it.

Each binary sensor class must report its own device class.

homeassistant is not installed here, so the classes are read out of the source
with ast rather than imported.
"""
import ast
import os

SOURCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "dirigera_platform", "base_classes.py",
)
with open(SOURCE_PATH) as fh:
    TREE = ast.parse(fh.read())

EXPECTED = {
    "ikea_motion_sensor":     "MOTION",
    "ikea_open_close_sensor": "WINDOW",
    "ikea_water_sensor":      "MOISTURE",
}


def _class(name):
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("class %s not found in base_classes.py" % name)


def _device_class(cls_node):
    """The BinarySensorDeviceClass member the device_class property returns."""
    for stmt in cls_node.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "device_class":
            for node in ast.walk(stmt):
                if (isinstance(node, ast.Return)
                        and isinstance(node.value, ast.Attribute)
                        and getattr(node.value.value, "id", None) == "BinarySensorDeviceClass"):
                    return node.value.attr
            raise AssertionError(
                "%s.device_class does not return a BinarySensorDeviceClass" % cls_node.name)
    return None


def test_binary_sensors_report_their_device_class():
    for name, expected in EXPECTED.items():
        assert _device_class(_class(name)) == expected, name
