"""Tests for adaptive-icon resource parsing (fixtures from aapt2 output)."""

from __future__ import annotations

import io

from duo.core.apps import (
        _alpha_bbox,
        _compose_adaptive,
        apply_rounded_mask,
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


def _foreground_layer(
        square: int, offset: tuple[int, int], layer: int = 432
) -> bytes:
        """PNG of a transparent 432px foreground layer with one opaque square."""
        from PIL import Image

        image = Image.new("RGBA", (layer, layer), (0, 0, 0, 0))
        image.paste(Image.new("RGBA", (square, square), (255, 0, 0, 255)), offset)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def test_compose_adaptive_crops_visible_center():
        """Compositing renders a rounded PNG with the 72/108 visible crop."""
        from PIL import Image

        fg = _foreground_layer(200, (116, 116))
        composed = _compose_adaptive(fg, None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # 432 * 72 / 108 = 288: an exact 108-unit multiple (P2-5) -
                # the retired 512-canvas crop was a fractional 341.33.
                assert image.size == (288, 288)
                # The white-canvas square is masked: all four corners go
                # transparent so the grid never shows a hard-edged square.
                for corner in [(0, 0), (0, 287), (287, 0), (287, 287)]:
                        assert image.getpixel(corner)[3] == 0
                # The opaque centre stays red and untouched by the mask.
                assert image.getpixel((144, 144))[:3] == (255, 0, 0)
                assert image.getpixel((144, 144))[3] == 255


def test_compose_adaptive_recentres_lopsided_content():
        """Foreground content with lopsided padding lands optically centred.

        The 60px square sits in the bottom-left corner of its layer; the
        old full-canvas stretch left it half outside the visible crop.
        Now the content bbox is cropped and centred, so the crop middle is
        red and the layer's original corner is plain background.
        """
        from PIL import Image

        fg = _foreground_layer(60, (20, 300))
        composed = _compose_adaptive(fg, None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # Centred content: the crop centre and symmetric points
                # either side of it are red (content spans crop 114..174).
                for point in [(144, 144), (120, 144), (168, 144)]:
                        assert image.getpixel(point)[:3] == (255, 0, 0)
                        assert image.getpixel(point)[3] == 255
                # Where the old stretch placed the square (crop of canvas
                # 20..80 x 300..360) only the background colour remains.
                assert image.getpixel((4, 230)) == (61, 61, 143, 255)
                assert image.getpixel((60, 144)) == (61, 61, 143, 255)


def test_compose_adaptive_shrinks_content_beyond_safe_zone():
        """Content larger than the 66-unit safe zone shrinks, aspect kept.

        A 380px square (bigger than the 264px safe diameter but not quite
        full-bleed) is scaled down to exactly 264px and centred: it fills
        crop 12..276 with background showing in the remaining rim.
        """
        from PIL import Image

        fg = _foreground_layer(380, (26, 26))
        composed = _compose_adaptive(fg, None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # Inside the shrunk content (crop 12..276).
                assert image.getpixel((144, 144))[:3] == (255, 0, 0)
                assert image.getpixel((20, 144))[:3] == (255, 0, 0)
                # The rim between safe zone and visible edge stays background.
                assert image.getpixel((6, 144)) == (61, 61, 143, 255)
                assert image.getpixel((144, 6)) == (61, 61, 143, 255)


def test_compose_adaptive_full_bleed_foreground_unchanged():
        """Full-canvas foregrounds keep the legacy full-bleed stretch.

        Such layers are artwork designed to be cropped by the mask; the
        whole visible square stays foreground red instead of being shrunk
        into the safe zone.
        """
        from PIL import Image

        fg_image = Image.new("RGBA", (432, 432), (255, 0, 0, 255))
        fg = io.BytesIO()
        fg_image.save(fg, format="PNG")
        composed = _compose_adaptive(fg.getvalue(), None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                for point in [(0, 144), (144, 0), (144, 144), (287, 144)]:
                        assert image.getpixel(point)[:3] == (255, 0, 0)
                        assert image.getpixel(point)[3] == 255


def test_compose_adaptive_skips_transparent_foreground():
        """A fully transparent foreground layer is skipped, not an error."""
        from PIL import Image

        fg_image = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
        fg = io.BytesIO()
        fg_image.save(fg, format="PNG")
        composed = _compose_adaptive(fg.getvalue(), None, "#3D3D8F")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # Only the background colour remains, still masked rounded.
                assert image.getpixel((144, 144)) == (61, 61, 143, 255)
                assert image.getpixel((0, 0))[3] == 0


def test_alpha_bbox_keys_on_alpha_channel():
        """The bbox measures visible ink, ignoring near-invisible alpha noise.

        A layer-wide alpha-8 wash (lossy export fringe) would make a raw
        ``getbbox`` report the full canvas; the threshold keys on the
        visible block alone.
        """
        from PIL import Image

        layer = Image.new("RGBA", (100, 100), (255, 255, 255, 8))
        layer.paste(Image.new("RGBA", (30, 30), (255, 0, 0, 255)), (40, 50))
        assert layer.getbbox() == (0, 0, 100, 100)
        assert _alpha_bbox(layer) == (40, 50, 70, 80)


def test_alpha_bbox_threshold_and_empty():
        """Alpha at/below the threshold counts as empty; nothing -> None."""
        from PIL import Image

        assert _alpha_bbox(Image.new("RGBA", (64, 64), (255, 255, 255, 8))) is None
        faint = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        faint.paste(Image.new("RGBA", (10, 10), (0, 0, 0, 9)), (20, 20))
        assert _alpha_bbox(faint) == (20, 20, 30, 30)


def test_apply_rounded_mask_shapes_corners_only():
        """Corners go transparent; centre and straight edges stay opaque."""
        from PIL import Image

        image = Image.new("RGB", (40, 40), (255, 0, 0))
        masked = apply_rounded_mask(image)
        # Non-RGBA input is converted instead of crashing on putalpha.
        assert masked.mode == "RGBA"
        # Same size - the mask rounds, it never resizes.
        assert masked.size == (40, 40)
        for corner in [(0, 0), (0, 39), (39, 0), (39, 39)]:
                assert masked.getpixel(corner)[3] == 0
        assert masked.getpixel((20, 20))[3] == 255
        # Straight edge midpoints keep their pixels: radius 23% < half side.
        for midpoint in [(0, 20), (20, 0), (39, 20), (20, 39)]:
                assert masked.getpixel(midpoint)[3] == 255


def test_apply_rounded_mask_radius_ratio():
        """Default ratio is 23%: a 40px image gets a radius of ~9px."""
        from PIL import Image

        masked = apply_rounded_mask(Image.new("RGBA", (40, 40), (0, 0, 255, 255)))
        # Along the top edge the cut ends where the straight edge resumes:
        # pixel 4 is fully clipped, 5..8 form the antialiased ramp, and 9
        # (= round(40 * 0.23)) is solid again.
        assert masked.getpixel((4, 0))[3] == 0
        assert 0 < masked.getpixel((5, 0))[3] < 255
        assert masked.getpixel((9, 0))[3] == 255
        # The same spot diagonally: the arc crosses between (2,2) and (3,3).
        assert 0 < masked.getpixel((2, 2))[3] < 255
        assert masked.getpixel((3, 3))[3] == 255
        # A deeper radius clips pixels the default leaves solid.
        rounder = apply_rounded_mask(Image.new("RGBA", (40, 40), (0, 0, 255, 255)), 0.35)
        assert rounder.getpixel((8, 0))[3] == 0
