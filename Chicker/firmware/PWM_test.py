from machine import Pin, PWM, Timer

kicker_pin = Pin(15, Pin.OUT)
pwm = PWM(kicker_pin)
pwm.freq(1000000)  # 1 MHz PWM frequency (1 µs period)
timer = Timer()

def stop_pulse(t):
    pwm.duty_u16(0)  # Turn PWM off (0% duty)

def kick(pulse_us):
    pwm.duty_u16(65535)  # 100% duty cycle
    # Schedule timer to stop pulse after pulse_us microseconds
    timer.init(mode=Timer.ONE_SHOT, period=pulse_us, callback=stop_pulse)

# Example: kick with 500us pulse
kick(500)



# for pWM 
pwm = PWM(KICK) # KICK

# Set the frequency (e.g., 1000 Hz)
pwm.freq(damp_freq)

# Set the duty cycle (0-65535, where 65535 is 100%)
pwm.duty_ns(1000*damp_duty_percent) #275_000)
