---
name: capcut-edit
description: Edit CapCut / JianYing video projects — read and write subtitles, timing, speed, volume, templates, animations, and cut long-form to shorts. Use when the user mentions capcut, jianying, subtitles, video editing, draft_content.json, draft_info.json, or cutting videos — in English or Chinese (剪映, 字幕, 草稿, 剪辑, 切片, 视频编辑).
---

# capcut-edit

CLI for editing CapCut / JianYing drafts through a version-aware store. Detects and synchronizes readable timelines across `draft_content.json`, `draft_info.json`, `draft_meta_info.json`, and `template-2.tmp`.

## Project locations

- **macOS**: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/<project>/`
- **Windows**: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\<project>\`

## Progressive disclosure

Start broad, drill into what you need. Never dump full project JSON.

```bash
capcut info <project> -H           # overview
capcut doctor -H                   # installed media/transcription capabilities
capcut diagnose <project> -H       # canonical files, divergence, editor safety
capcut tracks <project> -H         # all tracks
capcut materials <project> -H      # material summary
capcut segments <project> -H       # segments with timing
capcut segment <project> <id>      # one item, full detail
```

## Output modes

- **JSON** (default): pipe to `jq`, feed to scripts.
- **`-H`**: human-readable tables.
- **`-q`**: quiet, exit code only — for write commands in scripts.

## Batch writes

```bash
echo '{"cmd":"set-text","id":"a1b2c3","text":"Fixed"}
{"cmd":"volume","id":"d4e5f6","volume":0.5}' | capcut batch <project>
```

Operations: `set-text`, `shift`, `shift-all`, `speed`, `volume`, `opacity`, `trim`.

## Quickstart

```bash
capcut quickstart my-short --video clip.mp4 --ratio 9:16
```
