# RP2350 insulin pump and monitor

Firmware for a syringe-driver insulin pump built on an RP2350 (Pico 2), with a
hardware model of the same board for the [Emerson](.claude/skills/emerson)
SoC simulator so the safety behaviour can be exercised without a bench.

## Hardware

| Part | Role | Interface |
| --- | --- | --- |
| TI DRV8825 | microstepping driver for the syringe lead screw | STEP `GP2`, DIR `GP3`, nFAULT `GP6` |
| Honeywell ABP (0-15 psi gauge) | line pressure, for occlusion detection | ADC1 / `GP27` |
| Sensirion SLF3S-1300F | inline liquid flow, the independent delivery check | I2C0 @ `0x08` |
| SSD1306 128x64 OLED | patient display | I2C0 @ `0x3C` |

`clk_sys` runs at 48 MHz rather than the SDK's 150: the workload is 20 Hz
sensor polling, a 2 Hz display redraw and step pulses capped at 2 kHz, so the
rest is battery life spent for nothing. I2C0 is `GP4`/`GP5` at 400 kHz. [`src/board.h`](src/board.h) is the single
source of truth for the pin map and the mechanism constants, and
[`.emerson/peripherals.yaml`](.emerson/peripherals.yaml) describes the same
wiring to the simulator. Change one and change the other.

## How delivery is counted

One microstep moves 0.05 µL of fluid. U-100 insulin is 10 µL per unit, so a
microstep is exactly **5 milliunits** and every dose is a whole number of
microsteps. Basal scheduling accumulates demand in integer
milliunit-milliseconds and emits a microstep per 18,000,000 of them, so a rate
that is not a whole number of microsteps per hour still delivers exactly right
over a long run — there is no floating-point residue anywhere in the delivery
path.

Delivery totals are reconciled from microsteps the driver **actually pulsed**,
not from what was queued. A dose aborted mid-flight is therefore billed only
for the part that moved.

## Safety model

The delivery engine ([`src/app/pump.c`](src/app/pump.c)) is deliberately
credulous: it commands microsteps and believes them. The safety layer
([`src/app/safety.c`](src/app/safety.c)) is the part that does not, and it is
the only thing that can stop the mechanism.

| Alarm | Detected by | Stops delivery |
| --- | --- | --- |
| `MOTOR FAULT` | DRV8825 nFAULT asserted | yes |
| `OCCLUSION` | line pressure ≥ 4 psi for 2 s | yes |
| `AIR IN LINE` | SLF3x air-in-line flag | yes |
| `NO DELIVERY` | flow sensor saw under half the commanded volume | yes |
| `PRESS SENSOR` | ABP output outside its 2.5–97.5 % diagnostic band | yes |
| `FLOW SENSOR` | NACK or persistent CRC failure on the SLF3x | yes |
| `RESERVOIR EMPTY` | reservoir exhausted | yes |
| `HOURLY LIMIT` | rolling 60-minute delivery cap reached | yes |
| `RESERVOIR LOW` | ≤ 20 U remaining | no, advisory |

Dose envelope: 5.000 U/hr basal, 25 U single bolus, 30 U in any rolling hour.

The occlusion threshold is derived, not picked: the set builds about
0.2 psi/µL against a blockage and sits at 0.03 psi when clear, so every 1 psi
of threshold is half a unit pumped into a line that is going nowhere. 4 psi is
130× the working pressure of a clear line and costs about 2 U to reach.

The dwell costs the same again: holding the threshold for 2 s at the bolus
rate of 1 U/s is another 2 U, so a bolus into a blocked line loses roughly
4 U, not 2, and the pressure overshoots to about twice the threshold before
the pump acts — 8.1 psi measured against a 4 psi trip. That is the price of
not alarming on the pressure spike a bubble or a bumped syringe also makes,
and it is bounded: the mechanism stops, so the line goes no higher.

Alarms **latch**. `ack` clears only those whose underlying condition has
actually gone away; anything still true stays latched and delivery stays
suspended. "It fixed itself" is not a judgement a pump gets to make on a
patient's behalf.

