# Faceplate Board V2 Documentation

## Overview
---

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

The Faceplate can be seen on the front of our robot, with the eyes visible and the board covered by a 3D printed piece below:
<p align="center">
  <img width="1575" height="789" alt="image" src="https://github.com/user-attachments/assets/d8db98a9-bbcf-47a2-81a0-988f7e6c8df0" style="flex: 1; width: 49%;"/>
</p>

## Schematics
---


  
For any of the following schematics, it is useful to group components into a schematic library file inside the EDA tool you are using - in this case, KiCad. Saving custom schematics ensures that when you update a board design you carry over any modifications like manufacturer part numbers, JLC part numbers (LCSC), price, etc. The following serves as an example for what information is useful to keep in the schematic part.

<p align="center">
<img width="906" height="922" alt="image" src="https://github.com/user-attachments/assets/e92225a0-547a-401e-ab69-4eb361320a10"  style="flex: 1; width: 49%;"/>
</p>

> [!TIP]
> If you select "edit symbol library", you will see that it opens up the library tab in KiCad, which has all of our parts listed. The same information for the LDO as above is here as well.
> <p align="center">
> <img width="1918" height="1115" alt="image" src="https://github.com/user-attachments/assets/99134821-deaf-4334-96a6-c61dcacc3b2f" style="flex: 1; width: 70%;" />
> </p>

> [!IMPORTANT]
> You will need to add your library path to the .KICAD_SYM schematic library file, which stores all of your components. While separating the components into groups hasnt been done yet on our end due to time constraints, we recommend grouping these components by type in separate .KICAD_SYM files to increase productivity.
> <p align="center">
> <img width="1256" height="831" alt="image" src="https://github.com/user-attachments/assets/c45acfb7-16f0-4e79-8f1a-0f4201c3d013" style="flex: 1; width: 70%;" />
> </p>

Additionally, for each symbol in your library, an associated footprint needs to be added to each symbol used in the schematics, which will be covered in the layout section.

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
2. Board Setup (Layers and Shape)
3. Part Placment
4. DRC Rules (including differential pairs)
5. Power routing (including zones / polygons)
6. Signal routing (best practices)
7. Custom Rule Areas (e.g. clearance / keepouts) 

The following image shows the overall layout of the board, where red refers to the top layer, blue refers to the bottom layer, green refers to the inner 1 / power layer, and orange refers to the inner 2 / ground layer. These planes on the board make up the 4 layers for the most common PCB to be fabricated, with power layers included for better power distribution, and signals routed generally on the top and bottom layers, if possible.

<p align="center">
<img width="1262" height="467" alt="image" src="https://github.com/user-attachments/assets/c34c5978-9bda-4755-929a-95637f420a8d" style="flex: 1; width: 80%;"/>
</p>

Below reference most of the important shortcuts on the top bar used in KiCad: On the far left shows the board setup, then on the right past the lock and unlock icons are the footprint library editor, footprint library browser, 3D viewer, import changes from schematic, and DRC rules. 

<p align="center">
  <img width="1437" height="48" alt="image" src="https://github.com/user-attachments/assets/35a69b79-4f0d-41d1-bc0d-7787352f569e" />
</p>

### Footprint Libraries
Setting up the footprints for each component can be accomplished manually or by downloading a footprint from an EDA library online. One should always consult the datasheet to confirm the validity of the suggested layout. As an example, the 3.3V LDO mentioned in the symbol library section is shown below. One should always verify the validity of a footprint by downloading the 3D model of the part so that the pins will have sufficient solder area and are easy enough to rework with a soldering iron. 

<p align="center">
  <img width="2560" height="1362" alt="image" src="https://github.com/user-attachments/assets/7ea4a366-30a1-4323-8226-1f12ca5f67f9" style="flex: 1; width: 80%;"/>
</p>

