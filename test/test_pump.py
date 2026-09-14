"""
Scenario tests for the RP2350 insulin pump, run against the Emerson simulator.

Each scenario drives the firmware through its serial console and injects
hardware faults through Emerson's device models, then asserts on three
independent views of what happened: the microsteps the *driver model* counted,
the delivery totals the *firmware* reports, and the text actually rendered on
the *simulated OLED*.

Timing. Emerson runs the RP2350 core at roughly 0.6% of real time, so one
second of firmware time costs about three minutes of wall clock. Every
threshold a scenario waits out is therefore expensive, and the waits below are
sized in firmware time with wall-clock timeouts generous enough to absorb the
ratio - including the extra slack that comes from running many sessions at
once. Run with test/run_tests.sh, which parallelises them.
"""

import time

import pytest

import display
from pump import (
    ALARMS, MODES, MU_PER_MICROSTEP, OCCLUSION_MILLIPSI,
    PRESSURE_CODE_MAX, PRESSURE_CODE_MIN, alarm_names, wall,
)


def test_boot_sanity(pump):
    """Every peripheral answers and the pump comes up idle with no alarms."""
    t = pump.telemetry()
    assert t is not None, "no telemetry from the firmware"
    assert t["alarms"] == 0, f"unexpected alarms at boot: {alarm_names(t['alarms'])}"
    assert t["flow_st"] == 0, "flow sensor did not answer, or its CRC failed"
    assert 400 <= t["psi_raw"] <= 420, f"pressure sensor idle code out of band: {t['psi_raw']}"
    assert t["resv"] == 300000, f"reservoir should start full, got {t['resv']}"
    assert t["mode"] == 0, "the pump must come up stopped, not delivering"

    status = pump.action(pump.oled, "status")
    assert "display_on=true" in status, status
    # If multi-byte I2C commands are ever split into one transaction each, the
    # controller silently keeps its power-on defaults and these two flip back.
    assert "addr_mode=horizontal" in status, status
    assert "charge_pump=true" in status, status


def test_display_shows_state(pump):
    """The panel really renders the pump's state, not just any lit pixels."""
    pump.start_delivery()
    dump = pump.wait_display("RUNNING")
    assert display.contains_text(dump, "Line"), "OLED does not show the pressure row"
    assert display.contains_text(dump, "Total"), "OLED does not show the delivery total"


def test_dose_limits_rejected(pump):
    """Doses outside the configured envelope are refused, and deliver nothing."""
    pump.start_delivery()
    before = pump.telemetry()["steps"]

    for command, expect in [
        ("bolus 30", "dose out of range"),       # above the 25 U single-bolus cap
        ("bolus 0", "dose out of range"),
        ("basal 9", "rate above limit"),         # above the 5 U/hr basal cap
        ("bolus abc", "bad dose"),
    ]:
        pump.send_expect(command, expect)

    t = pump.telemetry()
    assert t["steps"] == before, "a rejected dose moved the mechanism"
    assert t["basal"] == 1000, f"a rejected basal rate was applied: {t['basal']}"


def test_bolus_delivers(pump):
    """A bolus moves exactly the microsteps it should, and the flow sensor agrees."""
    pump.start_delivery()
    start = pump.telemetry()

    pump.send_expect("bolus 1.0", "ok bolus 1.000 U")
    expected_steps = start["steps"] + 1000 // MU_PER_MICROSTEP   # 200 microsteps

    end = pump.wait_for(
        lambda t: t["bolus_total_mu"] == 0 and t["steps"] >= expected_steps,
        "the bolus to finish")

    delivered = end["steps"] - start["steps"]
    assert delivered == 200, f"expected 200 microsteps, got {delivered}"
    assert end["total"] - start["total"] == 1000, "delivery total does not match the dose"
    assert start["resv"] - end["resv"] == 1000, "reservoir did not fall by the dose"
    assert end["hour"] >= 1000, "rolling-hour accounting did not record the bolus"
    assert end["alarms"] == 0, f"bolus raised alarms: {alarm_names(end['alarms'])}"

    model = pump.stepper_microsteps()
    assert model == end["steps"], \
        f"driver model counted {model} microsteps, firmware thinks {end['steps']}"

    # The flow sensor must have witnessed the delivery, not just the stepper.
    # 200 microsteps/s x 0.05 uL is 600 uL/min commanded; the sensor lags the
    # pump and the bolus is only a second long, so this asks for the right
    # order of magnitude rather than the exact rate.
    during = [r for r in pump.telemetry_history(start["t"]) if r["bolus_total_mu"] > 0]
    assert during, "no telemetry was captured while the bolus was running"
    peak = max(r["flow_nlpm"] for r in during)
    assert peak >= 200000, \
        f"flow sensor saw only {peak} nL/min during a bolus commanding 600000"


def test_bolus_cancel_bills_only_what_moved(pump):
    """Cancelling mid-bolus stops delivery and charges only the part delivered."""
    pump.start_delivery()
    start = pump.telemetry()

    pump.send_expect("bolus 5.0", "ok bolus 5.000 U")
    pump.wait_for(lambda t: t["steps"] > start["steps"] + 30, "the bolus to get under way")
    pump.send_expect("cancel", "bolus cancelled")

    settled = pump.wait_for(lambda t: t["bolus_total_mu"] == 0, "the bolus to clear")
    time.sleep(20)
    after = pump.telemetry()

    moved = after["steps"] - start["steps"]
    assert moved < 1000, f"cancel did not stop delivery: {moved} microsteps"
    assert after["total"] - start["total"] == moved * MU_PER_MICROSTEP, \
        "delivery total does not match the microsteps actually pulsed"
    assert after["steps"] - settled["steps"] < 5, "mechanism kept running after cancel"


