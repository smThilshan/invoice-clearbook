"""Generated test documents (never client data)."""

import io

import pymupdf
from PIL import Image as PILImage

INVOICE_LINES = ["TAX INVOICE", "Invoice No: INV-00291", "Date: 12 Sep 2025", "Total AED 1,050.10"]


def digital_pdf(pages: int = 1) -> bytes:
    """PDF with a real text layer (A4)."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        for i, line in enumerate(INVOICE_LINES):
            page.insert_text((60, 80 + i * 40), line, fontsize=18)
    return bytes(doc.tobytes())


def page_png(width: int = 1190, height: int = 600) -> bytes:
    """A picture of the invoice text (what a scanner produces)."""
    doc = pymupdf.open()
    page = doc.new_page(width=width / 2, height=height / 2)
    for i, line in enumerate(INVOICE_LINES):
        page.insert_text((30, 40 + i * 30), line, fontsize=14)
    return bytes(page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png"))


def scanned_pdf(pages: int = 1) -> bytes:
    """PDF whose pages are only images: no text layer, needs OCR."""
    doc = pymupdf.open()
    image = page_png()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 300), stream=image)
    return bytes(doc.tobytes())


def jpeg(width: int, height: int, *, exif_orientation: int | None = None) -> bytes:
    """Plain JPEG; orientation 6 = 'rotate 90° clockwise to display' (phone held upright)."""
    image = PILImage.new("RGB", (width, height), "white")
    exif = PILImage.Exif()
    if exif_orientation is not None:
        exif[0x0112] = exif_orientation
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()
