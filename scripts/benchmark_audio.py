#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.benchmark import (  # noqa: E402
    benchmark_audio,
    benchmark_job_dir,
    benchmark_jobs_root,
    compare_benchmark_reports,
)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure LayerLab stem reconstruction and chord metadata quality."
    )
    parser.add_argument("--job-dir", type=Path, help="Existing LayerLab job directory.")
    parser.add_argument("--jobs-root", type=Path, help="Directory containing multiple LayerLab jobs.")
    parser.add_argument("--source", type=Path, help="Original source audio to compare against.")
    parser.add_argument("--stems-dir", type=Path, help="Directory containing stem WAV files.")
    parser.add_argument("--metadata", type=Path, help="metadata.json containing chord progression.")
    parser.add_argument(
        "--reference-chords",
        type=Path,
        help="Ground-truth chord .lab file for mir_eval WCSR metrics.",
    )
    parser.add_argument("--baseline", type=Path, help="Previous suite JSON to compare against.")
    parser.add_argument("--sr", type=int, default=44100, help="Benchmark decode sample rate.")
    parser.add_argument(
        "--duration",
        type=_positive_float,
        default=180.0,
        help="Seconds to benchmark from the start of each file.",
    )
    parser.add_argument("--out", type=Path, help="Write JSON report to this file.")
    args = parser.parse_args()

    comparison = None
    if args.jobs_root:
        report = benchmark_jobs_root(
            args.jobs_root,
            sr=args.sr,
            duration=args.duration,
        )
    elif args.job_dir:
        report = benchmark_job_dir(
            args.job_dir,
            source=args.source,
            reference_chords_path=args.reference_chords,
            sr=args.sr,
            duration=args.duration,
        )
    else:
        if not args.stems_dir:
            parser.error("--stems-dir is required when --job-dir is not used")
        report = benchmark_audio(
            source=args.source,
            stems_dir=args.stems_dir,
            metadata_path=args.metadata,
            reference_chords_path=args.reference_chords,
            sr=args.sr,
            duration=args.duration,
        )
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        comparison = compare_benchmark_reports(report, baseline)
        report = {"report": report, "comparison": comparison}

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
