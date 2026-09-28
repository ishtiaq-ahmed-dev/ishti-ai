"""ISHTI CapCut Controller: High-level Python interface to capcut-cli.
Allows full programmatic control of CapCut/JianYing drafts:
- Project creation, quickstart, inspection, and diagnosis
- Media track editing (video, audio, text, SRT subtitles)
- Effects, filters, transitions, speed, volume, audio fades
- Pre-cut intelligence: scene detection, silence removal, retake detection
- Hardware-accelerated proxy rendering via NVENC / FFmpeg
- Launching CapCut desktop app with active projects
"""

import os
import sys
import json
import shutil
import subprocess
from typing import Optional, List, Dict, Any, Union

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
CAPCUT_EXE = os.path.join(LOCALAPPDATA, "CapCut", "Apps", "CapCut.exe")
CAPCUT_DRAFTS = os.path.join(LOCALAPPDATA, "CapCut", "User Data", "Projects", "com.lveditor.draft")

try:
    import capcut
except ImportError:
    capcut = None


class CapCutController:
    def __init__(self):
        self.cli_available = shutil.which("capcut") is not None or shutil.which("capcut.cmd") is not None
        self.has_exe = os.path.isfile(CAPCUT_EXE)

    def is_installed(self) -> bool:
        """Returns True if capcut-cli is available on PATH."""
        return self.cli_available or capcut is not None

    def doctor(self) -> Dict[str, Any]:
        """Runs preflight checks across node, ffprobe, ffmpeg, draft stores."""
        if capcut is not None:
            try:
                return capcut.doctor()
            except Exception:
                pass
        return self._run_cli(["doctor"])

    def get_projects(self) -> List[Dict[str, Any]]:
        """Lists all CapCut/JianYing projects found on disk."""
        if capcut is not None:
            try:
                res = capcut.run("projects")
                return res.get("projects", [])
            except Exception:
                pass
        raw = self._run_cli(["projects"])
        return raw.get("projects", []) if isinstance(raw, dict) else []

    def find_project(self, query: str) -> Optional[Dict[str, Any]]:
        """Finds a project by exact or partial name match."""
        projs = self.get_projects()
        q = query.strip().lower()
        if not projs:
            return None
        # 1. Exact match on folder
        for p in projs:
            if p.get("folder", "").lower() == q:
                return p
        # 2. Substring match
        for p in projs:
            if q in p.get("folder", "").lower():
                return p
        # 3. Path match
        for p in projs:
            if os.path.normpath(p.get("path", "")).lower() == os.path.normpath(query).lower():
                return p
        return None

    def get_project_dir(self, proj_name_or_path: str) -> str:
        """Normalizes a project name or path to the project directory containing draft files."""
        if os.path.isdir(proj_name_or_path):
            return proj_name_or_path
        if os.path.isfile(proj_name_or_path):
            return os.path.dirname(proj_name_or_path)
        found = self.find_project(proj_name_or_path)
        if found:
            return os.path.dirname(found["path"])
        direct = os.path.join(CAPCUT_DRAFTS, proj_name_or_path)
        if os.path.isdir(direct):
            return direct
        return proj_name_or_path

    def get_info(self, proj_name_or_path: str) -> Dict[str, Any]:
        """Gets comprehensive details on a project (tracks, segments, duration, canvas)."""
        pdir = self.get_project_dir(proj_name_or_path)
        if capcut is not None:
            try:
                return capcut.run("info", pdir)
            except Exception as e:
                return {"error": str(e)}
        return self._run_cli(["info", pdir])

    def get_timeline(self, proj_name_or_path: str, human: bool = False) -> Union[Dict[str, Any], str]:
        """Gets track and segment layout (JSON or human-readable ASCII bars)."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["timeline", pdir]
        if human:
            args.append("-H")
            return self._run_cli_raw(args)
        if capcut is not None:
            try:
                return capcut.run("timeline", pdir)
            except Exception as e:
                return {"error": str(e)}
        return self._run_cli(args)

    def quickstart(
        self,
        name: str,
        video: Optional[str] = None,
        audio: Optional[str] = None,
        srt: Optional[str] = None,
        ratio: str = "9:16",
        width: Optional[int] = None,
        height: Optional[int] = None,
        allow_replace: bool = True,
    ) -> Dict[str, Any]:
        """One-command new project creation: seeds skeleton, adds media, and registers in CapCut."""
        target_dir = os.path.join(CAPCUT_DRAFTS, name)
        if allow_replace and os.path.isdir(target_dir):
            try:
                shutil.rmtree(target_dir)
            except Exception:
                pass

        kwargs: Dict[str, Any] = {"ratio": ratio}
        if video:
            kwargs["video"] = os.path.abspath(video)
        if audio:
            kwargs["audio"] = os.path.abspath(audio)
        if srt:
            kwargs["srt"] = os.path.abspath(srt)
        if width and height:
            kwargs.pop("ratio", None)
            kwargs["width"] = width
            kwargs["height"] = height

        if capcut is not None:
            try:
                return capcut.run("quickstart", name, **kwargs)
            except Exception as e:
                # Fall back to CLI
                pass

        cli_args = ["quickstart", name]
        for k, v in kwargs.items():
            cli_args.extend([f"--{k.replace('_', '-')}", str(v)])
        return self._run_cli(cli_args)

    def init_project(self, name: str, ratio: str = "9:16") -> Dict[str, Any]:
        """Creates an empty draft from template."""
        if capcut is not None:
            try:
                return capcut.run("init", name, ratio=ratio)
            except Exception:
                pass
        return self._run_cli(["init", name, "--ratio", ratio])

    def add_video(self, proj_name_or_path: str, file_path: str, start: str = "0s", duration: Optional[str] = None) -> Dict[str, Any]:
        """Appends or places a video clip into the project timeline."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["add-video", pdir, os.path.abspath(file_path), start]
        if duration:
            args.append(duration)
        return self._run_cli(args)

    def add_audio(self, proj_name_or_path: str, file_path: str, start: str = "0s", duration: Optional[str] = None) -> Dict[str, Any]:
        """Adds background music or sound bed to a dedicated audio track."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["add-audio", pdir, os.path.abspath(file_path), start]
        if duration:
            args.append(duration)
        return self._run_cli(args)

    def add_text(
        self,
        proj_name_or_path: str,
        text: str,
        start: str = "0s",
        duration: str = "3s",
        font_size: int = 16,
    ) -> Dict[str, Any]:
        """Adds a styled text or title card segment."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["add-text", pdir, start, duration, text, "--font-size", str(font_size)]
        return self._run_cli(args)

    def import_srt(self, proj_name_or_path: str, srt_file: str) -> Dict[str, Any]:
        """Imports an SRT subtitle file into editable caption tracks."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["import-srt", pdir, os.path.abspath(srt_file)]
        return self._run_cli(args)

    def cut_segment(self, proj_name_or_path: str, segment_id: str, at_time: str) -> Dict[str, Any]:
        """Splits a timeline segment at the specified timestamp (e.g. '00:01:23.500' or '5.2s')."""
        pdir = self.get_project_dir(proj_name_or_path)
        return self._run_cli(["cut", pdir, segment_id, at_time])

    def set_speed(self, proj_name_or_path: str, segment_id: str, speed_factor: float) -> Dict[str, Any]:
        """Sets playback speed of a segment (e.g. 1.5, 2.0)."""
        pdir = self.get_project_dir(proj_name_or_path)
        return self._run_cli(["speed", pdir, segment_id, str(speed_factor)])

    def set_volume(self, proj_name_or_path: str, segment_id: str, volume: float) -> Dict[str, Any]:
        """Sets volume level of an audio or video segment (1.0 = 100%, 0.5 = 50%)."""
        pdir = self.get_project_dir(proj_name_or_path)
        return self._run_cli(["volume", pdir, segment_id, str(volume)])

    def audio_fade(
        self,
        proj_name_or_path: str,
        segment_id: str,
        fade_in_s: Optional[float] = None,
        fade_out_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Adds smooth fade-in and/or fade-out transitions to an audio segment."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["audio-fade", pdir, segment_id]
        if fade_in_s is not None:
            args.extend(["--in", str(fade_in_s)])
        if fade_out_s is not None:
            args.extend(["--fade-out", str(fade_out_s)])
        return self._run_cli(args)

    def add_filter(self, proj_name_or_path: str, filter_slug: str, start: str = "0s", duration: Optional[str] = None) -> Dict[str, Any]:
        """Adds a color grading filter on its own track."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["add-filter", pdir, filter_slug]
        if duration:
            args.extend([start, duration])
        else:
            args.append("--full")
        return self._run_cli(args)

    def add_effect(self, proj_name_or_path: str, effect_slug: str, start: str = "0s", duration: Optional[str] = None) -> Dict[str, Any]:
        """Adds a scene visual effect on its own track."""
        pdir = self.get_project_dir(proj_name_or_path)
        args = ["add-effect", pdir, effect_slug]
        if duration:
            args.extend([start, duration])
        else:
            args.append("--full")
        return self._run_cli(args)

    def detect_silence(self, media_path: str, threshold_db: int = -30, min_silence: float = 0.5) -> Dict[str, Any]:
        """Analyzes audio for silent pauses to seed cuts (removes dead air)."""
        return self._run_cli(["detect-silence", os.path.abspath(media_path), "--threshold-db", str(threshold_db), "--min-silence", str(min_silence)])

    def detect_scenes(self, video_path: str, threshold: float = 0.4) -> Dict[str, Any]:
        """Analyzes video for scene-change cut points."""
        return self._run_cli(["detect-scenes", os.path.abspath(video_path), "--threshold", str(threshold)])

    def detect_retakes(self, proj_name_or_path: str) -> Dict[str, Any]:
        """Finds repeated take sequences in speech/captions and identifies the winning take."""
        pdir = self.get_project_dir(proj_name_or_path)
        return self._run_cli(["detect-retakes", pdir])

    def render_preview(
        self,
        proj_name_or_path: str,
        out_path: Optional[str] = None,
        burn_captions: bool = False,
    ) -> Dict[str, Any]:
        """Renders an accelerated FFmpeg proxy preview of the CapCut timeline."""
        pdir = self.get_project_dir(proj_name_or_path)
        if not out_path:
            pname = os.path.basename(pdir)
            out_dir = os.path.join(ROOT, "data", "media_output")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{pname}_preview.mp4")

        args = ["render", pdir, "--out", os.path.abspath(out_path)]
        if burn_captions:
            args.append("--burn-captions")
        return self._run_cli(args)

    def open_capcut(self, proj_name_or_path: Optional[str] = None) -> bool:
        """Launches CapCut desktop app. The newest created/modified project appears at the top."""
        if not self.has_exe:
            return False
        try:
            subprocess.Popen([CAPCUT_EXE], close_fds=True)
            return True
        except Exception:
            return False

    def _run_cli(self, args: List[str]) -> Dict[str, Any]:
        """Executes capcut CLI command and parses the returned JSON."""
        cmd = ["cmd", "/c", "capcut", *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8")
            stdout = p.stdout.strip()
            # Often capcut outputs JSON on stdout or first line
            for line in stdout.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except ValueError:
                        continue
            # If no JSON found, return raw text dict
            return {"ok": p.returncode == 0, "stdout": stdout, "stderr": p.stderr.strip()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _run_cli_raw(self, args: List[str]) -> str:
        """Executes capcut CLI command and returns raw stdout."""
        cmd = ["cmd", "/c", "capcut", *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8")
            return p.stdout.strip()
        except Exception as exc:
            return f"Error: {exc}"


_controller = None

def get_capcut_controller() -> CapCutController:
    global _controller
    if _controller is None:
        _controller = CapCutController()
    return _controller
