import can
import utime

bus = can.interface.Bus(channel='can0', bustype='socketcan')

prev_time_idle = 0
prev_time_kick = 0
prev_time_damp = 0
idling = 0
kicked = 0
damped = 0
kickedtwice = 0

def send_idle():
    data = [0x00, 0x00, 0x00, 0x00]
    msg = can.Message(arbitration_id=0x2AA, data=data, is_extended_id=False)
    bus.send(msg)

def send_kick(pulse_width_us):
    # pulse_width_us: int, 0-65535 microseconds
    data = [
        0x01,
        pulse_width_us & 0xFF,
        (pulse_width_us >> 8) & 0xFF,
        0x00,
    ]
    msg = can.Message(arbitration_id=0x2AA, data=data, is_extended_id=False)
    bus.send(msg)

def send_damp(freq_hz, duty_percent):
    # freq_hz: int, 1-50000
    # duty_percent: int, 1-99
    freq_hz = max(1, min(freq_hz, 50000))
    duty_percent = max(1, min(duty_percent, 99))
    data = [
        0x02,
        freq_hz & 0xFF,
        (freq_hz >> 8) & 0xFF,
        duty_percent,
    ]
    msg = can.Message(arbitration_id=0x2AA, data=data, is_extended_id=False)
    bus.send(msg)

prev_time_idle = utime.ticks_ms()
prev_time_kick = utime.ticks_ms()
prev_time_damp = utime.ticks_ms()

while True:
    current_time = utime.ticks_ms()
    # Example usage:

    if (idling == 0):
        send_idle()
        idling = 1
        prev_time_idle = utime.ticks_ms()
        print("Idling")
    elif (current_time - prev_time_idle >= 4000 and kicked == 0):
        send_kick(1000)       # 1000 us pulse width
        kicked = 1
        prev_time_kick = utime.ticks_ms()
        print("Kicking 1")
    elif (current_time - prev_time_kick >= 1500 and damped == 0):
        send_damp(1000, 1)   # 1 kHz, 1% duty
        damped = 1
        prev_time_damp = utime.ticks_ms()
        print("Damping")
    elif (damped == 1 and current_time - prev_time_damp >= 1000 and kickedtwice == 0):
        send_kick(1000)
        kickedtwice = 1
        print("Kicking 2")
    else:
        print("error")
