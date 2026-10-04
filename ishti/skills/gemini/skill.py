"""Image and video generation with Google Gemini, in the owner's own Chrome (the main profile, signed in to
Gemini Pro) through the Playwright Extension. Programmatic steps, no LLM driving: open Gemini, type the prompt,
wait for the result, click Download, move the file from Downloads into data/generated and report the path.
Generating is what the owner asked for and changes nothing else, so it runs without a yes (T1)."""
import os
import re
import shutil
import time

from ishti import ROOT
from ishti.skills import trace
from ishti.skills.browser import skill as browser

OUT = os.path.join(ROOT, "data", "generated")
URL = "https://gemini.google.com/app"
MEDIA = r"(?:image|picture|pic|photo|illustration|drawing|painting|sketch|logo|icon|wallpaper|poster|artwork|art|" \
        r"video|clip|animation)"
GEN = re.compile(rf"\b(?:generate|create|make|draw|design|render|imagine|paint)\b(?:\s+\w+){{0,3}}?\s+{MEDIA}s?\b", re.I)
GEMINI = re.compile(r"\bgemini\b", re.I)
VIDEO = re.compile(r"\b(?:video|clip|animation|animate)s?\b", re.I)
# the owner's own files or an edit: the video / fileops / media skills do those
OWN = re.compile(r"\bfrom\s+(?:my|these|those|the)\b|\b(?:my|these|those)\s+(?:clips|videos|photos|pictures|images|files|footage)\b|"
                 r"[a-z]:[\\/]|\.(?:png|jpe?g|gif|webp|mp4|mov|mkv|avi)\b|\b(?:smaller|bigger|resize|compress|crop|convert|"
                 r"rotate|trim|cut|merge|edit|rename|delete|move|copy)\b", re.I)
WITH_GEMINI = re.compile(r"\s*\b(?:on|with|using|in|via|through)\s+(?:google\s+)?gemini\b", re.I)


CONCEPT = re.compile(r'\b(?:description|concept|ideas?|prompt\s+for|in\s+words|outline)\b', re.I)


def match(text):
    if CONCEPT.search(text) and not GEMINI.search(text):
        return False
    return (bool(GEN.search(text)) or bool(GEMINI.search(text) and re.search(MEDIA, text, re.I))) and not OWN.search(text)


def parse(text):
    """(kind, prompt): 'video' or 'image', and the owner's words for Gemini without 'with Gemini'."""
    prompt = WITH_GEMINI.sub("", text).strip(" .")
    prompt = re.sub(r"^(?:please\s+|can\s+you\s+|could\s+you\s+)+", "", prompt, flags=re.I)
    return ("video" if VIDEO.search(text) else "image"), prompt[0].upper() + prompt[1:] if prompt else prompt


def ref_of(snapshot, pattern):
    """The ref of the LAST element whose snapshot line matches pattern (the newest answer is at the bottom)."""
    hits = [m.group(1) for line in snapshot.splitlines() if re.search(pattern, line, re.I)
            for m in [re.search(r"\[ref=([^\]]+)\]", line)] if m]
    return hits[-1] if hits else None


def state(snapshot):
    """('done', download ref) | ('working', None) | ('media', ref to hover) | ('text', Gemini's words)."""
    answer = snapshot.split("Conversation with Gemini", 1)[-1]
    download = ref_of(answer, r'button "Download(?: full[- ]size image| video| image)?"')
    if download:
        return "done", download
    if re.search(r'button "Stop (?:response|generating)"|Generating|Creating your (?:video|image)|Loading', answer, re.I):
        return "working", None
    media = ref_of(answer, r'- (?:img|video) "[^"]+"')  # the download control shows only on hover
    if media:
        return "media", media
    words = [re.sub(r"\s*\[ref=[^\]]+\]|\[cursor=pointer\]", "", l).strip(" -:") for l in answer.splitlines()
             if re.search(r"^\s*- (?:paragraph|text|listitem)\b", l)]
    return "text", " ".join(w.split(":", 1)[-1].strip() for w in words)[-500:]


def save(path, prompt):
    """Downloads/<file> -> data/generated/<prompt-slug>_<time>.<ext>; returns the new path."""
    os.makedirs(OUT, exist_ok=True)
    slug = "_".join(re.findall(r"[a-z0-9]+", prompt.lower())[:6]) or "gemini"
    dest = os.path.join(OUT, f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}{os.path.splitext(path)[1]}")
    shutil.move(path, dest)
    return dest


