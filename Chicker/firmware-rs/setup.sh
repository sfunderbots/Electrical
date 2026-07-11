#!/bin/sh
# One-time host setup for building and flashing this firmware.
# Run from a normal terminal — needs sudo once, for the udev rule.
set -e
cd "$(dirname "$0")"

rustup target add thumbv6m-none-eabi

if command -v picotool >/dev/null 2>&1; then
    echo "picotool already present: $(command -v picotool)"
else
    echo "== building picotool (not packaged for Ubuntu/Pop as of 24.04) =="
    pkg-config --exists libusb-1.0 2>/dev/null || {
        echo "missing build deps - run:" >&2
        echo "  sudo apt install build-essential cmake pkg-config libusb-1.0-0-dev git" >&2
        exit 1
    }
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    git clone --depth 1 https://github.com/raspberrypi/picotool "$TMP/picotool"
    git clone --depth 1 https://github.com/raspberrypi/pico-sdk "$TMP/pico-sdk"
    cmake -S "$TMP/picotool" -B "$TMP/picotool/build" \
        -DPICO_SDK_PATH="$TMP/pico-sdk" -DCMAKE_BUILD_TYPE=Release
    make -C "$TMP/picotool/build" -j"$(nproc)"
    mkdir -p "$HOME/.cargo/bin"
    cp "$TMP/picotool/build/picotool" "$HOME/.cargo/bin/"
    echo "picotool installed to ~/.cargo/bin"
fi

if [ -e /etc/udev/rules.d/60-picotool.rules ]; then
    echo "udev rule already installed"
else
    echo "== installing udev rule so picotool works without root (sudo) =="
    sudo cp udev/60-picotool.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
fi

echo "setup complete - flash with ./flash.sh"
