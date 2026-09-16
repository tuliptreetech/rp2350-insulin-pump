#!/usr/bin/env bash
#
# Demo 9, driven directly instead of through pytest.
#
# Stages a 100 us glitch on the DRV8825's nFAULT line, lands it on one sensor
# poll, and shows the pump suspend insulin delivery for a fault that is no
# longer present. See DEMOS.md, demo 9.
#
# Attaches to the session that is already running, so the web UI at
# http://localhost:10314 can be open on the OLED and the serial ports while
# this runs. Start one first:
#
#     emerson start && emerson ctl go
#
# Then:  test/glitch_demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# The core has to be stopped at the instruction that samples nFAULT, so the
# script needs that function's address. Read it from the ELF rather than
# pinning it: a rebuild can move it. The Python below disassembles forward
# from here to find the actual load, so only the symbol has to be right.
NFAULT_ADDR=$(nm build/rp2350-insulin-pump.elf 2>/dev/null \
              | awk '$3 == "drv8825_faulted" { print "0x" $1 }')
if [ -z "$NFAULT_ADDR" ]; then
    echo "drv8825_faulted not found in build/rp2350-insulin-pump.elf." >&2
    echo "Build first:  make -C build -j8" >&2
    exit 1
fi

# The firmware in the container has to be the firmware this address came from,
# or the breakpoint lands in the middle of some other function.
local_md5=$(md5 -q build/rp2350-insulin-pump.bin 2>/dev/null \
            || md5sum build/rp2350-insulin-pump.bin | cut -d" " -f1)
container_md5=$(emerson exec md5sum /opt/tuliptree/emerson/projects/rp2350/flash.bin \
                2>/dev/null | cut -d" " -f1 || true)
if [ "$local_md5" != "$container_md5" ]; then
    echo "The container is not running the firmware you just built." >&2
    echo "  build/rp2350-insulin-pump.bin  $local_md5" >&2
    echo "  container flash.bin            ${container_md5:-<unreadable>}" >&2
    echo "Run 'emerson load --reload build/rp2350-insulin-pump.bin' first." >&2
    exit 1
fi

PY=$(mktemp)
trap 'rm -f "$PY"' EXIT
cat > "$PY" <<'PYEOF'
import os
import sys
import time

from emerson import EmulatorController

HOST = "http://localhost:10314"
CPU = "/Hazard3Cpu"
STEPPER = "/MEM/stepper"