def test_occlusion_suspends(pump):
    """A blocked line raises the pressure, and the pump stops rather than pushing."""
    pump.start_delivery()
    start_t = pump.telemetry()["t"]

    pump.action(pump.pressure, "set_occluded", "true")
    # Large enough that the line has room to reach the threshold and hold it
    # for the dwell. The pump suspends partway through, so the rest is never
    # delivered and the extra size costs nothing when the alarm works.
    pump.send_expect("bolus 8.0", "ok bolus 8.000 U")

    # The longest wait in the suite. The line needs ~2 s of firmware time to
    # reach the threshold at this bolus rate, then the 2 s dwell on top, then
    # the pump has to act on it.
    t = pump.wait_alarm("OCCLUSION", timeout_s=wall(8))
    assert t["mode"] == 2, f"pump did not suspend, mode={MODES[t['mode']]}"

    peak = max(r["psi_m"] for r in pump.telemetry_history(start_t))
    assert peak >= OCCLUSION_MILLIPSI, \
        f"occlusion alarmed but pressure never reached the threshold: {peak} mpsi"

    after = pump.wait_for(lambda x: x["bolus_total_mu"] == 0, "the bolus to be abandoned")
    frozen = after["steps"]
    time.sleep(30)
    assert pump.telemetry()["steps"] == frozen, "mechanism still moving after suspension"

    pump.wait_display("OCCLUSION")


def test_recovery_requires_the_fault_to_clear(pump):
    """Acknowledging an alarm whose cause is still present must not resume delivery."""
    pump.start_delivery()
    pump.action(pump.pressure, "set_output_fault", "open")
    pump.wait_alarm("PRESSURE_SENSOR")

    pump.send_expect("ack", "ok alarms=")
    pump.send("run")
    time.sleep(30)
    t = pump.telemetry()
    assert t["mode"] == 2, "pump resumed while its pressure sensor was still broken"
    assert t["alarms"] & (1 << ALARMS.index("PRESSURE_SENSOR")), \
        "alarm cleared while the fault was still present"

    # Repair the sensor, and let the firmware actually see a healthy reading
    # before acknowledging - `ack` clears what has already cleared, so a user
    # who acks too early simply has to ack again.
    pump.action(pump.pressure, "set_output_fault", "none")
    pump.wait_for(lambda t: PRESSURE_CODE_MIN <= t["psi_raw"] <= PRESSURE_CODE_MAX,
                  "the pressure sensor back inside its diagnostic band")

    pump.send_expect("ack", "ok alarms=0x000")
    pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                  "recovery once the sensor was repaired")


def test_air_in_line_suspends(pump):
    """A bubble in the tubing stops delivery."""
    pump.start_delivery()
    pump.action(pump.flow, "set_air_in_line", "true")

    t = pump.wait_alarm("AIR_IN_LINE")
    assert t["air"] == 1, "firmware did not decode the air-in-line flag"
    assert t["mode"] == 2, f"pump did not suspend, mode={MODES[t['mode']]}"
    pump.wait_display("AIR IN LINE")


def test_motor_fault_suspends(pump):
    """The driver's own nFAULT output stops the pump."""
    pump.start_delivery()
    pump.action(pump.stepper, "inject_fault", "overcurrent")

    t = pump.wait_alarm("MOTOR_FAULT")
    assert t["nfault"] == 1, "firmware did not see nFAULT asserted"
    assert t["mode"] == 2, f"pump did not suspend, mode={MODES[t['mode']]}"


def test_flow_sensor_corruption_suspends(pump):
    """Persistent CRC failures are treated as a broken sensor, not as zero flow."""
    pump.start_delivery()
    pump.action(pump.flow, "set_crc_error_period", 1)

    t = pump.wait_alarm("FLOW_SENSOR")
    assert t["flow_st"] == 2, f"expected SLF3X_ERR_CRC (2), got flow_st={t['flow_st']}"
    assert t["flow_nlpm"] == 0, "a failed read left a stale flow value behind"
    assert t["mode"] == 2, f"pump did not suspend, mode={MODES[t['mode']]}"


def test_under_delivery_detected(pump):
    """
    If the mechanism reports delivery the flow sensor cannot see, the pump
    stops. This is the check that catches a stripped lead screw or a syringe
    that is not actually coupled.
    """
    pump.start_delivery()
    pump.action(pump.flow, "set_flow_scale_error_percent", -100)
    pump.send_expect("bolus 3.0", "ok bolus 3.000 U")

    t = pump.wait_alarm("UNDER_DELIVERY")
    assert t["mode"] == 2, f"pump did not suspend, mode={MODES[t['mode']]}"
    assert t["psi_m"] < OCCLUSION_MILLIPSI, \
        "pressure was high, so this should have been diagnosed as an occlusion"


@pytest.mark.slow
def test_basal_accumulates(pump):
    """
    Basal delivery emits microsteps on schedule.

    Slow by nature: at the 5 U/hr maximum the mechanism moves one microstep
    every 3.6 firmware seconds, which is about ten wall-clock minutes per step
    in the emulator.
    """
    pump.send_expect("basal 5.0", "ok basal 5.000 U/hr")
    pump.start_delivery()
    start = pump.wait_for(lambda t: t["mode"] == 1 and t["basal"] == 5000,
                          "basal delivery running")

    t = pump.wait_for(lambda t: t["steps"] >= start["steps"] + 2,
                      "two basal microsteps", timeout_s=wall(12))
    elapsed_ms = t["t"] - start["t"]
    expected_ms = 2 * 3600   # 3.6 s per microstep at 5.000 U/hr
    assert abs(elapsed_ms - expected_ms) < 1500, \
        f"two basal microsteps took {elapsed_ms} ms, expected about {expected_ms} ms"
