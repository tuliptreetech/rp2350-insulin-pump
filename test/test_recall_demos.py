"""
Demos 6-8 in DEMOS.md: the recall scenarios, as assertions.

test_pump.py and test_demos.py both have the same shape - inject a fault, an
alarm fires, the pump suspends. They show the safety layer working as designed.
That is not the shape the published FDA Class I insulin-pump recalls take. Those
are the other three shapes, and this file is one scenario per shape:

  6. delivered the wrong amount   Medtronic MiniMed 630G/670G retainer ring
  7. failed to alarm              Insulet Omnipod 5 internal tubing tear
  8. alarmed when it should not   Tandem Mobi "Malfunction 12"
  9. alarmed when it should not   the same recall, at a width that settles it

How these are meant to fail
---------------------------
These scenarios ask questions the firmware has not been written to answer, so
"the requirement is not met" is a likely and legitimate outcome rather than a
broken test. They are built so the two are never confused:

  * Everything that establishes the scenario actually staged - the injection
    took effect, the flow really was over-reported, the line really did leak -
    is a hard `assert`. If one of those trips, the harness or the device model
    is wrong and the run should go red.

  * The safety *requirement* at the end of each scenario is checked with
    `pytest.xfail(reason=...)`, which reports as `x` rather than `F` and
    carries the explanation into the summary (`run_tests.sh` passes `-ra`).
    Wire up the rule it names and the same test turns into an `XPASS`.

So a green run with a handful of xfails is this file working. Read the reasons.

Every scenario here has been run; the measured results and what they mean are
in DEMOS.md. Timeouts are budgeted from comparable scenarios in test_pump.py.

Cost
----
Demo 8 asserts that nothing happens for a defined stretch of *firmware* time,
which cannot be hurried: at the rates in pump.py a six-second window is tens of
wall-clock minutes. Those three are marked `slow` and need `--runslow`.
"""

import os
import time

import pytest

from pump import (
    MODES, OCCLUSION_MILLIPSI, PRESSURE_CODE_MAX, PRESSURE_CODE_MIN,
    alarm_names, wall,
)

# ---- Constants mirrored from the firmware and the device models -----------

# src/app/safety.c
VERIFY_MIN_FRACTION_PCT = 50       # UNDER_DELIVERY floor
VERIFY_MIN_COMMANDED_NL = 10000    # 1.0 U before verification arms at all
OCCLUSION_DWELL_MS = 2000
SENSOR_FAIL_LIMIT = 10

# src/drivers/abp_pressure.c: counts = 410 at 0 psi, 3276 counts per 15 psi.
ABP_COUNTS_ZERO = 410
ABP_COUNTS_PER_MILLIPSI = 3276 / 15000.0

# src/board.h: 20 Hz sensor polling, 48 MHz clk_sys.
SENSOR_POLL_MS = 50
SYS_CLOCK_KHZ = 48000

MICROSTEPS_PER_UNIT = 200          # 1.000 U / 5 mU per microstep

