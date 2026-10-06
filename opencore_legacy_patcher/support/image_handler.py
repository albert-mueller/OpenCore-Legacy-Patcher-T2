"""
image_handler.py: Loads the GUI's PNG icons, fully offline

Built app:   the PNGs are packed into Contents/Resources/OpenCore-Patcher-T2.assets
             by ci_tooling/build_modules/application.py (JSON: name -> base64 PNG).
From source: the PNGs are read straight from payloads/Resources/AppIcons.

Callers keep passing the same Paths the Constants class hands out
(eg. constants.patch_icon_path). If that file exists on disk it is used as-is
(source runs, .icns files, system icons); otherwise the file name is looked up
in the assets file. No network access is ever involved.
"""

import sys
import json
import base64
import logging
import functools

from pathlib import Path


ASSETS_FILE_NAME = "OpenCore-Patcher-T2.assets"
ASSETS_FORMAT = 1

__all__ = [
    "ASSETS_FILE_NAME",
    "ASSETS_FORMAT",
    "resolve_icon",
    "exists",
    "get_bytes",
    "get_data_uri",
    "get_bitmap",
    "load_icon",
]


def _assets_file() -> Path:
    """
    Contents/Resources/OpenCore-Patcher-T2.assets of the running app bundle
    (sys.executable is Contents/MacOS/OpenCore-Patcher-T2 when frozen).
    """
    return Path(sys.executable).resolve().parent.parent / "Resources" / ASSETS_FILE_NAME


def _source_icons_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "payloads" / "Resources" / "AppIcons"


@functools.lru_cache(maxsize=1)
def _load_assets() -> dict:
    """
    Read and decode the assets file once. Returns {} when not frozen or missing.
    """
    if not getattr(sys, "frozen", False):
        return {}

    path = _assets_file()
    if not path.exists():
        logging.error(f"Assets file not found: {path}")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logging.error(f"Failed to read assets file {path}: {e}")
        return {}

    # Accept both the versioned layout ({"format": 1, "images": {...}}) and the
    # flat layout the first draft of this PR wrote ({"name.png": {...}}).
    images = raw.get("images", raw) if isinstance(raw, dict) else {}

    decoded = {}
    for name, entry in images.items():
        if not isinstance(entry, dict) or "data" not in entry:
            continue
        try:
            decoded[name] = base64.b64decode(entry["data"])
        except Exception as e:
            logging.error(f"Corrupt asset '{name}' in {path}: {e}")
    return decoded


def resolve_icon(icon) -> "Path | None":
    """
    Resolve a caller-facing icon reference to a file-like object.

    Returns the original path when it exists on disk, otherwise a synthetic Path
    pointing at the matching asset name in the bundle, or None when neither match
    exists. This keeps call sites simple and lets the rest of this module handle
    the actual on-disk-vs-assets distinction.
    """
    icon = Path(str(icon))

    if icon.is_absolute() and icon.exists():
        return icon

    if icon.name in _load_assets():
        return icon

    source_file = _source_icons_dir() / icon.name
    if source_file.exists():
        return source_file

    return None


def get_bytes(icon) -> bytes:
    """
    Return the raw bytes of an icon.

    icon: a file name ("OC-Build.png") or a full Path/str from Constants.
    Raises FileNotFoundError if it is neither on disk nor in the assets file.
    """
    icon = Path(str(icon))

    if icon.is_absolute() and icon.exists():
        return icon.read_bytes()

    assets = _load_assets()
    if icon.name in assets:
        return assets[icon.name]

    source_file = _source_icons_dir() / icon.name
    if source_file.exists():
        return source_file.read_bytes()

    raise FileNotFoundError(f"Icon not found on disk or in {ASSETS_FILE_NAME}: {icon.name}")


def get_data_uri(icon) -> str:
    """
    data: URI for an icon - used by the About frame's WebView so the README
    image renders without internet and without a file on disk.
    """
    mime = "image/png" if Path(str(icon)).suffix.lower() == ".png" else "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(get_bytes(icon)).decode('ascii')}"


def exists(icon) -> bool:
    """
    True if the icon is on disk or packed into the assets file.
    Use this instead of Path.exists() for PNGs: in the built app they are no
    longer real files in Contents/Resources.
    """
    return resolve_icon(icon) is not None


def load_icon(icon, *, kind: str = "bytes", size: tuple = None):
    """
    High-level icon loader.

    This is the public API callers should use instead of assembling the on-disk,
    source-file, and bundled-assets checks themselves.

    kind:
        - "bytes"      -> raw file bytes
        - "data_uri"   -> base64 data: URI for a web view
        - "bitmap"     -> wx.Bitmap, optionally rescaled to size=(w, h)
        - "exists"     -> bool
    """
    if kind == "exists":
        return exists(icon)

    if kind == "bytes":
        return get_bytes(icon)

    if kind == "data_uri":
        return get_data_uri(icon)

    if kind == "bitmap":
        return get_bitmap(icon, size=size)

    raise ValueError(f"Unsupported icon kind: {kind!r}. Expected one of: bytes, data_uri, bitmap, exists")


def _bitmap_type_for(path: Path):
    """
    wx bitmap type for a file on disk.

    .icns must be loaded as wx.BITMAP_TYPE_ICON: on macOS that goes through
    NSImage. wx.BITMAP_TYPE_ANY (type 50) only tries the wxImage handlers,
    none of which understand ICNS, which produced the
    "Unknown image data format / no bitmap handler for type 50" error dialog.
    """
    import wx

    suffix = path.suffix.lower()
    if suffix == ".icns":
        return wx.BITMAP_TYPE_ICON
    if suffix == ".png":
        return wx.BITMAP_TYPE_PNG
    return wx.BITMAP_TYPE_ANY


def get_bitmap(icon, size: tuple = None):
    """
    wx.Bitmap for an icon, optionally rescaled to size=(w, h).

    Existing files (.icns, system icons) are loaded from disk with the bitmap
    type matching their extension. Icons that are not on disk are decoded
    from the assets file. Never shows a wx error dialog: a broken icon is
    logged and replaced by an empty bitmap.
    """
    import io
    import wx  # imported lazily so the build tooling / CLI never need wx

    path = Path(str(icon))
    image = None

    # wx.LogNull suppresses wx's modal "OpenCore Legacy Patcher T2 Error"
    # dialog; failures are reported through logging instead.
    with wx.LogNull():
        if path.is_absolute() and path.exists():
            bitmap = wx.Bitmap(str(path), _bitmap_type_for(path))
            if bitmap.IsOk():
                if size is None:
                    return bitmap
                image = bitmap.ConvertToImage()
            else:
                logging.error(f"Failed to load icon from disk: {path}")

        if image is None:
            try:
                data = get_bytes(path.name if path.is_absolute() and path.exists() else path)
            except FileNotFoundError as e:
                logging.error(str(e))
                return wx.Bitmap(1, 1)
            image = wx.Image(io.BytesIO(data), wx.BITMAP_TYPE_PNG)

        if not image.IsOk():
            logging.error(f"Failed to decode icon: {path.name}")
            return wx.Bitmap(1, 1)

        if size is not None:
            image = image.Rescale(size[0], size[1], wx.IMAGE_QUALITY_HIGH)
        return wx.Bitmap(image)
