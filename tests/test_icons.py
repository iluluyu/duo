"""Tests for adaptive-icon resource parsing (fixtures from aapt2 output)."""

from __future__ import annotations

import io

from duo.core.apps import (
        _compose_adaptive,
        parse_adaptive_refs,
        parse_resource_colors,
        parse_resource_files,
        resource_id_for_file,
)

XMLTREE = """N: android=http://schemas.android.com/apk/res/android (line=1)
  E: adaptive-icon (line=1)
      E: background (line=3)
        A: http://schemas.android.com/apk/res/android:drawable(0x01010199)=@0x7f06035c
      E: foreground (line=4)
        A: http://schemas.android.com/apk/res/android:drawable(0x01010199)=@0x7f08034c
"""

RESOURCES = """  type drawable id=08
    resource 0x7f080384 drawable/ic_launcher
      (xhdpi) (file) res/drawable-xhdpi-v4/ic_launcher.png type=PNG
      (xxhdpi) (file) res/drawable-xxhdpi-v4/ic_launcher.png type=PNG
      (xxxhdpi) (file) res/drawable-xxxhdpi-v4/ic_launcher.png type=PNG
    resource 0x7f080385 drawable/ic_learn_tip_btn
      () (file) res/drawable/ic_learn_tip_btn.xml type=XML
  type mipmap id=0f
    resource 0x7f0f0000 mipmap/ic_launcher_app
      (xxxhdpi) (file) res/mipmap-xxxhdpi-v4/ic_launcher_app.png type=PNG
      (anydpi-v26) (file) res/mipmap-anydpi-v26/ic_launcher_app.xml type=XML
    resource 0x7f0f0002 mipmap/ic_launcher_foreground_11
      (xxxhdpi) (file) res/mipmap-xxxhdpi-v4/ic_launcher_foreground_11.png type=PNG
  type color id=06
    resource 0x7f06035c color/ic_launcher_background
      () (color) #FF3D3D8F
"""


def test_parse_resource_files_prefers_raster_and_ranks_density():
        """XML entries are skipped; files map per resource with density ranks."""
        files = parse_resource_files(RESOURCES)
        assert files["0x7f080384"] == [
                (3, "res/drawable-xhdpi-v4/ic_launcher.png"),
                (4, "res/drawable-xxhdpi-v4/ic_launcher.png"),
                (5, "res/drawable-xxxhdpi-v4/ic_launcher.png"),
        ]
        # XML-only resource yields nothing.
        assert "0x7f080385" not in files
        assert files["0x7f0f0000"] == [(5, "res/mipmap-xxxhdpi-v4/ic_launcher_app.png")]


def test_parse_resource_colors():
        """Color resources resolve to their hex value."""
        assert parse_resource_colors(RESOURCES)["0x7f06035c"] == "#FF3D3D8F"


def test_resource_id_for_file():
        """The adaptive xml ref maps back to its owning resource id."""
        ref = "res/mipmap-anydpi-v26/ic_launcher_app.xml"
        assert resource_id_for_file(RESOURCES, ref) == "0x7f0f0000"
        assert resource_id_for_file(RESOURCES, "res/not/there.png") is None


def test_parse_adaptive_refs():
        """Foreground/background layer ids come out of the xmltree dump."""
        assert parse_adaptive_refs(XMLTREE) == {
                "background": "0x7f06035c",
                "foreground": "0x7f08034c",
        }


def test_compose_adaptive_crops_visible_center():
        """Compositing renders a square PNG with the 72/108 visible crop."""
        from PIL import Image

        fg_image = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
        for x in range(116, 316):
                for y in range(116, 316):
                        fg_image.putpixel((x, y), (255, 0, 0, 255))
        fg = io.BytesIO()
        fg_image.save(fg, format="PNG")
        composed = _compose_adaptive(fg.getvalue(), None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # 512 * 72 / 108 = 341 (integer division)
                assert image.size == (341, 341)
                # Transparent foreground margin lets the background show at
                # the corners; the opaque centre stays red.
                assert image.getpixel((2, 2))[:3] == (61, 61, 143)  # #3D3D8F
                assert image.getpixel((170, 170))[:3] == (255, 0, 0)
