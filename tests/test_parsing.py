import os

from app.parsing import UnsupportedDocumentType, extract_text

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_docs")


def test_extract_text_from_txt():
    text = extract_text(os.path.join(SAMPLE_DIR, "sample_invoice.txt"))
    assert "Acme Robotics Inc." in text
    assert "INV-10234" in text


def test_extract_text_from_pdf():
    text = extract_text(os.path.join(SAMPLE_DIR, "sample_invoice.pdf"))
    assert "Acme" in text
    assert "INV-10234" in text
    assert "Widget assembly" in text


def test_extract_text_from_docx():
    text = extract_text(os.path.join(SAMPLE_DIR, "sample_invoice.docx"))
    assert "Acme Robotics Inc." in text
    assert "Widget assembly (Model X)" in text
    assert "\t" in text  # table rows are tab-joined


def test_unsupported_extension_raises(tmp_path):
    bad_file = tmp_path / "invoice.xyz"
    bad_file.write_text("hello")
    try:
        extract_text(str(bad_file))
        assert False, "expected UnsupportedDocumentType"
    except UnsupportedDocumentType:
        pass
