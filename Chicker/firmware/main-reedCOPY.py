from machine import Pin, ADC
from time import sleep
from array import array
import math
import utime
import random
from battery import Voltages
from high_voltage import SenseHV
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
BREAKBEAM = Pin(23, Pin.IN)

CHARGE = Pin(5, Pin.OUT)
CHIP = Pin(2, Pin.OUT)
KICK = Pin(3, Pin.OUT)
NOT_DISCHARGE = Pin(8, Pin.OUT)
TESTPIN = Pin(24, Pin.OUT)
CAN_LED = Pin(6, Pin.OUT)
INT = Pin(20, Pin.IN)

<<<<<<< Updated upstream
data = None
new_can_data_bool = False
can_rx_time = None
AUTOKICK_EXPIRE_THRESH_MS = 2000
stored_prekick_can_data = None

# Interrupt handler
def breakbeam_handler(pin):
    global new_can_data_bool, data, stored_prekick_can_data
    if not pin.value():
        CAN_LED.value(1)   # Rising edge: turn on LED
        if new_can_data_bool: # Only kick if armed
            data = stored_prekick_can_data
    else:
        CAN_LED.value(0)  # Falling edge: turn off LED

# Attach interrupt for both edges
BREAKBEAM.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=breakbeam_handler)

=======
SHELL_OFF = Pin(9, Pin.IN, pull=None) # disable digital Shell off pin to have no pull up or pull down
REED_PIN = ADC(Pin(28)) # channel 2


# initial states
>>>>>>> Stashed changes
prev_time_int = 0
prev_time_can = 0
prev_time_volt = 0
prev_time_reed = 0
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
check_5s_done = 0
charge_ok_sim = 0
safe_charge = 0
delay_time_us = 0
delay_time_us_temp = 0
kick_cooldown = 1
kick_delayed = 0
HV_voltage = 0
HV_Scaling = 0

offset = 1000_000
data_rec = 0

startup_chg_2sdelay = 0

#kickpulse = pulses.Pulses(None, KICK, 1_000_000)
pulses = pulses.Pulses(None, KICK, 1_000_000)

<<<<<<< Updated upstream
NOT_DISCHARGE.value(1)
while True:
    #Read Inputs:
    current_time = utime.ticks_ms()
    #current_ustime = utime.ticks_us()
=======
damping_on_cycle = 0
damping_off_cycle = 0
#####CAN_LED.on()

# Simulates the CAN data object
class FakeCANData:
    def __init__(self, can_id, data):
        self.can_id = can_id
        self.data = data

    def __getitem__(self, i):
        return self.data[i]

    def __len__(self):
        return len(self.data)

def idle():
    global chg_stop_mode_ctrl, prev_startup_idle_mode
    global idling, kicking, damping, charging
    global prev_mode, startup_chg, not_dischg
>>>>>>> Stashed changes
    
    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = DONE.value()  #DONE.value()#done_sim # 
    CHARGE.value(charge)
    #print(charge_disable)
    can_data = None
    
    while can.checkReceive():
        what, can_data = can.recv()
        if (hex(can_data.can_id) == "0x2aa"):
            # CAN_LED.value(1)
            # If the breakbeam is already broken, kick immediately
            if BREAKBEAM.value() == 0:
                data = can_data
            # Otherwise, queue up an autokick using interrupt
            else:
                stored_prekick_can_data = can_data
                new_can_data_bool = True
            can_rx_time = utime.ticks_ms()
            print("COMMAND RECEIVED")
    if can_rx_time is not None and (current_time - can_rx_time > AUTOKICK_EXPIRE_THRESH_MS):
        new_can_data_bool = False
                
    if (data_rec == 0 and kick_cooldown == 0):
        if (data == None) :
            data_rec = 0
            delay_time_us_temp = 0
        else:
            data_rec = 1
            
            pulse_width = int(data.data[1]) | int(data.data[0]) << 8
            # We processed this frame, so set new data flag to false
            new_can_data_bool = False
            delay_time_us_temp = pulse_width
            # CAN_LED.value(0)

            if (delay_time_us_temp > 5000) :
                delay_time_us_temp = 5000 # set a limit to the delay time
                print("Pulse duration too long, setting to 5000us")
            elif (delay_time_us_temp < 0):
                delay_time_us_temp = 0
            print("Kicking in 2 seconds, at ", delay_time_us_temp, "us. Stand back!")
    elif (data_rec == 1) :
        if (done_state == 0):
            prev_kick_time = current_time
            delay_time_us = delay_time_us_temp
            data_rec = 0
            data = None
            kick_delayed = 1
            
        else:
            delay_time_us = 0
            if (kick_delayed == 0) :
                print("DONE is HIGH (Charging), kicking delayed")
                kick_delayed = 1

    if(kick_cooldown == 0 and delay_time_us != 0):
        kick_cooldown = 1
        pattern=(8, delay_time_us, 8)
        start=0
        ar = array("L", pattern)
        # Kick here
        #
        CAN_LED.off()
        pulses.put_pulses(ar,start)
        prev_pulse_time = current_time # start pulse timer
        startup_chg_2sdelay = 1
        
        print("just kicked")
    elif (kick_cooldown == 1 and current_time - prev_pulse_time >= 100):
        kick_cooldown = 0
        delay_time_us = 0
        
