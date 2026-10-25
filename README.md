# IOT-BASED-Anti-Theft-Floor-Mat-with-Email-Alert

This project is a low-cost security system that hides inside a floor mat.

# How it works, step by step:

Someone steps on the mat, and a piezoelectric sensor feels the pressure.
An Arduino Nano reads the sensor and tells the laptop that someone is there.

A USB webcam takes a picture of the person.

The laptop compares the face with known faces, using OpenCV and dlib .

If the face is unknown, the system sends an email alert through Gmail.

A buzzer sounds, and a Flask web dashboard shows what happened.

# Main features

Detects footsteps with a piezoelectric sensor
Recognizes known and unknown faces
Sends instant email alerts to the owner
Includes a cooldown so one visitor doesn’t trigger many emails
Has a web dashboard built with Flask
Is cheap and easy to build

# Hardware: Arduino Nano (ATmega328P), piezoelectric sensor, buzzer, SG90 servo (webcam mount), USB webcam, laptop

# Software: Python, Flask, OpenCV, dlib, smtplib/yagmail, Arduino C
