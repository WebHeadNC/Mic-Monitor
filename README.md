# Mic Monitor
Python script that sends a webhook when your mic is in use.

I created this app because I have a light connected to a smart outlet outside my home office that I turn on when I'm in a call or online meeting to ovaid interuptions. However I kept forgetting to turn it on. I tried to find a simple program to use but when I couldn't come up with anything I decided to write my own. I wrote it as a python script and then used pyinstaller to convert it to an executable file. When it detects that the mic is active it send a webhook to my smart outlet to turn the light on.

The app runs in the taskbar 

![image](https://github.com/user-attachments/assets/1d9a4771-d7fd-4645-98f0-606ac324abc7) ![image](https://github.com/user-attachments/assets/c6f5a9da-b043-4bed-b279-51903dfe4026)

It has a menu that allows you to configure the webhook. 

![image](https://github.com/user-attachments/assets/a876afa2-cfcb-441d-b80b-4c3b7d3b8e2d)

You can view the log if needed for troubleshooting. ![image](https://github.com/user-attachments/assets/6062c6b7-cb4a-4e1e-a05d-669524fab625)

Or you can exit the program.
