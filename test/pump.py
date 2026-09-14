"""
Driving the insulin pump firmware from inside the emerson-server container.

This runs against the `emerson` Python bindings rather than shelling out to
`emerson ctl`, which matters for two reasons: polling costs a local call
instead of a `docker exec` round trip, so scenarios can watch for a condition
at 2 Hz without the harness itself becoming the bottleneck; and each test can
hold its own session, so the suite runs in parallel.
"""

import time

CONSOLE = "tty0"

STEPPER = "/MEM/stepper"
FLOW = "/MEM/i2c0/slf3x"
PRESSURE = "/MEM/adc/abp_pressure"
OLED = "/MEM/i2c0/ssd1306"

# Alarm bit positions, mirroring pump_alarm_t in src/app/safety.h.
ALARMS = [
    "MOTOR_FAULT",
    "OCCLUSION",
    "AIR_IN_LINE",
    "UNDER_DELIVERY",
    "PRESSURE_SENSOR",
    "FLOW_SENSOR",
    "RESERVOIR_EMPTY",
    "HOUR_LIMIT",
    "RESERVOIR_LOW",
]

MODES = {0: "stopped", 1: "running", 2: "suspended"}

# Mirrors src/board.h and src/app/safety.c.
MU_PER_MICROSTEP = 5
OCCLUSION_MILLIPSI = 4000

# The ADC codes outside which the pressure sensor's output is not a reading.
PRESSURE_CODE_MIN = 102
PRESSURE_CODE_MAX = 3993


# Wall-clock seconds per second of *firmware* time, with headroom.
#
# Measured on the RP2350 image with the pump's 48 MHz clk_sys: a session
# running alone covers a firmware second in about 94 wall seconds, and four
# running at once - the default for the suite - contend to about 282. Every
# timeout below is budgeted as "how many firmware seconds does this step need"
# and converted through this constant, sized for the contended case with a
# margin, not for a session measured on its own.
#
# Getting it wrong is not a flaky test, it is a misleading one: the occlusion
# scenario once failed 50 ms of firmware time short of the alarm it was waiting
# for, which reads exactly like the firmware never alarming.
#
# Re-measure after changing BOARD_SYS_CLOCK_KHZ - this tracks it. At the SDK
# default of 150 MHz the contended figure was 506.
WALL_PER_FIRMWARE_SECOND = 340


def wall(firmware_seconds):
    """Wall-clock timeout for a step needing `firmware_seconds` of firmware time."""
    return int(firmware_seconds * WALL_PER_FIRMWARE_SECOND)


class Timeout(Exception):
    pass


def alarm_names(mask):
    return [n for i, n in enumerate(ALARMS) if mask & (1 << i)]


def _parse_record(line):
    out = {}
    for field in line.split():
        key, _, value = field.partition("=")
        if key == "alarms":
            out[key] = int(value, 16)
        elif key == "bolus":
            done, _, total = value.partition("/")
            out["bolus_delivered_mu"] = int(done)
            out["bolus_total_mu"] = int(total)
        else:
            try:
                out[key] = int(value)
            except ValueError:
                out[key] = value
    return out


