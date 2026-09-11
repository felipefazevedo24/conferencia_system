from __future__ import annotations

import io
import base64
import json
import math
import subprocess
import sys
import time
import warnings
from pathlib import Path
from statistics import median

import pymupdf as fitz
from PIL import Image, ImageChops, ImageFilter

if __package__:
    from .producao_documentos import DocumentError, normalize
else:
    from producao_documentos import DocumentError, normalize


def isolated_render(content: bytes, filename: str, settings: dict, description: str) -> dict[str, bytes]:
    header = json.dumps({"filename": filename, "settings": settings, "description": description}).encode() + b"\n"
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            input=header + content, capture_output=True, timeout=settings["timeout"], check=True,
        )
        payload = json.loads(process.stdout)
        if "error" in payload:
            raise DocumentError(payload["error"], payload["status"])
        return {variant: base64.b64decode(binary, validate=True) for variant, binary in payload.items()}
    except subprocess.TimeoutExpired:
        raise DocumentError("Tempo limite da miniatura excedido", 503) from None
    except (subprocess.SubprocessError, ValueError, OSError):
        raise DocumentError("Renderizador indisponivel", 503) from None


def candidates_for_page(page) -> list:
    area = page.rect.width * page.rect.height
    groups = []
    for drawing in page.get_drawings():
        rectangle = fitz.Rect(drawing["rect"]) & page.rect
        if rectangle.width * rectangle.height > area * 0.82:
            continue
        rectangle = (rectangle + (-1, -1, 1, 1)) & page.rect
        directions = set()
        count = curves = 0
        for item in drawing["items"]:
            if item[0] == "l":
                delta_x, delta_y = item[2].x - item[1].x, item[2].y - item[1].y
                if math.hypot(delta_x, delta_y) > 2:
                    directions.add(round((math.degrees(math.atan2(delta_y, delta_x)) % 180) / 10))
                    count += 1
            elif item[0] in {"c", "qu"}:
                curves += 1
            elif item[0] == "re":
                count += 4
                directions.update((0, 9))
        group = {"rect": rectangle, "count": count, "curves": curves, "directions": directions}
        matches = [other for other in groups if (other["rect"] + (-5, -5, 5, 5)).intersects(rectangle)]
        for other in matches:
            group["rect"] |= other["rect"]
            group["count"] += other["count"]
            group["curves"] += other["curves"]
            group["directions"] |= other["directions"]
            groups.remove(other)
        groups.append(group)
    text_blocks = page.get_text("blocks")
    candidates = []
    for group in groups:
        rectangle = group["rect"]
        coverage = rectangle.width * rectangle.height / area
        if not 0.001 <= coverage <= 0.72:
            continue
        families = len(group["directions"])
        if group["count"] < 4 and group["curves"] < 2:
            continue
        if families < 2 and group["curves"] < 2:
            continue
        text_count = sum(len(str(block[4]).split()) for block in text_blocks if rectangle.intersects(fitz.Rect(block[:4])))
        if text_count > 12:
            continue
        score = families * 2 + min(coverage, 0.35) * 3 + min(group["curves"], 4) - text_count * 0.5
        if rectangle.y0 > page.rect.height * 0.72 and rectangle.x0 > page.rect.width * 0.55:
            score -= 6
        margin = max(rectangle.width, rectangle.height) * 0.04 + 3
        candidates.append((score, (rectangle + (-margin, -margin, margin, margin)) & page.rect))
    return candidates


