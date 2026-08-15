#!/usr/bin/env python
"""Compare normalized path-level timing results from Innovus and PrimeTime.

Compatible with Python 2.7 and Python 3.x so it can run in older EDA Linux
environments where the `python` command still points to Python 2.7.
"""

from __future__ import print_function

import argparse
import csv
import glob
import io
import math
import os
import re
import sys
from collections import defaultdict


PY2 = sys.version_info[0] == 2
try:
    TEXT_TYPE = unicode
except NameError:
    TEXT_TYPE = str

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


class TimingPath(object):
    def __init__(
        self,
        tool,
        source_report,
        view,
        check_type,
        compare_group,
        report_rank,
        startpoint="",
        endpoint="",
        tool_path_group="",
        launch_clock="",
        capture_clock="",
        arrival=None,
        required=None,
        slack=None,
    ):
        self.tool = tool
        self.source_report = source_report
        self.view = view
        self.check_type = check_type
        self.compare_group = compare_group
        self.report_rank = report_rank
        self.startpoint = startpoint
        self.endpoint = endpoint
        self.tool_path_group = tool_path_group
        self.launch_clock = launch_clock
        self.capture_clock = capture_clock
        self.arrival = arrival
        self.required = required
        self.slack = slack

    @property
    def base_key(self):
        return (
            self.view,
            self.check_type,
            self.compare_group,
            self.startpoint,
            self.endpoint,
        )


