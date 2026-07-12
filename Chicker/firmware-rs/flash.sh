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

# Detect BOOTSEL by USB ID: `picotool info` (no args) segfaults on picotool
# 2.3.0 when it tries to parse the binary info of a non-pico-sdk image, so
# it can't be the probe. `picotool load` itself works fine.
in_bootsel() {
    lsusb -d 2e8a:0003 >/dev/null 2>&1
}

if ! in_bootsel; then
    # Not in BOOTSEL yet -> nudge the running firmware over its console.
    # The console only receives while the host holds the port open (DTR
    # asserted), so keep an fd open around the write instead of a bare
    # open-write-close, which the firmware may never see.
    PORT="${PORT:-$(ls /dev/ttyACM* 2>/dev/null | head -n1)}"
    if [ -n "$PORT" ] && [ -e "$PORT" ]; then
        echo "asking the firmware on $PORT to reboot into BOOTSEL"
        stty -F "$PORT" raw -echo 2>/dev/null || true
        # Drain the log stream while sending: if nothing reads the port, the
        # USB logger can stall on its unread output and never process the
        # command. Reading + writing together is the reliable recipe.
        # The first byte after opening the port is sometimes lost, so lead
        # with a newline and send the command twice — the line parser
        # ignores the leftovers.
        (
            cat "$PORT" >/dev/null 2>&1 &
            reader=$!
            sleep 0.3
            {
                printf '\r\n'
                sleep 0.2
                printf 'bootloader\r\n'
                sleep 0.4
                printf 'bootloader\r\n'
            } >"$PORT" 2>/dev/null
            sleep 0.5
            kill "$reader" 2>/dev/null
        ) 2>/dev/null || true
    fi
fi

printf 'waiting for BOOTSEL device'
i=0
until in_bootsel; do
    i=$((i + 1))
    if [ "$i" -ge 50 ]; then
        printf '\n'
        echo "no BOOTSEL device appeared - hold BOOT, tap RESET, re-run" >&2
        exit 1
    fi
    printf '.'
    sleep 0.2
done
printf '\n'
# Give udev a beat to apply the access rule to the fresh device node.
sleep 0.5

exec picotool load -u -v -x -t elf target/thumbv6m-none-eabi/release/chicker-fw
