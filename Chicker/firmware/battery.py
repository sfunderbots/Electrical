import utime
import uasyncio
from machine import Pin, ADC
BATT_LEVEL = ADC(Pin(26)) #channel 0
ref_5v = ADC(Pin(27)) # channel 1

prev_time_voltages_startup = 0
def Voltages(charge_ok, startup):
    # BATTERY voltage detect
    #274.2*2
    BATT_LEVEL_val = BATT_LEVEL.read_u16() # read value, 0-65535 across voltage range 0.0v - 3.3v
    battery_voltage_raw = BATT_LEVEL_val * (3.3 / 65535.0) * ((200.0+940.0)/200.0)#((164.1 + 172.0)/164.1)
    battery_voltage = round(battery_voltage_raw,2)
    
    battery_voltage = battery_voltage / 15.7 * 16.76
    #battery_voltage = round(battery_voltage_raw*1.05893540282*max(battery_voltage_raw * 1.0342 - 0.5339, 0), 2)
    # note this scale is relative to high level, low voltage is 2V is 2.45V (reading is higher) and 16.8V is 16.76V (reading is lower)
    #if (battery_voltage_raw < 1):
    #    battery_voltage = BATT_LEVEL_val *3.3 / 65535.0 * (20 + 94)/20
    
    # 5V Voltage (detecting USB vs board 5V)
    # 200ohm offset is equivalent to 150uA* 200 ohms = 0.3V -> 3.3V is 65535 / 65135 = 1.00614 (need to fix the board for next rev)
    ref_5V_val = ref_5v.read_u16()
    ref_5V_raw = ref_5V_val *3.3 / 65535.0 * (35.0 + 24.2)/24.2 #(51.0+30.0)/30.0 (somehow this is backwkards and i have no idea why)
    net_5V = round(ref_5V_raw, 2)     #37865 * 3.3/ 65535 * 
    #
    print(battery_voltage)
    if ( startup == 0):
        if (net_5V > 4.45) and (net_5V <= 4.70 ):
            print("4.6V USB Voltage")
        elif (net_5V > 4.70) and (net_5V <= 4.90):
            print("4.8V Battery Voltage (USB could still be plugged in, check LED)")
        elif (net_5V > 4.90) and (net_5V <= 5.20):
            print("WARNING!!!!: Series diodes for voltage switch has failed, check circuit!")
        else :
            print("dont worry, the 5V is actually probably not broken due to ADC but check maybe?")
      
    if (battery_voltage >  5.0) and (battery_voltage <= 12.8):
        #print("Batteries are Exceptionally low, damage is likely!!")
        charge_ok = 0
    elif (battery_voltage >  12.8) and (battery_voltage <= 13.3):
        #print("12.8V to 13.3V GUYS! Batteries are VERY Low! Replace them now!!!!")
        charge_ok = 0
    elif (battery_voltage >  13.3) and (battery_voltage <= 13.8):
        #print("13.3V to 13.8V Battery voltage is low, turning off HV charging, batteries need to be replaced!")
        charge_ok = 0
    elif (battery_voltage > 13.8) and (battery_voltage <= 14.7):
        #print("13.8V to 14.7V Batteries are below nominal, should replace soon")
        charge_ok = 0
    elif (battery_voltage > 14.7) and (battery_voltage <= 14.9):
        #print("14.7V to 14.9V Nominal Battery Voltage")
        charge_ok = 1
    elif (battery_voltage > 14.9) and (battery_voltage <= 16.4):
        print("Working Battery Voltage")
        charge_ok = 1
    elif (battery_voltage > 16.4) and (battery_voltage <= 17.0):
        print("Fully charged  Batteries (or close to it)")
        charge_ok = 1
    else :
        #print("Batteries are unplugged")
        charge_ok = 0
        
    print("5V Reference: ", net_5V, "V")
    print("Battery: ", battery_voltage, "V")
    #print("HV: ", HV_voltage, "V")
    
    #charge_ok = 1
    return charge_ok
    
