import utime
import uasyncio
from machine import Pin, ADC
HV_SENSE = ADC(Pin(29)) # channel 3

prev_time_voltages_startup = 0
def SenseHV():
    # High Voltage Sense Detection
    HV_level_val = HV_SENSE.read_u16();
    HV_voltage_raw = HV_level_val * (3.3 / 65535.0) * ((13.0 + 990.0)/13.0)
    HV_voltage = round(HV_voltage_raw,2);

    print("HV: ", HV_voltage, "V")
    
    #charge_ok = 1
    return HV_voltage
    

