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

BATT_LEVEL = ADC(Pin(26)) #channel 0
ref_5v = ADC(Pin(27)) # channel 1
ref_3v3 = ADC(Pin(28)) # channel 2

while True:
    BATT_LEVEL_val = BATT_LEVEL.read_u16() # read value, 0-65535 across voltage range 0.0v - 3.3v
    logic_3v3_ref_val = ref_3v3.read_u16()
    ref_5V_val = ref_5v.read_u16()
    battery_voltage = round(BATT_LEVEL_val *3.3 / 65535.0 * (20 + 94)/20, 2)
    logic_voltage = round(logic_3v3_ref_val *3.3 / 65535.0 * 1.00614, 2)
    net_5V = round(ref_5V_val *3.3 / 65535.0 * 1.00614 * (5.1+3.0)/3.0, 2) # this is incorrect need to fix later, put the resistor divider in backwards
    CAN_LED.value(1)
    INT_LED.value(0)
    sleep(0.6)
    INT_LED.value(1)
    sleep(0.4)
    
    if (net_5V > 4.45) and (net_5V <= 4.70 ):
        print("4.6V USB Voltage")
    elif (net_5V > 4.70) and (net_5V <= 4.90):
        print("4.8V Battery Voltage (USB could still be plugged in, check LED)")
    elif (net_5V > 4.90) and (net_5V <= 5.20):
        print("WARNING!!!!: Series diodes for voltage switch has failed, check circuit!")
    else :
        print("uh, guys, somethings really broken!!!! Seriously!")
        
    if (battery_voltage >  1.0) and (battery_voltage <= 12.8):
        print("Batteries are Exceptionally low, damage is likely!!")
    elif (battery_voltage >  12.8) and (battery_voltage <= 13.3):
        print("12.8V to 13.3V GUYS! Batteries are VERY Low! Replace them now!!!!")
    elif (battery_voltage >  13.3) and (battery_voltage <= 13.8):
        print("13.3V to 13.8V Battery voltage is low, turning off HV charging, batteries need to be replaced!")
    elif (battery_voltage > 13.8) and (battery_voltage <= 14.7):
        print("13.8V to 14.7V Batteries are below nominal, should replace soon")
    elif (battery_voltage > 14.7) and (battery_voltage <= 14.9):
        print("14.7V to 14.9V Nominal Battery Voltage")
    elif (battery_voltage > 14.9) and (battery_voltage <= 16.4):
        print("Working Battery Voltage")
    elif (battery_voltage > 16.4) and (battery_voltage <= 16.9):
        print("Fully charged  Batteries (or close to it)")
    else :
        print("Batteries are unplugged")
    print("3.3V Reference: ", logic_voltage, "V")
    print("5V Reference: ", net_5V, "V")    