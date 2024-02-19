from machine import Pin, ADC
from time import sleep



CAN_LED = Pin(6, Pin.OUT)
INT_LED = Pin(20, Pin.OUT)

DONE = Pin(4, Pin.IN)
SHELL_OFF = Pin(9, Pin.IN)
BREAKBEAM = Pin(23, Pin.IN)

CHARGE = Pin(5, Pin.OUT)
CHIP = Pin(3, Pin.OUT)
KICK = Pin(2, Pin.OUT)
NOT_DISCHARGE = Pin(8, Pin.OUT)

BATT_LEVEL = ADC(Pin(26))
ref_3v3 = ADC(2)

while True:
    BATT_LEVEL_val = BATT_LEVEL.read_u16() # read value, 0-65535 across voltage range 0.0v - 3.3v
    logic_3v3_ref_val = ref_3v3.read_u16()    
    battery_voltage = BATT_LEVEL_val *3.3 / 65535.0 * (20 + 94)/20
    logic_voltage = logic_3v3_ref_val *3.3 / 65535.0 * 1.00614
    CAN_LED.value(1)
    INT_LED.value(0)
    sleep(0.6)
    INT_LED.value(1)
    sleep(0.4)
    print(logic_voltage)
    print(battery_voltage)
    