<<<<<<< Updated upstream
    # do this only on startup
    if (startup == 0):
        done_sim = current_time
        prev_time_int = current_time
        prev_time_can = current_time
        prev_time_volt = current_time
        prev_time_HV = current_time
        prev_time_start_chg = current_time
        prev_time_wait_charge_vcc = current_time
        prev_sim_time = current_time
        prev_time_chg_wait = current_time
        prev_time_charge_disabled = current_time
        prev_time_countdown = current_time
        prev_sim_charge_time = current_time
        prev_pulse_time = current_time
        prev_kick_time = current_time
        startup_time = current_time
=======
        damp_period = int((1/damp_freq) *1000_000) # needs to be in microseconds
        damp_on_time = int(damp_period*damp_duty) # microseconds
        damp_off_time = damp_period - damp_on_time #microseconds
        print("Damp period", damp_period) # in seconds
        print("Damp on time", damp_on_time)
        print("Damp Frequency", damp_freq)
        prev_cycle_damp = utime.ticks_us()
        
        #if(damp_freq * damp_on_time > 20000):
        #    print("too big duty")
        #    damp_duty_perc_div100 = 10
        #    damp_duty = damp_duty_percent / 100.0
        
        if (damp_on_time > 5000):
            damp_on_time = 5000
    else:
        #started a damping cycle
        # definetely getting here. good to know
        # implement a delay here of duty off time.
        # Example I will try: 0x2AA, 2,     0xE8,          0x03,           10
        #                     ID     mode   low byte freq, high byte freq  duty / 100
        # keep damping until preset timetimeout or HV discharged
        if (utime.ticks_ms() - prev_time_damp < damp_timeout): # or HV_voltage > 10)
            # it is getting here.
            if (utime.ticks_us() - prev_time_damp_us > DAMP_INITIAL_PULSEWIDTH*15): # in us? # waiting for a second to do the damping
                # also getting here now that I changed the timer to us rather than *1000 in ms timer  
                
                if (utime.ticks_us() - prev_cycle_damp >= damp_period and damping_on_cycle == 0) :
                    damping_on_cycle = 1
                    damping_off_cycle = 0
                    # turn on kicker
                    #KICK.value(1)
                    
                    pattern=(8, damp_on_time, 8)
                    start=0
                    ar = array("L", pattern)
                    pulses.put_pulses(ar,start)
                    
                    ######CAN_LED.on()
                    prev_cycle_damp = utime.ticks_us()
                    #print("on")
                elif (utime.ticks_us() - prev_cycle_damp >= damp_off_time and damping_off_cycle == 0) :
                    damping_off_cycle = 1
                    damping_on_cycle = 0
                    # turn off kicker
                    #KICK.value(0)
                    ######CAN_LED.off()
                    #print("off")
        else :
            KICK.value(0)
            mode = 3 # KICK MODE TO TURN ON CHARGING. AKA BACK READY TO RECEIVE KICK
            ######CAN_LED.off()
            #print("whyyyy")
            # done damping, recharge HV for kick. Need to tell PI when it is charged using the done signal.
