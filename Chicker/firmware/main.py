from machine import Pin, ADC
from time import sleep
import utime
import outputs
import queue
from battery import Voltages


DONE = Pin(4, Pin.IN)
SHELL_OFF = Pin(9, Pin.IN)
BREAKBEAM = Pin(23, Pin.IN)

#CHARGE = Pin(5, Pin.OUT)
#CHIP = Pin(3, Pin.OUT)
#KICK = Pin(2, Pin.OUT)
#NOT_DISCHARGE = Pin(8, Pin.OUT)

CAN_LED = Pin(6, Pin.OUT)
INT_LED = Pin(20, Pin.OUT)
prev_time_int = 0
prev_time_can = 0
prev_time_volt = 0

while True:
    
    current_time = utime.ticks_ms()

    if(current_time - prev_time_can > 400):
        CAN_LED.toggle()
        prev_time_can = utime.ticks_ms()
    if(current_time - prev_time_int > 800):
        INT_LED.toggle()
        prev_time_int = utime.ticks_ms()    
    if(current_time - prev_time_volt > 2000):
        Voltages(charge)
        prev_time_volt = utime.ticks_ms()
    if (charge == 1 and done == 0)
        # set charge pin
        
       
        
    