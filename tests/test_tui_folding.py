from __future__ import annotations

from aunic.tui.folding import (
    apply_folds,
    carry_forward_managed_section_folds,
    default_folded_anchor_ids,
    detect_fold_regions,
    heading_anchor_ids_for_title,
    reconstruct_full_text,
    toggle_fold_for_line,
)


def test_default_folded_anchor_ids_include_search_results_and_work_log() -> None:
    text = (
        "# Search Results\n\n"
        "one\n\n"
        "# Body\n\n"
        "two\n\n"
        "# Work Log\n\n"
        "three\n"
    )

    folded = default_folded_anchor_ids(text)

    assert "heading:0:search-results" in folded
    assert "heading:6:work-log" in folded


def test_apply_folds_and_reconstruct_round_trip() -> None:
    text = "# Search Results\n\none\n\ntwo\n\n# Body\n\nthree\n"
    folded = default_folded_anchor_ids(text)

    render = apply_folds(text, folded)

    assert "▶ " in render.display_text
    assert reconstruct_full_text(render.display_text, render.placeholder_map) == text


def test_toggle_fold_for_line_toggles_heading_anchor() -> None:
    text = "# Heading\n\none\n\ntwo\n"

    updated = toggle_fold_for_line(text, set(), 0)

    assert "heading:0:heading" in updated


def test_detect_fold_regions_finds_heading_list_and_indented_regions() -> None:
    text = (
        "# Heading\n\n"
        "- item one\n"
        "  child\n"
        "- item two\n\n"
        "    code one\n"
        "    code two\n"
    )

    regions = detect_fold_regions(text)
    kinds = {region.kind for region in regions}

    assert "heading" in kinds
    assert "list" in kinds or "indented" in kinds


def test_carry_forward_managed_section_folds_new_search_results_default_to_folded() -> None:
    previous = "# Body\n\nhello\n"
    new = "# Body\n\nhello\n\n# Search Results\n\nbatch\n"

    updated = carry_forward_managed_section_folds(previous, new, set(), title="search results")

    assert heading_anchor_ids_for_title(new, "search results") <= updated


def test_carry_forward_managed_section_folds_preserves_unfolded_search_results() -> None:
    previous = "# Search Results\n\nold batch\n"
    new = "# Search Results\n\nold batch\n\nnew batch\n"

    updated = carry_forward_managed_section_folds(previous, new, set(), title="search results")

    assert not (heading_anchor_ids_for_title(new, "search results") & updated)
