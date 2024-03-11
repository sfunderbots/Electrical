from machine import Pin, ADC
from time import sleep
from array import array
import math
import utime
import random
from battery import Voltages
import sys, select
import pulses


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
prev_time_charge_disabled = 0
prev_time_countdown = 0
prev_sim_charge_time = 0
charge_ok = 0
prev_charge_ok = 0
startup = 0
startup_cycle = 0
charge_toggle_wait = 0
done_sim = 0
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
prev_pulse_time = 0
offset = 1000_000
data_rec = 0
prev_input_time = 0

#kickpulse = pulses.Pulses(None, KICK, 1_000_000)
pulses = pulses.Pulses(None, KICK, CAN_LED, 1_000_000)
'''
def put(pattern=(delay_time_us,), start=1):
    global pulses
    ar = array("H", pattern)
    pulses.put_pulses(ar, start)
    print(pulses.put_done)
'''    

while True:
    #Read Inputs:
    
    current_time = utime.ticks_ms()
    current_ustime = utime.ticks_us()
    '''
    res = select.select([sys.stdin], [], [], 0)
    data = ""
    while res[0]:
      data = data + sys.stdin.read(1)
      res = select.select([sys.stdin], [], [], 0)
      
    if (data_rec == 0 and kick_cooldown == 0):
        if (data == "") :
            data_rec = 0
            delay_time_us_temp = 0
        #elif (math.isnan(data) == 1): # if not a number set to 0 (default)
        #    delay_time_us = 0
        else :
            #print("stuff")
            data_rec = 1
            delay_time_us_temp = int(data)

            if (delay_time_us_temp > 5000) :
                delay_time_us_temp = 5000 # set a limit to the delay time
                print("Pulse duration too long, setting to 5000us")
            elif (delay_time_us_temp < 0):
                delay_time_us_temp = 0
            prev_input_time = current_time
            print("Kicking in 3 seconds, at ", delay_time_us_temp, "us. Stand back!")
    elif (data_rec == 1) :
        if (current_time - prev_input_time >= 3000):
            delay_time_us = delay_time_us_temp
            data = ""
            data_rec = 0
    '''
    CAN_LED.value(KICK.value())
    

    '''
    ###############################################
    #simulation values (correct ones)
    # rising edge of charge pin
    if (charge == 1 and prev_charge == 0):
        done_sim = 1
        prev_sim_time = current_time
        #print(current_time/1000)
        #print("done signal set to 1")        
    #elif (charge == 1) :
    elif (current_time - prev_sim_time >= 1702):
            done_sim = 0
            #print(current_time/1000)
            #print("done signal set to 0")
        
    prev_charge = charge
    #if (current_time - prev_sim_charge_time >= 12000):
    #    charge_ok_sim = random.randint(0,1)
     #   print("Random number:",charge_ok_sim)
     #   prev_sim_charge_time = current_time
    ###############################################
    '''
    if(kick_cooldown == 0 and delay_time_us != 0):
        kick_cooldown = 1
        pattern=(8, delay_time_us, 8 )
        start=0
        ar = array("L", pattern)
        #kickpulse.put_pulses(ar, start)
        pulses.put_pulses(ar,start)
        #print(ledpulse.put_done)
        #print(kickpulse.put_done)
        prev_pulse_time = current_ustime # start pulse timer
        
        #print("stufffffffffffffff")
    elif (kick_cooldown == 1 and current_ustime - prev_pulse_time >= 100000):
        kick_cooldown = 0
        delay_time_us = 0
        #print("cooldown")
    
    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = DONE.value()# done_sim #
    CHARGE.value(charge)
    # do this only on startup
    if (startup == 0):
        charge_ok = Voltages(charge_ok, startup) #charge_ok_sim 
        safe_charge = 0
        #CAN_LED.value(0)
        #INT_LED.value(0)
        prev_charge_ok = charge_ok
        
        pattern=(8, 8, 8)
        start=0
        ar = array("L", pattern)
        #kickpulse.put_pulses(ar, start)
        pulses.put_pulses(ar,start)
        #print(ledpulse.put_done)
        #print(kickpulse.put_done)
        #prev_pulse_time = current_ustime # start pulse timer
        
        # finished startup routine, this will only happen again on reset or boot
        startup = 1
    elif (prev_charge_ok == 0 and charge_ok == 1):
        charge = 0
        startup_cycle = 1
        # set wait to toggle charge pin
        charge_toggle_wait = 0
        #print(current_time/1000, done_state)
        print("charge toggle on startup")
        prev_time_start_chg = current_time
               
    # toggle LEDs at different intervals
    #CAN_LED.value(charge)
    INT_LED.value(data_rec)

    
    if(current_time - prev_time_can >=3000):
        if (delay_time_us == 0):
            delay_time_us = 5000
            #print("delay set to 5000 us")
        else:
            delay_time_us = 0
            #print("delay reset")
        prev_time_can = current_time
        #print(done_state)
    
    
    # check voltages every 2 seconds
    if(current_time - prev_time_volt >= 2000):
        charge_ok = Voltages(charge, startup) #charge_ok_sim
        prev_time_volt = current_time
        # enable disable timer
        if (charge_ok == 0):
            print("Charging Disabled, Battery Unplugged")
        #print(current_ustime)
        
        #print("CHARGE OK:", charge_ok)
        #print("Charge toggle wait", charge_toggle_wait)
        
    
    
    '''Note,

    . Any fault conditions such as thermal shutdown or undervoltage lockout will also turn on the NPN.
        for the DONE pin, which means it will be high if there is a problem
    '''
    if (charge_ok == 1 and charge_disable == 0):
        
        # charge_toggle_wait should be 0 if ready to start charging, and 1 if waiting after starting a charge cycle
        if (charge_toggle_wait == 0):
           # done will be 0 if either hasnt started charging, or has finished charging
            if (startup_cycle == 1):
                if (current_time - prev_time_start_chg >= 50): 
                    charge = 1
                    startup_cycle = 0
                    charge_toggle_wait = 1
                    prev_time_chg_wait = current_time
                    #print(current_time/1000, done_state)
                    print("charge toggle complete, waiting for 15s")
            elif (done_state == 0):
                # setup charge toggle wait time to set charge pin high
                if (current_time - prev_time_start_chg >= 50): 
                    charge = 1
                    charge_toggle_wait = 1
                    prev_time_chg_wait = current_time
                    #print(current_time/1000, done_state)
                    print("charge toggle complete, waiting for 15s")
            else :
                charge_disable = 1
                prev_time_charge_disabled = current_time
                if (safe_charge == 1):
                    print("Safe Charge mode active, charged to 50V and stopped. Need to power cycle to restart safe charge")
                else :   
                    print("DONE is still high - Potential reasons: Undervoltage Lockout, Thermal Shutdown")
                    print("Retrying in 30 seconds")
        # wait for charge cycle to do the charge toggle every 15 seconds
        elif (charge_started == 1):
            if (safe_charge == 1):
                if (current_time - prev_time_chg_wait >= 2100):#236 = 50V ish,  # 1108 = 160V ish (175 in sim)
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_5s_done = 0
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
    else :
        # not good, charging was disabled.
        charge = 0
        charge_started = 0 # reset charge_started to zero for the next charge cycle
        charge_toggle_wait = 0
        check_5s_done = 0
        # check flyback again in 30 seconds by reasserting the charge_disable variable'
        if (safe_charge == 1):
            charge_disable = 1
        else :
            if (charge_disable == 1):
                if (current_time - prev_time_countdown >= 1000):
                    print("...",int((30000 - (current_time - prev_time_charge_disabled))/1000), "...")
                    prev_time_countdown = current_time
                if (current_time - prev_time_charge_disabled >= 30000):
                    charge_disable = 0
            
        # maybe add checks on how to enable it after this point??   
    