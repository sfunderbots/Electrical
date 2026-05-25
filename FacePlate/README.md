# Faceplate Board V2 Documentation

## Overview

The Faceplate[^1], one of five of our robot's on board custom PCBs (not including the Raspberry Pi), is a connector and peripheral board between the Raspberry Pi and the rest of the robot system. As this documentation pertains to the Robocup Small Size League Autonomous Soccer, this documentation will be very specific in its use case. All five boards contained on our robot (six including the Raspberry Pi), for context, are listed as follows:
1. Raspberry Pi 4 - Main Robot Computer and link between the active running computer running our team's software
2. Faceplate[^1] - Connector between Raspberry Pi and Midplate for CANBUS communication, peripherals, and LCD screen eyes
3. Midplate - Battery input and power distribution (doubles as a mechanical chassis)
4. Chicker[^2] - High Voltage Flyback Capacitor Charger and Solenoid Driver
5. Simovr[^3] - A BLDC Motor Driver designed based on the Tinymovr platform in a smaller footprint
6. Baseplate - Power distribution and CANBUS distribution for the Simovr's, and breakbeam circuits to detect the ball

[^1]: Aptly named due to our robot's circular LCD displays
[^2]: Named after a combination of the flexibility of both kicking or chipping circuits
[^3]: Aptly named after its creator, Simon, and its reference design, the Tinymovr

<p align="center">
  <img width="461" height="502" alt="robot2026" src="https://github.com/user-attachments/assets/ba053e41-d132-4c2a-b95e-d5ae77be0df0" />
</p>

## Schematics
### Power
From the midplate, the Faceplate receives a regulated 5V supply for the Raspberry Pi at 3A, including the CANBUS which is wired all throughout our robot for between-board communication. Specifically, this uses a JST XH connector, which is easily sourced among hobbyists. Then, the board also steps this down to 3.3V for the onboard logic circuits needed such as peripherals and LCD screens. Decoupling capacitors are needed on the Low Voltage Regulator (LDO) to help maintain power stability, and is chosen over a DC/DC converter due to low current requirements. The part chosen as shown below is a SOT-223 package that can handle up to 1A of current (this is significantly over specification).

<div style="display: flex; gap: 10px;">
  <img src="https://github.com/user-attachments/assets/c87480ca-adc4-4e60-b1d5-105cca5da72c" alt="5V_CAN" style="flex: 1; width: 49%;" />
  <img src="https://github.com/user-attachments/assets/3afe2a88-e878-4c64-be91-d24933789819" alt="ldo" style="flex: 1; width: 49%;" />
</div>

### Connection to Raspberry Pi
Since the Raspberry Pi 4 has a 40x2 standard dupont style 2.54mm pitch connector, it is easiest to attach a board to board connector of the same type for this PCB. This is easily accomplished by measuring the placement of the 40 pin header in a CAD program (in our case, Onshape) compared to the PCB, with special consideration for ensuring no 3D conflicts between these two boards.

<p align="center">
<img width="1027" height="1080" alt="40pin" src="https://github.com/user-attachments/assets/6dfb532c-1686-45f4-a116-06a886afbfaf" style="flex: 1; width: 49%;"/>
</p>
## smaller
### evens maller
