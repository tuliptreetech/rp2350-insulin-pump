#include "drivers/drv8825.h"

#include "board.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

/*
 * Bounds on the pulse rate. The low end is one microstep every 100 ms, the
 * slowest rate worth running the timer at; the high end keeps the mechanism
 * inside the lead screw's slew limit, and also bounds how much insulin a
 * single runaway second could deliver (2000 microsteps == 10 U).
 */
#define RATE_MIN_SPS 10u
#define RATE_MAX_SPS 2000u

/* DRV8825 needs the STEP line held for at least 1.9 us to latch a step. */
#define STEP_PULSE_US 2

/*
 * Which DIR level advances the plunger. This is a property of the board - how
 * the motor phases are wired and which way the lead screw is cut - not of the
 * driver chip, so it is named rather than assumed. Getting it backwards makes
 * a bolus retract the syringe instead of delivering it.
 */
#define DIR_LEVEL_FORWARD 1

static inline bool dir_level(drv8825_dir_t dir)
{
    return (dir == DRV8825_FORWARD) ? DIR_LEVEL_FORWARD : !DIR_LEVEL_FORWARD;
}

static struct repeating_timer s_timer;

/*
 * Shared between the step timer callback and the main loop. Single core, so
 * disabling interrupts on the main-loop side is enough to exclude the
 * callback entirely - and it is required, not merely tidy: if the callback
 * were to run its `s_pending--` against a zero the main loop had just
 * written, the count would wrap to 4 billion queued microsteps. That is an
 * unbounded overdose, so every main-loop write to s_pending is fenced.
 */
static volatile uint32_t s_pending;
static volatile int64_t  s_net_microsteps;
static volatile drv8825_dir_t s_dir = DRV8825_FORWARD;
static uint32_t s_rate_sps = 200;

/*
 * Set if the step timer could not be armed. Without that timer no microstep
 * is ever pulsed, so the pump would quietly deliver nothing - the worst
 * failure mode available to it, because it looks exactly like working. It is
 * reported through drv8825_faulted() so the safety layer treats it the same
 * as the driver chip pulling nFAULT.
 */
static bool s_timer_failed;

static bool step_timer_cb(struct repeating_timer *t)
{
    (void)t;
    if (s_pending == 0) {
        return true;
    }

    gpio_put(BOARD_STEP_PIN, 1);
    busy_wait_us(STEP_PULSE_US);
    gpio_put(BOARD_STEP_PIN, 0);

    s_pending--;
    s_net_microsteps += (s_dir == DRV8825_FORWARD) ? 1 : -1;
    return true;
}

static void restart_timer(void)
{
    cancel_repeating_timer(&s_timer);
    /* Negative period: measured between callback starts, not end-to-start. */
    if (!add_repeating_timer_us(-(int64_t)(1000000u / s_rate_sps),
                                step_timer_cb, NULL, &s_timer)) {
        s_timer_failed = true;
        s_pending = 0;
    }
}

void drv8825_init(void)
{
    gpio_init(BOARD_STEP_PIN);
    gpio_set_dir(BOARD_STEP_PIN, GPIO_OUT);
    gpio_put(BOARD_STEP_PIN, 0);

    gpio_init(BOARD_DIR_PIN);
    gpio_set_dir(BOARD_DIR_PIN, GPIO_OUT);
    gpio_put(BOARD_DIR_PIN, dir_level(DRV8825_FORWARD));

    /* nFAULT is open-drain on the driver, so the pull-up is ours to provide. */
    gpio_init(BOARD_NFAULT_PIN);
    gpio_set_dir(BOARD_NFAULT_PIN, GPIO_IN);
    gpio_pull_up(BOARD_NFAULT_PIN);

    s_pending = 0;
    s_net_microsteps = 0;
    s_timer_failed = false;
    restart_timer();
}

void drv8825_set_rate(uint32_t microsteps_per_second)
{
    if (microsteps_per_second < RATE_MIN_SPS) microsteps_per_second = RATE_MIN_SPS;
    if (microsteps_per_second > RATE_MAX_SPS) microsteps_per_second = RATE_MAX_SPS;
    if (microsteps_per_second == s_rate_sps) {
        return;
    }
    s_rate_sps = microsteps_per_second;
    restart_timer();
}

void drv8825_move(drv8825_dir_t dir, uint32_t microsteps)
{
    if (microsteps == 0) {
        return;
    }

    uint32_t save = save_and_disable_interrupts();
    if (dir != s_dir) {
        /*
         * A reversal drops whatever was still queued. Mixing directions in
         * one queue would make the pending count meaningless, and the only
         * caller that reverses (priming) wants the forward queue gone anyway.
         */
        s_pending = 0;
        s_dir = dir;
        gpio_put(BOARD_DIR_PIN, dir_level(dir));
        /* DRV8825 samples DIR on the STEP edge; give it setup time. */
        busy_wait_us(2);
    }
    s_pending += microsteps;
    restore_interrupts(save);
}

uint32_t drv8825_pending(void)
{
    return s_pending;
}

void drv8825_abort(void)
{
    uint32_t save = save_and_disable_interrupts();
    s_pending = 0;
    restore_interrupts(save);
}

int64_t drv8825_net_microsteps(void)
{
    /*
     * 64-bit on a 32-bit core, so the two halves are read separately and the
     * callback could land between them. This counter is the basis of every
     * delivery total, so it is worth the few cycles to read it whole.
     */
    uint32_t save = save_and_disable_interrupts();
    int64_t net = s_net_microsteps;
    restore_interrupts(save);
    return net;
}

bool drv8825_faulted(void)
{
    return s_timer_failed || gpio_get(BOARD_NFAULT_PIN) == 0;  /* nFAULT is active low */
}
