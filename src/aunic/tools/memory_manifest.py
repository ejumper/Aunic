from __future__ import annotations

from typing import Any

# Per-tool hint bullets. Keyed by tool name so the manifest automatically
# grows when later phases add new memory tools.
MEMORY_TOOL_HINTS: dict[str, str] = {
    "search_transcripts": (
        "search_transcripts: query past tool calls and results across every Aunic note on this "
        "system. Filter by tool= (e.g. \"bash\", \"web_search\"), query= (substring over args "
        "and results JSON), scope=<path subtree>. Reach for this when the user mentions "
        "\"last time\", \"before\", or anything time-referential, and before any action that "
        "might repeat or contradict past work. Returns absolute path + row_number for each hit "
        "so you can open the full file with the read tool."
    ),
    "grep_notes": (
        "grep_notes: ripgrep-shaped content search scoped to Aunic notes only, with an optional "
        "section= filter (\"note-content\", \"transcript\", or \"all\"). Use "
        "section=\"transcript\" to find past executed commands and tool calls without prose "
        "noise; use section=\"note-content\" to find prose mentions without transcript noise. "
        "Returns absolute path, line number, and surrounding context. Reach for this when you "
        "know a literal phrase or pattern and want to find every note that contains it, or to "
        "distinguish \"where did I write about X\" from \"where did I actually do X\"."
    ),
    "read_map": (
        "read_map: read the user's pre-built index of every Aunic note on this system "
        "(~/.aunic/map.md). Each entry is a path + short summary. Reach for this when you do "
        "not yet know a specific query or phrase to search for, and want to browse the user's "
        "notes by topic. Pass scope=<path> to get only the subtree relevant to the current "
        "task. If the index is missing, tell the user to run /map."
    ),
    "rag_search": (
        "rag_search: search the local RAG knowledge base across indexed scopes. "
        "Use scope= to narrow to a specific collection (e.g. \"docs\", \"python\", \"wiki\"). "
        "Returns result_id, doc_id, title, snippet, and score for each hit. Follow up with "
        "rag_fetch(result_id=...) to retrieve chunk content. Reach for this when the user asks about topics likely "
        "covered in their indexed documentation, notes, or reference material."
    ),
}

_MANIFEST_PREAMBLE = (
    "Memory tools. Before proposing destructive bash commands, significant edits, or research "
    "that may already have been done in this or another note, check prior sessions:"
)


def build_memory_manifest(registry: tuple[Any, ...]) -> str | None:
    """Return the memory-tools block for the system prompt, or None if no memory tools
    are present in the registry.

    Takes the registry tuple so tool names can't drift and the manifest automatically
    shrinks when tools are absent.
    """
    tool_names = {definition.spec.name for definition in registry}
    bullets = [
        f"- {hint}"
        for name, hint in MEMORY_TOOL_HINTS.items()
        if name in tool_names
    ]
    if not bullets:
        return None
    return _MANIFEST_PREAMBLE + "\n" + "\n".join(bullets)
