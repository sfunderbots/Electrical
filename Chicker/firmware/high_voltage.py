import utime
import uasyncio
from machine import Pin, ADC
HV_SENSE = ADC(Pin(29)) # channel 3

prev_time_voltages_startup = 0
def SenseHV():
    # High Voltage Reference = 206V
    #######################################
    # High Voltage Sense Detection
    HV_level_val = HV_SENSE.read_u16()
    HV_voltage_raw = HV_level_val * (3.3 / 65535.0) * ((13.0 + 990.0)/13.0)
    HV_voltage = round(HV_voltage_raw,2)

    print("HV: ", HV_voltage, "V")
    
    #imagine you receive a pulse width of 1800us. High voltage is currently 180V. 
    # Need to pulse longer (theoretically) to achieve same result to m/s
    # pulse width_new = pulse_width * 206/ HV_voltage (this is approximate)

    if (HV_voltage < 150): # check if it is way out of bounds
        HV_scaling = 1
    else:
        HV_scaling = 206.0 / HV_voltage
    return HV_voltage
    

