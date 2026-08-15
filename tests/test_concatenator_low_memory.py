from pathlib import Path

from core.compositor.concatenator import VideoConcatenator


def _make_inputs(tmp_path: Path) -> list[str]:
    paths = [tmp_path / "scene-1.mp4", tmp_path / "scene-2.mp4"]
    for path in paths:
        path.write_bytes(b"input")
    return [str(path) for path in paths]


def test_concat_videos_uses_stream_copy_and_cleans_manifest(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _make_inputs(tmp_path)
    output = tmp_path / "final.mp4"
    calls = []
    manifests = []

    def fake_run(cmd: list, desc: str = "") -> None:
        calls.append((cmd, desc))
        manifest = Path(cmd[cmd.index("-i") + 1])
        manifests.append(manifest.read_text(encoding="utf-8"))
        Path(cmd[-1]).write_bytes(b"output")

    monkeypatch.setattr(VideoConcatenator, "_run_ffmpeg", fake_run)

    result = VideoConcatenator.concat_videos(inputs, str(output))

    assert result == str(output)
    assert len(calls) == 1
    assert calls[0][1] == "stream-copy concat"
    assert calls[0][0][calls[0][0].index("-c") + 1] == "copy"
    assert "scene-1.mp4" in manifests[0]
    assert "scene-2.mp4" in manifests[0]
    assert not Path(f"{output}.concat.txt").exists()


def test_concat_videos_falls_back_to_single_thread_encoder(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _make_inputs(tmp_path)
    output = tmp_path / "final.mp4"
    calls = []

    def fake_run(cmd: list, desc: str = "") -> None:
        calls.append((cmd, desc))
        if len(calls) == 1:
            Path(cmd[-1]).write_bytes(b"partial")
            raise RuntimeError("incompatible streams")
        Path(cmd[-1]).write_bytes(b"output")

    monkeypatch.setattr(VideoConcatenator, "_run_ffmpeg", fake_run)

    VideoConcatenator.concat_videos(inputs, str(output))

    assert [desc for _, desc in calls] == [
        "stream-copy concat",
        "low-memory concat",
    ]
    fallback_cmd = calls[1][0]
    assert fallback_cmd[fallback_cmd.index("-c:v") + 1] == "libx264"
    assert fallback_cmd[fallback_cmd.index("-threads") + 1] == "1"
    assert not Path(f"{output}.concat.txt").exists()

