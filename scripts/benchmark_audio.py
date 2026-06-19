#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.benchmark import benchmark_audio, benchmark_job_dir  # noqa: E402


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
    parser.add_argument("--source", type=Path, help="Original source audio to compare against.")
    parser.add_argument("--stems-dir", type=Path, help="Directory containing stem WAV files.")
    parser.add_argument("--metadata", type=Path, help="metadata.json containing chord progression.")
    parser.add_argument("--sr", type=int, default=44100, help="Benchmark decode sample rate.")
    parser.add_argument(
        "--duration",
        type=_positive_float,
        default=180.0,
        help="Seconds to benchmark from the start of each file.",
    )
    parser.add_argument("--out", type=Path, help="Write JSON report to this file.")
    args = parser.parse_args()

    if args.job_dir:
        report = benchmark_job_dir(
            args.job_dir,
            source=args.source,
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
            sr=args.sr,
            duration=args.duration,
        )

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
