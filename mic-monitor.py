from pywinauto import Desktop
from pystray import Icon, MenuItem, Menu
from PIL import Image
import requests
import time
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext
import os
import json
from datetime import datetime

# Optional WASAPI/Core Audio detection via pycaw. Imported lazily so the app
# still runs (falling back to the taskbar-icon method) if pycaw is missing.
try:
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioSessionManager2
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.api.audiopolicy import IAudioSessionControl2
    PYCAW_AVAILABLE = True
except Exception:
    PYCAW_AVAILABLE = False

# Core Audio constants (avoid depending on pycaw enum layout across versions)
EDATAFLOW_ECAPTURE = 1          # capture (recording) endpoints
DEVICE_STATE_ACTIVE = 0x1       # only enabled/plugged-in endpoints
AUDIO_SESSION_STATE_ACTIVE = 1  # AudioSessionStateActive

# Application version
__version__ = "4.1.0"

# Detection method identifiers
METHOD_AUDIO_SESSION = "audio_session"
METHOD_TASKBAR_ICON = "taskbar_icon"

# Global configuration variables
CONFIG = {
    "detection_method": METHOD_AUDIO_SESSION,  # or METHOD_TASKBAR_ICON
    "mic_appear_webhook": {
        "url": "http://192.168.1.9:8888/press/bank/1/2",
        "method": "GET",
        "payload": {}
    },
    "mic_disappear_webhook": {
        "url": "http://192.168.1.9:8888/press/bank/1/2",
        "method": "GET",
        "payload": {}
    }
}

# Event to signal the program to exit
exit_event = threading.Event()

# Shared, thread-safe "last known" mic status for UI display only — the
# detection loop below is still the sole source of truth for webhook
# decisions. None means no reading has happened yet.
_mic_status_lock = threading.Lock()
_mic_status = {"in_use": None}

def set_mic_status(value):
    with _mic_status_lock:
        _mic_status["in_use"] = value

def get_mic_status():
    with _mic_status_lock:
        return _mic_status["in_use"]

# Logging setup
LOG_FILE = os.path.join(os.path.dirname(sys.argv[0]), 'mic_monitor.log')
LOG_LOCK = threading.Lock()

def resource_path(relative):
    """Resolve a bundled read-only resource. Works both from source and when
    frozen by PyInstaller: files added via --add-data are extracted to
    sys._MEIPASS at runtime, so the icon lives inside the exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    return os.path.join(base, relative)

# Icon files — bundled inside the exe (PyInstaller --add-data), or alongside
# the script. ICON_FILE (standby/green) is the multi-resolution icon used for
# window title bars and the exe's own file icon.
#
# The tray icon uses separate single-frame 32x32 files (TRAY_ICON_FILE /
# TRAY_ICON_FILE_LIVE) instead of the multi-res ones: PIL's Image.open()
# defaults to the largest embedded frame (256x256) for a multi-res .ico, and
# pystray/Windows renders the tray icon far too small when handed that large
# a source. The original icon shipped as a single 32x32 frame and rendered
# correctly, so the tray icon mirrors that exact structure.
ICON_FILE = resource_path('headset2.ico')
TRAY_ICON_FILE = resource_path('headset2_tray.ico')
TRAY_ICON_FILE_LIVE = resource_path('headset_live_tray.ico')

# Persisted configuration file, stored alongside the script/exe
CONFIG_FILE = os.path.join(os.path.dirname(sys.argv[0]), 'mic_monitor_config.json')

def load_config():
    """Load persisted configuration from disk into CONFIG, if present.

    Missing keys fall back to the in-code defaults, so an older or partial
    config file won't break startup."""
    global CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            saved = json.load(f)
    except FileNotFoundError:
        return  # First run: keep defaults
    except (json.JSONDecodeError, OSError) as e:
        log_activity(f"Could not read config file, using defaults: {e}")
        return

    if isinstance(saved, dict):
        # Shallow-merge so any keys absent from the file keep their defaults
        for key, value in saved.items():
            CONFIG[key] = value

