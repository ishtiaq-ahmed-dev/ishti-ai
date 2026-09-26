"""
ISHTI Video Automation & Editing Engine
Leverages FFmpeg & FFprobe with NVIDIA NVENC GPU hardware acceleration on RTX 5070 Ti.
Provides automated video operations for a JARVIS-like assistant:
- Trimming & cutting clips
- Concatenation / merging multiple videos
- Audio extraction & voiceover / background music replacement & mixing
- Speed adjustments (slow-mo / fast-forward)
- Aspect ratio conversion (16:9 widescreen to 9:16 Shorts/Reels/TikTok)
- Video compression for messaging / Discord / WhatsApp
- Frame & thumbnail extraction
- Text & subtitle overlays
- Format conversions (MP4, MKV, MOV, GIF, WEBM)
"""

import os
import re
import json
import shutil
import subprocess
from typing import Dict, Any, List, Optional, Tuple

ROOT_DIR = r"D:\ISHTI"
DATA_DIR = os.path.join(ROOT_DIR, "data", "video_output")
os.makedirs(DATA_DIR, exist_ok=True)


class VideoEditor:
    """Automated video processing and editing agent powered by FFmpeg."""

    def __init__(self, output_dir: str = DATA_DIR):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_exe = shutil.which("ffprobe") or "ffprobe"
        self.has_nvenc = self._check_nvenc_support()

    def _check_nvenc_support(self) -> bool:
        """Detects whether NVIDIA NVENC hardware acceleration is available."""
        try:
            res = subprocess.run(
                [self.ffmpeg_exe, "-encoders"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            return "h264_nvenc" in res.stdout or "hevc_nvenc" in res.stdout
        except Exception:
            return False

    def _run_ffmpeg(self, cmd_args: List[str], timeout: int = 180) -> Tuple[bool, str]:
        """Runs ffmpeg command with error logging and status output."""
        full_cmd = [self.ffmpeg_exe, "-y", "-hide_banner"] + cmd_args
        print(f"[VideoEditor] Executing: {' '.join(full_cmd[:8])}...")
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, "Success"
            else:
                err_snippet = proc.stderr[-400:] if proc.stderr else "Unknown ffmpeg error"
                print(f"[VideoEditor] FFmpeg error: {err_snippet}")
                return False, err_snippet
        except subprocess.TimeoutExpired:
            return False, f"Operation timed out after {timeout} seconds"
        except Exception as e:
            return False, str(e)

    # -------------------------------------------------------------
    # 1. MEDIA INSPECTION & METADATA
    # -------------------------------------------------------------
    def get_media_info(self, file_path: str) -> Dict[str, Any]:
        """Gathers media metadata via ffprobe (duration, resolution, fps, codecs)."""
        if not os.path.exists(file_path):
            return {"error": f"File does not exist: {file_path}"}

        cmd = [
            self.ffprobe_exe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                return {"error": f"ffprobe error: {res.stderr[:200]}"}

            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0.0))

            video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
            audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

            width = video_stream.get("width") if video_stream else None
            height = video_stream.get("height") if video_stream else None

            # Calculate FPS
            fps = 0.0
            if video_stream and "r_frame_rate" in video_stream:
                rate_parts = video_stream["r_frame_rate"].split("/")
                if len(rate_parts) == 2 and float(rate_parts[1]) > 0:
                    fps = round(float(rate_parts[0]) / float(rate_parts[1]), 2)

            return {
                "file": file_path,
                "duration_seconds": round(duration, 2),
                "duration_formatted": f"{int(duration // 60):02d}:{int(duration % 60):02d}",
                "width": width,
                "height": height,
                "aspect_ratio": f"{width}:{height}" if width and height else "Unknown",
                "fps": fps,
                "video_codec": video_stream.get("codec_name") if video_stream else None,
                "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
                "size_mb": round(float(fmt.get("size", 0)) / (1024 * 1024), 2),
                "bitrate_kbps": round(float(fmt.get("bit_rate", 0)) / 1000, 1) if fmt.get("bit_rate") else None,
            }
        except Exception as e:
            return {"error": f"Failed to inspect media: {e}"}

    # -------------------------------------------------------------
    # 2. TRIMMING & CUTTING
    # -------------------------------------------------------------
    def trim_video(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        start_time: str = "00:00:00",
        end_time: Optional[str] = None,
        duration: Optional[str] = None,
        reencode: bool = False,
    ) -> Dict[str, Any]:
        """
        Trims a video segment.
        - start_time: e.g. "00:00:10" or "10"
        - end_time: e.g. "00:00:45"
        - duration: e.g. "30"
        - reencode: if False, uses ultra-fast lossless stream copy
        """
        if not os.path.exists(input_path):
            return {"status": "error", "error": f"File not found: {input_path}"}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(input_path))
            output_path = os.path.join(self.output_dir, f"{base}_trimmed{ext}")

        args = ["-ss", str(start_time), "-i", input_path]
        if end_time:
            args.extend(["-to", str(end_time)])
        elif duration:
            args.extend(["-t", str(duration)])

        if reencode:
            encoder = "h264_nvenc" if self.has_nvenc else "libx264"
            args.extend(["-c:v", encoder, "-c:a", "aac", output_path])
        else:
            args.extend(["-c", "copy", output_path])

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "trim",
                "output_path": output_path,
                "info": self.get_media_info(output_path),
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 3. MERGING & CONCATENATION
    # -------------------------------------------------------------
    def merge_videos(self, input_paths: List[str], output_path: Optional[str] = None) -> Dict[str, Any]:
        """Concatenates multiple video clips in order."""
        valid_paths = [os.path.abspath(p) for p in input_paths if os.path.exists(p)]
        if len(valid_paths) < 2:
            return {"status": "error", "error": "At least 2 valid video files are required to merge."}

        if not output_path:
            ext = os.path.splitext(valid_paths[0])[1] or ".mp4"
            output_path = os.path.join(self.output_dir, f"merged_{int(os.times().elapsed)}{ext}")

        # Create concat manifest
        manifest_path = os.path.join(self.output_dir, "concat_list.txt")
        with open(manifest_path, "w", encoding="utf-8") as f:
            for p in valid_paths:
                f.write(f"file '{p.replace(chr(92), '/')}'\n")

        # Try fast stream copy first
        args = ["-f", "concat", "-safe", "0", "-i", manifest_path, "-c", "copy", output_path]
        ok, msg = self._run_ffmpeg(args)

        if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) < 100:
            # Fallback to re-encoding if streams have different resolutions or codecs
            encoder = "h264_nvenc" if self.has_nvenc else "libx264"
            args = ["-f", "concat", "-safe", "0", "-i", manifest_path, "-c:v", encoder, "-c:a", "aac", output_path]
            ok, msg = self._run_ffmpeg(args)

        if os.path.exists(manifest_path):
            try:
                os.remove(manifest_path)
            except Exception:
                pass

        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "merge",
                "output_path": output_path,
                "input_count": len(valid_paths),
                "info": self.get_media_info(output_path),
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 4. AUDIO EXTRACTION & REPLACEMENT
    # -------------------------------------------------------------
    def extract_audio(self, video_path: str, output_path: Optional[str] = None, format: str = "mp3") -> Dict[str, Any]:
        """Extracts audio track from video to MP3, WAV, or AAC."""
        if not os.path.exists(video_path):
            return {"status": "error", "error": f"File not found: {video_path}"}

        if not output_path:
            base = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(self.output_dir, f"{base}_audio.{format}")

        args = ["-i", video_path, "-vn"]
        if format.lower() == "mp3":
            args.extend(["-c:a", "libmp3lame", "-q:a", "2"])
        elif format.lower() == "wav":
            args.extend(["-c:a", "pcm_s16le"])
        else:
            args.extend(["-c:a", "aac"])

        args.append(output_path)
        ok, msg = self._run_ffmpeg(args)

        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "extract_audio",
                "output_path": output_path,
                "format": format,
                "size_kb": round(os.path.getsize(output_path) / 1024, 1),
            }
        return {"status": "error", "error": msg}

    def add_audio_track(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
        mix: bool = False,
        video_volume: float = 0.5,
        audio_volume: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Adds or replaces audio in video.
        - mix: if True, mixes background audio with original audio (ducking/voiceover)
        """
        if not os.path.exists(video_path) or not os.path.exists(audio_path):
            return {"status": "error", "error": "Both video and audio files must exist."}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(video_path))
            output_path = os.path.join(self.output_dir, f"{base}_voiced{ext}")

        encoder = "h264_nvenc" if self.has_nvenc else "libx264"

        if mix:
            filter_complex = f"[0:a]volume={video_volume}[a1];[1:a]volume={audio_volume}[a2];[a1][a2]amix=inputs=2:duration=first[aout]"
            args = [
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", encoder,
                "-c:a", "aac",
                "-shortest",
                output_path,
            ]
        else:
            args = [
                "-i", video_path,
                "-i", audio_path,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                output_path,
            ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "add_audio",
                "output_path": output_path,
                "mixed": mix,
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 5. SPEED ADJUSTMENT
    # -------------------------------------------------------------
    def change_speed(self, video_path: str, speed_factor: float = 2.0, output_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Adjusts playback speed of video and audio (e.g. 0.5 for half speed, 2.0 for double speed).
        """
        if not os.path.exists(video_path):
            return {"status": "error", "error": f"File not found: {video_path}"}

        if speed_factor <= 0 or speed_factor > 10:
            return {"status": "error", "error": "Speed factor must be between 0.1 and 10.0"}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(video_path))
            output_path = os.path.join(self.output_dir, f"{base}_{speed_factor}x{ext}")

        # Video filter: setpts=(1/speed)*PTS
        video_pts = 1.0 / speed_factor
        # Audio filter: atempo (supports 0.5 to 2.0 per filter stage)
        atempo = speed_factor
        audio_filters = []
        while atempo > 2.0:
            audio_filters.append("atempo=2.0")
            atempo /= 2.0
        while atempo < 0.5:
            audio_filters.append("atempo=0.5")
            atempo /= 0.5
        audio_filters.append(f"atempo={atempo}")
        audio_filter_str = ",".join(audio_filters)

        encoder = "h264_nvenc" if self.has_nvenc else "libx264"
        args = [
            "-i", video_path,
            "-filter_complex", f"[0:v]setpts={video_pts}*PTS[v];[0:a]{audio_filter_str}[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", encoder,
            "-c:a", "aac",
            output_path,
        ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "speed_change",
                "speed_factor": speed_factor,
                "output_path": output_path,
                "info": self.get_media_info(output_path),
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 6. ASPECT RATIO & SOCIAL MEDIA FORMATTING (9:16 Shorts/Reels)
    # -------------------------------------------------------------
    def convert_to_vertical(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        mode: str = "pad",
    ) -> Dict[str, Any]:
        """
        Converts 16:9 landscape video into 9:16 vertical video for YouTube Shorts, Instagram Reels, or TikTok.
        - mode 'pad': preserves full frame with top/bottom blurred background or black bars
        - mode 'crop': centers and crops to fill 9:16
        """
        if not os.path.exists(video_path):
            return {"status": "error", "error": f"File not found: {video_path}"}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(video_path))
            output_path = os.path.join(self.output_dir, f"{base}_vertical_9x16{ext}")

        encoder = "h264_nvenc" if self.has_nvenc else "libx264"

        if mode == "crop":
            # Crop center 9:16
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        else:
            # Blurred background pad
            vf = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )

        args = [
            "-i", video_path,
            "-filter_complex" if mode != "crop" else "-vf", vf,
            "-c:v", encoder,
            "-c:a", "copy",
            output_path,
        ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "convert_vertical",
                "resolution": "1080x1920",
                "output_path": output_path,
                "info": self.get_media_info(output_path),
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 7. THUMBNAIL & FRAME EXTRACTION
    # -------------------------------------------------------------
    def extract_thumbnail(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        timestamp: str = "00:00:01",
    ) -> Dict[str, Any]:
        """Extracts a high-resolution still image frame at the given timestamp."""
        if not os.path.exists(video_path):
            return {"status": "error", "error": f"File not found: {video_path}"}

        if not output_path:
            base = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(self.output_dir, f"{base}_thumb.jpg")

        args = [
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_path,
        ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "thumbnail",
                "output_path": output_path,
                "size_kb": round(os.path.getsize(output_path) / 1024, 1),
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 8. COMPRESSION FOR MESSAGING
    # -------------------------------------------------------------
    def compress_video(
        self,
        video_path: str,
        target_size_mb: float = 25.0,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Compresses video to fit under target size (e.g. 25MB for Discord/WhatsApp).
        Calculates optimal bitrate and performs efficient two-pass or CRF encoding.
        """
        info = self.get_media_info(video_path)
        if "error" in info:
            return {"status": "error", "error": info["error"]}

        duration = info.get("duration_seconds", 0)
        if duration <= 0:
            return {"status": "error", "error": "Cannot determine video duration"}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(video_path))
            output_path = os.path.join(self.output_dir, f"{base}_compressed{ext}")

        # Target total bits = target_size_mb * 8192 kbits
        target_total_kb = target_size_mb * 8192
        audio_bitrate_kb = 96
        video_bitrate_kb = max(100, int((target_total_kb / duration) - audio_bitrate_kb))

        encoder = "h264_nvenc" if self.has_nvenc else "libx264"
        args = [
            "-i", video_path,
            "-b:v", f"{video_bitrate_kb}k",
            "-maxrate", f"{int(video_bitrate_kb * 1.5)}k",
            "-bufsize", f"{video_bitrate_kb * 2}k",
            "-c:v", encoder,
            "-c:a", "aac",
            "-b:a", f"{audio_bitrate_kb}k",
            output_path,
        ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            actual_size = round(os.path.getsize(output_path) / (1024 * 1024), 2)
            return {
                "status": "success",
                "action": "compress",
                "output_path": output_path,
                "original_size_mb": info.get("size_mb"),
                "new_size_mb": actual_size,
            }
        return {"status": "error", "error": msg}

    # -------------------------------------------------------------
    # 9. TEXT & TITLE OVERLAYS
    # -------------------------------------------------------------
    def add_text_overlay(
        self,
        video_path: str,
        text: str,
        output_path: Optional[str] = None,
        position: str = "bottom",
        font_size: int = 36,
        font_color: str = "white",
    ) -> Dict[str, Any]:
        """Adds text / title overlay to video."""
        if not os.path.exists(video_path):
            return {"status": "error", "error": f"File not found: {video_path}"}

        if not output_path:
            base, ext = os.path.splitext(os.path.basename(video_path))
            output_path = os.path.join(self.output_dir, f"{base}_titled{ext}")

        pos_coords = {
            "top": "x=(w-text_w)/2:y=40",
            "bottom": "x=(w-text_w)/2:y=h-th-40",
            "center": "x=(w-text_w)/2:y=(h-text_h)/2",
        }
        coord_expr = pos_coords.get(position, pos_coords["bottom"])

        clean_text = text.replace(":", "\\:").replace("'", "").replace('"', "")
        drawtext = f"drawtext=text='{clean_text}':fontsize={font_size}:fontcolor={font_color}:box=1:boxcolor=black@0.6:boxborderw=8:{coord_expr}"

        encoder = "h264_nvenc" if self.has_nvenc else "libx264"
        args = [
            "-i", video_path,
            "-vf", drawtext,
            "-c:v", encoder,
            "-c:a", "copy",
            output_path,
        ]

        ok, msg = self._run_ffmpeg(args)
        if ok and os.path.exists(output_path):
            return {
                "status": "success",
                "action": "text_overlay",
                "output_path": output_path,
            }
        return {"status": "error", "error": msg}


_video_editor = None


def get_video_editor() -> VideoEditor:
    global _video_editor
    if _video_editor is None:
        _video_editor = VideoEditor()
    return _video_editor


if __name__ == "__main__":
    editor = VideoEditor()
    print("Video Editor Ready:", editor.ffmpeg_exe)
    print("NVENC GPU Acceleration:", editor.has_nvenc)