A few design points worth knowing:

- **The flow sensor cannot see basal.** One count on the SLF3S-1300F is about
  2 µL/min and 1 U/hr is 0.17 µL/min, so delivery verification arms only
  during a bolus, once 1 U has been commanded. Basal is verified by the
  pressure sensor and the driver's fault line, not by flow.
- **Basal and bolus are serialised.** There is one mechanism, so basal demand
  accrues in the accumulator during a bolus and is paid out when the bolus
  finishes. It is held back, never dropped.
- **A suspended bolus is abandoned, not resumed.** `pump_suspend()` zeroes the
  outstanding dose, so clearing the fault and acknowledging it returns the pump
  to delivering basal — the interrupted bolus has to be re-commanded, by
  someone who has looked at how much of it already went in. Resuming a dose
  automatically after a fault is a decision for the person, not the firmware.
- **The specific diagnosis wins.** A blocked line stops the flow too, so
  `NO DELIVERY` is suppressed for any bolus during which the line pressure
  rose — `OCCLUSION` says what to go and fix, and the two rules must not race
  to see which threshold trips first. This cannot mask what `NO DELIVERY`
  exists to catch: a mechanism uncoupled from the syringe moves no fluid and
  therefore builds no pressure.

## Service console and telemetry

Two separate serial ports, because they serve two different audiences:

| Port | Broker | Carries |
| --- | --- | --- |
| `uart0` (stdio) | `tty0` | the interactive console a person types at |
| `uart1` (`GP8`) | `tty1` | one machine-readable telemetry record, 4 Hz |

Sharing one port makes both worse — the log scrolls the operator's reply out of
view, and anything they type lands in the middle of a record a parser is trying
to read.

The console echoes what you type, supports backspace, and prompts with
`pump> `. Commands: `run`, `stop`, `basal <U/hr>`, `bolus <U>`, `cancel`,
`ack`, `reservoir`, `status`, `help`. Anything not fully understood is refused
rather than guessed at.

This is a **service and test port**, not the patient interface — a shipping
pump would put dosing behind buttons and a confirmation step. The patient
interface is the OLED.

## Driving it by hand

Emerson's web UI at <http://localhost:10314> shows the OLED and gives you a
terminal on the serial ports, which is the easiest way to drive the pump
interactively. From the shell:

```sh
emerson ctl broker tty0 $'status\r'     # type a command
emerson ctl broker tty0                 # read the console
emerson ctl broker tty1                 # read telemetry
emerson ctl action /MEM/i2c0/ssd1306 dump_display
```

Fault injection is per device; `emerson ctl actions <path>` lists what each
model can be made to do:

```sh
emerson ctl action /MEM/stepper inject_fault overcurrent        # MOTOR FAULT
emerson ctl action /MEM/i2c0/slf3x set_air_in_line true         # AIR IN LINE
emerson ctl action /MEM/i2c0/slf3x set_crc_error_period 1       # FLOW SENSOR
emerson ctl action /MEM/adc/abp_pressure set_output_fault open  # PRESS SENSOR
emerson ctl action /MEM/adc/abp_pressure set_occluded true      # needs a bolus running
```

Measured latencies at 48 MHz on an idle server, so you know what to expect:
boot to first telemetry 21 s; a console command and its reply 3-4 s; motor or
air-in-line alarm 11 s, with the panel banner 16 s later; sensor-fault alarms
30-60 s (they need ten consecutive bad samples); a 1 U bolus about 90 s; and an
occlusion about 4½ minutes from the `bolus` command, since the line has to
build pressure and then hold it through the dwell. Expect the occlusion to take
longer when other sessions are running — it is the one alarm long enough for
the contention in the table below to show.

The recovery sequence is the most interesting thing to show. The quick version,
on the motor fault:

