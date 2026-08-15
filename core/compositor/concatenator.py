"""core.compositor.concatenator — 视频拼接器

支持纯视频拼接和带音频字幕的拼接。
"""

import json
import logging
import os
import shutil
import subprocess
from typing import List, Optional

import re as _re

import srt as srt_lib
from moviepy import AudioFileClip, CompositeVideoClip, VideoFileClip

from models.task import SubtitleStyle

logger = logging.getLogger(__name__)

# ── 视频输出常量（对齐 MoneyPrinterTurbo，确保播放器兼容性）──
_AUDIO_CODEC = "aac"
_AUDIO_BITRATE = "192k"
_AUDIO_FPS = 44100
_VIDEO_FPS = 30
_VIDEO_PRESET = os.environ.get("VIDEO_FFMPEG_PRESET", "veryfast")
try:
    _VIDEO_THREADS = max(1, int(os.environ.get("VIDEO_FFMPEG_THREADS", "1")))
except ValueError:
    _VIDEO_THREADS = 1


class VideoConcatenator:
    """视频拼接器：纯拼接 + 带音频合成拼接。"""

    @staticmethod
    def concat_videos(video_paths: List[str], output_path: str) -> str:
        """纯视频拼接（无音频处理）。

        Args:
            video_paths: 视频文件路径列表
            output_path: 输出文件路径

        Returns:
            输出文件路径
        """
        logger.info(f"[Compositor] Concatenating {len(video_paths)} videos → {output_path}")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if not video_paths:
            raise RuntimeError("No videos to concatenate")

        if len(video_paths) == 1:
            shutil.copy2(video_paths[0], output_path)
            logger.info("[Compositor] Single video, copied directly")
            return output_path

        missing = [path for path in video_paths if not os.path.isfile(path)]
        if missing:
            raise RuntimeError(f"Video file not found: {missing[0]}")

        # The concat demuxer reads one clip at a time. This prevents MoviePy
        # from retaining every decoded clip on memory-constrained instances.
        concat_list = f"{output_path}.concat.txt"
        try:
            with open(concat_list, "w", encoding="utf-8", newline="\n") as f:
                for path in video_paths:
                    absolute_path = os.path.abspath(path).replace("\\", "/")
                    escaped_path = absolute_path.replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")

            base_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
                "-fflags", "+genpts", "-f", "concat", "-safe", "0",
                "-i", concat_list, "-map", "0:v:0", "-map", "0:a?",
            ]
            copy_cmd = [
                *base_cmd, "-c", "copy", "-movflags", "+faststart", output_path,
            ]
            try:
                VideoConcatenator._run_ffmpeg(copy_cmd, "stream-copy concat")
            except RuntimeError as copy_error:
                logger.warning(
                    "[Compositor] Stream-copy concat failed; retrying with a "
                    "single-thread encoder: %s", copy_error,
                )
                if os.path.exists(output_path):
                    os.remove(output_path)
                encode_cmd = [
                    *base_cmd,
                    "-c:v", "libx264", "-preset", _VIDEO_PRESET,
                    "-threads", str(_VIDEO_THREADS), "-pix_fmt", "yuv420p",
                    "-r", str(_VIDEO_FPS),
                    "-c:a", _AUDIO_CODEC, "-b:a", _AUDIO_BITRATE,
                    "-ar", str(_AUDIO_FPS),
                    "-movflags", "+faststart", output_path,
                ]
                VideoConcatenator._run_ffmpeg(encode_cmd, "low-memory concat")
        finally:
            if os.path.exists(concat_list):
                try:
                    os.remove(concat_list)
                except OSError:
                    pass

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("FFmpeg did not create the concatenated video")

        logger.info(f"[Compositor] Concatenation complete: {output_path}")
        return output_path

    @staticmethod
    def _resolve_subtitle_position(
        pos, default=("center", "bottom"), video_height: int = 0, video_width: int = 1920,
    ) -> tuple:
        """将字幕位置配置归一化为 (horizontal, vertical) 元组。

        支持格式：
          - 标准四角: "top-left", "top-right", "bottom-left", "bottom-right"
          - 偏移: "bottom-80", "top+20", "left+10", "right-30"
          - 百分比: ("50%", "30%")
          - 像素坐标: ("center", 200)
          - 传统: ("center", "bottom"), ("left", "top")
          - 纯字符串: "center", "top", "bottom", "top-left" 等

        复用 SubtitleGenerator.resolve_position 的核心逻辑。
        """
        from core.audio.subtitle import SubtitleGenerator
        return SubtitleGenerator.resolve_position(
            pos, video_width or 1920, video_height or 1080,
        )

    @staticmethod
    def _parse_srt_to_clips(
        srt_path: str,
        subtitle_style: SubtitleStyle,
        video_width: int,
        video_height: int = 0,
        video_duration: float = 0.0,
        subtitle_styles: Optional[list] = None,
    ) -> list:
        """逐条解析 SRT，返回 TextClip 列表（支持多行自动换行 + 逐条样式覆盖）。

        Args:
            srt_path: SRT 文件路径。
            subtitle_style: 全局字幕样式（作为默认值/回退）。
            video_width: 视频宽度。
            video_height: 视频高度。
            video_duration: 视频总时长（用于钳位）。
            subtitle_styles: 逐条样式列表（Phase 2：LLM 生成），
                每项含 index, position, color, fontsize。
                未指定的字段回退到 subtitle_style 的全局值。
        """
        from moviepy import TextClip as MpTextClip
        from core.config import resolve_font_path
        from core.audio.subtitle import SubtitleGenerator

        font_path = resolve_font_path(subtitle_style.font)

        # 兼容旧格式 bg_color 字符串
        bg = subtitle_style.bg_color
        if isinstance(bg, str):
            if "@" in bg:
                parts = bg.split("@", 1)
                rgb = {"black": (0, 0, 0), "white": (255, 255, 255)}.get(parts[0].strip().lower(), (0, 0, 0))
                bg = (*rgb, int(float(parts[1]) * 255))
            else:
                bg = (0, 0, 0, 128)

        # 构建逐条样式查找表
        style_map: dict[int, dict] = {}
        if subtitle_styles:
            for s in subtitle_styles:
                idx = s.get("index", 0)
                if idx > 0:
                    style_map[idx] = s

        # 根据视频宽度动态计算每行最大字符数（与 subtitle.py 一致）
        available_w = video_width - 40

        # 位置冲突时的备选位置池（循环取用，确保重叠字幕不在同一位置）
        _FALLBACK_POSITIONS = [
            ("center", "top+80"),
            ("center", "center"),
            ("center", "bottom-100"),
            ("left", "center"),
            ("right", "center"),
            ("left", "top+60"),
            ("right", "bottom-120"),
            ("center", "top+120"),
            ("left", "bottom-80"),
            ("right", "top+80"),
        ]

        subs_clips = []
        _clip_registry: list[tuple[float, float, tuple]] = []  # (start, end, position)
        with open(srt_path, "r", encoding="utf-8") as f:
            for sub in srt_lib.parse(f):
                txt = sub.content
                start_s = sub.start.total_seconds()
                end_s = sub.end.total_seconds()
                dur = end_s - start_s
                idx = sub.index

                # ═ 逐条样式覆盖 ═
                entry_style = style_map.get(idx, {})
                fs = entry_style.get("fontsize", subtitle_style.fontsize)
                color = entry_style.get("color", subtitle_style.color)
                pos = entry_style.get("position", subtitle_style.position)

                # 每行字符数随字号动态调整
                cjk_max_chars = max(8, available_w // fs) if fs > 0 else 14

                # 长文本自动拆为多行，避免单行溢出屏幕
                wrapped = SubtitleGenerator._split_long_text(txt, cjk_max_chars)

                clip = MpTextClip(
                    text=wrapped,
                    font=font_path,
                    font_size=fs,
                    color=color,
                    stroke_color=subtitle_style.stroke_color,
                    stroke_width=subtitle_style.stroke_width,
                    bg_color=bg,
                    method="caption",
                    size=(available_w, None),
                    text_align="center",
                )
                # M10: 钳位字幕结束时间不超过视频时长
                if video_duration > 0:
                    end_s = min(end_s, video_duration - 0.01)
                    if end_s <= start_s:
                        continue
                    dur = end_s - start_s

                clip = (
                    clip.with_start(start_s)
                    .with_end(end_s)
                    .with_duration(dur)
                )
                pos_resolved = VideoConcatenator._resolve_subtitle_position(
                    pos, video_height=video_height, video_width=video_width,
                )
                h_part, v_part = pos_resolved
                # clamp horizontal pixel: keep text box (width=available_w) within frame
                if isinstance(h_part, (int, float)):
                    max_x = video_width - available_w
                    h_part = max(20, min(h_part, max(20, max_x)))
                # clamp vertical pixel: ~100px safe zone at bottom for 2-line text
                if isinstance(v_part, (int, float)):
                    v_part = max(20, min(v_part, video_height - 100))
                pos_tuple = (h_part, v_part)

                # ── 位置去重：与现有字幕时间重叠且位置相同 → 自动错开 ──
                for es, ee, ep in _clip_registry:
                    if start_s < ee and es < end_s and ep == pos_tuple:
                        for alt_pos in _FALLBACK_POSITIONS:
                            alt_resolved = VideoConcatenator._resolve_subtitle_position(
                                alt_pos, video_height=video_height, video_width=video_width,
                            )
                            if isinstance(alt_resolved[0], (int, float)):
                                max_x = video_width - available_w
                                alt_resolved = (max(20, min(alt_resolved[0], max(20, max_x))), alt_resolved[1])
                            if isinstance(alt_resolved[1], (int, float)):
                                alt_resolved = (alt_resolved[0], max(20, min(alt_resolved[1], video_height - 100)))
                            conflict = any(
                                start_s < ee2 and es2 < end_s and alt_resolved == ep2
                                for es2, ee2, ep2 in _clip_registry
                            )
                            if not conflict:
                                pos_tuple = alt_resolved
                                break
                        break

                clip = clip.with_position(pos_tuple)
                _clip_registry.append((start_s, end_s, pos_tuple))
                subs_clips.append(clip)
        return subs_clips

    @staticmethod
    def _get_duration(path: str) -> float:
        """用 ffprobe 获取媒体文件时长（秒）。"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=15,
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0

    @staticmethod
    def _run_ffmpeg(cmd: list, desc: str = "") -> None:
        """执行 ffmpeg 命令，失败时抛 RuntimeError。"""
        logger.info(f"[Compositor] ffmpeg: {desc}")
        try:
            r = subprocess.run(
                cmd, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg {desc} failed (code {r.returncode}): "
                    f"{r.stderr[:500]}"
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ffmpeg {desc} timed out")

    @staticmethod
    def concat_videos_with_audio_overlay(
        video_paths: List[str],
        audio_path: str,
        srt_path: Optional[str],
        output_path: str,
        subtitle_style: Optional[SubtitleStyle] = None,
        subtitle_styles_path: Optional[str] = None,
    ) -> str:
        """先拼接视频，再统一叠加单条音频 + 单条字幕。

        使用 ffmpeg 做音视频时长对齐（tpad/apad），确保音画精确同步。

        Args:
            video_paths: 按顺序的视频路径列表。
            audio_path: 整段音频文件路径（对应全部视频的总时间轴）。
            srt_path: 整段 SRT 字幕路径（可选）。
            output_path: 最终输出文件路径。
            subtitle_style: 字幕样式配置。

        Returns:
            输出文件路径。
        """
        logger.info(
            f"[Compositor] concat_videos_with_audio_overlay: "
            f"{len(video_paths)} videos + {audio_path} → {output_path}"
        )
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if not video_paths:
            raise RuntimeError("No videos to concatenate")

        # ── Step 1: 拼接视频（无声）──
        silent_path = output_path.replace(".mp4", "_silent.mp4")
        VideoConcatenator.concat_videos(video_paths, silent_path)

        # ── Step 2: 获取音视频时长 ──
        video_dur = VideoConcatenator._get_duration(silent_path)
        audio_dur = VideoConcatenator._get_duration(audio_path)
        final_dur = max(video_dur, audio_dur)
        logger.info(
            f"[Compositor] durations: video={video_dur:.2f}s, "
            f"audio={audio_dur:.2f}s, final={final_dur:.2f}s"
        )

        video_input = silent_path
        tmp_files = [silent_path]

        # ── Step 3: 若视频 < 音频，冻结尾帧补齐 ──
        if video_dur < final_dur - 0.3:
            extend_path = output_path.replace(".mp4", "_vext.mp4")
            tmp_files.append(extend_path)
            pad_dur = final_dur - video_dur
            VideoConcatenator._run_ffmpeg(
                ["ffmpeg", "-y",
                 "-i", silent_path,
                 "-vf", f"tpad=stop_mode=clone:stop_duration={pad_dur:.2f}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-preset", _VIDEO_PRESET, "-threads", str(_VIDEO_THREADS),
                 extend_path],
                desc=f"extend video by {pad_dur:.1f}s (freeze last frame)",
            )
            video_input = extend_path

        # ── Step 4: 若音频 < 视频，补齐静音 ──
        audio_input = audio_path
        if audio_dur < final_dur - 0.3:
            apad_path = audio_path.replace(".mp3", "_apad.mp3")
            tmp_files.append(apad_path)
            pad_dur = final_dur - audio_dur
            VideoConcatenator._run_ffmpeg(
                ["ffmpeg", "-y",
                 "-i", audio_path,
                 "-af", f"apad=pad_dur={pad_dur:.2f},volume=1.5",
                 "-c:a", "libmp3lame", "-q:a", "2",
                 apad_path],
                desc=f"pad audio by {pad_dur:.1f}s + volume 1.5x",
            )
            audio_input = apad_path
        else:
            # 只做音量放大
            vol_path = audio_path.replace(".mp3", "_vol.mp3")
            tmp_files.append(vol_path)
            VideoConcatenator._run_ffmpeg(
                ["ffmpeg", "-y",
                 "-i", audio_path,
                 "-af", "volume=1.5",
                 "-c:a", "libmp3lame", "-q:a", "2",
                 vol_path],
                desc="boost audio volume 1.5x",
            )
            audio_input = vol_path

        # ── Step 5: render subtitles only, then mux audio with ffmpeg ──
        video_clip = None
        rendered_video = video_input
        try:
            video_clip = VideoFileClip(video_input)
            target_dur = video_clip.duration

            # ── 叠加字幕 ──
            if srt_path and os.path.exists(srt_path) and subtitle_style:
                final = None
                subs_clips = []
                try:
                    per_entry_styles = None
                    if subtitle_styles_path and os.path.exists(subtitle_styles_path):
                        with open(subtitle_styles_path, "r", encoding="utf-8") as f:
                            per_entry_styles = json.load(f)

                    subs_clips = VideoConcatenator._parse_srt_to_clips(
                        srt_path, subtitle_style, video_clip.w,
                        video_height=video_clip.h,
                        video_duration=target_dur,
                        subtitle_styles=per_entry_styles,
                    )
                    if subs_clips:
                        rendered_video = output_path.replace(
                            ".mp4", "_subtitled_silent.mp4"
                        )
                        tmp_files.append(rendered_video)
                        final = CompositeVideoClip([
                            video_clip.without_audio(), *subs_clips,
                        ])
                        final.write_videofile(
                            rendered_video,
                            codec="libx264",
                            audio=False,
                            fps=_VIDEO_FPS,
                            preset=_VIDEO_PRESET,
                            threads=_VIDEO_THREADS,
                            logger="bar",
                        )
                except Exception as e:
                    logger.warning(
                        f"[Compositor] Subtitle overlay failed: {e}, muxing without subtitles"
                    )
                    rendered_video = video_input
                finally:
                    if final is not None:
                        final.close()
                    for subtitle_clip in subs_clips:
                        try:
                            subtitle_clip.close()
                        except Exception:
                            pass

            if video_clip is not None:
                video_clip.close()
                video_clip = None

            VideoConcatenator._run_ffmpeg(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
                    "-i", rendered_video, "-i", audio_input,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", _AUDIO_CODEC,
                    "-b:a", _AUDIO_BITRATE, "-ar", str(_AUDIO_FPS),
                    "-shortest", "-movflags", "+faststart", output_path,
                ],
                desc="mux narration audio",
            )
        finally:
            if video_clip is not None:
                video_clip.close()
            for tmp in tmp_files:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        logger.info(f"[Compositor] concat_videos_with_audio_overlay done: {output_path}")
        return output_path

    @staticmethod
    def composite_anchor_video(
        clip_path: str,
        audio_path: str,
        srt_path: Optional[str],
        output_path: str,
        audio_duration: float,
        subtitle_style: Optional[SubtitleStyle] = None,
        subtitle_styles_path: Optional[str] = None,
        video_width: int = 768,
        video_height: int = 1344,
    ) -> str:
        """将 5 秒主播动态视频片段循环拼接为覆盖完整音频时长，再叠加音频和字幕。

        核心思路：循环拼接 + 裁剪 + 统一叠加音频/字幕。
        接缝处用 ffmpeg xfade 做 0.3 秒交叉淡入淡出过渡。

        Args:
            clip_path: 5 秒主播动态视频片段路径。
            audio_path: TTS 读稿音频路径。
            srt_path: SRT 字幕文件路径（可选）。
            output_path: 最终输出视频路径。
            audio_duration: 音频总时长（秒）。
            subtitle_style: 字幕样式配置。
            subtitle_styles_path: LLM 样式 JSON 路径（可选）。
            video_width: 视频宽度。
            video_height: 视频高度。

        Returns:
            输出文件路径。
        """
        import math
        import subprocess

        logger.info(
            f"[Compositor] composite_anchor_video: {clip_path} + {audio_path} "
            f"(audio={audio_duration:.1f}s) → {output_path}"
        )
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Step 1: Get clip duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", clip_path],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15,
        )
        clip_duration = float(probe.stdout.strip() or 5.0)
        if clip_duration <= 0:
            clip_duration = 5.0

        # Step 2: Calculate loop count
        needed = audio_duration + 2.0  # extra 2s padding
        n = math.ceil(needed / clip_duration) + 1

        # Step 3: Build concat file list for ffmpeg
        loop_dir = os.path.dirname(output_path)
        concat_file = os.path.join(loop_dir, "_anchor_concat.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for _ in range(n):
                f.write(f"file '{clip_path}'\n")

        looped_path = output_path.replace(".mp4", "_looped.mp4")

        # Step 4: Concatenate with xfade cross-fade transitions
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", concat_file,
                 "-c", "copy",
                 "-t", str(needed),
                 looped_path],
                stdin=subprocess.DEVNULL,
                check=True, capture_output=True, timeout=300,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"[Compositor] Simple concat failed: {e.stderr[:200]}, trying xfade")

            # Build complex filter for xfade cross-fade between each pair
            fade_duration = 0.3
            filter_parts = []
            for i in range(n):
                if i == 0:
                    filter_parts.append(f"[0:{i}]")
                else:
                    filter_parts.append(f"[0:{i}]")
                    filter_parts.append(f"xfade=transition=fade:duration={fade_duration}:offset={i * clip_duration - fade_duration * i}")
            filter_str = "".join(filter_parts)

            subprocess.run(
                ["ffmpeg", "-y",
                 "-stream_loop", str(n - 1), "-i", clip_path,
                 "-filter_complex",
                 f"[0:v]trim=duration={needed}[v]",
                 "-map", "[v]",
                 "-c:v", "libx264",
                 "-preset", "fast",
                 "-t", str(needed),
                 looped_path],
                stdin=subprocess.DEVNULL,
                check=True, capture_output=True, timeout=300,
            )

        # Step 5: Overlay audio and subtitles
        concat_video_clip = None
        audio_clip_obj = None
        try:
            concat_video_clip = VideoFileClip(looped_path)
            audio_clip_obj = AudioFileClip(audio_path)

            _AUDIO_VOLUME_FACTOR = 1.5
            audio_clip_obj = audio_clip_obj.with_volume_scaled(_AUDIO_VOLUME_FACTOR)

            video_with_audio = concat_video_clip.with_audio(audio_clip_obj)

            if srt_path and os.path.exists(srt_path) and subtitle_style:
                per_entry_styles = None
                if subtitle_styles_path and os.path.exists(subtitle_styles_path):
                    with open(subtitle_styles_path, "r", encoding="utf-8") as f:
                        per_entry_styles = json.load(f)

                subs_clips = VideoConcatenator._parse_srt_to_clips(
                    srt_path, subtitle_style,
                    video_width, video_height,
                    video_duration=concat_video_clip.duration,
                    subtitle_styles=per_entry_styles,
                )
                if subs_clips:
                    final = CompositeVideoClip([video_with_audio, *subs_clips])
                    final.write_videofile(
                        output_path,
                        codec="libx264",
                        audio_codec=_AUDIO_CODEC,
                        audio_bitrate=_AUDIO_BITRATE,
                        audio_fps=_AUDIO_FPS,
                        fps=_VIDEO_FPS,
                        preset=_VIDEO_PRESET,
                        threads=_VIDEO_THREADS,
                        logger="bar",
                    )
                    final.close()
                else:
                    video_with_audio.write_videofile(
                        output_path,
                        codec="libx264",
                        audio_codec=_AUDIO_CODEC,
                        audio_bitrate=_AUDIO_BITRATE,
                        audio_fps=_AUDIO_FPS,
                        fps=_VIDEO_FPS,
                        preset=_VIDEO_PRESET,
                        threads=_VIDEO_THREADS,
                        logger="bar",
                    )
            else:
                video_with_audio.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec=_AUDIO_CODEC,
                    audio_bitrate=_AUDIO_BITRATE,
                    audio_fps=_AUDIO_FPS,
                    fps=_VIDEO_FPS,
                    preset=_VIDEO_PRESET,
                    threads=_VIDEO_THREADS,
                    logger="bar",
                )
        finally:
            if concat_video_clip is not None:
                concat_video_clip.close()
            if audio_clip_obj is not None:
                audio_clip_obj.close()
            for tmp in (looped_path, concat_file):
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        logger.info(f"[Compositor] composite_anchor_video done: {output_path}")
        return output_path