SYS_CLOCK_KHZ = 48000          # src/board.h
SENSOR_POLL_MS = 50            # CONTROL_PERIOD_US in src/main.c
GLITCH_US = 100
GLITCH_TICKS = GLITCH_US * (SYS_CLOCK_KHZ // 1000)

ALARMS = ["MOTOR_FAULT", "OCCLUSION", "AIR_IN_LINE", "UNDER_DELIVERY",
          "PRESSURE_SENSOR", "FLOW_SENSOR", "RESERVOIR_EMPTY", "HOUR_LIMIT",
          "RESERVOIR_LOW"]
MODES = {0: "stopped", 1: "running", 2: "SUSPENDED"}


def say(label, text):
    print("  %-16s %s" % (label, text), flush=True)


def telemetry(m):
    txt = bytes(m.get_broker_history("tty1")).decode("utf-8", "replace")
    last = None
    for line in txt.splitlines():
        if line.startswith("t=") and "alarms=" in line:
            rec = {}
            for field in line.split():
                key, _, value = field.partition("=")
                rec[key] = int(value, 16) if key == "alarms" else value
            last = rec
    return last


def describe(rec):
    names = [n for i, n in enumerate(ALARMS) if rec["alarms"] & (1 << i)]
    return "mode=%-9s alarms=0x%03x%-15s nfault=%s" % (
        MODES[int(rec["mode"])], rec["alarms"],
        (" [" + ",".join(names) + "]") if names else "", rec["nfault"])


def wait_for(m, pred, what, timeout_s=400):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = telemetry(m)
        if last and pred(last):
            return last
        time.sleep(0.5)
    sys.exit("timed out waiting for %s; last telemetry: %s" % (what, last))


def run_to_breakpoint(m, timeout_s=400):
    m.go()
    # go() returns before the core is actually running again; without this the
    # is_paused() below reads the state from before the resume.
    time.sleep(1.0)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if m.is_paused():
            return True
        time.sleep(0.2)
    return False


def find_sample_instruction(cpu, entry):
    """The load of SIO GPIO_IN inside drv8825_faulted() - the nFAULT sample."""
    listing = cpu.get_disassembly(entry, 24)
    base = None
    for addr, text in listing:
        if "lui" in text and "0xd0000" in text:
            base = text.split()[1].split(",")[0]
        elif base and "lw" in text and "0x4(%s)" % base in text:
            return addr, text
    sys.exit("no load of SIO GPIO_IN found in drv8825_faulted; firmware "
             "no longer samples nFAULT the way this demo assumes:\n%s" % listing)


entry = int(os.environ["PUMP_DRV8825_FAULTED_ADDR"], 16)

with EmulatorController(HOST).connect() as conn:
    sessions = conn.get_instance_list()
    if not sessions:
        sys.exit("no emerson session is running. Start one with:\n"
                 "    emerson start && emerson ctl go")
    session_id = sessions[0][0]

    with conn.attach(session_id) as m:
        cpu = m.get_device(CPU)
        stepper = m.get_device(STEPPER)
        sample_addr, sample_text = find_sample_instruction(cpu, entry)

        if not m.is_paused():
            pass
        else:
            m.go()

        print()
        print("  Demo 9 - a 100 us glitch on nFAULT")
        print("  " + "-" * 62)
        say("sampled at", "%s   %s" % (hex(sample_addr), sample_text))

        wait_for(m, lambda t: True, "the firmware's first telemetry record")
        before = telemetry(m)
        if int(before["mode"]) == 0:
            m.send_to_broker("tty0", b"run\r")
            before = wait_for(m, lambda t: int(t["mode"]) == 1, "basal delivery")
        if before["alarms"]:
            sys.exit("the pump is already alarmed (%s). Alarms latch - take a "
                     "fresh session:\n    emerson stop && emerson start && "
                     "emerson ctl go" % describe(before))

        say("pump before", describe(before))

        m.pause()
        time.sleep(0.5)
        bp = cpu.break_on_address(sample_addr)
        try:
            # Two hits give the poll period. Measured, not assumed: if a tick
            # were not a clk_sys cycle every width below would be wrong by
            # that factor.
            if not run_to_breakpoint(m):
                sys.exit("nFAULT is never sampled - is the firmware running?")
            first = m.tick_count
            if not run_to_breakpoint(m):
                sys.exit("nFAULT is sampled only once")
            period = m.tick_count - first
            expected = SENSOR_POLL_MS * SYS_CLOCK_KHZ
            if not expected * 0.9 <= period <= expected * 1.1:
                sys.exit("nFAULT sampled every %d ticks, not the ~%d expected "
                         "from a %d ms control period at %d kHz - a tick is not "
                         "a clk_sys cycle here and the glitch width cannot be "
                         "trusted" % (period, expected, SENSOR_POLL_MS,
                                      SYS_CLOCK_KHZ))
            say("poll period", "%s ticks = %.2f ms   (1 tick = 1 clk_sys cycle)"
                % ("{:,}".format(period), period / float(SYS_CLOCK_KHZ)))

            # Walk to GLITCH_TICKS before the next sample, line still clear.
            cpu.disable_breakpoint(bp.id)
            m.step(period - GLITCH_TICKS)
            m.wait()
            asserted_at = m.tick_count

            # Assert nFAULT *before* the sample, not at it: the device model
            # needs ticks to settle a level change onto the pin, and injecting
            # at the breakpoint itself reads back as still-clear.
            healthy = stepper.invoke_action("get_status", "")
            stepper.invoke_action("inject_fault", "overcurrent")
            faulted = stepper.invoke_action("get_status", "")
            if faulted == healthy:
                sys.exit("the stepper model reported the same status faulted as "
                         "healthy, so nFAULT was never asserted at all")
            say("nFAULT asserted", "%d us before the sample" % GLITCH_US)

            cpu.enable_breakpoint(bp.id)
            if not run_to_breakpoint(m):
                sys.exit("the core never reached the next nFAULT sample")

            m.step(8)          # retire the load and the compare behind it
            m.wait()
            sampled = int(cpu.get_register("a0").value)
            stepper.invoke_action("clear_fault", "")
            width = m.tick_count - asserted_at
        finally:
            cpu.disable_breakpoint(bp.id)
            m.go()

        width_us = width / (SYS_CLOCK_KHZ / 1000.0)
        say("at the sample", "drv8825_faulted() returned %d%s"
            % (sampled, "" if sampled else "  <-- the glitch was missed"))
        say("glitch width", "%.1f us  =  1/%d of one %d ms control period"
            % (width_us, period // width, SENSOR_POLL_MS))

        if not sampled:
            sys.exit("\n  The pin had not settled by the sample, so nothing was "
                     "staged.\n  Widen GLITCH_US and run again.")

        print()
        say("waiting", "for the next telemetry record...")
        after = wait_for(m, lambda t: t["alarms"] or int(t["mode"]) != 1,
                         "the pump to react", timeout_s=120)
        print()
        say("pump after", describe(after))
        print()
        if after["alarms"] and after["nfault"] == "0":
            print("  The pump has suspended insulin delivery for a motor fault.")
            print("  The same record reports nfault=0: there is no motor fault,")
            print("  and there was one for %.1f us. It appears nowhere in the log."
                  % width_us)
            print()
            print("  safety.c:139-141 raises MOTOR_FAULT from a single sample.")
            print("  The pressure and flow paths each require ten consecutive.")
        elif not after["alarms"]:
            print("  The pump rejected the glitch - the motor path has a debounce.")
        print()
PYEOF

emerson exec sh -c "PUMP_DRV8825_FAULTED_ADDR='$NFAULT_ADDR' python3 -" < "$PY"
