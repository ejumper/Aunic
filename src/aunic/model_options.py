from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aunic.config import SETTINGS
from aunic.proto_settings import get_openai_compatible_profiles


@dataclass(frozen=True)
class ModelOption:
    label: str
    provider_name: str
    model: str
    profile_id: str | None = None
    context_window: int | None = None


def build_model_options(
    cwd: Path,
    initial_provider: str = "codex",
    initial_model: str | None = None,
) -> tuple[ModelOption, ...]:
    codex_model = (
        initial_model
        if initial_provider == "codex" and initial_model
        else SETTINGS.codex.default_model
    )
    options: list[ModelOption] = [
        ModelOption(label=f"Codex ({codex_model})", provider_name="codex", model=codex_model),
    ]

    openai_profiles = get_openai_compatible_profiles(cwd)
    if openai_profiles:
        for profile in openai_profiles:
            model = (
                initial_model
                if initial_provider in {"openai_compatible", "llama"}
                and initial_model
                and profile.model == initial_model
                else profile.model
            )
            options.append(
                ModelOption(
                    label=profile.display_label,
                    provider_name="openai_compatible",
                    model=model,
                    profile_id=profile.profile_id,
                    context_window=profile.context_window,
                )
            )
    else:
        llama_model = (
            initial_model
            if initial_provider in {"openai_compatible", "llama"} and initial_model
            else SETTINGS.llama_cpp.default_model
        )
        options.append(
            ModelOption(
                label="Llama Addie",
                provider_name="openai_compatible",
                model=llama_model,
                profile_id="llama_addie",
            )
        )

    options.extend(
        [
            ModelOption(
                label="Claude Haiku",
                provider_name="claude",
                model=SETTINGS.claude.haiku_model,
            ),
            ModelOption(
                label="Claude Sonnet",
                provider_name="claude",
                model=SETTINGS.claude.sonnet_model,
            ),
            ModelOption(
                label="Claude Opus",
                provider_name="claude",
                model=SETTINGS.claude.opus_model,
            ),
        ]
    )
    return tuple(options)


def selected_model_index(
    options: tuple[ModelOption, ...],
    provider_name: str,
    model: str | None = None,
    profile_id: str | None = None,
) -> int:
    if provider_name == "llama":
        provider_name = "openai_compatible"
        profile_id = profile_id or "llama_addie"
    if provider_name == "openai_compatible" and profile_id is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.profile_id == profile_id:
                return index
    if provider_name == "openai_compatible" and model is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.model == model:
                return index
    if model is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.model == model:
                return index
    for index, option in enumerate(options):
        if option.provider_name == provider_name:
            return index
    return 0
