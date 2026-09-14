#include "drivers/slf3x.h"

#include "board.h"
#include "pico/stdlib.h"

/* 16-bit commands, sent MSB first. */
#define CMD_START_WATER  0x3608
#define CMD_START_IPA    0x3615
#define CMD_STOP         0x3FF9
#define CMD_SOFT_RESET   0x0006  /* sent to the I2C general-call address */

/*
 * Scale factors for the SLF3S-1300F variant. Flow counts are 500 per mL/min,
 * so one count is 1e6/500 == 2000 nL/min; temperature is 200 counts per
 * degree, so one count is 5 millidegrees.
 */
#define NL_PER_MIN_PER_COUNT 2000
#define MILLIC_PER_COUNT     5

/* Sensirion CRC-8: polynomial 0x31, initialised to 0xFF, no final XOR. */
static uint8_t crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

static bool send_command(uint16_t cmd)
{
    uint8_t buf[2] = { (uint8_t)(cmd >> 8), (uint8_t)cmd };
    int n = i2c_write_timeout_us(BOARD_I2C, BOARD_FLOW_ADDR, buf, sizeof buf, false, 10000);
    return n == (int)sizeof buf;
}

slf3x_status_t slf3x_init(slf3x_fluid_t fluid)
{
    /*
     * Stop first. After a warm reset of the MCU alone the sensor may still be
     * streaming from the previous run, and it rejects a start command while
     * continuous measurement is already active.
     */
    (void)send_command(CMD_STOP);
    sleep_ms(5);

    if (!send_command(fluid == SLF3X_FLUID_IPA ? CMD_START_IPA : CMD_START_WATER)) {
        return SLF3X_ERR_BUS;
    }

    /* First conversion is not ready for ~12 ms after the start command. */
    sleep_ms(15);
    return SLF3X_OK;
}

slf3x_status_t slf3x_read(slf3x_reading_t *out)
{
    /* Three big-endian words, each followed by its own CRC byte. */
    uint8_t buf[9];
    int n = i2c_read_timeout_us(BOARD_I2C, BOARD_FLOW_ADDR, buf, sizeof buf, false, 10000);
    if (n != (int)sizeof buf) {
        return SLF3X_ERR_BUS;
    }

    uint16_t word[3];
    for (int i = 0; i < 3; i++) {
        const uint8_t *p = &buf[i * 3];
        if (crc8(p, 2) != p[2]) {
            return SLF3X_ERR_CRC;
        }
        word[i] = (uint16_t)((p[0] << 8) | p[1]);
    }

    out->nl_per_min   = (int32_t)(int16_t)word[0] * NL_PER_MIN_PER_COUNT;
    out->millicelsius = (int32_t)(int16_t)word[1] * MILLIC_PER_COUNT;
    out->flags        = word[2];
    out->air_in_line  = (word[2] & SLF3X_FLAG_AIR_IN_LINE) != 0;
    return SLF3X_OK;
}
