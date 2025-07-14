from machine import Pin, ADC
from time import sleep
from array import array
import math
import utime
import random
from battery import Voltages
from high_voltage import SenseHV
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
check_5s_done = 0
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


data = None
new_can_data_bool = False
can_rx_time = None
AUTOKICK_EXPIRE_THRESH_MS = 2000
stored_prekick_can_data = None
DAMP_TIMEOUT = 5000
DAMP_INITIAL_PULSEWIDTH = 200

offset = 1000_000

MODE_IDLE = 0
MODE_KICK = 1
MODE_DAMP = 2
mode = 0

def run_setup() :
    done_sim = current_time
    prev_time_int = current_time
    prev_time_can = current_time
    prev_time_volt = current_time
    prev_time_HV = current_time
    prev_time_start_chg = current_time
    prev_time_wait_charge_vcc = current_time
    prev_sim_time = current_time
    prev_time_chg_wait = current_time
    prev_time_charge_disabled = current_time
    prev_time_countdown = current_time
    prev_sim_charge_time = current_time
    prev_pulse_time = current_time
    prev_kick_time = current_time
    startup_time = current_time
    prev_time_damp = utime.ticks_us()
    prev_cycle_damp = utime.ticks_us()
    prev_time_startDAMP = utime.ticks_us()
    charge_ok = Voltages(charge_ok, startup) #
    {HV_voltage,HV_scaling} = SenseHV()
    pattern=(8, 8, 8)
    start=0
    ar = array("L", pattern)
    #kickpulse.put_pulses(ar, start)
    pulses.put_pulses(ar,start)
    #print(ledpulse.put_done)
    #print(kickpulse.put_done)
    CAN_LED.value(0)
    KICK.value(0)
    CHIP.value(0)
    #TESTPIN.value(1)
    
    startup_chg_2sdelay = 1
    startup = 1
    charge_stop = 0


def idle():
    # Idle mode keeps HV discharged and stops charging.
    charge_stop = 1
    NOT_DISCHARGE.value(0)


def kick():
    charge_stop = 0
    NOT_DISCHARGE.value(1)

    if (data_rec == 0 and kick_cooldown == 0):
        if (data == None) :
            data_rec = 0
            delay_time_us_temp = 0
        else:
            data_rec = 1
            charge_stop = 1
            pulse_width = int(data.data[1]) | int(data.data[0]) << 8
            # We processed this frame, so set new data flag to false
            new_can_data_bool = False

            {HV_voltage, HV_scaling} = SenseHV()
            # HV Pulse width adjustment
            pulse_width_adjusted = HV_scaling * pulse_width
            delay_time_us_temp = pulse_width_adjusted
            # CAN_LED.value(0)

            if (delay_time_us_temp > 5000) :
                delay_time_us_temp = 5000 # set a limit to the delay time
                print("Pulse duration too long, setting to 5000us")
            elif (delay_time_us_temp < 0):
                delay_time_us_temp = 0
            #print("Kicking in 2 seconds, at ", delay_time_us_temp, "us. Stand back!")
    elif (data_rec == 1) :
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
    
def damp(damp_freq, damp_duty, damp_stop_time):
    # NEED TO IMPLEMENT A MICROSECOND TIMER OR THIS WILL NOT WORK!
    # utime.ticks_us() works!

    prev_time_damp = current_time
    charge_stop = 1
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
        if (damp_stop_time > DAMP_TIMEOUT):
            damp_stop_time = DAMP_TIMEOUT
        while (current_time - prev_time_damp < damp_stop_time or HV_voltage > 10) :
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
    # done damping, recharge HV for kick. Need to tell PI when it is charged using the done signal.
    charge_stop = 0

