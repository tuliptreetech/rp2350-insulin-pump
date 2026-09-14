/*
 * pump.h - Insulin delivery engine.
 *
 * Everything here counts in milliunits (mU) of U-100 insulin: 1000 mU = 1 U.
 * One microstep of the lead screw is exactly 5 mU, so a dose is always a whole
 * number of microsteps and delivery accounting never accumulates rounding
 * error. Rates are milliunits per hour.
 *
 * The engine only ever *commands* delivery. Whether the insulin actually
 * moved is the safety layer's question, answered with the flow and pressure
 * sensors, and the safety layer stops the engine through pump_suspend().
 */
#ifndef PUMP_H
#define PUMP_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    PUMP_STOPPED = 0,  /* idle at the user's request; no delivery of any kind */
    PUMP_RUNNING,      /* basal delivery active */
    PUMP_SUSPENDED,    /* stopped by the safety layer, not by the user */
} pump_mode_t;

typedef enum {
    PUMP_DOSE_OK = 0,
    PUMP_DOSE_BUSY,           /* a bolus is already running */
    PUMP_DOSE_TOO_LARGE,      /* over the single-bolus limit */
    PUMP_DOSE_OVER_HOUR_LIMIT,/* would breach the rolling one-hour cap */
    PUMP_DOSE_NO_RESERVOIR,   /* not enough insulin left */
    PUMP_DOSE_NOT_RUNNING,    /* suspended or stopped */
} pump_dose_result_t;

typedef struct {
    pump_mode_t mode;
    uint32_t basal_mu_per_hr;
    uint32_t bolus_total_mu;      /* size of the bolus in progress, 0 if none */
    uint32_t bolus_delivered_mu;
    uint32_t reservoir_mu;
    uint64_t delivered_total_mu;  /* since power-on */
    uint32_t delivered_hour_mu;   /* rolling 60-minute window */
} pump_status_t;

/* ---- Safety limits ----------------------------------------------------- */
#define PUMP_MAX_BASAL_MU_PER_HR  5000u   /*  5.000 U/hr */
#define PUMP_MAX_BOLUS_MU         25000u  /* 25.000 U in one bolus */
#define PUMP_MAX_HOUR_MU          30000u  /* 30.000 U in any rolling hour */
#define PUMP_RESERVOIR_LOW_MU     20000u  /* 20 U left: warn the user */

void pump_init(void);

/*
 * Advance the engine. Call from the main loop; `now_us` is a free-running
 * microsecond clock. Reconciles microsteps the driver has actually emitted
 * into the delivery totals, then schedules the next basal microsteps.
 */
void pump_tick(uint64_t now_us);

bool pump_set_basal(uint32_t mu_per_hr);
pump_dose_result_t pump_start_bolus(uint32_t mu);
void pump_cancel_bolus(void);

/* User-facing run/stop. */
void pump_start(void);
void pump_stop(void);

/* Safety-facing stop. Aborts queued microsteps and any bolus in progress. */
void pump_suspend(void);
/* Leave PUMP_SUSPENDED. Refused unless the caller has cleared the alarms. */
void pump_resume(void);

void pump_get_status(pump_status_t *out);
bool pump_bolus_active(void);

/* Nanolitres the mechanism has been commanded to deliver since power-on. */
uint64_t pump_commanded_nl(void);

/* Fit a fresh cartridge: reservoir back to full. */
void pump_replace_reservoir(void);

const char *pump_dose_result_str(pump_dose_result_t r);

#endif /* PUMP_H */
