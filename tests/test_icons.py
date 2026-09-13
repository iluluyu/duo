"""Tests for adaptive-icon resource parsing (fixtures from aapt2 output)."""

from __future__ import annotations

import io

from duo.core.apps import (
        _NEUTRAL_PLATE,
        _alpha_bbox,
        _compose_adaptive,
        apply_rounded_mask,
        normalize_raster,
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
                assert image.getpixel((4, 230))[:3] == (61, 61, 143)
                assert image.getpixel((4, 230))[3] >= 250
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
        """Default shape is the full Apple-style superellipse (50%).

        r = half the side: no straight edges remain, the whole outline is
        one |x/2a|^5+|y/2b|^5=1 curve - on a 40px image the top edge cut
        spans ~5px and the 45° diagonal turns solid at (3,3).
        """
        from PIL import Image

        masked = apply_rounded_mask(Image.new("RGBA", (40, 40), (0, 0, 255, 255)))
        assert masked.getpixel((0, 0))[3] == 0
        assert masked.getpixel((4, 0))[3] == 0
        assert masked.getpixel((5, 0))[3] > 0
        assert masked.getpixel((10, 0))[3] >= 250
        assert masked.getpixel((2, 2))[3] > 50
        assert masked.getpixel((3, 3))[3] >= 250
        # A shallower corner (38%) clips less than the default.
        shallower = apply_rounded_mask(Image.new("RGBA", (40, 40), (0, 0, 255, 255)), 0.38)
        assert shallower.getpixel((4, 0))[3] > 0
        assert shallower.getpixel((9, 0))[3] >= 250


# ------------------------------------------------- generative raster pass


XMLTREE_FG = """N: android=http://schemas.android.com/apk/res/android (line=1)
  E: adaptive-icon (line=1)
      E: background (line=3)
        A: http://schemas.android.com/apk/res/android:drawable(0x01010199)=@0x7f06035c
      E: foreground (line=4)
        A: http://schemas.android.com/apk/res/android:drawable(0x01010199)=@0x7f0f0002
"""


def _circle_blob(size: int = 128, fill: tuple = (61, 195, 75, 255)) -> object:
        """Transparent canvas with one centred filled circle + white core."""
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = 4
        draw.ellipse((margin, margin, size - margin, size - margin), fill=fill)
        core = size // 3
        draw.ellipse(
                ((size - core) // 2, (size - core) // 2,
                 (size + core) // 2, (size + core) // 2),
                fill=(255, 255, 255, 255),
        )
        return image


def test_normalize_floating_blob_lands_on_same_color_plate():
        """A circular logo on transparency becomes a squircle plate tile.

        The Coolapk shape: an inscribed green circle reads as a circle in
        the grid. Normalization pastes it at 68% on a dominant-colour
        plate, so the corners go opaque plate green and the white core
        stays centred.
        """

        image = _circle_blob()
        out = normalize_raster(image)  # type: ignore[arg-type]
        assert out.size == image.size
        for corner in [(0, 0), (0, 127), (127, 0), (127, 127)]:
                r, g, b, a = out.getpixel(corner)
                assert a == 255
                assert abs(r - 61) <= 8 and abs(g - 195) <= 8 and abs(b - 75) <= 8
        assert out.getpixel((64, 64))[:3] == (255, 255, 255)


def test_normalize_white_background_stays_brand_white():
        """White-background rasters recolour their bg, content untouched.

        Interior white enclosed by content ink must survive (flood starts
        at the border only) - a white ring inside a logo keeps its white.
        """
        from PIL import Image

        size = 128
        image = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        image.paste(Image.new("RGBA", (64, 64), (227, 68, 44, 255)), (32, 32))
        image.paste(Image.new("RGBA", (16, 16), (255, 255, 255, 255)), (56, 56))
        out = normalize_raster(image)  # type: ignore[arg-type]
        plate = out.getpixel((4, 4))
        assert plate[3] == 255
        assert plate[:3] == (255, 255, 255)   # white stays brand-white
        assert out.getpixel((40, 40))[:3] == (227, 68, 44)     # content kept


def test_normalize_passes_through_unhandled_shapes():
        """Full-bleed art, coloured plates, line art and tiny rasters pass."""
        from PIL import Image, ImageDraw

        full = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
        assert normalize_raster(full) is full  # type: ignore[arg-type]
        colored = Image.new("RGBA", (128, 128), (30, 100, 220, 255))
        colored.paste(Image.new("RGBA", (48, 48), (255, 255, 255, 255)), (40, 40))
        assert normalize_raster(colored) is colored  # type: ignore[arg-type]
        art = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(art)
        draw.rectangle((0, 62, 127, 65), fill=(220, 40, 40, 255))
        draw.rectangle((62, 0, 65, 127), fill=(220, 40, 40, 255))
        # Sparse line art on transparency now takes the flatten+tint path:
        # red cross keeps its strokes and gains a pale red plate.
        plated = normalize_raster(art)
        assert plated.getpixel((2, 2))[3] == 255
        assert plated.getpixel((2, 2))[:3] != (0, 0, 0)
        assert plated.getpixel((64, 64))[:3] == (220, 40, 40)
        tiny = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
        assert normalize_raster(tiny) is tiny  # type: ignore[arg-type]


def test_sparse_line_art_keeps_white_not_content_plate():
        """Sparse coloured line art (EasyTier mesh) must plate on WHITE.

        The mean colour of sparse art IS the line colour - plating on it
        swallows the art into a solid tile. Only mostly-opaque sheets
        (>=70% coverage, Weather) may lend their mean to the corners.
        """
        from PIL import Image, ImageDraw

        art = Image.new("RGBA", (288, 288), (0, 0, 0, 0))
        draw = ImageDraw.Draw(art)
        for i in range(6):
                x = 30 + i * 40
                draw.line((x, 60, 144, 230), fill=(101, 152, 251, 255), width=14)
                draw.line((288 - x, 60, 144, 230), fill=(101, 152, 251, 255), width=14)
        out = normalize_raster(art)
        top = out.getpixel((144, 2))
        assert min(top[:3]) > 245 and top[3] == 255   # white plate, not blue
        centre = out.getpixel((144, 144))
        assert centre[2] > 200 and centre[0] < 200     # blue mesh survives


def test_normalize_neutral_blob_gets_silver_plate():
        """A white blob on transparency flattens to the cool silver plate."""
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 119, 119), fill=(255, 255, 255, 255))
        out = normalize_raster(image)
        # Flood swallows everything: no ink survives, whole canvas
        # becomes the neutral plate (a white sheet must not read as a
        # hole on the panel).
        assert out.getpixel((2, 2))[:3] == _NEUTRAL_PLATE
        assert out.getpixel((64, 64))[:3] == _NEUTRAL_PLATE


