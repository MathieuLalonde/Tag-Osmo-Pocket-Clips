# Osmo Pocket 4P Metadata Extractor and Tagger for DaVinci Resolve

Tools to read the real color profile from Osmo Pocket 4P MP4s and tag clips in DaVinci Resolve.

The Osmo Pocket 4P introduced a new color profile: D-Log2. Unfortunately, this profile is only supported by one of it's two camera sensors, the second being limited to D-log. Add to that the various fast and slow modes in Rec.709 and you quickly end up having to deal with multiple color profiles in a project shot on a single device. Unfortunately, the metadata stored within the footage files is cannot currently be read by Davinci Resolve, making it harder to sort and grade clips correctly.

This script aims to solve this problem by reading all the metadata stored within the footage files and writing it to the Resolve metadata.

DJI stores `com.dji.camera.ColorGammaSxS` (`Rec.709`, `D-Log`, `D-Log2`) in the QuickTime keys atom, but every clip’s HEVC `colr` box stays BT.709 (`nclx 1/1/1`). Resolve only sees that standard tag, so the three profiles look identical on import. This project reads the DJI key and writes Resolve metadata (and optional clip attributes) so you can sort and grade correctly.

![Tag Osmo Pocket Clips options dialog](images/panel.webp)

## Files

| Path | Role |
| --- | --- |
| `Tag Osmo Pocket Clips.py` | Resolve Utility script (copy into Resolve) |
| `tools/read_osmo_metadata.py` | CLI scanner for a folder or single MP4 |
| `images/panel.webp` | Screenshot of the options dialog |

## Install the Resolve script

1. Copy `Tag Osmo Pocket Clips.py` into Resolve’s **Utility** scripts folder (create the folders if they don’t exist):

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\` |
| macOS | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/` |
| Linux | `~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/` |

2. Restart Resolve once if the menu item is missing.
3. Run **Workspace → Scripts → Tag Osmo Color**.

If **Scripts** is greyed out: Preferences → System → General → **External scripting using** → **Local**.

Options are remembered between runs:

| OS | Settings file |
| --- | --- |
| Windows | `%APPDATA%\Osmo_4p_Metadata\tag_osmo_color.json` |
| macOS | `~/Library/Application Support/Osmo_4p_Metadata/tag_osmo_color.json` |
| Linux | `~/.config/Osmo_4p_Metadata/tag_osmo_color.json` |

## Using the script

1. Import the MP4s and select clips, or open the bin you want to tag.
2. Run **Tag Osmo Color** and choose scope / actions.
3. Click **Run**.

### Scope

| Choice | Behavior |
| --- | --- |
| Selection if any, else current bin | Uses the Media Pool selection when one exists; otherwise the current bin |
| Selected clips only | Selection only (errors if nothing is selected) |
| Current bin | All clips in the current bin |

### Actions

| Option | Notes |
| --- | --- |
| Write metadata columns | Gamma Notes, Color Space Notes, lens, WB, ISO/EI, camera fields |
| Write Keywords | `Osmo Rec.709` / `Osmo D-Log` / `Osmo D-Log2` for smart bins |
| Set Input Color Space | Rec.709 and DJI D-Gamut/D-Log when Color Managed; D-Log2 has no native IDT |
| Apply D-Log2 Input LUT (if present) | DJI Pocket 4P D-Log2 → Rec.709 cube; **Use Vivid LUT** when both variants exist |
| Don’t replace existing values | Skips fields / colors / LUTs that already have a value |
| Set clip color labels | Defaults: Rec.709=Green, D-Log=Blue, D-Log2=Navy (pickers available) |
| Run silently | No progress window and no results dialog |

### Smart bins

With Keywords enabled, create Media Pool smart bins filtered on:

- Keywords contains `Osmo Rec.709`
- Keywords contains `Osmo D-Log`
- Keywords contains `Osmo D-Log2`

### D-Log2 LUTs

Since D-Log2 is not supported by the native color space of DaVinci Resolve, a custom LUT is needed to convert the footage to Rec.709. This script will apply the appropriate LUT to the footage (if it is present in Resolve's LUT folder).

Install DJI’s official cubes under Resolve’s LUT folder:

| OS | Path |
| --- | --- |
| Windows | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\LUT\DJI\` |
| macOS | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/DJI/` |
| Linux | `/opt/resolve/LUT/DJI/` |

Expected names (size33 or size65; size65 preferred):

- `DJI OSMO Pocket 4P D-Log2 to Rec.709 … .cube`
- `DJI OSMO Pocket 4P D-Log2 to Rec.709 vivid … .cube`

If neither is found, the Input LUT checkbox is disabled. If only one exists, **Use Vivid LUT** is forced on or off to match it.

This LUT is a Rec.709 conversion, not a wide-gamut IDT — useful for quick Rec.709 monitoring until Resolve ships a D-Log2 color space.

## Input Color Space needs Color Managed

**Set Input Color Space** only works when the project is color-managed.

1. **File → Project Settings → Color Management**
2. **Color science** = **DaVinci YRGB Color Managed** (not plain DaVinci YRGB)
3. Prefer **Automatic Color Management** off if you want per-clip control
4. Right-click a clip in the Media Pool — **Input Color Space** should appear

On plain **DaVinci YRGB**, that menu is hidden and the checkbox is disabled. Metadata, keywords, clip colors, and Input LUTs still work.

## Wide-gamut grading (RCM)

If you grade in DaVinci Wide Gamut, do **not** manually convert Rec.709 → DWG when RCM is handling the clips. Set the project once, tag Inputs correctly, and grade in the timeline space.

Suggested project settings:

| Setting | Value |
| --- | --- |
| Color science | DaVinci YRGB Color Managed |
| Timeline color space | DaVinci Wide Gamut / DaVinci Intermediate |
| Output color space | Rec.709 Gamma 2.4 (or your delivery) |
| Clip Input (Rec.709) | Rec.709 (or Rec.709 Gamma 2.4) |
| Clip Input (D-Log) | DJI D-Gamut/D-Log |
| Clip Input (D-Log2) | No native IDT — use DJI Input LUT or a Log→DWG LUT / group for now |

What that does:

- Resolve converts **camera Input → DWG** automatically for each clip
- You grade in wide gamut
- Resolve converts **DWG → Output** on monitoring / deliver

Wrong path: the project treats D-Log as Rec.709 (default or wrong Input), so you CST Rec.709 → DWG on log footage. Fix the **clip Input** (and project color science), not an extra Rec.709→DWG step.

Node CST workflow (no RCM): keep Color science = DaVinci YRGB, and on D-Log clips use CST **Input = DJI D-Gamut / DJI D-Log → Output = DaVinci Wide Gamut / Intermediate**. Input must match the camera profile, not Rec.709.

## CLI usage

Scan a camera card folder or a single file without Resolve:

```bash
python tools/read_osmo_metadata.py K:\DCIM\DJI_001
python tools/read_osmo_metadata.py path\to\clip.MP4
python tools/read_osmo_metadata.py path\to\folder --csv colorspaces.csv
```

Prints ColorGammaSxS (and related DJI keys) for each MP4.

## License

Licensed under the MIT License — see [LICENSE](LICENSE).
