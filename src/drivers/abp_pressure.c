#include "drivers/abp_pressure.h"

#include "board.h"
#include "hardware/adc.h"

/*
 * Transfer function for the fitted part, an ABP...015PG: 0..15 psi gauge,
 * ratiometric analog output.
 *
 *   10% of full scale (409.5 counts of 4095) ..  0 psi
 *   90% of full scale (3685.5 counts)        .. 15 psi
 *
 * Honeywell reserve the tails for diagnostics: below 2.5% or above 97.5% the
 * output is not a pressure at all, it is a broken sensor or broken wiring.
 * Those limits are what ABP_COUNTS_MIN/MAX encode.
 */
#define ADC_FULL_SCALE      4095
#define ABP_COUNTS_ZERO     410    /* 10.0% */
#define ABP_COUNTS_SPAN     3276   /* 80.0% of full scale */
#define ABP_RANGE_MILLIPSI  15000

#define ABP_COUNTS_MIN      102    /* 2.5%  - below this the output is railed low */
#define ABP_COUNTS_MAX      3993   /* 97.5% - above this the output is railed high */

void abp_init(void)
{
    adc_init();
    adc_gpio_init(26 + BOARD_PRESSURE_ADC_CH);
}

abp_reading_t abp_read(void)
{
    adc_select_input(BOARD_PRESSURE_ADC_CH);
    uint16_t counts = adc_read() & ADC_FULL_SCALE;

    abp_reading_t r = {
        .raw_counts = counts,
        .valid = (counts >= ABP_COUNTS_MIN && counts <= ABP_COUNTS_MAX),
        .millipsi = 0,
    };

    if (r.valid) {
        /*
         * Below the 10% point the sensor is still working, it is just reading
         * under its zero - a real reading, and a negative one, so it is kept
         * signed rather than clamped. Clamping here would hide a zero-offset
         * calibration fault.
         */
        int32_t delta = (int32_t)counts - ABP_COUNTS_ZERO;
        r.millipsi = (delta * ABP_RANGE_MILLIPSI) / ABP_COUNTS_SPAN;
    }
    return r;
}
