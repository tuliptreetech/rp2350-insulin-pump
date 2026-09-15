"""
Recovery scenarios: the demos in DEMOS.md, as assertions.

test_pump.py covers each alarm being *raised* - the fault goes in, the pump
suspends. This file covers what happens afterwards, which is the half of every
demo that an audience actually asks about: the blockage is cleared, the dose is
re-commanded, and the pump has to end up delivering correctly again.

One test per demo in DEMOS.md, in the same order, with the pressure-sensor
half of demo 5 left where it already lived as
`test_pump.py::test_recovery_requires_the_fault_to_clear`.

Two things here are contracts that are easy to "tidy up" into bugs:

- A suspended bolus is discarded, not paused. Recovery delivers a *new* dose.
- `ack` behaves differently depending on whether the firmware can test the
  condition at the moment you acknowledge it. Conditions it can check are
  withheld and reported in the reply; the rest are dropped and re-raised by
  the next safety pass. Both end up suspended, and only one says so.

Timing. These are the most expensive scenarios in the suite because each one
runs a fault *and* its recovery. Judging state after an `ack` is done by
waiting for a telemetry record that provably postdates it - telemetry is 4 Hz
in firmware time, which at emulated speed is 15-45 wall seconds between
records, so a sleep-then-read can easily sample the state from before the
command and read it as the state after.
"""

from pump import (
    ALARMS, MODES, MU_PER_MICROSTEP, OCCLUSION_MILLIPSI, alarm_names, wall,
)


def test_occlusion_recovery_completes_a_fresh_bolus(pump):
    """
    The whole recovery arc: a blocked line abandons a bolus, and once the
    blockage is gone a newly commanded dose is delivered in full.

    The abandoned bolus must not restart by itself. `pump_suspend()` discards
    it, and how much of a dose has already gone in is a judgement for the
    person holding the pump rather than for the firmware.
    """
    pump.start_delivery()
    pump.action(pump.pressure, "set_occluded", "true")
    pump.send_expect("bolus 8.0", "ok bolus 8.000 U")

    suspended = pump.wait_alarm("OCCLUSION", timeout_s=wall(8))
    assert suspended["mode"] == 2, \
        f"pump did not suspend, mode={MODES[suspended['mode']]}"
    assert suspended["bolus_total_mu"] == 0, "the blocked bolus was not abandoned"

    pump.action(pump.pressure, "set_occluded", "false")
    pump.wait_for(lambda t: t["psi_m"] < OCCLUSION_MILLIPSI,
                  "the line to vent once the blockage was removed")

    # No `run` here, deliberately: a successful acknowledge resumes delivery
    # on its own, and a `run` would hide it if that ever stopped being true.
    pump.send_expect("ack", "ok alarms=0x000")
    resumed = pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                            "delivery to resume once the line was clear")
    assert resumed["bolus_total_mu"] == 0, "the abandoned bolus restarted on its own"

    start = pump.telemetry()
    pump.send_expect("bolus 1.0", "ok bolus 1.000 U")
    expected_steps = start["steps"] + 1000 // MU_PER_MICROSTEP
    end = pump.wait_for(
        lambda t: t["bolus_total_mu"] == 0 and t["steps"] >= expected_steps,
        "the fresh bolus to finish", timeout_s=wall(4))

    delivered = end["steps"] - start["steps"]
    assert delivered == 200, f"expected 200 microsteps, got {delivered}"
    assert end["total"] - start["total"] == 1000, \
        "delivery total does not match the dose commanded after recovery"
    assert end["alarms"] == 0, \
        f"the recovery bolus raised alarms: {alarm_names(end['alarms'])}"

    model = pump.stepper_microsteps()
    assert model == end["steps"], \
        f"driver model counted {model} microsteps, firmware thinks {end['steps']}"


def test_ack_of_a_live_motor_fault_relatches(pump):
    """
    An `ack` the firmware cannot test is answered optimistically, then undone.

    nFAULT is a level that `safety_acknowledge()` does not consult, so the
    latch is dropped, the reply says the mask is empty, and the next safety
    pass raises it again from the pin. The end state is the contract - still
    alarming, still suspended - but the console reply on its own is not
    evidence of recovery. Worth pinning down, because it is the difference
    between this alarm and the ones the acknowledge path can check.
    """
    bit = 1 << ALARMS.index("MOTOR_FAULT")
    pump.start_delivery()
    pump.action(pump.stepper, "inject_fault", "overcurrent")
    before = pump.wait_alarm("MOTOR_FAULT")["t"]

    pump.send_expect("ack", "ok alarms=0x000")

    # Judge on a record that cannot predate the ack. Telemetry is 4 Hz in
    # firmware time, so two records is 500 ms of it - a wait that is cheap in
    # firmware time and a minute or so of wall clock.
    t = pump.wait_for(lambda x: x["t"] >= before + 500,
                      "telemetry from after the acknowledge")
    assert t["nfault"] == 1, "nFAULT released itself"
    assert t["alarms"] & bit, \
        "motor fault stayed cleared while nFAULT was still asserted"
    assert t["mode"] == 2, f"pump resumed with a live driver fault, mode={MODES[t['mode']]}"

    pump.action(pump.stepper, "clear_fault")
    pump.wait_for(lambda t: t["nfault"] == 0, "nFAULT to be released")
    pump.send_expect("ack", "ok alarms=0x000")
    pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                  "recovery once the driver fault was cleared")


