"""Pure image extraction shared in behavior with Estrutura; no database access.

The cutout helpers are ported from Estrutura/app/use_cases/documents.py.
Original documents remain in memory and no drawing content is logged.
"""
from __future__ import annotations

import io
import math
from typing import cast

import pymupdf as fitz
from PIL import Image, ImageChops, ImageDraw, ImageFilter


def render_variants(image: Image.Image) -> dict[str, bytes]:
    part = _transparent_part_thumbnail(_trim_white_space(image))
    rendered = {}
    for variant, maximum in (("thumbnail", 720), ("detail", 1600)):
        resized = part.copy()
        resized.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        resized.save(output, format="PNG", optimize=True)
        rendered[variant] = output.getvalue()
    return rendered


def _transparent_part_thumbnail(
    image: Image.Image,
    *,
    preserve_components: bool = False,
) -> Image.Image:
    """Isolate the dominant part and convert its background to clean RGBA."""
    source = image.convert("RGB")
    wide_view = source.width / max(1, source.height) >= 4.0
    if preserve_components:
        isolated = source
        selection = Image.new("L", source.size, 255)
    else:
        isolated, selection = _isolate_dominant_object(source)
    isolated, selection = _resize_for_high_resolution(isolated, selection)
    if selection.size != isolated.size:
        selection = selection.resize(isolated.size, Image.Resampling.LANCZOS)
    selection = selection.filter(ImageFilter.MaxFilter(3))
    transparent = _background_to_alpha(isolated, selection)
    alpha = transparent.getchannel("A")
    sharpened = transparent.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=1.1, percent=130, threshold=3)
    )
    sharpened.putalpha(alpha)
    trimmed = _trim_transparent_space(sharpened)
    if wide_view:
        return _pad_wide_line_view(_trim_wide_peripheral_annotations(trimmed))
    return trimmed


