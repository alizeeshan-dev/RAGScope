"""Dependency-free deterministic SVG rendering for research figure specifications."""

from __future__ import annotations

from collections import defaultdict
from html import escape

from backend.app.research.contracts import FigureDatum, FigureSpec

WIDTH = 960
HEIGHT = 560
PLOT_LEFT = 92
PLOT_TOP = 82
PLOT_WIDTH = 800
PLOT_HEIGHT = 340
PALETTE = ("#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed", "#0891b2")


def render_svg(spec: FigureSpec) -> str:
    """Render a fixed-layout SVG whose marks come only from ``spec.data``."""

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img">'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _text(32, 38, spec.title, size=22, weight="700"),
        _text(
            32,
            63,
            f"status={spec.status}; displayed sample sizes use exact figure denominators",
            size=12,
            fill="#475569",
        ),
    ]
    if spec.status == "incomplete":
        lines.extend(_render_incomplete(spec))
    elif spec.kind == "scatter":
        lines.extend(_render_scatter(spec))
    elif spec.kind == "line":
        lines.extend(_render_line(spec))
    elif spec.kind == "distribution":
        lines.extend(_render_distribution(spec))
    else:
        lines.extend(_render_bars(spec))
    lines.extend(_render_audit(spec))
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_incomplete(spec: FigureSpec) -> list[str]:
    lines = [
        '<rect x="92" y="100" width="800" height="300" rx="8" fill="#f8fafc" '
        'stroke="#94a3b8" stroke-dasharray="8 6"/>',
        _text(480, 220, "INCOMPLETE DATA", anchor="middle", size=28, weight="700"),
    ]
    for index, note in enumerate(spec.notes[:4]):
        lines.append(_text(480, 255 + index * 22, note, anchor="middle", size=13))
    return lines


def _render_bars(spec: FigureSpec) -> list[str]:
    data = [item for item in spec.data if item.value is not None]
    if not data:
        return _render_incomplete(spec)
    maximum = max(1.0, max(item.value or 0.0 for item in data))
    slot = PLOT_WIDTH / len(data)
    bar_width = min(62.0, slot * 0.64)
    colors = _series_colors(data)
    lines = _axes(maximum)
    for index, item in enumerate(data):
        assert item.value is not None
        height = item.value / maximum * PLOT_HEIGHT
        x = PLOT_LEFT + index * slot + (slot - bar_width) / 2
        y = PLOT_TOP + PLOT_HEIGHT - height
        label = item.category or item.series
        n = _sample_n(spec, item)
        lines.extend(
            [
                (
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" '
                    f'height="{height:.2f}" fill="{colors[item.series]}"/>'
                ),
                _text(x + bar_width / 2, y - 8, f"{item.value:.3g} (n={n})", anchor="middle"),
                _text(
                    x + bar_width / 2,
                    PLOT_TOP + PLOT_HEIGHT + 18,
                    _short(label),
                    anchor="middle",
                    size=10,
                ),
            ]
        )
    return lines


def _render_scatter(spec: FigureSpec) -> list[str]:
    data = [item for item in spec.data if item.x is not None and item.y is not None]
    if not data:
        return _render_incomplete(spec)
    x_values = [item.x for item in data if item.x is not None]
    y_values = [item.y for item in data if item.y is not None]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    colors = _series_colors(data)
    lines = _axes(y_max if y_max > 0 else 1.0)
    for item in data:
        assert item.x is not None and item.y is not None
        x = _scale(item.x, x_min, x_max, PLOT_LEFT, PLOT_LEFT + PLOT_WIDTH)
        y = _scale(item.y, y_min, y_max, PLOT_TOP + PLOT_HEIGHT, PLOT_TOP)
        lines.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{colors[item.series]}">'
            f"<title>{escape(item.run_id or '')}: cost={item.x}, correctness={item.y}</title>"
            "</circle>"
        )
    lines.append(_text(492, 450, "Estimated cost (stored units)", anchor="middle", size=12))
    lines.append(_text(20, 250, "Correctness", size=12))
    return lines


def _render_distribution(spec: FigureSpec) -> list[str]:
    data = [item for item in spec.data if item.value is not None]
    if not data:
        return _render_incomplete(spec)
    series = sorted({item.series for item in data})
    maximum = max(item.value or 0.0 for item in data) or 1.0
    colors = _series_colors(data)
    slot = PLOT_WIDTH / len(series)
    lines = _axes(maximum)
    for series_index, label in enumerate(series):
        values = [item for item in data if item.series == label]
        for point_index, item in enumerate(values):
            assert item.value is not None
            offset = ((point_index % 7) - 3) * 4
            x = PLOT_LEFT + series_index * slot + slot / 2 + offset
            y = PLOT_TOP + PLOT_HEIGHT - item.value / maximum * PLOT_HEIGHT
            lines.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{colors[label]}">'
                f"<title>{escape(item.run_id or '')}: {item.value}</title></circle>"
            )
        lines.append(
            _text(
                PLOT_LEFT + series_index * slot + slot / 2,
                PLOT_TOP + PLOT_HEIGHT + 18,
                f"{_short(label)} (n={len(values)})",
                anchor="middle",
                size=10,
            )
        )
    return lines


