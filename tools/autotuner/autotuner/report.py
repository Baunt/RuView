"""Deterministic one-second-grid comparison; no interpolation or lag search."""
import bisect
import math
from collections import Counter

POLICY = {"version": "public-observation-v1", "grid_s": 1,
          "reference_tolerance_s": 0.5, "observation_max_age_s": 1.5,
          "supported_hr_bpm": [40, 120], "interpolation": "none",
          "time_basis": "host_received_at", "reference_latency": "unknown"}


def build_report(session, observations, reference, markers):
    alignment = session.get("alignment") or {}
    offset = alignment.get("offset_s", 0)
    points = reference.get("points", []) if reference else []
    reference_times = [p["timestamp"] + offset for p in points]
    observation_times = [o["received_at"] for o in observations]
    start, end = session["started_at"], session.get("ended_at") or session["started_at"]
    end = min(end, start + 28800)
    timeline, counts, errors = [], Counter(), []
    state, marker_index = session.get("initial_block", "unmarked"), 0
    markers = sorted(markers, key=lambda m: m["timestamp"])
    for stamp in range(math.ceil(start), math.floor(end) + 1):
        while marker_index < len(markers) and markers[marker_index]["timestamp"] <= stamp:
            if markers[marker_index]["kind"] in ("seated", "supine", "motion", "empty", "unmarked"):
                state = markers[marker_index]["kind"]
            marker_index += 1
        index = bisect.bisect_left(reference_times, stamp)
        candidates = [i for i in (index-1, index) if 0 <= i < len(points)]
        closest = min(candidates, key=lambda i: abs(reference_times[i]-stamp)) if candidates else None
        ref = points[closest]["hr_bpm"] if closest is not None and abs(reference_times[closest]-stamp) <= POLICY["reference_tolerance_s"] else None
        oi = bisect.bisect_right(observation_times, stamp) - 1
        obs = observations[oi] if oi >= 0 else None
        fresh = obs and 0 <= stamp-obs["received_at"] <= POLICY["observation_max_age_s"]
        hr = obs.get("hr_bpm") if fresh and obs.get("status") == "valid" else None
        reason = "valid" if hr is not None else ("poll_gap" if not fresh else ",".join(obs.get("reasons", [])) or "no_heart_rate")
        counts["total_seconds"] += 1
        counts["reference_seconds"] += ref is not None
        counts["estimate_seconds"] += hr is not None
        eligible = ref is not None and 40 <= ref <= 120 and state in ("seated", "supine")
        counts["out_of_range_seconds"] += ref is not None and not 40 <= ref <= 120
        if eligible:
            counts["eligible_seconds"] += 1
            if hr is not None:
                errors.append(hr-ref)
                counts["matched_seconds"] += 1
            else:
                counts[f"missing:{reason}"] += 1
        timeline.append({"timestamp": stamp, "reference_hr": ref, "ruview_hr": hr,
                         "eligible": eligible, "block": state, "reason": reason})
    absolute = sorted(abs(e) for e in errors)
    eligible = counts["eligible_seconds"]
    limitations = ["A RuView időtengelye a helyi API-fogadás ideje; a becslés ablakvége és kibocsátási ideje ismeretlen.",
                   "A Garmin eszközkésése ismeretlen. Ez megfigyelési összehasonlítás, nem algoritmus- vagy pontosságigazolás."]
    if alignment.get("uncertainty_s") is None:
        limitations.append("Az időillesztési bizonytalanság nincs megadva.")
    if not reference:
        limitations.append("Nincs Garmin FIT-referencia hozzárendelve.")
    if not session.get("raw_capture"):
        limitations.append("Nincs nyers I/Q-felvétel; a pulzushangolás még nem indítható.")
    return {"session_id": session["id"], "status": "observational_only" if errors else "insufficient_data",
            "policy": POLICY, "alignment": alignment, "counts": dict(counts),
            "coverage": len(errors)/eligible if eligible else None,
            "total_coverage": counts["estimate_seconds"]/counts["total_seconds"] if counts["total_seconds"] else None,
            "mae_bpm": sum(absolute)/len(absolute) if absolute else None,
            "p95_abs_bpm": absolute[math.ceil(.95*len(absolute))-1] if absolute else None,
            "bias_bpm": sum(errors)/len(errors) if errors else None,
            "limitations": limitations, "timeline": timeline, "markers": markers,
            "reference_quality": reference.get("quality") if reference else None}
