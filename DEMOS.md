# Demos

Rehearsed walkthroughs of the pump's safety behaviour in the Emerson simulator,
for showing someone what the firmware does when the hardware misbehaves. Each
one below has been run end to end and the timings are measured, not estimated.

For what the alarms mean and why the thresholds are what they are, see the
[README](README.md#safety-model). This document is only the stage directions.

Every demo here is also asserted in
[`test/test_demos.py`](test/test_demos.py) — one test per demo, same order —
so if one stops behaving the way this page describes, the suite should say so
before an audience does. (The pressure-sensor half of demo 5 is covered by
`test_pump.py::test_recovery_requires_the_fault_to_clear`, which already did
exactly that.)

## Picking one

Times are for a fresh session and include the ~30 s boot. They are wall clock
on an idle server with nothing else running.

| Demo | Time | The point it makes |
| --- | --- | --- |
| [2. Motor fault](#demo-2--motor-fault-latch-and-recovery) | 70 s | alarms latch; acknowledging one does not overrule the hardware |
| [3. Air in line](#demo-3--air-in-line) | 100 s | the fastest alarm, and a second sensor catching what the driver cannot |
| [5. Sensor faults](#demo-5--sensor-faults-the-pump-distrusts-its-instruments) | 2 min | the pump alarms on its *instruments* failing, not just the patient's line |
| [4. No delivery](#demo-4--no-delivery-the-independent-check) | 6 min | flow is the independent check that insulin actually moved |
| [1. Occlusion](#demo-1--occlusion-the-pump-stops-rather-than-push) | 7 min | the pump stops rather than push against a blockage |

If you have time for exactly two, show **1 and 4**. They are the same symptom —
no insulin arriving — with opposite causes, and the firmware tells them apart
on whether the line pressure rose. That pair is the whole safety argument in
miniature.

## Before you start

```sh
emerson load build/rp2350-insulin-pump.bin   # only if the firmware changed
emerson start
emerson ctl go
```

Open the web UI at <http://localhost:10314>. It shows the OLED and gives you
terminals on both serial ports, which is what you want on a projector — the
alternative is three shell commands, and nobody wants to watch you type them.

Wait for boot. First telemetry lands about 21 s after `go`:

```sh
emerson ctl broker tty1 | tail -1
```

You are ready when that prints a record ending `alarms=0x000`.

### The three views

The demos cut between these, and the point of the whole exercise is that they
agree.

| View | Command | What it is |
| --- | --- | --- |
| Console | `emerson ctl broker tty0` | what an operator types and sees |
| Telemetry | `emerson ctl broker tty1` | one record, 4 Hz, machine-readable |
| Panel | `emerson ctl action /MEM/i2c0/ssd1306 dump_display` | what the patient sees |

The panel is the slowest of the three to catch up — see
[Expect it to be slow](#expect-it-to-be-slow). Dump it a second time if it
still shows the old state.

Send a console command with a trailing carriage return:

```sh
emerson ctl broker tty0 $'status\r'
```

### Reading a telemetry record

```
t=5825 mode=2 basal=1000 bolus=0/0 resv=295955 total=4045 hour=4045 steps=809
flow_nlpm=972000 flow_st=0 air=0 psi_m=8086 psi_raw=2176 nfault=0 alarms=0x002
```

The fields worth pointing at during a demo:

| Field | Means |
| --- | --- |
| `mode` | 0 stopped, 1 running, **2 suspended by the safety layer** |
| `alarms` | latched alarm bitmask; `0x001` motor, `0x002` occlusion |
| `psi_m` | line pressure in millipsi — the occlusion trip is 4000 |
| `steps` | net microsteps the driver model actually pulsed |
| `total` | delivered milliunits, reconciled from those microsteps |
| `bolus` | delivered/commanded milliunits for the bolus in flight |

`steps` is the honest one: it is counted by the driver model, not reported by
the firmware, so "the mechanism stopped" is a claim you can check rather than
take.

### Expect it to be slow

Emerson runs the core far below real time. Measured across the runs behind this
document, one firmware second costs **65-185 wall seconds** on an idle server,
varying with what the firmware is doing. Nothing is hung; it is just slow.

Everything the pump emits is paced in *firmware* time, so at these rates both
of its outputs lag badly in wall clock:

| Output | Rate in firmware | Actual wall-clock gap |
| --- | --- | --- |
| telemetry record | 4 Hz | **15-45 s** |
| OLED redraw | 2 Hz | **30-90 s** |

This is the single thing most likely to make you misread a demo. After sending
a command, `emerson ctl broker tty1 | tail -1` keeps handing back the record
from *before* your command for up to 45 seconds, and `dump_display` keeps
showing the previous frame for up to 90. **Watch the `t=` field advance before
you believe the rest of the record**, and expect the panel to catch up well
after the telemetry has — in two of the scenarios here the OLED still read
`RUNNING` a second after telemetry showed the alarm and the pump suspended.

Both scenarios that appeared to misbehave while these demos were being written
turned out to be this and nothing else: a stale record read back as if it were
current.

## Demo 1 — Occlusion: the pump stops rather than push

**About 7 minutes.** The story: a blocked line is the failure that a pump can
make worse by trying harder, so this one stops and says what to go and fix.

### 1. Start delivering

```sh
emerson ctl broker tty0 $'run\r'
```

`mode` goes to 1 within about 20 s. The panel shows `RUNNING`.

### 2. Block the line, then bolus into it

```sh
emerson ctl action /MEM/adc/abp_pressure set_occluded true
emerson ctl broker tty0 $'bolus 8.0\r'
```

The 8 U is deliberately oversized — the line needs room to build pressure and
hold it through the dwell, and the pump stops long before the dose is done, so
the extra costs nothing. Say this out loud before someone asks why you are
demonstrating a 8 U bolus.

### 3. Watch the pressure climb

```sh
watch -n5 'emerson ctl broker tty1 | tail -1'
```

`psi_m` rises from ~300 through the 4000 trip. This is the slow part —
**about 4½ minutes** from the `bolus` command to the alarm — because the
threshold has to be reached *and then held for 2 s* before the pump acts. If
you need to fill the silence, this is the moment to explain why it dwells: a
bubble or a bumped syringe also makes a pressure spike, and neither is an
occlusion.

### 4. The pump stops itself

```
mode=2 ... total=4045 ... steps=809 ... psi_m=8086 ... alarms=0x002
```

```sh
emerson ctl action /MEM/i2c0/ssd1306 dump_display     # SUSPENDED / OCCLUSION
```

Three things to point at:

- `mode=2` — suspended by the safety layer, not stopped by a person.
- `bolus=0/0` — the outstanding dose is **abandoned, not paused**. 4.045 U of
  the 8 went in; the remaining 4 never will.
- `psi_m=8086` — twice the trip threshold. 2 U gets the line to 4 psi and the
  2 s dwell pushes 2 U more, which is what the overshoot is. It is bounded
  because the mechanism stops.

### 5. Show that the mechanism is actually frozen

```sh
emerson ctl broker tty1 | tail -1 | tr ' ' '\n' | grep steps   # wait 45 s, repeat
```

`steps` does not move. The driver model counted the pulses, so this is
independent of anything the firmware claims.

### 6. Clear the occlusion and acknowledge

```sh
emerson ctl action /MEM/adc/abp_pressure set_occluded false
emerson ctl broker tty0 $'ack\r'
```

The line vents immediately — `psi_m` is back to 0 within one telemetry poll —
so the `ack` is accepted first try and prints `ok alarms=0x000`. **A successful
ack resumes delivery on its own**; you do not need to send `run`. `mode` goes
back to 1.

### 7. Re-command the bolus

```sh
emerson ctl broker tty0 $'bolus 1.0\r'
```

Completes in about 75 s: `total` 4045 → 5045 mU and `steps` 809 → 1009. That
is exactly 200 microsteps at 5 mU each — the delivery path has no rounding in
it, which is the closing point of the demo.

## Demo 2 — Motor fault: latch and recovery

**About 70 seconds**, for when you have no time for the occlusion. Same shape,
much faster, and it makes the latching point more sharply.

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/stepper inject_fault overcurrent
```

The alarm lands in about **12 s**: `mode=2`, `nfault=1`, `alarms=0x001`.

```sh
emerson ctl broker tty0 $'ack\r'                  # while the fault is present
emerson ctl broker tty1 | tail -1                 # alarms is STILL 0x001
```

**Watch the telemetry here, not the console.** The console replies
`ok alarms=0x000`, which looks like the ack worked — and for one tick it did.
The condition is still true, so the next safety pass re-latches it and the pump
stays suspended. The console tells you what the mask was at that instant; the
telemetry tells you what it is now. Demonstrating "it refuses to clear" by
pointing at the console will make you look wrong.

```sh
emerson ctl action /MEM/stepper clear_fault
emerson ctl broker tty0 $'ack\r'
```

Now `alarms=0x000` and `mode=1` — delivery resumed, no `run` needed.

## Demo 3 — Air in line

**About 100 seconds**, and the fastest alarm in the set — good when you want to
show the safety layer reacting without anyone having time to get bored.

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/i2c0/slf3x set_air_in_line true
```

**15 s** later: `air=1`, `alarms=0x004`, `mode=2`, and the panel reads
`SUSPENDED` / `AIR IN LINE` once it redraws. A bubble is the one fault
here that the driver and the pressure sensor both miss entirely — the
mechanism turns freely and the line pressure does not move — so this is the
clearest case of a second instrument earning its place.

Recovery is the motor-fault shape (see [Demo 2](#demo-2--motor-fault-latch-and-recovery)):
an `ack` with the bubble still present replies `ok alarms=0x000` and the alarm
comes straight back.

```sh
emerson ctl action /MEM/i2c0/slf3x set_air_in_line false
emerson ctl broker tty0 $'ack\r'        # alarms=0x000, mode=1
```

## Demo 4 — No delivery: the independent check

**About 6 minutes.** Show this straight after the occlusion if you can. Same
symptom, opposite cause, and the point is how the firmware tells them apart.

The failure being simulated is a mechanism that has come uncoupled from the
syringe: the motor turns, the driver counts every microstep it was asked for,
and no fluid moves. Nothing in the delivery path can detect this — only the
flow sensor can.

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/i2c0/slf3x set_flow_scale_error_percent -100
emerson ctl broker tty0 $'bolus 1.5\r'
```

**207 s** after the `bolus` command:

```
mode=2 total=1045 steps=209 flow_nlpm=0 psi_m=64 alarms=0x008
```

The two numbers to put side by side:

- `steps=209` — the driver model counted 209 microsteps. The mechanism did
  everything it was told.
- `flow_nlpm=0` — and nothing came out.

The panel reads `SUSPENDED` / `NO DELIVERY`.

`psi_m=64` is the one that matters for the comparison with Demo 1. Sixty-four
millipsi is the line sitting at rest; against an occlusion it was 8086. **No
pressure rose, so this is not a blockage** — and that is exactly the test the
firmware uses. [safety.c:111-127](src/app/safety.c#L111-L127) suppresses
`NO DELIVERY` for any bolus during which the pressure went high, so a blocked
line is reported as `OCCLUSION` and never as the vaguer alarm. An uncoupled
mechanism builds no pressure, so it still alarms here. Two rules, one
mechanism, and they do not race.

The verification arms only once **1.0 U has been commanded**
(`VERIFY_MIN_COMMANDED_NL`), which is why the demo uses a 1.5 U bolus and why
basal is never checked this way — at 1 U/hr the flow is below one count on the
sensor. The README has the arithmetic.

## Demo 5 — Sensor faults: the pump distrusts its instruments

**About 2 minutes each.** Everything above is the pump detecting a problem with
the patient's line. These two are the pump detecting a problem with *itself*,
and they recover differently, which is the reason to show one.

```sh
emerson ctl action /MEM/adc/abp_pressure set_output_fault open  # -> alarms=0x010
emerson ctl action /MEM/i2c0/slf3x set_crc_error_period 1       # -> alarms=0x020
```

Either alarms **29 s** after injection — slower than the others because both
need ten consecutive bad samples before they will call the instrument broken.
One dropped reading is not a dead sensor.

The open-circuit pressure sensor rails its output: `psi_raw` goes from 410 to
0, outside the diagnostic band, and `psi_m` becomes meaningless. Corrupting
every CRC shows up as `flow_st=2`.

These are the alarms where **`ack` is refused outright**, and the console says
so:

```
pump> ack
ok alarms=0x010          <- the mask comes back NON-zero: still latched
```

Compare that with the motor fault and the bubble, which reply `ok alarms=0x000`
and then re-latch a tick later. The difference is real and worth a sentence on
the slide: [safety.c:215-228](src/app/safety.c#L215-L228) lists the conditions
the firmware can test *at the moment you ack* — both sensor faults, occlusion,
reservoir-empty and the hour limit — and those are withheld in the reply.
Everything else is dropped and re-raised by the next safety pass.

One more thing this demo shows that the occlusion does not. Repair the sensor
and ack immediately and it is **still** refused:

```sh
emerson ctl action /MEM/adc/abp_pressure set_output_fault none
emerson ctl broker tty0 $'ack\r'        # ok alarms=0x010 - still refused
```

The fail counter only resets when the firmware actually *sees* a good sample,
and at emulated speed that is most of a minute away. Wait for `psi_raw` to read
410 again — a new telemetry record, not the stale one — and then ack. A user
who acks too early simply has to ack again, which is the intended behaviour and
not a bug to apologise for.

Once it does clear, it stays clear: `mode` goes to 1 and `alarms` to `0x000`,
held for 200 s of wall clock in a run done specifically to check it. If you
see the alarm apparently come back a few seconds after a successful `ack`, you
are looking at the pre-ack telemetry record — give it 45 seconds.

## Resetting between demos

Alarms latch, the reservoir drains, and the rolling-hour counter deliberately
cannot be cleared from the console. Take a fresh session between runs rather
than trying to tidy up:

```sh
emerson stop && emerson start && emerson ctl go
```

Budget ~30 s for the restart and the boot. If you are demoing twice in a row,
restart during the questions.

## Other faults

`RESERVOIR EMPTY`, `RESERVOIR LOW` and `HOURLY LIMIT` are real alarms with test
coverage, but there is no quick way to show them: the reservoir holds 300 U and
the rolling cap is 30 U/hr, so reaching either means pumping tens of units at
1 U/s of firmware time — hours of wall clock. Describe them from the
[README](README.md#safety-model) table rather than trying to demonstrate them.

`emerson ctl actions <path>` lists everything a given device model can be made
to do, including calibration errors and step slip that are not demoed here.

## When it goes wrong

**`error: not licensed to execute: this session's license is no longer valid`**
— the session outlived its license. `emerson stop && emerson start` fixes it.
Check this before you present, not during.

**The panel or the telemetry shows the wrong state** — it is almost certainly
stale rather than wrong. See the lag table in
[Expect it to be slow](#expect-it-to-be-slow): up to 45 s for a telemetry
record and 90 s for the OLED. Check `t=` has advanced before concluding
anything.

**An alarm seems to come back right after a successful `ack`** — same cause.
The record you are reading predates the ack. Wait for a new one.

**A console command gets no reply** — commands are never pipelined. The UART
FIFO holds 32 bytes and the firmware drains it once per control period, so
back-to-back sends can be truncated. Wait for each reply before sending the
next.

**Everything is slower than the times above** — something else is running a
session. Emerson's throughput saturates at four concurrent sessions; the
occlusion demo is the one long enough to notice. `emerson sessions` shows what
is live.
