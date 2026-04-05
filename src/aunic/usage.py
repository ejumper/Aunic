from __future__ import annotations

from collections.abc import Iterable

from aunic.domain import Usage, UsageLog, UsageLogEntry


def combine_usage(usages: Iterable[Usage | None]) -> Usage | None:
    values = [usage for usage in usages if usage is not None]
    if not values:
        return None
    return Usage(
        total_tokens=_sum_field(values, "total_tokens"),
        input_tokens=_sum_field(values, "input_tokens"),
        cached_input_tokens=_sum_field(values, "cached_input_tokens"),
        output_tokens=_sum_field(values, "output_tokens"),
        reasoning_output_tokens=_sum_field(values, "reasoning_output_tokens"),
        model_context_window=_max_field(values, "model_context_window"),
    )


def build_usage_log(entries: Iterable[UsageLogEntry]) -> UsageLog:
    entry_tuple = tuple(entries)
    return UsageLog(
        entries=entry_tuple,
        total=combine_usage(entry.usage for entry in entry_tuple),
    )


def combine_usage_logs(logs: Iterable[UsageLog]) -> UsageLog:
    merged: list[UsageLogEntry] = []
    for log in logs:
        merged.extend(log.entries)
    reindexed = tuple(
        UsageLogEntry(
            index=index,
            stage=entry.stage,
            usage=entry.usage,
            provider=entry.provider,
            model=entry.model,
            finish_reason=entry.finish_reason,
            metadata=dict(entry.metadata),
        )
        for index, entry in enumerate(merged, start=1)
    )
    return UsageLog(
        entries=reindexed,
        total=combine_usage(entry.usage for entry in reindexed),
    )


def format_usage_brief(usage: Usage | None) -> str:
    if usage is None:
        return "usage unavailable"
    parts: list[str] = []
    if usage.input_tokens is not None:
        parts.append(f"in={usage.input_tokens}")
    if usage.cached_input_tokens is not None:
        parts.append(f"cached={usage.cached_input_tokens}")
    if usage.output_tokens is not None:
        parts.append(f"out={usage.output_tokens}")
    if usage.reasoning_output_tokens is not None:
        parts.append(f"reason={usage.reasoning_output_tokens}")
    if usage.total_tokens is not None:
        parts.append(f"total={usage.total_tokens}")
    return " ".join(parts) if parts else "usage unavailable"


def usage_to_dict(usage: Usage | None) -> dict[str, int | None] | None:
    if usage is None:
        return None
    return {
        "total_tokens": usage.total_tokens,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "model_context_window": usage.model_context_window,
    }


def usage_log_to_dict(log: UsageLog) -> dict[str, object]:
    return {
        "entries": [
            {
                "index": entry.index,
                "stage": entry.stage,
                "provider": entry.provider,
                "model": entry.model,
                "finish_reason": entry.finish_reason,
                "usage": usage_to_dict(entry.usage),
                "metadata": dict(entry.metadata),
            }
            for entry in log.entries
        ],
        "total": usage_to_dict(log.total),
    }


def _sum_field(values: list[Usage], field_name: str) -> int | None:
    items = [getattr(value, field_name) for value in values if getattr(value, field_name) is not None]
    if not items:
        return None
    return sum(items)


def _max_field(values: list[Usage], field_name: str) -> int | None:
    items = [getattr(value, field_name) for value in values if getattr(value, field_name) is not None]
    if not items:
        return None
    return max(items)
