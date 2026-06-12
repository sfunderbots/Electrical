# Solenoid Active Damping

## Motivation
The solenoid active damping theory and subsequent implementation was developed by The Bots as a tool to improve pass receiving performance of the SSL league. This was first tested after RoboCup 2025, with updated code for The Bots' Chicker board which split the CAN messages into operating modes - a key change to incoroprate damping was first to be able to test it seamlessly without changing the kicking functions of the robot. A more detailed explanation of the changes to the firmware and the 'modes' can be seen on our 2026 TDP. 

To achieve a pass, most teams build some complex damping mechanism involving the rotating dribbler and compliant mechanisms, which was explained on our 2026 TDP. However, as this adds cost and complexity for new teams and becomes a significant hurdle to overcome, we decided to try a theory that would use the existing electromechanical energy on the robot to achieve the same goal (and potentially better, pending full testing).

If one is to calculate the energy on SSL robots, one can notice there is a tremendous amount of energy that is unspent during the receiving phase. For our robots specifically, there is 1/2 * 2000uF * 210V^2 = 44.1J of energy on the robot at a full charge of the capacitors. One important question we wanted to ask ourselves is: why is that same energy used for kicking, not used for the opposite, in ball receiving. When the robot is receiving a pass, it is subsequently not kicking at that moment - and secondly, the time that would need to be used for repositioning once a pass is received is sufficiently long enough for the capacitors to charge back up again for a 9A charging circuit. 

Thus, the reasoning to develop an electromechanical damping framework was formed. 

## How does it work?

Before the ball is received, the robot extends the kicker head with a low power kick, and then is held at the minimum required force to remain extended (Note: this increases slightly as you deplete your capacitor banks). The holding force is accomplished by pulsing the IGBT with a PWM signal with a duty cycle equal to just above the threshold where the kicker head would otherwise collapse back into the robot. 

Once the ball (weighing 45 grams) contacts the plunger (weighing 60 grams), the inertia from the ball transfers to the plunger, making it retract fully into the robot without enough energy for re-extension. The ball then rolls towards the dribbler with any remaining energy left, allowing for operation even without a dribbler or even better control with a simple dribbler which are both very acheiveable for new teams.

## Firmware Elaboration

As mentioned in our TDP, one needs to apply a short pulse which is equivalent to a very soft kick (just enough to get the kicker head out) to first extend the plunger. On our chicker boards, this is about 500 micro-seconds or less. All that is required is that the kicker head extends fully but with almost no power. 

Then, some nonzero waiting time should be applied to allow the mechanical system to finish actuating from the electrical force generated. The electrical system has a time scale of microseconds to milliseconds, however the mechanical system has a timescale from several milliseconds to seconds. The electrical force and mechanical force on the plunger can be modelled, but this requires a complex simulation involving the change in inductance over position of the solenoid - this is not important for one to get started, but may help one understand the exact time delay needed. This will hopefully be covered in our 2027 TDP.

Once the mechanical system has actuated the plunger, the PWM will be applied to the IGBT. Choosing a frequency for the PWM signal is somewhat important, as it will slightly change the audible sound from the robot, but will only matter depending on the accuracy of your microcontroller timer and any minimum limitations for pulse width. Higher frequencies (20kHz +) are recommended if you are able to achieve very accurate pulse widths for each switching cycle, with our initial goal to raise the frequency outside the audible range of the human ear. Due to the limitations on the RP2040 at the time, the 

## Rough Dynamic Model

To understand why the system works as it does, it is important to understand collision physics. In an elastic collision, the ball is repelled fully from the robot, such as a stationary object or a robot that is unable to receive the ball. Consequently, in an inelastic collision, the ball would be dragged backwards by the kicker head in an illegal move by SSL rules (which would only be possible if it was sticky). 

What we are achieving here is somewhere in the middle, a partially inelastic collision - because the kicker head is extended at minimum possible force to remain stationary, when the ball contacts the kicker head, it subsequently collapses with not enough sufficient force to maintain the hold.

From our inconclusive tests 1 and 3 shown on our 2026 TDP where the ball bounced off the robot, it can be observed that the plunger re-extends (due to microcontroller inaccuracy), causing a significantly more elastic collision than expected. This can be verified using momentum math, expecting a perfect damping to produce a coefficient of restitution (e) proportional (a measure of inelastic collision) to the masses of the ball to the plunger. 

Looking at the velocities of the ball on impact shown on the TDP, we can calculate this coefficient based on the momentum transfer equation:

-----
CHATGPT Stuff

To find the final velocity of the ball ($v_{out}$) when striking a stationary plunger, the conservation of momentum and coefficient of restitution equations combine into this explicit formula:

$$v_{out} = v_{in} \cdot \left( \frac{m_{ball} - e \cdot m_{plunger}}{m_{ball} + m_{plunger}} \right)$$

### Active Damping Test Summary

| Test No. | Ball Mass | Plunger Mass | Input Velocity ($v_{in}$) | Output Velocity ($v_{out}$) | Calculated Effective e | Collision Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | 45g | 60g | 3.0 m/s | 0.26 m/s | **0.5373** | Incomplete Damping (Ball rolled forward) |
| **2** | 45g | 60g | 2.7 m/s | 0.00 m/s | **0.7500** | **Perfect Active Damping (Dead Stop)** |
| **3** | 45g | 60g | 5.3 m/s | 0.40 m/s | **0.5283** | Incomplete Damping (Ball rolled forward) |

---

### Proof: Why Test 2 Worked Perfectly

For the ball to come to a complete, perfect standstill, its final velocity must equal zero ($v_{out} = 0$). Looking at the numerator of our formula, this only happens when:

$$m_{ball} - e \cdot m_{plunger} = 0$$

If we isolate $e$ for your exact physical system parameters:

$$e = \frac{m_{ball}}{m_{plunger}} = \frac{45\text{g}}{60\text{g}} = 0.75$$

Because Test 2's active damping system perfectly countered the impact mechanics to yield an effective $e$ of **exactly 0.75**, the numerator became zero, forcing $v_{out}$ to $0\text{ m/s}$. This mathematically proves why the middle test succeeded perfectly where the others fell short.

## Recommendations

As each team's robot will be slightly different, it is important to note that the active damping described here may differ in implementation due to the plunger weight. The heavier the plunger, the larger the force (and subsequently momentum) required to collapse the plunger. What this means is that with a heavier plunger the ball will remain in contact with the plunger longer, effectively raising the coefficient of restitution (e) (a measure of inelastic collision), and the ball will bounce off the plunger with increasing velocity.









