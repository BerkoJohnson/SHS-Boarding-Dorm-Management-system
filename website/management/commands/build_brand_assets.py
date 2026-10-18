"""Generate the public website's favicon set and Open Graph image.

The Eden Creative Systems mark is used exactly as supplied: it is scaled
proportionally and centred, never cropped, stretched or recoloured. Output goes
to a namespaced directory so the existing school-application favicons in
``static/favicons/`` are left untouched.

Usage::

    python manage.py build_brand_assets

Re-run this whenever the supplied mark changes. The generated files are
committed to the repository, consistent with how ``static/css/output.css`` is
handled.
"""
from __future__ import annotations

import io
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    from PIL import Image
except ImportError as error:  # pragma: no cover - Pillow is a project dependency
    raise CommandError("Pillow is required to build brand assets.") from error

SOURCE_RELATIVE = Path("img") / "eden-creative-systems-mark.png"
OUTPUT_DIRECTORY = Path("img") / "eden"

FAVICON_BACKGROUND = (255, 255, 255, 255)
OG_BACKGROUND = (0, 26, 77, 255)  # eden.navy, sampled from the brand mark

SQUARE_SIZES = {
    "favicon-16x16.png": (16, 0.06),
    "favicon-32x32.png": (32, 0.08),
    "apple-touch-icon.png": (180, 0.12),
    "android-chrome-192x192.png": (192, 0.12),
    "android-chrome-512x512.png": (512, 0.12),
}

ICO_SIZES = (16, 32, 48, 64)

OG_SIZE = (1200, 630)


class Command(BaseCommand):
    help = "Generate Edensys favicons and the Open Graph image from the brand mark."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report the output that would be written without writing it.",
        )

    def handle(self, *args, **options):
        static_root = Path(settings.STATICFILES_DIRS[0])
        source = static_root / SOURCE_RELATIVE
        output_directory = static_root / OUTPUT_DIRECTORY
        icon_directory = output_directory / "favicon"

        if not source.exists():
            raise CommandError(f"Brand mark not found at {source}.")

        with Image.open(source) as opened:
            mark = opened.convert("RGBA")
        mark.thumbnail((2048, 2048))

        plan = [icon_directory / name for name in SQUARE_SIZES]
        plan.append(icon_directory / "favicon.ico")
        plan.append(output_directory / "og-image.png")

        if options["check"]:
            for target in plan:
                self.stdout.write(str(target))
            return

        icon_directory.mkdir(parents=True, exist_ok=True)
        self._write_squares(mark, icon_directory)
        self._write_ico(mark, icon_directory / "favicon.ico")
        self._write_og_image(mark, output_directory / "og-image.png")

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(plan)} brand assets from {source.name}."
            )
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _placed(mark: Image.Image, size: int, padding_ratio: float):
        """Return the mark scaled to fit ``size`` with proportional padding."""
        inner = max(1, int(size * (1 - 2 * padding_ratio)))
        scaled = mark.copy()
        scaled.thumbnail((inner, inner), Image.Resampling.LANCZOS)
        offset = ((size - scaled.width) // 2, (size - scaled.height) // 2)
        return scaled, offset

    def _write_squares(self, mark: Image.Image, directory: Path) -> None:
        for filename, (size, padding) in SQUARE_SIZES.items():
            canvas = Image.new("RGBA", (size, size), FAVICON_BACKGROUND)
            scaled, offset = self._placed(mark, size, padding)
            canvas.alpha_composite(scaled, offset)
            canvas.convert("RGB").save(directory / filename, format="PNG")

    def _write_ico(self, mark: Image.Image, target: Path) -> None:
        largest = Image.new("RGBA", (256, 256), FAVICON_BACKGROUND)
        scaled, offset = self._placed(mark, 256, 0.08)
        largest.alpha_composite(scaled, offset)
        largest.convert("RGB").save(
            target, format="ICO", sizes=[(size, size) for size in ICO_SIZES]
        )

    def _write_og_image(self, mark: Image.Image, target: Path) -> None:
        canvas = Image.new("RGBA", OG_SIZE, OG_BACKGROUND)
        inner = int(OG_SIZE[1] * 0.62)
        scaled = mark.copy()
        scaled.thumbnail((inner, inner), Image.Resampling.LANCZOS)
        offset = (
            (OG_SIZE[0] - scaled.width) // 2,
            (OG_SIZE[1] - scaled.height) // 2,
        )
        canvas.alpha_composite(scaled, offset)

        # A restrained gold rule beneath the mark, echoing the brand accent.
        rule = Image.new("RGBA", (OG_SIZE[0] // 4, 4), (201, 162, 39, 255))
        canvas.alpha_composite(
            rule,
            ((OG_SIZE[0] - rule.width) // 2, offset[1] + scaled.height + 40),
        )
        canvas.convert("RGB").save(target, format="PNG", optimize=True)