def save_config_to_disk():
    """Persist the current CONFIG to disk as JSON."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(CONFIG, f, indent=4)
        log_activity("Configuration saved to disk")
    except OSError as e:
        log_activity(f"Failed to save config file: {e}")

def log_activity(message):
    """Log activity to file, maintaining a max of 50 lines."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}\n"
    
    with LOG_LOCK:
        # Read existing log lines
        try:
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        
        # Append new line
        lines.append(log_entry)
        
        # Trim to last 50 lines
        lines = lines[-50:]
        
        # Write back to file
        with open(LOG_FILE, 'w') as f:
            f.writelines(lines)

def find_microphone_icon():
    """Check if the microphone icon is present in the taskbar (UI Automation)."""
    try:
        # Connect to the Taskbar
        taskbar = Desktop(backend="uia").window(title_re=".*Taskbar.*")

        # Iterate through all descendants in the taskbar
        for element in taskbar.descendants():
            element_info = element.element_info  # Get detailed info about the element

            # Check if the element contains "microphone" in its name (adjust for localization)
            if element_info.name and "microphone" in element_info.name.lower():
                return True  # Microphone icon found
        return False  # Microphone icon not found
    except Exception as e:
        log_activity(f"Error finding microphone icon: {e}")
        return False

def find_microphone_pycaw():
    """Check if any application is actively capturing audio via the Core Audio
    (WASAPI) session API. This reads real capture state from the OS rather than
    scraping a taskbar UI element, so it is not subject to render/UI-tree lag.

    Returns True if any active recording session is found on any enabled capture
    endpoint. Returns None to signal the caller it should fall back (pycaw not
    available or an unexpected COM error occurred)."""
    if not PYCAW_AVAILABLE:
        return None

    try:
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER,
        )
        # Sessions live per-endpoint, so check every active capture device
        # (covers setups with more than one microphone).
        collection = enumerator.EnumAudioEndpoints(
            EDATAFLOW_ECAPTURE, DEVICE_STATE_ACTIVE
        )
        for i in range(collection.GetCount()):
            device = collection.Item(i)
            mgr = device.Activate(
                IAudioSessionManager2._iid_, CLSCTX_ALL, None
            ).QueryInterface(IAudioSessionManager2)

            session_enum = mgr.GetSessionEnumerator()
            for j in range(session_enum.GetCount()):
                ctl = session_enum.GetSession(j)
                if ctl is None:
                    continue
                ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                if ctl2.GetState() == AUDIO_SESSION_STATE_ACTIVE:
                    return True
        return False
    except Exception as e:
        log_activity(f"Error checking audio sessions (pycaw): {e}")
        return None

def is_mic_in_use():
    """Dispatch to the configured detection method, falling back to the taskbar
    icon scan if the audio-session method is unavailable or errors out."""
    method = CONFIG.get("detection_method", METHOD_AUDIO_SESSION)

    if method == METHOD_AUDIO_SESSION:
        result = find_microphone_pycaw()
        if result is not None:
            return result
        # pycaw unavailable or errored: fall back to the icon scan
        return find_microphone_icon()

    return find_microphone_icon()

def send_webhook(webhook_config, event_type):
    """Send a webhook based on the provided configuration."""
    try:
        # Send webhook based on method
        if webhook_config['method'].upper() == 'GET':
            response = requests.get(webhook_config['url'], params=webhook_config['payload'])
        elif webhook_config['method'].upper() == 'POST':
            response = requests.post(webhook_config['url'], json=webhook_config['payload'])
        else:
            log_activity(f"Unsupported HTTP method: {webhook_config['method']}")
            return
        
        response.raise_for_status()
        log_activity(f"{event_type} Webhook sent successfully to {webhook_config['url']}")
    except requests.exceptions.RequestException as e:
        log_activity(f"Failed to send {event_type} webhook: {e}")

# Function to load an existing .ico file
def create_image(live=False):
    # Single-frame 32x32 icon for the tray, matching the state requested
    image = Image.open(TRAY_ICON_FILE_LIVE if live else TRAY_ICON_FILE)
    return image

# Function for quitting the app and closing the program
def quit_action(icon, item):
    icon.stop()  # Stop the icon
    exit_event.set()  # Set the exit event to signal the program to exit

