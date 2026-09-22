"""Select dataset-wide thresholds and write reproducible audit reports.

The optimizer looks for consecutive stable count plateaus and optionally
rejects low-threshold background plateaus. It never changes QuPath detections.
"""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path

from .version import APP_VERSION, OUTPUT_SCHEMA_VERSION
from typing import Any


def _percentage_change(current: int, previous: int) -> float | None:
    if previous == 0:
        return 0.0 if current == 0 else None
    return abs(current - previous) / previous * 100.0


def _same_thresholds(first: list[float], second: list[float]) -> bool:
    return len(first) == len(second) and all(
        math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1.0e-9)
        for left, right in zip(first, second)
    )


def _automatic_signal_separation(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Estimate a background/positive split from a cumulative threshold curve."""
    if len(rows) < 3:
        return None
    thresholds = [float(row["threshold"]) for row in rows]
    counts = [int(row["total_count"]) for row in rows]
    if counts[0] <= 0:
        return None
    histogram = [max(0, counts[index] - counts[index + 1]) for index in range(len(counts) - 1)]
    histogram.append(max(0, counts[-1]))
    total = sum(histogram)
    if total <= 0:
        return None
    weighted_sum = sum(value * weight for value, weight in zip(thresholds, histogram))
    mean = weighted_sum / total
    total_variance = sum(weight * (value - mean) ** 2 for value, weight in zip(thresholds, histogram)) / total
    background_weight = 0
    background_sum = 0.0
    best_score = -1.0
    best_index: int | None = None
    for index in range(len(thresholds) - 1):
        weight = histogram[index]
        background_weight += weight
        background_sum += thresholds[index] * weight
        foreground_weight = total - background_weight
        if background_weight <= 0 or foreground_weight <= 0:
            continue
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        between_variance = (
            (background_weight / total)
            * (foreground_weight / total)
            * (background_mean - foreground_mean) ** 2
        )
        if between_variance > best_score:
            best_score = between_variance
            best_index = index + 1
    if best_index is None:
        return None
    separability = best_score / total_variance if total_variance > 0 else 0.0
    return {
        "threshold_index": best_index,
        "threshold": thresholds[best_index],
        "positive_count": counts[best_index],
        "positive_fraction_pct": counts[best_index] / counts[0] * 100.0,
        "separability_score": max(0.0, min(1.0, separability)),
        "method": "Otsu separation reconstructed from the dataset-wide cumulative intensity curve",
    }


def calculate_global_thresholds(
    profiles: list[dict[str, Any]],
    *,
    total_change_limit: float = 3.0,
    section_change_limit: float = 5.0,
    required_stable_fraction: float = 0.90,
    minimum_consecutive_steps: int = 3,
    objective: str = "independent_plateaus",
    pair_keys: tuple[str, str] | list[str] | None = None,
    automatic_background_guard: bool = False,
) -> dict[str, Any]:
    """Calculate one global recommendation per channel or channel pair."""
    """Find dataset-wide thresholds from per-section intensity profiles.

    A stable step must satisfy both the total-count and per-section rules. A
    plateau is a consecutive run of stable steps; the selected threshold is the
    highest threshold in the longest qualifying run. With the double-positive
    objective, the selected channel pair is instead jointly selected by maximum
    overlap within both qualifying plateaus. Ties favor higher thresholds.
    """
    if not profiles:
        raise ValueError("No section profiles were supplied.")
    if total_change_limit <= 0 or section_change_limit <= 0:
        raise ValueError("Change limits must be greater than zero.")
    if not 0 < required_stable_fraction <= 1:
        raise ValueError("Required stable-section fraction must be in (0, 1].")
    if minimum_consecutive_steps < 1:
        raise ValueError("Minimum consecutive stable steps must be at least 1.")
    if objective == "maximize_channel1_channel2_overlap":
        # Backward compatibility with reports/settings created before pair selection.
        objective = "maximize_pair_overlap"
        pair_keys = ("channel1", "channel2")
    if objective not in {"independent_plateaus", "maximize_pair_overlap"}:
        raise ValueError(f"Unknown threshold optimization objective: {objective}")
    if objective == "maximize_pair_overlap":
        pair_keys = tuple(pair_keys or ())
        if len(pair_keys) != 2 or pair_keys[0] == pair_keys[1]:
            raise ValueError("Pair-overlap optimization requires two different channel keys.")

    section_ids = [str(profile["section_id"]) for profile in profiles]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("Section identifiers must be unique.")

    channel_keys = [str(channel["key"]) for channel in profiles[0]["channels"]]
    if not channel_keys:
        raise ValueError("Profiles contain no channels.")

    channel_results: list[dict[str, Any]] = []
    for channel_key in channel_keys:
        per_section: list[dict[str, Any]] = []
        reference_thresholds: list[float] | None = None
        channel_name = channel_key

        for profile in profiles:
            channel = next(
                (item for item in profile["channels"] if str(item["key"]) == channel_key),
                None,
            )
            if channel is None:
                raise ValueError(f"Section {profile['section_id']} is missing {channel_key}.")
            thresholds = [float(value) for value in channel["thresholds"]]
            counts = [int(value) for value in channel["counts"]]
            if len(thresholds) != len(counts) or len(thresholds) < 2:
                raise ValueError(f"Invalid threshold profile for {profile['section_id']}.")
            if reference_thresholds is None:
                reference_thresholds = thresholds
                channel_name = str(channel.get("name") or channel_key)
            elif not _same_thresholds(reference_thresholds, thresholds):
                raise ValueError(f"Threshold grids differ for channel {channel_name}.")
            per_section.append(
                {
                    "section_id": str(profile["section_id"]),
                    "counts": counts,
                }
            )

        assert reference_thresholds is not None
        rows: list[dict[str, Any]] = []
        for index, threshold in enumerate(reference_thresholds):
            counts_by_section = {
                item["section_id"]: item["counts"][index] for item in per_section
            }
            total_count = sum(counts_by_section.values())
            if index == 0:
                total_change = None
                stable_sections = 0
                stable_fraction = 0.0
                stable_step = False
            else:
                previous_counts = {
                    item["section_id"]: item["counts"][index - 1] for item in per_section
                }
                previous_total = sum(previous_counts.values())
                total_change = _percentage_change(total_count, previous_total)
                section_changes = [
                    _percentage_change(counts_by_section[key], previous_counts[key])
                    for key in section_ids
                ]
                stable_sections = sum(
                    change is not None and change < section_change_limit
                    for change in section_changes
                )
                stable_fraction = stable_sections / len(section_ids)
                stable_step = (
                    total_change is not None
                    and total_change < total_change_limit
                    and stable_fraction >= required_stable_fraction
                )
            rows.append(
                {
                    "threshold": threshold,
                    "total_count": total_count,
                    "total_change_pct": total_change,
                    "stable_sections": stable_sections,
                    "section_count": len(section_ids),
                    "stable_section_pct": stable_fraction * 100.0,
                    "stable_step": stable_step,
                    "plateau_id": None,
                    "selected": False,
                    "counts_by_section": counts_by_section,
                }
            )

        runs: list[dict[str, int]] = []
        run_start: int | None = None
        for index in range(1, len(rows)):
            if rows[index]["stable_step"]:
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                runs.append({"start_step": run_start, "end_step": index - 1})
                run_start = None
        if run_start is not None:
            runs.append({"start_step": run_start, "end_step": len(rows) - 1})

        for plateau_id, run in enumerate(runs, start=1):
            run["plateau_id"] = plateau_id
            run["stable_steps"] = run["end_step"] - run["start_step"] + 1
            run["start_threshold_index"] = run["start_step"] - 1
            run["end_threshold_index"] = run["end_step"]
            for index in range(run["start_step"], run["end_step"] + 1):
                rows[index]["plateau_id"] = plateau_id

        automatic_separation = (
            _automatic_signal_separation(rows) if automatic_background_guard else None
        )
        minimum_signal_index = (
            int(automatic_separation["threshold_index"])
            if automatic_separation is not None
            else 0
        )
        qualifying_runs = [
            run
            for run in runs
            if run["stable_steps"] >= minimum_consecutive_steps
            and rows[run["start_threshold_index"]]["total_count"] > 0
            and rows[run["end_threshold_index"]]["total_count"] > 0
            and run["end_threshold_index"] >= minimum_signal_index
        ]
        selected_run = (
            max(qualifying_runs, key=lambda run: (run["stable_steps"], run["end_step"]))
            if qualifying_runs
            else None
        )
        selected_threshold = None
        selected_index = None
        selection_source = None
        if selected_run is not None:
            selected_index = selected_run["end_threshold_index"]
            selected_threshold = rows[selected_index]["threshold"]
            rows[selected_index]["selected"] = True
            selection_source = "stable_plateau_after_background_separation"
        elif automatic_separation is not None:
            # A scientifically safer fallback than accepting the saturated
            # low-threshold plateau when no stable post-background run exists.
            selected_index = minimum_signal_index
            selected_threshold = rows[selected_index]["threshold"]
            rows[selected_index]["selected"] = True
            selection_source = "automatic_signal_separation_fallback"

        channel_results.append(
            {
                "key": channel_key,
                "name": channel_name,
                "selected_threshold": selected_threshold,
                "selected_threshold_index": selected_index,
                "plateau": selected_run,
                "plateaus": runs,
                "automatic_signal_separation": automatic_separation,
                "selection_source": selection_source,
                "automatic_guard_warning": (
                    "Weak negative/positive separation; visually validate this threshold."
                    if automatic_separation is not None
                    and automatic_separation["separability_score"] < 0.5
                    else None
                ),
                "range_ends_inside_plateau": bool(
                    selected_run and selected_run["end_threshold_index"] == len(rows) - 1
                ),
                "rows": rows,
            }
        )

    double_positive_result: dict[str, Any] | None = None
    if objective == "maximize_pair_overlap":
        channel_by_key = {channel["key"]: channel for channel in channel_results}
        if any(key not in channel_by_key for key in pair_keys):
            raise ValueError(f"Unknown channel pair: {' + '.join(pair_keys)}")
        first_result, second_result = (channel_by_key[key] for key in pair_keys)
        pair_label = f"{first_result['name']} + {second_result['name']}"
        if first_result["selected_threshold"] is not None and second_result["selected_threshold"] is not None:
            first_thresholds = [float(row["threshold"]) for row in first_result["rows"]]
            second_thresholds = [float(row["threshold"]) for row in second_result["rows"]]
            aggregate_counts = [
                [0 for _ in second_thresholds] for _ in first_thresholds
            ]
            section_matrices: dict[str, list[list[int]]] = {}
            for profile in profiles:
                joint = profile.get("doublePositive")
                if not isinstance(joint, dict):
                    raise ValueError(
                        f"Section {profile['section_id']} has no {pair_label} overlap profile."
                    )
                if (joint.get("firstChannelKey"), joint.get("secondChannelKey")) != pair_keys:
                    raise ValueError(
                        f"Section {profile['section_id']} contains the wrong overlap channel pair."
                    )
                joint_first = [float(value) for value in joint.get("firstThresholds", [])]
                joint_second = [float(value) for value in joint.get("secondThresholds", [])]
                if not _same_thresholds(first_thresholds, joint_first) or not _same_thresholds(
                    second_thresholds, joint_second
                ):
                    raise ValueError(
                        f"Double-positive threshold grids differ for section {profile['section_id']}."
                    )
                matrix = [[int(value) for value in row] for row in joint.get("counts", [])]
                if len(matrix) != len(first_thresholds) or any(
                    len(row) != len(second_thresholds) for row in matrix
                ):
                    raise ValueError(
                        f"Invalid double-positive count grid for section {profile['section_id']}."
                    )
                section_id = str(profile["section_id"])
                section_matrices[section_id] = matrix
                for row_index, row in enumerate(matrix):
                    for column_index, count in enumerate(row):
                        aggregate_counts[row_index][column_index] += count

            def candidate_indexes(channel: dict[str, Any]) -> range:
                separation = channel.get("automatic_signal_separation") or {}
                minimum_index = int(separation.get("threshold_index", 0))
                run = channel.get("plateau")
                if run is None:
                    selected_index = int(channel["selected_threshold_index"])
                    return range(selected_index, selected_index + 1)
                start_index = max(int(run["start_threshold_index"]), minimum_index)
                return range(start_index, int(run["end_threshold_index"]) + 1)

            first_indexes = candidate_indexes(first_result)
            second_indexes = candidate_indexes(second_result)
            candidates = [
                {
                    "first_index": row_index,
                    "second_index": column_index,
                    "first_threshold": first_thresholds[row_index],
                    "second_threshold": second_thresholds[column_index],
                    "total_double_positive": aggregate_counts[row_index][column_index],
                    "counts_by_section": {
                        section_id: matrix[row_index][column_index]
                        for section_id, matrix in section_matrices.items()
                    },
                }
                for row_index in first_indexes
                for column_index in second_indexes
            ]
            selected_pair = max(
                candidates,
                key=lambda item: (
                    item["total_double_positive"],
                    item["first_threshold"],
                    item["second_threshold"],
                ),
            )
            for channel, selected_index in (
                (first_result, selected_pair["first_index"]),
                (second_result, selected_pair["second_index"]),
            ):
                for row in channel["rows"]:
                    row["selected"] = False
                channel["rows"][selected_index]["selected"] = True
                channel["selected_threshold_index"] = selected_index
                channel["selected_threshold"] = channel["rows"][selected_index]["threshold"]
                channel["selection_reason"] = f"maximum {pair_label} overlap inside stable plateaus"
            double_positive_result = {
                "first_channel_key": pair_keys[0],
                "second_channel_key": pair_keys[1],
                "first_channel_name": first_result["name"],
                "second_channel_name": second_result["name"],
                "selected_first_threshold": selected_pair["first_threshold"],
                "selected_second_threshold": selected_pair["second_threshold"],
                "total_double_positive": selected_pair["total_double_positive"],
                "counts_by_section": selected_pair["counts_by_section"],
                "candidate_pair_count": len(candidates),
                "restricted_to_stable_plateaus": bool(
                    first_result.get("plateau") and second_result.get("plateau")
                ),
                "automatic_background_guard": automatic_background_guard,
                "candidates": candidates,
            }

    return {
        "version": APP_VERSION,
        "method": (
            f"maximum {' + '.join(pair_keys)} double-positive count inside both dataset-wide stable plateaus"
            if objective == "maximize_pair_overlap"
            else "highest threshold in the longest dataset-wide stable plateau"
        ),
        "objective": objective,
        "section_count": len(section_ids),
        "sections": section_ids,
        "parameters": {
            "total_change_limit_pct": total_change_limit,
            "section_change_limit_pct": section_change_limit,
            "required_stable_sections_pct": required_stable_fraction * 100.0,
            "minimum_consecutive_stable_steps": minimum_consecutive_steps,
            "automatic_background_guard": automatic_background_guard,
        },
        "channels": channel_results,
        "double_positive_optimization": double_positive_result,
    }


def _format_number(value: Any, decimals: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _chart_svg(channel: dict[str, Any], width: int = 900, height: int = 330) -> str:
    rows = channel["rows"]
    margin_left, margin_right, margin_top, margin_bottom = 72, 24, 24, 54
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    thresholds = [float(row["threshold"]) for row in rows]
    counts = [int(row["total_count"]) for row in rows]
    minimum_threshold, maximum_threshold = min(thresholds), max(thresholds)
    maximum_count = max(counts) if counts else 1
    threshold_span = maximum_threshold - minimum_threshold or 1.0
    count_span = maximum_count or 1

    def x_position(value: float) -> float:
        return margin_left + (value - minimum_threshold) / threshold_span * plot_width

    def y_position(value: int) -> float:
        return margin_top + plot_height - value / count_span * plot_height

    points = " ".join(
        f"{x_position(threshold):.1f},{y_position(count):.1f}"
        for threshold, count in zip(thresholds, counts)
    )
    plateau_rect = ""
    selected_line = ""
    plateau = channel.get("plateau")
    if plateau:
        start_threshold = thresholds[plateau["start_threshold_index"]]
        end_threshold = thresholds[plateau["end_threshold_index"]]
        x1, x2 = x_position(start_threshold), x_position(end_threshold)
        plateau_rect = (
            f'<rect x="{x1:.1f}" y="{margin_top}" width="{max(2.0, x2 - x1):.1f}" '
            f'height="{plot_height}" fill="#c9f2d4" opacity="0.65"/>'
        )
        selected_x = x_position(float(channel["selected_threshold"]))
        selected_line = (
            f'<line x1="{selected_x:.1f}" y1="{margin_top}" x2="{selected_x:.1f}" '
            f'y2="{margin_top + plot_height}" stroke="#d62f2f" stroke-width="3"/>'
        )

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="Threshold stability chart">
  <rect width="{width}" height="{height}" fill="white"/>
  {plateau_rect}
  <line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" stroke="#555"/>
  <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#555"/>
  <polyline points="{points}" fill="none" stroke="#1769aa" stroke-width="3"/>
  {selected_line}
  <text x="{margin_left}" y="{height - 17}" font-size="14">{minimum_threshold:g}</text>
  <text x="{width - margin_right}" y="{height - 17}" text-anchor="end" font-size="14">{maximum_threshold:g}</text>
  <text x="{width / 2}" y="{height - 12}" text-anchor="middle" font-size="14">Threshold</text>
  <text x="{margin_left - 10}" y="{margin_top + 5}" text-anchor="end" font-size="14">{maximum_count}</text>
  <text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" text-anchor="middle" font-size="14">Total positive objects</text>
</svg>
""".strip()


def write_threshold_reports(result: dict[str, Any], output_directory: Path) -> dict[str, str]:
    """Persist CSV, JSON, and HTML evidence for a threshold decision."""
    output_directory.mkdir(parents=True, exist_ok=True)
    csv_path = output_directory / f"global_threshold_optimization_v{OUTPUT_SCHEMA_VERSION}.csv"
    json_path = output_directory / f"global_threshold_optimization_v{OUTPUT_SCHEMA_VERSION}.json"
    html_path = output_directory / f"global_threshold_optimization_v{OUTPUT_SCHEMA_VERSION}.html"
    overlap_csv_path = output_directory / f"channel_pair_double_positive_optimization_v{OUTPUT_SCHEMA_VERSION}.csv"

    section_ids = result["sections"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = [
            "Channel",
            "Threshold",
            "Total_Count",
            "Total_Change_Percent",
            "Stable_Sections",
            "Section_Count",
            "Stable_Section_Percent",
            "Stable_Step",
            "Plateau_ID",
            "Selected_Optimal",
            *section_ids,
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for channel in result["channels"]:
            for row in channel["rows"]:
                output_row = {
                    "Channel": channel["name"],
                    "Threshold": row["threshold"],
                    "Total_Count": row["total_count"],
                    "Total_Change_Percent": _format_number(row["total_change_pct"]),
                    "Stable_Sections": row["stable_sections"],
                    "Section_Count": row["section_count"],
                    "Stable_Section_Percent": _format_number(row["stable_section_pct"]),
                    "Stable_Step": row["stable_step"],
                    "Plateau_ID": row["plateau_id"] or "",
                    "Selected_Optimal": row["selected"],
                }
                output_row.update(row["counts_by_section"])
                writer.writerow(output_row)

    double_positive = result.get("double_positive_optimization")
    if double_positive:
        with overlap_csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            fieldnames = [
                "First_Channel",
                "Second_Channel",
                "First_Channel_Threshold",
                "Second_Channel_Threshold",
                "Total_Double_Positive",
                "Selected_Optimal",
                *section_ids,
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            selected_pair = (
                float(double_positive["selected_first_threshold"]),
                float(double_positive["selected_second_threshold"]),
            )
            for candidate in double_positive["candidates"]:
                current_pair = (
                    float(candidate["first_threshold"]),
                    float(candidate["second_threshold"]),
                )
                output_row = {
                    "First_Channel": double_positive["first_channel_name"],
                    "Second_Channel": double_positive["second_channel_name"],
                    "First_Channel_Threshold": candidate["first_threshold"],
                    "Second_Channel_Threshold": candidate["second_threshold"],
                    "Total_Double_Positive": candidate["total_double_positive"],
                    "Selected_Optimal": current_pair == selected_pair,
                }
                output_row.update(candidate["counts_by_section"])
                writer.writerow(output_row)

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    channel_sections = []
    for channel in result["channels"]:
        selected = channel.get("selected_threshold")
        if selected is None:
            decision = "No qualifying plateau found"
        else:
            decision = f"Selected global threshold: {selected:g}"
        warning = (
            "<p class='warning'>The selected plateau reaches the end of the tested range. "
            "Extend the range before locking the threshold.</p>"
            if channel.get("range_ends_inside_plateau")
            else ""
        )
        separation = channel.get("automatic_signal_separation")
        separation_note = ""
        if separation:
            separation_note = (
                "<p><strong>Automatic background/positive split:</strong> "
                f"{separation['threshold']:g}; positive fraction "
                f"{separation['positive_fraction_pct']:.1f}%; separability score "
                f"{separation['separability_score']:.2f}. Selection source: "
                f"{html.escape(str(channel.get('selection_source') or 'none'))}.</p>"
            )
            if channel.get("automatic_guard_warning"):
                separation_note += (
                    f"<p class='warning'>{html.escape(str(channel['automatic_guard_warning']))}</p>"
                )
        channel_sections.append(
            f"""
<section>
  <h2>{html.escape(str(channel['name']))}</h2>
  <p class="decision">{html.escape(decision)}</p>
  {warning}
  {separation_note}
  {_chart_svg(channel)}
</section>
"""
        )

    parameters = result["parameters"]
    if double_positive:
        pair_constraint = (
            "The search was restricted to threshold pairs inside both channels' qualifying stable plateaus."
            if double_positive["restricted_to_stable_plateaus"]
            else "At least one channel had no qualifying post-background plateau, so its automatic signal-separation threshold was used as a fallback."
        )
        overlap_summary = (
            f"<section><h2>{html.escape(str(double_positive['first_channel_name']))} + "
            f"{html.escape(str(double_positive['second_channel_name']))} double-positive optimization</h2>"
            f"<p class='decision'>Selected thresholds: {html.escape(str(double_positive['first_channel_name']))} = "
            f"{double_positive['selected_first_threshold']:g}, {html.escape(str(double_positive['second_channel_name']))} = "
            f"{double_positive['selected_second_threshold']:g}</p>"
            f"<p>Total double-positive objects: <strong>{double_positive['total_double_positive']}</strong>. "
            f"{pair_constraint}</p>"
            "</section>"
        )
    elif result.get("objective") == "maximize_pair_overlap":
        overlap_summary = (
            "<section><h2>Channel-pair double-positive optimization</h2>"
            "<p class='warning'>No overlap recommendation was possible because one or both channels had no qualifying stable plateau.</p>"
            "</section>"
        )
    else:
        overlap_summary = ""
    html_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Global threshold optimization</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 28px auto; max-width: 1000px; color: #1f2933; }}
h1, h2 {{ color: #153e63; }}
section {{ border: 1px solid #ccd6df; border-radius: 10px; padding: 18px; margin: 20px 0; }}
.decision {{ font-size: 1.15rem; font-weight: 700; }}
.warning {{ color: #9a5700; font-weight: 600; }}
.rules {{ background: #f3f7fa; padding: 14px 18px; border-radius: 8px; }}
svg {{ width: 100%; height: auto; }}
</style>
</head>
<body>
<h1>Dataset-wide global threshold optimization</h1>
<p>Sections analyzed: <strong>{result['section_count']}</strong></p>
<div class="rules">
Total change &lt; {parameters['total_change_limit_pct']:g}% and at least
{parameters['required_stable_sections_pct']:g}% of sections change by less than
{parameters['section_change_limit_pct']:g}%, for at least
{parameters['minimum_consecutive_stable_steps']} consecutive threshold steps.
</div>
{overlap_summary}
{''.join(channel_sections)}
<p>The selected value is the highest threshold in the longest qualifying stable plateau. It is not the threshold with the highest cell count.</p>
</body>
</html>
""",
        encoding="utf-8",
    )
    reports = {"csv": str(csv_path), "json": str(json_path), "html": str(html_path)}
    if double_positive:
        reports["double_positive_csv"] = str(overlap_csv_path)
    return reports
