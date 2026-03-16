import re
from pathlib import Path
from typing import Dict, List

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
HTML_EXTENSIONS = {".html", ".htm"}


def list_conferencia_reports(base_dir: str, max_reports: int = 500) -> Dict:
    base_path = Path(base_dir)
    if not base_path.exists() or not base_path.is_dir():
        return {
            "base_dir": str(base_path),
            "exists": False,
            "reports": [],
            "total": 0,
        }

    html_files: List[Path] = [
        path for path in base_path.iterdir() if path.is_file() and path.suffix.lower() in HTML_EXTENSIONS
    ]
    html_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    reports = []
    for html_file in html_files[: max_reports if max_reports > 0 else None]:
        image_folder = html_file.with_suffix("")
        image_folder = image_folder.parent / f"{image_folder.name}.files"

        images = []
        if image_folder.exists() and image_folder.is_dir():
            images = sorted(
                [
                    img.name
                    for img in image_folder.iterdir()
                    if img.is_file() and img.suffix.lower() in IMAGE_EXTENSIONS
                ]
            )

        reports.append(
            {
                "file_name": html_file.name,
                "file_path": str(html_file),
                "modified_at": int(html_file.stat().st_mtime),
                "image_folder": str(image_folder),
                "image_folder_exists": image_folder.exists() and image_folder.is_dir(),
                "images_count": len(images),
                "images": images,
            }
        )

    return {
        "base_dir": str(base_path),
        "exists": True,
        "reports": reports,
        "total": len(reports),
    }


def _resolve_report_path(base_dir: str, file_name: str) -> Path:
    base_path = Path(base_dir).resolve()
    report_path = (base_path / file_name).resolve()
    if base_path not in report_path.parents:
        raise ValueError("Arquivo de relatorio invalido.")
    if not report_path.exists() or not report_path.is_file():
        raise FileNotFoundError("Relatorio nao encontrado.")
    if report_path.suffix.lower() not in HTML_EXTENSIONS:
        raise ValueError("Arquivo precisa ser HTML.")
    return report_path


def _extract_all(pattern: str, html: str) -> List[str]:
    return [re.sub(r"\s+", " ", m).strip() for m in re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL)]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_image_name_from_block(block: str) -> str:
    tags = re.findall(r'(<img\s+[^>]*src="([^"]+\.files\/[^\"]+\.(?:png|jpg|jpeg|bmp|gif|webp))"[^>]*>)', block, flags=re.IGNORECASE | re.DOTALL)
    if not tags:
        return ""

    # First pass: keep probable piece images and ignore report logos.
    for tag_html, src in tags:
        image_name = src.rsplit("/", 1)[-1]
        lower = image_name.lower()
        if lower in {"img0.png", "img1.png"}:
            continue

        width_m = re.search(r'width="(\d+)"', tag_html, flags=re.IGNORECASE)
        height_m = re.search(r'height="(\d+)"', tag_html, flags=re.IGNORECASE)
        width = int(width_m.group(1)) if width_m else None
        height = int(height_m.group(1)) if height_m else None

        if width is not None and height is not None:
            if 80 <= width <= 160 and 35 <= height <= 120:
                return image_name

    # Fallback: first non-logo image in the block.
    for _, src in tags:
        image_name = src.rsplit("/", 1)[-1]
        if image_name.lower() not in {"img0.png", "img1.png"}:
            return image_name
    return ""


