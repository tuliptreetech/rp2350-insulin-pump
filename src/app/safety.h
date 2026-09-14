/*
 * safety.h - Alarm detection and the interlock that stops delivery.
 *
 * The delivery engine is deliberately naive: it commands microsteps and
 * believes them. This layer is the part that does not believe them. It
 * compares what was commanded against what the flow sensor and the pressure
 * sensor observed, watches the driver's own fault line, and suspends the pump
 * when the two accounts disagree.
 *
 * Alarms latch. A condition that has cleared still needs a deliberate
 * acknowledgement before delivery can resume, because "it fixed itself" is not
 * something a pump gets to decide on a patient's behalf.
 */
#ifndef SAFETY_H
#define SAFETY_H

#include <stdbool.h>
#include <stdint.h>

#include "app/pump.h"
#include "drivers/abp_pressure.h"
#include "drivers/slf3x.h"

typedef enum {
    ALARM_MOTOR_FAULT = 0,   /* DRV8825 pulled nFAULT low */
    ALARM_OCCLUSION,         /* line pressure high while delivering */
    ALARM_AIR_IN_LINE,       /* flow sensor sees gas, not liquid */
    ALARM_UNDER_DELIVERY,    /* flow sensor disagrees with what we commanded */
    ALARM_PRESSURE_SENSOR,   /* analog output outside its diagnostic band */
    ALARM_FLOW_SENSOR,       /* NACK or persistent CRC failures */
    ALARM_RESERVOIR_EMPTY,
    ALARM_HOUR_LIMIT,        /* rolling one-hour delivery cap reached */
    ALARM_RESERVOIR_LOW,     /* advisory only - does not stop delivery */
    ALARM__COUNT
} pump_alarm_t;

typedef struct {
    bool            motor_fault;
    abp_reading_t   pressure;
    slf3x_status_t  flow_status;
    slf3x_reading_t flow;
} safety_inputs_t;

void safety_init(void);

/* Evaluate every rule and apply the interlock. Call once per main-loop tick. */
void safety_update(uint64_t now_us, const safety_inputs_t *in);

/* Bitmask of latched alarms, bit `id` per pump_alarm_t. */
uint32_t safety_active(void);

/* True if any latched alarm is severe enough to stop delivery. */
bool safety_delivery_blocked(void);

/*
 * Clear latched alarms whose underlying condition is gone, and resume the
 * pump if nothing blocking is left. Returns the mask still latched.
 */
uint32_t safety_acknowledge(void);

/* Highest-priority active alarm, or ALARM__COUNT when there is none. */
pump_alarm_t safety_top_alarm(void);

const char *safety_alarm_name(pump_alarm_t id);

#endif /* SAFETY_H */
