# Solenoid Active Damping - Dedicated Project Repository

## Motivation
The solenoid active damping firmware and subsequent rough electromechanical model was developed by The Bots as a tool to improve pass receiving performance of the SSL league. Instead of using some complex mechanism described by other teams and mentioned in our 2026 TDP, we opted to use existing electromechanical energy on the robot to achieve the same goal. 

There is a tremendous amount of energy in each robot (1/2 * 2000uF * 210V^2, which is 44.1J for us specifically), and one important question to ask is why that energy is used for kicking the ball but not for receiving the ball - the robot that is receiving is not kicking at that moment, and the time used for repositioning once a pass is received is sufficiently long enough for the capacitors to charge back up again. 

Thus, the reasoning to develop a electromechanical damping framework was formed. 

## How does it work?

Before the ball is received, the robot extends the kicker head with a low power kick, and then is held at the minimum required force to remain extended (Note: this increases slightly as you deplete your capacitor banks). The holding force is accomplished by pulsing the IGBT with a PWM signal with a duty cycle equal to just above the threshold where the kicker head would otherwise collapse back into the robot. 

Once the ball (weighing 45 grams) contacts the plunger (weighing 60 grams), the inertia from the ball transfers to the plunger, making it retract fully into the robot. The ball then rolls towards the dribbler with any remaining energy left, allowing for operation even without a dribbler, or even better control with a simple dribbler.

## Firmware Elaboration

As mentioned in our TDP, one needs to apply a short pulse which is equivalent to a very soft kick (just enough to get the kicker head out) to first extend the plunger. Then, some nonzero waiting time should be applied to allow the mechanical system to finish actuating from the electrical force generated, and after which the PWM will be applied to the IGBT. 

Choosing a frequency for the PWM signal is important, as it will slightly change the audible sound from the robot. Depending on the accuracy of your microcontroller, you may be able to achieve better resolution at higher frequencies. 

## Rough Electromechanical Model

To understand why the system works as it does, it is important to understand collision physics. In an elastic collision, the ball is repelled fully from the robot, such as a stationary object or a dribbler that is unable to receive the ball. Consequently, in an inelastic collision, the ball would be dragged backwards by the kicker head in an illegal move by SSL rules (which would only be possible if it was sticky). What we are achieving here is somewhere in the middle, a partially inelastic collision - because the kicker head is extended at minimum possible force to remain stationary, when the ball contacts the kicker head, it subsequently collapses with not enough sufficient force to maintain the hold.

From our tests, it is interesting to note that from the partially inconclusive tests 1 and 3 shown on our 2026 TDP where the ball bounced off the robot, it can be seen that both tests had some nonzero horizontal velocity. However, these tests also showed that the plunger would re-extend, causing a significantly more elastic collision than expected. Thus, perhaps both of these explanations could be the causes for the bounce back in the tests shown. 





