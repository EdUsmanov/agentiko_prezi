"""Small corpus-free checks for the standalone classifier and XML cleaner."""

from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from bgextract.cleanup import remove_subject_svg_overlays
from bgextract.raster_regions import (
    large_white_content_surface,
    reconstruct_regions,
    smooth_light_canvas,
)
from bgextract.roles import A, P, apply_background_roles, background_roles
from bgextract.subject_fragments import (
    preserve_masked_photo_collage,
    remove_screenshot_header_strips,
)
from bgextract.vl_regions import _normalized_regions, review_raster_backgrounds
from PIL import Image

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def shape(sid: int, x: int, y: int, w: int, h: int, text: str = "", name: str = "") -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/>'
        f"</a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
        "</p:txBody></p:sp>"
    )


def picture(sid: int, x: int, y: int, w: int, h: int) -> str:
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="Icon"/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="rId1"/></p:blipFill><p:spPr><a:xfrm>'
        f'<a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/>'
        "</a:xfrm></p:spPr></p:pic>"
    )


class BackgroundExtractionTests(unittest.TestCase):
    def test_subject_svg_badge_is_removed_but_repeated_motif_survives(self) -> None:
        rows = [
            {"type": "pic", "asset": "photo.jpg", "box": [0.5, 0.25, 0.5, 0.75]},
            {"type": "pic", "asset": "unique.svg", "box": [0.75, 0.2, 0.1, 0.16]},
            {
                "type": "sp",
                "asset": "",
                "box": [0.73, 0.17, 0.14, 0.22],
                "preset": "ellipse",
                "text": "",
            },
            {"type": "pic", "asset": "motif.svg", "box": [0.55, 0.6, 0.1, 0.16]},
        ]
        decisions = [
            {"action": "remove", "role": "content", "reason": "replaceable_placeholder"},
            {"action": "keep", "role": "unknown", "reason": "unresolved_media_preserved"},
            {"action": "keep", "role": "decoration", "reason": "unlabelled_vector_preserved"},
            {"action": "keep", "role": "unknown", "reason": "unresolved_media_preserved"},
        ]
        remove_subject_svg_overlays(rows, decisions, {"unique.svg": 1, "motif.svg": 5})
        self.assertEqual(
            [item["action"] for item in decisions], ["remove", "remove", "remove", "keep"]
        )

    def test_contrast_enhanced_vl_catches_pale_empty_card(self) -> None:
        image = Image.new("RGB", (400, 240), (248, 250, 254))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        package = BytesIO()
        with ZipFile(package, "w") as archive:
            archive.writestr("ppt/media/canvas.png", buffer.getvalue())
        model = {
            "parts": {
                "ppt/slides/slide1.xml": [
                    {
                        "action": "keep",
                        "role": "background",
                        "asset": "ppt/media/canvas.png",
                        "box": [0, 0, 1, 1],
                    }
                ]
            }
        }
        region = {"x1": 100, "y1": 100, "x2": 800, "y2": 900, "reason": "pale card"}
        with patch("bgextract.vl_regions._review", side_effect=[[], [region]]) as review:
            review_raster_backgrounds(package.getvalue(), model, "unused", "unused", "test")
        self.assertEqual(review.call_count, 2)
        self.assertTrue(review.call_args.kwargs["enhanced"])
        self.assertEqual(model["rasterRegions"]["ppt/media/canvas.png"], [region])

    def test_vl_reviews_raster_background_fill_outside_shape_tree(self) -> None:
        image = Image.new("RGB", (400, 240), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        package = BytesIO()
        with ZipFile(package, "w") as archive:
            archive.writestr(
                "ppt/presentation.xml",
                f'<p:presentation xmlns:p="{P}" xmlns:r="{R}">'
                '<p:sldIdLst><p:sldId r:id="rId1"/></p:sldIdLst></p:presentation>',
            )
            archive.writestr(
                "ppt/_rels/presentation.xml.rels",
                f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
                f'Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>',
            )
            archive.writestr("ppt/slides/slide1.xml", f'<p:sld xmlns:p="{P}"/>')
            archive.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
                f'Type="{R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                "</Relationships>",
            )
            archive.writestr(
                "ppt/slideLayouts/slideLayout1.xml",
                f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
                '<p:cSld><p:bg><p:bgPr><a:blipFill><a:blip r:embed="rId2"/>'
                "</a:blipFill></p:bgPr></p:bg></p:cSld></p:sldLayout>",
            )
            archive.writestr(
                "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                f'<Relationships xmlns="{PR}"><Relationship Id="rId2" '
                f'Type="{R}/image" Target="../media/canvas.png"/></Relationships>',
            )
            archive.writestr("ppt/media/canvas.png", buffer.getvalue())
        model = {"parts": {}}
        region = {"x1": 300, "y1": 200, "x2": 700, "y2": 800, "reason": "subject"}
        with patch("bgextract.vl_regions._review", side_effect=[[region], []]) as review:
            review_raster_backgrounds(package.getvalue(), model, "unused", "unused", "test")
        self.assertEqual(review.call_count, 2)
        self.assertEqual(model["rasterRegions"]["ppt/media/canvas.png"], [region])

    def test_pixel_coordinate_vl_reply_is_scaled_to_image(self) -> None:
        image = Image.new("RGB", (2000, 1100), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        result = _normalized_regions(
            [{"x1": 80, "y1": 100, "x2": 2300, "y2": 1020, "reason": "cards"}],
            buffer.getvalue(),
        )
        self.assertEqual(result, [{"x1": 35, "y1": 91, "x2": 1000, "y2": 927, "reason": "cards"}])
        with self.assertRaises(ValueError):
            _normalized_regions(
                [{"x1": 80, "y1": 100, "x2": 4000, "y2": 1020, "reason": "cards"}],
                buffer.getvalue(),
            )

    def test_masked_photo_collage_keeps_only_a_complete_pair(self) -> None:
        photos = [
            {"type": "pic", "box": [0.0, 0.1, 0.49, 0.9], "image": {"alpha": 0}},
            {"type": "pic", "box": [0.49, 0.27, 0.51, 0.73], "image": {"alpha": 0}},
        ]
        mask = {
            "type": "pic",
            "box": [0.0, 0.0, 1.0, 1.0],
            "image": {"alpha": 0.47, "colors": 2},
        }
        rows = [*photos, mask]
        decisions = [
            {"role": "content", "action": "remove", "reason": "interior_subject_raster"},
            {"role": "content", "action": "remove", "reason": "interior_subject_raster"},
            {"role": "background", "action": "keep", "reason": "full_canvas_surface"},
        ]
        preserve_masked_photo_collage(rows, decisions)
        self.assertTrue(all(item["action"] == "keep" for item in decisions))
        self.assertEqual(decisions[0]["reason"], "masked_photo_backdrop")

        rows[1] = {**photos[1], "box": [0.55, 0.27, 0.45, 0.73]}
        decisions[0].update(role="content", action="remove", reason="interior_subject_raster")
        decisions[1].update(role="content", action="remove", reason="interior_subject_raster")
        preserve_masked_photo_collage(rows, decisions)
        self.assertEqual(decisions[0]["action"], "remove")
        self.assertEqual(decisions[1]["action"], "remove")

    def test_two_screenshot_header_strips_are_removed(self) -> None:
        rows = [
            {
                "type": "pic",
                "box": [0.06, y, 0.88, 0.035],
                "recurring": 1,
                "metadata": "UI bar",
            }
            for y in (0.26, 0.295)
        ]
        rows.append({"type": "pic", "box": [0.03, 0.33, 0.94, 0.67]})
        decisions = [
            {"action": "keep", "reason": "transparent_flat_artwork"},
            {"action": "keep", "reason": "transparent_flat_artwork"},
            {"action": "remove", "reason": "interior_subject_raster"},
        ]
        remove_screenshot_header_strips(rows, decisions)
        self.assertEqual([item["action"] for item in decisions], ["remove"] * 3)
        decisions[0].update(action="keep", reason="transparent_flat_artwork")
        decisions[1].update(action="remove", reason="interior_subject_raster")
        remove_screenshot_header_strips(rows, decisions)
        self.assertEqual(decisions[0]["action"], "keep")

    def test_embedded_card_is_removed_without_changing_outer_canvas(self) -> None:
        image = Image.new("RGB", (400, 240), (248, 250, 254))
        for y in range(240):
            for x in range(400):
                if 150 <= x < 260 and 40 <= y < 190:
                    image.putpixel((x, y), (255, 255, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(smooth_light_canvas(buffer.getvalue()))
        self.assertEqual(len(large_white_content_surface(buffer.getvalue())), 0)
        result = reconstruct_regions(
            buffer.getvalue(), [{"x1": 375, "y1": 167, "x2": 650, "y2": 792}]
        )
        with Image.open(BytesIO(result)) as cleaned:
            self.assertEqual(cleaned.getpixel((200, 120)), (248, 250, 254))
            self.assertEqual(cleaned.getpixel((10, 10)), (248, 250, 254))

    def test_faint_full_page_card_fallback(self) -> None:
        image = Image.new("RGB", (400, 240), (248, 250, 254))
        for y in range(12, 228):
            for x in range(12, 388):
                image.putpixel((x, y), (255, 255, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        regions = large_white_content_surface(buffer.getvalue())
        self.assertEqual(len(regions), 1)
        with Image.open(BytesIO(reconstruct_regions(buffer.getvalue(), regions))) as cleaned:
            self.assertEqual(cleaned.getpixel((200, 120)), (248, 250, 254))

    def test_repeated_sample_icons_and_backings_are_removed_but_logo_survives(self) -> None:
        contents = [shape(2, 20, 20, 80, 25, name="Corporate logo")]
        for index, y in enumerate((180, 370, 560)):
            base = 10 + index * 3
            contents.extend(
                (
                    shape(base, 450, y, 80, 80),
                    picture(base + 1, 466, y + 16, 48, 48),
                    shape(base + 2, 550, y, 400, 90, text="Sample content"),
                )
            )
        slide = (
            f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
            + "".join(contents)
            + "</p:spTree></p:cSld></p:sld>"
        ).encode()
        rels = (
            f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
            f'Type="{R}/image" Target="../media/icon.png"/></Relationships>'
        ).encode()
        icon = Image.new("RGBA", (16, 16), (20, 100, 220, 0))
        for x in range(3, 13):
            for y in range(3, 13):
                icon.putpixel((x, y), (20, 100, 220, 255))
        buffer = BytesIO()
        icon.save(buffer, format="PNG")
        part = "ppt/slides/slide1.xml"
        parts = {
            part: slide,
            "ppt/slides/_rels/slide1.xml.rels": rels,
            "ppt/media/icon.png": buffer.getvalue(),
        }
        decisions = background_roles(parts, 1000, 720)[part]
        by_id = {row["id"]: row for row in decisions}
        self.assertEqual(by_id["2"]["role"], "identity")
        self.assertEqual(by_id["2"]["action"], "keep")
        for sid in (10, 11, 13, 14, 16, 17):
            self.assertEqual(by_id[str(sid)]["action"], "remove")
        root = ET.fromstring(slide)
        apply_background_roles(root, decisions)
        surviving = {node.get("id") for node in root.iter(f"{{{P}}}cNvPr")}
        self.assertIn("2", surviving)
        self.assertFalse({"10", "11", "13", "14", "16", "17"} & surviving)

    def test_dynamic_page_field_does_not_reappear(self) -> None:
        slide = (
            f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Footer"/></p:nvSpPr>'
            "<p:txBody><a:p><a:r><a:t>© Example | Page </a:t></a:r>"
            '<a:fld type="slidenum"><a:t>5</a:t></a:fld></a:p></p:txBody></p:sp>'
            "</p:spTree></p:cSld></p:sld>"
        ).encode()
        roles = background_roles({"ppt/slides/slide1.xml": slide}, 1000, 720)[
            "ppt/slides/slide1.xml"
        ]
        root = ET.fromstring(slide)
        apply_background_roles(root, roles)
        self.assertFalse(list(root.iter(f"{{{A}}}fld")))


if __name__ == "__main__":
    unittest.main()

