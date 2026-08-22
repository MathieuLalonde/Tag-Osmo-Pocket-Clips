"""Tag Osmo Pocket 4P clips in DaVinci Resolve from embedded ColorGammaSxS.

Install (already copied if you ran the project installer):
  %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\

Then in Resolve:
  1. Import the MP4s and open that bin (or select the clips).
  2. Workspace > Scripts > Tag Osmo Color
  Restart Resolve once if the menu item is missing.

Selected clips are tagged if any are selected; otherwise the current bin is tagged.
Files on disk are not modified.
"""

from __future__ import annotations

import os
import re
import struct
import sys
from collections import Counter


CLIP_COLOR = {
    "Rec.709": "Green",
    "D-Log": "Blue",
    "D-Log2": "Purple",
}

# First matching dropdown string wins. D-Log2 has no native CST in Resolve yet.
INPUT_COLOR_SPACE = {
    "Rec.709": ("Rec.709", "Rec.709 Gamma 2.4"),
    "D-Log": ("DJI D-Gamut/D-Log", "DJI D-Log"),
    "D-Log2": (),
}

COLOR_SPACE_NOTES = {
    "Rec.709": "Rec.709",
    "D-Log": "D-Gamut",
    "D-Log2": "D-Gamut2",
}

# Resolve metadata keys, with fallbacks if a given build uses a shorter name.
METADATA_ALIASES = {
    "Camera Manufacturer": ("Camera Manufacturer",),
    "Camera Type": ("Camera Type",),
    "Camera Serial #": ("Camera Serial #", "Camera Serial"),
    "Camera Firmware": ("Camera Firmware",),
    "Camera Notes": ("Camera Notes",),
    "Lens Type": ("Lens Type", "Lens"),
    "Lens Notes": ("Lens Notes",),
    "Focal Point (mm)": ("Focal Point (mm)", "Focal Point"),
    "Camera Aperture": ("Camera Aperture", "Aperture"),
    "Camera Aperture Type": ("Camera Aperture Type",),
    "White Point (Kelvin)": ("White Point (Kelvin)", "White Point"),
    "White Balance Tint": ("White Balance Tint", "WB Tint"),
    "ISO": ("ISO",),
    "Gamma Notes": ("Gamma Notes",),
    "Color Space Notes": ("Color Space Notes",),
}