> [!NOTE]
> One can access pre-built footprint files for your EDA tool of choice from libraries such as [snapeda.com](https://www.snapeda.com/home/) or [ultralibrarian.com](https://www.ultralibrarian.com/) for example. Many more exist, but these are the most frequently used. Always remember to verify these footprints as they may not match the datasheet.

> [!IMPORTANT]
> It is especially important to pay attention to drill sizes on through hole parts, as even half a millimeter of difference from the part size may be enough to cause interference during fabrication. For this reason we always recommend to check the "confirm production files" and "confirm part placement", so that the JLCPCB engineers can review your board and catch any issues before they are manufactured.

Lastly, with the same process as the symbol libraries, you need to ensure the path to the footprint library exists in the global or project path directories, so that KiCad knows where to look for this board when you place parts.

### Board Setup
Clicking the gear icon in the top left of the screen on the PCB editor in KiCad, you can access the Board Setup menu. In this menu you can select the number of layers on your board you intend to use, using the "Physical Stackup" submenu, and can define the type of layer used to help KiCad understand how you intend to use this board by using the "Board Editor Layers" submenu. 

> [!NOTE]
> This menu can be confusing, but most of this information can be ignored as when you upload your design to JLCPCB the selection menu for PCB type and layer stackup types is all done on their website. Here, the most important thing to remember is simply to select the number of layers used - usually 2, 4 or 6 depending on complexity of the signal routing.
> <p align="center">
> <img width="1245" height="713" alt="image" src="https://github.com/user-attachments/assets/25e25eae-2fd0-44f3-91b8-f4774e328bdc" style="flex: 1; width: 70%;"/>
></p>

The shape of the PCB can be defined by creating a bounding box (or whatever closed shape you prefer) with the Edge Cuts Layer. First clicking on the edge cuts layer on the side bar shown below you can define a PCB shape using lines or arcs to create the shape of the board. As long as it is a closed shape, this will work and you can check the 3D view to verify it cut properly. The following shows an example when your board shape is malformed versus closed. When the board shape is closed, the shape will be filled in with a subtle grey tone, and the 3D model will be formed.

<div style="display: flex; gap: 10px;">
  <img width="1443" height="1120" alt="image" src="https://github.com/user-attachments/assets/8ab1a8f8-dfa4-4316-b0b9-7fdedbb8c0e3" style="flex: 1; width: 49%;"/>
  <img width="1438" height="1146" alt="image" src="https://github.com/user-attachments/assets/438890df-3d00-4606-81d0-9c749a695be1" style="flex: 1; width: 49%;"/>
</div>

> [!TIP]
> As one can see, it is not always obvious when a board shape is malformed as seen by the above example, where two lines were clearly removed but the overall shape looks the same. As a general rule, the board shape will always fit to the largest area KiCad sees as possible on the Edge Cuts layer, hence how below the shape does not follow any of the drawn lines below.
> <img width="1992" height="712" alt="image" src="https://github.com/user-attachments/assets/dce4e3fd-fd2b-4687-8935-91476e46a78f" />

### Part Placement
When placing parts on the PCB one should think ahead where the power routing will go. In this case, the I would anticipate that the 5V net for the Raspberry Pi would go near the edge of the board and wrap around to the rightmost pins on the 40 pin header, with the 3.3V being the main power plane on the board. Secondly, one should leave enough space around parts to make it easier to solder if rework must be completed, or if you are soldering the boards yourselves. For the faceplate below, you can see that it was ideal to put our power and CAN connector (JST XH 4 pin) on the same side as our power switch and power connector on the midplate. The rest of the components will be arranged in similar fashion leaving enough space for connecting vias for power connection.

<p align="center">
<img width="2560" height="1362" alt="image" src="https://github.com/user-attachments/assets/51da4a68-74ab-4dae-ab09-b8cd4301047d" />
</p>

To help your process in laying out the PCB parts, you can see the "ratnest" of white lines across the board, which show you where the pads will be connected according to the schematic. 

> [!IMPORTANT]
> It is also super important at this time to pay attention to any missing ratnest wires or pads that do not have any connection, which may be a mistake on the schematic. For this reason I always label my net names on the schematic so that when laying out the PCB it will be obvious if something is missing.

> [!TIP]
> It is always useful to include mounting holes on the PCB so that you can attach the board to an enclosure, which may not be necessary for prototyping but it will be necessary in the end - therefore getting it out of the way early will avoid headaches in the future!
>
> Lastly, one should avoid placing components too close to the edge of the board, especially if the board is panelized, as any stresses to the PCB may cause early failure in certain components, such as capacitors. A guide about part placement for proper power and signal delivery can be seen on the JLCPCB website here: [JLCPCB design guidelines for placement and routing](https://jlcpcb.com/blog/pcb-design-guidelines-placement-and-routing).

### DRC Rules

On any board project, DRC rules should be set up so that you are generally following manufacturer specifications. You can view the capabilities of JLCPCB on their website [here](https://jlcpcb.com/capabilities/pcb-capabilities), and if I can find it there should be a downloadable file for DRC rules. In the case that this doesn't exist any more, these settings can be set up as follows:

The default constraints should be filled out first, which shows important rules which will dictate how the PCB is fabricated later. Important values to consider here are the minimum widths for trace widths and clearance/spacing (for which I tend to at least double the minimum specified by JLCPCB, as this only specifies an absolute minimum, but not a practical number to avoid noise and EMI), copper to hole, and copper to edge clearances.

<p align="center">
  <img width="1242" height="771" alt="image" src="https://github.com/user-attachments/assets/3a5842b7-2010-4c59-a28b-589f2a777f9a" style="flex: 1; width: 49%;"/>
</p>

Additionally, you can include DRC rules by setting up Net Classes in the schematic with colors, and in the PCB you can select these net classes to choose specific clearance and widths based on the expected current on those traces. The following shows the example of the "Power" Net Class which I have colored orange for example.

<p align="center">
<img width="608" height="341" alt="image" src="https://github.com/user-attachments/assets/46ad4845-ede2-413d-99ac-c60b696a1c5e" style="flex: 1; width: 30%;"/>
<img width="1235" height="373" alt="image" src="https://github.com/user-attachments/assets/eb28bcfb-426c-48cf-a639-a56d932a4290" style="flex: 1; width: 80%;"/>
</p>

For these specific nets, I have selected a slightly larger default via size (0.7/0.35mm) with larger overall trace width minimum. Generally speaking with larger trace widths the larger the clearance should be as well.

> [!TIP]
> Always check your DRC rules BEFORE you start routing component tracks. This way, you will ensure there are no part placement issues you will have to solve later.

> [!WARNING]
> It is important not to look over any DRC warnings and especially errors, I would recommend only setting a DRC rule to "ignore" if it is absolutely necessary (set in "Violation Severity" under board setup design rules), as this can cause issues to be forgotten about especially when a lot of errors that seem meaningless show up can mask a significant board error if those are ignored.
 
### Power Routing

### Signal Routing

### Custom Rule Areas

## Implementation
---

To fabricate the boards, we used JLCPCB, which has been extremely reliable for countless hobby projects over the last five years. Here’s JLC's new user [6-layer PCB coupon](https://jlcpcb.com/6-layer-pcb?from=social) which saves you 30$ on your project so you can start a project with relatively low cost. Apart from being a reliable PCB manufacturer, JLCPCB has also been very cost effective. Alternate components on their partner LCSC make board projects feasible at lower cost with similar or the same specifications. 

Clicking the 3D model view in KiCad (the icon that looks like a capacitor) you can view the 3D model of the board - the Faceplate looks as follows:

<p align="center">
  <img width="1293" height="507" alt="image" src="https://github.com/user-attachments/assets/6715656e-b172-49ec-b217-de1b871c9253" style="flex: 1; width: 80%;"/>
  <img width="1325" height="507" alt="image" src="https://github.com/user-attachments/assets/fc6b64f9-f950-49e7-b4a1-05ee0faa1b3d" style="flex: 1; width: 80%;" />
</p>

