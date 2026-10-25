#include <Servo.h>
#include <Wire.h>
#include <DFRobot_LCD.h>

const int colorR = 255;
const int colorG = 0;
const int colorB = 0;

DFRobot_LCD lcd(16,2);  //16 characters and 2 lines of show
Servo myservo;  // create servo object to control a servo
#define piezo_L 7
#define piezo_R 6

int flag = false;
int flag2 = false;
 
void setup()
{
  Serial.begin(9600);
  lcd.init();
  lcd.setRGB(colorR, colorG, colorB);//If the module is a monochrome screen, you need to shield it
 
  lcd.print("> Welcome to the ");
  lcd.setCursor(0, 1);
  lcd.print("<<< System >>>");
  delay(1000);
  myservo.attach(9);  // attaches the servo on pin 9 to the servo object
  pinMode(piezo_L, INPUT);
  pinMode(piezo_R, INPUT);

  Serial.println("welcome");

}

void loop()
{
 if(digitalRead(piezo_L) == LOW && flag == false){
    flag = true; myservo.write(0); lcd_1();
    Serial.println("     "); 
    Serial.println("Waiting for Capturing Image"); delay(1500);
    Serial.println("Image captured and send via Gmail");
  }

 else if(digitalRead(piezo_L) == HIGH && flag == true) { flag = false; }

 if(digitalRead(piezo_R) == LOW && flag2 == false){
    flag2 = true; myservo.write(180); lcd_2();
    Serial.println("     "); 
    Serial.println("Waiting for Capturing Image"); delay(1500);
    Serial.println("Image captured and send via Gmail");
 }

 else if(digitalRead(piezo_R) == HIGH && flag2 == true) { flag2 = false; }
}

void lcd_1()
{   
  lcd.clear(); 
  lcd.setCursor(0, 0); lcd.print("Person at ");
  lcd.setCursor(0, 1); lcd.print("Location First ");
}

void lcd_2()
{   
  lcd.clear(); 
  lcd.setCursor(0, 0); lcd.print("Person at ");
  lcd.setCursor(0, 1); lcd.print("Location Second ");
}