# 0x64 is 100 in hex
def chg():
    global chg_stop_mode_ctrl, charge_toggle_wait, startup_time, mode
    global prev_mode, startup_chg, not_dischg
    global idling, kicking, damping, charging
    
    idling = 0
    kicking = 0
    damping = 0
    
    if (charging == 0):
        if (prev_mode == MODE_IDLE or prev_mode == MODE_DAMP):
            startup_chg = 1
        prev_mode = mode
        print("CHARGING PREPARE MODE: HV STARTS CHARGING")
        charging = 1
        chg_stop_mode_ctrl = 0
        not_dischg = 1
        
    #if (utime.ticks_ms() - startup_time >= 4500 and utime.ticks_ms() - startup_time < 4750):
    #    mode = 0 # set to autokick mode after startup

# Interrupt handler
def breakbeam_handler(pin):
    global new_can_data_bool, data, stored_prekick_can_data
    if not pin.value():
        #CAN_LED.value(1)   # Rising edge: turn on LED
        if new_can_data_bool: # Only kick if armed
            data = stored_prekick_can_data
    else:
        pass
        #CAN_LED.value(0)  # Falling edge: turn off LED

# Attach interrupt for both edges
BREAKBEAM.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=breakbeam_handler)

#kickpulse = pulses.Pulses(None, KICK, 1_000_000)
pulses = pulses.Pulses(None, KICK, 1_000_000)

while True:
    # LITTLE ENDIAN. ~500 us ish = 0x01, 0x02. ~250 ish = 0x02, 0x01
    
    # FAKE CAN RECEIVE USING USB #######################3
    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline().strip()
        if not line:
            continue

        try:
            # Expected format: 0x2AA, 0x01, 0xE8, 0x03, 0x32
            parts = [int(p.strip(), 0) for p in line.split(',')]
            if len(parts) < 5:
                print("Invalid test input (need 5 bytes: id, mode, freq_l, freq_h, duty)")
                continue

            fake_can_data = FakeCANData(parts[0], parts[1:])

            if hex(fake_can_data.can_id) != "0x2aa":
                continue

            if len(fake_can_data) < 4:
                print("Invalid CAN message length.")
            else:
                mode = fake_can_data[0]
                pulse_freq = int.from_bytes(bytes(fake_can_data[1:3]), 'little')
                duty = fake_can_data[3]

                # Clamp values
                pulse_freq = max(1, min(pulse_freq, 50000))
                duty = max(1, min(duty, 99))

                if mode == MODE_AUTOKICK:
                    if BREAKBEAM.value() == 0:
                        data = pulse_freq
                    else:
                        stored_prekick_can_data = fake_can_data
                        new_can_data_bool = True
                    can_rx_time = utime.ticks_ms()
                    print("KICK COMMAND RECEIVED")
                print("COMMAND RECEIVED")

        except Exception as e:
            print("Error parsing input:", e)
    ################## FAKE CAN DATA STOP $$$$$$$$$$####################33
    
    '''
    ###############################################
    #simulation values (correct ones)
    # rising edge of charge pin
    if (charge == 1 and prev_charge == 0):
        done_sim = 1
        prev_sim_time = utime.ticks_ms()
        #print(utime.ticks_ms()/1000)
        #print("done signal set to 1")        
    #elif (charge == 1) :
    elif (utime.ticks_ms() - prev_sim_time >= 2000):
        done_sim = 0
        #print(utime.ticks_ms()/1000)
        #print("done signal set to 0")
        prev_sim_time = 0
        
    prev_charge = charge
    '''
    
    
    can_data = None
    
    '''
    while can.checkReceive():
        what, can_data = can.recv()

        if hex(can_data.can_id) != "0x2aa":
            if len(can_data) < 4:
                print("Invalid CAN message length.")
            else:
                mode = can_data[0]
                pulse_freq = int.from_bytes(can_data[1:3], 'little')
                duty = can_data[3]

                # Clamp unconditionally
                pulse_freq = max(1, min(pulse_freq, 50000))
                duty = max(1, min(duty, 99))

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
                print("COMMAND RECEIVED")
    '''


 
    # BYTE 0             = MODE
    #
    # BYTE 1-2 IN MODE 1 = PULSE WIDTH 
    # BYTE 1-2 IN MODE 2 = DAMP_FREQ
    # BYTE 3             = DUTY (In Percentage)

    # Mode Select (Idle, Kick, Damp)
    if mode == MODE_IDLE :
        # do idle. HV discharged, stops charging.
        idle()
    elif mode == MODE_AUTOKICK :
        # do kick mode. Ready to receive a kick command. High Voltage is Charged
        kick()
    elif mode == MODE_DAMP :
        # do damping mode. pulse the kicker to receive a pass and transfer energy. Happens only once and exits.
        # pulse_width or 
        damp(pulse_freq, duty, 2000)
    elif mode == MODE_CHARGE :
        # setup charge mode, which charges caps but does not ready for a kick. Useful if you want to turn off autokick or prep a damping cycle but dont want to kick yet
        chg()
    else :
        # unknown command
        print("unknown command")
        chg_stop_mode_ctrl = 1
    
    
    # DONE actually stays high until the end of a charge cycle is reached. so you cant do it the way i have my checks for startup.
    done_state = DONE.value() # done_sim
    CHARGE.value(charge)
    NOT_DISCHARGE.value(not_dischg)
    CAN_LED(SHELL_OFF.value())
    #print(charge_disable)
    
    # do this only on startup
    if (startup == 0):
        done_sim = 1
        prev_time_int = utime.ticks_ms()
        prev_time_can = utime.ticks_ms()
        prev_time_volt = utime.ticks_ms()
        prev_time_reed = utime.ticks_ms()
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
        prev_time_wait_chg = utime.ticks_ms()