def _icon_apk(tmp_path, entries: dict) -> object:
        import zipfile

        apk = tmp_path / "app.apk"
        with zipfile.ZipFile(apk, "w") as zf:
                for name, blob in entries.items():
                        zf.writestr(name, blob)
        return apk


def test_extract_icon_adaptive_layers_beat_legacy_raster(tmp_path, monkeypatch):
        """Adaptive refs composite their fg/bg; the legacy raster waits behind."""
        import io

        from PIL import Image

        import duo.core.apps as apps

        fg = _foreground_layer(200, (116, 116))
        legacy = Image.new("RGBA", (192, 192), (61, 195, 75, 255))
        legacy_bytes = io.BytesIO()
        legacy.save(legacy_bytes, format="PNG")
        apk = _icon_apk(tmp_path, {
                "res/mipmap-xxxhdpi-v4/ic_launcher_app.png": legacy_bytes.getvalue(),
                "res/mipmap-xxxhdpi-v4/ic_launcher_foreground_11.png": fg,
        })

        def fake_aapt2(aapt2: object, argv: list) -> str | None:
                if argv[1] == "resources":
                        return RESOURCES
                if argv[1] == "xmltree":
                        return XMLTREE_FG
                return None

        monkeypatch.setattr(apps, "_aapt2_output", fake_aapt2)
        out = tmp_path / "out.png"
        ref = "res/mipmap-anydpi-v26/ic_launcher_app.xml"
        assert apps.extract_icon(apk, ref, out, tmp_path / "aapt2.exe") == out
        with Image.open(out) as image:
                assert image.size == (288, 288)   # adaptive crop, not the 192 legacy
                assert image.getpixel((144, 144))[:3] == (255, 0, 0)   # fg content


