#!/bin/sh
# One-command reflash over USB, no buttons:
# build, ask the running firmware to reboot into BOOTSEL (console
# `bootloader` command), wait for the UF2 drive, flash.
#
# First flash of a blank/old board: hold BOOT, tap RESET, then run this
# (the serial nudge is skipped/ignored and the drive is already there).
# Override the serial port with PORT=/dev/ttyACM1 ./flash.sh
set -e
cd "$(dirname "$0")"

cargo build --release

PORT="${PORT:-$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)}"
if [ -n "$PORT" ] && [ -e "$PORT" ]; then
    echo "sending 'bootloader' to $PORT"
    printf 'bootloader\r\n' > "$PORT" 2>/dev/null || true
fi

echo "waiting for RPI-RP2 drive..."
i=0
until elf2uf2-rs -d target/thumbv6m-none-eabi/release/chicker-fw 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 50 ]; then
        echo "no RPI-RP2 drive appeared - hold BOOT, tap RESET, re-run" >&2
        exit 1
    fi
    sleep 0.2
done
echo "flashed."
