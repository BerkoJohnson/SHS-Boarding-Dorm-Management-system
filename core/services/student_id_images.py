from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from django.conf import settings


CARD_WIDTH = 1011
CARD_HEIGHT = 638
CARD_DPI = 300

_COLORS = {
    "classic": {
        "ink": "#172554",
        "accent": "#b45309",
        "pale": "#eff3fb",
        "muted": "#64748b",
        "text": "#0f172a",
    },
    "crest": {
        "ink": "#14532d",
        "accent": "#39854d",
        "pale": "#f0f8ef",
        "muted": "#668271",
        "text": "#193625",
    },
    "modern": {
        "ink": "#172554",
        "accent": "#0f766e",
        "pale": "#eff6ff",
        "muted": "#64748b",
        "text": "#0f172a",
    },
}
_FONT_PATHS = (
    (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ),
)


def safe_student_folder_name(student) -> str:
    """Return a filesystem-safe folder name for a student's card images."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", student.full_name).strip(" .")
    admission_no = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        student.admission_no,
    ).strip(" .")
    return f"{admission_no or student.pk} - {name or 'Student'}"


def private_student_id_export_path(archive_name: str) -> Path:
    if Path(archive_name).name != archive_name or not archive_name.endswith(".zip"):
        raise ValueError("Invalid student ID export archive name.")
    return Path(settings.BASE_DIR) / ".private" / "student_id_exports" / archive_name


def render_student_card_side(
    student,
    school_configuration,
    public_url: str,
    layout: str,
    side: str,
    image_format: str,
) -> bytes:
    if layout not in _COLORS:
        raise ValueError("Choose a supported student ID layout.")
    if side not in {"front", "back"}:
        raise ValueError("Choose a student ID card side.")
    if image_format not in {"PNG", "JPEG"}:
        raise ValueError("Choose PNG or JPEG for the student ID export.")

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    colors = _COLORS[layout]
    fonts = _load_fonts()
    if side == "front":
        _draw_front(image, draw, fonts, colors, layout, student, school_configuration)
    else:
        _draw_back(
            image,
            draw,
            fonts,
            colors,
            layout,
            public_url,
            school_configuration,
        )

    output = BytesIO()
    if image_format == "PNG":
        image.save(output, format="PNG", dpi=(CARD_DPI, CARD_DPI), optimize=True)
    else:
        image.save(
            output,
            format="JPEG",
            dpi=(CARD_DPI, CARD_DPI),
            quality=95,
            subsampling=0,
            optimize=True,
        )
    return output.getvalue()


def _load_fonts():
    regular_path = bold_path = None
    for regular, bold in _FONT_PATHS:
        if regular.is_file() and bold.is_file():
            regular_path, bold_path = regular, bold
            break
    if regular_path is None:
        return {
            "small": ImageFont.load_default(),
            "label": ImageFont.load_default(),
            "body": ImageFont.load_default(),
            "body_bold": ImageFont.load_default(),
            "title": ImageFont.load_default(),
            "heading": ImageFont.load_default(),
        }
    return {
        "small": ImageFont.truetype(str(regular_path), 19),
        "label": ImageFont.truetype(str(bold_path), 20),
        "body": ImageFont.truetype(str(regular_path), 25),
        "body_bold": ImageFont.truetype(str(bold_path), 27),
        "title": ImageFont.truetype(str(bold_path), 40),
        "heading": ImageFont.truetype(str(bold_path), 31),
    }


def _draw_front(image, draw, fonts, colors, layout, student, school_configuration):
    draw.rounded_rectangle(
        (0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1),
        radius=28,
        fill="white",
        outline="#d1e3d5" if layout == "crest" else "#cbd5e1",
        width=3,
    )

    if layout == "crest":
        _draw_crest_front_header(image, draw, fonts, colors, student, school_configuration)
    elif layout == "classic":
        draw.rectangle((0, 0, CARD_WIDTH, 145), fill=colors["ink"])
        _draw_school_logo(image, draw, fonts, school_configuration, (34, 27, 100, 100))
        _draw_school_heading(draw, fonts, 125, 40, "white", "#dbeafe", school_configuration.name)
        draw.text((CARD_WIDTH - 180, 57), student.get_residency_display().upper(), font=fonts["label"], fill="#dbeafe")
    else:
        draw.rectangle((0, 0, CARD_WIDTH, 14), fill=colors["accent"])
        _draw_school_logo(image, draw, fonts, school_configuration, (34, 30, 91, 91))
        _draw_school_heading(draw, fonts, 110, 37, colors["ink"], colors["muted"], school_configuration.name)
        draw.rounded_rectangle((790, 42, 974, 91), radius=20, fill=colors["pale"])
        draw.text((810, 54), student.get_residency_display().upper(), font=fonts["label"], fill=colors["ink"])

    photo_box = (44, 181, 267, 478) if layout == "crest" else (767, 181, 970, 472)
    _draw_photo(image, student, photo_box, colors)
    if layout == "crest":
        info_x = 306
        draw.text((info_x, 184), _fit_text(draw, student.full_name, fonts["title"], 653), font=fonts["title"], fill=colors["text"])
        draw.text((info_x, 241), _fit_text(draw, student.programme.name, fonts["label"], 653), font=fonts["label"], fill=colors["accent"])
        _draw_student_fields(draw, fonts, student, info_x, 300, 2, colors, 305)
        _draw_geometric_footer(draw)
    else:
        info_x = 44
        draw.text((info_x, 184), _fit_text(draw, student.full_name, fonts["title"], 685), font=fonts["title"], fill=colors["text"])
        draw.text((info_x, 241), _fit_text(draw, student.programme.name, fonts["label"], 685), font=fonts["label"], fill=colors["accent"])
        _draw_student_fields(draw, fonts, student, info_x, 300, 2, colors, 330)
        if layout == "classic":
            draw.rectangle((0, CARD_HEIGHT - 15, CARD_WIDTH, CARD_HEIGHT), fill=colors["accent"])
        else:
            draw.polygon(
                [(0, CARD_HEIGHT - 22), (CARD_WIDTH - 115, CARD_HEIGHT - 22), (CARD_WIDTH, CARD_HEIGHT - 102), (CARD_WIDTH, CARD_HEIGHT), (0, CARD_HEIGHT)],
                fill="#e7f4f1",
            )
            draw.rectangle((0, CARD_HEIGHT - 12, CARD_WIDTH, CARD_HEIGHT), fill=colors["accent"])


def _draw_crest_front_header(image, draw, fonts, colors, student, school_configuration):
    draw.rectangle((0, 0, CARD_WIDTH, 145), fill="#f5fbf5")
    draw.polygon([(720, 0), (CARD_WIDTH, 0), (CARD_WIDTH, 145), (800, 145)], fill="#d8eed7")
    draw.polygon([(830, 0), (CARD_WIDTH, 0), (CARD_WIDTH, 145), (910, 145)], fill="#80bc71")
    draw.polygon([(940, 0), (CARD_WIDTH, 0), (CARD_WIDTH, 145)], fill="#39854d")
    _draw_school_logo(image, draw, fonts, school_configuration, (35, 25, 98, 98), circular=True)
    _draw_school_heading(draw, fonts, 145, 39, "#123b27", "#477153", school_configuration.name)
    draw.rounded_rectangle((823, 50, 974, 99), radius=22, fill="#e3f2df")
    draw.text((841, 62), student.get_residency_display().upper(), font=fonts["label"], fill="#24613b")


def _draw_school_heading(draw, fonts, x, y, title_color, subtitle_color, school_name):
    title = _fit_text(draw, school_name, fonts["heading"], CARD_WIDTH - x - 235)
    draw.text((x, y), title, font=fonts["heading"], fill=title_color)
    draw.text((x, y + 49), "STUDENT IDENTITY CARD", font=fonts["small"], fill=subtitle_color)


def _draw_student_fields(draw, fonts, student, start_x, start_y, columns, colors, column_width):
    fields = (
        ("ADMISSION NO.", student.admission_no),
        ("INDEX NO.", student.index_number),
        ("CLASS", student.class_group.name if student.class_group else "—"),
        ("HOUSE", student.house.name if student.house else "—"),
    )
    for index, (label, value) in enumerate(fields):
        x = start_x + (index % columns) * column_width
        y = start_y + (index // columns) * 91
        draw.text((x, y), label, font=fonts["label"], fill=colors["muted"])
        draw.text((x, y + 32), _fit_text(draw, value, fonts["body_bold"], column_width - 18), font=fonts["body_bold"], fill=colors["text"])


def _draw_back(image, draw, fonts, colors, layout, public_url, school_configuration):
    draw.rounded_rectangle(
        (0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1),
        radius=28,
        fill="white",
        outline="#d1e3d5" if layout == "crest" else "#cbd5e1",
        width=3,
    )
    if layout == "crest":
        panel_width = 205
        _draw_geometric_panel(draw, panel_width)
        _draw_school_logo(
            image,
            draw,
            fonts,
            school_configuration,
            (57, 250, 148, 341),
            circular=True,
        )
        content_left = 230
    else:
        panel_width = 16
        draw.rectangle((0, 0, panel_width, CARD_HEIGHT), fill=colors["ink"])
        if layout == "modern":
            draw.rectangle((panel_width, 0, panel_width + 13, CARD_HEIGHT), fill="#ccfbf1")
        content_left = 46

    draw.text((content_left, 105), "EXEAT PASS VERIFICATION", font=fonts["heading"], fill=colors["ink"])
    _draw_wrapped_text(
        draw,
        "Scan this code to check the student's latest exeat request, approval and recorded gate movements.",
        (content_left, 151),
        fonts["body"],
        colors["muted"],
        max_width=475,
        max_lines=3,
    )
    draw.line((content_left, 286, content_left + 473, 286), fill="#dce8dc", width=3)
    draw.text((content_left, 314), "IF FOUND, RETURN TO", font=fonts["label"], fill=colors["muted"])
    draw.text(
        (content_left, 351),
        _fit_text(draw, school_configuration.name, fonts["body_bold"], 473),
        font=fonts["body_bold"],
        fill=colors["ink"],
    )
    if school_configuration.address:
        draw.text(
            (content_left, 391),
            _fit_text(draw, school_configuration.address, fonts["small"], 473),
            font=fonts["small"],
            fill=colors["muted"],
        )
    if school_configuration.contact_number:
        draw.text(
            (content_left, 416),
            _fit_text(draw, school_configuration.contact_number, fonts["small"], 473),
            font=fonts["small"],
            fill=colors["muted"],
        )

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=9, border=2)
    qr.add_data(public_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if layout == "crest":
        qr_box = (747, 150, 987, 390)
    else:
        qr_box = (735, 126, 975, 366)
    qr_image = ImageOps.fit(qr_image, (qr_box[2] - qr_box[0], qr_box[3] - qr_box[1]))
    image.paste(qr_image, (qr_box[0], qr_box[1]))
    draw.text((qr_box[0] + 8, qr_box[3] + 12), "SCAN TO VERIFY", font=fonts["label"], fill=colors["accent"])


def _draw_geometric_panel(draw, width):
    draw.rectangle((0, 0, width, CARD_HEIGHT), fill="#419253")
    draw.polygon([(0, 0), (width, 0), (0, 250)], fill="#8bc76e")
    draw.polygon([(width, 0), (width, 225), (0, 390)], fill="#246b3c")
    draw.polygon([(0, 390), (width, 225), (width, 440), (0, CARD_HEIGHT)], fill="#63aa62")
    draw.polygon([(0, CARD_HEIGHT), (width, 440), (width, CARD_HEIGHT)], fill="#2d7744")
    draw.polygon([(0, 0), (width, 225), (width, 0)], fill="#b4da87")


def _draw_geometric_footer(draw):
    y = CARD_HEIGHT - 22
    draw.polygon([(0, y), (175, y), (240, CARD_HEIGHT), (0, CARD_HEIGHT)], fill="#21643a")
    draw.polygon([(170, y), (355, y), (420, CARD_HEIGHT), (238, CARD_HEIGHT)], fill="#4d9959")
    draw.polygon([(350, y), (555, y), (620, CARD_HEIGHT), (418, CARD_HEIGHT)], fill="#9bcd76")
    draw.polygon([(550, y), (770, y), (835, CARD_HEIGHT), (618, CARD_HEIGHT)], fill="#e2f1d9")
    draw.rectangle((835, y, CARD_WIDTH, CARD_HEIGHT), fill="#39854d")


def _draw_photo(image, student, box, colors):
    left, top, right, bottom = box
    photo = _open_image(student.photo)
    if photo:
        photo = ImageOps.fit(photo, (right - left, bottom - top), centering=(0.5, 0.38))
    else:
        photo = Image.new("RGB", (right - left, bottom - top), colors["pale"])
        draw = ImageDraw.Draw(photo)
        fonts = _load_fonts()
        initial = student.full_name[:1].upper()
        bounds = draw.textbbox((0, 0), initial, font=fonts["title"])
        draw.text(
            ((photo.width - bounds[2]) // 2, (photo.height - bounds[3]) // 2),
            initial,
            font=fonts["title"],
            fill=colors["muted"],
        )
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width - 1, photo.height - 1), radius=16, fill=255)
    image.paste(photo, (left, top), mask)


def _open_image(field):
    if not field:
        return None
    with field.open("rb") as image_file:
        with Image.open(image_file) as image:
            return ImageOps.exif_transpose(image).convert("RGBA")


def _draw_school_logo(image, draw, fonts, school_configuration, box, circular=False):
    left, top, right, bottom = box
    bounds = (left, top, right - 1, bottom - 1)
    if circular:
        draw.ellipse(bounds, fill="white", outline="#94a3b8", width=3)
    else:
        draw.rounded_rectangle(bounds, radius=8, fill="white", outline="#94a3b8", width=3)
    logo = _open_image(school_configuration.logo)
    if logo:
        logo = ImageOps.contain(logo, (right - left - 12, bottom - top - 12))
        mask = Image.new("L", logo.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        if circular:
            mask_draw.ellipse((0, 0, logo.width - 1, logo.height - 1), fill=255)
        else:
            mask_draw.rounded_rectangle(
                (0, 0, logo.width - 1, logo.height - 1),
                radius=5,
                fill=255,
            )
        mask = ImageChops.multiply(mask, logo.getchannel("A"))
        image.paste(
            logo,
            (
                left + (right - left - logo.width) // 2,
                top + (bottom - top - logo.height) // 2,
            ),
            mask,
        )
        return

    label_font = fonts["small"]
    label = _fit_text(draw, "LOGO", label_font, right - left - 12)
    text_bounds = draw.textbbox((0, 0), label, font=label_font)
    x = left + ((right - left) - (text_bounds[2] - text_bounds[0])) // 2
    y = top + ((bottom - top) - (text_bounds[3] - text_bounds[1])) // 2
    draw.text((x, y), label, font=label_font, fill="#64748b")


def _fit_text(draw, text, font, max_width):
    text = str(text or "")
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _wrap_lines(draw, text, font, max_width, max_lines):
    words = str(text or "").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit_text(draw, lines[-1] + " …", font, max_width)
    return lines


def _draw_wrapped_text(draw, text, position, font, fill, max_width, max_lines):
    x, y = position
    for index, line in enumerate(_wrap_lines(draw, text, font, max_width, max_lines)):
        draw.text((x, y + index * 34), line, font=font, fill=fill)
