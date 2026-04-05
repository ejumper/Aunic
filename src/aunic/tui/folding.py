from __future__ import annotations

import re
from dataclasses import dataclass

FOLD_PLACEHOLDER_PREFIX = "▶ "

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LIST_RE = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])\s+")
_INDENTED_RE = re.compile(r"^(?:\t| {4,})\S")
_DEFAULT_FOLDED_TITLES = {"search results", "work log"}


@dataclass(frozen=True)
class FoldRegion:
    anchor_id: str
    kind: str
    title: str
    start_line: int
    end_line: int
    hidden_start_line: int
    hidden_end_line: int
    hidden_text: str
    placeholder_line: str


@dataclass(frozen=True)
class FoldRender:
    display_text: str
    placeholder_map: dict[str, str]
    anchor_for_display_line: dict[int, str]
    folded_anchor_ids: tuple[str, ...]
    regions: tuple[FoldRegion, ...]


def detect_fold_regions(text: str) -> tuple[FoldRegion, ...]:
    raw_lines = text.splitlines(keepends=True)
    plain_lines = [line.rstrip("\r\n") for line in raw_lines]
    heading_regions = _detect_heading_regions(raw_lines, plain_lines)
    list_regions = _detect_list_regions(raw_lines, plain_lines)
    indented_regions = _detect_indented_regions(raw_lines, plain_lines)
    return tuple(
        sorted(
            (*heading_regions, *list_regions, *indented_regions),
            key=lambda item: (item.start_line, item.hidden_start_line, item.hidden_end_line, item.anchor_id),
        )
    )


def default_folded_anchor_ids(text: str) -> set[str]:
    folded: set[str] = set()
    for region in detect_fold_regions(text):
        if region.kind != "heading":
            continue
        if region.title.casefold() in _DEFAULT_FOLDED_TITLES:
            folded.add(region.anchor_id)
    return folded


def carry_forward_managed_section_folds(
    previous_text: str,
    new_text: str,
    previous_folded_anchor_ids: set[str],
    *,
    title: str = "search results",
) -> set[str]:
    updated = set(previous_folded_anchor_ids)
    previous_anchor_ids = heading_anchor_ids_for_title(previous_text, title)
    new_anchor_ids = heading_anchor_ids_for_title(new_text, title)

    updated.difference_update(previous_anchor_ids)

    if not new_anchor_ids:
        return updated

    if not previous_anchor_ids or bool(previous_anchor_ids & previous_folded_anchor_ids):
        updated.update(new_anchor_ids)
    else:
        updated.difference_update(new_anchor_ids)

    return updated


def apply_folds(text: str, folded_anchor_ids: set[str]) -> FoldRender:
    raw_lines = text.splitlines(keepends=True)
    regions = detect_fold_regions(text)
    folded_regions = tuple(
        region
        for region in regions
        if region.anchor_id in folded_anchor_ids
    )
    hidden_start_map = {region.hidden_start_line: region for region in folded_regions}

    display_parts: list[str] = []
    placeholder_map: dict[str, str] = {}
    anchor_for_display_line: dict[int, str] = {}

    display_line = 0
    line_index = 0
    while line_index < len(raw_lines):
        hidden_region = hidden_start_map.get(line_index)
        if hidden_region is not None:
            placeholder_text = hidden_region.placeholder_line
            if hidden_region.hidden_text.endswith("\n"):
                placeholder_text += "\n"
            display_parts.append(placeholder_text)
            placeholder_map[hidden_region.placeholder_line] = hidden_region.hidden_text
            anchor_for_display_line[display_line] = hidden_region.anchor_id
            display_line += 1
            line_index = hidden_region.hidden_end_line + 1
            continue

        display_parts.append(raw_lines[line_index])
        for region in regions:
            if region.start_line == line_index:
                anchor_for_display_line[display_line] = region.anchor_id
        display_line += 1
        line_index += 1

    return FoldRender(
        display_text="".join(display_parts),
        placeholder_map=placeholder_map,
        anchor_for_display_line=anchor_for_display_line,
        folded_anchor_ids=tuple(sorted(folded_anchor_ids)),
        regions=regions,
    )


def reconstruct_full_text(
    display_text: str,
    placeholder_map: dict[str, str],
) -> str:
    raw_lines = display_text.splitlines(keepends=True)
    rebuilt: list[str] = []
    for raw_line in raw_lines:
        stripped = raw_line.rstrip("\r\n")
        replacement = placeholder_map.get(stripped)
        if replacement is not None:
            rebuilt.append(replacement)
            continue
        rebuilt.append(raw_line)
    if display_text and not raw_lines:
        return display_text
    return "".join(rebuilt)


def is_fold_placeholder_line(line_text: str) -> bool:
    return line_text.startswith(FOLD_PLACEHOLDER_PREFIX)


def toggle_fold_for_line(
    text: str,
    folded_anchor_ids: set[str],
    line_number: int,
) -> set[str]:
    render = apply_folds(text, folded_anchor_ids)
    anchor_id = render.anchor_for_display_line.get(line_number)
    if anchor_id is None:
        return set(folded_anchor_ids)
    updated = set(folded_anchor_ids)
    if anchor_id in updated:
        updated.remove(anchor_id)
    else:
        updated.add(anchor_id)
    return updated


