"""Read existing localizer estimates and map an adopted source interval into planned segment time."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def interval(start, end):
    return [round(start, 6), round(end, 6)]


def read_units(result):
    matching = result.get("matching", {})
    units = result.get("sentence_candidates", matching.get("units"))
    if not isinstance(units, list) or not units or matching.get("units", units) != units:
        raise ValueError("Missing or inconsistent sentence candidates")
    candidate = result.get("candidate")
    if candidate is not None:
        encoded = json.dumps(candidate, sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if (hashlib.sha256(encoded).hexdigest() != result.get("candidate_content_sha256")
                or candidate["matching"]["units"] != units
                or candidate["audio_file_sha256"] != result["audio_file_sha256"]
                or candidate["source_content_hashes"]["script"] != result["script_sha256"]
                or candidate["decoded_duration_seconds"] != result["decoded_duration_seconds"]):
            raise ValueError("Source and sentence candidates differ from the recorded candidate")
    ids = set()
    for row in units:
        if not isinstance(row, dict) or not row.get("id") or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError("Missing original sentence ID or text")
        if row["id"] in ids:
            raise ValueError(f"Repeated sentence ID {row['id']}")
        ids.add(row["id"])
    return units


def audio_windows(result_path, audio_path, script_path, window=None, inserts=()):
    raw = Path(result_path).read_bytes()
    result = json.loads(raw.decode("utf-8-sig"))
    audio_sha = file_sha(audio_path)
    script_sha = file_sha(script_path)
    if audio_sha != result["audio_file_sha256"] or script_sha != result["script_sha256"]:
        raise ValueError("Audio or original localizer script file differs from result.json")
    duration = result["decoded_duration_seconds"]
    if result["source_origin_seconds"] != 0 or not finite(duration) or duration <= 0:
        raise ValueError("Expected an original-track result with zero origin and positive duration")
    start, end = (0, duration) if window is None else window
    if not (finite(start) and finite(end) and 0 <= start < end <= duration):
        raise ValueError("Source range must fall within the decoded original track")
    cuts = {}
    for at, seconds in inserts:
        if not (finite(at) and finite(seconds) and start <= at <= end and seconds > 0):
            raise ValueError("Insert needs an in-range original-track position and positive seconds")
        if at in cuts:
            raise ValueError("Specify the total inserted duration at each source position once")
        cuts[at] = seconds
    cuts = dict(sorted(cuts.items()))

    def before(source_time):
        return source_time - start + sum(length for at, length in cuts.items() if at < source_time)

    added = [{"source_at": at, "planned": interval(before(at), before(at) + length)}
             for at, length in cuts.items()]
    selected, unlocated, spans = [], [], []
    for row in read_units(result):
        left, right = row["estimated_start"], row["estimated_end"]
        entry = {key: row.get(key) for key in ("id", "text", "kind", "speaker", "boundary_status")}
        entry.update(listeners=row.get("listeners", row.get("listener")), issues=row["issues"],
                     estimated_source=[left, right])
        if not (finite(left) and finite(right) and 0 <= left < right <= duration):
            entry["status"] = "unlocated" if left is None and right is None else "invalid_estimate"
            unlocated.append(entry)
            continue
        if right <= start or left >= end:
            continue
        low, high = max(start, left), min(end, right)
        spans.append((low, high))
        internal_cuts = [at for at in cuts if low < at < high]
        edges = [low, *internal_cuts, high]
        parts = [{"source": interval(a, b), "planned": interval(before(a) + cuts.get(a, 0), before(b))}
                 for a, b in zip(edges, edges[1:])]
        entry.update(status="estimate_with_issues" if row["issues"] else "estimate",
                     crosses_range_start=left < start, crosses_range_end=right > end,
                     inserts_within_sentence=internal_cuts, parts=parts)
        selected.append(entry)

    # Gaps between estimates can contain speech or other sounds; they are not verified silence.
    gaps = []
    cursor = start
    for low, high in sorted(spans):
        if low > cursor:
            gaps.append(interval(cursor, low))
        cursor = max(cursor, high)
    if cursor < end:
        gaps.append(interval(cursor, end))
    return {
        "result_sha256": hashlib.sha256(raw).hexdigest(), "audio_file_sha256": audio_sha,
        "script_sha256": script_sha, "decoded_duration_seconds": duration,
        "source_range": interval(start, end), "inserts": added,
        "planned_segment_seconds": round(end - start + sum(cuts.values()), 6),
        "adoption_status": result.get("adoption_status"), "automatic_pass": result.get("automatic_pass"),
        "adoption_source": result.get("adoption_source"), "playback_verified": result.get("playback_verified"),
        "warnings": result.get("warnings", []), "sentences": selected, "unlocated_episode_sentences": unlocated,
        "source_without_sentence_estimate": gaps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="Existing local_audio result.json")
    parser.add_argument("--audio", type=Path, required=True, help="Actual complete original audio file")
    parser.add_argument("--script", type=Path, required=True, help="Actual script file passed to the localizer")
    parser.add_argument("--range", nargs=2, type=float, metavar=("START", "END"), help="Original-track seconds")
    parser.add_argument("--insert", nargs=2, type=float, action="append", default=[],
                        metavar=("SOURCE_AT", "SECONDS"), help="Director's planned extra picture duration")
    args = parser.parse_args()
    try:
        report = audio_windows(args.result, args.audio, args.script, args.range, args.insert)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Audio handoff: {exc}\n")
    sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
