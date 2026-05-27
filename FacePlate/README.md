# Faceplate Board V2 Documentation

> ## Overview

The Faceplate[^1], one of five of our robot's on board custom PCBs (not including the Raspberry Pi), is a connector and peripheral board between the Raspberry Pi and the rest of the robot system. As this documentation pertains to the Robocup Small Size League Autonomous Soccer, this documentation will be very specific in its use case. The new version of the faceplate incorporates connectors for mouse sensors (localization improvement) and an offboard IMU (placed closer to the center of the robot with less vibration). All five boards contained on our robot (six including the Raspberry Pi), for context, are listed as follows:
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

> ## Schematics

> [!TIP]
> For any of the following schematics, it is useful to group components into a schematic library file inside the EDA tool you are using - in this case, KiCad. Saving custom schematics ensures that when you update a board design you carry over any modifications like manufacturer part numbers, JLC part numbers (LCSC), price, etc. The following serves as an example for what information is useful to keep in the schematic part.

<p align="center">
<img width="906" height="922" alt="image" src="https://github.com/user-attachments/assets/e92225a0-547a-401e-ab69-4eb361320a10"  style="flex: 1; width: 49%;"/>
</p>

> [!TIP]
> If you select "edit symbol library", you will see that it opens up the library tab in KiCad, which has all of our parts listed. The same information for the LDO as above is here as well.

<p align="center">
<img width="1918" height="1115" alt="image" src="https://github.com/user-attachments/assets/99134821-deaf-4334-96a6-c61dcacc3b2f" style="flex: 1; width: 70%;" />
</p>

> [!IMPORTANT]
> You will need to add your library path to the .KICAD_SYM schematic library file, which stores all of your components. While separating the components into groups hasnt been done yet on our end due to time constraints, we recommend grouping these components by type in separate .KICAD_SYM files to increase productivity.

<p align="center">
<img width="1256" height="831" alt="image" src="https://github.com/user-attachments/assets/c45acfb7-16f0-4e79-8f1a-0f4201c3d013" style="flex: 1; width: 70%;" />
</p>

> [!NOTE]
> Additionally, for each symbol in your library, an associated footprint needs to be added to each symbol used in the schematics, which will be covered in the layout section.

### Power
From the midplate, the Faceplate receives a regulated 5V supply for the Raspberry Pi at 3A, including the CANBUS which is wired all throughout our robot for between-board communication. Specifically, this uses a JST XH connector, which is easily sourced among hobbyists. Then, the board also steps this down to 3.3V for the onboard logic circuits needed such as peripherals and LCD screens. Bypass and decoupling capacitors are needed on the Low Voltage Regulator (LDO) to help maintain power stability, and is chosen over a DC/DC converter due to low current requirements.

<div style="display: flex; gap: 10px;">
  <img src="https://github.com/user-attachments/assets/c87480ca-adc4-4e60-b1d5-105cca5da72c" alt="5V_CAN" style="flex: 1; width: 49%;" />
  <img src="https://github.com/user-attachments/assets/3afe2a88-e878-4c64-be91-d24933789819" alt="ldo" style="flex: 1; width: 49%;" />
</div>


> [!NOTE]
> The part chosen as shown (SOT-223 package) can handle up to 1A of current, which was significantly over specification for our needs.

> [!TIP]
> Always remember to double check the current requirements and heat generation of your system, especially for power conversion!

### Connection to Raspberry Pi
Since the Raspberry Pi 4 has a 40x2 standard dupont style 2.54mm pitch connector, it is easiest to attach a board to board connector of the same type for this PCB. To line it up with the Raspberry Pi, it was easily accomplished by measuring the placement of the 40 pin header in a CAD program (in our case, Onshape) and comparing it to the PCB.

<p align="center">
<img width="1027" height="1080" alt="40pin" src="https://github.com/user-attachments/assets/6dfb532c-1686-45f4-a116-06a886afbfaf" style="flex: 1; width: 49%;"/>
</p>

> [!IMPORTANT]
> Special consideration must be taken for ensuring no physical interferences between parts on the bottom layer and the Raspberry Pi, and the top layer to the LCD screens.

