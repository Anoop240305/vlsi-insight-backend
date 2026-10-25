from math import isfinite


TIMING_STATES = {
    "NOT_ANALYZED",
    "MISSING_CONSTRAINTS",
    "INCOMPLETE_ANALYSIS",
    "ANALYZED_PASS",
    "ANALYZED_FAIL",
    "NOT_APPLICABLE",
}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _metric(metrics, key):
    value = metrics.get(key)
    return value if _finite(value) else None


def _resource_map(report):
    if not report:
        return {}
    return _resource_map_from_metrics(report.parsed_metrics or {})


def _resource_map_from_metrics(metrics):
    resources = metrics.get("resources")
    if isinstance(resources, dict):
        return resources
    return {
        name: metrics.get(key, {})
        for name, key in (
            ("Slice LUTs", "slice_luts"),
            ("Slice Registers", "slice_registers"),
            ("Block RAM Tiles", "block_ram_tiles"),
            ("DSP Blocks", "dsp_blocks"),
            ("Bonded IOBs", "bonded_iobs"),
        )
        if isinstance(metrics.get(key), dict)
    } | {
        name: {"used": metrics.get(key), "available": None}
        for name, key in (
            ("LUT as Logic", "lut_as_logic_used"),
            ("LUT as Memory", "lut_as_memory_used"),
            ("Register as Flip-Flop", "register_as_flip_flop_used"),
            ("Register as Latch", "register_as_latch_used"),
            ("F7 Muxes", "f7_muxes_used"), ("F8 Muxes", "f8_muxes_used"),
            ("BUFGCTRL", "clock_buffers_bufgctrl_used"),
            ("Slice resources", "slice_resources_used"),
        ) if _finite(metrics.get(key))
    }


def _normalized_report_metrics(report):
    normalized = getattr(report, "normalized_metrics", None) or {}
    if not normalized:
        return report.parsed_metrics or {}
    metrics = {}
    for path, item in normalized.items():
        parts = path.split(".")
        cursor = metrics
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = item.get("value") if isinstance(item, dict) else item
    return metrics


