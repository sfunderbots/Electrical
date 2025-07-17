from machine import Pin, ADC
from time import sleep
from array import array
import math
import utime
import random
from battery import Voltages
#from high_voltage import SenseHV
import sys, select
import pulses

from canbus import Can, CanError, CanMsg, CanMsgFlag

from canbus.internal import RXF

can = Can()
ret = can.begin()
if ret != CanError.ERROR_OK:
    print("Error to initilize can!")
    sys.exit(1)
print("initlized succesuqfully!")
can.init_mask(0, 0, 0x0)
can.init_mask(1, 0, 0x0)

can.init_filter(0, 0, 0x2AA)
can.init_filter(1, 0, 0x2AA)
can.init_filter(2, 0, 0x2AA)
can.init_filter(3, 0, 0x2AA)
can.init_filter(4, 0, 0x2AA)
can.init_filter(5, 0, 0x2AA)


DONE = Pin(4, Pin.IN)
SHELL_OFF = Pin(9, Pin.IN)
BREAKBEAM = Pin(23, Pin.IN)

CHARGE = Pin(5, Pin.OUT)
CHIP = Pin(2, Pin.OUT)
KICK = Pin(3, Pin.OUT)
NOT_DISCHARGE = Pin(8, Pin.OUT)
TESTPIN = Pin(24, Pin.OUT)
CAN_LED = Pin(6, Pin.OUT)
INT = Pin(20, Pin.IN)

# initial states
prev_time_int = 0
prev_time_can = 0
prev_time_volt = 0
prev_time_HV = 0
prev_time_start_chg = 0
prev_time_wait_charge_vcc = 0
prev_sim_time = 0
prev_time_chg_wait = 0
prev_time_charge_disabled = 0
prev_time_countdown = 0
prev_sim_charge_time = 0
prev_pulse_time = 0
prev_kick_time = 0
prev_time_damp = 0
prev_cycle_damp = 0
prev_time_startDAMP = 0
charge_ok = 0
prev_charge_ok = 0
startup = 0
startup_time = 0
startup_cycle = 0
startup_vcc_wait = 0
charge_toggle_wait = 0
done_sim = 1
prev_charge = 0
charge = 0
charge_disable = 0
charge_started = 0
check_2s_done = 0
charge_ok_sim = 0
safe_charge = 0
delay_time_us = 0
delay_time_us_temp = 0
kick_cooldown = 1
kick_delayed = 0
HV_voltage = 0
data_rec = 0
startup_chg_2sdelay = 0
charge_stop = 0

CHG_WAIT = 5000


data = None
new_can_data_bool = False
can_rx_time = None
AUTOKICK_EXPIRE_THRESH_MS = 2000
stored_prekick_can_data = None
DAMP_INITIAL_PULSEWIDTH = 200

offset = 1000_000

MODE_IDLE = 0
MODE_AUTOKICK = 1
MODE_DAMP = 2
MODE_DISCHARGE = 3
mode = 0


def idle():
    # Idle mode keeps HV discharged and stops charging.
    NOT_DISCHARGE.value(0)


def kick(pulse_width):
    #Global variables (FUCK ME...)
    global can_rx_time, new_can_data_bool, data_rec, kick_cooldown, delay_time_us_temp, delay_time_us
    global charge_stop, pulse_freq, new_can_data_bool, pulse_width_adjusted, done_state
    global prev_kick_time, kick_delayed, start, ar, prev_pulse_time, startup_chg_2sdelay

    if can_rx_time is not None and (current_time - can_rx_time > AUTOKICK_EXPIRE_THRESH_MS):
        new_can_data_bool = False

    NOT_DISCHARGE.value(1)

    if (data_rec == 0 and kick_cooldown == 0):
        if (data == None) :
            data_rec = 0
            delay_time_us_temp = 0
        else:
            data_rec = 1
            charge_stop = 1
            pulse_width = pulse_freq
            # We processed this frame, so set new data flag to false
            new_can_data_bool = False

            #{HV_voltage, HV_scaling} = SenseHV()
            # HV Pulse width adjustment
            HV_scaling = 1
            pulse_width_adjusted = HV_scaling * pulse_width
            delay_time_us_temp = pulse_width_adjusted
            # CAN_LED.value(0)

            if (delay_time_us_temp > 5000) :
                delay_time_us_temp = 5000 # set a limit to the delay time
                print("Pulse duration too long, setting to 5000us")
            elif (delay_time_us_temp < 0):
                delay_time_us_temp = 0
            #print("Kicking in 2 seconds, at ", delay_time_us_temp, "us. Stand back!")
    else :
        if (done_state == 0):
            prev_kick_time = current_time
            delay_time_us = delay_time_us_temp
            data_rec = 0
            data = None
            #kick_delayed = 1
        # charging still, wait till charging stopped from charge STOP to kick.
        else:
            delay_time_us = 0
            if (kick_delayed == 0) :
                print("DONE is HIGH (Charging), kicking delayed")
                #kick_delayed = 1
 #############################################
    if(kick_cooldown == 0 and delay_time_us != 0):
        kick_cooldown = 1
        pattern=(8, delay_time_us, 8)
        start=0
        ar = array("L", pattern)
        # Kick here
        #
        CAN_LED.off()
        pulses.put_pulses(ar,start)
        prev_pulse_time = current_time # start pulse timer # this seems redundant honestly, we are only sending pulse widths 
        startup_chg_2sdelay = 1
            
    elif (kick_cooldown == 1 and current_time - prev_pulse_time >= 100):
        kick_cooldown = 0
        delay_time_us = 0
        charge_stop = 0

