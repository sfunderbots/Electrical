# Board Description

This is a high voltage board that uses a LT3750 Flyback Charge controller to charge two 1000micro farad capacitors to 210V (these are 250V rated)

The board was designed in KiCad and the Files are available in this Github. It uses a novel power pillar design to transmit power and signals over Brass standoffs (including HV) which go to the midplate (power distribution board and chassis). 

When the robot kicks, the chicker sends a pulse from 500 to 5000us to the IGBT gate driver, turning the solenoid on for a specified time generating more force with more applied current.

![Chicker V1.1 Front View](Images/ChickerV1.1_front.jpeg)
![Chicker V1.1 Back View](Images/ChickerV1.1_back.jpeg)

## Firmware Flowcharts
The detailed workflow for the board operation is described here, which allows the LT3750 charge controller to operate safely with proper state machine logic, and the mode control firmware to control the active mode of the chicker board (kick, damp, charge, and idle).
![Charge State Machine Flowchart](Images/chicker-charge-state-machine.png)
![Mode Control Flowchart](Images/chicker-mode-control-state-machine.png)

## License
This project is licensed under the open-sourced MIT License - see the [LICENSE](LICENSE) file for details.