def _render_line(spec: FigureSpec) -> list[str]:
    data = [item for item in spec.data if item.value is not None and item.category is not None]
    if not data:
        return _render_incomplete(spec)
    category_order = {"retrieval": 0, "reranking": 1, "context": 2}
    categories = sorted(
        {item.category for item in data if item.category is not None},
        key=lambda value: (category_order.get(value, 99), value),
    )
    colors = _series_colors(data)
    grouped: dict[str, list[FigureDatum]] = defaultdict(list)
    for item in data:
        grouped[item.series].append(item)
    lines = _axes(1.0)
    denominator = max(1, len(categories) - 1)
    for series in sorted(grouped):
        points: list[tuple[float, float, FigureDatum]] = []
        by_category = {item.category: item for item in grouped[series]}
        for index, category in enumerate(categories):
            current = by_category.get(category)
            if current is None or current.value is None:
                continue
            x = PLOT_LEFT + index / denominator * PLOT_WIDTH
            y = PLOT_TOP + PLOT_HEIGHT - current.value * PLOT_HEIGHT
            points.append((x, y, current))
        if len(points) > 1:
            path = " ".join(
                ("M" if index == 0 else "L") + f" {x:.2f} {y:.2f}"
                for index, (x, y, _) in enumerate(points)
            )
            lines.append(
                f'<path d="{path}" fill="none" stroke="{colors[series]}" stroke-width="3"/>'
            )
        for x, y, item in points:
            n = _sample_n(spec, item)
            lines.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{colors[series]}"/>')
            lines.append(_text(x, y - 10, f"{item.value:.3g} (n={n})", anchor="middle"))
    for index, category in enumerate(categories):
        x = PLOT_LEFT + index / denominator * PLOT_WIDTH
        lines.append(_text(x, PLOT_TOP + PLOT_HEIGHT + 18, category, anchor="middle", size=11))
    return lines


def _axes(maximum: float) -> list[str]:
    return [
        f'<line x1="{PLOT_LEFT}" y1="{PLOT_TOP}" x2="{PLOT_LEFT}" '
        f'y2="{PLOT_TOP + PLOT_HEIGHT}" stroke="#334155"/>',
        f'<line x1="{PLOT_LEFT}" y1="{PLOT_TOP + PLOT_HEIGHT}" '
        f'x2="{PLOT_LEFT + PLOT_WIDTH}" y2="{PLOT_TOP + PLOT_HEIGHT}" stroke="#334155"/>',
        _text(PLOT_LEFT - 8, PLOT_TOP + 4, f"{maximum:.3g}", anchor="end", size=10),
        _text(PLOT_LEFT - 8, PLOT_TOP + PLOT_HEIGHT + 4, "0", anchor="end", size=10),
    ]


def _render_audit(spec: FigureSpec) -> list[str]:
    total = sum(item.denominator_count for item in spec.samples)
    missing = sum(item.missing_count for item in spec.samples)
    excluded = sum(item.excluded_infrastructure_count for item in spec.samples)
    summary = (
        f"Displayed denominator total n={total}; missing={missing}; "
        f"infrastructure-excluded={excluded}"
    )
    lines = [
        _text(
            32,
            492,
            summary,
            size=12,
            weight="700",
        )
    ]
    if spec.notes:
        lines.append(_text(32, 515, "Notes: " + " ".join(spec.notes[:2]), size=11))
    lines.append(
        _text(
            32,
            542,
            f"schema={spec.schema_version}; figure={spec.figure_id}",
            size=10,
            fill="#64748b",
        )
    )
    return lines


def _sample_n(spec: FigureSpec, datum: FigureDatum) -> int:
    candidates = (
        f"{datum.series} / {datum.category}",
        datum.series,
        datum.category or "",
    )
    for candidate in candidates:
        for sample in spec.samples:
            if sample.group == candidate:
                return sample.denominator_count
    return 0


def _series_colors(data: list[FigureDatum]) -> dict[str, str]:
    return {
        series: PALETTE[index % len(PALETTE)]
        for index, series in enumerate(sorted({item.series for item in data}))
    }


def _scale(value: float, low: float, high: float, target_low: float, target_high: float) -> float:
    if high == low:
        return (target_low + target_high) / 2
    return target_low + (value - low) / (high - low) * (target_high - target_low)


def _short(value: str, limit: int = 22) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _text(
    x: float,
    y: float,
    value: str,
    *,
    anchor: str = "start",
    size: int = 11,
    weight: str = "400",
    fill: str = "#0f172a",
) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
        f'font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}">{escape(value)}</text>'
    )
