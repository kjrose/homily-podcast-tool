"""Build a portable plugin ZIP containing only reviewed source files."""

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "homily-studio"
SOURCE = ROOT / "homily_monitor" / "editorial-catalog.json"
FILES = ["homily-studio.php", "includes/class-hs-profile.php", "includes/class-hs-rest.php",
         "assets/admin.js", "assets/admin.css", "editorial-catalog.json", "readme.txt", "LICENSE"]


def package():
    if json.loads(SOURCE.read_text(encoding="utf-8")) != json.loads((PLUGIN / "editorial-catalog.json").read_text(encoding="utf-8")):
        raise ValueError("The Python and plugin catalogues differ; synchronize them before packaging")
    output = ROOT / "dist" / "homily-studio.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(PLUGIN / name, "homily-studio/" + name)
    print("Plugin package: dist/homily-studio.zip")


if __name__ == "__main__":
    package()