# ============================================================
# UI theme — "studio tally panel": a dark instrument-panel look that
# borrows the vocabulary of broadcast ON AIR / tally lights, since this
# app's whole job is acting like one — it drives a real physical light.
# Centralized here so both windows stay visually consistent.
# ============================================================

PANEL = "#2B2A28"    # window background (warm graphite, not pure black)
WELL = "#211F1D"     # recessed fields / unselected controls
LINE = "#46433E"     # hairlines, selected control background
INK = "#EDE7DD"      # primary text (warm bone white)
MUTED = "#9C948A"    # secondary / de-emphasized text
LIVE = "#E1432C"     # tally red — mic active (reserved for this meaning only)
STANDBY = "#7A9B76"  # tally green — idle / normal (reserved for this meaning only)
ALERT = "#D9A441"    # amber — genuine errors/warnings only, log viewer

FONT_DISPLAY = ("Bahnschrift", 20, "bold")   # big status word
FONT_EYEBROW = ("Bahnschrift", 9, "bold")    # small tracked section headers
FONT_BODY = ("Segoe UI", 10)
FONT_BODY_BOLD = ("Segoe UI", 10, "bold")
FONT_CAPTION = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_MONO_BOLD = ("Consolas", 10, "bold")


def tracked(text, gap=" "):
    """Fake letter-spacing for short caps labels (Tk has no tracking)."""
    return gap.join(text.upper())


