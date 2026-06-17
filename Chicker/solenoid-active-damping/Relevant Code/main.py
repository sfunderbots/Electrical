from machine import Pin, ADC, PWM
from array import array
import math
import random
import utime
import sys, select
import pulses

# Non-damping relevant instantiations removed
KICK = Pin(3, Pin.OUT)

# ── Instantiate PIO at maximum resolution (125MHz = 8ns per tick) ────────────
pulse_lib = pulses.Pulses(None, KICK, 125_000_000)
print("machine freq", machine.freq())
print("pulse_lib sm_freq", pulse_lib.sm_freq)
# This uses a blocking assignment and will be changed in the future. 

# Initial states (irrelevant variables to damping removed)

pulse_freq = 0
duty = 1


idling = 0
kicking = 0
damping = 0
charging = 0

CHG_WAIT = 5000


DAMP_INITIAL_KICK_US = 500   # 500*1000us for visualization on an LED, change to 500us for real use

DAMP_STATE_KICK   = 0
DAMP_STATE_SETTLE = 1
DAMP_STATE_HOLD   = 2

damp_state        = DAMP_STATE_KICK
damp_settle_start = 0
damp_hold_start   = 0


MODE_IDLE = 0
MODE_AUTOKICK = 1
MODE_DAMP = 2
MODE_CHARGE = 3
mode = 3 # automatically boot into charge mode to keep caps charged. and make sure there is no booting bug

prev_mode = 0

def idle():
    # Pseudo: Charging disabled, auto-discharge enabled, caps are discharged. Useful for time-outs or long periods of waiting to save battery life
def kick():
    ## Pseudo: Enable autokick loop, where the caps are charged (charging enabled on flyback chip), and robot waits for a command as well as breakbeam trigger


def damp(damp_freq, damp_duty_percent, damp_timeout):
    #Global variables
    global idling, kicking, damping, charging
    global mode, prev_mode, chg_stop_mode_ctrl, not_dischg
    global damp_state, damp_settle_start, damp_hold_start

    idling   = 0
    kicking  = 0
    charging = 0
    
    damp_timeout_us = damp_timeout * 1000

    if damping == 0:
        prev_mode          = mode
        damping            = 1
        damp_state         = DAMP_STATE_SETTLE
        chg_stop_mode_ctrl = 1
        not_dischg         = 1
        print("DAMPING MODE: firing initial kick")
        pulse_lib.put_pulses(DAMP_INITIAL_KICK_US)
        damp_settle_start  = utime.ticks_us()

    elif damp_state == DAMP_STATE_SETTLE:
        if utime.ticks_us() - damp_settle_start >= DAMP_INITIAL_KICK_US * 15:
            damp_state      = DAMP_STATE_HOLD

    elif damp_state == DAMP_STATE_HOLD:
        # start PWM to hold kicker at minimum force.
        print("starting PIO PWM hold: freq", damp_freq, "Hz  duty", damp_duty_percent, "%")
        pulse_lib.put_pwm_v2(duty=damp_duty_percent, freq_hz=damp_freq, duration_us=damp_timeout_us)
        #put_pwm is modified from a pulse width library accessible online
        damp_hold_start = utime.ticks_us()
        damp_state = DAMP_STATE_KICK

    elif damp_state == DAMP_STATE_KICK:
        if utime.ticks_us() - damp_hold_start >= damp_timeout_us:
            pulse_lib.sm_put.active(0)
            mode       = MODE_CHARGE # set the mode back to charging to recharge caps.
            print("DAMPING COMPLETE: returning to charge mode")

# 0x64 is 100 in hex
def chg():
    ## Pseudo: Enable charging on the flyback charge controller ##


while True:
    ## Pseudo: GET data from USB or CAN depending on how your setup is formed. This code isnt as important as it is highly dependent on the codebase type. ##
    
 
    # BYTE 0             = MODE
    #
    # BYTE 1-2 IN MODE 1 = PULSE WIDTH 
    # BYTE 1-2 IN MODE 2 = DAMP_FREQ
    # BYTE 3             = DUTY (In Percentage)

    # Mode Select (Idle, Kick, Damp)
    if mode == MODE_IDLE :
        # do idle. HV discharged, stops charging.
        idle()
    elif mode == MODE_AUTOKICK :
        # do kick mode. Ready to receive a kick command. High Voltage is Charged
        kick()
    elif mode == MODE_DAMP :
        # do damping mode. pulse the kicker to receive a pass and transfer energy. Happens only once and exits.
        # pulse_width or 
        damp(pulse_freq, duty, 3000)
    elif mode == MODE_CHARGE :
        # setup charge mode, which charges caps but does not ready for a kick. Useful if you want to turn off autokick or prep a damping cycle but dont want to kick yet
        chg()
    else :
        # unknown command
        print("unknown command")
        chg_stop_mode_ctrl = 1

## Charging Code omitted due to length and not being applicable. ##