def run(text, session):
    kind, prompt = parse(text)
    wanted = browser.profile_for("gemini")
    with browser.LOCK:
        try:
            if browser.use_server(wanted, session) != wanted:
                return {"reply": "Gemini needs your main Chrome profile (signed in to Gemini Pro), and it isn't connected - "
                                 "connect it in Settings -> Chrome profiles.", "sources": []}
        except RuntimeError as exc:
            return {"reply": str(exc), "sources": []}
        trace(session, "gemini", "opening Gemini in your Chrome", status="start")
        page, t0 = browser._call("browser_navigate", {"url": URL}), time.time()
        while not (box := ref_of(page, r'textbox "Enter a prompt')) and time.time() - t0 < 20:
            if re.search(r'(?:link|button) "Sign in"', page):
                return {"reply": "Gemini isn't signed in in your main Chrome profile - sign in there once, then ask again.",
                        "sources": []}
            time.sleep(2)
            page = browser._call("browser_snapshot", {})
        if not box:
            return {"reply": "Gemini opened, but I couldn't find its prompt box.", "sources": []}
        browser._call("browser_type", {"element": "Gemini prompt box", "ref": box, "text": prompt, "submit": True})
        trace(session, "gemini", f"asked Gemini for the {kind}", status="done")
        trace(session, "gemini", f"waiting for the {kind}" + (" (videos take a few minutes)" if kind == "video" else ""),
              status="start")
        deadline, hovered, idle = time.time() + (480 if kind == "video" else 180), False, 0
        while time.time() < deadline:
            time.sleep(6)
            st, val = state(browser._call("browser_snapshot", {}))
            if st == "done":
                break
            if st == "media" and not hovered:
                browser._call("browser_hover", {"element": f"the generated {kind}", "ref": val})
                hovered = True
            idle = idle + 1 if st == "text" else 0
            if idle >= 3:  # finished writing without any media: Gemini said no, or asked something
                return {"reply": f"Gemini didn't make the {kind}. It said: {val or '(nothing readable)'}", "sources": []}
        else:
            return {"reply": f"Gemini is still working on the {kind} after {int(time.time() - t0)} s - it's open in your "
                             "Chrome; I stopped waiting.", "sources": []}
        trace(session, "gemini", f"the {kind} is ready - downloading", status="done")
        since = time.time()
        browser._call("browser_click", {"element": f"Download the {kind}", "ref": val})
        path = browser.wait_download(since, timeout=120 if kind == "video" else 45)
        if not path:
            return {"reply": f"The {kind} is ready in Gemini, but no download reached {browser.DOWNLOADS}. It's open in your "
                             "Chrome.", "sources": []}
        dest = save(path, prompt)
        trace(session, "gemini", f"saved {os.path.basename(dest)}", status="done")
        return {"reply": f"Here's your {kind} from Gemini: {dest}", "sources": []}


if __name__ == "__main__":
    for t in ["generate an image of a cat on the moon", "create a video of waves at sunset with gemini",
              "draw me a logo for my shop", "make a picture of a red boat", "ask gemini for an image of a castle"]:
        assert match(t), t
    for t in ["make a video from my clips", "make this image smaller", "compress D:/x.png", "create a folder",
              "trim the video to 10 seconds", "what's the weather", "open gemini"]:
        assert not match(t), t
    assert parse("create a video of waves at sunset with Gemini") == ("video", "Create a video of waves at sunset")
    assert parse("please generate an image of a cat") == ("image", "Generate an image of a cat")
    working = '- heading "Conversation with Gemini" [ref=e1]\n  - button "Stop response" [ref=e9]'
    image = working.replace('button "Stop response" [ref=e9]', 'img "Generated image of a cat" [ref=e20]')
    ready = image + '\n  - button "Download full size image" [ref=e31]'
    refused = '- heading "Conversation with Gemini" [ref=e1]\n  - paragraph [ref=e5]: I can\'t create images of real people.'
    assert state(working) == ("working", None) and state(image) == ("media", "e20") and state(ready) == ("done", "e31")
    assert state(refused)[0] == "text" and "real people" in state(refused)[1]
    import tempfile
    tmp = tempfile.mkdtemp()
    f = os.path.join(tmp, "Gemini_Generated_Image.png")
    open(f, "wb").write(b"png")
    assert browser.wait_download(time.time() - 5, timeout=2, folder=tmp) == f
    OUT = tmp + "/out"
    assert os.path.basename(save(f, "Generate an image of a cat")).startswith("generate_an_image_of_a_cat_")
    print("[self-check] gemini OK - routing, prompt, page states (working / hover / ready / refused), download pickup")
