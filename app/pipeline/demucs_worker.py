from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from importlib.metadata import version as package_version
from inspect import signature
from pathlib import Path
from typing import Any, TypeVar

import torch
from demucs import apply as demucs_apply
from demucs.apply import BagOfModels, apply_model
from demucs.audio import save_audio
from demucs.hdemucs import HDemucs
from demucs.htdemucs import HTDemucs
from demucs.pretrained import get_model
from demucs.separate import load_track

from app.core.config import DEMUCS_WORKER_HEARTBEAT_INTERVAL
from app.pipeline.demucs_protocol import (
    ENGINE,
    PROTOCOL_PREFIX,
    PROTOCOL_VERSION,
    SUPPORTED_DEMUCS_VERSION,
)

_T = TypeVar("_T")
_Emitter = Callable[[dict[str, Any]], None]
_EMIT_LOCK = threading.Lock()


def emit_protocol(event: dict[str, Any]) -> None:
    with _EMIT_LOCK:
        sys.stdout.write(PROTOCOL_PREFIX + json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def validate_demucs_compatibility() -> str:
    """Fail before loading weights if the internal worker contract drifted."""
    actual_version = package_version("demucs")
    if actual_version != SUPPORTED_DEMUCS_VERSION:
        raise RuntimeError(
            f"LayerLab persistent worker requires Demucs {SUPPORTED_DEMUCS_VERSION}; "
            f"found {actual_version}"
        )
    required_parameters = {
        "model",
        "mix",
        "shifts",
        "split",
        "overlap",
        "progress",
        "device",
        "num_workers",
        "segment",
    }
    if not required_parameters.issubset(signature(apply_model).parameters):
        raise RuntimeError("Demucs apply_model API is incompatible with the persistent worker")
    if not callable(getattr(getattr(demucs_apply, "tqdm", None), "tqdm", None)):
        raise RuntimeError("Demucs progress API is incompatible with the persistent worker")
    return actual_version


@contextmanager
def inference_heartbeat(
    request_id: str,
    emitter: _Emitter = emit_protocol,
    *,
    interval: float = DEMUCS_WORKER_HEARTBEAT_INTERVAL,
):
    """Emit liveness while a single Demucs chunk blocks progress callbacks."""
    stopped = threading.Event()

    def _run() -> None:
        while not stopped.wait(max(0.01, interval)):
            emitter(
                {
                    "event": "heartbeat",
                    "engine": ENGINE,
                    "requestId": request_id,
                    "phase": "inference",
                    "time": time.time(),
                }
            )

    thread = threading.Thread(target=_run, name="demucs-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=max(0.1, interval + 0.1))


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() != error_invalid_parameter


def start_parent_watchdog(parent_pid: int) -> threading.Thread:
    """Hard-stop an orphaned worker even if inference is blocked in Torch."""

    def _watch() -> None:
        while True:
            if not _process_exists(parent_pid):
                os._exit(1)
            time.sleep(1.0)

    thread = threading.Thread(target=_watch, name="demucs-parent-watchdog", daemon=True)
    thread.start()
    return thread


class ProgressReporter:
    """Replace tqdm while preserving Demucs' model/shift/chunk progress."""

    def __init__(
        self,
        request_id: str,
        *,
        shifts: int,
        model_count: int,
        emitter: _Emitter = emit_protocol,
    ) -> None:
        self.request_id = request_id
        self.pass_index = 0
        self.pass_total = max(1, shifts) * max(1, model_count)
        self.emitter = emitter

    def _emit(self, fraction: float, pass_index: int, chunk_index: int, chunk_total: int) -> None:
        self.emitter(
            {
                "event": "progress",
                "requestId": self.request_id,
                "fraction": min(1.0, max(0.0, fraction)),
                "passIndex": pass_index,
                "passTotal": self.pass_total,
                "chunkIndex": chunk_index,
                "chunkTotal": chunk_total,
            }
        )

    def wrap(self, values: Iterable[_T], *_args: object, **_kwargs: object) -> Iterator[_T]:
        total = max(1, len(values) if hasattr(values, "__len__") else 1)
        current_pass = min(self.pass_index, self.pass_total - 1)
        self._emit(current_pass / self.pass_total, current_pass + 1, 0, total)
        completed = 0
        try:
            for completed, value in enumerate(values, start=1):
                yield value
                self._emit(
                    (current_pass + (completed / total)) / self.pass_total,
                    current_pass + 1,
                    completed,
                    total,
                )
        finally:
            self.pass_index = min(self.pass_total, current_pass + 1)
            self._emit(
                self.pass_index / self.pass_total,
                self.pass_index,
                completed,
                total,
            )


def _int_setting(request: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = request.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be within {minimum}..{maximum}")
    return value


def _float_setting(
    request: dict[str, Any],
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    value = request.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be within {minimum:g}..{maximum:g}")
    return result


def _model_count(model: object) -> int:
    return max(1, len(model.models)) if isinstance(model, BagOfModels) else 1


def _max_segment(model: object) -> float:
    if isinstance(model, HTDemucs):
        return float(model.segment)
    if isinstance(model, BagOfModels):
        return float(model.max_allowed_segment)
    if isinstance(model, HDemucs):
        return float(model.segment)
    return float("inf")


def separate_track(
    model: object,
    model_name: str,
    device: str,
    request: dict[str, Any],
    *,
    emitter: _Emitter = emit_protocol,
) -> Path:
    request_id = request.get("requestId")
    source_raw = request.get("source")
    job_dir_raw = request.get("jobDir")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("requestId is required")
    if not isinstance(source_raw, str) or not isinstance(job_dir_raw, str):
        raise ValueError("source and jobDir are required")
    if Path(model_name).name != model_name:
        raise ValueError("model name must not contain path components")
    source = Path(source_raw).expanduser().resolve()
    job_dir = Path(job_dir_raw).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source audio does not exist: {source}")
    job_dir.mkdir(parents=True, exist_ok=True)

    shifts = _int_setting(request, "shifts", 0, 64)
    overlap = _float_setting(request, "overlap", 0.001, 0.99)
    segment_value = _float_setting(request, "segment", 0.0, 3600.0)
    jobs = _int_setting(request, "jobs", 0, 16)
    float32 = request.get("float32")
    if not isinstance(float32, bool):
        raise ValueError("float32 must be a boolean")
    clip_mode = request.get("clipMode") or "rescale"
    if clip_mode not in {"rescale", "clamp", "tanh", "none"}:
        raise ValueError("clipMode is invalid")
    segment = segment_value or None
    if segment is not None and segment > _max_segment(model):
        raise ValueError(f"segment exceeds model maximum of {_max_segment(model):g} seconds")

    waveform = load_track(source, model.audio_channels, model.samplerate)
    reference = waveform.mean(0)
    reference_mean = reference.mean()
    reference_std = reference.std()
    if not bool(torch.isfinite(reference_std)) or float(reference_std) < 1e-8:
        reference_std = torch.ones_like(reference_std)
    normalized = (waveform - reference_mean) / reference_std

    reporter = ProgressReporter(
        request_id,
        shifts=shifts,
        model_count=_model_count(model),
        emitter=emitter,
    )
    original_tqdm = demucs_apply.tqdm.tqdm
    demucs_apply.tqdm.tqdm = reporter.wrap
    try:
        with inference_heartbeat(request_id, emitter):
            sources = apply_model(
                model,
                normalized[None],
                device=device,
                shifts=shifts,
                split=True,
                overlap=overlap,
                progress=True,
                num_workers=jobs if device == "cpu" else 0,
                segment=segment,
            )[0]
    finally:
        demucs_apply.tqdm.tqdm = original_tqdm
    restored_sources = (sources * reference_std) + reference_mean

    output_dir = job_dir / model_name / source.stem
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[Path, Path]] = []
    try:
        for index, (stem_audio, stem_name) in enumerate(
            zip(restored_sources, model.sources, strict=True),
            start=1,
        ):
            destination = output_dir / f"{stem_name}.wav"
            temporary = output_dir / f"{stem_name}.tmp.wav"
            save_audio(
                stem_audio,
                str(temporary),
                samplerate=model.samplerate,
                clip=clip_mode,
                bits_per_sample=16,
                as_float=float32,
            )
            pending.append((temporary, destination))
            emitter(
                {
                    "event": "saving",
                    "requestId": request_id,
                    "stem": stem_name,
                    "index": index,
                    "total": len(model.sources),
                }
            )
        for temporary, destination in pending:
            temporary.replace(destination)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return output_dir


def _load_model(model_name: str, device: str) -> object:
    model = get_model(model_name)
    model.eval()
    model.to(torch.device(device))
    return model


def _serve(model_name: str, device: str) -> int:
    emit_protocol({"event": "starting", "engine": ENGINE, "model": model_name, "device": device})
    try:
        demucs_version = validate_demucs_compatibility()
        model = _load_model(model_name, device)
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit_protocol({"event": "error", "engine": ENGINE, "message": str(error), "startup": True})
        return 1

    emit_protocol(
        {
            "event": "ready",
            "engine": ENGINE,
            "protocolVersion": PROTOCOL_VERSION,
            "demucsVersion": demucs_version,
            "model": model_name,
            "device": device,
            "modelCount": _model_count(model),
            "sources": list(model.sources),
            "sampleRate": model.samplerate,
        }
    )
    for raw_line in sys.stdin:
        request: dict[str, Any] | None = None
        try:
            decoded = json.loads(raw_line)
            if not isinstance(decoded, dict):
                raise ValueError("request must be an object")
            request = decoded
            operation = request.get("operation")
            if operation == "shutdown":
                emit_protocol({"event": "stopped", "engine": ENGINE})
                return 0
            if operation == "ping":
                emit_protocol({"event": "pong", "engine": ENGINE})
                continue
            if operation != "separate":
                raise ValueError("unsupported operation")
            output = separate_track(model, model_name, device, request)
            emit_protocol(
                {
                    "event": "done",
                    "engine": ENGINE,
                    "requestId": request["requestId"],
                    "output": str(output),
                }
            )
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            emit_protocol(
                {
                    "event": "error",
                    "engine": ENGINE,
                    "requestId": request.get("requestId") if request is not None else None,
                    "message": str(error),
                    "startup": False,
                }
            )
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LayerLab persistent Demucs worker")
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    if args.parent_pid <= 0 or args.parent_pid == os.getpid():
        parser.error("--parent-pid must identify the backend process")
    start_parent_watchdog(args.parent_pid)
    return _serve(args.model, args.device)


if __name__ == "__main__":
    raise SystemExit(main())
