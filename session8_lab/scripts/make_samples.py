# make_samples.py — generate the Session 8 sample intake documents
#
# Renders two simple document-like PNGs with PIL and writes a matching .txt
# sidecar with the SAME content for each:
#   data/samples/garage_estimate_clm1001.png / .txt   (motor claim CLM-1001)
#   data/samples/discharge_summary_clm1005.png / .txt (health claim CLM-1005)
#
# The sidecars power the LOCAL intake path: the 0.5B model is text-only, so
# POST /intake (without VISION_MODEL) reads the sidecar matching the uploaded
# filename and runs the same extraction prompt over the text.
#
# Run from the session8_lab directory:   python scripts/make_samples.py
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

GARAGE_ESTIMATE = """\
SHREE AUTO WORKS — AUTHORISED GARAGE
Repair Estimate No. GE-2314        Date: 2026-07-05

Claim ID: CLM-1001
Policy No: POL-88231
Customer: Asha Verma
Vehicle: Private car, accidental front-end damage

ITEM                              AMOUNT (INR)
Front bumper replacement                18,500
Bonnet panel repair & repaint           22,000
Headlamp assembly (right)               14,800
Radiator support member                 16,700
Labour & consumables                    12,500
                                  ------------
TOTAL ESTIMATE                          84,500

Repairs to commence only after surveyor inspection.
"""

DISCHARGE_SUMMARY = """\
CITY CARE HOSPITAL — DISCHARGE SUMMARY
Summary No. DS-8872               Date: 2026-07-18

Claim ID: CLM-1005
Policy No: POL-73310
Patient: Priya Nair

Date of admission: 2026-07-14
Date of discharge: 2026-07-18
Diagnosis: Acute appendicitis
Treatment given: Laparoscopic appendectomy under
general anaesthesia; uneventful recovery.

Total hospital charges (INR): 67,800

Condition at discharge: Stable. Review in OPD
after 10 days. This summary is a mandatory claim
document under clause H-5.1.
"""

SAMPLES = [
    ("garage_estimate_clm1001", GARAGE_ESTIMATE),
    ("discharge_summary_clm1005", DISCHARGE_SUMMARY),
]


def render_png(text: str, path: Path) -> None:
    """A plain 'scanned document' look: dark text on white, ruled header."""
    lines = text.splitlines()
    width, line_h, margin = 760, 26, 40
    height = margin * 2 + line_h * len(lines)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:  # a monospace face if the OS has one; PIL's default otherwise
        font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([20, 20, width - 20, height - 20], outline="#888888", width=2)
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_h), line, fill="#1a1a1a", font=font)
    img.save(path)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stem, text in SAMPLES:
        png = OUT_DIR / f"{stem}.png"
        txt = OUT_DIR / f"{stem}.txt"
        render_png(text, png)
        txt.write_text(text, encoding="utf-8")
        print(f"wrote {png}  (+ sidecar {txt.name})")
    print("\nDone. Upload the PNGs in the UI's 'Upload document' tab.")


if __name__ == "__main__":
    main()
