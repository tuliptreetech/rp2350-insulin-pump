"""
Read the simulated OLED back and look for text in it.

The font table is parsed straight out of src/drivers/font5x7.c rather than
duplicated here, so a test that asserts "the panel says OCCLUSION" is checking
the real glyphs the firmware would have drawn, and cannot drift away from them.
"""

import pathlib
import re

# The suite is shipped into the emerson-server container, where the firmware
# tree is not mounted, so the font table travels next to this file. Falling
# back to the repo path keeps it usable from a checkout as well.
_HERE = pathlib.Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE / "font5x7.c",
    _HERE.parent / "src" / "drivers" / "font5x7.c",
]
_SRC = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[-1])

FIRST_CHAR = 0x20
GLYPH_WIDTH = 5
ADVANCE = 6


def _load_font():
    text = _SRC.read_text()
    body = text[text.index("{"): text.rindex("}")]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    values = [int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{2}", body)]
    return values


FONT = _load_font()


def parse_dump(dump_text):
    """Turn `dump_display` ASCII art into a list of rows of booleans."""
    rows = []
    for line in dump_text.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        rows.append([c != "." for c in line[1:-1]])
    return rows


def render(text):
    """Render `text` at scale 1 as a 7-row-tall bitmap of booleans."""
    cells = [[False] * (ADVANCE * len(text)) for _ in range(8)]
    for i, ch in enumerate(text):
        idx = (ord(ch) - FIRST_CHAR) * GLYPH_WIDTH
        for col in range(GLYPH_WIDTH):
            bits = FONT[idx + col]
            for row in range(8):
                if bits & (1 << row):
                    cells[row][i * ADVANCE + col] = True
    return cells


def contains_text(dump_text, text):
    """True if `text` appears anywhere on the panel, drawn at scale 1."""
    screen = parse_dump(dump_text)
    if not screen:
        return False
    pattern = render(text)
    ph, pw = len(pattern), len(pattern[0])
    sh, sw = len(screen), len(screen[0])

    # Only the lit pixels of the glyphs are matched. The pump inverts the
    # alarm banner, so the surrounding pixels differ between normal and
    # inverted renderings while the glyph shape does not.
    lit = [(r, c) for r in range(ph) for c in range(pw) if pattern[r][c]]
    dark = [(r, c) for r in range(ph) for c in range(pw) if not pattern[r][c]]

    for y in range(sh - ph + 1):
        for x in range(sw - pw + 1):
            if all(screen[y + r][x + c] for r, c in lit) and \
               all(not screen[y + r][x + c] for r, c in dark):
                return True
            # Same glyphs, inverted background (the alarm banner).
            if all(not screen[y + r][x + c] for r, c in lit) and \
               all(screen[y + r][x + c] for r, c in dark):
                return True
    return False