def apply_dark_titlebar(window):
    """Best-effort: match the native title bar to the dark panel theme on
    Windows 10 1809+/11. Safely no-ops if unsupported."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, then old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


def styled_window(title):
    """Create a themed Tk root: dark panel background + matching titlebar."""
    window = tk.Tk()
    window.title(title)
    window.configure(bg=PANEL)
    try:
        window.iconbitmap(ICON_FILE)
    except Exception:
        pass
    window.after(10, lambda: apply_dark_titlebar(window))
    return window


def eyebrow(parent, text):
    return tk.Label(parent, text=tracked(text), font=FONT_EYEBROW, bg=PANEL, fg=MUTED)


def make_segmented(parent, options, variable, disabled_values=None, on_change=None):
    """A row of toggle buttons acting as a single-select control, bound to a
    tk.StringVar. Used instead of ttk.Combobox so labels never truncate and
    the control matches the panel theme."""
    disabled_values = disabled_values or set()
    wrap = tk.Frame(parent, bg=WELL, highlightthickness=1, highlightbackground=LINE)
    buttons = {}

    def refresh():
        current = variable.get()
        for value, btn in buttons.items():
            if value in disabled_values:
                btn.configure(bg=WELL, fg=LINE, cursor="arrow")
            elif value == current:
                btn.configure(bg=LINE, fg=INK, cursor="hand2")
            else:
                btn.configure(bg=WELL, fg=MUTED, cursor="hand2")

    def select(value):
        if value in disabled_values:
            return
        variable.set(value)
        refresh()
        if on_change:
            on_change(value)

    for i, (label, value) in enumerate(options):
        btn = tk.Button(
            wrap, text=tracked(label), font=FONT_BODY_BOLD,
            bd=0, relief=tk.FLAT, padx=16, pady=7,
            activebackground=LINE, activeforeground=INK,
            highlightthickness=1, highlightbackground=WELL, highlightcolor=STANDBY,
            command=lambda v=value: select(v),
        )
        btn.pack(side=tk.LEFT, padx=(1 if i else 0, 0), pady=1)
        btn.bind("<FocusIn>", lambda e, b=btn: b.configure(highlightbackground=STANDBY))
        btn.bind("<FocusOut>", lambda e, b=btn: b.configure(highlightbackground=WELL))
        buttons[value] = btn

    refresh()
    return wrap


def styled_entry(parent, initial=""):
    entry = tk.Entry(
        parent, font=FONT_MONO, bg=WELL, fg=INK, insertbackground=INK,
        relief=tk.FLAT, highlightthickness=1, highlightbackground=LINE,
        highlightcolor=STANDBY,
    )
    entry.insert(0, initial)
    return entry


def build_status_panel(parent):
    """The signature element: a tally light showing whether the app currently
    believes the mic is active. Driven by the shared status the detection
    loop already computes, not a separate independent poll."""
    frame = tk.Frame(parent, bg=PANEL)

    row = tk.Frame(frame, bg=PANEL)
    row.pack(anchor="w")

    led = tk.Canvas(row, width=20, height=20, bg=PANEL, highlightthickness=0)
    led.pack(side=tk.LEFT, padx=(0, 10))
    ring = led.create_oval(2, 2, 18, 18, outline="", fill=WELL)
    dot = led.create_oval(6, 6, 14, 14, outline="", fill=WELL)

    word = tk.Label(row, text=tracked("Checking"), font=FONT_DISPLAY, bg=PANEL, fg=MUTED)
    word.pack(side=tk.LEFT)

    caption = tk.Label(
        frame, text="Waiting for the first reading…",
        font=FONT_CAPTION, bg=PANEL, fg=MUTED,
    )
    caption.pack(anchor="w", pady=(2, 0))

    job = {"id": None}

    def pulse(bright):
        try:
            led.itemconfigure(dot, fill=LIVE if bright else "#8A2A1E")
            led.itemconfigure(ring, fill="#5A2018" if bright else WELL)
            job["id"] = frame.after(650, lambda: pulse(not bright))
        except tk.TclError:
            pass

    def set_status(value):
        if job["id"] is not None:
            try:
                frame.after_cancel(job["id"])
            except tk.TclError:
                pass
            job["id"] = None
        if value is True:
            word.configure(text=tracked("On Air"), fg=LIVE)
            caption.configure(text="Sending the signal to turn your light on")
            pulse(True)
        elif value is False:
            led.itemconfigure(ring, fill=WELL)
            led.itemconfigure(dot, fill=STANDBY)
            word.configure(text=tracked("Standby"), fg=STANDBY)
            caption.configure(text="Listening for microphone activity")
        else:
            led.itemconfigure(ring, fill=WELL)
            led.itemconfigure(dot, fill=WELL)
            word.configure(text=tracked("Checking"), fg=MUTED)
            caption.configure(text="Waiting for the first reading…")

    def stop():
        if job["id"] is not None:
            try:
                frame.after_cancel(job["id"])
            except tk.TclError:
                pass
            job["id"] = None

    set_status(None)
    return frame, set_status, stop


def classify_log_message(message):
    """Map a log message to a (tag, label) pair used to colorize the log
    viewer. Purely a display concern — the on-disk log format is untouched."""
    m = message.lower()
    if "mic appear webhook sent" in m:
        return "live", "ON-AIR"
    if "mic disappear webhook sent" in m:
        return "standby", "STANDBY"
    if "failed" in m or "error" in m or "could not" in m:
        return "alert", "ALERT"
    return "system", "SYSTEM"


def render_log(text_widget, content):
    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", tk.END)
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        text_widget.insert(tk.END, "No activity yet.\n", ("system", "entry"))
    for line in lines:
        if " - " in line:
            ts, message = line.split(" - ", 1)
        else:
            ts, message = "", line
        tag, label = classify_log_message(message)
        # "entry" (hanging indent for wrapped continuation lines) is applied
        # directly at insert time on every segment, rather than computed
        # afterward via tag_add — Tk's END index doesn't reliably line up
        # with where insert(END, ...) actually lands in this widget.
        text_widget.insert(tk.END, f"{ts}  ", ("ts", "entry"))
        text_widget.insert(tk.END, f"{label.ljust(7)} ", (tag, "entry"))
        text_widget.insert(tk.END, f"{message}\n", ("msg", "entry"))
    text_widget.configure(state=tk.DISABLED)

# Function to view log file
def view_log_file():
    """Open a window showing the log as a colorized, auto-refreshing readout."""
    log_window = styled_window("Mic Monitor Log")
    log_window.geometry("640x420")
    log_window.resizable(True, True)

    body = tk.Frame(log_window, bg=PANEL)
    body.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

    eyebrow(body, "Activity Log").pack(anchor="w")
    tk.Frame(body, bg=LINE, height=1).pack(fill=tk.X, pady=(6, 10))

    text_frame = tk.Frame(body, bg=WELL, highlightthickness=1, highlightbackground=LINE)
    text_frame.pack(fill=tk.BOTH, expand=True)

    # ScrolledText already includes its own scrollbar, so no separate one is
    # needed. A flat border with a little internal padding keeps it clean.
    log_text = scrolledtext.ScrolledText(
        text_frame,
        wrap=tk.WORD,
        font=FONT_MONO,
        relief=tk.FLAT,
        borderwidth=0,
        padx=10,
        pady=10,
        background=WELL,
        foreground=INK,
        insertbackground=INK,
    )
    log_text.pack(fill=tk.BOTH, expand=True)

    # Tags used to colorize each line by what it means, not just show raw text
    log_text.tag_configure("ts", foreground=MUTED, font=FONT_MONO_SM)
    log_text.tag_configure("live", foreground=LIVE, font=FONT_MONO_BOLD)
    log_text.tag_configure("standby", foreground=STANDBY, font=FONT_MONO_BOLD)
    log_text.tag_configure("alert", foreground=ALERT, font=FONT_MONO_BOLD)
    log_text.tag_configure("system", foreground=MUTED, font=FONT_MONO_BOLD)
    log_text.tag_configure("msg", foreground=INK, font=FONT_MONO)
    # Hang-indent wrapped continuation lines under the message column
    log_text.tag_configure("entry", lmargin2=205)

    # Read and display log contents
    try:
        with open(LOG_FILE, 'r') as f:
            initial_content = f.read()
    except FileNotFoundError:
        initial_content = ""
    render_log(log_text, initial_content)
    log_text.see(tk.END)

    # Poll for new entries so the window can be left open during a call and
    # show mic transitions live, without needing to reopen it.
    refresh_state = {"mtime": None, "job": None}

    def refresh_loop():
        try:
            if not log_window.winfo_exists():
                return
            try:
                mtime = os.path.getmtime(LOG_FILE)
            except FileNotFoundError:
                mtime = None
            if mtime != refresh_state["mtime"]:
                refresh_state["mtime"] = mtime
                at_bottom = log_text.yview()[1] >= 0.999
                try:
                    with open(LOG_FILE, 'r') as f:
                        content = f.read()
                except FileNotFoundError:
                    content = ""
                render_log(log_text, content)
                if at_bottom:
                    log_text.see(tk.END)
            refresh_state["job"] = log_window.after(1500, refresh_loop)
        except tk.TclError:
            pass

    refresh_state["job"] = log_window.after(1500, refresh_loop)

    # Cancel the pending refresh timer before destroying — Tk doesn't do this
    # automatically, and a dangling `after` firing post-destroy raises a Tcl
    # "invalid command name" error.
    def close_log_window():
        if refresh_state["job"] is not None:
            try:
                log_window.after_cancel(refresh_state["job"])
            except tk.TclError:
                pass
            refresh_state["job"] = None
        log_window.destroy()

    log_window.protocol("WM_DELETE_WINDOW", close_log_window)
    log_window.mainloop()

# Function to open the Webhook Configuration GUI
def open_webhook_gui():
    """Themed control panel: live status, detection method, and both webhooks."""
    method_options = [("Audio Session", METHOD_AUDIO_SESSION), ("Taskbar Icon", METHOD_TASKBAR_ICON)]
    method_disabled = set() if PYCAW_AVAILABLE else {METHOD_AUDIO_SESSION}
    method_hints = {
        METHOD_AUDIO_SESSION: "Reads real microphone activity from Windows audio — most reliable.",
        METHOD_TASKBAR_ICON: "Watches the taskbar icon — can lag a few seconds behind.",
    }
    if not PYCAW_AVAILABLE:
        method_hints[METHOD_AUDIO_SESSION] += " (Unavailable: pycaw isn't installed.)"

    gui_window = styled_window("Webhook Configuration")
    gui_window.geometry("460x640")
    gui_window.resizable(True, True)

    body = tk.Frame(gui_window, bg=PANEL)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)

    # --- Status: the signature element ---
    status_frame, set_status, stop_status_pulse = build_status_panel(body)
    status_frame.pack(fill=tk.X, pady=(0, 16))

    tk.Frame(body, bg=LINE, height=1).pack(fill=tk.X, pady=(0, 16))

    # --- Detection method ---
    eyebrow(body, "Detection Method").pack(anchor="w")

    current_method = CONFIG.get('detection_method', METHOD_AUDIO_SESSION)
    if current_method in method_disabled:
        current_method = METHOD_TASKBAR_ICON
    detection_method_var = tk.StringVar(value=current_method)

    method_hint = tk.Label(
        body, text=method_hints[current_method], font=FONT_CAPTION,
        bg=PANEL, fg=MUTED, wraplength=400, justify=tk.LEFT,
    )

    def on_method_change(value):
        method_hint.configure(text=method_hints[value])

    make_segmented(
        body, method_options, detection_method_var,
        disabled_values=method_disabled, on_change=on_method_change,
    ).pack(anchor="w", pady=(8, 4))
    method_hint.pack(anchor="w", pady=(0, 16))

    # --- Mic appear webhook ---
    eyebrow(body, "When Mic Turns On").pack(anchor="w")
    tk.Label(body, text="URL", font=FONT_CAPTION, bg=PANEL, fg=MUTED).pack(anchor="w", pady=(8, 2))
    appear_url_entry = styled_entry(body, CONFIG['mic_appear_webhook']['url'])
    appear_url_entry.pack(fill=tk.X, ipady=5)

    appear_method_var = tk.StringVar(value=CONFIG['mic_appear_webhook']['method'])
    make_segmented(
        body, [("GET", "GET"), ("POST", "POST")], appear_method_var,
    ).pack(anchor="w", pady=(8, 16))

    # --- Mic disappear webhook ---
    eyebrow(body, "When Mic Turns Off").pack(anchor="w")
    tk.Label(body, text="URL", font=FONT_CAPTION, bg=PANEL, fg=MUTED).pack(anchor="w", pady=(8, 2))
    disappear_url_entry = styled_entry(body, CONFIG['mic_disappear_webhook']['url'])
    disappear_url_entry.pack(fill=tk.X, ipady=5)

    disappear_method_var = tk.StringVar(value=CONFIG['mic_disappear_webhook']['method'])
    make_segmented(
        body, [("GET", "GET"), ("POST", "POST")], disappear_method_var,
    ).pack(anchor="w", pady=(8, 20))

    # --- Save ---
    footer = tk.Frame(body, bg=PANEL)
    footer.pack(fill=tk.X)

    saved_label = tk.Label(footer, text="", font=FONT_CAPTION, bg=PANEL, fg=STANDBY)
    saved_label.pack(side=tk.RIGHT, padx=(0, 12))

    def do_save():
        global CONFIG

        CONFIG['detection_method'] = detection_method_var.get()
        CONFIG['mic_appear_webhook'] = {
            "url": appear_url_entry.get(),
            "method": appear_method_var.get(),
            "payload": {}  # Future enhancement: add payload configuration
        }
        CONFIG['mic_disappear_webhook'] = {
            "url": disappear_url_entry.get(),
            "method": disappear_method_var.get(),
            "payload": {}  # Future enhancement: add payload configuration
        }

        # Persist so settings survive a restart
        save_config_to_disk()
        log_activity("Webhook configurations updated")

        # Inline confirmation instead of a jarring native dialog
        saved_label.configure(text=tracked("Saved"))
        gui_window.after(900, close_window)

    save_button = tk.Button(
        footer, text=tracked("Save Changes"), font=FONT_BODY_BOLD,
        bg=STANDBY, fg="#16211A", activebackground="#8FB588", activeforeground="#16211A",
        bd=0, relief=tk.FLAT, padx=20, pady=9, cursor="hand2",
        highlightthickness=1, highlightbackground=STANDBY, highlightcolor=INK,
        command=do_save,
    )
    save_button.pack(side=tk.LEFT)

    # --- Live status polling: reads the shared status set by the detection
    # loop (mic_check_loop), rather than running a second independent poll ---
    poll_job = {"id": None}

    def poll_status():
        try:
            if not gui_window.winfo_exists():
                return
            set_status(get_mic_status())
            poll_job["id"] = gui_window.after(1000, poll_status)
        except tk.TclError:
            pass

    poll_status()

    # Cancel pending timers (status poll + LED pulse) before destroying — Tk
    # doesn't do this automatically, and a dangling `after` firing post-destroy
    # raises a Tcl "invalid command name" error.
    def close_window():
        if poll_job["id"] is not None:
            try:
                gui_window.after_cancel(poll_job["id"])
            except tk.TclError:
                pass
            poll_job["id"] = None
        stop_status_pulse()
        gui_window.destroy()

    # Properly handle closing the window (X button)
    gui_window.protocol("WM_DELETE_WINDOW", close_window)

    # Start the tkinter event loop
    gui_window.mainloop()

# Function to start the tray icon
def watch_tray_icon(icon):
    """Swap the tray icon between standby/on-air art to match the shared mic
    status, the same one the config window's status LED reads from."""
    last = None
    while not exit_event.is_set():
        current = bool(get_mic_status())
        if current != last:
            try:
                icon.icon = create_image(live=current)
            except Exception as e:
                log_activity(f"Failed to update tray icon: {e}")
            last = current
        time.sleep(1)

