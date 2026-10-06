"""Local OCR: turn pixels (image files, or rendered pages of a scanned PDF)
into layout-preserving text, with no network and no API key.

Two engines, both optional at install time and detected lazily:

- RapidOCR (`rapidocr-onnxruntime`) — pure pip, ONNX models shipped inside the
  wheel, so it runs fully offline once installed. Returns per-line boxes and
  scores; rows are rebuilt from the box geometry so invoice tables keep their
  columns (cells of one row are joined with "\\t", the same convention
  `parsing.py` uses for DOCX tables and the heuristic line-item parser reads).
- Tesseract (`pytesseract` + the Tesseract binary on PATH) — used when
  RapidOCR is not installed. Tesseract preserves the layout in its own text
  output.

`OCR_ENGINE` picks one: `auto` (default, try RapidOCR then Tesseract), `rapid`,
`tesseract`, or `off` (never OCR; image input then needs the AI vision path).

When no engine is installed, `ocr_image` raises `OcrUnavailable` and the
callers fall back to sending the image to the model's vision input instead.
"""
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Rows whose vertical centres sit within this many pixels of each other are
# treated as one line of text. Tuned for ~200 DPI page renders.
ROW_TOLERANCE_PX = 28

# Below this mean per-line score the OCR text is treated as unreliable and the
# caller is encouraged to escalate to the vision model.
OCR_UNRELIABLE_SCORE = 0.60


class OcrUnavailable(RuntimeError):
    """No OCR engine installed (or disabled). Image input then needs AI
    credentials so the pixels can go to a vision model instead."""


@dataclass
class OcrResult:
    text: str
    engine: str
    mean_score: float
    line_count: int

    @property
    def unreliable(self) -> bool:
        return self.mean_score < OCR_UNRELIABLE_SCORE


def _engine_pref() -> str:
    return os.environ.get("OCR_ENGINE", "auto").strip().lower() or "auto"


def available_engine() -> Optional[str]:
    """Name of the OCR engine that would be used right now, or None when OCR is
    off/unavailable. Never runs an OCR pass — only checks imports and PATH."""
    pref = _engine_pref()
    if pref == "off":
        return None
    if pref in ("auto", "rapid") and _rapid_importable():
        return "rapidocr"
    if pref in ("auto", "tesseract") and _tesseract_importable():
        return "tesseract"
    return None


def _rapid_importable() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _tesseract_importable() -> bool:
    """pytesseract is only usable when the Tesseract binary is on PATH, which
    `get_tesseract_version()` probes (it raises when the binary is missing)."""
    try:
        import pytesseract

        return pytesseract.get_tesseract_version() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Layout reconstruction from per-line boxes
# ---------------------------------------------------------------------------

def group_boxes_into_rows(
    items: Sequence[Tuple[Sequence[Sequence[float]], str, float]],
    tolerance: int = ROW_TOLERANCE_PX,
) -> List[str]:
    """`items` is (box, text, score) per detected line, box being 4 corner
    points. Returns one string per page row, cells joined left-to-right with
    tabs. Boxes that overlap vertically belong to the same row."""
    decorated = []
    for box, text, score in items:
        text = (text or "").strip()
        if not text:
            continue
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        decorated.append({
            "y": sum(ys) / len(ys),
            "x": min(xs),
            "text": text,
            "score": float(score),
        })

    decorated.sort(key=lambda d: d["y"])
    rows: List[List[dict]] = []
    for entry in decorated:
        if rows and abs(rows[-1][0]["y"] - entry["y"]) <= tolerance:
            rows[-1].append(entry)
        else:
            rows.append([entry])

    lines = []
    for row in rows:
        row.sort(key=lambda d: d["x"])
        lines.append("\t".join(d["text"] for d in row))
    return lines


def _mean(scores: Sequence[float]) -> float:
    return round(sum(scores) / len(scores), 3) if scores else 0.0


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class _RapidEngine:
    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        # Model load is ~0.5-2s; done once per process and cached by
        # `get_engine()`, so only the first image request pays it.
        self._ocr = RapidOCR()

    def run(self, paths: List[str]) -> OcrResult:
        all_lines: List[str] = []
        scores: List[float] = []
        for path in paths:
            result, _ = self._ocr(path)
            if not result:
                continue
            boxes = [(box, text, score) for box, text, score in result]
            all_lines.extend(group_boxes_into_rows(boxes))
            scores.extend(score for _, _, score in boxes)
        return OcrResult(
            text="\n".join(all_lines).strip(),
            engine=self.name,
            mean_score=_mean(scores),
            line_count=len(all_lines),
        )


class _TesseractEngine:
    name = "tesseract"

    def __init__(self):
        import pytesseract

        self._pytesseract = pytesseract

    def run(self, paths: List[str]) -> OcrResult:
        from PIL import Image

        chunks: List[str] = []
        for path in paths:
            with Image.open(path) as img:
                chunks.append(self._pytesseract.image_to_string(img))
        text = "\n\n".join(chunk.strip() for chunk in chunks).strip()
        # Tesseract's plain-text API reports no confidence; assume a middling
        # 0.7 so the auto gate can still escalate on garbage output.
        return OcrResult(
            text=text, engine=self.name, mean_score=0.7, line_count=len(text.splitlines())
        )


_engine_cache: dict = {}


def get_engine():
    """Instantiate (and cache) the configured OCR engine. Raises
    `OcrUnavailable` when nothing usable is installed."""
    name = available_engine()
    if name is None:
        raise OcrUnavailable(
            "no OCR engine available - install 'rapidocr-onnxruntime' (see "
            "requirements-ocr.txt) or Tesseract + pytesseract, or set ANTHROPIC_* "
            "env vars so image pages can be read by a vision model instead"
        )
    if name not in _engine_cache:
        _engine_cache[name] = _RapidEngine() if name == "rapidocr" else _TesseractEngine()
    return _engine_cache[name]


def ocr_image(path: str) -> OcrResult:
    return ocr_pages([path])


def ocr_pages(paths: List[str]) -> OcrResult:
    if not paths:
        return OcrResult(text="", engine="none", mean_score=0.0, line_count=0)
    return get_engine().run(paths)
