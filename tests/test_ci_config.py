"""CI, packaging and docs agree with each other."""

from __future__ import annotations

import json
import re
from importlib.metadata import metadata, version
from pathlib import Path

from custom_components.caltrans_roads.const import DOMAIN, VERSION

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / DOMAIN


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^([A-Za-z0-9_.-]+)==([^\s#]+)", line.strip()):
            pins[m.group(1).lower()] = m.group(2)
    return pins


def test_ruff_version_lives_only_in_requirements():
    # pre-commit runs the ruff installed from requirements.txt (a local hook),
    # so Dependabot's single-file bump is complete and nothing can drift.
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "ruff-pre-commit" not in config
    assert re.search(r"^\s*entry:\s*ruff check", config, re.M)
    assert re.search(r"^\s*entry:\s*ruff format", config, re.M)
    assert "ruff" in _pins(ROOT / "requirements.txt")


def test_test_requirements_pin_a_matching_ha_pair():
    pins = _pins(ROOT / "requirements_test.txt")
    assert "pytest-homeassistant-custom-component" in pins
    assert "homeassistant" in pins


def test_ci_python_satisfies_home_assistant():
    workflow = (ROOT / ".github" / "workflows" / "tests.yaml").read_text(encoding="utf-8")
    m = re.search(r"DEFAULT_PYTHON:\s*\"?(\d+)\.(\d+)", workflow)
    assert m is not None
    needs = re.match(r">=\s*(\d+)\.(\d+)", metadata("homeassistant")["Requires-Python"])
    assert (int(m.group(1)), int(m.group(2))) >= (int(needs.group(1)), int(needs.group(2)))


def test_versions_agree():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert manifest["domain"] == DOMAIN
    assert f"## {VERSION}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_minimum_home_assistant_is_stated_everywhere():
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    minimum = hacs["homeassistant"]
    assert re.search(r"^homeassistant>=" + re.escape(minimum) + r"\s*$", (ROOT / "requirements.txt").read_text(), re.M)
    assert f"Home Assistant {minimum} or newer" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert tuple(map(int, minimum.split("."))) <= tuple(
        int(p) for p in re.findall(r"\d+", version("homeassistant"))[:3]
    )


def test_brand_icons_exist():
    from PIL import Image

    with Image.open(COMPONENT / "brand" / "icon.png") as icon:
        assert icon.size == (256, 256)
    with Image.open(COMPONENT / "brand" / "icon@2x.png") as icon:
        assert icon.size == (512, 512)


def test_readme_says_unofficial():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "unofficial" in readme
    assert "not affiliated with or endorsed by caltrans" in readme


def test_every_translation_key_used_exists():
    strings = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
    code = "".join(p.read_text(encoding="utf-8") for p in COMPONENT.glob("*.py"))
    for key in re.findall(r'translation_key="(\w+)"', code):
        found = (
            any(key in strings["entity"].get(platform, {}) for platform in strings["entity"])
            or key in strings.get("selector", {})
            or key in strings.get("issues", {})
        )
        assert found, key
