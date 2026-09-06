"""WS25 A10 (D80): every package under src/ ships in the wheel.

Tests import from src/ via conftest and containers COPY src/, so a package
missing from hatch's explicit list is invisible until someone installs the
wheel. The list must name every directory under src/ that has an __init__.py.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_package_list_covers_every_src_package():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    listed = set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])
    on_disk = {f"src/{p.name}" for p in (ROOT / "src").iterdir() if (p / "__init__.py").is_file()}
    missing = sorted(on_disk - listed)
    assert not missing, f"packages under src/ not in the wheel list: {missing}"
