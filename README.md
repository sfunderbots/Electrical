# Electrical

This is a repository containing all of the electrical board designs of The Bots, including firmware for the chicker board, which is contained in that sub-folder. 

The robot contains five custom PCBs (not including the Raspberry Pi).

All five boards contained on our robot (six including the Raspberry Pi), for context, are listed as follows:
1. Raspberry Pi 4 - Main Robot Computer and link between the active running computer running our team's software
2. [Faceplate](https://github.com/sfunderbots/Electrical/tree/main/FacePlate) - Connector between Raspberry Pi and Midplate for CANBUS communication, peripherals, and LCD screen eyes
3. [Midplate](https://github.com/sfunderbots/Electrical/tree/main/MidPlate) - Battery input and power distribution (doubles as a mechanical chassis)
4. [Chicker](https://github.com/sfunderbots/Electrical/tree/main/Chicker) - High Voltage Flyback Capacitor Charger and Solenoid Driver
5. [Simovr](https://github.com/sfunderbots/Electrical/tree/main/PAC5527Driver) - A BLDC Motor Driver designed based on the Tinymovr platform in a smaller footprint
6. [Baseplate](https://github.com/sfunderbots/Electrical/tree/main/BasePlate) - Power distribution and CANBUS distribution for the Simovr's, and breakbeam circuits to detect the ball

<p align="center">
  <img width="461" height="502" alt="robot2026" src="https://github.com/user-attachments/assets/ba053e41-d132-4c2a-b95e-d5ae77be0df0" />
</p>
