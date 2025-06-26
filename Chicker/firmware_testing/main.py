from machine import Pin, ADC
from time import sleep
from array import array
import math
import utime
import random
import sys, select

CHIP_IGBT = Pin(2, Pin.OUT)
KICK_IGBT = Pin(3, Pin.OUT)

?_SENSE = ADC(Pin(28)) # channel 2
?_SENSE = ADC(Pin(29)) # channel 3 (not sure which it maps to)

current_time = utime.ticks_ms()

# turn kick on. wait 50ms. check ADC. if low (ish), good. otherwise, flag
# turn kick off. wait 50ms. check ADC. if high, good. otherwise, flag

#repeat for chip IGBT

while True:
    #Read Inputs:
    current_time = utime.ticks_ms()
    #current_ustime = utime.ticks_us()
    
    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = DONE.value()  #DONE.value()#done_sim # 
    CHARGE.value(charge)
    #print(charge_disable)
    
    

    
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
            #print("Kicking in 2 seconds, at ", delay_time_us_temp, "us. Stand back!")
    elif (data_rec == 1) :
        #if (current_time - prev_kick_time >= 2000):
        if (done_state == 0):
            prev_kick_time = current_time
            delay_time_us = delay_time_us_temp
            data_rec = 0
            data = ""
            kick_delayed = 1
            
        else :
            delay_time_us = 0
            if (kick_delayed == 0) :
                print("DONE is HIGH (Charging), kicking delayed")
                kick_delayed = 1
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
    elif (current_time - prev_sim_time >= 2300+1702):
        done_sim = 0
        #print(current_time/1000)
        #print("done signal set to 0")
        prev_sim_time = 0
        
    prev_charge = charge
    
    if (current_time - startup_time >= 100 and current_time - startup_time <= 101):
        charge_ok_sim = 1
        print("VCC on")
    if (current_time - startup_time >= 24000 and current_time - startup_time <= 24002):
        charge_ok_sim = 0
        print("VCC off")
        #print("Random number:",charge_ok_sim)
    if (current_time - startup_time >= 27000 and current_time - startup_time <= 27002):
        charge_ok_sim = 1
        print("VCC on")
        #print("Random number:",charge_ok_sim)
    ###############################################
    '''
    