# Demo 9 stages a glitch this wide. 100 us is 1/500th of a control period and
# three orders of magnitude below any overcurrent a DRV8825 would hold - short
# enough that nobody can argue it is a real fault the firmware caught.
GLITCH_US = 100
GLITCH_TICKS = GLITCH_US * (SYS_CLOCK_KHZ // 1000)

# The Hazard3 core, for breakpoints. Demo 9 is the only scenario that needs to
# stop the core at a specific instruction rather than step a duration.
CPU = "/Hazard3Cpu"

# RP2350 SIO. GPIO_IN is the register drv8825_faulted() reads to sample nFAULT,
# and it is fixed by the architecture rather than by our build.
SIO_BASE_UPPER = "0xd0000"
SIO_GPIO_IN_OFFSET = "0x4"

# How long a "nothing happened" window has to be, in firmware milliseconds, to
# mean anything. Three times the occlusion dwell and 120 times the ten
# consecutive samples a sensor fault needs - long enough that a debounce that
# does not work has had every chance to prove it.
QUIET_WINDOW_MS = 6000

# A healthy bolus has to move the line at least this far above its resting
# pressure for "the pressure failed to rise" to be a rule anyone can build.
# Below this the signal is inside the noise and demo 7 has no foundation.
MIN_PRESSURE_SIGNATURE_MPSI = 200


# ---- Helpers --------------------------------------------------------------


def integrate_flow_nl(records):
    """
    Nanolitres the flow sensor reported across `records`, by trapezoid.

    This is the same sum safety.c keeps, at a quarter of the resolution: the
    firmware integrates every 20 Hz sample and telemetry only carries 4 Hz of
    them. Fine for the factor-of-two discriminations below, not fine for
    anything that needs to agree with the firmware's own total to the nanolitre.
    """
    total = 0.0
    for prev, cur in zip(records, records[1:]):
        dt_ms = cur["t"] - prev["t"]
        total += (prev["flow_nlpm"] + cur["flow_nlpm"]) / 2.0 * dt_ms / 60000.0
    return total


# Alarms that are a legitimate answer to "did the pump notice the delivered
# volume was wrong". Anything else firing during a demo 6 scenario means the run
# was disturbed by an unrelated fault rather than answering the question.
# OVER_DELIVERY does not exist yet; naming it here means these scenarios keep
# working on the day it does.
DELIVERY_VOLUME_ALARMS = ("UNDER_DELIVERY", "OVER_DELIVERY")


def trace(records):
    """What the instruments did across `records`, for a failure message."""
    if not records:
        return "no telemetry records"
    psi = [r["psi_m"] for r in records]
    raw = [r["psi_raw"] for r in records]
    flow = [r["flow_nlpm"] for r in records]
    return (
        f"psi_m {min(psi)}..{max(psi)} mpsi, psi_raw {min(raw)}..{max(raw)} "
        f"(valid band {PRESSURE_CODE_MIN}-{PRESSURE_CODE_MAX}), "
        f"flow {min(flow)}..{max(flow)} nL/min, across {len(records)} records"
    )


def assert_pressure_stayed_readable(records, scenario):
    """
    A railed ABP output reports millipsi = 0 (abp_pressure.c), so any check of
    the form `psi_m < threshold` is *satisfied* by a broken sensor. Every
    scenario that reasons about line pressure has to establish the sensor was
    actually reading first, or it is reading a zero that means "no idea".
    """
    railed = [r for r in records
              if not (PRESSURE_CODE_MIN <= r["psi_raw"] <= PRESSURE_CODE_MAX)]
    assert not railed, (
        f"the pressure sensor left its diagnostic band during {scenario}, so "
        f"psi_m reads 0 and says nothing about the line. {trace(records)}"
    )


def assert_not_confounded(outcome, records, scenario):
    """
    Require that any alarm raised is one this scenario is actually asking
    about, and return the names that fired.

    Without this a scenario "passes" on whatever alarm happened to fire first,
    which is the failure mode these tests exist to avoid in the firmware.
    """
    fired = alarm_names(outcome["alarms"])
    confounders = [n for n in fired if n not in DELIVERY_VOLUME_ALARMS]
    assert not confounders, (
        f"{scenario} raised {confounders}, which is not an answer to the "
        f"question it asks - the run was disturbed rather than informative. "
        f"{trace(records)}. Fix the staging before reading anything into it."
    )
    return fired


def hold(pump, firmware_ms, predicate, what, timeout_s=None):
    """
    Assert `predicate` holds across `firmware_ms` of *firmware* time.

    The inverse of `Pump.wait_for`, and the reason demo 8 needs its own
    vocabulary: every other scenario in the suite waits for something to start
    being true, and these have to establish that something never became true.

    Measured in firmware time deliberately. A wall-clock window would mean a
    different number of sensor polls on a loaded server than on an idle one,
    which is exactly the kind of test that passes because it was too short.
    """
    if timeout_s is None:
        # Generous: the window itself, plus room for the session to be sharing
        # the server with three others.
        timeout_s = wall(firmware_ms / 1000.0 * 2)

    start = pump.wait_for(lambda t: True, "a telemetry record to start the window")
    deadline = time.time() + timeout_s
    last = start
    while time.time() < deadline:
        last = pump.telemetry()
        if not predicate(last):
            return False, last
        if last["t"] - start["t"] >= firmware_ms:
            return True, last
        time.sleep(0.5)
    raise AssertionError(
        f"{what}: only {last['t'] - start['t']} ms of firmware time elapsed in "
        f"{timeout_s} s of wall clock, needed {firmware_ms} ms. The window never "
        f"completed, so nothing was proved either way."
    )


def quiet(pump, what, firmware_ms=QUIET_WINDOW_MS):
    """Hold for a window, requiring no alarm and continued delivery throughout."""
    return hold(
        pump, firmware_ms,
        lambda t: t["alarms"] == 0 and t["mode"] == 1,
        what,
    )


def settle_telemetry(pump, after_ms, timeout_s=60):
    """
    Read a telemetry record that provably postdates firmware time `after_ms`.

    Used around `Machine.step()`, where `Pump.wait_for` cannot help: it polls
    for the core to advance and the core is deliberately not running.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        t = pump.telemetry()
        if t is not None and t["t"] > after_ms:
            return t
        time.sleep(1)
    raise AssertionError(
        f"no telemetry record past t={after_ms} ms after stepping the core"
    )


# ===========================================================================
# Demo 6 - Free flow: the check that only looks one way
#
# Medtronic MiniMed 630G/670G, Class I February 2020 (Z-0955-2020,
# Z-0956-2020). 322,005 units, 26,421 complaints, 2,175 injuries, one death. A
# cracked plastic retainer ring let the insulin cartridge slip in the
# drive-shaft chamber. Usually told as under-delivery, but the same slip also
# produced accidental bolus: a cartridge free to move means fluid can go in
# that nobody commanded.
#
# safety.c compares measured flow against commanded flow in one direction
# only - UNDER_DELIVERY when measured falls below 50% of commanded. There is no
# upper bound in the file. These three scenarios are what decides whether that
# matters.
# ===========================================================================


def test_over_delivery_during_a_bolus(pump):
    """
    The flow sensor reports far more insulin arriving than was commanded.

    Demo 4 with the sign reversed: `-100` made the sensor report nothing
    moving, `+300` makes it report four times the commanded volume. The
    mechanism is behaving; the account of what reached the patient is not.

    A pump that cannot tell it is over-delivering cannot bound a hypoglycaemic
    event, which is the half of the retainer-ring recall that killed someone.
    """
    pump.start_delivery()
    start = pump.telemetry()

    pump.action(pump.flow, "set_flow_scale_error_percent", 300)
    pump.send_expect("bolus 1.5", "ok bolus 1.500 U")

    target_steps = start["steps"] + int(1.5 * MICROSTEPS_PER_UNIT)
    outcome = pump.wait_for(
        lambda t: t["alarms"] != 0 or t["steps"] >= target_steps,
        "the over-reported bolus to alarm or to run to completion",
        timeout_s=wall(6))

    # --- the scenario really staged -------------------------------------
    records = pump.telemetry_history(start["t"])
    measured_nl = integrate_flow_nl(records)
    commanded_nl = (outcome["total"] - start["total"]) * 10  # 1 mU == 10 nL

    assert commanded_nl >= VERIFY_MIN_COMMANDED_NL, (
        f"only {commanded_nl} nL was commanded, below the "
        f"{VERIFY_MIN_COMMANDED_NL} nL at which verification arms - the "
        f"scenario never reached the code it is aimed at"
    )
    assert measured_nl > commanded_nl * 2, (
        f"the flow sensor reported {measured_nl:.0f} nL against {commanded_nl} nL "
        f"commanded; the scale error did not take effect, so this run says "
        f"nothing about over-delivery detection"
    )
    assert_pressure_stayed_readable(records, "the over-delivery bolus")
    assert max(r["psi_m"] for r in records) < OCCLUSION_MILLIPSI, (
        f"the line pressure reached the occlusion trip, so whatever happened "
        f"here was diagnosed as a blockage rather than as over-delivery. "
        f"{trace(records)}"
    )
    fired = assert_not_confounded(outcome, records, "the over-delivery scenario")

    # --- the requirement -------------------------------------------------
    if not fired:
        pytest.xfail(
            f"the flow sensor reported {measured_nl / commanded_nl:.1f}x the "
            f"commanded volume and the pump delivered the bolus without "
            f"alarming. safety.c:111 tests `measured < commanded * "
            f"{VERIFY_MIN_FRACTION_PCT}%` and nothing tests the other "
            f"direction, so there is no rule for this to trip. Needs an "
            f"over-delivery bound and an alarm bit (0x200; bits 0-8 are taken)."
        )

    assert outcome["mode"] == 2, (
        f"an over-delivery alarm was raised but the pump kept delivering, "
        f"mode={MODES[outcome['mode']]}"
    )


def test_free_flow_with_the_mechanism_stopped(pump):
    """
    Insulin moving while the pump is commanding nothing at all.

    The version of the retainer-ring failure worth building, because it is the
    one the existing rule cannot see under any threshold. Verification arms
    only once 1.0 U has been *commanded* (VERIFY_MIN_COMMANDED_NL), and a dose
    nobody commanded never arms it - so basal runs, gravity siphons the
    cartridge into the patient, and every rule in safety.c is satisfied.

    `set_flow_scale_error_percent` cannot express this: it scales the flow that
    is actually happening, and with the mechanism idle that is zero. The model
    needs a knob that originates flow instead of scaling it.
    """
    try:
        pump.action(pump.flow, "set_free_flow_ul_per_min", 30)
    except Exception:
        pytest.skip(
            "needs a `set_free_flow_ul_per_min` action on the slf3x model: "
            "flow originating with the mechanism stopped, which no existing "
            "knob can produce. See DEMOS.md, demo 6."
        )

    pump.start_delivery()
    start = pump.telemetry()

    held, last = quiet(pump, "free flow with the mechanism stopped")

    records = pump.telemetry_history(start["t"])
    measured_nl = integrate_flow_nl(records)
    moved = last["steps"] - start["steps"]

    assert measured_nl > VERIFY_MIN_COMMANDED_NL, (
        f"only {measured_nl:.0f} nL of free flow was staged; not enough to be "
        f"a clinically interesting dose, so the run proves nothing"
    )
    assert moved * 50 < measured_nl / 2, (
        f"the mechanism moved {moved} microsteps during the window, so this is "
        f"not free flow - the pump was commanding delivery"
    )

    if held:
        pytest.xfail(
            f"{measured_nl:.0f} nL went through the line with the mechanism "
            f"stopped and the pump never alarmed. Nothing in safety.c compares "
            f"flow against zero commanded delivery. Needs the over-delivery "
            f"bound from test_over_delivery_during_a_bolus, armed independently "
            f"of VERIFY_MIN_COMMANDED_NL."
        )

    assert last["mode"] == 2, (
        f"free flow raised an alarm but the pump kept running, "
        f"mode={MODES[last['mode']]}"
    )


def test_step_slip_under_delivers_below_the_alarm_threshold(pump):
    """
    The mechanism half of the same cracked ring: one microstep in four is
    pulsed but never moves the plunger.

    This is not a scenario about whether the under-delivery rule exists - it
    does, and demo 4 shows it firing. It is a scenario about where its
    threshold sits. A 25% shortfall is comfortably above the 50% floor, so the
    rule is not expected to fire, and a pump quietly running a quarter short
    indefinitely is a clinical problem that no alarm in the table describes.

    So this asserts the invariant rather than an outcome: whatever the firmware
    did has to be consistent with its own threshold. The number it prints is
    the point.
    """
    pump.start_delivery()
    start = pump.telemetry()
    moved_start = pump.stepper_microsteps()

    pump.action(pump.stepper, "set_step_slip", 4)
    pump.send_expect("bolus 1.5", "ok bolus 1.500 U")

    target_steps = start["steps"] + int(1.5 * MICROSTEPS_PER_UNIT)
    outcome = pump.wait_for(
        lambda t: t["alarms"] != 0 or t["steps"] >= target_steps,
        "the slipping bolus to alarm or to run to completion",
        timeout_s=wall(6))

    # --- the scenario really staged -------------------------------------
    # `steps` counts pulses the driver emitted; get_position counts plunger
    # movement. Healthy, they agree - test_demos.py asserts exactly that. Under
    # slip they must not.
    pulsed = outcome["steps"] - start["steps"]
    moved = pump.stepper_microsteps() - moved_start
    assert pulsed > 0, "no microsteps were pulsed; the bolus never started"
    assert moved < pulsed, (
        f"the driver model moved {moved} microsteps against {pulsed} pulsed, so "
        f"set_step_slip had no effect and this run says nothing about the "
        f"under-delivery threshold"
    )

    records = pump.telemetry_history(start["t"])
    measured_nl = integrate_flow_nl(records)
    commanded_nl = pulsed * 50  # BOARD_NL_PER_MICROSTEP
    shortfall_pct = 100.0 * (1.0 - measured_nl / commanded_nl) if commanded_nl else 0.0

    assert_pressure_stayed_readable(records, "the slipping bolus")
    fired = assert_not_confounded(outcome, records, "the step-slip scenario")

    # --- the invariant ---------------------------------------------------
    delivered_pct = 100.0 - shortfall_pct
    if fired:
        assert delivered_pct < VERIFY_MIN_FRACTION_PCT, (
            f"UNDER_DELIVERY fired at {delivered_pct:.0f}% delivered, above its "
            f"own {VERIFY_MIN_FRACTION_PCT}% floor"
        )
    else:
        assert delivered_pct >= VERIFY_MIN_FRACTION_PCT, (
            f"only {delivered_pct:.0f}% of the commanded volume arrived, below "
            f"the {VERIFY_MIN_FRACTION_PCT}% floor, and the pump did not alarm"
        )
        pytest.xfail(
            f"a mechanism slipping one microstep in four delivered "
            f"{delivered_pct:.0f}% of the commanded dose and the pump did not "
            f"alarm, which is correct against a {VERIFY_MIN_FRACTION_PCT}% "
            f"floor. Whether that floor belongs at 50% is the question this "
            f"scenario exists to put on the table."
        )


# ===========================================================================
# Demo 7 - The leaking set: the failure both instruments call healthy
#
# Insulet Omnipod 5, Class I 29 April 2026. About 1.5% of distributed pods, 18
# serious adverse events, DKA reported. A tear in the internal silicone tubing
# between reservoir and cannula let insulin leak into the pod casing instead of
# into the patient. The sentence that makes it a demo: the occlusion alarm did
# not detect it.
#
# The exact inverse of demo 1. An occlusion is fluid that cannot get out, and
# it announces itself as pressure rising. A tear is fluid getting out where it
# should not, and it shows as pressure that never builds. Every pressure rule
# in safety.c is written for the first case.
# ===========================================================================


def test_a_leaking_set_is_not_detected(pump):
    """
    Two boluses in one session: an intact line, then a torn one.

    The comparison has to happen inside one session because the baseline does
    not exist anywhere else. The rule that would catch a leak keys on pressure
    *failing to rise*, and no demo has ever measured what a healthy bolus does
    to the line - demo 1 reports the line at rest and at the occlusion trip,
    demo 4 reports a bolus in which no fluid moved. Neither is this number.

    So the first bolus is the measurement, and the second is the scenario. If
    the first one shows no usable pressure signature, that is reported as the
    finding rather than papered over: a rule cannot be founded on a signal that
    is not there.
    """
    pump.start_delivery()
    rest = pump.wait_for(lambda t: t["mode"] == 1, "basal delivery running")

    # --- 1. the baseline: a bolus into an intact line --------------------
    healthy_start = pump.telemetry()
    healthy_target = healthy_start["steps"] + int(1.5 * MICROSTEPS_PER_UNIT)
    pump.send_expect("bolus 1.5", "ok bolus 1.500 U")
    healthy_end = pump.wait_for(
        lambda t: t["alarms"] != 0 or t["steps"] >= healthy_target,
        "the baseline bolus to finish", timeout_s=wall(6))

    assert healthy_end["alarms"] == 0, (
        f"the baseline bolus into an intact line alarmed: "
        f"{alarm_names(healthy_end['alarms'])}"
    )

    healthy = pump.telemetry_history(healthy_start["t"])
    healthy_peak = max(r["psi_m"] for r in healthy)
    healthy_flow_nl = integrate_flow_nl(healthy)
    signature = healthy_peak - rest["psi_m"]

    if signature < MIN_PRESSURE_SIGNATURE_MPSI:
        pytest.xfail(
            f"a healthy 1.5 U bolus moved the line only {signature} mpsi above "
            f"its resting {rest['psi_m']} mpsi (peak {healthy_peak}). That is "
            f"below the {MIN_PRESSURE_SIGNATURE_MPSI} mpsi this scenario treats "
            f"as the floor for a usable signal, so 'the pressure failed to rise' "
            f"cannot be told from a normal delivery and demo 7 has no rule to "
            f"be built on. The finding is about the instrument, not the firmware."
        )

    # --- 2. the scenario: the same bolus into a torn set -----------------
    pump.action(pump.pressure, "set_leak_rate_psi_per_s", 0.5)

    leak_start = pump.telemetry()
    leak_target = leak_start["steps"] + int(1.5 * MICROSTEPS_PER_UNIT)
    pump.send_expect("bolus 1.5", "ok bolus 1.500 U")
    leak_end = pump.wait_for(
        lambda t: t["alarms"] != 0 or t["steps"] >= leak_target,
        "the leaking bolus to alarm or to finish", timeout_s=wall(6))

    leaking = pump.telemetry_history(leak_start["t"])
    leak_peak = max(r["psi_m"] for r in leaking)
    leak_flow_nl = integrate_flow_nl(leaking)

    # --- the scenario really staged -------------------------------------
    assert leak_peak < healthy_peak, (
        f"the leaking bolus peaked at {leak_peak} mpsi against {healthy_peak} "
        f"mpsi for the intact one; set_leak_rate_psi_per_s did not bleed the "
        f"line, so this run says nothing about leak detection"
    )
    assert PRESSURE_CODE_MIN <= leak_end["psi_raw"] <= PRESSURE_CODE_MAX, (
        "the pressure sensor railed during the leak, so this would be caught as "
        "a sensor fault rather than missed as a leak"
    )

    # The point of the demo: the flow sensor is upstream of the tear, so it
    # watches the commanded volume go past and reports a healthy delivery.
    assert leak_flow_nl > healthy_flow_nl * 0.5, (
        f"the flow sensor saw {leak_flow_nl:.0f} nL against {healthy_flow_nl:.0f} "
        f"nL for the intact bolus. The leak is modelled upstream of the sensor, "
        f"so UNDER_DELIVERY can catch it and this is not the Omnipod case - "
        f"model the leak downstream of the flow sensor"
    )

    # --- the requirement -------------------------------------------------
    if leak_end["alarms"] == 0:
        pytest.xfail(
            f"1.5 U was commanded into a leaking set and the pump reported a "
            f"normal delivery. The flow sensor saw {leak_flow_nl:.0f} nL go "
            f"past, so UNDER_DELIVERY was satisfied; the line peaked at "
            f"{leak_peak} mpsi against an intact {healthy_peak} mpsi and never "
            f"approached the {OCCLUSION_MILLIPSI} mpsi trip, so OCCLUSION was "
            f"satisfied; both sensors stayed inside their diagnostic bands, so "
            f"neither sensor fault fired. Three rules content and no insulin in "
            f"the patient. Needs a line-integrity rule keyed on the "
            f"{signature} mpsi signature a healthy bolus leaves behind."
        )

    assert leak_end["mode"] == 2, (
        f"a leak alarm was raised but the pump kept delivering, "
        f"mode={MODES[leak_end['mode']]}"
    )


# ===========================================================================
# Demo 8 - The false alarm: the scenarios that assert nothing happens
#
# Tandem Mobi, Class I 2026, firm-initiated 6 October 2025. 17,700+ devices,
# 281 adverse events, 4 injuries. A software issue made the pump incorrectly
# detect a vibration-motor problem and raise "Malfunction 12". The pump was not
# broken; its fault detection was. Corrected by a remote software update.
#
# These invert the format of every other scenario in the suite. They inject
# transients - signals that momentarily resemble faults and are not - and
# require that `alarms` stays 0x000 and `mode` stays 1 throughout. A safety
# layer is only as good as its willingness not to fire.
#
# All three are `slow`: a window of firmware time is the whole assertion and it
# cannot be hurried.
# ===========================================================================


@pytest.mark.slow
def test_pressure_noise_near_the_trip_does_not_alarm(pump):
    """
    ADC noise whose peaks cross the occlusion threshold but never hold it.

    This is what OCCLUSION_DWELL_US is for, and the only scenario that tests
    it: demo 1 shows the dwell being satisfied, nothing shows it rejecting.

    Staging needs care and it is the reason this scenario is fiddly rather than
    obvious. The noise has to be big enough to cross 4 psi from the line's
    resting pressure, and small enough that the low excursions stay inside the
    sensor's 2.5-97.5% diagnostic band - otherwise PRESSURE_SENSOR fires and
    the run proves something else entirely. A resting line sits near the bottom
    of the band, so the baseline is lifted first with a zero-offset error: the
    quiescent reading becomes 2.5 psi, still well under the trip, and the noise
    band then sits clear of both limits.
    """
    trip_counts = ABP_COUNTS_ZERO + OCCLUSION_MILLIPSI * ABP_COUNTS_PER_MILLIPSI
    assert trip_counts < PRESSURE_CODE_MAX, "the occlusion trip is outside the band"

    pump.start_delivery()

    # Measured against the model rather than guessed, because the first
    # calibration of this was wrong in a way that looked like a firmware
    # result. With the line clear the ABP sits at exactly 410 counts;
    # `set_zero_offset_psi` moves that baseline by 218.4 counts per psi, and
    # `set_noise_counts N` adds a roughly uniform +/- N on top.
    #
    # 3.2 psi of offset puts the quiescent reading at ~1109 counts, which is
    # 3200 mpsi - clearly under the 4000 trip, so the baseline alone can never
    # satisfy the dwell. +/- 400 counts then spans ~709..1509, straddling the
    # 1284-count trip on roughly 30% of samples and staying far clear of the
    # 102-count floor.
    #
    # Both bounds matter. At 2.5 psi of offset only 9% of samples crossed,
    # which at the 4 Hz telemetry this is judged on meant the window could
    # legitimately contain no crossing at all and the scenario proved nothing.
    # At 1200 counts of noise the troughs hit the band floor and raise
    # PRESSURE_SENSOR, which is a different alarm and a different scenario.
    pump.action(pump.pressure, "set_zero_offset_psi", 3.2)
    pump.action(pump.pressure, "set_noise_counts", 400)

    held, last = quiet(pump, "pressure noise straddling the occlusion trip")

    # --- the scenario really staged -------------------------------------
    records = pump.telemetry_history()
    crossings = [r for r in records if r["psi_m"] >= OCCLUSION_MILLIPSI]
    railed = [r for r in records
              if not (PRESSURE_CODE_MIN <= r["psi_raw"] <= PRESSURE_CODE_MAX)]

    assert crossings, (
        f"the line never crossed {OCCLUSION_MILLIPSI} mpsi during the window, so "
        f"the dwell was never asked to reject anything. {trace(records)}. "
        f"Raise set_zero_offset_psi so more of the noise band sits above the "
        f"trip, keeping the troughs clear of the {PRESSURE_CODE_MIN}-count floor."
    )
    assert not railed, (
        f"the pressure sensor left its diagnostic band on {len(railed)} samples "
        f"(psi_raw {min(r['psi_raw'] for r in railed)}.."
        f"{max(r['psi_raw'] for r in railed)}), which is a sensor fault rather "
        f"than the occlusion noise this scenario is staging. Lower "
        f"set_noise_counts or raise set_zero_offset_psi."
    )

    # --- the requirement -------------------------------------------------
    if not held:
        pytest.xfail(
            f"transient pressure noise crossed the trip on "
            f"{len(crossings)}/{len(records)} telemetry samples and the pump "
            f"raised {alarm_names(last['alarms'])} after "
            f"{last['t']} ms. OCCLUSION_DWELL_US is {OCCLUSION_DWELL_MS} ms and "
            f"should have rejected every one of these. This is Malfunction 12's "
            f"shape: a pump suspending basal on a signal that was never a fault."
        )


@pytest.mark.slow
def test_isolated_crc_errors_do_not_alarm(pump):
    """
    One corrupt word in three on the flow sensor, never two in a row.

    Demo 5 uses `set_crc_error_period 1` to show FLOW_SENSOR firing. This is
    the other side of SENSOR_FAIL_LIMIT: a sensor that drops a third of its
    reads is noisy, not dead, and ten *consecutive* failures is the line the
    firmware draws. Period 3 can never produce two in a row, so the counter
    should never climb past one.

    A period of 3 rather than 20 on purpose. It packs many more rejections into
    the same window of firmware time, and firmware time is the expensive thing
    here.
    """
    pump.start_delivery()
    pump.action(pump.flow, "set_crc_error_period", 3)

    held, last = quiet(pump, "isolated CRC failures on the flow sensor")

    # --- the scenario really staged -------------------------------------
    records = pump.telemetry_history()
    bad = [r for r in records if r["flow_st"] != 0]
    assert bad, (
        "no telemetry sample caught a CRC failure, so the injection either did "
        "not take effect or the firmware is not surfacing it. Nothing was "
        "rejected, so nothing was proved."
    )

    # --- the requirement -------------------------------------------------
    if not held:
        pytest.xfail(
            f"a flow sensor failing one read in three - never twice in a row - "
            f"raised {alarm_names(last['alarms'])} after {last['t']} ms. "
            f"SENSOR_FAIL_LIMIT is {SENSOR_FAIL_LIMIT} *consecutive* failures "
            f"and a period of 3 cannot produce two, so the counter is not being "
            f"reset on a good read."
        )


@pytest.mark.slow
def test_a_single_nfault_sample_does_not_latch(pump):
    """
    nFAULT asserted for about one sensor poll, then released.

    The closest analogue to Malfunction 12 in this firmware, and the one path
    with no debounce behind it. Pressure and flow each need
    SENSOR_FAIL_LIMIT consecutive bad samples before the firmware will call an
    instrument broken; safety.c:139-141 raises MOTOR_FAULT from a single
    sample of a pin.

    Whether that asymmetry is a defect depends on whether a real DRV8825 can
    glitch nFAULT briefly enough to land inside one control period - a hardware
    question this scenario frames rather than answers. The failure it would
    produce is the recall's exactly: a spurious fault detection that latches,
    suspends basal, and takes the display with it.

    This cannot be driven the way the other scenarios are. Asserting and
    clearing the fault back-to-back from the host puts an unbounded amount of
    firmware time between the two calls - tens of minutes of wall clock is
    tens of seconds of firmware time - so the glitch would be arbitrarily wide
    and the result would be luck in either direction. The core is paused and
    stepped instead, which makes the width of the glitch a controlled quantity.
    """
    ticks_per_ms = SYS_CLOCK_KHZ
    glitch_ticks = SENSOR_POLL_MS * ticks_per_ms
    calibration_ms = 500

    pump.start_delivery()
    before = pump.telemetry()

    pump.m.pause()
    try:
        # Confirm the tick assumption before relying on it. `step()` counts
        # ticks, which is not documented as one clk_sys cycle - if it is not,
        # every width below is wrong by that factor and the scenario would
        # quietly test nothing.
        pump.m.step(calibration_ms * ticks_per_ms)
        pump.m.wait()
        calibrated = settle_telemetry(pump, before["t"])
        elapsed = calibrated["t"] - before["t"]
        assert calibration_ms / 4 <= elapsed <= calibration_ms * 4, (
            f"stepping {calibration_ms * ticks_per_ms} ticks advanced firmware "
            f"time by {elapsed} ms, not the ~{calibration_ms} ms expected at "
            f"{SYS_CLOCK_KHZ} kHz. A tick is not a clk_sys cycle here, so the "
            f"glitch width cannot be trusted - recalibrate before reading "
            f"anything into this scenario."
        )

        healthy_status = pump.action(pump.stepper, "get_status")
        pump.action(pump.stepper, "inject_fault", "overcurrent")
        faulted_status = pump.action(pump.stepper, "get_status")

        pump.m.step(glitch_ticks)
        pump.m.wait()

        pump.action(pump.stepper, "clear_fault")
    finally:
        pump.m.go()

    # --- the scenario really staged -------------------------------------
    # Not asserted from telemetry: it is 4 Hz, and a 50 ms glitch cannot appear
    # in it. The driver model's own view is the only witness at this width.
    assert faulted_status != healthy_status, (
        f"the stepper model reported the same status faulted as healthy "
        f"({healthy_status!r}), so nFAULT was never actually asserted"
    )

    released = pump.wait_for(lambda t: t["nfault"] == 0,
                             "nFAULT to read released again")

    # --- the requirement -------------------------------------------------
    held, last = quiet(pump, "a one-sample nFAULT glitch")
    if not held:
        pytest.xfail(
            f"nFAULT was asserted for about {SENSOR_POLL_MS} ms of firmware "
            f"time - roughly one sensor poll - and released, and the pump "
            f"latched {alarm_names(last['alarms'])} and suspended "
            f"(mode={MODES[last['mode']]}). safety.c:139-141 raises "
            f"MOTOR_FAULT from a single sample while the pressure and flow "
            f"paths both require {SENSOR_FAIL_LIMIT} consecutive. Whether the "
            f"DRV8825 can glitch this briefly is a hardware question; that the "
            f"firmware cannot tell a glitch from a fault is not."
        )

    assert released["nfault"] == 0, "nFAULT never released"


# ===========================================================================
# Demo 9 - Tandem Mobi "Malfunction 12", Class I 2026, firm-initiated
# 6 October 2025. 17,700+ devices, 281 adverse events, 4 injuries. A software
# issue made the pump incorrectly detect a vibration-motor problem and raise a
# false alert that cut insulin delivery. The pump was not broken; its fault
# detection was.
#
# Demo 8c asks the same question at a 50 ms glitch - about one control period -
# and DEMOS.md is careful to say what that does not prove: 50 ms of nFAULT on
# real silicon is plausibly a genuine overcurrent, so "the firmware latched"
# and "the firmware was wrong to latch" are not the same claim at that width.
#
# This scenario closes that gap by making the glitch narrow enough that the
# ambiguity disappears, and it is the experiment a bench cannot run: staging a
# pulse three orders of magnitude shorter than the poll interval and landing it
# on the poll requires stopping the core at the sampling instruction.
# ===========================================================================


def _nfault_sample_address(cpu):
    """
    The address of the instruction that samples nFAULT.

    Resolved rather than hardcoded. run_tests.sh passes the address of
    drv8825_faulted from the ELF's symbol table; the sampling instruction is
    then found by disassembling forward to the first load of SIO's GPIO_IN,
    which is what makes this survive a rebuild that moves the function or
    changes the register allocation.
    """
    entry = os.environ.get("PUMP_DRV8825_FAULTED_ADDR")
    if not entry:
        pytest.skip(
            "PUMP_DRV8825_FAULTED_ADDR is not set, so the nFAULT sampling "
            "instruction cannot be located. Run this through test/run_tests.sh, "
            "which reads it out of the ELF."
        )

    listing = cpu.get_disassembly(int(entry, 16), 24)
    base_reg = None
    for addr, text in listing:
        if "lui" in text and SIO_BASE_UPPER in text:
            # "lui a5,0xd0000" -> "a5"
            base_reg = text.split()[1].split(",")[0]
        elif base_reg and "lw" in text and f"{SIO_GPIO_IN_OFFSET}({base_reg})" in text:
            return addr, text

    raise AssertionError(
        f"no load of SIO GPIO_IN found in the first {len(listing)} instructions "
        f"of drv8825_faulted at {entry}. The firmware no longer samples nFAULT "
        f"the way this scenario assumes, so it would be staging nothing. "
        f"Disassembly: {listing}"
    )


def _run_to_breakpoint(pump, timeout_s):
    """Resume and block until the breakpoint stops the core again."""
    pump.m.go()
    # go() returns before the core is actually running; without this the
    # is_paused() below reads the state from *before* the resume and the
    # function returns immediately, having stepped nothing.
    time.sleep(1.0)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if pump.m.is_paused():
            return True
        time.sleep(0.2)
    return False


@pytest.mark.slow
def test_a_microsecond_nfault_glitch_suspends_delivery(pump):
    """
    nFAULT asserted for 100 us, landed on one poll, then released.

    Demo 8c's 50 ms glitch shows the motor path takes no second look. It
    cannot show that the firmware was *wrong* to act, because 50 ms of nFAULT
    is a plausible real fault. This one can: 100 us is 1/500th of the control
    period, and no overcurrent trip on a DRV8825 is that brief.

    Staging it needs the core stopped at the sampling instruction, because the
    glitch has to be present at the instant of one poll and absent at every
    other. The sequence is: measure the poll period from two breakpoint hits,
    walk to GLITCH_TICKS before the next one, assert nFAULT, let the core run
    into the sample, release nFAULT immediately after the load retires.

    The pin state has to be asserted *before* the core reaches the load, not
    at it - the device model needs ticks to propagate a level change onto the
    pin, and injecting at the breakpoint itself reads back as still-clear.
    """
    cpu = pump.m.get_device(CPU)
    sample_addr, sample_text = _nfault_sample_address(cpu)

    pump.start_delivery()
    before = pump.telemetry()
    assert before["alarms"] == 0 and before["mode"] == 1, (
        f"the pump was not cleanly delivering before the glitch: {before}"
    )

    pump.m.pause()
    time.sleep(0.5)
    bp = cpu.break_on_address(sample_addr)
    try:
        # --- the poll period, measured rather than assumed ---------------
        assert _run_to_breakpoint(pump, wall(2)), "nFAULT is never sampled"
        first = pump.m.tick_count
        assert _run_to_breakpoint(pump, wall(2)), "nFAULT is sampled only once"
        period = pump.m.tick_count - first

        # This doubles as the tick calibration demo 8c does explicitly: if a
        # tick were not a clk_sys cycle, the period would not land on the
        # control period and every width below would be wrong by that factor.
        expected = SENSOR_POLL_MS * SYS_CLOCK_KHZ
        assert expected * 0.9 <= period <= expected * 1.1, (
            f"nFAULT is sampled every {period} ticks, not the ~{expected} "
            f"expected from a {SENSOR_POLL_MS} ms control period at "
            f"{SYS_CLOCK_KHZ} kHz. Either a tick is not a clk_sys cycle or the "
            f"loop has changed, and the glitch width cannot be trusted."
        )

        # --- walk to just before the next sample, still clear -------------
        cpu.disable_breakpoint(bp.id)
        pump.m.step(period - GLITCH_TICKS)
        pump.m.wait()
        asserted_at = pump.m.tick_count

        healthy = pump.action(pump.stepper, "get_status")
        pump.action(pump.stepper, "inject_fault", "overcurrent")
        faulted = pump.action(pump.stepper, "get_status")

        # --- run into the sample, then release immediately ----------------
        cpu.enable_breakpoint(bp.id)
        assert _run_to_breakpoint(pump, wall(2)), (
            "the core never reached the next nFAULT sample"
        )
        at_sample = pump.m.tick_count

        pump.m.step(8)          # retire the load and the compare behind it
        pump.m.wait()
        sampled = int(cpu.get_register("a0").value)
        pump.action(pump.stepper, "clear_fault", "")
        width_ticks = pump.m.tick_count - asserted_at
    finally:
        cpu.disable_breakpoint(bp.id)
        pump.m.go()

    width_us = width_ticks / (SYS_CLOCK_KHZ / 1000.0)

    # --- the scenario really staged --------------------------------------
    assert faulted != healthy, (
        f"the stepper model reported the same status faulted as healthy "
        f"({healthy!r}), so nFAULT was never asserted at all"
    )
    assert sampled == 1, (
        f"drv8825_faulted() returned {sampled} at {sample_text!r}, so the "
        f"glitch was not present when the firmware sampled the pin and this "
        f"run says nothing about debouncing. The pin needs ticks to settle - "
        f"widen GLITCH_TICKS (currently {GLITCH_TICKS}) before reading "
        f"anything into a zero here."
    )
    assert at_sample - asserted_at <= period // 2, (
        f"nFAULT was asserted {at_sample - asserted_at} ticks before the "
        f"sample, more than half the {period}-tick poll interval - the pulse "
        f"is not the narrow glitch this scenario claims to have staged"
    )
    assert width_us < SENSOR_POLL_MS * 1000 / 10.0, (
        f"the glitch was {width_us:.1f} us wide against a "
        f"{SENSOR_POLL_MS} ms poll interval; that is not narrow enough to "
        f"distinguish a glitch from a fault"
    )

    # --- the requirement --------------------------------------------------
    held, last = quiet(pump, f"a {width_us:.0f} us nFAULT glitch")
    if not held:
        pytest.xfail(
            f"nFAULT was asserted for {width_us:.1f} us - 1/{period // width_ticks} "
            f"of one {SENSOR_POLL_MS} ms control period - and released, and the "
            f"pump latched {alarm_names(last['alarms'])} and suspended "
            f"(mode={MODES[last['mode']]}). Telemetry reports nfault="
            f"{last['nfault']} alongside it, so the pump is stopped for a fault "
            f"that is not present and never appears in the record. "
            f"safety.c:139-141 raises MOTOR_FAULT from a single sample while "
            f"pressure and flow each require {SENSOR_FAIL_LIMIT} consecutive. "
            f"This is Malfunction 12's failure mode: not a broken pump, a "
            f"broken fault detector. Needs a debounce on the motor path."
        )