def test_extract_icon_vector_foreground_falls_back_to_legacy(tmp_path, monkeypatch):
        """Unresolvable fg (vector) + present legacy raster -> legacy wins."""
        import io

        from PIL import Image

        import duo.core.apps as apps

        legacy = Image.new("RGBA", (192, 192), (255, 255, 255, 255))
        legacy.paste(Image.new("RGBA", (90, 90), (227, 68, 44, 255)), (51, 51))
        legacy_bytes = io.BytesIO()
        legacy.save(legacy_bytes, format="PNG")
        apk = _icon_apk(
                tmp_path, {"res/mipmap-xxxhdpi-v4/ic_launcher_app.png": legacy_bytes.getvalue()}
        )

        def fake_aapt2(aapt2: object, argv: list) -> str | None:
                if argv[1] == "resources":
                        return RESOURCES
                if argv[1] == "xmltree":
                        return XMLTREE   # foreground 0x7f08034c: no raster file
                return None

        monkeypatch.setattr(apps, "_aapt2_output", fake_aapt2)
        out = tmp_path / "out.png"
        ref = "res/mipmap-anydpi-v26/ic_launcher_app.xml"
        assert apps.extract_icon(apk, ref, out, tmp_path / "aapt2.exe") == out
        with Image.open(out) as image:
                # 192px legacy, white bg stays brand white.
                assert image.size == (192, 192)
                assert image.getpixel((2, 2))[:3] == (255, 255, 255)


def test_extract_icon_raster_runs_normalize(tmp_path):
        """The plain raster path normalizes before the rounded mask."""
        import io

        from PIL import Image

        from duo.core.apps import extract_icon

        source = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
        source.paste(Image.new("RGBA", (64, 64), (30, 100, 220, 255)), (32, 32))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        apk = _icon_apk(
                tmp_path, {"res/mipmap-xxxhdpi/ic_launcher.png": buffer.getvalue()}
        )
        out = tmp_path / "out.png"
        assert extract_icon(apk, "res/mipmap-xxxhdpi/ic_launcher.png", out) == out
        with Image.open(out) as image:
                # Edge midpoint stays brand white (mask only shapes corners).
                r, g, b, a = image.getpixel((0, 64))
                assert a == 255
                assert (r, g, b) == (255, 255, 255)


def test_apply_rounded_mask_preserves_transparency():
        """The mask multiplies alpha; interior transparency survives.

    putalpha alone flattened transparent sources onto an opaque black
    canvas - the Coolapk black-tile bug.
    """
        from PIL import Image

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        image.paste(Image.new("RGBA", (40, 40), (10, 200, 90, 255)), (12, 12))
        masked = apply_rounded_mask(image)
        # Transparent spot inside stays transparent (not opaque black).
        assert masked.getpixel((2, 2))[3] == 0
        assert masked.getpixel((32, 62))[3] == 0
        # Opaque content stays opaque away from the rounded corners.
        assert masked.getpixel((32, 32)) == (10, 200, 90, 255)


def test_compose_adaptive_keeps_white_background():
        """Adaptive composites with a white bg layer get the pastel plate."""
        from PIL import Image

        fg = _foreground_layer(200, (116, 116))
        composed = _compose_adaptive(fg, None, "#FFFFFF")
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                # White adaptive bg layer stays brand white.
                assert image.getpixel((144, 4))[3] == 255
                assert image.getpixel((144, 4))[:3] == (255, 255, 255)
                assert image.getpixel((144, 144))[:3] == (255, 0, 0)


