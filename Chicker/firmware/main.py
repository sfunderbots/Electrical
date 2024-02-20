from machine import Pin, ADC
from time import sleep
import utime
import outputs
import queue
from battery import Voltages


DONE = Pin(4, Pin.IN)
#SHELL_OFF = Pin(9, Pin.IN)
#BREAKBEAM = Pin(23, Pin.IN)

CHARGE = Pin(5, Pin.OUT)
#CHIP = Pin(3, Pin.OUT)
KICK = Pin(2, Pin.OUT)
#NOT_DISCHARGE = Pin(8, Pin.OUT)

CAN_LED = Pin(6, Pin.OUT)
INT_LED = Pin(20, Pin.OUT)
prev_time_int = 0
prev_time_can = 0
prev_time_volt = 0
prev_time_start_chg = 0
prev_sim_time = 0
prev_time_chg_wait = 0
charge_ok = 0
startup = 0
charge_toggle_wait = 0
done_sim = 0
charge = 0
charge_disable = 0
charge_started = 0
while True:
    #Read Inputs:
    
    current_time = utime.ticks_ms()
    
    #simulation values
    
    # if (rising edge of charge ?):
        # set done_sim = 1
        # prev_sim_time = current_time
    # if (current_time - prev_sim_time > 1700)
        #done_sim = 0
            
    done_state = done_sim #DONE.value()
    # do this only on startup
    if (startup == 0):
        charge_ok = Voltages(charge_ok)
        
        if (charge_ok == 1 and done_state == 0):
            charge = 0
            # set wait to toggle charge pin
            charge_toggle_wait = 1
            print("charge toggle on startup")
        # finished startup routine, this will only happen again on reset or boot
        startup = 1
        
    # toggle LEDs at different intervals
    if(current_time - prev_time_can > 400):
        CAN_LED.toggle()
        prev_time_can = current_time
    if(current_time - prev_time_int > 800):
        INT_LED.toggle()
        prev_time_int = current_time
        
    # check voltages every 2 seconds
    if(current_time - prev_time_volt > 2000):
        charge_ok = Voltages(charge)
        prev_time_volt = current_time
        #print("CHARGE OK:", charge_ok)
        #print("Charge toggle wait", charge_toggle_wait)
        
    

    # done will be 0 if either hasnt started charging, or has finished charging
    if (charge_ok == 1 and done_state == 0 and charge_disable == 0):
        # setup charge toggle wait time to set charge pin high
        if (charge_toggle_wait == 1):  
            if(current_time - prev_time_start_chg > 50):
                charge = 1
                charge_toggle_wait = 0
                prev_time_chg_wait = current_time
                print("charge toggle complete, waiting for 15s")
        # wait for charge cycle to do the charge toggle every 15 seconds
        elif (current_time - prev_time_chg_wait >= 14950):
            charge = 0
            charge_toggle_wait = 1
            print("charge toggle")
            # set charge pin
        # check after 5 seconds if the done signal actually goes low after being high for charging
        elif (current_time - prev_time_chg_wait > 5000 and charge_started == 1):
            charge_started = 0 # reset charge_started to zero for the next charge cycle
            if(done_state == 0):
                #good
                charge_disable = 0
            else :
                # not good, done does not go low, implying not charging properly
                charge_disable = 1
                print("charging disabled, done does not go low: TIMEOUT 5s, not charging properly")
        # check after 50ms if done actually toggles high, implying good flyback chip
        elif (current_time - prev_time_chg_wait > 50 and charge_started == 0):
            if(done_state == 1):
                # good
                charge_started = 1 # variable for the 5s timeout check
                charge_disable = 0
            else :
                # not good, disable charging
                charge_started = 0
                charge_disable = 1
                print("charging was disabled, no high DONE signal received: no charge cycle started")
    #else :
     #   print("Charging Disabled")
        # not good, charging was disabled.
        # need to add checks on how to enable it after this point
        
    