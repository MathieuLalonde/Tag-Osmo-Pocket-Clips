"""Tag Osmo Pocket 4P clips in DaVinci Resolve from embedded ColorGammaSxS.

Install: copy this file into Resolve's Utility Scripts folder
(see README.md for Windows / macOS / Linux paths).

Then in Resolve: Workspace > Scripts > Tag Osmo Color
(restart Resolve once if the menu item is missing).

Files on disk are not modified. Comments are left untouched.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from collections import Counter


# Resolve Media Pool clip colors (SetClipColor names).
CLIP_COLOR_CHOICES = [
    "Orange",
    "Apricot",
    "Yellow",
    "Lime",
    "Olive",
    "Green",
    "Teal",
    "Navy",
    "Blue",
    "Purple",
    "Violet",
    "Pink",
    "Tan",
    "Beige",
    "Brown",
    "Chocolate",
]

# Media Pool swatch colors (0–255).
CLIP_COLOR_RGB = {
	"Orange": (235, 110, 1),
    "Apricot": (255, 168, 51),
    "Yellow": (212, 173, 31),
    "Lime": (159, 198, 21),
    "Olive": (95, 153, 33),
    "Green": (68, 143, 101),
    "Teal": (1, 152, 153),
    "Navy": (0, 82, 120),
    "Blue": (67, 118, 161),
    "Purple": (153, 114, 160),
    "Violet": (208, 86, 141),
    "Pink": (233, 140, 181),
    "Tan": (185, 175, 151),
    "Beige": (196, 160, 124),
    "Brown": (153, 102, 1),
    "Chocolate": (140, 90, 63),
}

DEFAULT_CLIP_COLORS = {
    "Rec.709": "Green",
    "D-Log": "Blue",
    "D-Log2": "Navy",
}

# Keywords for Media Pool smart bins / filters.
KEYWORD_TAGS = {
    "Rec.709": "Osmo Rec.709",
    "D-Log": "Osmo D-Log",
    "D-Log2": "Osmo D-Log2",
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
    "Keywords": ("Keywords", "Keyword"),
}

LENS_RE = re.compile(
    r"(?P<focal>\d+(?:\.\d+)?)\s*mm\s*F(?P<aperture>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

DEFAULT_OPTIONS = {
    "write_metadata": True,
    "write_keywords": False,
    "set_clip_color": False,
    "set_input_color_space": False,
    "set_dlog2_input_lut": False,
    "dlog2_lut_vivid": False,
    "preserve_existing": False,  # only fill empty fields / unset colors
    "run_silently": False,
    "scope": "auto",  # auto | selected | current_bin
    "clip_colors": dict(DEFAULT_CLIP_COLORS),
}

SCOPE_LABELS = {
    "auto": "Selection if any, else current bin",
    "selected": "Selected clips only",
    "current_bin": "Current bin",
}

# Project.GetSetting("colorScienceMode") values that support per-clip Input Color Space.
RCM_COLOR_SCIENCE = {
    "davinciYRGBColorManaged",
    "davinciYRGBColorManagedv2",
    "davinciYRGBColorManagedV2",
}


def color_science_info(project):
    """Return (mode_string, supports_input_cs, short_label)."""
    mode = ""
    try:
        mode = project.GetSetting("colorScienceMode") or ""
    except Exception:
        mode = ""
    mode = str(mode).strip()
    supports = mode in RCM_COLOR_SCIENCE or (
        "ColorManaged" in mode and "davinciYRGB" in mode
    )
    if not mode:
        label = "Color science: unknown"
    elif mode == "davinciYRGB":
        label = "Color science: DaVinci YRGB (no Input CS)"
    elif supports:
        label = "Color science: Color Managed (Input CS available)"
    elif "ACES" in mode.upper() or mode.lower().startswith("aces"):
        label = "Color science: %s (ACES — Input CS differs)" % mode
        supports = False
    else:
        label = "Color science: %s" % mode
    return mode, supports, label


def settings_path():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    folder = os.path.join(base, "Osmo_4p_Metadata")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return os.path.join(folder, "tag_osmo_color.json")


def normalize_clip_colors(raw):
    colors = dict(DEFAULT_CLIP_COLORS)
    if not isinstance(raw, dict):
        return colors
    for profile, default in DEFAULT_CLIP_COLORS.items():
        value = raw.get(profile, default)
        if value in CLIP_COLOR_CHOICES:
            colors[profile] = value
        else:
            colors[profile] = default
    return colors


def load_options():
    opts = dict(DEFAULT_OPTIONS)
    opts["clip_colors"] = dict(DEFAULT_CLIP_COLORS)
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            for key in DEFAULT_OPTIONS:
                if key == "clip_colors":
                    continue
                if key in saved:
                    opts[key] = saved[key]
            if "clip_colors" in saved:
                opts["clip_colors"] = normalize_clip_colors(saved["clip_colors"])
    except (OSError, ValueError, TypeError):
        pass
    if opts.get("scope") not in SCOPE_LABELS:
        opts["scope"] = "auto"
    opts["clip_colors"] = normalize_clip_colors(opts.get("clip_colors"))
    return opts


def save_options(opts):
    path = settings_path()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(opts, fh, indent=2)
    except OSError:
        pass


def lut_search_roots():
    roots = []
    home = os.path.expanduser("~")
    candidates = []

    if sys.platform.startswith("win"):
        programdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        appdata = os.environ.get("APPDATA") or ""
        candidates.extend(
            [
                os.path.join(
                    programdata, "Blackmagic Design", "DaVinci Resolve", "Support", "LUT"
                ),
                os.path.join(
                    appdata, "Blackmagic Design", "DaVinci Resolve", "Support", "LUT"
                ),
                r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT",
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\LUT",
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
                os.path.join(
                    home,
                    "Library",
                    "Application Support",
                    "Blackmagic Design",
                    "DaVinci Resolve",
                    "LUT",
                ),
            ]
        )
    else:
        candidates.extend(
            [
                "/opt/resolve/LUT",
                "/home/resolve/LUT",
                os.path.join(home, ".local", "share", "DaVinciResolve", "LUT"),
            ]
        )

    seen = set()
    for path in candidates:
        if not path:
            continue
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or not os.path.isdir(path):
            continue
        seen.add(norm)
        roots.append(path)
    return roots


def _lut_rank(path):
    name = os.path.basename(path).lower()
    size_score = 2 if "size65" in name else (1 if "size33" in name else 0)
    version = 0
    match = re.search(r"v(\d+(?:\.\d+)?)", name)
    if match:
        try:
            version = float(match.group(1))
        except ValueError:
            version = 0
    return (size_score, version, len(name))


def _pick_best_lut(paths):
    if not paths:
        return None
    return sorted(paths, key=_lut_rank, reverse=True)[0]


def find_dlog2_luts():
    """Locate DJI Pocket 4P D-Log2 → Rec.709 cubes.

    Returns dict with standard/vivid paths and any/both flags.
    """
    standard_hits = []
    vivid_hits = []
    for root in lut_search_roots():
        for dirpath, _dirs, files in os.walk(root):
            for filename in files:
                if not filename.lower().endswith(".cube"):
                    continue
                lower = filename.lower()
                if "pocket 4p" not in lower or "d-log2" not in lower:
                    continue
                if "to rec.709" not in lower and "to rec709" not in lower:
                    continue
                full = os.path.join(dirpath, filename)
                if "vivid" in lower:
                    vivid_hits.append(full)
                else:
                    standard_hits.append(full)

    standard = _pick_best_lut(standard_hits)
    vivid = _pick_best_lut(vivid_hits)
    return {
        "standard": standard,
        "vivid": vivid,
        "any": bool(standard or vivid),
        "both": bool(standard and vivid),
    }


def resolve_dlog2_lut_path(lut_info, vivid):
    if vivid:
        return lut_info.get("vivid") or lut_info.get("standard")
    return lut_info.get("standard") or lut_info.get("vivid")


def lut_property_values(abs_path):
    """Candidate Input LUT strings Resolve may accept."""
    values = []
    if abs_path:
        values.append(abs_path)
        values.append(abs_path.replace("\\", "/"))
    for root in lut_search_roots():
        root_norm = os.path.normpath(root)
        try:
            common = os.path.commonpath([root_norm, os.path.normpath(abs_path)])
        except ValueError:
            continue
        if os.path.normcase(common) != os.path.normcase(root_norm):
            continue
        rel = os.path.relpath(abs_path, root_norm).replace("\\", "/")
        values.append(rel)
        stem, _ext = os.path.splitext(rel)
        if stem:
            values.append(stem)
    deduped = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def try_set_input_lut(clip, abs_path, project=None):
    if not abs_path:
        return None
    if project is not None:
        try:
            project.RefreshLUTList()
        except Exception:
            pass
    for key in ("Input LUT", "3D Input LUT"):
        for value in lut_property_values(abs_path):
            try:
                if clip.SetClipProperty(key, value):
                    return value
            except Exception:
                continue
    return None


def clip_has_input_lut(clip):
    for key in ("Input LUT", "3D Input LUT"):
        try:
            current = clip.GetClipProperty(key)
        except Exception:
            continue
        if nonempty(current) and str(current).strip().lower() not in ("none", "no lut"):
            return True
    return False


def parse_keyword_list(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    text = str(raw).replace(";", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def get_keywords(clip):
    for key in METADATA_ALIASES["Keywords"]:
        try:
            value = clip.GetMetadata(key)
        except Exception:
            continue
        tags = parse_keyword_list(value)
        if tags:
            return tags
    return []


def set_keywords(clip, tags):
    text = ", ".join(tags)
    for key in METADATA_ALIASES["Keywords"]:
        try:
            if clip.SetMetadata(key, text):
                return True
        except Exception:
            continue
    return False


def is_osmo_profile_keyword(tag):
    return tag in KEYWORD_TAGS.values() or tag.startswith("Osmo Rec") or tag.startswith(
        "Osmo D-Log"
    )


def apply_osmo_keyword(clip, color, preserve_existing=False):
    wanted = KEYWORD_TAGS.get(color)
    if not wanted:
        return False
    current = get_keywords(clip)
    if preserve_existing and any(is_osmo_profile_keyword(t) for t in current):
        return wanted in current
    kept = [t for t in current if not is_osmo_profile_keyword(t)]
    if wanted not in kept:
        kept.append(wanted)
    if kept == current:
        return True
    return set_keywords(clip, kept)


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


def get_ui():
    resolve = get_resolve()
    fusion = resolve.Fusion()
    ui = fusion.UIManager
    disp = bmd.UIDispatcher(ui)  # noqa: F821
    return resolve, ui, disp


def clip_file_path(clip):
    path = clip.GetClipProperty("File Path")
    if path:
        return path
    props = clip.GetClipProperty() or {}
    return props.get("File Path") or ""


def clip_color_rgb(name):
    return CLIP_COLOR_RGB.get(name, (128, 128, 128))


def clip_color_hex(name):
    r, g, b = clip_color_rgb(name)
    return "#%02X%02X%02X" % (r, g, b)


def swatch_dot_html(name):
    # Labels render basic HTML; BackgroundColor often does not paint in UIManager.
    return '<font color="%s" size="5">●</font>' % clip_color_hex(name)


def apply_swatch(label_item, name):
    try:
        label_item.Text = swatch_dot_html(name)
    except Exception:
        pass


def pick_clip_color(ui, current):
    """Modal picker: name on the left, color dot on the right. Returns name or None."""
    disp = bmd.UIDispatcher(ui)  # noqa: F821
    rows = []
    for name in CLIP_COLOR_CHOICES:
        rows.append(
            ui.HGroup(
                {"Weight": 0, "Spacing": 8},
                [
                    ui.Button(
                        {
                            "ID": "Pick_%s" % name,
                            "Text": name,
                            "Flat": True,
                            "Weight": 1,
                            "MinimumSize": [120, 24],
                            "Alignment": {"AlignLeft": True},
                        }
                    ),
                    ui.Label(
                        {
                            "ID": "Dot_%s" % name,
                            "Text": swatch_dot_html(name),
                            "Weight": 0,
                            "MinimumSize": [22, 22],
                            "Alignment": {"AlignHCenter": True, "AlignVCenter": True},
                        }
                    ),
                ],
            )
        )

    win = disp.AddWindow(
        {
            "ID": "OsmoColorPickWin",
            "WindowTitle": "Clip color",
            "Geometry": [360, 180, 280, 520],
        },
        [
            ui.VGroup(
                [
                    ui.Label(
                        {
                            "Text": "Current: %s" % current,
                            "Weight": 0,
                        }
                    ),
                    ui.VGap(4),
                    ui.VGroup({"Weight": 1, "Spacing": 4}, rows),
                    ui.Button({"ID": "CancelPick", "Text": "Cancel", "Weight": 0}),
                ]
            )
        ],
    )
    items = win.GetItems()
    for name in CLIP_COLOR_CHOICES:
        apply_swatch(items["Dot_%s" % name], name)

    result = {"color": None}

    def _cancel(_ev):
        result["color"] = None
        disp.ExitLoop()

    def _make_pick(color_name):
        def _pick(_ev):
            result["color"] = color_name
            disp.ExitLoop()

        return _pick

    win.On.CancelPick.Clicked = _cancel
    win.On.OsmoColorPickWin.Close = _cancel
    for name in CLIP_COLOR_CHOICES:
        win.On["Pick_%s" % name].Clicked = _make_pick(name)

    win.Show()
    disp.RunLoop()
    win.Hide()
    return result["color"]


def nonempty(value):
    if value is None:
        return False
    text = str(value).strip()
    return text not in ("", "N/A", "n/a")


def get_metadata_field(clip, field):
    """Read a metadata column, trying known aliases."""
    for key in METADATA_ALIASES.get(field, (field,)):
        try:
            value = clip.GetMetadata(key)
        except Exception:
            continue
        if nonempty(value):
            return str(value).strip()
    return ""


def set_metadata_field(clip, field, value, preserve_existing=False):
    """Write a metadata column, trying known aliases. Return False if none stuck."""
    if not nonempty(value):
        return True
    if preserve_existing and nonempty(get_metadata_field(clip, field)):
        return True
    text = str(value).strip()
    for key in METADATA_ALIASES.get(field, (field,)):
        try:
            if clip.SetMetadata(key, text):
                return True
        except Exception:
            continue
    return False


def clip_has_clip_color(clip):
    try:
        current = clip.GetClipColor()
    except Exception:
        return False
    return nonempty(current) and str(current).strip().lower() not in ("none", "no color")


def clip_has_input_color_space(clip):
    try:
        current = clip.GetClipProperty("Input Color Space")
    except Exception:
        return False
    return nonempty(current) and str(current).strip().lower() not in ("none", "same as project")


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

    return {
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


def try_set_input_color_space(clip, color):
    candidates = INPUT_COLOR_SPACE.get(color, ())
    for name in candidates:
        try:
            if clip.SetClipProperty("Input Color Space", name):
                return name
        except Exception:
            continue
    return None


def collect_clips(pool, scope):
    selected = pool.GetSelectedClips() or []
    folder = pool.GetCurrentFolder()
    bin_name = folder.GetName() if folder else "(none)"

    if scope == "selected":
        return selected, "selected clips"
    if scope == "current_bin":
        clips = (folder.GetClipList() if folder else None) or []
        return clips, "bin '%s'" % bin_name
    # auto
    if selected:
        return selected, "selected clips"
    clips = (folder.GetClipList() if folder else None) or []
    return clips, "bin '%s'" % bin_name


def tag_clip(clip, options, project=None):
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

    parts = [color]
    failed = []
    preserve = bool(options.get("preserve_existing", False))

    if options.get("write_metadata", True):
        fields = build_metadata(keys, color)
        for field, value in fields.items():
            if not nonempty(value):
                continue
            if not set_metadata_field(clip, field, value, preserve_existing=preserve):
                failed.append(field)

        old_description = clip.GetMetadata("Description") or ""
        if (not preserve) and str(old_description).startswith("Osmo Pocket 4P"):
            clip.SetMetadata("Description", "")

    if options.get("write_keywords", False):
        if apply_osmo_keyword(clip, color, preserve_existing=preserve):
            parts.append("kw=%s" % KEYWORD_TAGS.get(color, color))
        else:
            failed.append("Keywords")

    if options.get("set_clip_color", False):
        color_map = normalize_clip_colors(options.get("clip_colors"))
        clip_color = color_map.get(color)
        if clip_color:
            if preserve and clip_has_clip_color(clip):
                parts.append("color kept")
            else:
                clip.SetClipColor(clip_color)
                parts.append("color=%s" % clip_color)

    if options.get("set_input_color_space", False):
        if preserve and clip_has_input_color_space(clip):
            parts.append("Input CS kept")
        else:
            idt = try_set_input_color_space(clip, color)
            if idt:
                parts.append("IDT=%s" % idt)
            elif color == "D-Log2":
                parts.append("no CST for D-Log2")
            else:
                parts.append("Input CS not set")

    if color == "D-Log2" and options.get("set_dlog2_input_lut", False):
        if preserve and clip_has_input_lut(clip):
            parts.append("Input LUT kept")
        else:
            lut_info = options.get("_dlog2_luts") or find_dlog2_luts()
            lut_path = resolve_dlog2_lut_path(
                lut_info, bool(options.get("dlog2_lut_vivid", False))
            )
            applied = try_set_input_lut(clip, lut_path, project=project)
            if applied:
                kind = "vivid" if options.get("dlog2_lut_vivid") else "standard"
                parts.append("LUT=%s" % kind)
            else:
                parts.append("Input LUT not set")

    note = " · ".join(parts)
    if failed:
        note = "%s · unset: %s" % (note, ", ".join(failed))
    return name, color, note


def show_message(title, text):
    try:
        _resolve, ui, disp = get_ui()
    except Exception:
        print("%s\n%s" % (title, text))
        return

    win = disp.AddWindow(
        {
            "ID": "OsmoMsgWin",
            "WindowTitle": title,
            "Geometry": [240, 220, 640, 420],
        },
        [
            ui.VGroup(
                [
                    ui.TextEdit(
                        {
                            "ID": "Body",
                            "ReadOnly": True,
                            "Weight": 1,
                        }
                    ),
                    ui.Button({"ID": "CloseButton", "Text": "Close", "Weight": 0}),
                ]
            )
        ],
    )
    items = win.GetItems()
    items["Body"].SetPlainText(text)

    def _close(_ev):
        disp.ExitLoop()

    win.On.CloseButton.Clicked = _close
    win.On.OsmoMsgWin.Close = _close
    win.Show()
    disp.RunLoop()
    win.Hide()


def show_options_dialog(defaults, selected_count, bin_name, bin_count, project=None):
    """Return options dict, or None if cancelled / UI unavailable."""
    try:
        _resolve, ui, disp = get_ui()
    except Exception as exc:
        print("Tag Osmo Pocket Clips: options UI unavailable (%s)" % exc)
        return None

    scope_keys = ["auto", "selected", "current_bin"]
    try:
        scope_index = scope_keys.index(defaults.get("scope", "auto"))
    except ValueError:
        scope_index = 0

    clip_colors = normalize_clip_colors(defaults.get("clip_colors"))
    # (button_id, swatch_id, profile_label)
    color_rows = [
        ("Rec709Btn", "Rec709Swatch", "Rec.709"),
        ("DLogBtn", "DLogSwatch", "D-Log"),
        ("DLog2Btn", "DLog2Swatch", "D-Log2"),
    ]
    chosen_colors = {
        "Rec.709": clip_colors["Rec.709"],
        "D-Log": clip_colors["D-Log"],
        "D-Log2": clip_colors["D-Log2"],
    }

    _mode, supports_input_cs, science_label = color_science_info(project) if project else (
        "",
        False,
        "Color science: unknown",
    )
    want_input_cs = bool(defaults.get("set_input_color_space", False)) and supports_input_cs
    if supports_input_cs:
        input_cs_text = "Set Input Color Space (D-Log2 not supported)"
    else:
        input_cs_text = "Set Input Color Space — requires Color Managed project"

    lut_info = find_dlog2_luts()
    only_vivid = bool(lut_info["vivid"] and not lut_info["standard"])
    only_standard = bool(lut_info["standard"] and not lut_info["vivid"])
    if only_vivid:
        vivid_checked = True
        vivid_enabled = False
    elif only_standard:
        vivid_checked = False
        vivid_enabled = False
    elif lut_info["both"]:
        vivid_checked = bool(defaults.get("dlog2_lut_vivid", False))
        vivid_enabled = True
    else:
        vivid_checked = False
        vivid_enabled = False

    want_dlog2_lut = bool(defaults.get("set_dlog2_input_lut", False)) and lut_info["any"]
    dlog2_lut_text = "Apply D-Log2 Input LUT (if present)"

    def clip_word(n):
        return "clip" if n == 1 else "clips"

    def scope_result_text(scope_key):
        if scope_key == "selected":
            if selected_count:
                return "Will tag %d selected %s." % (selected_count, clip_word(selected_count))
            return "No clips selected."
        if scope_key == "current_bin":
            return "Will tag %d %s in bin '%s'." % (bin_count, clip_word(bin_count), bin_name)
        # auto
        if selected_count:
            return "Will tag %d selected %s." % (selected_count, clip_word(selected_count))
        return "Will tag %d %s in bin '%s'." % (bin_count, clip_word(bin_count), bin_name)

    win = disp.AddWindow(
        {
            "ID": "OsmoOptWin",
            "WindowTitle": "Tag Osmo Pocket Clips",
            "Geometry": [300, 140, 480, 560],
        },
        [
            ui.VGroup(
                {"Weight": 1, "Spacing": 4},
                [
                    ui.Label(
                        {
                            "ID": "Intro",
                            "Text": "Fill Resolve metadata from Osmo Pocket 4P ColorGammaSxS.",
                            "WordWrap": True,
                            "Weight": 0,
                        }
                    ),
                    ui.Label(
                        {
                            "ID": "Science",
                            "Text": science_label,
                            "WordWrap": True,
                            "Weight": 0,
                        }
                    ),
                    ui.VGap(8),
                    ui.Label({"Text": "Scope", "Weight": 0}),
                    ui.ComboBox({"ID": "Scope", "Weight": 0}),
                    ui.Label(
                        {
                            "ID": "ScopeResult",
                            "Text": scope_result_text(scope_keys[scope_index]),
                            "WordWrap": True,
                            "Weight": 0,
                        }
                    ),
                    ui.VGap(8),
                    ui.Label({"Text": "Actions", "Weight": 0}),
                    ui.CheckBox(
                        {
                            "ID": "WriteMetadata",
                            "Text": "Write metadata columns (Gamma Notes, Lens, WB, …)",
                            "Checked": bool(defaults.get("write_metadata", True)),
                            "Weight": 0,
                        }
                    ),
                    ui.CheckBox(
                        {
                            "ID": "SetInputCS",
                            "Text": input_cs_text,
                            "Checked": want_input_cs,
                            "Enabled": supports_input_cs,
                            "Weight": 0,
                        }
                    ),
                    ui.HGroup(
                        {"Weight": 0, "Spacing": 12},
                        [
                            ui.CheckBox(
                                {
                                    "ID": "SetDLog2LUT",
                                    "Text": dlog2_lut_text,
                                    "Checked": want_dlog2_lut,
                                    "Enabled": lut_info["any"],
                                    "Weight": 1,
                                }
                            ),
                            ui.CheckBox(
                                {
                                    "ID": "DLog2Vivid",
                                    "Text": "Use Vivid LUT",
                                    "Checked": vivid_checked,
                                    "Enabled": vivid_enabled and want_dlog2_lut,
                                    "Weight": 0,
                                }
                            ),
                        ],
                    ),
                    ui.CheckBox(
                        {
                            "ID": "WriteKeywords",
                            "Text": "Write Keywords (Osmo Rec.709 / D-Log / D-Log2)",
                            "Checked": bool(defaults.get("write_keywords", False)),
                            "Weight": 0,
                        }
                    ),
                    ui.CheckBox(
                        {
                            "ID": "PreserveExisting",
                            "Text": "Don't replace existing values",
                            "Checked": bool(defaults.get("preserve_existing", False)),
                            "Weight": 0,
                        }
                    ),
                    ui.VGap(8),
                    ui.CheckBox(
                        {
                            "ID": "SetClipColor",
                            "Text": "Set clip color labels",
                            "Checked": bool(defaults.get("set_clip_color", False)),
                            "Weight": 0,
                        }
                    ),
                    ui.HGroup(
                        {"Weight": 0, "Spacing": 8},
                        [
                            ui.Label({"Text": "Rec.709", "Weight": 0.28}),
                            ui.Button({"ID": "Rec709Btn", "Text": chosen_colors["Rec.709"], "Weight": 0.6}),
                            ui.Label(
                                {
                                    "ID": "Rec709Swatch",
                                    "Text": swatch_dot_html(chosen_colors["Rec.709"]),
                                    "Weight": 0,
                                    "MinimumSize": [22, 22],
                                }
                            ),
                        ],
                    ),
                    ui.HGroup(
                        {"Weight": 0, "Spacing": 8},
                        [
                            ui.Label({"Text": "D-Log", "Weight": 0.28}),
                            ui.Button({"ID": "DLogBtn", "Text": chosen_colors["D-Log"], "Weight": 0.6}),
                            ui.Label(
                                {
                                    "ID": "DLogSwatch",
                                    "Text": swatch_dot_html(chosen_colors["D-Log"]),
                                    "Weight": 0,
                                    "MinimumSize": [22, 22],
                                }
                            ),
                        ],
                    ),
                    ui.HGroup(
                        {"Weight": 0, "Spacing": 8},
                        [
                            ui.Label({"Text": "D-Log2", "Weight": 0.28}),
                            ui.Button({"ID": "DLog2Btn", "Text": chosen_colors["D-Log2"], "Weight": 0.6}),
                            ui.Label(
                                {
                                    "ID": "DLog2Swatch",
                                    "Text": swatch_dot_html(chosen_colors["D-Log2"]),
                                    "Weight": 0,
                                    "MinimumSize": [22, 22],
                                }
                            ),
                        ],
                    ),
                    # Flexible spacer: keeps Cancel/Run/silent parked at the bottom.
                    ui.VGap(0, 1.0),
                    ui.HGroup(
                        {"Weight": 0},
                        [
                            ui.Button({"ID": "CancelButton", "Text": "Cancel"}),
                            ui.Button({"ID": "RunButton", "Text": "Run", "Default": True}),
                        ],
                    ),
                    ui.CheckBox(
                        {
                            "ID": "RunSilently",
                            "Text": "Run silently (no progress or results)",
                            "Checked": bool(defaults.get("run_silently", False)),
                            "Weight": 0,
                        }
                    ),
                ]
            )
        ],
    )
    items = win.GetItems()
    for label in (SCOPE_LABELS[k] for k in scope_keys):
        items["Scope"].AddItem(label)
    items["Scope"].CurrentIndex = scope_index

    # Some UIManager builds ignore constructor Enabled; force after create.
    try:
        items["SetInputCS"].Enabled = supports_input_cs
        if not supports_input_cs:
            items["SetInputCS"].Checked = False
    except Exception:
        pass
    try:
        items["SetDLog2LUT"].Enabled = lut_info["any"]
        if not lut_info["any"]:
            items["SetDLog2LUT"].Checked = False
        items["DLog2Vivid"].Checked = vivid_checked
        items["DLog2Vivid"].Enabled = vivid_enabled and bool(items["SetDLog2LUT"].Checked)
    except Exception:
        pass

    for _btn_id, swatch_id, profile in color_rows:
        apply_swatch(items[swatch_id], chosen_colors[profile])

    result = {"cancelled": True, "options": dict(defaults)}

    def _cancel(_ev):
        result["cancelled"] = True
        disp.ExitLoop()

    def _make_color_pick(profile, btn_id, swatch_id):
        def _pick(_ev):
            picked = pick_clip_color(ui, chosen_colors[profile])
            if picked:
                chosen_colors[profile] = picked
                items[btn_id].Text = picked
                apply_swatch(items[swatch_id], picked)

        return _pick

    def _scope_changed(_ev):
        idx = int(items["Scope"].CurrentIndex)
        if idx < 0 or idx >= len(scope_keys):
            idx = 0
        items["ScopeResult"].Text = scope_result_text(scope_keys[idx])

    def _sync_vivid_enabled(_ev=None):
        lut_on = bool(items["SetDLog2LUT"].Checked) if lut_info["any"] else False
        try:
            items["DLog2Vivid"].Enabled = vivid_enabled and lut_on
            if only_vivid:
                items["DLog2Vivid"].Checked = True
            elif only_standard:
                items["DLog2Vivid"].Checked = False
        except Exception:
            pass

    def _run(_ev):
        idx = int(items["Scope"].CurrentIndex)
        if idx < 0 or idx >= len(scope_keys):
            idx = 0
        set_input = bool(items["SetInputCS"].Checked) if supports_input_cs else False
        set_lut = bool(items["SetDLog2LUT"].Checked) if lut_info["any"] else False
        if only_vivid:
            vivid = True
        elif only_standard:
            vivid = False
        else:
            vivid = bool(items["DLog2Vivid"].Checked) if set_lut else False
        result["options"] = {
            "write_metadata": bool(items["WriteMetadata"].Checked),
            "write_keywords": bool(items["WriteKeywords"].Checked),
            "set_clip_color": bool(items["SetClipColor"].Checked),
            "set_input_color_space": set_input,
            "set_dlog2_input_lut": set_lut,
            "dlog2_lut_vivid": vivid,
            "preserve_existing": bool(items["PreserveExisting"].Checked),
            "run_silently": bool(items["RunSilently"].Checked),
            "scope": scope_keys[idx],
            "clip_colors": dict(chosen_colors),
            "_dlog2_luts": lut_info,
        }
        result["cancelled"] = False
        disp.ExitLoop()

    win.On.CancelButton.Clicked = _cancel
    win.On.RunButton.Clicked = _run
    win.On.OsmoOptWin.Close = _cancel
    win.On.Scope.CurrentIndexChanged = _scope_changed
    win.On.SetDLog2LUT.Clicked = _sync_vivid_enabled
    for btn_id, swatch_id, profile in color_rows:
        win.On[btn_id].Clicked = _make_color_pick(profile, btn_id, swatch_id)

    win.Show()
    disp.RunLoop()
    win.Hide()

    if result["cancelled"]:
        return None
    return result["options"]


def begin_progress(total):
    """Non-modal progress window updated during tagging."""
    try:
        _resolve, ui, disp = get_ui()
    except Exception:
        return None
    win = disp.AddWindow(
        {
            "ID": "OsmoProgWin",
            "WindowTitle": "Tag Osmo Pocket Clips",
            "Geometry": [360, 280, 420, 120],
        },
        [
            ui.VGroup(
                [
                    ui.Label({"ID": "Status", "Text": "Starting…", "Weight": 0}),
                    ui.Label({"ID": "Detail", "Text": "", "Weight": 0, "WordWrap": True}),
                ]
            )
        ],
    )
    items = win.GetItems()
    win.Show()
    return {"win": win, "items": items, "total": total}


def update_progress(prog, index, name):
    if not prog:
        return
    total = prog["total"]
    try:
        prog["items"]["Status"].Text = "Tagging %d of %d…" % (index, total)
        prog["items"]["Detail"].Text = name or ""
        prog["win"].RecalcLayout()
    except Exception:
        pass


def end_progress(prog):
    if not prog:
        return
    try:
        prog["win"].Hide()
    except Exception:
        pass


def format_report(clips_count, source, options, counts, lines, failures):
    enabled = []
    if options.get("write_metadata"):
        enabled.append("metadata")
    if options.get("write_keywords"):
        enabled.append("keywords")
    if options.get("set_clip_color"):
        colors = normalize_clip_colors(options.get("clip_colors"))
        mapping = ", ".join("%s=%s" % (k, colors[k]) for k in ("Rec.709", "D-Log", "D-Log2"))
        enabled.append("clip color (%s)" % mapping)
    if options.get("set_input_color_space"):
        enabled.append("Input Color Space")
    if options.get("set_dlog2_input_lut"):
        kind = "vivid" if options.get("dlog2_lut_vivid") else "standard"
        enabled.append("D-Log2 Input LUT (%s)" % kind)
    if options.get("preserve_existing"):
        enabled.append("keep existing")

    parts = [
        "Tagged %d %s from %s." % (clips_count, "clip" if clips_count == 1 else "clips", source),
        "Actions: %s" % (", ".join(enabled) if enabled else "(none)"),
        "",
        "Counts: " + ", ".join("%s=%d" % item for item in sorted(counts.items())),
    ]
    if failures:
        parts.extend(["", "Problems (%d):" % len(failures), "\n".join(failures)])
    parts.extend(["", "Clips:", "\n".join(lines) if lines else "(none)"])
    return "\n".join(parts)


def main():
    try:
        resolve = get_resolve()
    except Exception as exc:
        show_message("Tag Osmo Pocket Clips", "Could not connect to Resolve:\n%s" % exc)
        return

    if resolve is None:
        show_message(
            "Tag Osmo Pocket Clips",
            "Resolve is not running, or scripting is unavailable.\n"
            "Run this from Workspace > Scripts inside Resolve.",
        )
        return

    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        show_message("Tag Osmo Pocket Clips", "Open a project first.")
        return

    pool = project.GetMediaPool()
    folder = pool.GetCurrentFolder()
    selected = pool.GetSelectedClips() or []
    bin_clips = (folder.GetClipList() if folder else None) or []
    bin_name = folder.GetName() if folder else "(none)"

    options = show_options_dialog(
        load_options(),
        len(selected),
        bin_name,
        len(bin_clips),
        project=project,
    )
    if options is None:
        return

    # Don't persist ephemeral LUT scan payload.
    to_save = dict(options)
    to_save.pop("_dlog2_luts", None)
    save_options(to_save)

    if not (
        options.get("write_metadata")
        or options.get("write_keywords")
        or options.get("set_clip_color")
        or options.get("set_input_color_space")
        or options.get("set_dlog2_input_lut")
    ):
        show_message("Tag Osmo Pocket Clips", "Nothing selected to do. Enable at least one action.")
        return

    clips, source = collect_clips(pool, options.get("scope", "auto"))
    if not clips:
        show_message(
            "Tag Osmo Pocket Clips",
            "No clips to tag for scope '%s'.\nSelect clips or open a bin that contains them."
            % SCOPE_LABELS.get(options.get("scope"), options.get("scope")),
        )
        return

    counts = Counter()
    lines = []
    failures = []
    silent = bool(options.get("run_silently", False))
    prog = None if silent else begin_progress(len(clips))
    try:
        for index, clip in enumerate(clips, start=1):
            try:
                name, color, note = tag_clip(clip, options, project=project)
            except Exception as exc:
                name, color, note = "?", None, str(exc)
            update_progress(prog, index, name)
            counts[color or "skipped"] += 1
            line = "%s  —  %s" % (name, note)
            lines.append(line)
            if color is None or "unset:" in note or "missing file" in note or "no file path" in note:
                failures.append(line)
    finally:
        end_progress(prog)

    if silent:
        return

    report = format_report(len(clips), source, options, counts, lines, failures)
    print(report)
    show_message("Tag Osmo Pocket Clips — Results", report)


if __name__ == "__main__":
    main()