>>>>>>> Stashed changes
        charge_ok = Voltages(charge_ok, startup) #
        pattern=(8, 8, 8)
        start=0
        ar = array("L", pattern)
        #kickpulse.put_pulses(ar, start)
        pulses.put_pulses(ar,start)
        #print(ledpulse.put_done)
        #print(kickpulse.put_done)
        ######CAN_LED.value(0)
        KICK.value(0)
        CHIP.value(0)
        #TESTPIN.value(1)
        
        startup_chg_2sdelay = 1
        startup = 1
        '''
        if (charge_ok == 1) :
            #safe_charge = 0
            #CAN_LED.value(0)
            #INT_LED.value(0)            
            #startup_cycle = 1
            
            #prev_pulse_time = current_ustime # start pulse timer
            charge = 0
            # set wait to toggle charge pin
            charge_toggle_wait = 0
            #print(current_time/1000, done_state)
            print("charge toggle on startup")
            prev_time_start_chg = current_time
            # finished startup routine, this will only happen again on reset or boot
            startup = 1
        else :
            startup_novcc = 1
        '''
    
    # start a new charge cycle if the battery is plugged in
    if (startup_chg_2sdelay == 1): 
        if (startup_vcc_wait == 0):
            if (charge_ok == 1):
                prev_time_wait_charge_vcc = current_time
                startup_vcc_wait = 1
                print("waiting to charge")
        else:
            if (current_time - prev_time_wait_charge_vcc >= 2000):  
                # wait for 2 seconds then start charge cycle
                charge = 0
                startup_cycle = 1
                # set wait to toggle charge pin
                charge_toggle_wait = 0
                #print(current_time/1000, done_state)
                print("charge toggle started on VBAT received after delay")
                #startup_cycle = 1
                prev_time_start_chg = current_time
                startup_vcc_wait = 0
                startup_chg_2sdelay = 0
            
    if (charge_ok == 0):
        startup_chg_2sdelay = 1
        startup = 1
        done_sim = 1
        #startup_cycle = 0
        
    # toggle LEDs at different intervals
    #CAN_LED.value(charge)
    #INT_LED.value(data_rec)
    
    #if (current_time - prev_time_can >= 50):
     #   print(startup_cycle)
     #   prev_time_can = current_time
    '''
    if (current_time - prev_time_can >= 200):
        if (delay_time_us == 0):
            #delay_time_us_temp = 2 # 1000 is 100us, 100 is 10us, very accurate, 10 is 1us, also pretty accurate
            delay_time_us = 210#int(delay_time_us_temp*20.0) -> 100us = 111us
            #50us = 62us
            #12us = 14us (very weird triangular signal tho)
            #print("delay set to 5000 us")
        else:
            delay_time_us = 0
            #print("delay reset")
        prev_time_can = current_time
    '''
    
        
    
    
    '''Note,

    . Any fault conditions such as thermal shutdown or undervoltage lockout will also turn on the NPN.
        for the DONE pin, which means it will be high if there is a problem
    '''
    if (charge_ok == 1 and charge_disable == 0):
        
        # charge_toggle_wait should be 0 if ready to start charging, and 1 if waiting after starting a charge cycle
        if (charge_toggle_wait == 0):
           # done will be 0 if either hasnt started charging, or has finished charging
            if (startup_cycle == 1):
                if (current_time - prev_time_start_chg >= 50 and current_time - prev_kick_time >= 50): 
                    charge = 1
                    startup_cycle = 0
                    charge_toggle_wait = 1
                    prev_time_chg_wait = current_time
                    #print(current_time/1000, done_state)
                    print("charge toggle complete on VCC input, waiting for 15s")
            elif (startup_chg_2sdelay == 0) :
                #print("waiting for 2 seconds to charge")
                if (done_state == 0):
                    # setup charge toggle wait time to set charge pin high
                    if (current_time - prev_time_start_chg >= 50 and current_time - prev_kick_time >= 50): 
                        charge = 1
                        charge_toggle_wait = 1
                        prev_time_chg_wait = current_time
                        #print(current_time/1000, done_state)
                        print("charge toggle complete on DONE input, waiting for 15s")
                else :
                    charge_disable = 1
                    prev_time_charge_disabled = current_time
                    if (safe_charge == 0):
                        #print("Safe Charge mode active, charged to 50V and stopped. Need to power cycle to restart safe charge")
                    #else :   
                        print("DONE is still high - Potential reasons: Undervoltage Lockout, Thermal Shutdown")
                        print("Retrying in 30 seconds")
            
        # wait for charge cycle to do the charge toggle every 15 seconds
        elif (charge_started == 1):
            if (safe_charge == 1):
                if (current_time - prev_time_chg_wait >= 236):#236 = 50V ish,  # 1108 = 160V ish (175 in sim)
                    charge_started = 0 # reset charge_started to zero for the next charge cycle
                    charge = 0
                    charge_toggle_wait = 0
                    check_5s_done = 0
                    charge_disable = 1
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
                    print("charge toggle (3s waited)")
                    # set charge pin
                # charge toggle wait is 1, charge_started should be 1 unless its an error    
                # check after 5 seconds if the done signal actually goes low after being high for charging
                elif (check_5s_done == 0 and current_time - prev_time_chg_wait >= 5000):
                    check_5s_done = 1
                    if(done_state == 0):
                        #good
                        charge_disable = 0
                        print("DONE is low after 2.5s, assuming normal operation")
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
        # check flyback again in 5 seconds by reasserting the charge_disable variable'
        if (safe_charge == 1):
            charge_disable = 1
        '''
        else :
            if (charge_disable == 1):
                if (current_time - prev_time_countdown >= 1000):
                    print("...",int((5000 - (current_time - prev_time_charge_disabled))/1000), "...")
                    prev_time_countdown = current_time
                if (current_time - prev_time_charge_disabled >= 5000):
                    charge_disable = 0
        '''   
        # maybe add checks on how to enable it after this point?? 
    # check voltages every 2 seconds
    if(current_time - prev_time_volt >= 2000):
        charge_ok = Voltages(charge, startup) #charge_ok_sim
