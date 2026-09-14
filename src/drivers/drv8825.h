/*
 * drv8825.h - STEP/DIR microstepping driver for the syringe lead screw.
 *
 * The driver owns the pulse train and nothing else: it knows how to emit
 * microsteps at a requested rate and how to report the driver chip's own
 * fault line. Dose accounting lives in app/pump.c.
 */
#ifndef DRV8825_H
#define DRV8825_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    DRV8825_FORWARD = 0,  /* plunger advances - fluid is delivered */
    DRV8825_REVERSE = 1,  /* plunger retracts - priming/rewind only */
} drv8825_dir_t;

/* Configure the pins and start the step-pulse timer. */
void drv8825_init(void);

/*
 * Queue `microsteps` further microsteps in `dir`. Steps are emitted by a
 * timer callback at the rate set by drv8825_set_rate(), so this returns
 * immediately. Queueing in the opposite direction to the steps still
 * outstanding cancels them rather than mixing the two.
 */
void drv8825_move(drv8825_dir_t dir, uint32_t microsteps);

/* Microsteps per second for subsequently emitted steps. Clamped to a sane range. */
void drv8825_set_rate(uint32_t microsteps_per_second);

/* Microsteps still queued. Zero means the mechanism is at rest. */
uint32_t drv8825_pending(void);

/*
 * Discard every queued microstep immediately. Used by the safety layer to
 * stop delivery mid-dose; steps already emitted are not undone.
 */
void drv8825_abort(void);

/*
 * Total microsteps this driver has actually pulsed out, net of direction.
 * This is what the firmware *commanded*; the flow sensor is the independent
 * check on what the mechanism really moved.
 */
int64_t drv8825_net_microsteps(void);

/* True while nFAULT is asserted (driver reports overcurrent or thermal). */
bool drv8825_faulted(void);

#endif /* DRV8825_H */
