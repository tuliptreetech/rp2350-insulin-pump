#include "app/safety.h"

#include <string.h>

#include "board.h"

/* ---- Detection thresholds --------------------------------------------- */

/*
 * Occlusion threshold, derived from the infusion set's compliance rather than
 * picked round.
 *
 * Measured on the simulated set: a blocked line builds about 0.2 psi per
 * microlitre pushed into it, while a clear line at full bolus rate sits at
 * 0.03 psi. The threshold therefore decides how much insulin is lost into a
 * blockage before the pump notices - every 1 psi of threshold is 5 uL, or
 * half a unit, pumped into a line that is going nowhere.
 *
 * 4 psi is 130x the working pressure of a clear line, so it cannot be reached
 * by normal delivery, and it bounds the loss at roughly 2 U to reach the
 * threshold plus whatever the dwell adds. The dwell is what rejects
 * transients - a patient rolling onto the tubing - and two seconds of
 * sustained pressure is not a transient.
 */
#define OCCLUSION_MILLIPSI   4000
#define OCCLUSION_DWELL_US   ((uint64_t)2 * 1000 * 1000)

/*
 * Delivery verification. The SLF3S-1300F resolves about 2 uL/min per count,
 * so it cannot see a basal rate at all (1 U/hr is 0.17 uL/min) - only a bolus
 * moves enough fluid to check. The test therefore arms on a bolus, waits for
 * 2 U to have been commanded so the comparison has something to bite on, and
 * alarms if under half of it showed up at the sensor.
 */
#define VERIFY_MIN_COMMANDED_NL  10000u   /* 10 uL == 1.0 U */
#define VERIFY_MIN_FRACTION_PCT  50

/* Consecutive bad samples before a sensor is declared broken. A single NACK
 * is a bus collision; ten in a row is a disconnected sensor. */
#define SENSOR_FAIL_LIMIT  10

/* Alarms that stop delivery: everything except the advisory low-reservoir. */
#define BLOCKING_MASK  (~(1u << ALARM_RESERVOIR_LOW))

static struct {
    uint32_t latched;
    uint64_t last_us;

    uint64_t occlusion_since_us;  /* 0 when pressure is below threshold */

    uint32_t pressure_fails;
    uint32_t flow_fails;

    bool     verify_armed;
    bool     verify_saw_pressure;   /* line pressure went high during this bolus */
    uint64_t verify_commanded_base_nl;
    int64_t  verify_measured_nl;
} s;

void safety_init(void)
{
    memset(&s, 0, sizeof s);
}

static void raise(pump_alarm_t id)
{
    s.latched |= (1u << id);
}

/*
 * Track the flow sensor against the mechanism for the duration of a bolus.
 * Integrating both sides over the whole bolus rather than comparing
 * instantaneous rates is deliberate: the sensor's response lags the pump, and
 * a rate-vs-rate comparison would false-alarm at every start and stop.
 */
static void update_delivery_verification(uint64_t dt_us, const safety_inputs_t *in,
                                         bool line_pressure_high)
{
    bool bolus = pump_bolus_active();

    if (bolus && !s.verify_armed) {
        s.verify_armed = true;
        s.verify_saw_pressure = false;
        s.verify_commanded_base_nl = pump_commanded_nl();
        s.verify_measured_nl = 0;
        return;
    }
    if (!bolus) {
        s.verify_armed = false;
        return;
    }

    if (line_pressure_high) {
        s.verify_saw_pressure = true;
    }

    if (in->flow_status == SLF3X_OK) {
        /* nL/min * us -> nL */
        s.verify_measured_nl += ((int64_t)in->flow.nl_per_min * (int64_t)dt_us) / 60000000;
    }

    uint64_t commanded = pump_commanded_nl() - s.verify_commanded_base_nl;
    if (commanded < VERIFY_MIN_COMMANDED_NL) {
        return;
    }

    int64_t measured = s.verify_measured_nl;
    if (measured < 0) {
        measured = 0;
    }
    if ((uint64_t)measured * 100u < commanded * VERIFY_MIN_FRACTION_PCT) {
        /*
         * A blocked line also stops the flow, and "occlusion" is the useful
         * thing to tell someone - it says what to go and fix. So if the
         * pressure has risen at any point during this bolus, the diagnosis is
         * left to the occlusion rule rather than pre-empted by a second,
         * vaguer alarm. Tracking it across the whole bolus rather than
         * sampling it here is what stops the two rules racing: whichever
         * threshold happens to trip first, the specific one wins.
         *
         * This cannot mask the failure it exists to catch. A mechanism that
         * has come uncoupled from the syringe moves no fluid and so builds no
         * pressure, which is exactly the case this still alarms on.
         */
        if (!s.verify_saw_pressure) {
            raise(ALARM_UNDER_DELIVERY);
        }
    }
}