def test_ack_withholds_a_condition_it_can_still_see(pump):
    """
    For conditions it *can* test, `ack` refuses outright and says so.

    A sensor's own fail counter is available to `safety_acknowledge()`, so a
    flow sensor still failing its CRC is never dropped from the latch and the
    reply carries the alarm still set - unlike the motor fault above, which is
    cleared and re-raised. Both end up suspended; only one admits it in the
    reply, and a demo that reads the console aloud needs to know which.
    """
    bit = 1 << ALARMS.index("FLOW_SENSOR")
    pump.start_delivery()
    pump.action(pump.flow, "set_crc_error_period", 1)
    before = pump.wait_alarm("FLOW_SENSOR")["t"]

    pump.send_expect("ack", f"ok alarms=0x{bit:03x}")

    t = pump.wait_for(lambda x: x["t"] >= before + 500,
                      "telemetry from after the acknowledge")
    assert t["alarms"] & bit, "flow sensor alarm cleared while the sensor was broken"
    assert t["mode"] == 2, f"pump resumed with a broken flow sensor, mode={MODES[t['mode']]}"

    pump.action(pump.flow, "set_crc_error_period", 0)
    pump.wait_for(lambda t: t["flow_st"] == 0, "the flow sensor answering cleanly again")
    pump.send_expect("ack", "ok alarms=0x000")
    pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                  "recovery once the sensor was repaired")


def test_air_in_line_recovers_once_the_bubble_passes(pump):
    """
    A bubble is an alarm the acknowledge path cannot test, so it behaves like
    the motor fault: acknowledged optimistically while the air is still there,
    and re-raised from the sensor flag on the next pass. Once the line is
    liquid again the same command sticks and delivery resumes.
    """
    bit = 1 << ALARMS.index("AIR_IN_LINE")
    pump.start_delivery()
    pump.action(pump.flow, "set_air_in_line", "true")
    before = pump.wait_alarm("AIR_IN_LINE")["t"]

    pump.send_expect("ack", "ok alarms=0x000")
    t = pump.wait_for(lambda x: x["t"] >= before + 500,
                      "telemetry from after the acknowledge")
    assert t["air"] == 1, "the air-in-line flag cleared itself"
    assert t["alarms"] & bit, "air alarm stayed cleared with the bubble still present"
    assert t["mode"] == 2, f"pump resumed with air in the line, mode={MODES[t['mode']]}"

    pump.action(pump.flow, "set_air_in_line", "false")
    pump.wait_for(lambda t: t["air"] == 0, "the bubble to pass")
    pump.send_expect("ack", "ok alarms=0x000")
    pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                  "delivery to resume once the line was liquid again")


def test_under_delivery_recovers_and_reverifies(pump):
    """
    Recovering from NO DELIVERY has to re-arm the delivery check, not just
    clear the alarm: the next bolus must be verified as well as delivered.

    So this does not stop at `mode == 1`. It commands a fresh dose with the
    flow sensor working and requires it to complete without alarming, which is
    the only evidence that verification came back armed rather than disabled.
    """
    pump.start_delivery()
    pump.action(pump.flow, "set_flow_scale_error_percent", -100)
    pump.send_expect("bolus 1.5", "ok bolus 1.500 U")

    suspended = pump.wait_alarm("UNDER_DELIVERY")
    assert suspended["mode"] == 2, \
        f"pump did not suspend, mode={MODES[suspended['mode']]}"
    assert suspended["psi_m"] < OCCLUSION_MILLIPSI, \
        "pressure was high, so this should have been diagnosed as an occlusion"
    assert suspended["bolus_total_mu"] == 0, "the unverified bolus was not abandoned"

    pump.action(pump.flow, "set_flow_scale_error_percent", 0)
    pump.wait_for(lambda t: t["flow_st"] == 0, "the flow sensor reading normally again")
    pump.send_expect("ack", "ok alarms=0x000")
    pump.wait_for(lambda t: t["alarms"] == 0 and t["mode"] == 1,
                  "delivery to resume once the mechanism was coupled again")

    start = pump.telemetry()
    pump.send_expect("bolus 1.0", "ok bolus 1.000 U")
    expected_steps = start["steps"] + 1000 // MU_PER_MICROSTEP
    end = pump.wait_for(
        lambda t: t["bolus_total_mu"] == 0 and t["steps"] >= expected_steps,
        "the verified bolus to finish", timeout_s=wall(4))

    assert end["steps"] - start["steps"] == 200, \
        f"expected 200 microsteps, got {end['steps'] - start['steps']}"
    assert end["alarms"] == 0, \
        f"a bolus with a working flow sensor still alarmed: {alarm_names(end['alarms'])}"
