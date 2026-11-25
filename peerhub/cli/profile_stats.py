"""Render profile measurements and ask receipts for the terminal."""
from __future__ import annotations

from typing import Any, Mapping


def metric_cell(value: int | float | None, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return f"{value:.3f}".rstrip("0").rstrip(".") + suffix


def _duration_cell(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, remaining_seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{remaining_seconds:02d}s"
    return f"{remaining_seconds}s"


def _tokens_cell(tokens: float | None) -> str:
    if tokens is None:
        return "-"
    if round(tokens / 1000) >= 1000:
        return f"{round(tokens / 1_000_000)}M"
    if round(tokens) >= 1000:
        return f"{round(tokens / 1000)}k"
    return str(round(tokens))


def _percent_cell(value: float | None) -> str:
    return "-" if value is None else f"{round(value)}%"


def render_profile_stats(stats: list[dict[str, Any]]) -> str:
    from peerhub._console import format_text_cell, visible_len

    if not stats:
        return "(no profiles)"
    rows = [("PROFILE", "N", "P50", "CACHE%", "IN/ASK", "OUT/ASK", "POOL/ACC", "BENCH", "STALE", "TOK%")]
    for row in stats:
        tokens = row["tokens_per_ask"]
        rows.append((row["profile"], str(row["n"]), _duration_cell(row["p50_wall_seconds"]),
                     _percent_cell(row["cache_read_pct"]), _tokens_cell(tokens["input_tokens"]),
                     _tokens_cell(tokens["output_tokens"]), metric_cell(row["pool_pct_per_accepted_answer"], "pp"),
                     (row["last_benchmark_run"] or "-")[:10], "yes" if row["stale"] else "-",
                     _percent_cell(row["token_coverage_pct"])))
    widths = [max(map(visible_len, column)) for column in zip(*rows)]
    profile_width = min(widths[0], 119 - sum(widths[1:]) - 2 * (len(widths) - 1))
    return "\n".join(
        "  ".join([format_text_cell(name, profile_width),
                   *(cell.rjust(width) for cell, width in zip(cells, widths[1:]))])
        for name, *cells in rows
    )


def render_ask_receipt(ask_id: str | None, profile: str | None, metrics: Mapping[str, Any]) -> str:
    profile = metrics.get("profile") or profile or "-"
    wall = metric_cell(metrics.get("wall_seconds"), "s")
    tokens_in, tokens_out = (metric_cell(metrics.get(key)) for key in ("input_tokens", "output_tokens"))
    cache = metric_cell(metrics.get("cache_read_pct"), "%")
    pool = metric_cell(metrics.get("pool_delta_pp"), "pp")
    return f"{ask_id or '-'} - {profile} - {wall} - tokens {tokens_in}/{tokens_out}/{cache} - {pool}"
