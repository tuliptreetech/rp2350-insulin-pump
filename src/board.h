/*
 * board.h - Pin map and mechanism constants for the RP2350 insulin pump.
 *
 * This file is the single source of truth for how the board is wired, and it
 * must stay in step with .emerson/peripherals.yaml, which describes the same
 * wiring to the Emerson hardware simulator.
 */
#ifndef BOARD_H
#define BOARD_H

#include "hardware/i2c.h"

/* ---- I2C0: OLED display and inline flow sensor ------------------------- */
#define BOARD_I2C            i2c0
#define BOARD_I2C_SDA_PIN    4
#define BOARD_I2C_SCL_PIN    5
#define BOARD_I2C_HZ         400000

#define BOARD_OLED_ADDR      0x3C
#define BOARD_FLOW_ADDR      0x08

/* ---- DRV8825 stepper driver on the syringe lead screw ------------------ */
#define BOARD_STEP_PIN       2
#define BOARD_DIR_PIN        3
#define BOARD_NFAULT_PIN     6   /* open-drain, active low, pulled up */

/* ---- Honeywell ABP line-pressure sensor -------------------------------- */
#define BOARD_PRESSURE_ADC_CH 1  /* ADC1 == GP27 */

/* ---- Mechanism -------------------------------------------------------- */

/*
 * Delivery resolution. The lead screw, syringe barrel and the DRV8825's
 * microstep setting together move 0.05 uL of fluid per microstep. U-100
 * insulin is 100 units per mL, i.e. 10 uL per unit, so one microstep is
 * 0.005 U == 5 milliunits.
 *
 * Every dose in this firmware is an integer number of microsteps, which is
 * what keeps delivery accounting exact: there is no floating-point residue
 * to accumulate over a day of basal delivery.
 */
#define BOARD_NL_PER_MICROSTEP   50u    /* 0.05 uL, in nanolitres */
#define BOARD_MU_PER_MICROSTEP   5u     /* milliunits of U-100 insulin */

/* Reservoir size of a full cartridge, in milliunits (3.0 mL == 300 U). */
#define BOARD_RESERVOIR_FULL_MU  300000u

#endif /* BOARD_H */
