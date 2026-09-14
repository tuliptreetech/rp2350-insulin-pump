/*
 * slf3x.h - Sensirion SLF3S-1300F liquid flow sensor, inline on the tubing.
 *
 * This is the pump's independent witness: the stepper reports what was
 * commanded, the flow sensor reports what actually moved. Every measurement
 * is CRC-protected on the wire and the driver refuses to hand up a word whose
 * CRC does not check, so line noise degrades to "no reading" rather than to a
 * plausible wrong one.
 */
#ifndef SLF3X_H
#define SLF3X_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    SLF3X_OK = 0,
    SLF3X_ERR_BUS,   /* NACK or short read - sensor missing or wiring broken */
    SLF3X_ERR_CRC,   /* sensor answered, but the data is corrupt */
} slf3x_status_t;

/* Calibration field the sensor applies to raw counts. */
typedef enum {
    SLF3X_FLUID_WATER = 0,
    SLF3X_FLUID_IPA   = 1,
} slf3x_fluid_t;

typedef struct {
    int32_t  nl_per_min;   /* flow, nanolitres per minute */
    int32_t  millicelsius; /* fluid temperature */
    uint16_t flags;        /* raw signalling flags, see SLF3X_FLAG_* */
    bool     air_in_line;  /* decoded from flags, the one the pump acts on */
} slf3x_reading_t;

#define SLF3X_FLAG_AIR_IN_LINE  (1u << 0)
#define SLF3X_FLAG_HIGH_FLOW    (1u << 1)

/*
 * Reset the sensor and start continuous measurement with the given
 * calibration. Insulin is close enough to water for the water field; the IPA
 * field exists because the line is flushed with alcohol during service.
 */
slf3x_status_t slf3x_init(slf3x_fluid_t fluid);

/* Read the newest measurement. Continuous mode must already be running. */
slf3x_status_t slf3x_read(slf3x_reading_t *out);

#endif /* SLF3X_H */
