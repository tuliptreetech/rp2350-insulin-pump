#include "app/pump.h"

#include <string.h>

#include "board.h"
#include "drivers/drv8825.h"

/*
 * Basal scheduling is done entirely in integers. The accumulator holds
 * milliunit-milliseconds: each tick adds rate_mU_per_hr * dt_ms, and one
 * microstep is emitted for every 5 mU * 3,600,000 ms of accumulated demand.
 * Nothing is ever rounded away, so a basal rate that is not a whole number of
 * microsteps per hour still delivers exactly right over a long enough run.
 */
#define MU_MS_PER_MICROSTEP  ((uint64_t)BOARD_MU_PER_MICROSTEP * 3600000u)

/* Step rates. Basal is one microstep at a time, so it only needs to be quick
 * enough not to smear the dose; a bolus wants to be over in a sensible time. */
#define BASAL_STEP_RATE_SPS  50u
#define BOLUS_STEP_RATE_SPS  200u   /* 200 microsteps/s == 1.0 U/s */

/* The rolling one-hour cap is tracked in 60 one-minute buckets. */
#define HOUR_BUCKETS  60
#define BUCKET_US     ((uint64_t)60 * 1000 * 1000)

static struct {
    pump_mode_t mode;
    uint32_t basal_mu_per_hr;

    uint32_t bolus_total_mu;
    uint32_t bolus_delivered_mu;

    uint32_t reservoir_mu;
    uint64_t delivered_total_mu;

    uint64_t basal_acc;          /* milliunit-milliseconds, see above */
    uint64_t last_tick_us;
    int64_t  last_net_microsteps;

    uint32_t hour[HOUR_BUCKETS];
    int      hour_idx;
    uint64_t hour_bucket_started_us;
} s;

static uint32_t hour_total_mu(void)
{
    uint32_t total = 0;
    for (int i = 0; i < HOUR_BUCKETS; i++) {
        total += s.hour[i];
    }
    return total;
}

static void hour_advance(uint64_t now_us)
{
    while (now_us - s.hour_bucket_started_us >= BUCKET_US) {
        s.hour_bucket_started_us += BUCKET_US;
        s.hour_idx = (s.hour_idx + 1) % HOUR_BUCKETS;
        s.hour[s.hour_idx] = 0;
    }
}

void pump_init(void)
{
    memset(&s, 0, sizeof s);
    s.mode = PUMP_STOPPED;
    s.reservoir_mu = BOARD_RESERVOIR_FULL_MU;
    s.basal_mu_per_hr = 1000;  /* 1.000 U/hr until the user says otherwise */
    s.last_net_microsteps = drv8825_net_microsteps();
    drv8825_set_rate(BASAL_STEP_RATE_SPS);
}

/*
 * Fold microsteps the driver has actually emitted since the last tick into the
 * delivery totals. Reading the driver rather than trusting what we queued is
 * what makes an aborted dose account correctly: microsteps discarded by
 * drv8825_abort() were never pulsed, so they are never billed to the patient.
 */
static void reconcile_delivered(uint64_t now_us)
{
    int64_t net = drv8825_net_microsteps();
    int64_t delta = net - s.last_net_microsteps;
    s.last_net_microsteps = net;

    if (delta <= 0) {
        return;  /* rewind during priming - not delivery */
    }

    uint32_t mu = (uint32_t)delta * BOARD_MU_PER_MICROSTEP;

    s.delivered_total_mu += mu;
    s.reservoir_mu = (s.reservoir_mu > mu) ? (s.reservoir_mu - mu) : 0;

    hour_advance(now_us);
    s.hour[s.hour_idx] += mu;

    /*
     * Basal is held off for the duration of a bolus (see pump_tick), so while
     * one is running every forward microstep the driver emits belongs to it.
     */
    if (s.bolus_total_mu > 0) {
        s.bolus_delivered_mu += mu;
        if (s.bolus_delivered_mu >= s.bolus_total_mu) {
            s.bolus_total_mu = 0;
            s.bolus_delivered_mu = 0;
            drv8825_set_rate(BASAL_STEP_RATE_SPS);
        }
    }
}

void pump_tick(uint64_t now_us)
{
    if (s.last_tick_us == 0) {
        s.last_tick_us = now_us;
        s.hour_bucket_started_us = now_us;
        return;
    }

    uint64_t dt_ms = (now_us - s.last_tick_us) / 1000u;
    if (dt_ms == 0) {
        return;  /* keep last_tick_us so the remainder is not thrown away */
    }
    s.last_tick_us += dt_ms * 1000u;

    reconcile_delivered(now_us);
    hour_advance(now_us);

    if (s.mode != PUMP_RUNNING) {
        s.basal_acc = 0;  /* time spent stopped is not owed to the patient */
        return;
    }

    /* Basal demand for the elapsed interval. */
    s.basal_acc += (uint64_t)s.basal_mu_per_hr * dt_ms;

    /*
     * One mechanism, so basal and bolus cannot both drive it. Basal demand
     * keeps accruing in the accumulator during a bolus and is paid out as
     * soon as the bolus finishes - held back, not dropped.
     */
    if (s.bolus_total_mu > 0) {
        return;
    }

    uint32_t microsteps = 0;
    while (s.basal_acc >= MU_MS_PER_MICROSTEP) {
        s.basal_acc -= MU_MS_PER_MICROSTEP;
        microsteps++;
    }
    if (microsteps == 0) {
        return;
    }

    uint32_t mu = microsteps * BOARD_MU_PER_MICROSTEP;

    /*
     * Two hard gates sit in front of every commanded microstep, basal
     * included. An empty reservoir and a breached hourly cap are both
     * conditions where the safe action is to stop rather than to deliver a
     * partial dose and carry on.
     */
    if (mu > s.reservoir_mu) {
        pump_suspend();
        return;
    }
    if (hour_total_mu() + mu > PUMP_MAX_HOUR_MU) {
        pump_suspend();
        return;
    }

    drv8825_move(DRV8825_FORWARD, microsteps);
}