def _extract_items_by_block(html: str) -> List[Dict]:
    part_matches = list(
        re.finditer(r'class="s13">\s*([A-Z0-9]{2}-[A-Z0-9-]+)\s*<\/td>', html, flags=re.IGNORECASE | re.DOTALL)
    )
    if not part_matches:
        return []

    items = []
    for idx, part_match in enumerate(part_matches):
        start = part_match.start()
        next_start = part_matches[idx + 1].start() if idx + 1 < len(part_matches) else min(len(html), start + 14000)
        block = html[start:next_start]

        nome_peca = _normalize_text(part_match.group(1)) or f"PECA-{idx + 1}"

        os_match = re.search(r'class="s15">\s*(OS\s*[^<]+)\s*<\/td>', block, flags=re.IGNORECASE | re.DOTALL)
        os_value = _normalize_text(os_match.group(1)) if os_match else "---"

        cliente_values = [
            _normalize_text(c)
            for c in re.findall(r'class="s15">\s*([A-Z][A-Z0-9\s\-\.]{2,})\s*<\/td>', block, flags=re.IGNORECASE | re.DOTALL)
        ]
        cliente_values = [c for c in cliente_values if c and not c.upper().startswith("OS")]
        cliente_value = cliente_values[0] if cliente_values else "---"

        dim_a_match = re.search(r'class="s16">\s*([0-9\.,]+)\s*<\/td>', block, flags=re.IGNORECASE | re.DOTALL)
        dim_b_match = re.search(r'class="s17">\s*(X\s*[0-9\.,]+)\s*<\/td>', block, flags=re.IGNORECASE | re.DOTALL)
        dim_a = _normalize_text(dim_a_match.group(1)) if dim_a_match else ""
        dim_b = _normalize_text(dim_b_match.group(1)) if dim_b_match else ""
        dimensao = f"{dim_a} {dim_b}".strip() if (dim_a or dim_b) else "---"

        qtd_match = re.search(r'class="s13">\s*(\d+)\s*<\/td>', block, flags=re.IGNORECASE | re.DOTALL)
        qtd_esperada = int(qtd_match.group(1)) if qtd_match else 0

        image_name = _extract_image_name_from_block(block)

        items.append(
            {
                "index": idx,
                "nome_peca": nome_peca,
                "dimensao": dimensao,
                "os": os_value,
                "cliente": cliente_value,
                "qtd_esperada": qtd_esperada,
                "imagem": image_name,
            }
        )

    return items


def parse_conferencia_report(base_dir: str, file_name: str) -> Dict:
    report_path = _resolve_report_path(base_dir, file_name)
    html = report_path.read_text(encoding="utf-8", errors="ignore")
    items = _extract_items_by_block(html)

    image_folder = report_path.with_suffix("")
    image_folder = image_folder.parent / f"{image_folder.name}.files"

    return {
        "file_name": report_path.name,
        "file_path": str(report_path),
        "image_folder": str(image_folder),
        "items": items,
        "total_items": len(items),
    }


def validate_blind_conference(report_data: Dict, contagens: Dict[str, int]) -> Dict:
    divergencias = []
    ok = []
    pendentes = []

    for item in report_data.get("items", []):
        idx_key = str(item.get("index"))
        qtd_esperada = int(item.get("qtd_esperada", 0) or 0)

        has_count = idx_key in contagens and str(contagens.get(idx_key)).strip() != ""
        qtd_auditada = int(contagens.get(idx_key, 0) or 0) if has_count else None

        entry = {
            "index": item.get("index"),
            "nome_peca": item.get("nome_peca"),
            "os": item.get("os"),
            "cliente": item.get("cliente"),
            "qtd_esperada": qtd_esperada,
            "qtd_auditada": qtd_auditada,
        }

        if not has_count:
            pendentes.append(entry)
            continue

        if qtd_auditada == qtd_esperada:
            ok.append(entry)
        else:
            divergencias.append(entry)

    return {
        "total_itens": len(report_data.get("items", [])),
        "total_ok": len(ok),
        "total_divergencias": len(divergencias),
        "total_pendentes": len(pendentes),
        "ok": ok,
        "divergencias": divergencias,
        "pendentes": pendentes,
    }


def resolve_report_image_path(base_dir: str, file_name: str, image_name: str) -> Path:
    report_path = _resolve_report_path(base_dir, file_name)
    safe_name = Path(image_name).name
    if not safe_name:
        raise ValueError("Imagem invalida.")

    image_folder = report_path.with_suffix("")
    image_folder = image_folder.parent / f"{image_folder.name}.files"
    image_path = (image_folder / safe_name).resolve()

    if image_folder.resolve() not in image_path.parents and image_path != image_folder.resolve():
        raise ValueError("Imagem invalida.")
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError("Imagem nao encontrada.")
    return image_path
