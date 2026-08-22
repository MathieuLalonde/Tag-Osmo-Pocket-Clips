"""Read DJI Osmo Pocket 4P color-space metadata from MP4 files.

DaVinci Resolve only sees the standard HEVC `colr` nclx box, which DJI always
writes as BT.709. The actual profile lives in a QuickTime Keys atom:

    com.dji.camera.ColorGammaSxS = Rec.709 | D-Log | D-Log2

Usage:
    python read_osmo_metadata.py
    python read_osmo_metadata.py K:\\DCIM\\DJI_001
    python read_osmo_metadata.py path\\to\\clip.MP4
"""

from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
from pathlib import Path


DJI_KEYS = (
    "com.dji.camera.ColorGammaSxS",
    "com.dji.camera.LensType",
    "com.dji.camera.WhiteBalanceKelvin",
    "com.dji.camera.WhiteBalanceTintCc",
    "com.dji.camera.ExposureIndexAsa",
    "com.dji.camera.CameraModel",
    "com.dji.camera.CameraSerialNumber",
    "com.dji.camera.SupVersion",
)


def _read_box_header(fh, file_end: int):
    offset = fh.tell()
    header = fh.read(8)
    if len(header) < 8:
        return None
    size, typ = struct.unpack(">I4s", header)
    hdr_len = 8
    if size == 1:
        large = fh.read(8)
        if len(large) < 8:
            return None
        size = struct.unpack(">Q", large)[0]
        hdr_len = 16
    elif size == 0:
        size = file_end - offset
    return offset, size, typ, hdr_len


def extract_moov(path: Path) -> bytes | None:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_end = fh.tell()
        fh.seek(0)
        while fh.tell() + 8 <= file_end:
            box = _read_box_header(fh, file_end)
            if box is None:
                break
            offset, size, typ, hdr_len = box
            if size < hdr_len:
                break
            if typ == b"moov":
                fh.seek(offset)
                return fh.read(size)
            fh.seek(offset + size)
    return None


def parse_dji_keys(moov: bytes) -> dict[str, str]:
    marker = b"com.dji.camera.CameraModel"
    idx = moov.find(marker)
    if idx < 0:
        return {}

    keys_tag = moov.rfind(b"keys", 0, idx)
    if keys_tag < 4:
        return {}

    keys_size = struct.unpack_from(">I", moov, keys_tag - 4)[0]
    keys_box = moov[keys_tag - 4 : keys_tag - 4 + keys_size]
    body = keys_box[8:]
    if len(body) < 8:
        return {}

    count = struct.unpack_from(">I", body, 4)[0]
    names: list[str] = []
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
    values: dict[int, str] = {}
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


def nclx_colr(moov: bytes) -> str | None:
    """Return the standard HEVC nclx triple if present (always 1,1,1 on Pocket 4P)."""
    idx = moov.find(b"colrnclx")
    if idx < 0:
        return None
    primaries, transfer, matrix = struct.unpack_from(">HHH", moov, idx + 8)
    return f"{primaries}/{transfer}/{matrix}"


def read_clip(path: Path) -> dict[str, str]:
    info = {
        "file": path.name,
        "path": str(path),
        "color_space": "",
        "nclx": "",
    }
    moov = extract_moov(path)
    if not moov:
        info["color_space"] = "(no moov)"
        return info

    keys = parse_dji_keys(moov)
    info["color_space"] = keys.get("com.dji.camera.ColorGammaSxS", "(missing)")
    info["nclx"] = nclx_colr(moov) or ""
    for key in DJI_KEYS:
        short = key.rsplit(".", 1)[-1]
        if short == "ColorGammaSxS":
            continue
        info[short] = keys.get(key, "")
    return info


def iter_clips(target: Path):
    if target.is_file():
        yield target
        return
    for path in sorted(target.rglob("*.MP4")):
        yield path
    for path in sorted(target.rglob("*.mp4")):
        if path.suffix == ".MP4":
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=r"K:\DCIM\DJI_001",
        help="MP4 file or folder (default: K:\\DCIM\\DJI_001)",
    )
    parser.add_argument("--csv", metavar="FILE", help="Write results to CSV")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr)
        return 1

    rows = [read_clip(path) for path in iter_clips(target)]
    if not rows:
        print("No MP4 files found.", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["color_space"]] = counts.get(row["color_space"], 0) + 1

    print(f"{'file':42} {'color':8} {'lens':12} {'WB':5} {'EI':4} nclx")
    print("-" * 86)
    for row in rows:
        print(
            f"{row['file'][:42]:42} {row['color_space'][:8]:8} "
            f"{row.get('LensType', ''):12} {row.get('WhiteBalanceKelvin', '')[:5]:5} "
            f"{row.get('ExposureIndexAsa', '')[:4]:4} {row.get('nclx', '')}"
        )

    print()
    print("Counts:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("nclx 1/1/1 is Rec.709 primaries/transfer/matrix — Resolve only sees this.")

    if args.csv:
        fieldnames = ["file", "path", "color_space", "nclx"] + [
            k.rsplit(".", 1)[-1] for k in DJI_KEYS if k != "com.dji.camera.ColorGammaSxS"
        ]
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