bool pump_set_basal(uint32_t mu_per_hr)
{
    if (mu_per_hr > PUMP_MAX_BASAL_MU_PER_HR) {
        return false;
    }
    s.basal_mu_per_hr = mu_per_hr;
    return true;
}

pump_dose_result_t pump_start_bolus(uint32_t mu)
{
    if (s.mode != PUMP_RUNNING) {
        return PUMP_DOSE_NOT_RUNNING;
    }
    if (s.bolus_total_mu > 0) {
        return PUMP_DOSE_BUSY;
    }
    if (mu == 0 || mu > PUMP_MAX_BOLUS_MU) {
        return PUMP_DOSE_TOO_LARGE;
    }
    if (mu > s.reservoir_mu) {
        return PUMP_DOSE_NO_RESERVOIR;
    }
    if (hour_total_mu() + mu > PUMP_MAX_HOUR_MU) {
        return PUMP_DOSE_OVER_HOUR_LIMIT;
    }

    /* Round down to a whole microstep: never deliver more than was asked. */
    uint32_t microsteps = mu / BOARD_MU_PER_MICROSTEP;
    if (microsteps == 0) {
        return PUMP_DOSE_TOO_LARGE;
    }

    s.bolus_total_mu = microsteps * BOARD_MU_PER_MICROSTEP;
    s.bolus_delivered_mu = 0;

    drv8825_set_rate(BOLUS_STEP_RATE_SPS);
    drv8825_move(DRV8825_FORWARD, microsteps);
    return PUMP_DOSE_OK;
}

void pump_cancel_bolus(void)
{
    if (s.bolus_total_mu == 0) {
        return;
    }
    drv8825_abort();
    s.bolus_total_mu = 0;
    s.bolus_delivered_mu = 0;
    drv8825_set_rate(BASAL_STEP_RATE_SPS);
}

void pump_start(void)
{
    if (s.mode == PUMP_SUSPENDED) {
        return;  /* only pump_resume() may leave a safety suspend */
    }
    s.mode = PUMP_RUNNING;
    s.basal_acc = 0;
}

void pump_stop(void)
{
    pump_cancel_bolus();
    s.mode = PUMP_STOPPED;
}

void pump_suspend(void)
{
    drv8825_abort();
    s.bolus_total_mu = 0;
    s.bolus_delivered_mu = 0;
    drv8825_set_rate(BASAL_STEP_RATE_SPS);
    s.mode = PUMP_SUSPENDED;
    s.basal_acc = 0;
}

void pump_resume(void)
{
    if (s.mode != PUMP_SUSPENDED) {
        return;
    }
    s.mode = PUMP_RUNNING;
    s.basal_acc = 0;
}

void pump_get_status(pump_status_t *out)
{
    out->mode = s.mode;
    out->basal_mu_per_hr = s.basal_mu_per_hr;
    out->bolus_total_mu = s.bolus_total_mu;
    out->bolus_delivered_mu = s.bolus_delivered_mu;
    out->reservoir_mu = s.reservoir_mu;
    out->delivered_total_mu = s.delivered_total_mu;
    out->delivered_hour_mu = hour_total_mu();
}

bool pump_bolus_active(void)
{
    return s.bolus_total_mu > 0;
}

uint64_t pump_commanded_nl(void)
{
    int64_t net = drv8825_net_microsteps();
    if (net < 0) {
        net = 0;
    }
    return (uint64_t)net * BOARD_NL_PER_MICROSTEP;
}

void pump_replace_reservoir(void)
{
    s.reservoir_mu = BOARD_RESERVOIR_FULL_MU;
}

const char *pump_dose_result_str(pump_dose_result_t r)
{
    switch (r) {
    case PUMP_DOSE_OK:              return "ok";
    case PUMP_DOSE_BUSY:            return "bolus already running";
    case PUMP_DOSE_TOO_LARGE:       return "dose out of range";
    case PUMP_DOSE_OVER_HOUR_LIMIT: return "would exceed hourly limit";
    case PUMP_DOSE_NO_RESERVOIR:    return "not enough insulin left";
    case PUMP_DOSE_NOT_RUNNING:     return "pump is not running";
    }
    return "?";
}
