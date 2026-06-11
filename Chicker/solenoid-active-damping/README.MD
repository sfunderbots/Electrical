# Solenoid Active Damping - Dedicated Project Repository

## Motivation
The solenoid active damping theory and subsequent implementation was developed by The Bots as a tool to improve pass receiving performance of the SSL league. This was first tested after RoboCup 2025, with updated code for The Bots' Chicker board which split the CAN messages into operating modes - a key change to incoroprate damping was first to be able to test it seamlessly without changing the kicking functions of the robot. A more detailed explanation of the changes to the firmware and the 'modes' can be seen on our 2026 TDP. 

To achieve a pass, most teams build some complex damping mechanism involving the rotating dribbler and compliant mechanisms, which was explained on our 2026 TDP. However, as this adds cost and complexity for new teams and becomes a significant hurdle to overcome, we decided to try a theory that would use the existing electromechanical energy on the robot to achieve the same goal (and potentially better, pending full testing).

If one is to calculate the energy on SSL robots, one can notice there is a tremendous amount of energy that is unspent during the receiving phase. For our robots specifically, there is 1/2 * 2000uF * 210V^2 = 44.1J of energy on the robot at a full charge of the capacitors. One important question we wanted to ask ourselves is: why is that same energy used for kicking, not used for the opposite, in ball receiving. When the robot is receiving a pass, it is subsequently not kicking at that moment - secthe time used for repositioning once a pass is received is sufficiently long enough for the capacitors to charge back up again. 

Thus, the reasoning to develop a electromechanical damping framework was formed. 

## How does it work?

Before the ball is received, the robot extends the kicker head with a low power kick, and then is held at the minimum required force to remain extended (Note: this increases slightly as you deplete your capacitor banks). The holding force is accomplished by pulsing the IGBT with a PWM signal with a duty cycle equal to just above the threshold where the kicker head would otherwise collapse back into the robot. 

Once the ball (weighing 45 grams) contacts the plunger (weighing 60 grams), the inertia from the ball transfers to the plunger, making it retract fully into the robot. The ball then rolls towards the dribbler with any remaining energy left, allowing for operation even without a dribbler, or even better control with a simple dribbler.

## Firmware Elaboration

As mentioned in our TDP, one needs to apply a short pulse which is equivalent to a very soft kick (just enough to get the kicker head out) to first extend the plunger. On our chicker boards, this is about 500 micro-seconds or less. All that is required is that the kicker head extends fully but with almost no power. 

Then, some nonzero waiting time should be applied to allow the mechanical system to finish actuating from the electrical force generated. The electrical system has a time scale of microseconds to milliseconds, however the mechanical system has a timescale from several milliseconds to seconds. The electrical force and mechanical force on the plunger can be modelled, but this requires a complex simulation involving the change in inductance over position of the solenoid - this is not important for one to get started, but may help one understand the exact time delay needed. This will hopefully be covered in our 2027 TDP.

Once the mechanical system has actuated the plunger, the PWM will be applied to the IGBT. Choosing a frequency for the PWM signal is somewhat important, as it will slightly change the audible sound from the robot, but will only matter depending on the accuracy of your microcontroller timer and any minimum limitations for pulse width. Higher frequencies (20kHz +) are recommended if you are able to achieve very accurate pulse widths for each switching cycle, with our initial goal to raise the frequency outside the audible range of the human ear. Due to the limitations on the RP2040 at the time, the 



## Rough Dynamic Model

To understand why the system works as it does, it is important to understand collision physics. In an elastic collision, the ball is repelled fully from the robot, such as a stationary object or a dribbler that is unable to receive the ball. Consequently, in an inelastic collision, the ball would be dragged backwards by the kicker head in an illegal move by SSL rules (which would only be possible if it was sticky). 

What we are achieving here is somewhere in the middle, a partially inelastic collision - because the kicker head is extended at minimum possible force to remain stationary, when the ball contacts the kicker head, it subsequently collapses with not enough sufficient force to maintain the hold.

From our tests, it is interesting to note that from the partially inconclusive tests 1 and 3 shown on our 2026 TDP where the ball bounced off the robot, it can be seen that both tests had some nonzero horizontal velocity. However the hese tests also showed that the plunger would re-extend (due to microcontroller inaccuracy), causing a significantly more elastic collision than expected. Thus, perhaps both of these explanations could be the causes for the reflections shown in those tests. 







