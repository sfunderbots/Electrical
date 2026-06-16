# Solenoid Active Damping
> What is it: An Electromechanical ball damping method for SSL robots that uses existing kicker hardware, without any mechanical mechanisms required.

---
## Motivation

The solenoid active damping theory and subsequent implementation was developed by The Bots as a tool to improve pass receiving performance of the SSL league. This was first tested after RoboCup 2025, with updated code for The Bots' Chicker board which split the CAN messages into operating modes - a key change to incoroprate damping was first to be able to test it seamlessly without changing the kicking functions of the robot. A more detailed explanation of the changes to the firmware and the 'modes' can be seen on our 2026 TDP. 

To achieve a pass, most teams build some complex damping mechanism involving the rotating dribbler and compliant mechanisms, which was explained on our 2026 TDP. However, as this adds cost and complexity for new teams and becomes a significant hurdle to overcome, we decided to try a theory that would use the existing electromechanical energy on the robot to achieve the same goal (and potentially better, pending full testing).

If one is to calculate the energy on SSL robots, one can notice there is a tremendous amount of energy that is unspent during the receiving phase. For our robots specifically, there is 1/2 * 2000uF * 210V^2 = 44.1J of energy on the robot at a full charge of the capacitors. One important question we wanted to ask ourselves is: why is that same energy used for kicking, not used for the opposite, in ball receiving. When the robot is receiving a pass, it is subsequently not kicking at that moment - and secondly, the time that would need to be used for repositioning once a pass is received is sufficiently long enough for the capacitors to charge back up again for a 9A charging circuit. 

Thus, the reasoning to develop an electromechanical damping framework was formed. 

---
## How does it work?

Before the ball is received, the robot extends the kicker head with a low power kick, and then is held at the minimum required force to remain extended. The holding force is accomplished by pulsing the IGBT with a PWM signal with a duty cycle equal to just above the threshold where the kicker head would otherwise collapse back into the robot. 

Once the ball (weighing 45 grams) contacts the plunger (weighing 60 grams), the inertia from the ball transfers to the plunger, making it retract fully into the robot without enough energy for re-extension. The ball then rolls towards the dribbler with any remaining energy left, allowing for operation even without a dribbler or even better control with a simple dribbler which are both very acheiveable for new teams.

---
## Firmware Elaboration

As mentioned in our TDP, one needs to apply a short pulse which is equivalent to a very soft kick (just enough to get the kicker head out) to first extend the plunger. 

> [!IMPORTANT]
> On our chicker boards, this is about 500 micro-seconds or less. All that is required is that the kicker head extends fully but with almost no power. 

Then, some nonzero waiting time should be applied to allow the mechanical system to finish actuating from the electrical force generated. The electrical force and mechanical force on the plunger can be modelled, but this requires a complex simulation involving the change in inductance over position of the solenoid - this is not important for one to get started, but may help one understand the exact time delay needed. 

> [!NOTE]
> The electrical system has a time scale of microseconds to milliseconds, however the mechanical system has a timescale from several milliseconds to seconds, which is why this time delay is needed. 

Once the mechanical system has actuated the plunger, the PWM will be applied to the IGBT. Choosing a frequency for the PWM signal is somewhat important, as it will slightly change the audible sound from the robot, but will only matter depending on the accuracy of your microcontroller timer and any minimum limitations for pulse width. Due to the limitations on the RP2040 at the time, a higher frequency was not achievable. 

> [!TIP]
> Higher frequencies (20kHz +) are recommended if you are able to achieve very accurate pulse widths for each switching cycle, with our initial goal to raise the frequency outside the audible range of the human ear. 

Code snippets for the firmware are available in this folder with relevant pwm code for the RP2040 in micropython with the rest being abstracted away. The full code can be viewed under [Chicker/firmware/main.py](https://github.com/sfunderbots/Electrical/edit/main/Chicker/firmware/main.py)

---
## Rough Dynamic Model

To understand why the system works as it does, it is important to understand collision physics. In an elastic collision, the ball is repelled fully from the robot, such as a stationary object or a robot that is unable to receive the ball. Consequently, in an inelastic collision, the ball would be dragged backwards by the kicker head in an illegal move by SSL rules (which would only be possible if it was sticky). 

What we are achieving here is somewhere in the middle, a partially inelastic collision - because the kicker head is extended at minimum possible force to remain stationary, when the ball contacts the kicker head, it subsequently collapses with not enough sufficient force to maintain the hold.

From our inconclusive tests 1 and 3 shown on our 2026 TDP where the ball bounced off the robot, it can be observed that the plunger re-extends (due to microcontroller inaccuracy), causing a significantly more elastic collision than expected. This can be verified using momentum math, expecting a perfect damping to produce a coefficient of restitution (e, a measure of inelastic collision) proportional to the masses of the ball to the plunger. 

---
### Momentum Transfer Equations

Looking at the velocities of the ball on impact shown on the TDP, we can calculate this coefficient based on the momentum transfer equation, where the coefficient of restitution (e) is defined by the difference in separation velocity (post collision) divided by the difference in incoming velocity (only the ball is moving pre-collision):

$$e = \frac{v'_{plunger} - v'_{ball}}{v_{ball}}$$

Next, the unknown plunger velocity after the collision can be written in terms of e:

$$v'_{plunger} = e \cdot v_{ball} + v'_{ball}$$

Using the momentum transfer equation below, we can substitute in the equation for $$v'_{plunger}$$ in terms of e into the equation below:

$$m_{ball} \cdot v_{ball} = m_{ball} \cdot v'_{ball} + m_{plunger} \cdot v'_{plunger}$$

$$m_{ball} \cdot v_{ball} = m_{ball} \cdot v'_{ball} + m_{plunger}(e \cdot v_{ball} + v'_{ball})$$

In the case of a perfectly damped test, the ball velocity after collision is zero or slightly negative (implying it rolls with topspin), therefore we know that e must be above the following ratio of ball to plunger weight:

$$ e = \frac{m_{ball}}{m_{plunger}} = \frac{0.045kg}{0.060kg} = 0.75 $$

### Test Summary

Combining these equations, we can calculate the effective coefficient of restitution (e) for the inconclusive tests shown on our TDP, which prove that the kicker head re-extended increasing the force on the ball:

$$e = \frac{\frac{m_{ball}}{m_{plunger}} ( v_{ball} -  v'_{ball}) - v'_{ball}}{v_{ball}}$$

<div align="center">

| Test No. | Input Velocity ($v_{ball}$) | Output Velocity ($v'_{ball}$) | Calculated Effective e |
| :---: | :---: | :---: | :---: |
| **1** | 3.0 m/s | 0.26 m/s | 0.598 |
| **2** | 2.7 m/s | 0 m/s | 0.75 |
| **3** | 5.3 m/s | 0.40 m/s | 0.617 |

</div>

---
## Future Work and Recommendations

As this math only pertains to a perfect collision with minimal friction, it would be important to model and understand the static friction needed to overcome the holding force of the solenoid. This will vary based on the plunger weight, as the heavier the plunger, the more force to required overcome and collapse the plunger. This in turn will decrease the coefficient of restitution as there is more contact time with the plunger, where the ball will start to bounce off of the robot.

> [!WARNING]
> Depending on your team's plunger weight, you will have to tune the duty cycle of the PWM to hold the plunger out, as described by the Firmware section, which may also affect the quality of the damping performance.

For reference to the PWM code written in micropython, you can refer to this snippet included in the folder, or the main firmware included in the chicker firmware folder.

---
## Open Source License

This work is open-sourced with the MIT license, please refer to the license file for more details.