def setup_tray_icon():
    """Create a tray icon with options to edit webhook URL and view log."""
    # Create a menu with options
    menu = Menu(
        MenuItem('Open Webhook Editor', lambda icon, item: threading.Thread(target=open_webhook_gui, daemon=True).start()),
        MenuItem('View Log', lambda icon, item: threading.Thread(target=view_log_file, daemon=True).start()),
        MenuItem('Quit', quit_action)
    )
    icon = Icon("Mic Monitor", create_image(), menu=menu)
    log_activity("Tray icon loaded")
    threading.Thread(target=watch_tray_icon, args=(icon,), daemon=True).start()
    icon.run()

def mic_check_loop():
    """Main loop to check microphone status and send webhook."""
    previous_status = None  # Track the previous mic status to avoid duplicate webhooks
    first_check = True  # Flag to ignore the first status check

    # Initialize COM for this thread so the pycaw/Core Audio calls work
    if PYCAW_AVAILABLE:
        try:
            comtypes.CoInitialize()
        except OSError as e:
            # RPC_E_CHANGED_MODE (-2147417850): COM is already initialized on
            # this thread in a different apartment mode. Harmless — the audio
            # APIs work regardless, so only log genuinely unexpected errors.
            if getattr(e, "winerror", None) != -2147417850:
                log_activity(f"COM initialization warning: {e}")

    log_activity(f"Microphone monitoring started (method: {CONFIG.get('detection_method')})")

    while not exit_event.is_set():
        mic_in_use = is_mic_in_use()
        set_mic_status(mic_in_use)

        # Skip webhook on the first check
        if first_check:
            previous_status = mic_in_use
            first_check = False
            time.sleep(10)
            continue
        
        # Send webhook if status changes
        if mic_in_use != previous_status:
            if mic_in_use:
                # If microphone icon appears, wait for 5 seconds to confirm
                start_time = time.time()
                confirmation_passed = False
                while time.time() - start_time < 5:
                    # Continue checking if the microphone is still in use
                    still_in_use = is_mic_in_use()
                    set_mic_status(still_in_use)
                    if not still_in_use:
                        # If mic use stops during the 5-second wait, log and break
                        log_activity("Mic use stopped during confirmation")
                        break
                    time.sleep(0.5)  # Check every 0.5 seconds
                else:
                    # If the microphone stays in use for 5 seconds, send webhook
                    send_webhook(CONFIG['mic_appear_webhook'], "Mic Appear")
                    confirmation_passed = True
                
                # Only update previous status if confirmation passes
                if confirmation_passed:
                    previous_status = mic_in_use
            else:
                # If microphone icon disappears, send webhook
                send_webhook(CONFIG['mic_disappear_webhook'], "Mic Disappear")
                previous_status = mic_in_use
        
        time.sleep(10)  # Check every 10 seconds

    log_activity("Microphone monitoring stopped")

if __name__ == "__main__":
    # Ensure log file exists
    open(LOG_FILE, 'a').close()

    # Load persisted configuration (webhooks + detection method)
    load_config()

    # Run the system tray icon in a separate thread
    tray_thread = threading.Thread(target=setup_tray_icon, daemon=True)
    tray_thread.start()

    # Run the microphone checking loop
    mic_check_loop()

    # After the loop ends (i.e., exit_event is set), exit the program
    print("Exiting program...")
    sys.exit()
