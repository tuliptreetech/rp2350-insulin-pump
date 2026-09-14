/*
 * RP2350 insulin pump and monitor.
 *
 *   DRV8825 + lead screw  drives the syringe            (STEP/DIR on GP2/GP3)
 *   Honeywell ABP         watches line pressure         (ADC1 / GP27)
 *   Sensirion SLF3S-1300F watches actual flow           (I2C0 @ 0x08)
 *   SSD1306 128x64 OLED   shows the patient what is on  (I2C0 @ 0x3C)
 *
 * The loop is deliberately flat and single-threaded: sample every sensor,
 * let the safety layer judge the result, let the delivery engine act, draw.
 * The only thing that runs off a timer is the step pulse train, because that
 * is the one job with a hard timing requirement.
 */
#include <stdint.h>
#include <stdio.h>

#include "app/console.h"
#include "app/pump.h"
#include "app/safety.h"
#include "app/ui.h"
#include "board.h"
#include "drivers/abp_pressure.h"
#include "drivers/drv8825.h"
#include "drivers/slf3x.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "pico/stdlib.h"

#define CONTROL_PERIOD_US    50000u    /* 20 Hz: sensors, safety, delivery */
#define DISPLAY_PERIOD_US   500000u    /*  2 Hz: OLED redraw */
#define TELEMETRY_PERIOD_US 250000u    /*  4 Hz: serial telemetry */

static uint64_t earliest(uint64_t a, uint64_t b, uint64_t c)
{
    uint64_t m = (a < b) ? a : b;
    return (m < c) ? m : c;
}

static void bus_init(void)
{
    i2c_init(BOARD_I2C, BOARD_I2C_HZ);
    gpio_set_function(BOARD_I2C_SDA_PIN, GPIO_FUNC_I2C);
    gpio_set_function(BOARD_I2C_SCL_PIN, GPIO_FUNC_I2C);
    gpio_pull_up(BOARD_I2C_SDA_PIN);
    gpio_pull_up(BOARD_I2C_SCL_PIN);
}

static void sample_sensors(safety_inputs_t *in)
{
    in->motor_fault = drv8825_faulted();
    in->pressure = abp_read();
    in->flow_status = slf3x_read(&in->flow);
    if (in->flow_status != SLF3X_OK) {
        /* Leave no stale numbers behind for the UI or telemetry to show. */
        in->flow.nl_per_min = 0;
        in->flow.air_in_line = false;
    }
}

int main(void)
{
    /*
     * Drop to the pump's working clock before anything else. This has to
     * happen before stdio_init_all(), because clk_peri follows clk_sys and the
     * UART divisor is computed from it at init.
     *
     * Not fatal if it is refused: the SDK leaves the default clock running,
     * which is faster than asked for and so still meets every deadline. The
     * rate is reported at boot rather than assumed.
     */
    bool clock_set = set_sys_clock_khz(BOARD_SYS_CLOCK_KHZ, false);

    stdio_init_all();

    /*
     * Unbuffered stdout. The console echoes with putchar(), which pico_stdio
     * sends straight to the UART, while printf() and fputs() go through
     * newlib's buffer and only drain on a newline. Mixing the two reorders
     * the stream: the prompt, which has no trailing newline, sat in the
     * buffer and surfaced in the middle of the next line the operator typed.
     */
    setvbuf(stdout, NULL, _IONBF, 0);

    printf("\nclk_sys=%lu Hz%s\n", (unsigned long)clock_get_hz(clk_sys),
           clock_set ? "" : " (requested rate refused, running at default)");

    bus_init();
    abp_init();
    drv8825_init();
    pump_init();
    safety_init();

    bool display_ok = ui_init();
    if (!display_ok) {
        printf("warn OLED did not respond\n");
    }

    /* Insulin is close enough to water for the sensor's water calibration. */
    if (slf3x_init(SLF3X_FLUID_WATER) != SLF3X_OK) {
        printf("warn flow sensor did not respond\n");
    }

    console_init();
    sleep_ms(300);  /* let the splash screen be readable */

    safety_inputs_t in = {0};
    uint64_t next_control = 0, next_telemetry = 0;
    /*
     * A panel that never answered is scheduled at infinity rather than left
     * at zero: a deadline permanently in the past would make the idle
     * computation below pick it every time and turn the loop into a spin.
     */
    uint64_t next_display = display_ok ? 0 : UINT64_MAX;

    for (;;) {
        uint64_t now = time_us_64();

        console_poll();

        if (now >= next_control) {
            next_control = now + CONTROL_PERIOD_US;
            sample_sensors(&in);
            safety_update(now, &in);
            pump_tick(now);
        }

        if (now >= next_display) {
            next_display = now + DISPLAY_PERIOD_US;
            ui_render(&in);
        }

        if (now >= next_telemetry) {
            next_telemetry = now + TELEMETRY_PERIOD_US;
            console_telemetry(&in);
        }

        /*
         * Idle until the next deadline rather than spinning on time_us_64().
         * On a battery-powered pump that is the difference between days and
         * hours of run time, and it is also what lets the step timer keep its
         * schedule without competing with a busy main loop.
         *
         * Console input is only looked at on a wake, so a typed command is
         * acted on within one control period - 50 ms, imperceptible to a user
         * and irrelevant to delivery.
         */
        uint64_t next = earliest(next_control, next_display, next_telemetry);
        if (next > time_us_64()) {
            sleep_until(from_us_since_boot(next));
        }
    }
}
