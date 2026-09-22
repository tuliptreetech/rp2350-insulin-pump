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

/* ---- Serial ports ----------------------------------------------------- */

/*
 * Two ports, deliberately separated.
 *
 * uart0 is stdio: the interactive service console, where a person types a
 * command and reads the reply. uart1 carries telemetry only - one
 * machine-readable record four times a second, for a log or a test harness.
 *
 * Sharing one port makes both jobs worse. The log scrolls the operator's
 * command out of view before they can read the answer, and whatever they type
 * lands in the middle of a record that a parser is trying to read. They are
 * two different streams for two different audiences, so they get two pins.
 */
#define BOARD_TELEMETRY_UART    uart1
#define BOARD_TELEMETRY_TX_PIN  8
#define BOARD_TELEMETRY_BAUD    115200

/* ---- Clocking --------------------------------------------------------- */

/*
 * System clock: 150 MHz, the RP2350's native rate and the SDK default.
 *
 * The workload itself is modest - sensors are polled at 20 Hz, the display
 * redraws at 2 Hz, and the step pulse train tops out at 2 kHz - and this pump
 * previously ran at 48 MHz for battery life. It runs at the native clock so the
 * emulated demo matches the part as shipped: Emerson now paces the guest to the
 * wall clock at the rate clk_sys actually has, so 150 MHz costs nothing in
 * demo time.
 *
 * The test suite and glitch demo mirror this value as SYS_CLOCK_KHZ; change
 * them together.
 */
#define BOARD_SYS_CLOCK_KHZ  150000

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
