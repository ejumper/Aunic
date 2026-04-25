from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from collections.abc import Iterable
from pathlib import Path

from PIL import Image

from aunic.config import ImageInputSettings, SETTINGS
from aunic.domain import ProviderImageInput
from aunic.errors import FileReadError

_MEDIA_TYPE_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def is_supported_image_path(
    path: Path | str,
    *,
    settings: ImageInputSettings | None = None,
) -> bool:
    limits = settings or SETTINGS.image_inputs
    return Path(path).suffix.lower() in limits.supported_extensions


async def prepare_image_input_from_path(
    path: Path | str,
    *,
    persistent: bool,
    settings: ImageInputSettings | None = None,
) -> ProviderImageInput:
    normalized = Path(path).expanduser().resolve()
    try:
        data = await asyncio.to_thread(normalized.read_bytes)
    except FileNotFoundError as exc:
        raise FileReadError(f"File does not exist: {normalized}") from exc
    except OSError as exc:
        raise FileReadError(f"Could not read file: {normalized}") from exc
    return await prepare_image_input_from_bytes(
        name=normalized.name,
        data=data,
        source_path=normalized,
        persistent=persistent,
        settings=settings,
    )


async def prepare_image_inputs_from_paths(
    paths: Iterable[Path | str],
    *,
    persistent: bool,
    settings: ImageInputSettings | None = None,
) -> tuple[ProviderImageInput, ...]:
    prepared = await asyncio.gather(
        *(
            prepare_image_input_from_path(
                path,
                persistent=persistent,
                settings=settings,
            )
            for path in paths
        )
    )
    return tuple(prepared)


async def prepare_image_input_from_base64(
    *,
    name: str,
    data_base64: str,
    persistent: bool,
    source_path: Path | None = None,
    settings: ImageInputSettings | None = None,
) -> ProviderImageInput:
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except ValueError as exc:
        raise FileReadError(f"Attachment {name!r} is not valid base64 image data.") from exc
    return await prepare_image_input_from_bytes(
        name=name,
        data=raw,
        source_path=source_path,
        persistent=persistent,
        settings=settings,
    )


async def prepare_image_input_from_bytes(
    *,
    name: str,
    data: bytes,
    persistent: bool,
    source_path: Path | None = None,
    settings: ImageInputSettings | None = None,
) -> ProviderImageInput:
    limits = settings or SETTINGS.image_inputs
    if len(data) > limits.max_original_bytes:
        raise FileReadError(
            f"Image {name!r} exceeds the maximum original size of {limits.max_original_bytes} bytes."
        )
    return await asyncio.to_thread(
        _prepare_image_input_sync,
        name,
        data,
        source_path,
        persistent,
        limits,
    )


def image_input_to_anthropic_block(image: ProviderImageInput) -> dict[str, object]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.media_type,
            "data": image.data_base64,
        },
    }


def image_input_to_openai_chat_block(image: ProviderImageInput) -> dict[str, object]:
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{image.media_type};base64,{image.data_base64}",
        },
    }


def _prepare_image_input_sync(
    name: str,
    data: bytes,
    source_path: Path | None,
    persistent: bool,
    settings: ImageInputSettings,
) -> ProviderImageInput:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            processed = image
            if max(width, height) > settings.max_dimension_px:
                processed = image.copy()
                processed.thumbnail((settings.max_dimension_px, settings.max_dimension_px))
            media_type, output = _serialize_image(processed, original=image, settings=settings)
            out_width, out_height = processed.size
            if processed is not image:
                processed.close()
    except OSError as exc:
        raise FileReadError(f"Attachment {name!r} is not a supported image file.") from exc

    if len(output) > settings.max_processed_bytes:
        raise FileReadError(
            f"Image {name!r} exceeds the maximum processed size of {settings.max_processed_bytes} bytes."
        )

    return ProviderImageInput(
        name=name,
        media_type=media_type,
        data_base64=base64.b64encode(output).decode("ascii"),
        source_path=source_path,
        width=out_width,
        height=out_height,
        size_bytes=len(output),
        persistent=persistent,
        sha256=hashlib.sha256(output).hexdigest(),
    )


def _serialize_image(
    image: Image.Image,
    *,
    original: Image.Image,
    settings: ImageInputSettings,
) -> tuple[str, bytes]:
    output = io.BytesIO()
    original_format = (original.format or "").upper()
    has_alpha = "A" in image.getbands()
    if original_format in {"JPEG", "JPG"} and not has_alpha:
        converted = image.convert("RGB")
        converted.save(output, format="JPEG", quality=settings.jpeg_quality, optimize=True)
        return "image/jpeg", output.getvalue()
    image.save(output, format="PNG", optimize=True)
    return "image/png", output.getvalue()