def options(config=None) -> dict:
    config = config or {}
    crop = config.get("PRODUCAO_THUMBNAIL_CROP", "0,0,1,1")
    try:
        crop = tuple(float(value) for value in (crop.split(",") if isinstance(crop, str) else crop))
        dpi = int(config.get("PRODUCAO_THUMBNAIL_DPI", 180))
        pixels = int(config.get("PRODUCAO_MAX_PIXELS", 24_000_000))
        timeout = float(config.get("PRODUCAO_RENDER_TIMEOUT", 20))
        if len(crop) != 4 or not all(0 <= value <= 1 for value in crop):
            raise ValueError
        if crop[0] >= crop[2] or crop[1] >= crop[3] or not 72 <= dpi <= 600:
            raise ValueError
        if not 1 <= pixels <= 40_000_000 or not 0 < timeout <= 120:
            raise ValueError
    except (TypeError, ValueError):
        raise DocumentError("Configuracao de miniatura invalida", 503) from None
    return {"crop": crop, "dpi": dpi, "pixels": pixels, "timeout": timeout}


def check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise DocumentError("Tempo limite da miniatura excedido", 503)


def check_pixels(width: float, height: float, limit: int) -> None:
    if not math.isfinite(width * height) or width <= 0 or height <= 0 or width * height > limit:
        raise DocumentError("Imagem acima do limite de pixels", 413)


def _trim(image: Image.Image, mask: Image.Image, padding: int) -> Image.Image:
    bounds = mask.getbbox()
    if bounds is None:
        raise DocumentError("Vista ausente")
    return image.crop((max(0, bounds[0] - padding), max(0, bounds[1] - padding), min(image.width, bounds[2] + padding), min(image.height, bounds[3] + padding)))