<<<<<<< Updated upstream
        prev_time_volt = current_time
        # enable disable timer
        if (charge_ok == 0):
            print("Charging Disabled, Battery Unplugged")
        
        
        #else:
            #print("VCC OK")
        
        #print("CHARGE OK:", charge_ok)
        #print("Charge toggle wait", charge_toggle_wait)
=======
        prev_time_volt = utime.ticks_ms()
        #print("damp time", utime.ticks_us() - prev_cycle_damp)
        #check high voltage constantly to be able to adjust kick power.
        HV_voltage = SenseHV()
        print(reed_val)
        # enable disable timer
        if (charge_ok == 0):
            print("Charging Disabled, Battery Unplugged")
            
    # check voltages every 2 seconds
    if(utime.ticks_ms() - prev_time_reed >= 100):
        
        prev_time_reed = utime.ticks_ms()
        #check adc for reed every 10ms to avoid error in measurement (using a high impedance voltage divider).
        reed_val = REED_PIN.read_u16() # read value, 0-65535 across voltage range 0.0v - 3.3v
        reed_val_raw = reed_val * (3.3 / 65535.0) * (100+100)/100#((164.1 + 172.0)/164.1)
        reed_voltage = round(reed_val_raw,2)
        
>>>>>>> Stashed changes