def timing_readiness(timing_report, utilization_report=None):
    if not timing_report:
        return {
            "state": "NOT_ANALYZED",
            "explanation": "No timing report has been parsed for this design.",
            "next_action": "Upload a Vivado timing summary or detailed timing report.",
            "report_id": None,
        }

    metrics = timing_report.parsed_metrics or {}
    coverage = timing_report.analysis_coverage or {}
    if metrics.get("timing_constraints_detected") is False:
        util_metrics = utilization_report.parsed_metrics if utilization_report else {}
        util_registers = util_metrics.get("slice_registers")
        util_registers = util_registers if isinstance(util_registers, dict) else {}
        run_matches = (
            utilization_report
            and utilization_report.run_id == timing_report.run_id
            and utilization_report.stage == timing_report.stage
            and utilization_report.device_part
            and utilization_report.device_part == timing_report.device_part
            and timing_report.stage != "unknown"
        )
        no_sequential_resources = (
            run_matches
            and util_registers.get("used") == 0
            and util_metrics.get("register_as_flip_flop_used") == 0
            and util_metrics.get("register_as_latch_used") == 0
        )
        check_counts = coverage.get("check_timing_issue_counts", {})
        no_unconstrained_paths = (
            check_counts.get("no_clock") == 0
            and check_counts.get("unconstrained_internal_endpoints") == 0
            and coverage.get("unconstrained_internal_endpoints") == 0
        )
        if no_sequential_resources and no_unconstrained_paths:
            return {
                "state": "NOT_APPLICABLE",
                "explanation": "The matching report run shows no sequential resources or unconstrained internal endpoints; a clock constraint is not required for the reported design contents.",
                "next_action": "Review input/output delay requirements if this combinational block is timed at its interfaces.",
                "report_id": timing_report.pk,
            }
        return {
            "state": "MISSING_CONSTRAINTS",
            "explanation": "Vivado explicitly reports that no user timing constraints were specified. Slack values are unavailable, so timing is unknown rather than passed.",
            "next_action": "Review clock definitions and input/output delay constraints where applicable, then rerun report_timing_summary.",
            "report_id": timing_report.pk,
        }

    if metrics.get("timing_constraints_detected") is not True:
        return {
            "state": "INCOMPLETE_ANALYSIS",
            "explanation": "The report does not establish whether timing constraints were detected.",
            "next_action": "Inspect the source report's constraints and coverage sections, then generate a complete timing summary.",
            "report_id": timing_report.pk,
        }

    observed = {
        key: _metric(metrics, key)
        for key in ("wns_ns", "tns_ns", "whs_ns", "ths_ns", "wpws_ns", "tpws_ns", "slack_ns")
    }
    failing = {key: value for key, value in observed.items() if value is not None and value < 0}
    if failing:
        return {
            "state": "ANALYZED_FAIL",
            "explanation": "Vivado reports one or more negative timing slack values.",
            "next_action": "Review the reported violating paths and the constraints used for this analysis.",
            "report_id": timing_report.pk,
            "violations": failing,
        }

    command = str(timing_report.source_metadata.get("command", "")).strip().lower()
    summary_report = command.startswith("report_timing_summary")
    complete_setup = observed["wns_ns"] is not None and observed["tns_ns"] is not None
    complete_coverage = (
        coverage.get("setup_total_endpoints") is not None
        and coverage.get("check_timing_checks_reported", 0) > 0
    )
    coverage_issues = coverage.get("check_timing_issue_counts", {})
    relevant_issues = sum(
        int(coverage_issues.get(key, 0) or 0)
        for key in ("no_clock", "unconstrained_internal_endpoints", "no_input_delay", "no_output_delay")
    )
    if summary_report and complete_setup and complete_coverage and relevant_issues == 0:
        return {
            "state": "ANALYZED_PASS",
            "explanation": "Vivado reported constrained setup timing results with non-negative WNS/TNS and no relevant check_timing issues.",
            "next_action": "Keep the timing constraints and rerun this summary after design changes.",
            "report_id": timing_report.pk,
        }

    reason = (
        "The report is a single-path timing report, which cannot establish design-wide timing pass status."
        if not summary_report
        else "The timing summary is missing setup metrics, endpoint coverage, or reports check_timing coverage issues."
    )
    return {
        "state": "INCOMPLETE_ANALYSIS",
        "explanation": reason,
        "next_action": "Resolve reported coverage issues and generate a complete report_timing_summary.",
        "report_id": timing_report.pk,
    }


def _finding(severity, category, code, title, metric, evidence, explanation, causes, next_step, verification):
    return {
        "severity": severity,
        "category": category,
        "code": code,
        "title": title,
        "metric": metric,
        "measured_evidence": evidence,
        "explanation": explanation,
        "likely_causes": causes,
        "recommended_next_step": next_step,
        "verification_criteria": verification,
    }


