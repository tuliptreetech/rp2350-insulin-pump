#!/usr/bin/env bash
#
# Run the pump scenarios inside emerson-server, in parallel.
#
# The `emerson` Python bindings only exist inside the container and the project
# tree is not mounted there, so the suite is shipped in over stdin as a tar and
# run from /tmp/pumptest. The firmware's font table goes with it, because the
# display assertions match against the real glyphs the firmware draws.
#
# Usage:
#   test/run_tests.sh                 # the standard suite
#   test/run_tests.sh --runslow       # including the slow basal-rate scenario
#   test/run_tests.sh -k occlusion    # one scenario
#   PUMP_TEST_WORKERS=4 test/run_tests.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Four, because that is where this server saturates. Measured on the RP2350
# image: aggregate throughput is 714k ticks/s at one session, 1.19M at four,
# and 1.14M at eight - so past four you buy no extra throughput and simply
# halve every session's speed, which pushes individual scenarios towards their
# timeouts for nothing.
WORKERS="${PUMP_TEST_WORKERS:-4}"
REMOTE=/tmp/pumptest

# The Python bindings create sessions with run_project(), which - unlike
# `emerson start` - does not notice that a rebuild replaced build/*.bin and
# left the container's single-file bind mount pointing at a dead inode. The
# tests would then silently exercise whatever firmware the container still
# sees. Compare the two before running anything.
local_md5=$(md5 -q build/rp2350-insulin-pump.bin 2>/dev/null \
            || md5sum build/rp2350-insulin-pump.bin | cut -d" " -f1)
container_md5=$(emerson exec md5sum /opt/tuliptree/emerson/projects/rp2350/flash.bin \
                2>/dev/null | cut -d" " -f1 || true)

if [ "$local_md5" != "$container_md5" ]; then
    echo "The container is not running the firmware you just built."
    echo "  build/rp2350-insulin-pump.bin  $local_md5"
    echo "  container flash.bin            ${container_md5:-<unreadable>}"
    echo "Refreshing it with 'emerson start'..."
    emerson start >/dev/null
    container_md5=$(emerson exec md5sum /opt/tuliptree/emerson/projects/rp2350/flash.bin \
                    | cut -d" " -f1)
    if [ "$local_md5" != "$container_md5" ]; then
        echo "Still mismatched after reload - run 'emerson load --reload' by hand." >&2
        exit 1
    fi
fi

# Re-quote the caller's arguments so they survive the trip through `sh -c`
# inside the container. Interpolating $* raw splits on whitespace, which turns
# -k 'a or b' into three arguments and silently collects no tests at all.
pytest_args=""
if [ "$#" -gt 0 ]; then
    pytest_args=$(printf ' %q' "$@")
fi

# Demo 9 stops the core at the instruction that samples nFAULT, which means it
# needs that instruction's address. Read it out of the ELF rather than pinning
# it in the test: every rebuild can move it. The test disassembles forward from
# here to find the actual load, so only the symbol has to be right.
NFAULT_ADDR=$(nm build/rp2350-insulin-pump.elf 2>/dev/null \
              | awk '$3 == "drv8825_faulted" { print "0x" $1 }')
if [ -z "$NFAULT_ADDR" ]; then
    echo "warning: drv8825_faulted not found in the ELF - demo 9 will skip." >&2
fi

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp test/*.py "$stage/"
cp src/drivers/font5x7.c "$stage/"

echo "shipping the suite into emerson-server..."
tar -C "$stage" -cf - . \
  | emerson exec sh -c "rm -rf $REMOTE && mkdir -p $REMOTE && tar -C $REMOTE -xf -"

emerson exec sh -c "
set -e
python3 -c 'import xdist' 2>/dev/null \
  || pip install --quiet --break-system-packages pytest-xdist

# Once, before any worker starts: a session leaked by a crashed run silently
# stalls the next session on the same project. Doing this inside the per-test
# fixture instead would stop sibling workers' sessions that are still in
# flight.
python3 $REMOTE/clear_sessions.py

# --dist load spreads individual tests across workers. loadfile would keep
# every test in one file on a single worker, which is all of them.
cd $REMOTE && PUMP_DRV8825_FAULTED_ADDR='$NFAULT_ADDR' exec python3 -m pytest \
    -p no:cacheprovider -n $WORKERS --dist load -v -ra$pytest_args
"