def transparent_part(image: Image.Image, deadline: float) -> Image.Image:
    source = image.convert("RGB")
    difference = ImageChops.difference(source, Image.new("RGB", source.size, "white")).convert("L")
    mask = difference.point(lambda value: 255 if value > 18 else 0)
    if mask.getbbox():
        source = _trim(source, mask, 18)
    largest = max(source.size)
    if 240 <= largest < 1200:
        scale = min(3, 1200 / largest)
        source = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
    source.thumbnail((1800, 1200), Image.Resampling.LANCZOS)
    border = []
    for horizontal in range(0, source.width, max(1, source.width // 160)):
        border.extend((source.getpixel((horizontal, 0)), source.getpixel((horizontal, source.height - 1))))
    for vertical in range(0, source.height, max(1, source.height // 160)):
        border.extend((source.getpixel((0, vertical)), source.getpixel((source.width - 1, vertical))))
    background = tuple(int(median(pixel[channel] for pixel in border)) for channel in range(3))
    pixels = source.tobytes()
    result = bytearray(source.width * source.height * 4)
    for offset in range(0, len(pixels), 3):
        if offset % (source.width * 3) == 0:
            check_time(deadline)
        color = pixels[offset:offset + 3]
        distance = max(abs(color[channel] - background[channel]) for channel in range(3))
        alpha = min(255, max(0, round((distance - 2) * 255 / 28)))
        target = offset // 3 * 4
        for channel in range(3):
            result[target + channel] = min(255, max(0, round((color[channel] * 255 - background[channel] * (255 - alpha)) / alpha))) if alpha else 0
        result[target + 3] = alpha
    transparent = Image.frombytes("RGBA", source.size, bytes(result))
    alpha = transparent.getchannel("A")
    if alpha.getbbox() is None and min(background) < 237:
        preserved = Image.new("RGBA", (source.width + 16, source.height + 16))
        preserved.paste(source.convert("RGBA"), (8, 8))
        return preserved
    sharpened = transparent.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.1, percent=130, threshold=3))
    sharpened.putalpha(alpha)
    trimmed = _trim(sharpened, alpha.point(lambda value: 255 if value > 2 else 0), 8)
    if trimmed.width / trimmed.height >= 4:
        height = max(trimmed.height, math.ceil(trimmed.width / 14))
        padded = Image.new("RGBA", (trimmed.width, height))
        padded.paste(trimmed, (0, (height - trimmed.height) // 2))
        return padded
    return trimmed


def validate_png(content: bytes) -> None:
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.mode != "RGBA":
            raise DocumentError("Miniatura invalida")
        image.load()
        if not image.getchannel("A").getbbox():
            raise DocumentError("Vista ausente")


def _pdf_image(content: bytes, settings: dict, description: str, deadline: float) -> Image.Image:
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if pdf.needs_pass:
            raise DocumentError("PDF protegido")
        if not pdf.page_count:
            raise DocumentError("Documento sem paginas")
        page = pdf.load_page(0)
        scale = max(300, settings["dpi"]) / 72
        check_pixels(math.ceil(page.rect.width * scale), math.ceil(page.rect.height * scale), settings["pixels"])
        placed_images = page.get_image_info()
        for image_info in placed_images:
            check_pixels(image_info["width"], image_info["height"], settings["pixels"])
        candidates = candidates_for_page(page)
        check_time(deadline)
        labels = []
        for block in page.get_text("blocks"):
            text = normalize(block[4])
            if any(label in text for label in ("VISTA ISOMETRICA", "ISOMETRIC", "VISTA 3D", "WIDOK IZOMETRYCZNY")) or text.startswith("WIDOK"):
                labels.append(fitz.Rect(block[:4]))
        clip = None
        labeled = []
        for score, rectangle in candidates:
            for label in labels:
                overlap = min(rectangle.x1, label.x1) - max(rectangle.x0, label.x0)
                gap = max(0, label.y0 - rectangle.y1, rectangle.y0 - label.y1)
                if overlap > 0 and gap < page.rect.height * 0.18:
                    labeled.append((gap, -score, rectangle))
        if labeled:
            clip = min(labeled, key=lambda candidate: candidate[:2])[2]
        approval = "aprova" in description.lower()
        if clip is None and not approval:
            placed = [fitz.Rect(image["bbox"]) & page.rect for image in placed_images]
            placed = [rectangle for rectangle in placed if rectangle.width * rectangle.height > page.rect.width * page.rect.height * 0.06]
            clip = max(placed, key=lambda rectangle: rectangle.width * rectangle.height, default=None)
        if clip is None and candidates:
            clip = max(candidates, key=lambda candidate: candidate[1].width * candidate[1].height if approval else candidate[0])[1]
        if clip is None:
            left, top, right, bottom = settings["crop"]
            clip = fitz.Rect(page.rect.width * left, page.rect.height * top, page.rect.width * right, page.rect.height * bottom)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False, colorspace=fitz.csRGB)
        check_time(deadline)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def render(content: bytes, filename: str, settings: dict | None = None, description: str = "") -> dict[str, bytes]:
    settings = settings or options()
    deadline = time.monotonic() + settings["timeout"]
    try:
        if filename.lower().endswith(".pdf"):
            if not content.startswith(b"%PDF-"):
                raise DocumentError("Documento PDF invalido")
            image = _pdf_image(content, settings, description, deadline)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as original:
                    if original.format not in {"PNG", "JPEG", "BMP", "WEBP", "TIFF"}:
                        raise DocumentError("Formato sem suporte", 415)
                    check_pixels(original.width, original.height, settings["pixels"])
                    original.seek(0)
                    rgba = original.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
        check_time(deadline)
        result = transparent_part(image, deadline)
        previews = {}
        for variant, size in (("thumbnail", (720, 480)), ("detail", (1800, 1200))):
            resized = result.copy()
            resized.thumbnail(size, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            resized.save(output, format="PNG")
            previews[variant] = output.getvalue()
            validate_png(previews[variant])
        return previews
    except DocumentError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DocumentError("Imagem acima do limite de pixels", 413) from None
    except Exception:
        raise DocumentError("Documento invalido ou ilegivel") from None


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.readline())
        previews = render(sys.stdin.buffer.read(), request["filename"], request["settings"], request["description"])
        response = {variant: base64.b64encode(binary).decode("ascii") for variant, binary in previews.items()}
    except DocumentError as error:
        response = {"error": str(error), "status": error.status}
    except Exception:
        response = {"error": "Renderizador indisponivel", "status": 503}
    sys.stdout.write(json.dumps(response))