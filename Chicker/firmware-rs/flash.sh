#!/bin/sh
# Build + flash over USB using picotool, which talks to the ROM bootloader
# directly over the PICOBOOT protocol — no mounted RPI-RP2 drive needed.
#
# If the firmware is already running, its console `bootloader` command is
# used to reboot into BOOTSEL first, so no buttons are ever pressed.
# First flash of a blank/old board: hold BOOT, tap RESET, then run this.
# Override the serial port with PORT=/dev/ttyACM1 ./flash.sh
cd "$(dirname "$0")" || exit 1

cargo build --release || exit 1

if ! picotool info >/dev/null 2>&1; then
    # Not in BOOTSEL yet -> nudge the running firmware over its console.
    # The console only receives while the host holds the port open (DTR
    # asserted), so keep an fd open around the write instead of a bare
    # open-write-close, which the firmware may never see.
    PORT="${PORT:-$(ls /dev/ttyACM* 2>/dev/null | head -n1)}"
    if [ -n "$PORT" ] && [ -e "$PORT" ]; then
        echo "asking the firmware on $PORT to reboot into BOOTSEL"
        stty -F "$PORT" raw -echo 2>/dev/null || true
        (
            exec 3<>"$PORT" && printf 'bootloader\r\n' >&3 && sleep 0.5
        ) 2>/dev/null || true
    fi
fi

printf 'waiting for BOOTSEL device'
i=0
until picotool info >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 50 ]; then
        printf '\n'
        echo "no BOOTSEL device reachable - hold BOOT, tap RESET, re-run" >&2
        echo "(permission errors? install the udev rule - see README)" >&2
        exit 1
    fi
    printf '.'
    sleep 0.2
done
printf '\n'

exec picotool load -u -v -x -t elf target/thumbv6m-none-eabi/release/chicker-fw