VECTOR_TREE = """N: android=http://schemas.android.com/apk/res/android (line=6)
E: vector (line=6)
 A: http://schemas.android.com/apk/res/android:height(0x01010155)=108.0dp
 A: http://schemas.android.com/apk/res/android:width(0x01010159)=108.0dp
 A: http://schemas.android.com/apk/res/android:viewportWidth(0x01010402)=108
 A: http://schemas.android.com/apk/res/android:viewportHeight(0x01010403)=108
  E: group (line=10)
   A: http://schemas.android.com/apk/res/android:scaleX(0x01010324)=0.66
   A: http://schemas.android.com/apk/res/android:scaleY(0x01010325)=0.66
   A: http://schemas.android.com/apk/res/android:translateX(0x0101045a)=18.36
   A: http://schemas.android.com/apk/res/android:translateY(0x0101045b)=18.36
    E: clip-path (line=13)
     A: http://schemas.android.com/apk/res/android:pathData(0x01010405)="M0,0h1v1z"
    E: path (line=19)
     A: http://schemas.android.com/apk/res/android:fillColor(0x01010404)=@0x7f08000c
     A: http://schemas.android.com/apk/res/android:pathData(0x01010405)="M28,67 L44,58 Z"
     A: http://schemas.android.com/apk/res/android:fillAlpha(0x010104cc)=0.95
    E: path (line=33)
     A: http://schemas.android.com/apk/res/android:fillColor(0x01010404)=-1
     A: http://schemas.android.com/apk/res/android:pathData(0x01010405)="M10,10 h20 v20 z"
"""


def test_resource_xml_file_finds_layer_xml():
        """The vector layer's XML file path resolves from the dump."""
        from duo.core.apps import resource_xml_file

        assert resource_xml_file(RESOURCES, "0x7f080384") is None
        assert resource_xml_file(RESOURCES, "0x7f080385") == "res/drawable/ic_learn_tip_btn.xml"


def test_parse_vector_tree_groups_and_paths():
        """Viewport, group transforms, fill refs and alpha all come out."""
        from duo.core.apps import parse_vector_tree

        tree = parse_vector_tree(VECTOR_TREE)
        assert tree is not None
        assert tree["viewport"] == (108.0, 108.0)
        group = tree["body"]["children"][0]
        assert group["transform"]["scaleX"] == "0.66"
        assert group["transform"]["translateX"] == "18.36"
        paths = [c for c in group["children"] if "d" in c]
        assert len(paths) == 2   # the clip-path is skipped
        assert paths[0]["fill"] == "@0x7f08000c"
        assert paths[0]["opacity"] == 0.95
        assert paths[1]["fill"] == "-1"
        assert paths[1]["d"].startswith("M10,10")


def test_parse_vector_tree_rejects_non_vector():
        """Adaptive-icon and bitmap xmltrees yield None."""
        from duo.core.apps import parse_vector_tree

        assert parse_vector_tree(XMLTREE) is None
        assert parse_vector_tree("") is None


def test_vector_svg_resolves_colors_and_transforms():
        """Resource-ref fills resolve; packed ints become #AARRGGBB."""
        from duo.core.apps import parse_vector_tree, vector_svg

        tree = parse_vector_tree(VECTOR_TREE)
        assert tree is not None
        svg = vector_svg(tree, {"0x7f08000c": "#FFD97757"})
        assert svg is not None
        assert 'viewBox="0 0 108 108"' in svg
        assert 'transform="translate(18.36 18.36) scale(0.66 0.66)"' in svg
        assert 'fill="#D97757"' in svg
        assert 'fill-opacity="0.95"' in svg
        assert 'fill="#FFFFFF"' in svg   # -1 = opaque white


def test_compose_adaptive_accepts_rendered_image_layer():
        """Vector-rendered PIL layers take the raster-bytes slot unchanged."""
        from PIL import Image

        from duo.core.apps import _compose_adaptive

        fg = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
        fg.paste(Image.new("RGBA", (200, 200), (255, 0, 0, 255)), (116, 116))
        bg = Image.new("RGBA", (432, 432), (61, 195, 75, 255))
        composed = _compose_adaptive(fg, bg, None)
        assert composed is not None
        with Image.open(io.BytesIO(composed)) as image:
                assert image.size == (288, 288)
                assert image.getpixel((144, 144))[:3] == (255, 0, 0)
