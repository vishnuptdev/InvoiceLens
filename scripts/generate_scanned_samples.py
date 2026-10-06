"""Generate image-only (no text layer) invoice samples for testing the OCR and
vision paths: sample_docs/scanned_invoice.png and sample_docs/scanned_invoice.pdf.

A text-layer-free raster PDF is what a real photocopier scan looks like to this
service, so these two files are the standing fixtures for "can it read a
picture of an invoice" checks:

    python scripts/generate_scanned_samples.py

Both files are committed next to the other sample_docs. The PDF is built from
the PNG with Pillow, which embeds the raster page and adds no text layer.
"""
import os
import random

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_docs")

# Same content as sample_docs/sample_invoice.txt, laid out the way a printed
# invoice looks: label/value lines plus a spaced-out item table.
LINES = [
    (0,  "Acme Robotics Inc.", 44, "bold"),
    (1,  "123 Innovation Way, Springfield, IL", 26, "plain"),
    (2,  "Tax ID 31-0998877", 26, "plain"),
    (4,  "INVOICE", 60, "bold"),
    (6,  "Invoice Number: INV-10234", 30, "plain"),
    (7,  "Invoice Date: 03/14/2026", 30, "plain"),
    (8,  "Due Date: 04/13/2026", 30, "plain"),
    (10, "Bill To:", 30, "bold"),
    (11, "Contoso Widgets LLC", 30, "plain"),
    (12, "45 Procurement Rd, Detroit, MI", 26, "plain"),
    (15, "Description                  Qty    Unit Price    Amount", 28, "bold"),
    (16, "Widget assembly (Model X)    10     $45.00        $450.00", 28, "plain"),
    (17, "Custom bracket fabrication   4      $120.00       $480.00", 28, "plain"),
    (18, "Rush shipping fee            1      $75.50        $75.50", 28, "plain"),
    (21, "Subtotal:                    $1,005.50", 28, "plain"),
    (22, "Total:  $1,005.50", 36, "bold"),
    (25, "Payment terms: Net 30. Wire / ACH only.", 26, "plain"),
    (26, "Thank you for your business.", 26, "plain"),
]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    ordered = FONT_CANDIDATES if bold else FONT_CANDIDATES[::-1]
    for path in ordered:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def render_page() -> Image.Image:
    """A 200 DPI-ish grayscale 'scan' of the invoice: white page, ink text,
    speckle noise and a slight warp-free tilt (kept straight so OCR is fair)."""
    width, height = 1654, 2340
    img = Image.new("L", (width, height), 245)
    draw = ImageDraw.Draw(img)

    rng = random.Random(7)
    # faint paper mottling, so the image is not synthetic-perfect
    for _ in range(4000):
        x, y = rng.randrange(width), rng.randrange(height)
        draw.point((x, y), fill=rng.randrange(228, 246))

    row = 90
    line_unit = 74
    for idx, text, size, weight in LINES:
        draw.text((110, row + idx * line_unit), text, font=_font(size, weight == "bold"), fill=25)

    # rule under the header, like most printed invoices
    draw.line([(110, row + 5 * line_unit - 18), (width - 110, row + 5 * line_unit - 18)], fill=60, width=3)
    draw.line([(110, row + 14 * line_unit + 10), (width - 110, row + 14 * line_unit + 10)], fill=60, width=3)
    return img


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    page = render_page()

    png_path = os.path.join(OUT_DIR, "scanned_invoice.png")
    page.convert("RGB").save(png_path)

    # Image-only PDF: Pillow embeds the raster, writes no text layer.
    pdf_path = os.path.join(OUT_DIR, "scanned_invoice.pdf")
    page.convert("RGB").save(pdf_path, resolution=200.0)

    for path in (png_path, pdf_path):
        print(f"wrote {path} ({os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main()
