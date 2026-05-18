from __future__ import annotations

import contextlib
import json
import sys

from santiszr.infra.tts.cosyvoice_client import CosyVoiceClient


def _configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(encoding="utf-8", errors="replace")


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    _configure_stdio()
    client = CosyVoiceClient()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except Exception as exc:
            _emit({"ok": False, "error": f"Invalid helper request: {exc}"})
            continue

        try:
            with contextlib.redirect_stdout(sys.stderr):
                audio_path, provider, notes = client.synthesize(
                    text=str(request.get("text") or ""),
                    voice=str(request.get("voice") or ""),
                    output_path=str(request.get("output_path") or ""),
                    reference_audio_path=str(request.get("reference_audio_path") or ""),
                    speed=float(request.get("speed") or 1.0),
                    sample_rate=int(request.get("sample_rate") or 22050),
                    speaker=str(request.get("speaker") or "") or None,
                )
        except Exception as exc:
            _emit({"ok": False, "error": str(exc)})
            continue

        _emit(
            {
                "ok": True,
                "audio_path": str(audio_path),
                "provider": provider,
                "notes": list(notes),
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
