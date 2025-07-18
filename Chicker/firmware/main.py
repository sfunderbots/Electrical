from machine import Pin, ADC
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
prev_time_data = 0
prev_time_test = 0
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
charge_stop = 1

idle_mode = 0
kick_mode = 0
damp_mode = 0
disc_mode = 0


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
mode = 0

CAN_LED.on()

def idle():
    # Idle mode keeps HV discharged and stops charging.
    NOT_DISCHARGE.value(0)


def kick():
    #Global variables (FUCK ME...)
    global can_rx_time, new_can_data_bool, data, data_rec, kick_cooldown, delay_time_us_temp, delay_time_us
    global charge_stop, pulse_freq, new_can_data_bool, pulse_width_adjusted, done_state
    global prev_kick_time, kick_delayed, start, ar, prev_pulse_time, startup_chg_2sdelay
   
    if can_rx_time is not None and (utime.ticks_ms() - can_rx_time > AUTOKICK_EXPIRE_THRESH_MS):
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
            CAN_LED.value(0)

            if (delay_time_us_temp > 5000) :
                delay_time_us_temp = 5000 # set a limit to the delay time
                print("Pulse duration too long, setting to 5000us")
            elif (delay_time_us_temp < 0):
                delay_time_us_temp = 0
            #print("Kicking in 2 seconds, at ", delay_time_us_temp, "us. Stand back!")
    else :
        if (done_state == 0):
            prev_kick_time = utime.ticks_ms()
            delay_time_us = delay_time_us_temp
            data_rec = 0
            data = None
        # charging still, wait till charging stopped from charge STOP to kick.
        else:
            delay_time_us = 0
            
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
        prev_pulse_time = utime.ticks_ms() # start pulse timer # this seems redundant honestly, we are only sending pulse widths 
        startup_chg_2sdelay = 1
            
    elif (kick_cooldown == 1 and utime.ticks_ms() - prev_pulse_time >= 100):
        kick_cooldown = 0
        delay_time_us = 0
        charge_stop = 0

# damp_freq = freq in Hz (two bytes)
# damp_duty_percent = duty cycle in percentage (integer)
# damp_timeout = timeout in milliseconds
def damp(damp_freq, damp_duty_percent, damp_timeout):
    #Global variables (FUCK ME...)
    global prev_time_damp, start, ar, prev_time_test

    # utime.ticks_us() works!
    prev_time_damp = utime.ticks_ms()
    damp_duty = damp_duty_percent / 100.0
    NOT_DISCHARGE.value(1)

    # Initial Kick (to put plunger out)
    pattern=(8, DAMP_INITIAL_PULSEWIDTH, 8)
    start=0
    ar = array("L", pattern)
    pulses.put_pulses(ar,start)
    damp_period = 1/damp_freq *1000_000 # needs to be in microseconds
    damp_on_time = damp_period*damp_duty # microseconds
    damp_off_time = damp_period - damp_on_time #microseconds

    prev_cycle_damp = utime.ticks_us()
    # implement a delay here of duty off time.
    # keep damping until preset timetimeout or HV discharged
    while (utime.ticks_ms() - prev_time_damp < damp_timeout): # or HV_voltage > 10) :
        
        if (utime.ticks_us() - prev_time_damp*1000 > DAMP_INITIAL_PULSEWIDTH):
            if (utime.ticks_us() - prev_cycle_damp < damp_off_time) :
                # turn off kicker
                KICK.value(0)
                #CAN_LED.off()
                # 
            elif (utime.ticks_us() - prev_cycle_damp < damp_period) :
                # turn on kicker
                KICK.value(1)
                #CAN_LED.on()
                prev_cycle_damp = utime.ticks_us()
            else:
                print("something broken")
    KICK.value(0)
    #CAN_LED.off()

# Interrupt handler
def breakbeam_handler(pin):
    global new_can_data_bool, data, stored_prekick_can_data
    if not pin.value():
        CAN_LED.value(1)   # Rising edge: turn on LED
        if new_can_data_bool: # Only kick if armed
            data = stored_prekick_can_data
    else:
        pass
        CAN_LED.value(0)  # Falling edge: turn off LED

# Attach interrupt for both edges
BREAKBEAM.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=breakbeam_handler)

#kickpulse = pulses.Pulses(None, KICK, 1_000_000)
pulses = pulses.Pulses(None, KICK, 1_000_000)