def summarize_fold_region(region: FoldRegion) -> str:
    return region.placeholder_line


def heading_anchor_ids_for_title(text: str, title: str) -> set[str]:
    normalized = title.casefold().strip()
    return {
        region.anchor_id
        for region in detect_fold_regions(text)
        if region.kind == "heading" and region.title.casefold() == normalized
    }


def _detect_heading_regions(
    raw_lines: list[str],
    plain_lines: list[str],
) -> tuple[FoldRegion, ...]:
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(plain_lines):
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        headings.append((index, len(match.group(1)), match.group(2).strip()))

    regions: list[FoldRegion] = []
    for position, (index, level, title) in enumerate(headings):
        next_index = len(raw_lines)
        for candidate_index, candidate_level, _ in headings[position + 1 :]:
            if candidate_level <= level:
                next_index = candidate_index
                break
        hidden_start = index + 1
        hidden_end = next_index - 1
        if hidden_start > hidden_end:
            continue
        hidden_text = "".join(raw_lines[hidden_start:next_index])
        line_count = hidden_end - hidden_start + 1
        regions.append(
            FoldRegion(
                anchor_id=f"heading:{_logical_heading_line_index(plain_lines, index)}:{_slugify(title)}",
                kind="heading",
                title=title,
                start_line=index,
                end_line=hidden_end,
                hidden_start_line=hidden_start,
                hidden_end_line=hidden_end,
                hidden_text=hidden_text,
                placeholder_line=f"{FOLD_PLACEHOLDER_PREFIX}[{line_count} lines folded under {title}]",
            )
        )
    return tuple(regions)


def _detect_list_regions(
    raw_lines: list[str],
    plain_lines: list[str],
) -> tuple[FoldRegion, ...]:
    regions: list[FoldRegion] = []
    index = 0
    while index < len(plain_lines):
        match = _LIST_RE.match(plain_lines[index])
        if match is None:
            index += 1
            continue
        base_indent = len(match.group(1))
        end = index
        pointer = index + 1
        while pointer < len(plain_lines):
            line = plain_lines[pointer]
            if not line.strip():
                end = pointer
                pointer += 1
                continue
            next_match = _LIST_RE.match(line)
            if next_match is not None and len(next_match.group(1)) <= base_indent:
                end = pointer
                pointer += 1
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if indent > base_indent:
                end = pointer
                pointer += 1
                continue
            break
        hidden_start = index + 1
        hidden_end = end
        if hidden_start <= hidden_end:
            title = plain_lines[index].strip()
            hidden_text = "".join(raw_lines[hidden_start : hidden_end + 1])
            regions.append(
                FoldRegion(
                    anchor_id=f"list:{index}:{_slugify(title)}",
                    kind="list",
                    title=title,
                    start_line=index,
                    end_line=hidden_end,
                    hidden_start_line=hidden_start,
                    hidden_end_line=hidden_end,
                    hidden_text=hidden_text,
                    placeholder_line=f"{FOLD_PLACEHOLDER_PREFIX}[{hidden_end - hidden_start + 1} list lines folded]",
                )
            )
        index = max(pointer, index + 1)
    return tuple(regions)


def _detect_indented_regions(
    raw_lines: list[str],
    plain_lines: list[str],
) -> tuple[FoldRegion, ...]:
    regions: list[FoldRegion] = []
    index = 0
    while index < len(plain_lines):
        if _INDENTED_RE.match(plain_lines[index]) is None or _LIST_RE.match(plain_lines[index]):
            index += 1
            continue
        end = index
        pointer = index + 1
        while pointer < len(plain_lines) and (
            not plain_lines[pointer].strip() or _INDENTED_RE.match(plain_lines[pointer]) is not None
        ):
            end = pointer
            pointer += 1
        hidden_start = index + 1
        hidden_end = end
        if hidden_start <= hidden_end:
            title = plain_lines[index].strip()
            hidden_text = "".join(raw_lines[hidden_start : hidden_end + 1])
            regions.append(
                FoldRegion(
                    anchor_id=f"indent:{index}:{_slugify(title)}",
                    kind="indented",
                    title=title,
                    start_line=index,
                    end_line=hidden_end,
                    hidden_start_line=hidden_start,
                    hidden_end_line=hidden_end,
                    hidden_text=hidden_text,
                    placeholder_line=f"{FOLD_PLACEHOLDER_PREFIX}[{hidden_end - hidden_start + 1} indented lines folded]",
                )
            )
        index = max(pointer, index + 1)
    return tuple(regions)


def _slugify(text: str) -> str:
    lowered = text.casefold()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return collapsed or "section"

def _logical_heading_line_index(plain_lines: list[str], heading_index: int) -> int:
    collapsed_blank_lines = 0
    for index in range(1, heading_index):
        if plain_lines[index]:
            continue
        if _HEADING_RE.match(plain_lines[index - 1]) is not None:
            collapsed_blank_lines += 1
    return heading_index - collapsed_blank_lines