def parse_report_identity(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split("__", 3)
    if len(parts) != 4:
        raise ValueError(
            "Unexpected report name '{0}'. Expected "
            "tool__view__check__group.rpt".format(os.path.basename(path))
        )
    tool, view, check_type, group = parts
    if tool not in ("innovus", "primetime"):
        raise ValueError("Unknown tool '{0}' in {1}".format(tool, os.path.basename(path)))
    if check_type not in ("setup", "hold"):
        raise ValueError(
            "Unknown check type '{0}' in {1}".format(check_type, os.path.basename(path))
        )
    return tool, view, check_type, group


def as_float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def final_number(line):
    match = re.search(r"({0})\s*$".format(NUMBER), line)
    return as_float(match.group(1)) if match else None


def clock_from_description(line):
    patterns = (
        r"clocked by\s+['\"]?([^'\"\s\)]+)",
        r"(?:checked with|triggered by).*?edge of\s+['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def parse_primetime(path, view, check_type, group):
    paths = []
    current = None

    def finish(current_path):
        if (
            current_path
            and current_path.startpoint
            and current_path.endpoint
            and current_path.slack is not None
        ):
            paths.append(current_path)

    with io.open(path, "r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            start_match = re.match(r"\s*Startpoint:\s+(\S+)", line, re.IGNORECASE)
            if start_match:
                finish(current)
                current = TimingPath(
                    tool="primetime",
                    source_report=path,
                    view=view,
                    check_type=check_type,
                    compare_group=group,
                    report_rank=len(paths) + 1,
                    startpoint=start_match.group(1),
                    launch_clock=clock_from_description(line),
                )
                continue
            if current is None:
                continue

            endpoint_match = re.match(r"\s*Endpoint:\s+(\S+)", line, re.IGNORECASE)
            if endpoint_match:
                current.endpoint = endpoint_match.group(1)
                current.capture_clock = clock_from_description(line)
                continue

            group_match = re.match(r"\s*Path Group:\s*(.+?)\s*$", line, re.IGNORECASE)
            if group_match:
                current.tool_path_group = group_match.group(1).strip()
                continue

            if re.match(r"\s*data arrival time\b", line, re.IGNORECASE):
                # PrimeTime repeats arrival in the final slack equation with
                # a subtraction sign. Keep the first, absolute arrival value.
                if current.arrival is None:
                    current.arrival = final_number(line)
                continue
            if re.match(r"\s*data required time\b", line, re.IGNORECASE):
                if current.required is None:
                    current.required = final_number(line)
                continue
            if re.match(r"\s*slack(?:\s|\()", line, re.IGNORECASE):
                current.slack = final_number(line)

    finish(current)
    return paths


def parse_innovus(path, view, check_type, group):
    paths = []
    current = None

    def finish(current_path):
        if (
            current_path
            and current_path.startpoint
            and current_path.endpoint
            and current_path.slack is not None
        ):
            paths.append(current_path)

    with io.open(path, "r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            path_match = re.match(r"\s*Path\s+(\d+)\s*:", line, re.IGNORECASE)
            if path_match:
                finish(current)
                current = TimingPath(
                    tool="innovus",
                    source_report=path,
                    view=view,
                    check_type=check_type,
                    compare_group=group,
                    report_rank=int(path_match.group(1)),
                )
                continue
            if current is None:
                continue

            endpoint_match = re.match(r"\s*Endpoint:\s+(\S+)", line, re.IGNORECASE)
            if endpoint_match:
                current.endpoint = endpoint_match.group(1)
                current.capture_clock = clock_from_description(line)
                continue

            start_match = re.match(r"\s*Beginpoint:\s+(\S+)", line, re.IGNORECASE)
            if start_match:
                current.startpoint = start_match.group(1)
                current.launch_clock = clock_from_description(line)
                continue

            group_match = re.match(r"\s*Path Groups?:\s*(.+?)\s*$", line, re.IGNORECASE)
            if group_match:
                current.tool_path_group = group_match.group(1).strip().strip("{}")
                continue

            if re.match(r"\s*=?\s*Required Time\b", line, re.IGNORECASE):
                current.required = final_number(line)
                continue
            if re.match(r"\s*-?\s*Arrival Time\b", line, re.IGNORECASE):
                current.arrival = final_number(line)
                continue
            if re.match(r"\s*=?\s*Slack Time\b", line, re.IGNORECASE):
                current.slack = final_number(line)

    finish(current)
    return paths


def load_reports(input_dir):
    all_paths = []
    pattern = os.path.join(input_dir, "*", "*.rpt")
    for report_path in sorted(glob.glob(pattern)):
        tool, view, check_type, group = parse_report_identity(report_path)
        if tool == "primetime":
            all_paths.extend(parse_primetime(report_path, view, check_type, group))
        else:
            all_paths.extend(parse_innovus(report_path, view, check_type, group))
    return all_paths


def value_or_blank(value):
    return "" if value is None else "{0:.6f}".format(value)


def delta(left, right, scale):
    if left is None or right is None:
        return None
    return (left - right) * scale


def classify(abs_delta_ps, warn_ps, fail_ps):
    if abs_delta_ps is None:
        return "NO_SLACK_DATA"
    if abs_delta_ps <= warn_ps:
        return "PASS"
    if abs_delta_ps <= fail_ps:
        return "WARNING"
    return "FAIL"


def timing_sort_key(item):
    slack = float("inf") if item.slack is None else item.slack
    return slack, item.report_rank


def pair_paths(paths):
    by_tool = {
        "innovus": defaultdict(list),
        "primetime": defaultdict(list),
    }
    for timing_path in paths:
        by_tool[timing_path.tool][timing_path.base_key].append(timing_path)

    keys = sorted(set(by_tool["innovus"]) | set(by_tool["primetime"]))
    pairs = []
    for key in keys:
        innovus = sorted(by_tool["innovus"].get(key, []), key=timing_sort_key)
        primetime = sorted(by_tool["primetime"].get(key, []), key=timing_sort_key)
        count = max(len(innovus), len(primetime))
        for index in range(count):
            pairs.append(
                (
                    innovus[index] if index < len(innovus) else None,
                    primetime[index] if index < len(primetime) else None,
                )
            )
    return pairs


COMPARISON_FIELDS = [
    "view",
    "check_type",
    "compare_group",
    "startpoint",
    "endpoint",
    "pair_rank",
    "innovus_path_group",
    "primetime_path_group",
    "innovus_launch_clock",
    "primetime_launch_clock",
    "innovus_capture_clock",
    "primetime_capture_clock",
    "innovus_arrival",
    "primetime_arrival",
    "delta_arrival_ps",
    "innovus_required",
    "primetime_required",
    "delta_required_ps",
    "innovus_slack",
    "primetime_slack",
    "delta_slack_ps",
    "abs_delta_slack_ps",
    "status",
]


def comparison_rows(pairs, scale, warn_ps, fail_ps):
    rows = []
    key_ranks = defaultdict(int)
    for innovus, primetime in pairs:
        exemplar = innovus or primetime
        key_ranks[exemplar.base_key] += 1
        pair_rank = key_ranks[exemplar.base_key]

        if innovus is None:
            status = "MISSING_INNOVUS"
        elif primetime is None:
            status = "MISSING_PRIMETIME"
        else:
            slack_difference = delta(innovus.slack, primetime.slack, scale)
            status = classify(
                None if slack_difference is None else abs(slack_difference),
                warn_ps,
                fail_ps,
            )

        arrival_delta = delta(
            None if innovus is None else innovus.arrival,
            None if primetime is None else primetime.arrival,
            scale,
        )
        required_delta = delta(
            None if innovus is None else innovus.required,
            None if primetime is None else primetime.required,
            scale,
        )
        slack_delta = delta(
            None if innovus is None else innovus.slack,
            None if primetime is None else primetime.slack,
            scale,
        )

        rows.append(
            {
                "view": exemplar.view,
                "check_type": exemplar.check_type,
                "compare_group": exemplar.compare_group,
                "startpoint": exemplar.startpoint,
                "endpoint": exemplar.endpoint,
                "pair_rank": pair_rank,
                "innovus_path_group": "" if innovus is None else innovus.tool_path_group,
                "primetime_path_group": "" if primetime is None else primetime.tool_path_group,
                "innovus_launch_clock": "" if innovus is None else innovus.launch_clock,
                "primetime_launch_clock": "" if primetime is None else primetime.launch_clock,
                "innovus_capture_clock": "" if innovus is None else innovus.capture_clock,
                "primetime_capture_clock": "" if primetime is None else primetime.capture_clock,
                "innovus_arrival": value_or_blank(None if innovus is None else innovus.arrival),
                "primetime_arrival": value_or_blank(None if primetime is None else primetime.arrival),
                "delta_arrival_ps": "" if arrival_delta is None else "{0:.3f}".format(arrival_delta),
                "innovus_required": value_or_blank(None if innovus is None else innovus.required),
                "primetime_required": value_or_blank(None if primetime is None else primetime.required),
                "delta_required_ps": "" if required_delta is None else "{0:.3f}".format(required_delta),
                "innovus_slack": value_or_blank(None if innovus is None else innovus.slack),
                "primetime_slack": value_or_blank(None if primetime is None else primetime.slack),
                "delta_slack_ps": "" if slack_delta is None else "{0:.3f}".format(slack_delta),
                "abs_delta_slack_ps": "" if slack_delta is None else "{0:.3f}".format(abs(slack_delta)),
                "status": status,
            }
        )
    return rows


def csv_value(value):
    if PY2 and isinstance(value, TEXT_TYPE):
        return value.encode("utf-8")
    return value


def write_csv(path, fieldnames, rows):
    if PY2:
        stream = open(path, "wb")
    else:
        stream = open(path, "w", newline="")
    try:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict((key, csv_value(value)) for key, value in row.items()))
    finally:
        stream.close()


SUMMARY_FIELDS = [
    "view",
    "check_type",
    "compare_group",
    "innovus_paths",
    "primetime_paths",
    "matched_paths",
    "missing_innovus",
    "missing_primetime",
    "pass",
    "warning",
    "fail",
    "innovus_wns",
    "primetime_wns",
    "delta_wns_ps",
    "mean_abs_delta_slack_ps",
    "max_abs_delta_slack_ps",
]


def summarize(rows, scale):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["view"], row["check_type"], row["compare_group"])].append(row)

    summaries = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        innovus_slacks = [
            float(row["innovus_slack"])
            for row in group_rows
            if row["innovus_slack"] != ""
        ]
        primetime_slacks = [
            float(row["primetime_slack"])
            for row in group_rows
            if row["primetime_slack"] != ""
        ]
        abs_deltas = [
            float(row["abs_delta_slack_ps"])
            for row in group_rows
            if row["abs_delta_slack_ps"] != ""
        ]
        statuses = [row["status"] for row in group_rows]
        innovus_wns = min(innovus_slacks) if innovus_slacks else None
        primetime_wns = min(primetime_slacks) if primetime_slacks else None
        wns_delta = None
        if innovus_wns is not None and primetime_wns is not None:
            wns_delta = (innovus_wns - primetime_wns) * scale

        summaries.append(
            {
                "view": key[0],
                "check_type": key[1],
                "compare_group": key[2],
                "innovus_paths": len(innovus_slacks),
                "primetime_paths": len(primetime_slacks),
                "matched_paths": sum(
                    status not in ("MISSING_INNOVUS", "MISSING_PRIMETIME")
                    for status in statuses
                ),
                "missing_innovus": statuses.count("MISSING_INNOVUS"),
                "missing_primetime": statuses.count("MISSING_PRIMETIME"),
                "pass": statuses.count("PASS"),
                "warning": statuses.count("WARNING"),
                "fail": statuses.count("FAIL"),
                "innovus_wns": "" if innovus_wns is None else "{0:.6f}".format(innovus_wns),
                "primetime_wns": "" if primetime_wns is None else "{0:.6f}".format(primetime_wns),
                "delta_wns_ps": "" if wns_delta is None else "{0:.3f}".format(wns_delta),
                "mean_abs_delta_slack_ps": ""
                if not abs_deltas
                else "{0:.3f}".format(sum(abs_deltas) / float(len(abs_deltas))),
                "max_abs_delta_slack_ps": ""
                if not abs_deltas
                else "{0:.3f}".format(max(abs_deltas)),
            }
        )
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, help="Root containing innovus/ and primetime/ reports"
    )
    parser.add_argument("--output", required=True, help="Directory for comparison CSV files")
    parser.add_argument("--input-unit", choices=("ns", "ps"), default="ns")
    parser.add_argument("--warn-ps", type=float, default=10.0)
    parser.add_argument("--fail-ps", type=float, default=30.0)
    args = parser.parse_args()

    if args.warn_ps < 0 or args.fail_ps < args.warn_ps:
        parser.error("Require 0 <= --warn-ps <= --fail-ps")

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    scale = 1000.0 if args.input_unit == "ns" else 1.0
    paths = load_reports(input_dir)
    if not paths:
        raise SystemExit("No timing paths parsed below {0}".format(input_dir))

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    pairs = pair_paths(paths)
    rows = comparison_rows(pairs, scale, args.warn_ps, args.fail_ps)
    summaries = summarize(rows, scale)

    comparison_file = os.path.join(output_dir, "path_comparison.csv")
    summary_file = os.path.join(output_dir, "summary.csv")
    unmatched_file = os.path.join(output_dir, "unmatched_paths.csv")
    write_csv(comparison_file, COMPARISON_FIELDS, rows)
    write_csv(summary_file, SUMMARY_FIELDS, summaries)
    write_csv(
        unmatched_file,
        COMPARISON_FIELDS,
        (row for row in rows if row["status"].startswith("MISSING_")),
    )

    print("Python version      : {0}".format(sys.version.split()[0]))
    print("Parsed timing paths : {0}".format(len(paths)))
    print("Compared rows       : {0}".format(len(rows)))
    print("Summary             : {0}".format(summary_file))
    print("Path comparison     : {0}".format(comparison_file))
    print("Unmatched paths     : {0}".format(unmatched_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