while True:
        
    ###############################################
    #simulation values (correct ones)
    # rising edge of charge pin
    if (charge == 1 and prev_charge == 0):
        done_sim = 1
        prev_sim_time = utime.ticks_ms()
        #print(utime.ticks_ms()/1000)
        #print("done signal set to 1")        
    #elif (charge == 1) :
    elif (utime.ticks_ms() - prev_sim_time >= 300):
        done_sim = 0
        #print(utime.ticks_ms()/1000)
        #print("done signal set to 0")
        prev_sim_time = 0
        
    prev_charge = charge

    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = done_sim #DONE.value() # doneSIM 
    CHARGE.value(charge)
    #print(charge_disable)
    
    '''
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
                    print("KICK COMMAND RECEIVED")
    '''
    # BYTE 0             = MODE
    #
    # BYTE 1-2 IN MODE 1 = PULSE WIDTH 
    # BYTE 1-2 IN MODE 2 = DAMP_FREQ
    # BYTE 3             = DUTY (In Percentage)

    # Mode Select (Idle, Kick, Damp)
    if mode == MODE_IDLE :
        # do idle. HV discharged, stops charging.
        charge_stop = 1
        kick_mode = 0
        
        if (idle_mode == 0):
            print("IDLE MODE: HV DISCHARGED. STOPS CHARGING.")
            idle_mode = 1
        
        idle()
    elif mode == MODE_AUTOKICK :
        charge_stop = 0
        idle_mode = 0
        if (kick_mode == 0):
            print("AUTOKICK MODE: HV CHARGED. READY TO RECEIVE KICK COMMAND")
            kick_mode = 1
        # do kick mode. Ready to receive a kick command. High Voltage is Charged
        kick()
    elif mode == MODE_DAMP :
        # do damping mode. pulse the kicker to receive a pass and transfer energy. Happens only once and exits.
        kick_mode = 0
        idle_mode = 0
        print("DAMPING MODE: HV STOPS CHARGING. SENDS A PULSE TO THE KICKER TO HOLD FOR DAMPING")
        charge_stop = 1
        damp(pulse_freq, duty, 5000)
        # done damping, recharge HV for kick. Need to tell PI when it is charged using the done signal.
        charge_stop = 0
    else :
        # unknown command
        print("unknown command")
        pass
    
    # do this only on startup
    if (startup == 0):
        done_sim = 1
        prev_time_int = utime.ticks_ms()
        prev_time_can = utime.ticks_ms()
        prev_time_volt = utime.ticks_ms()
        prev_time_HV = utime.ticks_ms()
        prev_time_start_chg = utime.ticks_ms()
        prev_time_wait_charge_vcc = utime.ticks_ms()
        prev_sim_time = utime.ticks_ms()
        prev_time_chg_wait = utime.ticks_ms()
        prev_time_charge_disabled = utime.ticks_ms()
        prev_time_countdown = utime.ticks_ms()
        prev_sim_charge_time = utime.ticks_ms()
        prev_pulse_time = utime.ticks_ms()
        prev_kick_time = utime.ticks_ms()
        startup_time = utime.ticks_ms()
        prev_time_damp = utime.ticks_ms()
        prev_cycle_damp = utime.ticks_us()
        prev_time_data = utime.ticks_ms()
        prev_time_test = utime.ticks_ms()
        charge_ok = Voltages(charge_ok, startup) #
        #{HV_voltage,HV_scaling} = SenseHV()
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
        
   # start a new charge cycle if the battery is plugged in
    if (startup_chg_2sdelay == 1): 
        if (startup_vcc_wait == 0):
            if (charge_ok == 1):
                prev_time_wait_charge_vcc = utime.ticks_ms()
                startup_vcc_wait = 1
        else:
            if (utime.ticks_ms() - prev_time_wait_charge_vcc >= 2000):  
                # wait for 2 seconds then start charge cycle
                charge = 0
                startup_cycle = 1
                # set wait to toggle charge pin
                charge_toggle_wait = 0
                #print(utime.ticks_ms()/1000, done_state)
                print("charge toggle started on VBAT received after delay")
                #startup_cycle = 1
                prev_time_start_chg = utime.ticks_ms()
                startup_vcc_wait = 0
                startup_chg_2sdelay = 0
            
    if (charge_ok == 0):
        startup_chg_2sdelay = 1
        startup = 1
        done_sim = 1
        #startup_cycle = 0
        
    '''Note,

    . Any fault conditions such as thermal shutdown or undervoltage lockout will also turn on the NPN.
        for the DONE pin, which means it will be high if there is a problem
    '''
    if (charge_ok == 1 and charge_disable == 0 and charge_stop == 0):
        # charge_toggle_wait should be 0 if ready to start charging, and 1 if waiting after starting a charge cycle
        if (charge_toggle_wait == 0):
           # done will be 0 if either hasnt started charging, or has finished charging
            if (startup_cycle == 1):
                if (utime.ticks_ms() - prev_time_start_chg >= 50 and utime.ticks_ms() - prev_kick_time >= 50): 
                    charge = 1
                    startup_cycle = 0
                    charge_toggle_wait = 1
                    prev_time_chg_wait = utime.ticks_ms()
                    #print(utime.ticks_ms()/1000, done_state)
                    print("charge toggle complete on VCC input, waiting")
            elif (startup_chg_2sdelay == 0) :
                #print("waiting for 2 seconds to charge")
                if (done_state == 0):
                    # setup charge toggle wait time to set charge pin high
                    if (utime.ticks_ms() - prev_time_start_chg >= 50 and utime.ticks_ms() - prev_kick_time >= 50): 
                        charge = 1
                        charge_toggle_wait = 1
                        prev_time_chg_wait = utime.ticks_ms()
                        #print(utime.ticks_ms()/1000, done_state)
                        print("charge toggle complete on DONE input, waiting")
                else :
                    charge_disable = 1
                    prev_time_charge_disabled = utime.ticks_ms()
                    if (safe_charge == 0):
                        #print("Safe Charge mode active, charged to 50V and stopped. Need to power cycle to restart safe charge")
                    #else :   
                        print("DONE is still high - Potential reasons: Undervoltage Lockout, Thermal Shutdown")
                        print("Retrying in 30 seconds")
            
        # wait for charge cycle to do the charge toggle every second
        elif (charge_started == 1):
            if (safe_charge == 1):
                if (utime.ticks_ms() - prev_time_chg_wait >= 236):#236 = 50V ish,  # 1108 = 160V ish (175 in sim)
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_3s_done = 0
                    charge_disable = 1
                    #print(utime.ticks_ms()/1000, done_state)
                    print("Safe Charge mode, Charge pin reset at 50V")
                    # set charge pin
            else :
                if (utime.ticks_ms() - prev_time_chg_wait >= 1000 and done_state == 1):
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_3s_done = 0
                    #print(utime.ticks_ms()/1000, done_state)
                    print("charge toggle (1s waited)")
                    # set charge pin
                # charge toggle wait is 1, charge_started should be 1 unless its an error    
                # check after 5 seconds if the done signal actually goes low after being high for charging
                elif (check_3s_done == 0 and utime.ticks_ms() - prev_time_chg_wait >= 500):
                    check_3s_done = 1
                    if(done_state == 0):
                        #good
                        charge_disable = 0
                        print("DONE is low after 500ms, assuming normal operation")
                    else :
                        # not good, done does not go low, implying not charging properly
                        charge_disable = 1
                        prev_time_charge_disabled = utime.ticks_ms()
                        #print(utime.ticks_ms()/1000, done_state)
                        print("charging disabled, done does not go low: TIMEOUT 5s, not charging properly")
                        print("Retrying in 30 seconds")
                    
        # charge_toggle wait is 1 and charge_started is 0, implying a "started" charge cycle, need to check
        # check after 50ms if done actually toggles high, implying good flyback chip
        elif (utime.ticks_ms() - prev_time_chg_wait >= 50):
            if(done_state == 1):
                # good
                charge_started = 1 # variable for the 5s timeout check
                charge_disable = 0
                #print(utime.ticks_ms()/1000, done_state)
                print("charge started (DONE toggles high properly)")
            else :
                # not good, disable charging
                charge_started = 0
                charge_disable = 1
                prev_time_charge_disabled = utime.ticks_ms()
                #print(utime.ticks_ms()/1000, done_state)
                print("charging was disabled, no high DONE signal received: no charge cycle started")
                print("Retrying in 30 seconds")
    else :
        # not good, charging was disabled.
        charge = 0
        charge_started = 0 # reset charge_started to zero for the next charge cycle
        charge_toggle_wait = 0
        check_3s_done = 0
        # check flyback again in 5 seconds by reasserting the charge_disable variable'
        if (safe_charge == 1):
            charge_disable = 1
    
    #check high voltage constantly to be able to adjust kick power.
    #{HV_voltage, HV_scaling} = SenseHV()
    #if (utime.ticks_ms() - prev_time_test >= 500):
    #    print(prev_time_damp)
    #    print(utime.ticks_ms())
    #    prev_time_test = utime.ticks_ms()
        


    if (mode == 1 and utime.ticks_ms() - prev_time_data >= 2500 and utime.ticks_ms() - prev_time_data <=2502):
        data = pulse_freq
        #print(data)
    # check voltages every 2 seconds
    if(utime.ticks_ms() - prev_time_volt >= 8000):
        prev_time_data = utime.ticks_ms()
        #if (mode + 1 == 3):
        #    mode == 0
        #else : 
        #    mode = mode + 1
        mode = 2
        pulse_freq = 1
        duty = 70
        
        charge_ok = Voltages(charge, startup) #charge_ok_sim
        prev_time_volt = utime.ticks_ms()
        # enable disable timer
        if (charge_ok == 0):
            print("Charging Disabled, Battery Unplugged")