```sh
emerson ctl action /MEM/stepper inject_fault overcurrent
emerson ctl broker tty0 $'ack\r'          # appears to clear, then re-latches
emerson ctl action /MEM/stepper clear_fault
emerson ctl broker tty0 $'ack\r'          # clears, and delivery resumes
```

Note what the first `ack` actually does, because it is easy to describe
wrongly: it is not refused. `safety_acknowledge()` drops the latch, the next
safety pass sees the fault is still asserted and raises it again, so the
console prints `ok alarms=0x000` and the telemetry still reads `alarms=0x001`
a moment later. The mask in the reply is what was left at that instant, not a
promise about the next one. Alarms whose condition the ack can test directly —
occlusion, both sensor faults, reservoir-empty, hour-limit — are held back in
the reply instead and never clear at all.

A successful `ack` also resumes delivery by itself (`pump_resume()`, at the end
of `safety_acknowledge()`), so the `run` you might expect to need afterwards is
redundant.

The occlusion tells the better story, because the pump has to stop mid-dose.
End to end this is about 7 minutes of wall clock — [DEMOS.md](DEMOS.md) has
this one and the motor fault written out as rehearsed walkthroughs, with the
measured timings and the places it is easy to misnarrate:

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/adc/abp_pressure set_occluded true
emerson ctl broker tty0 $'bolus 8.0\r'    # oversized: the line needs room to build

# ~4.5 min: watch psi_m climb past 4000 and the pump suspend itself
emerson ctl broker tty1 | tail -1         # mode=2 alarms=0x002 psi_m=8086
emerson ctl action /MEM/i2c0/ssd1306 dump_display   # SUSPENDED / OCCLUSION

emerson ctl action /MEM/adc/abp_pressure set_occluded false
emerson ctl broker tty0 $'ack\r'          # clears first try, and resumes
emerson ctl broker tty0 $'bolus 1.0\r'    # the abandoned dose does not resume
```

Two things to know before doing this in front of anyone. The line vents the
moment `set_occluded` goes false — pressure was back to zero within one
telemetry poll — so `ack` is accepted immediately, unlike the sensor faults,
where you have to wait for a healthy reading before it will clear. And the 8 U
bolus is deliberately oversized: about 4 U goes in before the alarm and the
rest is never delivered, so the dose you re-command afterwards is a fresh one.

## Building

```sh
cmake -S . -B build          # RP2350, RISC-V (Hazard3) toolchain
make -C build -j8
```

## Running it in the simulator

```sh
emerson load build/rp2350-insulin-pump.bin   # first time, or to change firmware
emerson start                                # after each rebuild
emerson ctl go

