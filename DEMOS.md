# Demos

Walkthroughs of the pump's safety behaviour in the Emerson simulator, for
showing someone what the firmware does when the hardware misbehaves.

**Demos 1-5 are rehearsed walkthroughs.** Each has been run end to end and the
timings are measured, not estimated. These are the ones to put on a projector.

**[Demos 6-8](#the-recall-scenarios--demos-6-8) are scenarios, not
walkthroughs.** Each is written against a specific FDA Class I insulin-pump
recall and asks whether this firmware would have caught it. They are built and
they run, but two of the three end in a finding rather than a tidy alarm, and
one of them spends minutes asserting that nothing happens — which makes them
far better as evidence than as theatre. Run them from the suite and read the
result.

For what the alarms mean and why the thresholds are what they are, see the
[README](README.md#safety-model). This document is only the stage directions.

Every rehearsed demo is also asserted in
[`test/test_demos.py`](test/test_demos.py) — one test per demo, same order —
so if one stops behaving the way this page describes, the suite should say so
before an audience does. (The pressure-sensor half of demo 5 is covered by
`test_pump.py::test_recovery_requires_the_fault_to_clear`, which already did
exactly that.)

Demos 6-8 live in [`test/test_recall_demos.py`](test/test_recall_demos.py), and
they are built to report differently: everything that establishes the scenario
is a hard assertion, while the safety *requirement* each one ends on is checked
with `pytest.xfail`. A requirement the firmware does not meet therefore reports
as `x` with its reason in the summary rather than as a red failure, and turns
into an `XPASS` the day the rule is written.

So a green run with xfails in it is that file working. **Read the reasons** —
they are where the findings are.

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

This table is the rehearsed set only. The recall scenarios are demos 6-8, and
they are run from the suite rather than performed — see
[The recall scenarios](#the-recall-scenarios--demos-6-8).

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

## The recall scenarios — demos 6-8

**Scripted in [`test/test_recall_demos.py`](test/test_recall_demos.py) and run.**
The sections below are the reasoning behind each scenario, followed by what it
actually found.

### What they found

Measured on Emerson `1.0.15-local`, at the pump's 48 MHz `clk_sys`.

| Scenario | Result | What it means |
| --- | --- | --- |
| 6. Over-delivery | `xfail` | flow sensor reported **3.8x** the commanded volume; no alarm, because no rule looks that way |
| 6. Step slip | `xfail` | **73%** of the commanded dose arrived; no alarm, which is correct against a 50% floor — is 50% the right place? |
| 6. Free flow | `skip` | needs a model knob that does not exist |
| 7. Leaking set | `xfail` | a healthy bolus moves the line only **27 mpsi**; there is no pressure signature to build a leak rule on |
| 8. Pressure noise | **pass** | noise crossing the trip on ~30% of samples never satisfied the 2 s dwell |
| 8. Isolated CRC | **pass** | one bad read in three never tripped `FLOW_SENSOR` |
| 8. nFAULT glitch | `xfail` | one sampled assertion of nFAULT latched `MOTOR_FAULT` and suspended delivery |
| 9. Microsecond glitch | `xfail` | a **100 µs** nFAULT pulse — 1/498 of a control period — latched `MOTOR_FAULT` and suspended delivery |

The two passes are as much a result as the xfails, and they are the half worth
saying out loud when the subject is Malfunction 12: both debounces demonstrably
reject transients, and that is now evidence rather than a reading of the source.

**The nFAULT result is the one real firmware finding**, and
[demo 9](#demo-9--the-microsecond-glitch-the-experiment-a-bench-cannot-run) is
what settles it. Demo 8c shows the motor path commits on a single sample where
pressure and flow each require ten consecutive, but at a 50 ms glitch it cannot
show the firmware was *wrong* to act — 50 ms of nFAULT on real silicon is
plausibly a genuine overcurrent. Demo 9 closes that by staging the glitch three
orders of magnitude narrower, and the pump latches anyway.

What can now be said on a slide: **we reproduced Malfunction 12's failure mode
in our own firmware** — a transient far too brief to be a real fault stops
insulin delivery, latches, and leaves no trace of a fault in the record. What
still cannot: that this was Tandem's mechanism, or that our DRV8825 emits such
glitches at any particular rate. The defect demonstrated is the missing
debounce, not a frequency.

Demos 1-5 all have the same shape. Inject a fault, the right alarm fires, the
pump suspends. They show the safety layer working as designed, which is worth
showing — but it is not the shape the published Class I recalls take. Those are
the other three shapes: the device **delivered the wrong amount**, it **failed
to alarm**, or it **alarmed when it should not have**. Demos 6-8 are scenarios
in those shapes, each drawn from a specific recall.

| Demo | Drawn from | The shape of the failure |
| --- | --- | --- |
| [6. Free flow](#demo-6--free-flow-the-check-that-only-looks-one-way) | Medtronic MiniMed 630G/670G retainer ring (Class I, Feb 2020) | delivered too much |
| [7. Leaking set](#demo-7--the-leaking-set-the-failure-both-instruments-call-healthy) | Insulet Omnipod 5 internal tubing tear (Class I, Apr 2026) | failed to alarm |
| [8. False alarm](#demo-8--the-false-alarm-the-scenarios-that-assert-nothing-happens) | Tandem Mobi "Malfunction 12" (Class I, 2026) | alarmed when it should not have |
| [9. Microsecond glitch](#demo-9--the-microsecond-glitch-the-experiment-a-bench-cannot-run) | Tandem Mobi "Malfunction 12" (Class I, 2026) | alarmed when it should not have |

There is a bias in demos 1-5 worth naming while presenting these: every one of
them is on the **under-delivery** side. That matches the largest bucket in
MAUDE's adverse-event reporting — hyperglycaemia and DKA — but it leaves the
second bucket, hypoglycaemia and loss of consciousness, uncovered. Demo 6 is
the one that goes there.

**On reading these results.** Each section below states its reasoning first and
what it found second, and the two are different kinds of claim. Where a section
describes what the code does today — where a threshold sits, which rule tests
what — that is a reading of the source. Where it says **what it found**, that is
a measured run, and the number is reproducible with
`test/run_tests.sh --runslow`.

One result is a firmware finding (the nFAULT path). Two are findings about the
*instruments* rather than the firmware, which is a distinction worth keeping
sharp in front of an audience: an alarm that cannot be built because the sensor
does not see the failure is not the same as an alarm someone forgot to write.

### Demo 6 — Free flow: the check that only looks one way

**Drawn from:** Medtronic MiniMed 630G/670G, Class I February 2020
(Z-0955-2020, Z-0956-2020). 322,005 units, 26,421 complaints, 2,175 injuries,
one death. A plastic retainer ring cracked and let the insulin cartridge slip
in the drive-shaft chamber, typically after the pump was dropped or bumped.
The recall is usually told as under-delivery, but the same slip also produced
**accidental bolus** — a cartridge free to move means fluid can go in that
nobody asked for.

**What the scenario probes.** Delivery verification at
[safety.c:103-128](src/app/safety.c#L103-L128) compares measured flow against
commanded flow in one direction: it raises `UNDER_DELIVERY` when measured falls
below `VERIFY_MIN_FRACTION_PCT` — 50% — of commanded. There is no corresponding
upper bound in the file. Whether that matters is what this scenario decides.

**The injection, using what exists today:**

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/i2c0/slf3x set_flow_scale_error_percent 300
emerson ctl broker tty0 $'bolus 1.5\r'
```

This is Demo 4 with the sign reversed. Where `-100` made the sensor report that
nothing was moving, `+300` makes it report four times the commanded volume
arriving.

**What it asserts:** that the pump suspends — `mode=2`, a non-zero `alarms`.

**What it found.** It does not. The flow sensor reported **3.8x** the commanded
volume — the 4x the injection asks for, minus sensor lag — and the pump
delivered the whole bolus without a murmur. That is the scenario doing its job,
not failing: [safety.c:111](src/app/safety.c#L111) tests
`measured < commanded * 50%` and nothing anywhere tests the other direction,
so there is no rule for this to trip.

**The companion injection**, the mechanism half rather than the fluid half:

```sh
emerson ctl action /MEM/stepper set_step_slip 4
```

Same cracked-ring hardware, under-delivery signature: one commanded microstep
in four never moves the plunger. This one is not a scenario about whether the
under-delivery rule exists — it does, and Demo 4 shows it firing. It is a
scenario about whether its threshold is in the right place.

**What it found. 73% of the commanded dose arrived, and the pump said nothing**
— which is *correct* behaviour against a 50% floor, and is exactly why the
scenario exists. The question it puts on the table is whether 50% is the right
number, given that a pump quietly running a quarter short indefinitely is a
clinical problem that no alarm in the [README](README.md#safety-model) table
describes. That is a conversation for whoever owns the dose envelope, not a bug
to file.

The test asserts the invariant rather than the outcome, so it stays honest
whichever way the threshold moves: if an alarm fires, the delivered fraction
must be below the floor; if none fires, it must be above it.

**What it needs before it can be run:**

- Nothing, for the scale-error version — the knob is already on the model.
- A new alarm and rule in `safety.c`, so the pump has something to raise. Bits
  0-8 are taken (`ALARM__COUNT` is 9 in
  [safety.h:24-35](src/app/safety.h#L24-L35)), so a new one is bit 9, `0x200`.
- For **true** free flow — the cartridge slipped and gravity siphons while the
  motor sits idle — a new knob on the slf3x model, something along the lines of
  `set_free_flow_ul_per_min`. `set_flow_scale_error_percent` scales the flow
  that is actually happening, and with the mechanism stopped that is zero, so a
  scale error cannot express fluid moving on its own. That is the version worth
  building, because it is the case the existing rule cannot see at all:
  verification arms only once 1.0 U has been *commanded*
  (`VERIFY_MIN_COMMANDED_NL`), and a dose nobody commanded never arms it.

### Demo 7 — The leaking set: the failure both instruments call healthy

**Drawn from:** Insulet Omnipod 5, Class I 29 April 2026. About 1.5% of
distributed pods, 18 serious adverse events, DKA cases reported. A tear in the
internal silicone tubing between the reservoir and the built-in cannula let
insulin leak into the pod casing instead of into the patient. The sentence in
the recall that makes this a demo: **the occlusion alarm did not detect it.**

Two related failures belong in the same conversation — an external cannula tear
leaking onto the skin (correction of 26 May 2026), and the 2015 deployment
failure where the cannula never reached the right subcutaneous depth.

**What the scenario probes.** This is the exact inverse of Demo 1. An occlusion
is fluid that cannot get out, and it announces itself as pressure rising. A
tear is fluid getting out somewhere it should not, and it shows up as pressure
that never builds. Every pressure rule in the safety layer is written for the
first case.

Against a leak, each rule is satisfied in turn: pressure stays low, so
`OCCLUSION` never trips; both sensors keep returning valid readings, so neither
sensor-fault trips; and if the leak is downstream of the flow sensor, the
sensor watches the commanded volume go past and `UNDER_DELIVERY` never trips
either. Three rules, all content, and no insulin in the patient.

**The injection:**

```sh
emerson ctl broker tty0 $'run\r'
emerson ctl action /MEM/adc/abp_pressure set_leak_rate_psi_per_s 0.5
emerson ctl broker tty0 $'bolus 1.5\r'
```

`set_leak_rate_psi_per_s` already exists on the ABP model and bleeds pressure
off the line, which is what a tear does. The rate above is a starting guess and
part of what the first run has to calibrate.

**The baseline comes first**, and the scenario now measures it rather than
assuming it. Any rule that catches a leak keys on pressure *failing to rise*,
so it has to know what a healthy bolus does to the line. The test therefore
runs two boluses in one session — intact, then torn — and compares.

**What it found, and it is not what the scenario was built to show.** A healthy
1.5 U bolus moves the line **27 mpsi** above its resting pressure. The floor
this scenario treats as the minimum usable signal is 200 mpsi, so it stops
there and says so.

That is a finding about the **instrument, not the firmware**. "The pressure
failed to rise" cannot be told apart from a normal delivery when a normal
delivery barely registers, so there is no line-integrity rule to be built on
this sensor at this threshold — and the honest thing is to say that rather than
to invent a rule keyed on 27 mpsi of signal. The occlusion trip sits at 4000
mpsi, roughly 150x the entire pressure excursion of a healthy dose, which is
the same fact from the other end.

**What it needs:** the baseline run; a new rule and alarm bit; and a decision
about whether the leak is modelled upstream or downstream of the flow sensor,
because that is what determines whether the flow sensor is a second witness or
blind to it. Downstream is the Omnipod case and the harder one. Model it that
way.

**What this demo should say out loud.** The 2015 cannula-depth failure and the
external tear are **not detectable by this sensor set at all**. Insulin
delivered intradermally or onto the skin leaves a normal flow reading and a
normal pressure profile, and no rule written against these two instruments will
ever see it. Saying so is the same move that gives Demo 3 its point: being
precise about what an instrument can and cannot see is worth more than implying
it sees everything.

### Demo 8 — The false alarm: the scenarios that assert nothing happens

**Drawn from:** Tandem Mobi, Class I 2026, firm-initiated 6 October 2025.
17,700+ devices, 281 adverse events, 4 injuries. A software issue made the pump
incorrectly detect a vibration-motor problem and raise its "Malfunction 12"
alert. The pump was not broken; its fault detection was. Corrected by a remote
software update to version 7.9.0.2.

**What the scenario probes.** This inverts the format of every demo above.
Demos 1-5 inject a fault and assert that an alarm appears. This one injects
**transients** — signals that momentarily resemble faults and are not — and
asserts that `alarms` stays `0x000` and `mode` stays `1` for a defined period.
A safety layer is only as good as its willingness not to fire.

Three injections, one per debounce path.

**a. Noise near the trip.**

```sh
emerson ctl action /MEM/adc/abp_pressure set_zero_offset_psi 3.2
emerson ctl action /MEM/adc/abp_pressure set_noise_counts 400
```

These two numbers are measured against the model, not derived, and getting them
wrong is the easiest way to make this scenario lie. With the line clear the ABP
sits at exactly 410 counts; `set_zero_offset_psi` moves that baseline by 218.4
counts per psi, and `set_noise_counts N` adds a roughly uniform ±N on top.

3.2 psi of offset puts the quiescent reading at ~1109 counts — 3200 mpsi,
clearly under the 4000 trip, so the baseline alone can never satisfy the dwell.
±400 counts then spans ~709..1509, straddling the 1284-count trip on about 30%
of samples and staying well clear of the 102-count floor. That asserts
`OCCLUSION_DWELL_US` ([safety.c:26](src/app/safety.c#L26)) earns its place.

Both bounds are real, and both were hit while calibrating this:

- At **2.5 psi** of offset only 9% of samples crossed the trip. Judged against
  4 Hz telemetry over the window, a run could legitimately contain no crossing
  at all — the scenario then proves nothing and correctly refuses to report.
- At **1200 counts** of noise the troughs reach the band floor and raise
  `PRESSURE_SENSOR`, which is a different alarm and a different scenario.

**What it found: the dwell holds.** Noise crossing the trip on roughly a third
of samples, sustained across the window, never produced an occlusion alarm.

**b. Isolated CRC failures.**

```sh
emerson ctl action /MEM/i2c0/slf3x set_crc_error_period 3
```

One corrupt word in three, which can never produce two in a row, let alone ten.
Demo 5 uses period `1` to show `FLOW_SENSOR` firing; this uses `3` to show it
staying quiet, and the two together make a better point than either alone —
that is `SENSOR_FAIL_LIMIT` ([safety.c:40](src/app/safety.c#L40)) drawn from
both sides.

A period of 3 rather than something sparser on purpose: it packs many more
rejections into the same window of firmware time, and firmware time is the
expensive thing here.

**What it found: the counter resets properly.** A sensor failing a third of its
reads never tripped the alarm across the whole window.

**c. A glitch on nFAULT.** The closest analogue to Malfunction 12, and the one
with no debounce behind it. Pressure and flow each require ten consecutive bad
samples before the firmware will call an instrument broken; the motor path at
[safety.c:139-141](src/app/safety.c#L139-L141) raises `MOTOR_FAULT` from a
single sample.

```sh
emerson ctl action /MEM/stepper inject_fault overcurrent
emerson ctl action /MEM/stepper clear_fault
```

Whether that asymmetry is a defect depends on whether a real DRV8825 can glitch
nFAULT briefly enough to fall inside one control period — a hardware question
this scenario frames rather than answers. But the failure mode it would produce
is Malfunction 12's: a spurious fault detection that latches and suspends
basal.

**What it found: nFAULT held for about one control period latched
`MOTOR_FAULT` and suspended delivery.** The asymmetry is real and reachable —
the motor path commits on a single sample where the sensor paths each want ten.

Be careful how far you take that. What is demonstrated is that the firmware
takes no second look. What is **not** demonstrated is that a spurious assertion
is likely: the glitch that can be staged deterministically is about 50 ms wide,
and 50 ms of nFAULT on real silicon is plausibly a genuine overcurrent fault
rather than a glitch. Proving the hazard needs a glitch far narrower than a
poll that still lands on one, which is a harder experiment than this.

So the claim to make is "our motor path takes no second look, and our sensor
paths do" — not "we found Malfunction 12".

**This one cannot be driven from the shell**, and it is worth knowing that
before anyone tries. The glitch has to be asserted and cleared *between* two
polls of the fault line — tens of milliseconds of firmware time, which is tens
of minutes of wall clock at emulated speed. Two `emerson ctl` invocations will
land arbitrarily far apart in firmware time and the result is luck in either
direction. Build this one against the Python bindings with the core held
between the two actions, so the width of the glitch is a controlled quantity
rather than a race. See the
[emerson-python](.claude/skills/emerson-python/SKILL.md) skill.

**The annunciation half.** The recall notice is explicit that the failure "cuts
both insulin delivery and connectivity at the same time", so a patient could be
unaware that basal had been suspended. The harm was not only the false alarm —
it was the false alarm nobody could see. Our equivalent assertion costs nothing
and can be made during any of the rehearsed demos rather than as a scenario of
its own: when the pump suspends, the OLED reads `SUSPENDED` with the alarm name
on it, and telemetry keeps flowing on `tty1` throughout. Two independent
annunciation paths, neither of them the one that failed. Point at it during
Demo 2.

**What it took.** No firmware or model work for (a) and (b) — both knobs
already existed and the assertion is `alarms == 0x000` held for a defined
window. (c) needed the core paused and stepped between the two actions, so the
width of the glitch is a controlled quantity rather than a race; the test
calibrates the tick-to-microsecond ratio first and fails loudly if a tick turns
out not to be a `clk_sys` cycle, rather than silently testing nothing.

The suite also needed a new kind of test. Everything in `test_demos.py` waits
for something to happen; these wait out a period and assert that nothing did.
That window is budgeted in **firmware** time, not wall clock — a wall-clock
window would mean a different number of sensor polls on a loaded server than on
an idle one, which is exactly the test that passes because it was too short.
All three are marked `slow` and need `--runslow`.

### Demo 9 — The microsecond glitch: the experiment a bench cannot run

**Drawn from:** Tandem Mobi, Class I 2026, firm-initiated 6 October 2025.
17,700+ devices, 281 adverse events, 4 injuries. Software incorrectly detected
a vibration-motor problem and raised "Malfunction 12", cutting insulin
delivery. Corrected by a remote update to 7.9.0.2. Same recall as
[demo 8](#demo-8--the-false-alarm-the-scenarios-that-assert-nothing-happens);
this is the half demo 8c could not finish.

**What the scenario probes.** Demo 8c asserts nFAULT for about one control
period — 50 ms — and the pump latches. That shows the asymmetry is reachable.
It does not show the firmware was *wrong*, because 50 ms of nFAULT on a real
DRV8825 is a plausible overcurrent. The two claims only separate at a width no
real fault could have.

So this one stages **100 µs**: 1/500th of the control period, three orders of
magnitude below any overcurrent trip the driver would hold. If the pump stops
insulin for that, it is not catching a fault. It is inventing one.

**Why it cannot be driven from the shell, or from a bench.** The glitch has to
be present at the instant of one poll and absent at every other, and the polls
are 50 ms apart. Two `emerson ctl` invocations land arbitrarily far apart in
firmware time; a signal generator on real hardware has no way to know when the
firmware samples. The core has to be stopped *at the sampling instruction*:

1. Break on the instruction in `drv8825_faulted()` that loads SIO `GPIO_IN`.
   Two hits give the poll period, measured rather than assumed.
2. Walk to 100 µs before the next sample, with the line still clear.
3. Assert nFAULT — **before** the sample, not at it. The device model needs
   ticks to settle a level change onto the pin; injecting at the breakpoint
   itself reads back as still-clear and the scenario would stage nothing.
4. Run into the sample, retire the load, release nFAULT immediately.

The address is not hardcoded. `run_tests.sh` reads `drv8825_faulted` out of the
ELF and the test disassembles forward to the load, so a rebuild that moves the
function or changes the register allocation does not silently test nothing.

**What it found.**

```
poll period          2,400,000 ticks = 50.00 ms
nFAULT asserted      100.2 µs  (1/498 of one control period)
drv8825_faulted()    returned 1 — the sample really saw it
result               mode=2 SUSPENDED, alarms=0x001 MOTOR_FAULT, nfault=0
```

A 100 µs pulse latched `MOTOR_FAULT` and suspended basal delivery, and it stays
suspended until someone acknowledges it.

**The detail worth putting on the screen** is the last line. Telemetry reports
`nfault=0` *alongside* `alarms=0x001`: the pump is stopped for a fault that is
not present, was never present for longer than a tenth of a millisecond, and
appears nowhere in the record. Anyone reading the log afterwards sees a pump
that suspended itself for no reason — which is exactly the position Mobi's
users and Tandem's engineers were in.

The measured poll period doubles as the tick calibration demo 8c performs
explicitly: 2,400,000 ticks landing on a 50 ms control period at 48 MHz is what
establishes that a tick is a `clk_sys` cycle. If it were not, every width above
would be wrong by that factor and the test fails loudly rather than reporting a
number it cannot justify.

**What it needs:** a debounce on the motor path. `safety.c:139-141` raises
`MOTOR_FAULT` from one sample; the pressure and flow paths each require
`SENSOR_FAIL_LIMIT` — ten — consecutive. Two consecutive samples would reject
every glitch narrower than 50 ms while costing 50 ms of latency on a real
fault, against an occlusion rule that already dwells for 2 s.

**What this demo is for.** The other scenarios ask whether this firmware would
have caught someone else's recall. This one asks the more useful question:
**would this method have caught the recall before it shipped?** The defect is
a missing debounce on a fault input — invisible in review, unreachable from
the console, and not reproducible on a bench, because staging it needs the core
stopped between two instructions. Run this scenario against Mobi's motor fault
detection before release and the missing second look is a test result, not
17,700 devices and 281 adverse events.


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
to do. The calibration-error, step-slip, noise and leak knobs are not used by
any rehearsed demo; they are what
[demos 6-8](#the-recall-scenarios--demos-6-8) are built out of.

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
