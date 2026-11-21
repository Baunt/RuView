"""FIT records are reference observations, never reconstructed physiology."""
import io
import math
import statistics
from datetime import timezone

import fitdecode


def normalize_records(records):
    points, seen = [], {}
    issues = {"records": 0, "missing_timestamp": 0, "missing_hr": 0,
              "invalid_hr": 0, "duplicates": 0, "conflicts": 0, "out_of_order": 0}
    previous = None
    for record in records:
        issues["records"] += 1
        stamp, hr = record.get("timestamp"), record.get("heart_rate")
        if stamp is None:
            issues["missing_timestamp"] += 1
            continue
        if hasattr(stamp, "timestamp"):
            stamp = stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
        stamp = float(stamp)
        if not math.isfinite(stamp):
            issues["missing_timestamp"] += 1
            continue
        if previous is not None and stamp < previous:
            issues["out_of_order"] += 1
        previous = stamp
        if hr is None:
            issues["missing_hr"] += 1
            value = None
        elif isinstance(hr, bool) or not isinstance(hr, (int, float)) or not math.isfinite(hr) or not 1 <= hr < 255:
            issues["invalid_hr"] += 1
            value = None
        else:
            value = float(hr)
        if stamp in seen:
            issues["duplicates"] += 1
            if seen[stamp] != value:
                issues["conflicts"] += 1
                seen[stamp] = None  # Never choose the duplicate closer to RuView.
        else:
            seen[stamp] = value
    points = [{"timestamp": t, "hr_bpm": seen[t]} for t in sorted(seen)]
    deltas = [b["timestamp"] - a["timestamp"] for a, b in zip(points, points[1:])]
    return {"points": points, "quality": {**issues, "unique_timestamps": len(points),
            "valid_hr": sum(p["hr_bpm"] is not None for p in points),
            "median_interval_s": statistics.median(deltas) if deltas else None,
            "max_interval_s": max(deltas) if deltas else None,
            "gaps_over_2s": sum(d > 2 for d in deltas),
            "start": points[0]["timestamp"] if points else None,
            "end": points[-1]["timestamp"] if points else None}}


def read_fit(payload: bytes):
    records, metadata = [], {}
    # Strict CRC checking: a truncated or corrupted file is not a valid reference.
    with fitdecode.FitReader(io.BytesIO(payload), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "file_id":
                metadata = {key: frame.get_value(key, fallback=None) for key in ("manufacturer", "product", "type")}
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record":
                records.append({"timestamp": frame.get_value("timestamp", fallback=None),
                                "heart_rate": frame.get_value("heart_rate", fallback=None)})
    result = normalize_records(records)
    result["device_metadata"] = metadata
    if not result["quality"]["valid_hr"]:
        raise ValueError("A FIT-fájlban nincs érvényes, időbélyeges pulzusrekord.")
    return result