### LCD Displays (eyes)
Our LCD displays are a circular GC9A LCD display for which the board is then covered by custom-made 3D printed material for protection surrounding the eyes. Each display (and corresponding breakout board) attaches to a standard 7x1 Dupont style 2.54mm pitch connector, and includes decoupling power capacitors for the 3.3V logic rail to maintain chip operation during normal operating transients. The parts are organized into hierarchical sheets for better organization and viewing of schematics.

<p align="center">
<img width="1295" height="522" alt="image" src="https://github.com/user-attachments/assets/f5988cd9-373f-4fc7-a52b-0c29e8d25a3f" style="flex: 1; width: 50%;"/>

<img width="1605" height="812" alt="displays" src="https://github.com/user-attachments/assets/6bb74e0a-a286-4728-9c26-5810eb765e33" style="flex: 1; width: 70%;"/>
</p>

### CANBUS
In order to communicate on the CANBUS, one needs to convert the standard SPI interface on the Raspberry Pi to a CAN interface, using a CAN controller (MCP2515T), and a CAN transceiver (SN65HVD233). The CAN controller operates on an 8MHz clock and communicates with a CAN transceiver to send and receive commands over the CANBUS interface. As with the displays, the CAN interface is grouped into a hierarchical sheet to simplify organization.

<p align="center">
<img width="1113" height="508" alt="can-top" src="https://github.com/user-attachments/assets/df21f037-adb1-4dfb-80ee-1a108f11854f" style="flex: 1; width: 40%;"/>
  
<img width="1816" height="610" alt="can" src="https://github.com/user-attachments/assets/a01f5431-a823-4e52-9366-4c31954d321d" style="flex: 1; width: 100%;"/>
</p>

### Mouse Sensors
Two 8-pin JST GH series connectors link the Raspberry Pi with two identical custom PCBs using a Pixart Imaging PMW3360DM-T2QU navigation sensor, which will be placed at the base of the robot on either side. This will provide significant improvement to our localization and control of the robot, incorporating precice movement of sensors near the surface of the playing field. 
<p align="center">
  <img width="1940" height="776" alt="image" src="https://github.com/user-attachments/assets/f85c225d-284f-4928-872c-f2d36272836f" style="flex: 1; width: 80%;"/>
</p>


The board files of the mouse sensor can be found on our github page under [PWM3360_PCB_JST](https://github.com/sfunderbots/Electrical/tree/main/pmw3360-pcb-main/pmw3360_pcb_jst).

### IMU
For this revision of the faceplate, we have removed the direct connection of the IMU on the faceplate in place of an external connector to be wired to an IMU, in our case chosen as a breakout board from Adafruit: [4438](https://www.adafruit.com/product/4438). This will provide accurate 6-DoF for the robot to improve robot control speed. This is connected using a 4 pin JST PH connector (Adafruit calls it the STEMMA QT QWIIC) which provides both power and data lines.
<p align="center">
  <img width="1137" height="575" alt="imu" src="https://github.com/user-attachments/assets/0043f4e1-8bf4-47eb-837b-108d4a3a492e" style="flex: 1; width: 50%;"/>
</p>

> ## Layout

The layout of this board and boards in general can be described by the following seven steps:
1. Footprint Libraries (for schematic)
2. Part Placment
3. Board Setup (Layers and Shape)
4. Board DRC Rules (including differential pairs)
5. Power routing (including zones / polygons)
6. Signal routing (best practices)
7. Custom Clearance Rule Areas or keepouts

The following image shows the layout of the board, where red refers to the top layer, blue refers to the bottom layer, green refers to the inner 1 / power layer, and orange refers to the inner 2 / ground layer. These planes on the board make up the 4 layers for the most common PCB to be fabricated, with power layers included for better power distribution, and signals routed generally on the top and bottom layers, if possible.

<p align="center">
<img width="1262" height="467" alt="image" src="https://github.com/user-attachments/assets/c34c5978-9bda-4755-929a-95637f420a8d" style="flex: 1; width: 80%;"/>
</p>

> ## Implementation

To fabricate the boards, we used JLCPCB, which has been extremely reliable for countless hobby projects over the last five years. Here’s JLC's new user [6-layer PCB coupon](https://jlcpcb.com/6-layer-pcb?from=social) which saves you 30$ on your project so you can start a project with relatively low cost. Apart from being a reliable PCB manufacturer, JLCPCB has also been very cost effective. Alternate components on their partner LCSC make board projects feasible at lower cost with similar or the same specifications. 

