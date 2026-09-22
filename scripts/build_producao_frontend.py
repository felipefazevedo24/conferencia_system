"""Compila o painel React e publica seus assets no bundle do Sync."""
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend_producao"
DESTINATION = ROOT / "static" / "producao_original"


def build() -> None:
    subprocess.run(["npm.cmd", "run", "build"], cwd=FRONTEND, check=True)
    built = FRONTEND / "dist"
    html = (built / "index.html").read_text(encoding="utf-8")
    script = re.search(r'/assets/(index-[^" ]+\.js)', html)
    stylesheet = re.search(r'/assets/(index-[^" ]+\.css)', html)
    if not script or not stylesheet:
        raise RuntimeError("Vite não gerou os assets esperados do painel.")

    for asset in (script.group(1), script.group(1) + ".map", stylesheet.group(1)):
        shutil.copy2(built / "assets" / asset, DESTINATION / "assets" / asset)

    index_path = DESTINATION / "index.html"
    index = index_path.read_text(encoding="utf-8")
    index = re.sub(r'/producao-original/assets/index-[^" ]+\.js',
                   f'/producao-original/assets/{script.group(1)}', index)
    index = re.sub(r'/producao-original/assets/index-[^" ]+\.css',
                   f'/producao-original/assets/{stylesheet.group(1)}', index)
    index_path.write_text(index, encoding="utf-8")


if __name__ == "__main__":
    build()