# damp_freq = freq in Hz (two bytes)
# damp_duty = duty cycle in percentage (integer)
# damp_timeout = timeout in milliseconds
def damp(damp_freq, damp_duty, damp_timeout):
    #Global variables (FUCK ME...)
    

    # NEED TO IMPLEMENT A MICROSECOND TIMER OR THIS WILL NOT WORK!
    # utime.ticks_us() works!
    prev_time_damp = current_time
    NOT_DISCHARGE.value(1)

    # Initial Kick (to put plunger out)
    pattern=(8, DAMP_INITIAL_PULSEWIDTH, 8)
    start=0
    ar = array("L", pattern)
    pulses.put_pulses(ar,start)
    damp_period = 1/damp_freq
    damp_on_time = damp_period*damp_duty
    damp_off_time = damp_period - damp_on_time

    prev_cycle_damp = utime.ticks_us()
    # implement a delay here of duty off time.
    if (utime.ticks_us() - prev_cycle_damp > damp_off_time) :
        # keep damping until preset timetimeout or HV discharged
        while (current_time - prev_time_damp < damp_timeout or HV_voltage > 10) :
            if (utime.ticks_us() - prev_cycle_damp > damp_on_time) :
                # turn off kicker
                KICK.value(0)
                # 
            elif (utime.ticks_us() - prev_cycle_damp > damp_period) :
                # turn on kicker
                KICK.value(1)
                # implement TBA

            prev_cycle_damp = utime.ticks_us()
        KICK.value(0)

# Interrupt handler
def breakbeam_handler(pin):
    global new_can_data_bool, data, stored_prekick_can_data
    if not pin.value():
        CAN_LED.value(1)   # Rising edge: turn on LED
        if new_can_data_bool: # Only kick if armed
            data = stored_prekick_can_data
    else:
        CAN_LED.value(0)  # Falling edge: turn off LED

# Attach interrupt for both edges
BREAKBEAM.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=breakbeam_handler)

#kickpulse = pulses.Pulses(None, KICK, 1_000_000)
pulses = pulses.Pulses(None, KICK, 1_000_000)

while True:
    #Read Inputs:
    current_time = utime.ticks_ms()
    #current_ustime = utime.ticks_us()
    
    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = DONE.value()  #DONE.value()#done_sim # 
    CHARGE.value(charge)
    #print(charge_disable)
    can_data = None
    
    while can.checkReceive():
        what, can_data = can.recv()
        if (hex(can_data.can_id) == "0x2aa"):
            if len(can_data) < 4:
                print("Invalid message length.")
            else :
                mode = can_data[0]
                pulse_freq = int.from_bytes(can_data[1:3], 'little')
                duty = can_data[3]
            
                if (mode == MODE_AUTOKICK):
                    # If the breakbeam is already broken, kick immediately
                    if BREAKBEAM.value() == 0:
                        data = pulse_freq
                    # Otherwise, queue up an autokick using interrupt
                    else:
                        stored_prekick_can_data = can_data
                        new_can_data_bool = True
                can_rx_time = utime.ticks_ms()
                print("COMMAND RECEIVED")

    # BYTE 0             = MODE
    #
    # BYTE 1-2 IN MODE 1 = PULSE WIDTH 
    # BYTE 1-2 IN MODE 2 = DAMP_FREQ
    # BYTE 3             = DUTY (In Percentage)

    # Mode Select (Idle, Kick, Damp)
    if mode == MODE_IDLE :
        # do idle. HV discharged, stops charging.
        charge_stop = 1
        idle()
    elif mode == MODE_AUTOKICK :
        # do kick mode. Ready to receive a kick command. High Voltage is Charged
        charge_stop = 0
        kick()
    elif mode == MODE_DAMP :
        # do damping mode. pulse the kicker to receive a pass and transfer energy. Happens only once and exits.
        charge_stop = 1
        damp(pulse_freq, duty, 5000)
        # done damping, recharge HV for kick. Need to tell PI when it is charged using the done signal.
        charge_stop = 0
    elif mode == MODE_DISCHARGE :
        # discharge the HV using 7Hz pulses (using the same damping feature)
        charge_stop = 1
        damp(7,10,1500)
    else :
        # unknown command
        print("unknown command")