def _pad_wide_line_view(image: Image.Image) -> Image.Image:
    """Keep very long manufactured views legible without changing their scale."""
    target_height = max(image.height, math.ceil(image.width / 14))
    if target_height == image.height:
        return image
    padded = Image.new("RGBA", (image.width, target_height), (0, 0, 0, 0))
    padded.paste(image, (0, (target_height - image.height) // 2), image)
    return padded


def _trim_wide_peripheral_annotations(image: Image.Image) -> Image.Image:
    """Remove sparse dimensions around a long part while retaining all body lines."""
    alpha = image.getchannel("A")
    width, height = alpha.size
    dense_rows = [
        y
        for y in range(height)
        if sum(_mask_value(alpha, x, y) >= 24 for x in range(width)) >= width * 0.55
    ]
    if len(dense_rows) < 2:
        return image
    clusters: list[list[int]] = []
    max_gap = max(12, round(height * 0.45))
    for row in dense_rows:
        if not clusters or row - clusters[-1][-1] > max_gap:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    body_rows = max(clusters, key=len)
    if len(body_rows) < 2:
        return image
    padding = max(8, round((body_rows[-1] - body_rows[0] + 1) * 0.08))
    top = max(0, body_rows[0] - padding)
    bottom = min(height, body_rows[-1] + padding + 1)
    body = image.crop((0, top, width, bottom))
    body_alpha = body.getchannel("A")
    dense_columns = [
        x
        for x in range(width)
        if sum(
            _mask_value(body_alpha, x, y) >= 24 for y in range(body_alpha.height)
        )
        >= body_alpha.height * 0.20
    ]
    if len(dense_columns) < 2:
        return body
    horizontal_padding = max(
        8,
        round((dense_columns[-1] - dense_columns[0] + 1) * 0.012),
    )
    left = max(0, dense_columns[0] - horizontal_padding)
    right = min(width, dense_columns[-1] + horizontal_padding + 1)
    return body.crop((left, 0, right, body.height))


def _resize_for_high_resolution(
    image: Image.Image,
    selection: Image.Image,
) -> tuple[Image.Image, Image.Image]:
    max_dimension = max(image.size)
    if 240 <= max_dimension < 1200:
        scale = min(3.0, 1200 / max_dimension)
        target = (round(image.width * scale), round(image.height * scale))
        image = image.resize(target, Image.Resampling.LANCZOS)
        selection = selection.resize(target, Image.Resampling.LANCZOS)
    image.thumbnail((1800, 1200), Image.Resampling.LANCZOS)
    selection.thumbnail((1800, 1200), Image.Resampling.LANCZOS)
    return image, selection


def _isolate_dominant_object(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    background = _estimate_background_color(image)
    difference = ImageChops.difference(image, Image.new("RGB", image.size, background))
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    foreground = maximum.point([255 if value >= 18 else 0 for value in range(256)])
    component = _dominant_component(foreground)
    if component is None:
        return image, Image.new("L", image.size, 255)
    bbox, selection = component
    cropped_foreground = foreground.crop(bbox)
    cropped_selection = ImageChops.lighter(selection.crop(bbox), cropped_foreground)
    return image.crop(bbox), cropped_selection


def _estimate_background_color(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    step_x = max(1, width // 160)
    step_y = max(1, height // 160)
    border: list[tuple[int, int, int]] = []
    for x in range(0, width, step_x):
        border.append(cast(tuple[int, int, int], rgb.getpixel((x, 0))))
        border.append(cast(tuple[int, int, int], rgb.getpixel((x, height - 1))))
    for y in range(0, height, step_y):
        border.append(cast(tuple[int, int, int], rgb.getpixel((0, y))))
        border.append(cast(tuple[int, int, int], rgb.getpixel((width - 1, y))))
    channels = tuple(
        tuple(sorted(pixel[channel] for pixel in border))
        for channel in range(3)
    )
    sample_count = len(border)
    return cast(
        tuple[int, int, int],
        tuple(channel_values[sample_count // 2] for channel_values in channels),
    )


def _background_to_alpha(image: Image.Image, selection: Image.Image) -> Image.Image:
    background = _estimate_background_color(image)
    difference = ImageChops.difference(image, Image.new("RGB", image.size, background))
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    difference_values = list(maximum.get_flattened_data())
    selection_values = list(selection.convert("L").get_flattened_data())
    object_differences = sorted(
        cast(int, value)
        for value, selected in zip(difference_values, selection_values, strict=True)
        if cast(int, selected) >= 128 and cast(int, value) > 6
    )
    opaque_limit = (
        max(30, object_differences[round((len(object_differences) - 1) * 0.7)])
        if object_differences
        else 96
    )
    alpha = maximum.point(
        [_background_difference_to_alpha(value, opaque_limit) for value in range(256)]
    )
    candidate = maximum.point([255 if value > 6 else 0 for value in range(256)])
    interior = candidate.filter(ImageFilter.MinFilter(3))
    interior = ImageChops.multiply(interior, selection.convert("L"))
    alpha = ImageChops.lighter(alpha, interior)
    alpha = ImageChops.multiply(alpha, selection.convert("L"))

    source_pixels = list(image.get_flattened_data())
    alpha_values = list(alpha.get_flattened_data())
    rgba_pixels: list[tuple[int, int, int, int]] = []
    for pixel, alpha_value in zip(source_pixels, alpha_values, strict=True):
        rgb = cast(tuple[int, int, int], pixel)
        opacity = cast(int, alpha_value)
        if opacity <= 2:
            rgba_pixels.append((0, 0, 0, 0))
            continue
        if opacity >= 252:
            rgba_pixels.append((*rgb, 255))
            continue
        clean_rgb = cast(
            tuple[int, int, int],
            tuple(
                _clamp_channel(
                    round(
                        (channel * 255 - background_channel * (255 - opacity))
                        / opacity
                    )
                )
                for channel, background_channel in zip(rgb, background, strict=True)
            ),
        )
        rgba_pixels.append((*clean_rgb, opacity))

    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result.putdata(rgba_pixels)
    return result


def _background_difference_to_alpha(value: int, opaque_limit: int) -> int:
    transparent_limit = 6
    if value <= transparent_limit:
        return 0
    if value >= opaque_limit:
        return 255
    return round((value - transparent_limit) * 255 / (opaque_limit - transparent_limit))


def _clamp_channel(value: int) -> int:
    return max(0, min(255, value))


def _trim_transparent_space(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.point([255 if value > 2 else 0 for value in range(256)]).getbbox()
    if bbox is None:
        return image
    padding = 8
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom))


def _largest_placed_image(
    document: fitz.Document,
    page: fitz.Page,
) -> Image.Image | None:
    candidates: list[tuple[float, bytes]] = []
    for image_info in page.get_images(full=True):
        xref = image_info[0]
        placements = page.get_image_rects(xref)
        if not placements:
            continue
        extracted = document.extract_image(xref)
        content = extracted.get("image")
        if not isinstance(content, bytes):
            continue
        displayed_area = max(rect.get_area() for rect in placements)
        candidates.append((displayed_area, content))
    if not candidates:
        return None
    _, content = max(candidates, key=lambda candidate: candidate[0])
    return Image.open(io.BytesIO(content)).convert("RGB")


def _dominant_component(
    geometry_mask: Image.Image,
) -> tuple[tuple[int, int, int, int], Image.Image] | None:
    reduced = geometry_mask.copy()
    reduced.thumbnail((900, 900), Image.Resampling.NEAREST)
    reduced = _remove_isolated_long_rules(reduced)
    connected = reduced.filter(ImageFilter.MaxFilter(7))
    width, height = connected.size
    visited = bytearray(width * height)
    best: tuple[int, tuple[int, int, int, int], list[tuple[int, int]]] | None = None

    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if visited[index] or _mask_value(connected, start_x, start_y) < 128:
                continue
            stack = [(start_x, start_y)]
            visited[index] = 1
            coordinates: list[tuple[int, int]] = []
            left = right = start_x
            top = bottom = start_y
            while stack:
                x, y = stack.pop()
                coordinates.append((x, y))
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
                for neighbor_x, neighbor_y in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ):
                    if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                        continue
                    neighbor_index = neighbor_y * width + neighbor_x
                    if (
                        not visited[neighbor_index]
                        and _mask_value(connected, neighbor_x, neighbor_y) >= 128
                    ):
                        visited[neighbor_index] = 1
                        stack.append((neighbor_x, neighbor_y))

            component_width = right - left + 1
            component_height = bottom - top + 1
            score = len(coordinates)
            if (
                score > 25
                and component_width > 12
                and component_height > 10
                and (best is None or score > best[0])
            ):
                best = (
                    score,
                    (left, top, right + 1, bottom + 1),
                    coordinates,
                )

    if best is None:
        return None

    _, reduced_bbox, coordinates = best
    reduced_component = Image.new("L", reduced.size, 0)
    ImageDraw.Draw(reduced_component).point(coordinates, fill=255)
    component_mask = reduced_component.resize(
        geometry_mask.size,
        Image.Resampling.NEAREST,
    )

    scale_x = geometry_mask.width / width
    scale_y = geometry_mask.height / height
    left, top, right, bottom = reduced_bbox
    full_bbox = (
        left * scale_x,
        top * scale_y,
        right * scale_x,
        bottom * scale_y,
    )
    padding = max(
        full_bbox[2] - full_bbox[0],
        full_bbox[3] - full_bbox[1],
    ) * 0.055
    crop_bbox = (
        round(max(0, full_bbox[0] - padding)),
        round(max(0, full_bbox[1] - padding)),
        round(min(geometry_mask.width, full_bbox[2] + padding)),
        round(min(geometry_mask.height, full_bbox[3] + padding)),
    )
    return crop_bbox, component_mask


def _remove_isolated_long_rules(mask: Image.Image) -> Image.Image:
    """Remove standalone drawing-sheet rules without erasing connected part edges."""
    cleaned = mask.copy()
    probe = mask.filter(ImageFilter.MaxFilter(3))
    width, height = probe.size
    visited = bytearray(width * height)
    components: list[
        tuple[int, tuple[int, int, int, int], bool, bool]
    ] = []

    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if visited[index] or _mask_value(probe, start_x, start_y) < 128:
                continue
            stack = [(start_x, start_y)]
            visited[index] = 1
            size = 0
            left = right = start_x
            top = bottom = start_y
            while stack:
                x, y = stack.pop()
                size += 1
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
                for neighbor_x, neighbor_y in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ):
                    if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                        continue
                    neighbor_index = neighbor_y * width + neighbor_x
                    if (
                        not visited[neighbor_index]
                        and _mask_value(probe, neighbor_x, neighbor_y) >= 128
                    ):
                        visited[neighbor_index] = 1
                        stack.append((neighbor_x, neighbor_y))

            component_width = right - left + 1
            component_height = bottom - top + 1
            long_vertical = (
                component_width <= 10
                and component_height >= max(60, round(height * 0.12))
                and component_height >= component_width * 25
            )
            long_horizontal = (
                component_height <= 10
                and component_width >= max(60, round(width * 0.12))
                and component_width >= component_height * 25
            )
            components.append(
                (
                    size,
                    (left, top, right, bottom),
                    long_vertical,
                    long_horizontal,
                )
            )

    body_components = [
        component
        for component in components
        if not component[2] and not component[3]
    ]
    if not body_components:
        return cleaned

    _, (body_left, body_top, body_right, body_bottom), _, _ = max(
        body_components,
        key=lambda component: component[0],
    )
    draw = ImageDraw.Draw(cleaned)
    for _, (left, top, right, bottom), long_vertical, long_horizontal in components:
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        outside_body = (
            long_vertical and not body_left <= center_x <= body_right
        ) or (
            long_horizontal and not body_top <= center_y <= body_bottom
        )
        if outside_body:
            draw.rectangle(
                (
                    max(0, left - 2),
                    max(0, top - 2),
                    min(width - 1, right + 2),
                    min(height - 1, bottom + 2),
                ),
                fill=0,
            )
    return cleaned


def _mask_value(image: Image.Image, x: int, y: int) -> int:
    return cast(int, image.getpixel((x, y)))


def _trim_white_space(image: Image.Image) -> Image.Image:
    background = Image.new("RGB", image.size, "white")
    difference = ImageChops.difference(image, background).convert("L")
    difference = difference.point([255 if value > 18 else 0 for value in range(256)])
    bbox = difference.getbbox()
    if bbox is None:
        return image
    padding = 18
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom))
