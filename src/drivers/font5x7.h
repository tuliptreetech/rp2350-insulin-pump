/*
 * font5x7.h - Classic 5x7 bitmap font, printable ASCII 0x20..0x7E.
 *
 * Each glyph is five column bytes; bit 0 is the top row. Glyphs are drawn
 * with a one-pixel gap to the right, so the cell is 6x8 and a 128x64 panel
 * holds 21 columns by 8 rows of text.
 */
#ifndef FONT5X7_H
#define FONT5X7_H

#include <stdint.h>

#define FONT_FIRST_CHAR 0x20
#define FONT_LAST_CHAR  0x7E
#define FONT_WIDTH      5
#define FONT_ADVANCE    6

extern const uint8_t font5x7[(FONT_LAST_CHAR - FONT_FIRST_CHAR + 1) * FONT_WIDTH];

#endif /* FONT5X7_H */
