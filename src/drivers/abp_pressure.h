/*
 * abp_pressure.h - Honeywell ABP line-pressure sensor on an ADC input.
 *
 * The part is ratiometric: it swings 10%..90% of its supply across its rated
 * range, and the bands outside that are reserved so a broken output can be
 * told apart from a real reading. This driver reports millipsi plus an
 * explicit validity flag rather than a bare number, so a railed output can
 * never be mistaken for "0 psi".
 */
#ifndef ABP_PRESSURE_H
#define ABP_PRESSURE_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    int32_t  millipsi;   /* only meaningful when valid */
    uint16_t raw_counts; /* 12-bit ADC code, always populated */
    bool     valid;      /* false when the output is outside the diagnostic band */
} abp_reading_t;

void abp_init(void);

/* Take one sample. Cheap enough to call from the main loop at 10 Hz. */
abp_reading_t abp_read(void);

#endif /* ABP_PRESSURE_H */
