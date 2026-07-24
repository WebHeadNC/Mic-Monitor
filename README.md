# Mic Monitor
Python script that sends a webhook when your mic is in use.

I built this because I have a light connected to a smart outlet outside my home office, which I turn on when I'm in a call or online meeting so people know not to interrupt me. The problem was I kept forgetting to turn it on. I looked for an existing tool to automate this and came up empty, so I wrote my own.

It's a Python script packaged into a standalone executable with PyInstaller. When it detects the microphone is active, it fires a webhook to turn on the smart outlet. In my setup, that webhook goes to Bitfocus Companion, which controls a TP-Link Kasa outlet.

The app runs in the taskbar 

![image](https://github.com/user-attachments/assets/1d9a4771-d7fd-4645-98f0-606ac324abc7) ![image](https://github.com/user-attachments/assets/c6f5a9da-b043-4bed-b279-51903dfe4026)

It has a menu that allows you to configure the webhook. 

![image](https://github.com/user-attachments/assets/a876afa2-cfcb-441d-b80b-4c3b7d3b8e2d)

You can view the log if needed for troubleshooting. ![image](https://github.com/user-attachments/assets/6062c6b7-cb4a-4e1e-a05d-669524fab625)

Or you can exit the program.

## Mic Detection Method

The app can detect microphone use in two ways, selectable from the **Webhook Editor** menu:

- **Audio Session (recommended)** — reads the actual capture state from Windows Core Audio (WASAPI) via `pycaw`. This checks whether any application is really recording, so it isn't affected by taskbar rendering or UI lag.
- **Taskbar Icon** — the original method, which looks for the microphone icon in the taskbar using UI Automation. Kept as a fallback.

If `pycaw` isn't installed, the app automatically falls back to the Taskbar Icon method.

## Running the executable

Download **`Mic_Monitor_v4.exe`**. The icon is embedded, so it's fully self-contained — a single file with no other downloads required.

1. **Put the exe in its own folder** (for example `C:\Tools\MicMonitor\`). The app writes its log and settings files next to itself, so giving it a dedicated folder keeps those alongside the exe and out of a cluttered directory like Downloads. Avoid protected locations such as `C:\Program Files`, where Windows may block those writes.
2. **Double-click `Mic_Monitor_v4.exe`** to launch it. It runs in the taskbar tray — right-click the tray icon to open the Webhook Editor, view the log, or exit.

On first run the app creates `mic_monitor.log` automatically. `mic_monitor_config.json` is created only after you save settings in the Webhook Editor; until then it runs on built-in defaults.

## Start automatically with Windows

To have Mic Monitor launch every time you sign in, add a shortcut to your Startup folder:

1. Right-click **`Mic_Monitor_v4.exe`** and choose **Show more options → Send to → Desktop (create shortcut)**. (Or right-click the exe and pick **Create shortcut**.)
2. Press **Win + R**, type `shell:startup`, and press **Enter**. This opens your personal Startup folder:
   `%AppData%\Microsoft\Windows\Start Menu\Programs\Startup`
3. Move the shortcut you created into that Startup folder.

The app will now start automatically the next time you sign in. To stop it from auto-starting, delete the shortcut from the Startup folder (or disable it under **Task Manager → Startup apps**).

> Note: leave the exe in its dedicated folder and point the shortcut at it there — don't move the exe into the Startup folder itself, or its log/config files will be written into that folder too.

## Configuration

Settings (both webhook URLs/methods and the chosen detection method) are saved to `mic_monitor_config.json`, created next to the executable when you save them in the Webhook Editor. Your changes persist across restarts.

> Note: the config and log files are written to the app's own folder, so run the exe from a user-writable location (not `C:\Program Files`) to avoid Windows blocking the writes.

## Running from source

Install the dependencies and run the script:

```
pip install -r requirements.txt
python mic-monitor.py
```

Dependencies: `pywinauto`, `pystray`, `Pillow`, `requests`, and `pycaw` (for the Audio Session detection method).