def interpret_report(report, report_type=None, readiness=None):
    """Build concise deterministic, source-backed findings for one report."""
    if not report:
        messages = {
            "timing": ("Timing not analyzed", "No timing report is available for the selected run.", "Upload a Vivado timing summary for this run."),
            "utilization": ("Utilization not analyzed", "No utilization report is available for the selected run.", "Upload a Vivado utilization report for this run."),
            "power": ("Power not analyzed", "No power report is available for the selected run.", "Upload a Vivado power report for this run."),
        }
        title, explanation, action = messages.get(report_type, ("Analysis not available", "No source report is available.", "Upload the relevant Vivado report."))
        if report_type == "timing" and readiness and readiness.get("state") != "NOT_ANALYZED":
            explanation = readiness.get("explanation", explanation)
            action = readiness.get("next_action", action)
        return [{
            "title": title, "severity": "informational", "observed_metric": "Report",
            "observed_value": None, "unit": None, "observation": explanation,
            "engineering_interpretation": "No numeric assessment is possible without report data.",
            "why_it_matters": "Missing evidence is not treated as a zero or a passing result.",
            "recommended_next_step": action, "evidence": {"source_report": None},
            "confidence_or_limitations": "No source report is available for the selected run.",
        }]
    metrics = _normalized_report_metrics(report)
    source = report.name
    stage = report.stage
    findings = []

    def add(severity, title, metric, value, unit, interpretation, why, next_step, confidence=None):
        findings.append({
            "title": title,
            "severity": severity,
            "observed_metric": metric,
            "observed_value": value,
            "unit": unit,
            "observation": f"{metric}: {value} {unit or ''}".strip(),
            "engineering_interpretation": interpretation,
            "why_it_matters": why,
            "recommended_next_step": next_step,
            "evidence": {
                "source_report": source, "stage": stage,
                "project_id": getattr(report, "project_id", None),
                "run_id": str(getattr(report, "run_id", "")) or None,
                "device_part": getattr(report, "device_part", None),
                "extraction_status": getattr(report, "status", "completed"),
                "value": value, "unit": unit,
            },
            "confidence_or_limitations": confidence,
        })

    if report.report_type == "timing":
        groups = (
            ("setup", ("wns_ns", "tns_ns"), "Setup slack", "ns"),
            ("hold", ("whs_ns", "ths_ns"), "Hold slack", "ns"),
            ("pulse width", ("wpws_ns", "tpws_ns"), "Pulse-width slack", "ns"),
        )
        for label, keys, title, unit in groups:
            observed = {key: _metric(metrics, key) for key in keys}
            observed = {key: value for key, value in observed.items() if value is not None}
            if not observed:
                continue
            violations = {key: value for key, value in observed.items() if value < 0}
            if violations:
                severity = "critical"
                interpretation = f"Vivado reports negative {label} slack; the corresponding timing checks fail."
                action = f"Inspect the reported failing {label} paths, clock relationships, and applied constraints."
            elif all(value == 0 for value in observed.values()):
                severity = "informational"
                interpretation = f"The report shows no accumulated negative {label} slack for these summary metrics."
                action = f"Review path coverage before treating {label} timing as closed."
            else:
                severity = "informational"
                interpretation = f"Reported {label} slack is non-negative for the values present."
                action = f"Review the analysis coverage and constraints for the reported {label} checks."
            add(severity, title, ", ".join(k.upper() for k in observed), observed, unit,
                interpretation, "Slack compares a path's required time with its measured arrival time.", action,
                "Summary evidence only; path details and analysis coverage determine design-wide closure.")
        path_slack = _metric(metrics, "slack_ns")
        if path_slack is not None:
            path = {
                "slack_ns": path_slack,
                "startpoint": metrics.get("startpoint"),
                "endpoint": metrics.get("endpoint"),
                "path_group": metrics.get("path_group"),
                "data_arrival_time_ns": _metric(metrics, "data_arrival_time_ns"),
                "data_required_time_ns": _metric(metrics, "data_required_time_ns"),
            }
            add("critical" if path_slack < 0 else "informational", "Reported timing path", "Path slack", path, "ns",
                "Vivado reports this path slack and the associated path fields where available.",
                "A single path describes only that path and does not establish design-wide timing closure.",
                "Inspect this path's startpoint, endpoint, path group, and applied timing constraints.",
                "Path details are limited to fields present in the source timing report.")
        periods = (report.analysis_coverage or {}).get("clock_periods_ns", {})
        valid_periods = {name: period for name, period in periods.items() if _finite(period) and period > 0}
        if valid_periods:
            frequencies = {name: round(1000 / period, 3) for name, period in valid_periods.items()}
            clock_text = f"Clock Summary reports {len(valid_periods)} clock(s): {valid_periods} ns ({frequencies} MHz derived from 1000 / ns)."
            if findings:
                findings[0]["engineering_interpretation"] += f" {clock_text}"
                findings[0]["evidence"]["clock_periods_ns"] = valid_periods
                findings[0]["evidence"]["clock_frequencies_mhz"] = frequencies
            else:
                add("informational", "Clock definitions", "Clock period", valid_periods, "ns",
                    clock_text,
                    "Clock periods define timing targets for synchronous paths.",
                    "Confirm these definitions match the intended clocks and generated-clock relationships.",
                    "Frequency is derived only from valid reported periods.")
        has_slack_values = any(_metric(metrics, key) is not None for key in (
            "wns_ns", "tns_ns", "whs_ns", "ths_ns", "wpws_ns", "tpws_ns", "slack_ns"
        ))
        if not has_slack_values:
            if metrics.get("timing_constraints_detected") is False:
                add("warning", "Timing constraints missing", "Timing slack", None, "ns",
                    "Vivado reports that no user timing constraints were specified; no slack values are available.",
                    "Without clock and interface constraints where applicable, timing cannot be classified as passing or failing.",
                    "Review clock definitions and input/output delays where applicable, then rerun report_timing_summary.",
                    "A clockless or combinational design may legitimately not require a clock.")
            else:
                add("warning", "Timing evidence unavailable", "Timing slack", None, "ns",
                    "No usable timing slack values were extracted from this report.",
                    "Without measured slack, timing cannot be classified as passing or failing.",
                    "Review clock definitions and constraints, then generate a timing summary with path coverage.",
                    "Missing values are not treated as zero.")

    elif report.report_type == "utilization":
        resources = _resource_map_from_metrics(metrics)
        measured = []
        for name, resource in resources.items():
            used, available = resource.get("used"), resource.get("available")
            if _finite(used) and _finite(available) and available > 0:
                measured.append((name, used, available, used / available * 100))
        for name, used, available, percent in sorted(measured, key=lambda row: row[3], reverse=True)[:3]:
            remaining = available - used
            add("critical" if percent > 100 else "informational", f"{name} utilization", name,
                {"used": used, "available": available, "utilization_percent": round(percent, 4), "remaining": remaining},
                "resources", f"{percent:.2f}% used, with {remaining:g} resources remaining.",
                "Resource headroom constrains how much additional logic can be mapped to the selected device.",
                (f"The reported used count exceeds the reported capacity; verify the device and source rows." if percent > 100 else
                 f"Track {name} usage against planned design changes."),
                "High usage alone is not a failure; no capacity limit is claimed unless used exceeds available.")
        count_only = {
            name: resource["used"] for name, resource in resources.items()
            if _finite(resource.get("used")) and not _finite(resource.get("available"))
        }
        if count_only:
            add("informational", "Additional reported resource counts", "Used resources", count_only,
                "count", "The report provides counts for these resources but no corresponding device capacities.",
                "Used counts show the observed implementation and are insufficient on their own for a utilization percentage.",
                "Use a report for the matching device that includes capacities if headroom must be assessed.",
                "No percentage or device limit is inferred for count-only rows.")
        if not measured:
            add("warning", "Utilization capacity unavailable", "Used / available", None, None,
                "The report does not provide complete used and available counts for supported resource rows.",
                "A percentage or remaining capacity cannot be determined without a valid device capacity.",
                "Confirm the selected device and report the utilization summary.",
                "No missing capacity is replaced with zero.")

    elif report.report_type == "power":
        total = _metric(metrics, "total_on_chip_power_w")
        dynamic = _metric(metrics, "dynamic_power_w")
        static = _metric(metrics, "static_power_w")
        if total is not None or dynamic is not None or static is not None:
            values = {key: value for key, value in (
                ("total_on_chip_power_w", total), ("dynamic_power_w", dynamic), ("static_power_w", static)
            ) if value is not None}
            add("informational", "Power estimate", "Reported power", values, "W",
                "Vivado reports total, dynamic, and static power where available. Component values are shown separately and are not added again to the total.",
                "Power estimates inform thermal and power-budget decisions; these values are estimates, not hardware measurements.",
                "Review the report's estimation confidence and activity assumptions before using the estimate for sign-off.",
                f"Vivado confidence: {metrics.get('confidence') or 'not reported'}.")
        if total is not None and static is not None and total > 0 and findings:
            static_share = static / total * 100
            findings[0]["engineering_interpretation"] += f" Static power contributes {static_share:.2f}% of the reported total; this does not imply a device fault."
            findings[0]["evidence"]["static_share_percent"] = round(static_share, 3)
        components = {
            label: _metric(metrics, key) for label, key in (
                ("Logic", "logic_power_w"), ("Signals", "signal_power_w"),
                ("Clocks", "clock_power_w"), ("I/O", "io_power_w"),
            )
        }
        components = {label: value for label, value in components.items() if value is not None}
        if components:
            largest = max(components, key=components.get)
            dynamic_total = dynamic
            fraction = (components[largest] / dynamic_total * 100) if dynamic_total and dynamic_total > 0 else None
            severity = "informational"
            context = f"; {fraction:.1f}% of reported dynamic power" if fraction is not None else ""
            action_by_component = {
                "I/O": "Review output loading, I/O standards, voltage assumptions, and switching activity.",
                "Signals": "Review switching activity and signal-interconnect assumptions in the power report.",
                "Logic": "Review the logic and switching assumptions represented in the power report.",
                "Clocks": "Review clock activity and the clock network assumptions in the power report.",
            }
            add(severity, f"Largest reported component: {largest}", largest, components[largest], "W",
                f"{largest} is the largest reported dynamic component{context}.",
                "The component breakdown identifies which estimated power category contributes most to dynamic power.",
                action_by_component[largest],
                "Component ratios are shown only when a positive dynamic total is reported; this is not an excessive-power judgment.")
        if str(metrics.get("confidence") or "").lower() == "low":
            add("warning", "Low-confidence power estimate", "Confidence", "Low", None,
                "Vivado marks this estimate as low confidence.",
                "The estimate has limited input coverage and may not represent actual operating activity.",
                "Review activity inputs and the confidence section of the source report.",
                "The report's confidence field is the evidence for this limitation.")
        if not findings:
            add("warning", "Power estimate unavailable", "Total on-chip power", None, "W",
                "No total or component power values were extracted.",
                "No numeric power assessment can be made from the available report.",
                "Confirm the report is a Vivado power report and includes a power summary.",
                "Missing values remain unavailable.")
    return findings