LENS_RE = re.compile(
    r"(?P<focal>\d+(?:\.\d+)?)\s*mm\s*F(?P<aperture>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def extract_moov(path):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_end = fh.tell()
        fh.seek(0)
        while fh.tell() + 8 <= file_end:
            header = fh.read(8)
            if len(header) < 8:
                break
            size, typ = struct.unpack(">I4s", header)
            hdr_len = 8
            offset = fh.tell() - 8
            if size == 1:
                large = fh.read(8)
                if len(large) < 8:
                    break
                size = struct.unpack(">Q", large)[0]
                hdr_len = 16
            elif size == 0:
                size = file_end - offset
            if size < hdr_len:
                break
            if typ == b"moov":
                fh.seek(offset)
                return fh.read(size)
            fh.seek(offset + size)
    return None


def parse_dji_keys(moov):
    idx = moov.find(b"com.dji.camera.CameraModel")
    if idx < 0:
        return {}
    keys_tag = moov.rfind(b"keys", 0, idx)
    if keys_tag < 4:
        return {}
    keys_size = struct.unpack_from(">I", moov, keys_tag - 4)[0]
    body = moov[keys_tag + 4 : keys_tag - 4 + keys_size]
    if len(body) < 8:
        return {}
    count = struct.unpack_from(">I", body, 4)[0]
    names = []
    pos = 8
    for _ in range(count):
        entry_size = struct.unpack_from(">I", body, pos)[0]
        names.append(body[pos + 8 : pos + entry_size].decode("utf-8", "replace"))
        pos += entry_size
    ilst_off = keys_tag - 4 + keys_size
    ilst_size, ilst_type = struct.unpack_from(">I4s", moov, ilst_off)
    if ilst_type != b"ilst":
        return {}
    ilst = moov[ilst_off : ilst_off + ilst_size]
    values = {}
    pos = 8
    while pos + 8 <= len(ilst):
        entry_size, raw_index = struct.unpack_from(">I4s", ilst, pos)
        index = struct.unpack(">I", raw_index)[0]
        entry = ilst[pos : pos + entry_size]
        data_at = entry.find(b"data")
        if data_at >= 4:
            data_size = struct.unpack_from(">I", entry, data_at - 4)[0]
            payload = entry[data_at - 4 + 16 : data_at - 4 + data_size]
            values[index] = payload.split(b"\x00")[0].decode("utf-8", "replace").strip()
        if entry_size <= 0:
            break
        pos += entry_size
    return {names[i]: values.get(i + 1, "") for i in range(len(names))}


def read_osmo_metadata(path):
    moov = extract_moov(path)
    if not moov:
        return {}
    return parse_dji_keys(moov)


def get_resolve():
    try:
        return bmd.scriptapp("Resolve")  # noqa: F821 — injected inside Resolve
    except NameError:
        pass
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        module_path = os.path.join(
            program_data,
            "Blackmagic Design",
            "DaVinci Resolve",
            "Support",
            "Developer",
            "Scripting",
            "Modules",
        )
        if module_path not in sys.path:
            sys.path.append(module_path)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")


def clip_file_path(clip):
    path = clip.GetClipProperty("File Path")
    if path:
        return path
    props = clip.GetClipProperty() or {}
    return props.get("File Path") or ""


def nonempty(value):
    if value is None:
        return False
    text = str(value).strip()
    return text not in ("", "N/A", "n/a")


def set_metadata_field(clip, field, value):
    """Write a metadata column, trying known aliases. Return False if none stuck."""
    if not nonempty(value):
        return True
    text = str(value).strip()
    for key in METADATA_ALIASES.get(field, (field,)):
        try:
            if clip.SetMetadata(key, text):
                return True
        except Exception:
            continue
    return False


def parse_lens(lens):
    focal = aperture = ""
    if not lens:
        return focal, aperture
    match = LENS_RE.search(lens)
    if match:
        focal = match.group("focal")
        aperture = match.group("aperture")
    return focal, aperture


def build_metadata(keys, color):
    """Map DJI QuickTime keys onto Resolve metadata columns."""
    lens = (keys.get("com.dji.camera.LensType") or "").strip()
    wb = (keys.get("com.dji.camera.WhiteBalanceKelvin") or "").strip()
    tint = (keys.get("com.dji.camera.WhiteBalanceTintCc") or "").strip()
    ei = (keys.get("com.dji.camera.ExposureIndexAsa") or "").strip()
    serial = (keys.get("com.dji.camera.CameraSerialNumber") or "").strip()
    firmware = (keys.get("com.dji.camera.SupVersion") or "").strip()
    model_id = (keys.get("com.dji.camera.CameraModel") or "").strip()
    focal, aperture = parse_lens(lens)

    fields = {
        "Camera Manufacturer": "DJI",
        "Camera Type": "Osmo Pocket 4P",
        "Camera Serial #": serial,
        "Camera Firmware": firmware if firmware not in ("", "N/A") else "",
        "Camera Notes": "Model ID %s" % model_id if model_id else "",
        "Lens Type": lens,
        "Focal Point (mm)": focal,
        "Camera Aperture": aperture,
        "Camera Aperture Type": "F-stop" if aperture else "",
        "White Point (Kelvin)": wb if wb not in ("", "0") else "",
        "White Balance Tint": tint,
        "ISO": ei,
        "Gamma Notes": color,
        "Color Space Notes": COLOR_SPACE_NOTES.get(color, color),
    }
    return fields


def try_set_input_color_space(clip, color):
    candidates = INPUT_COLOR_SPACE.get(color, ())
    for name in candidates:
        try:
            if clip.SetClipProperty("Input Color Space", name):
                return name
        except Exception:
            continue
    return None


def collect_clips(pool):
    selected = pool.GetSelectedClips() or []
    if selected:
        return selected, "selected clips"
    folder = pool.GetCurrentFolder()
    clips = folder.GetClipList() or []
    return clips, "bin '%s'" % folder.GetName()


def tag_clip(clip):
    name = clip.GetName() or clip.GetClipProperty("Clip Name") or "(unnamed)"
    path = clip_file_path(clip)
    if not path:
        return name, None, "no file path (offline?)"
    if not os.path.isfile(path):
        return name, None, "missing file: %s" % path

    keys = read_osmo_metadata(path)
    color = keys.get("com.dji.camera.ColorGammaSxS")
    if not color:
        return name, None, "no ColorGammaSxS (not an Osmo movie?)"

    fields = build_metadata(keys, color)
    failed = []
    for field, value in fields.items():
        if not nonempty(value):
            continue
        if not set_metadata_field(clip, field, value):
            failed.append("%s: %s" % (field, value))

    old_description = clip.GetMetadata("Description") or ""
    if str(old_description).startswith("Osmo Pocket 4P"):
        clip.SetMetadata("Description", "")

    clip_color = CLIP_COLOR.get(color)
    if clip_color:
        clip.SetClipColor(clip_color)

    idt = try_set_input_color_space(clip, color)
    note = color if idt else (color + " (tagged; Input CS not set)")
    if failed:
        note = "%s | unset: %s" % (note, ", ".join(failed))
    return name, color, note


def show_dialog(title, text):
    try:
        resolve = get_resolve()
        fusion = resolve.Fusion()
        ui = fusion.UIManager
        disp = bmd.UIDispatcher(ui)  # noqa: F821
    except Exception:
        print(text)
        return

    win = disp.AddWindow(
        {
            "ID": "OsmoTagWin",
            "WindowTitle": title,
            "Geometry": [200, 200, 720, 480],
        },
        [
            ui.VGroup(
                [
                    ui.TextEdit(
                        {
                            "ID": "Results",
                            "Text": text,
                            "ReadOnly": True,
                            "Weight": 1,
                        }
                    ),
                    ui.Button({"ID": "CloseButton", "Text": "Close"}),
                ]
            )
        ],
    )
    items = win.GetItems()
    items["Results"].SetPlainText(text)

    def _close(_ev):
        disp.ExitLoop()

    win.On.CloseButton.Clicked = _close
    win.On.OsmoTagWin.Close = _close
    win.Show()
    disp.RunLoop()
    win.Hide()


def main():
    try:
        resolve = get_resolve()
    except Exception as exc:
        show_dialog("Tag Osmo Color", "Could not connect to Resolve:\n%s" % exc)
        return

    if resolve is None:
        show_dialog(
            "Tag Osmo Color",
            "Resolve is not running, or scripting is unavailable.\n"
            "Run this from Workspace > Scripts inside Resolve.",
        )
        return

    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        show_dialog("Tag Osmo Color", "Open a project first.")
        return

    pool = project.GetMediaPool()
    clips, source = collect_clips(pool)
    if not clips:
        show_dialog(
            "Tag Osmo Color",
            "No clips to tag. Select clips in the Media Pool, or open a bin that contains them.",
        )
        return

    counts = Counter()
    lines = []
    for clip in clips:
        try:
            name, color, note = tag_clip(clip)
        except Exception as exc:
            name, color, note = "?", None, str(exc)
        counts[color or "skipped"] += 1
        lines.append("%s  —  %s" % (name, note))

    summary = [
        "Tagged %d clip(s) from %s." % (len(clips), source),
        "",
        "Counts: " + ", ".join("%s=%d" % item for item in sorted(counts.items())),
        "",
        "Clip colors: Rec.709=Green, D-Log=Blue, D-Log2=Purple",
        "Metadata: Gamma Notes, Color Space Notes, Lens Type, WB, ISO/EI, camera/lens fields.",
        "Comments are left untouched.",
        "",
        "\n".join(lines),
    ]
    report = "\n".join(summary)
    print(report)
    show_dialog("Tag Osmo Color", report)


if __name__ == "__main__":
    main()