emerson ctl broker tty0 'run
'                                            # type a command at the console
emerson ctl broker tty0                      # read telemetry
emerson ctl action /MEM/i2c0/ssd1306 dump_display
```

Fault injection is per device — `emerson ctl actions <path>` lists what each
model can be made to do:

```sh
emerson ctl action /MEM/adc/abp_pressure set_occluded true
emerson ctl action /MEM/i2c0/slf3x set_air_in_line true
emerson ctl action /MEM/stepper inject_fault overcurrent
emerson ctl action /MEM/i2c0/slf3x set_crc_error_period 1
```

## Tests

```sh
test/run_tests.sh                    # the fault-injection suite, in parallel
test/run_tests.sh --runslow          # adds the slow basal-rate scenario
test/run_tests.sh -k occlusion       # one scenario
test/run_tests.sh -k 'ack or recovery'   # compound selections work too
PUMP_TEST_WORKERS=4 test/run_tests.sh
```

Two files:

| File | Covers |
| --- | --- |
| [`test/test_pump.py`](test/test_pump.py) | delivery accounting, the dose envelope, and every alarm being **raised** |
| [`test/test_demos.py`](test/test_demos.py) | what happens **afterwards** — clearing the fault, acknowledging it, and delivering again |

The split is by half of the story rather than by feature: a fault that stops
the pump and a pump that comes back afterwards fail for different reasons and
cost very different amounts to run. The recovery scenarios are also the ones
walked through in [DEMOS.md](DEMOS.md), so they double as a check that the
demos still do what that document says they do.

Each scenario drives the firmware through its serial console, injects a
hardware fault through Emerson's device models, and asserts on three
independent views of the result: the microsteps the **driver model** counted,
the delivery totals the **firmware** reports, and the text actually rendered on
the **simulated OLED** (matched against the firmware's own font table, parsed
out of `font5x7.c`, so the assertion cannot drift from the glyphs it draws).

### How it runs

The tests use the `emerson` Python bindings, not `emerson ctl`. That package
only exists inside the `emerson-server` container and the project tree is not
mounted there, so `run_tests.sh` ships the suite in over stdin as a tar and
runs pytest from `/tmp/pumptest`.

**Every test gets its own freshly booted emulator session**, and the tests run
concurrently under `pytest-xdist`.

Concurrency helps, but far less than linearly — measured on this image:

| sessions | aggregate ticks/s | per-session | vs 1 session |
| --- | --- | --- | --- |
| 1 | 714,044 | 714,044 | 100 % |
| 2 | 890,257 | 445,129 | 62 % |
| 4 | 1,186,113 | 296,528 | 42 % |
| 8 | 1,141,188 | 142,648 | 20 % |

Aggregate throughput saturates at four sessions and *degrades* slightly at
eight, so the ceiling is about 1.7× a single session. (Measured at the SDK
default 150 MHz; the shape holds at 48 MHz.) Past four workers you buy
no extra throughput and simply halve each session's speed, which pushes
individual scenarios toward their timeouts for nothing. Hence the default of
`PUMP_TEST_WORKERS=4`. Re-measure on other hardware before raising it.

A session per test also buys real isolation: no scenario can leave a latched
alarm, a drained reservoir, or a part-filled rolling-hour window behind for the
next one. The rolling-hour counter matters especially — it is a safety limit,
so there is deliberately no way to reset it from the console, and a sequential
suite would eventually trip it on accumulated test doses.

Two rules the fixtures depend on:

- **One `Connection` per session.** A `Connection` can only be attached to one
  session at a time, so the fixture builds its own rather than sharing one.
- **Clear leaked sessions once, before the run** — never from the per-test
  fixture, which would stop sibling workers' sessions that are still in flight.
  A session leaked by a crashed run silently stalls the next session on the
  same project, with no error anywhere.
- **Never run two `run_tests.sh` at once.** The same clearing step cannot tell
  a leaked session from a live one belonging to another run, so the second
  invocation stops the first one's sessions out from under it. The victim
  reports a test failure somewhere unrelated, which is a thoroughly misleading
  way to find this out. Use `-k` to select within a single run instead.

### Expect it to be slow

Emerson runs the core far below real time, and the rate is flat — it does not
improve when the firmware idles, so there is nothing to gain from sleeping
rather than spinning. Measured on this image:

| `clk_sys` | one session | four sessions |
| --- | --- | --- |
| 150 MHz (SDK default) | 210 wall s per firmware s | 506 |
| 48 MHz (this pump) | 94 | 282 |

The pump's 48 MHz clock (see `BOARD_SYS_CLOCK_KHZ` in [src/board.h](src/board.h))
is chosen for battery life, but it roughly halves emulated-time cost as a side
effect, which took the suite from 43 to 14 minutes. Note the speedup is smaller
than the clock ratio: Emerson's own throughput falls as the clock drops
(714k ticks/s at 150 MHz, 557k at 48 MHz), because some of its cost scales with
*emulated time* rather than with instructions executed.

`WALL_PER_FIRMWARE_SECOND` in [test/pump.py](test/pump.py) carries this figure
and every timeout is derived from it, so **re-measure it if you change
`BOARD_SYS_CLOCK_KHZ`** — otherwise the suite inherits timeouts calibrated for
a machine that no longer exists.

Scenarios are written to spend as few firmware seconds as they can, parallelism
and the clock do the rest, and the basal-rate test stays opt-in because one
microstep at the maximum basal rate still takes minutes on its own.
