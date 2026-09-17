#!/usr/bin/env bash
#
# Build the pump firmware.
#
# Nothing this build needs is on PATH: cmake, the RISC-V toolchain and the SDK
# are all installed under ~/.pico-sdk by the Raspberry Pi Pico VS Code
# extension, which puts them on PATH only inside its own integrated terminal.
# This script does the same thing for an ordinary shell, so the two produce the
# same binary.
#
# The versions are read out of CMakeLists.txt rather than pinned here. That
# block is maintained by the extension and it does change SDK versions
# underneath you; reading it means the script follows rather than fights it.
#
# Usage:
#   ./build.sh                 # configure if needed, then build
#   ./build.sh --debug         # -O0 with symbols, for stepping in the emulator
#   ./build.sh --clean         # discard build/ and start over
#   ./build.sh -- -v           # anything after -- goes to the underlying build
set -euo pipefail

cd "$(dirname "$0")"

BUILD_DIR=build
BUILD_TYPE=Release
CLEAN=0
passthrough=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --debug)   BUILD_TYPE=Debug; shift ;;
        --release) BUILD_TYPE=Release; shift ;;
        --clean)   CLEAN=1; shift ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^#\{1,2\} \{0,1\}//; $d'; exit 0 ;;
        --)        shift; passthrough=("$@"); break ;;
        *)         echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

# --- the toolchain the extension installed ------------------------------------

cmakelists_var() {   # read one `set(name value)` out of CMakeLists.txt
    sed -n "s/^set($1 \([^)]*\))/\1/p" CMakeLists.txt | head -1
}

SDK_VERSION=$(cmakelists_var sdkVersion)
TOOLCHAIN_VERSION=$(cmakelists_var toolchainVersion)
PICOTOOL_VERSION=$(cmakelists_var picotoolVersion)

PICO_SDK_PATH="$HOME/.pico-sdk/sdk/$SDK_VERSION"
PICO_TOOLCHAIN_PATH="$HOME/.pico-sdk/toolchain/$TOOLCHAIN_VERSION"
export PICO_SDK_PATH PICO_TOOLCHAIN_PATH

# Glob the cmake directory instead of pinning a version: it is the one part of
# the install CMakeLists.txt says nothing about, and the extension upgrades it.
CMAKE=$(ls -d "$HOME"/.pico-sdk/cmake/v*/bin/cmake 2>/dev/null | sort -V | tail -1 || true)

for required in "$PICO_SDK_PATH" "$PICO_TOOLCHAIN_PATH" "$CMAKE"; do
    if [ -z "$required" ] || [ ! -e "$required" ]; then
        echo "missing: ${required:-cmake under ~/.pico-sdk/cmake}" >&2
        echo "Install it from the Raspberry Pi Pico VS Code extension, which owns" >&2
        echo "~/.pico-sdk. CMakeLists.txt asks for SDK $SDK_VERSION and toolchain" >&2
        echo "$TOOLCHAIN_VERSION." >&2
        exit 1
    fi
done

PATH="$PICO_TOOLCHAIN_PATH/bin:$HOME/.pico-sdk/picotool/$PICOTOOL_VERSION/picotool:$PATH"
export PATH

# --- configure ----------------------------------------------------------------

if [ "$CLEAN" = 1 ]; then
    echo "removing $BUILD_DIR/"
    rm -rf "$BUILD_DIR"
fi

# -G only on a fresh tree. Naming a generator that disagrees with an existing
# cache is a hard error, and the point here is to build whatever is already
# configured - the VS Code extension configures Ninja, the README configures
# Makefiles, and both should keep working.
# Bash 3.2 (what macOS ships) treats an empty array as unset under `set -u`,
# hence the ${a[@]+...} guards on both expansions below.
generator_args=()
if [ ! -f "$BUILD_DIR/CMakeCache.txt" ]; then
    if [ -x "$HOME/.pico-sdk/ninja/v1.13.2/ninja" ]; then
        PATH="$HOME/.pico-sdk/ninja/v1.13.2:$PATH"
        generator_args=(-G Ninja)
    else
        generator_args=(-G "Unix Makefiles")
    fi
fi

"$CMAKE" -S . -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    ${generator_args[@]+"${generator_args[@]}"} >/dev/null

# --- build --------------------------------------------------------------------

JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
"$CMAKE" --build "$BUILD_DIR" -j"$JOBS" ${passthrough[@]+"${passthrough[@]}"}

# --- what came out ------------------------------------------------------------

elf=$BUILD_DIR/rp2350-insulin-pump.elf
if command -v riscv32-pico-elf-size >/dev/null; then
    riscv32-pico-elf-size "$elf"
fi

echo
echo "built $BUILD_TYPE:  $BUILD_DIR/rp2350-insulin-pump.bin"
echo "load it with:       emerson start        # after each rebuild"