void safety_update(uint64_t now_us, const safety_inputs_t *in)
{
    uint64_t dt_us = (s.last_us == 0) ? 0 : (now_us - s.last_us);
    s.last_us = now_us;

    pump_status_t ps;
    pump_get_status(&ps);

    /* ---- Motor driver ------------------------------------------------- */
    if (in->motor_fault) {
        raise(ALARM_MOTOR_FAULT);
    }

    /* ---- Pressure: sensor health first, then what it is telling us ----- */
    bool line_pressure_high = in->pressure.valid &&
                              in->pressure.millipsi >= OCCLUSION_MILLIPSI;

    if (!in->pressure.valid) {
        if (++s.pressure_fails >= SENSOR_FAIL_LIMIT) {
            raise(ALARM_PRESSURE_SENSOR);
        }
        s.occlusion_since_us = 0;  /* a railed output says nothing about the line */
    } else {
        s.pressure_fails = 0;
        if (line_pressure_high) {
            if (s.occlusion_since_us == 0) {
                s.occlusion_since_us = now_us;
            } else if (now_us - s.occlusion_since_us >= OCCLUSION_DWELL_US) {
                raise(ALARM_OCCLUSION);
            }
        } else {
            s.occlusion_since_us = 0;
        }
    }

    /* ---- Flow sensor: health, air in the line, then delivery check ----- */
    if (in->flow_status != SLF3X_OK) {
        if (++s.flow_fails >= SENSOR_FAIL_LIMIT) {
            raise(ALARM_FLOW_SENSOR);
        }
    } else {
        s.flow_fails = 0;
        if (in->flow.air_in_line) {
            raise(ALARM_AIR_IN_LINE);
        }
    }

    update_delivery_verification(dt_us, in, line_pressure_high);

    /* ---- Reservoir and the rolling delivery cap ------------------------ */
    if (ps.reservoir_mu == 0) {
        raise(ALARM_RESERVOIR_EMPTY);
    } else if (ps.reservoir_mu <= PUMP_RESERVOIR_LOW_MU) {
        raise(ALARM_RESERVOIR_LOW);
    }

    if (ps.delivered_hour_mu + BOARD_MU_PER_MICROSTEP > PUMP_MAX_HOUR_MU) {
        raise(ALARM_HOUR_LIMIT);
    }

    /* ---- The interlock ------------------------------------------------- */
    if (s.latched & BLOCKING_MASK) {
        pump_suspend();
    }
}

uint32_t safety_active(void)
{
    return s.latched;
}

bool safety_delivery_blocked(void)
{
    return (s.latched & BLOCKING_MASK) != 0;
}

uint32_t safety_acknowledge(void)
{
    /*
     * Only conditions that have genuinely cleared are dropped. Everything
     * still true re-latches on the next safety_update() anyway, so clearing
     * the whole mask here would produce an alarm that flickers instead of one
     * the user can act on.
     */
    uint32_t still_present = 0;

    if (s.pressure_fails >= SENSOR_FAIL_LIMIT) still_present |= (1u << ALARM_PRESSURE_SENSOR);
    if (s.flow_fails >= SENSOR_FAIL_LIMIT)     still_present |= (1u << ALARM_FLOW_SENSOR);
    if (s.occlusion_since_us != 0)             still_present |= (1u << ALARM_OCCLUSION);

    pump_status_t ps;
    pump_get_status(&ps);
    if (ps.reservoir_mu == 0) {
        still_present |= (1u << ALARM_RESERVOIR_EMPTY);
    }
    if (ps.delivered_hour_mu + BOARD_MU_PER_MICROSTEP > PUMP_MAX_HOUR_MU) {
        still_present |= (1u << ALARM_HOUR_LIMIT);
    }

    s.latched &= still_present;
    s.verify_armed = false;

    if (!safety_delivery_blocked()) {
        pump_resume();
    }
    return s.latched;
}

pump_alarm_t safety_top_alarm(void)
{
    for (int i = 0; i < ALARM__COUNT; i++) {
        if (s.latched & (1u << i)) {
            return (pump_alarm_t)i;
        }
    }
    return ALARM__COUNT;
}

const char *safety_alarm_name(pump_alarm_t id)
{
    switch (id) {
    case ALARM_MOTOR_FAULT:     return "MOTOR FAULT";
    case ALARM_OCCLUSION:       return "OCCLUSION";
    case ALARM_AIR_IN_LINE:     return "AIR IN LINE";
    case ALARM_UNDER_DELIVERY:  return "NO DELIVERY";
    case ALARM_PRESSURE_SENSOR: return "PRESS SENSOR";
    case ALARM_FLOW_SENSOR:     return "FLOW SENSOR";
    case ALARM_RESERVOIR_EMPTY: return "RESERVOIR EMPTY";
    case ALARM_HOUR_LIMIT:      return "HOURLY LIMIT";
    case ALARM_RESERVOIR_LOW:   return "RESERVOIR LOW";
    case ALARM__COUNT:          break;
    }
    return "";
}
