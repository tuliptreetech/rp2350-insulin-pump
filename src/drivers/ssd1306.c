#include "drivers/ssd1306.h"

#include <string.h>

#include "board.h"
#include "drivers/font5x7.h"
#include "pico/stdlib.h"

#define CTRL_CMD   0x00
#define CTRL_DATA  0x40

/*
 * One byte per 8-pixel vertical column slice: framebuffer[page * WIDTH + x]
 * holds rows page*8 .. page*8+7 of column x, LSB topmost. That is exactly the
 * panel's own GDDRAM layout, so a flush is a straight memcpy onto the bus.
 */
static uint8_t s_fb[SSD1306_WIDTH * (SSD1306_HEIGHT / 8)];

/*
 * Send a run of command bytes as one transaction: a single 0x00 control byte
 * followed by the commands themselves. Multi-byte commands (contrast,
 * addressing mode, the charge pump) must reach the controller inside one
 * transaction, so splitting the sequence one byte at a time silently drops
 * every argument and leaves the panel on its power-on defaults.
 */
#define CMD_CHUNK 24

static bool write_cmds(const uint8_t *cmds, size_t n)
{
    uint8_t buf[1 + CMD_CHUNK];
    while (n > 0) {
        size_t chunk = (n > CMD_CHUNK) ? CMD_CHUNK : n;
        buf[0] = CTRL_CMD;
        memcpy(&buf[1], cmds, chunk);
        if (i2c_write_timeout_us(BOARD_I2C, BOARD_OLED_ADDR, buf, chunk + 1, false, 10000)
            != (int)(chunk + 1)) {
            return false;
        }
        cmds += chunk;
        n -= chunk;
    }
    return true;
}

bool ssd1306_init(void)
{
    static const uint8_t init_seq[] = {
        0xAE,             /* display off while we reconfigure */
        0xD5, 0x80,       /* clock: default divide, suggested oscillator */
        0xA8, 0x3F,       /* multiplex ratio: 64 rows */
        0xD3, 0x00,       /* no display offset */
        0x40,             /* start line 0 */
        0x8D, 0x14,       /* charge pump on - panel has no external Vcc */
        0x20, 0x00,       /* horizontal addressing: a flush is one long write */
        0xA1,             /* segment remap, module is wired mirrored */
        0xC8,             /* COM scan direction reversed, ditto */
        0xDA, 0x12,       /* alternate COM pin config for a 128x64 panel */
        0x81, 0xCF,       /* contrast */
        0xD9, 0xF1,       /* pre-charge period */
        0xDB, 0x40,       /* VCOMH deselect level */
        0xA4,             /* follow GDDRAM, not all-on */
        0xA6,             /* non-inverted */
        0x2E,             /* scrolling off */
        0xAF,             /* display on */
    };

    if (!write_cmds(init_seq, sizeof init_seq)) {
        return false;
    }
    ssd1306_clear();
    return ssd1306_flush();
}

void ssd1306_clear(void)
{
    memset(s_fb, 0, sizeof s_fb);
}

void ssd1306_set_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= SSD1306_WIDTH || y < 0 || y >= SSD1306_HEIGHT) {
        return;
    }
    uint8_t *cell = &s_fb[(y / 8) * SSD1306_WIDTH + x];
    uint8_t bit = (uint8_t)(1u << (y % 8));
    if (on) {
        *cell |= bit;
    } else {
        *cell &= (uint8_t)~bit;
    }
}

void ssd1306_fill_rect(int x, int y, int w, int h, bool on)
{
    for (int yy = y; yy < y + h; yy++) {
        for (int xx = x; xx < x + w; xx++) {
            ssd1306_set_pixel(xx, yy, on);
        }
    }
}

void ssd1306_invert_rect(int x, int y, int w, int h)
{
    for (int yy = y; yy < y + h; yy++) {
        for (int xx = x; xx < x + w; xx++) {
            if (xx < 0 || xx >= SSD1306_WIDTH || yy < 0 || yy >= SSD1306_HEIGHT) {
                continue;
            }
            s_fb[(yy / 8) * SSD1306_WIDTH + xx] ^= (uint8_t)(1u << (yy % 8));
        }
    }
}

void ssd1306_text(int x, int y, const char *s, int scale)
{
    if (scale < 1) scale = 1;

    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < FONT_FIRST_CHAR || c > FONT_LAST_CHAR) {
            c = '?';
        }
        const uint8_t *glyph = &font5x7[(c - FONT_FIRST_CHAR) * FONT_WIDTH];

        for (int col = 0; col < FONT_WIDTH; col++) {
            uint8_t bits = glyph[col];
            for (int row = 0; row < 8; row++) {
                if (!(bits & (1u << row))) {
                    continue;
                }
                if (scale == 1) {
                    ssd1306_set_pixel(x + col, y + row, true);
                } else {
                    ssd1306_fill_rect(x + col * scale, y + row * scale, scale, scale, true);
                }
            }
        }
        x += FONT_ADVANCE * scale;
    }
}

void ssd1306_text_at(int col, int row, const char *s)
{
    ssd1306_text(col * FONT_ADVANCE, row * 8, s, 1);
}

bool ssd1306_flush(void)
{
    static const uint8_t window[] = {
        0x21, 0, SSD1306_WIDTH - 1,          /* column address range */
        0x22, 0, (SSD1306_HEIGHT / 8) - 1,   /* page address range */
    };
    if (!write_cmds(window, sizeof window)) {
        return false;
    }

    /*
     * Send a page at a time. One 1025-byte transfer would work too, but a
     * page-sized buffer keeps the stack cost to 129 bytes and bounds how long
     * the flow sensor has to wait for the bus.
     */
    for (int page = 0; page < SSD1306_HEIGHT / 8; page++) {
        uint8_t buf[1 + SSD1306_WIDTH];
        buf[0] = CTRL_DATA;
        memcpy(&buf[1], &s_fb[page * SSD1306_WIDTH], SSD1306_WIDTH);
        if (i2c_write_timeout_us(BOARD_I2C, BOARD_OLED_ADDR, buf, sizeof buf, false, 50000)
            != (int)sizeof buf) {
            return false;
        }
    }
    return true;
}
