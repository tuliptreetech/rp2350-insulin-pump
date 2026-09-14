/*
 * ssd1306.h - 128x64 monochrome OLED over I2C.
 *
 * Drawing goes into a RAM framebuffer and reaches the panel only on
 * ssd1306_flush(). That keeps the I2C bus free between redraws, which matters
 * because the flow sensor shares it and the pump would rather miss a frame
 * than miss a measurement.
 */
#ifndef SSD1306_H
#define SSD1306_H

#include <stdbool.h>
#include <stdint.h>

#define SSD1306_WIDTH   128
#define SSD1306_HEIGHT  64
#define SSD1306_CHAR_W  6                     /* 5x7 glyph plus one blank column */
#define SSD1306_COLS    (SSD1306_WIDTH / SSD1306_CHAR_W)   /* characters per line */
#define SSD1306_ROWS    (SSD1306_HEIGHT / 8)  /* text lines */

bool ssd1306_init(void);

void ssd1306_clear(void);
void ssd1306_set_pixel(int x, int y, bool on);
void ssd1306_fill_rect(int x, int y, int w, int h, bool on);

/* Draw text at a pixel position. `scale` of 1 is 5x7; 2 doubles both axes. */
void ssd1306_text(int x, int y, const char *s, int scale);

/* Draw text in character cells: col 0..20, row 0..7. */
void ssd1306_text_at(int col, int row, const char *s);

/* Flip every pixel in a rectangle - used for the inverted status bar. */
void ssd1306_invert_rect(int x, int y, int w, int h);

/* Push the framebuffer to the panel. Returns false if the panel did not ACK. */
bool ssd1306_flush(void);

#endif /* SSD1306_H */