def charge_cycle_start():
    if (startup_chg_2sdelay == 1): 
        if (startup_vcc_wait == 0):
            if (charge_ok == 1):
                prev_time_wait_charge_vcc = current_time
                startup_vcc_wait = 1
            else :
                # perhaps here can modify pulse width based on HV. Not sure the calibration
                print("charging disabled due to kick requested")
                # charging is disabled due to kick requested
        else:
            if (current_time - prev_time_wait_charge_vcc >= 2000):  
                # wait for 2 seconds then start charge cycle
                charge = 0
                startup_cycle = 1
                # set wait to toggle charge pin
                charge_toggle_wait = 0
                #print(current_time/1000, done_state)
                print("charge toggle started on VBAT received after delay")
                #startup_cycle = 1
                prev_time_start_chg = current_time
                startup_vcc_wait = 0
                startup_chg_2sdelay = 0
            
    if (charge_ok == 0):
        startup_chg_2sdelay = 1
        startup = 1
        done_sim = 1

def charge_cycle_run() :
    # charge_toggle_wait should be 0 if ready to start charging, and 1 if waiting after starting a charge cycle
        if (charge_toggle_wait == 0):
           # done will be 0 if either hasnt started charging, or has finished charging
            if (startup_cycle == 1):
                if (current_time - prev_time_start_chg >= 50 and current_time - prev_kick_time >= 50): 
                    charge = 1
                    startup_cycle = 0
                    charge_toggle_wait = 1
                    prev_time_chg_wait = current_time
                    #print(current_time/1000, done_state)
                    print("charge toggle complete on VCC input, waiting for 15s")
            elif (startup_chg_2sdelay == 0) :
                #print("waiting for 2 seconds to charge")
                if (done_state == 0):
                    # setup charge toggle wait time to set charge pin high
                    if (current_time - prev_time_start_chg >= 50 and current_time - prev_kick_time >= 50): 
                        charge = 1
                        charge_toggle_wait = 1
                        prev_time_chg_wait = current_time
                        #print(current_time/1000, done_state)
                        print("charge toggle complete on DONE input, waiting for 15s")
                else :
                    charge_disable = 1
                    prev_time_charge_disabled = current_time
                    if (safe_charge == 0):
                        #print("Safe Charge mode active, charged to 50V and stopped. Need to power cycle to restart safe charge")
                    #else :   
                        print("DONE is still high - Potential reasons: Undervoltage Lockout, Thermal Shutdown")
                        print("Retrying in 30 seconds")
            
        # wait for charge cycle to do the charge toggle every 15 seconds
        elif (charge_started == 1):
            if (safe_charge == 1):
                if (current_time - prev_time_chg_wait >= 236):#236 = 50V ish,  # 1108 = 160V ish (175 in sim)
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_5s_done = 0
                    charge_disable = 1
                    #print(current_time/1000, done_state)
                    print("Safe Charge mode, Charge pin reset at 50V")
                    # set charge pin
            else :
                if (current_time - prev_time_chg_wait >= 14950):
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_5s_done = 0
                    #print(current_time/1000, done_state)
                    print("charge toggle (15s waited)")
                    # set charge pin
                # charge toggle wait is 1, charge_started should be 1 unless its an error    
                # check after 5 seconds if the done signal actually goes low after being high for charging
                elif (check_5s_done == 0 and current_time - prev_time_chg_wait >= 5000):
                    check_5s_done = 1
                    if(done_state == 0):
                        #good
                        charge_disable = 0
                        print("DONE is low after 5s, assuming normal operation")
                    else :
                        # not good, done does not go low, implying not charging properly
                        charge_disable = 1
                        prev_time_charge_disabled = current_time
                        #print(current_time/1000, done_state)
                        print("charging disabled, done does not go low: TIMEOUT 5s, not charging properly")
                        print("Retrying in 30 seconds")
                    
        # charge_toggle wait is 1 and charge_started is 0, implying a "started" charge cycle, need to check
        # check after 50ms if done actually toggles high, implying good flyback chip
        elif (current_time - prev_time_chg_wait >= 50):
            if(done_state == 1):
                # good
                charge_started = 1 # variable for the 5s timeout check
                charge_disable = 0
                #print(current_time/1000, done_state)
                print("charge started (DONE toggles high properly)")
            else :
                # not good, disable charging
                charge_started = 0
                charge_disable = 1
                prev_time_charge_disabled = current_time
                #print(current_time/1000, done_state)
                print("charging was disabled, no high DONE signal received: no charge cycle started")
                print("Retrying in 30 seconds")

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
            # CAN_LED.value(1)
            # If the breakbeam is already broken, kick immediately
            if BREAKBEAM.value() == 0:
                data = can_data
            # Otherwise, queue up an autokick using interrupt
            else:
                stored_prekick_can_data = can_data
                new_can_data_bool = True
            can_rx_time = utime.ticks_ms()
            print("COMMAND RECEIVED")
    if can_rx_time is not None and (current_time - can_rx_time > AUTOKICK_EXPIRE_THRESH_MS):
        new_can_data_bool = False

    # Mode Select (Idle, Kick, Damp)
    if mode == MODE_IDLE :
        # do idle. HV discharged, stops charging.
        idle()
    elif mode == MODE_KICK :
        # do kick mode. Ready to receive a kick command.
        kick()
    elif mode == MODE_DAMP :
        # do damping mode. pulse the kicker to receive a pass and transfer energy.
        damp(damp_freq, damp_duty, damp_stop_time)
    else :
        # unknown command
        print("unknown command")
        pass

        
    # do this only on startup
    if (startup == 0):
        run_setup()
        
    # start a new charge cycle if the battery is plugged in
    if (charge_stop == 0) :
        charge_cycle_start()
    else :
        print("charging disabled")
        #startup_cycle = 0
        
    '''Note,

    . Any fault conditions such as thermal shutdown or undervoltage lockout will also turn on the NPN.
        for the DONE pin, which means it will be high if there is a problem
    '''
    if (charge_stop == 0 charge_ok == 1 and charge_disable == 0):
        charge_cycle_run()
    else :
        # not good, charging was disabled.
        charge = 0
        charge_started = 0 # reset charge_started to zero for the next charge cycle
        charge_toggle_wait = 0
        check_5s_done = 0
        # check flyback again in 5 seconds by reasserting the charge_disable variable'
        if (safe_charge == 1):
            charge_disable = 1
        '''
        else :
            if (charge_disable == 1):
                if (current_time - prev_time_countdown >= 1000):
                    print("...",int((5000 - (current_time - prev_time_charge_disabled))/1000), "...")
                    prev_time_countdown = current_time
                if (current_time - prev_time_charge_disabled >= 5000):
                    charge_disable = 0
        '''   
        # maybe add checks on how to enable it after this point?? 
    # check voltages every 2 seconds
    if(current_time - prev_time_volt >= 2000):
        charge_ok = Voltages(charge, startup) #charge_ok_sim
        prev_time_volt = current_time
        # enable disable timer
        if (charge_ok == 0):
            print("Charging Disabled, Battery Unplugged")
        
        
        #else:
            #print("VCC OK")
        
        #print("CHARGE OK:", charge_ok)
        #print("Charge toggle wait", charge_toggle_wait)
    
    #check high voltage constantly to be able to adjust kick power.
    {HV_voltage, HV_scaling} = SenseHV()


    # toggle LEDs at different intervals
    #CAN_LED.value(charge)
    #INT_LED.value(data_rec)
    
    #if (current_time - prev_time_can >= 50):
     #   print(startup_cycle)
     #   prev_time_can = current_time
    '''
    if (current_time - prev_time_can >= 200):
        if (delay_time_us == 0):
            #delay_time_us_temp = 2 # 1000 is 100us, 100 is 10us, very accurate, 10 is 1us, also pretty accurate
            delay_time_us = 210#int(delay_time_us_temp*20.0) -> 100us = 111us
            #50us = 62us
            #12us = 14us (very weird triangular signal tho)
            #print("delay set to 5000 us")
        else:
            delay_time_us = 0
            #print("delay reset")
        prev_time_can = current_time
    '''