class Pump:
    """One booted pump on one emulator session."""

    def __init__(self, machine):
        self.m = machine
        self.stepper = machine.get_device(STEPPER)
        self.flow = machine.get_device(FLOW)
        self.pressure = machine.get_device(PRESSURE)
        self.oled = machine.get_device(OLED)

    # ---- lifecycle ------------------------------------------------------

    def boot(self, timeout_s=wall(2)):
        """Run the machine and wait for the firmware's first telemetry record."""
        self.m.go()
        self.wait_for(lambda t: True, "the firmware's first telemetry record",
                      timeout_s=timeout_s)

    def start_delivery(self):
        self.send_expect("run", "ok running")
        self.wait_for(lambda t: t["mode"] == 1, "basal delivery running")

    # ---- serial console -------------------------------------------------

    def console(self):
        """
        Everything the firmware has printed so far.

        `get_broker_history` rather than `read_from_broker`: the latter has
        been seen returning nothing for data that was already present.
        """
        return bytes(self.m.get_broker_history(CONSOLE)).decode("utf-8", "replace")

    def send(self, line):
        self.m.send_to_broker(CONSOLE, (line + "\r").encode())

    def wait_console(self, text, since=0, timeout_s=wall(1), poll_s=0.5):
        """
        Wait for `text` in the transcript past offset `since`.

        The offset matters: the broker hands back everything ever printed, so
        without it a reply left over from an earlier command satisfies the
        wait immediately.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            tail = self.console()[since:]
            if text in tail:
                return tail
            time.sleep(poll_s)
        raise Timeout(f"console never printed {text!r}")

    def send_expect(self, line, expect, timeout_s=wall(1)):
        """
        Send one command and wait for its reply before returning.

        Commands are never pipelined. The UART receive FIFO holds 32 bytes and
        the firmware drains it once per control period, so back-to-back
        commands could be truncated - and a dose that loses its decimal point
        is a tenfold overdose, which is not a failure mode a test harness
        should be able to create by accident.
        """
        before = len(self.console())
        self.send(line)
        return self.wait_console(expect, since=before, timeout_s=timeout_s)

    # ---- telemetry ------------------------------------------------------

    def telemetry_history(self, since_ms=0):
        """
        Every telemetry record printed since `since_ms` of firmware time.

        Scenarios that assert on a transient - flow rate mid-bolus, peak line
        pressure before the pump shut down - have to look at the record rather
        than at one sample, because the interesting moment is usually already
        over by the time the host looks.
        """
        records = []
        for line in self.console().splitlines():
            if line.startswith("t=") and "alarms=" in line:
                r = _parse_record(line)
                if r["t"] >= since_ms:
                    records.append(r)
        return records

    def telemetry(self):
        records = self.telemetry_history()
        return records[-1] if records else None

    def wait_for(self, predicate, what, timeout_s=wall(4), poll_s=0.5):
        """
        Poll until `predicate(telemetry)` holds.

        Timeouts are in wall-clock seconds and are generous because Emerson
        runs the core well below real time - see the note at the top of
        test_pump.py.
        """
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            last = self.telemetry()
            if last is not None and predicate(last):
                return last
            time.sleep(poll_s)
        raise Timeout(f"timed out waiting for {what}; last telemetry: {last}")

    def wait_alarm(self, name, timeout_s=wall(4)):
        bit = 1 << ALARMS.index(name)
        return self.wait_for(lambda t: t["alarms"] & bit, f"the {name} alarm",
                             timeout_s=timeout_s)

    # ---- devices --------------------------------------------------------

    @staticmethod
    def action(device, verb, *args):
        return device.invoke_action(verb, " ".join(str(a) for a in args))

    def stepper_microsteps(self):
        # "1234 microsteps (0.0617 mL dispensed)"
        return int(self.action(self.stepper, "get_position").split()[0])

    def dump_display(self):
        return self.action(self.oled, "dump_display")

    def wait_display(self, text, timeout_s=wall(3), poll_s=2):
        """
        Poll the panel until `text` appears on it.

        The OLED redraws at 2 Hz of *firmware* time, which here is well over a
        minute of wall clock per frame. Telemetry updates eight times more
        often, so a scenario that waits on telemetry and then samples the panel
        once is reading a frame drawn before the event it is checking for.
        """
        import display

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            dump = self.dump_display()
            if display.contains_text(dump, text):
                return dump
            time.sleep(poll_s)
        raise Timeout(f"panel never showed {text!r}")

    def reset_fault_injection(self):
        """Put every simulated part back in its healthy state."""
        self.action(self.pressure, "set_output_fault", "none")
        self.action(self.pressure, "set_occluded", "false")
        self.action(self.pressure, "set_zero_offset_psi", 0)
        self.action(self.pressure, "set_span_error_percent", 0)
        self.action(self.pressure, "set_leak_rate_psi_per_s", 0)
        self.action(self.flow, "set_air_in_line", "false")
        self.action(self.flow, "set_crc_error_period", 0)
        self.action(self.flow, "set_flow_scale_error_percent", 0)
        self.action(self.stepper, "clear_fault")
        self.action(self.stepper, "set_step_slip", 0)