def evaluate_design_health(latest):
    timing = latest.get("timing")
    power = latest.get("power")
    utilization = latest.get("utilization")
    findings = []

    readiness = timing_readiness(timing, utilization)
    if readiness["state"] == "MISSING_CONSTRAINTS":
        findings.append(_finding(
            "warning", "timing", "TIMING_CONSTRAINTS_MISSING", "Timing constraints are missing",
            "timing_constraints_detected",
            {"value": False, "unit": None, "source_report": timing.name},
            readiness["explanation"],
            ["Clock definitions or interface delays may not have been applied to this implementation."],
            readiness["next_action"],
            "A new report_timing_summary identifies applicable constraints and reports timing coverage.",
        ))
    elif readiness["state"] == "INCOMPLETE_ANALYSIS":
        findings.append(_finding(
            "warning", "timing", "TIMING_ANALYSIS_INCOMPLETE", "Timing analysis is incomplete",
            "timing_coverage", timing.analysis_coverage or {}, readiness["explanation"],
            ["The report may be a single-path report or may omit coverage data."],
            readiness["next_action"],
            "A complete timing summary reports constrained design-wide setup results and no relevant coverage issues.",
        ))
    elif readiness["state"] == "ANALYZED_FAIL":
        for metric, value in readiness.get("violations", {}).items():
            findings.append(_finding(
                "critical", "timing", "NEGATIVE_SLACK", f"Negative timing slack: {metric.upper()}",
                metric, {"value": value, "unit": "ns", "source_report": timing.name, "stage": timing.stage},
                f"Vivado measured {value:g} ns for {metric}; this is a reported timing violation.",
                ["The path delay may exceed its required time; inspect the specific path and its applied constraints."],
                "Review the violating path, clock relationship, exceptions, and implementation timing.",
                "A rerun of the relevant timing summary reports non-negative slack for the affected path group.",
            ))

    if timing:
        timing_checks = (timing.analysis_coverage or {}).get("check_timing_issue_counts", {})
        for check_name, title, code, message in (
            ("no_clock", "Unclocked sequential endpoints reported", "CLOCK_DEFINITION_MISSING", "Vivado's check_timing section reports register/latch pins without a clock."),
            ("unconstrained_internal_endpoints", "Unconstrained internal endpoints reported", "UNCONSTRAINED_ENDPOINTS", "Vivado reports internal endpoints without maximum-delay constraints."),
        ):
            count = timing_checks.get(check_name, 0) or 0
            if count > 0:
                findings.append(_finding(
                    "warning", "timing", code, title, check_name,
                    {"value": count, "unit": "endpoints", "source_report": timing.name, "stage": timing.stage},
                    message,
                    ["The relevant clock or path constraints may be absent or may not cover these endpoints."],
                    "Inspect the listed check_timing category and review applicable clock and path constraints.",
                    "A new check_timing report shows the issue resolved or explains why the endpoints are intentionally unconstrained.",
                ))
        if (timing.analysis_coverage or {}).get("clock_count") == 0 and not timing_checks.get("no_clock", 0):
            matching_utilization = (
                utilization
                and utilization.run_id == timing.run_id
                and utilization.stage == timing.stage
                and utilization.device_part
                and utilization.device_part == timing.device_part
            )
            if matching_utilization:
                util_metrics = utilization.parsed_metrics or {}
                sequential_count = sum(
                    value for value in (
                        (util_metrics.get("slice_registers") or {}).get("used"),
                        util_metrics.get("register_as_flip_flop_used"),
                        util_metrics.get("register_as_latch_used"),
                    ) if _finite(value)
                )
                if sequential_count > 0:
                    findings.append(_finding(
                        "warning", "timing", "CLOCK_DEFINITION_MISSING", "Sequential resources have no detected clock",
                        "clock_count", {"value": 0, "sequential_resources": sequential_count, "unit": "count", "source_report": timing.name},
                        "The matching utilization run contains sequential resources, while the timing report lists no clock definitions.",
                        ["Clock definitions may not have been applied to this design stage."],
                        "Inspect clock constraints for the same design stage and rerun the timing summary.",
                        "A matched timing report lists the expected clock definitions and analyzes the sequential endpoints.",
                    ))

    utilization_state = "NOT_ANALYZED"
    if utilization:
        utilization_state = "MEASURED"
        for name, resource in _resource_map(utilization).items():
            used, available = resource.get("used"), resource.get("available")
            if not _finite(used) or not _finite(available) or available <= 0:
                continue
            ratio = used / available
            if ratio > 1:
                findings.append(_finding(
                    "critical", "utilization", "RESOURCE_CAPACITY_EXCEEDED", f"{name} exceeds device capacity",
                    name, {"used": used, "available": available, "unit": "resources", "source_report": utilization.name},
                    f"The report lists {used:g} used against {available:g} available.",
                    ["The report may combine incompatible device data or indicate an invalid resource mapping."],
                    "Verify the selected device and the resource rows in the source utilization report.",
                    "A new utilization report for the selected device reports used resources no greater than available resources.",
                ))
        if not any(_finite(r.get("used")) and _finite(r.get("available")) for r in _resource_map(utilization).values()):
            utilization_state = "INCOMPLETE_ANALYSIS"

    power_state = "NOT_ANALYZED"
    if power:
        power_metrics = power.parsed_metrics or {}
        total = _metric(power_metrics, "total_on_chip_power_w")
        budget = _metric(power_metrics, "design_power_budget_w")
        power_state = "REPORTED" if total is not None else "INCOMPLETE_ANALYSIS"
        if total is not None and budget is not None:
            power_state = "ANALYZED_FAIL" if total > budget else "ANALYZED_PASS"
            if total > budget:
                findings.append(_finding(
                    "critical", "power", "POWER_BUDGET_EXCEEDED", "On-chip power exceeds the design budget",
                    "total_on_chip_power_w", {"value": total, "budget": budget, "unit": "W", "source_report": power.name},
                    f"Vivado estimates {total:g} W against the explicitly specified {budget:g} W budget.",
                    ["The estimate reflects the activity, constraints, and device data supplied to Vivado."],
                    "Review the power budget and activity assumptions, then inspect the power report components.",
                    "A new power report estimates total on-chip power at or below the specified budget.",
                ))
        if str(power_metrics.get("confidence") or "").lower() == "low":
            findings.append(_finding(
                "warning", "power", "POWER_ESTIMATE_LOW_CONFIDENCE", "Power estimate has low confidence",
                "confidence", {"value": power_metrics["confidence"], "unit": None, "source_report": power.name},
                "Vivado marks this estimate as low confidence; the power value is reported but has limited input coverage.",
                ["Some I/O or internal activity may be unspecified."],
                "Review the confidence section and provide missing switching activity where available.",
                "A rerun reports improved confidence after activity inputs are supplied.",
            ))

    domain_states = {
        "timing": readiness["state"],
        "utilization": utilization_state,
        "power": power_state,
    }
    evidence_count = sum(report is not None for report in latest.values())
    assessment = "Insufficient evidence" if evidence_count == 0 else "Provisional"
    return {
        "score": None,
        "assessment": assessment,
        "domain_states": domain_states,
        "findings": findings,
    }, readiness
