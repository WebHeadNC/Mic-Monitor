from pywinauto import Desktop
from pystray import Icon, MenuItem, Menu
from PIL import Image
import requests
import time
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import os
from datetime import datetime

# Global configuration variables
CONFIG = {
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

# Logging setup
LOG_FILE = os.path.join(os.path.dirname(sys.argv[0]), 'mic_monitor.log')
LOG_LOCK = threading.Lock()

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
    """Check if the microphone icon is present in the taskbar."""
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
def create_image():
    # Load the .ico file
    image = Image.open(r"C:\Scripts\headset2.ico")  # Replace with your actual path to the icon file
    return image

# Function for quitting the app and closing the program
def quit_action(icon, item):
    icon.stop()  # Stop the icon
    exit_event.set()  # Set the exit event to signal the program to exit

# Function to view log file
def view_log_file():
    """Open a window to view the log file with resizable features."""
    # Create the main window in the main thread
    log_window = tk.Tk()
    log_window.title("Mic Monitor Log")
    log_window.geometry("600x400")
    
    # Set the window icon to match the system tray icon
    log_window.iconbitmap(r"C:\Scripts\headset2.ico")

    # Make the window resizable
    log_window.resizable(True, True)

    # Configure grid layout to make the text area expandable
    log_window.grid_rowconfigure(0, weight=1)
    log_window.grid_columnconfigure(0, weight=1)

    # Create a scrolled text widget with dynamic wrapping
    log_text = scrolledtext.ScrolledText(log_window, 
                                         wrap=tk.WORD,  # Wrap by word
                                         borderwidth=10)
    log_text.grid(row=0, column=0, sticky='nsew')  # Expand in all directions

    # Read and display log contents
    try:
        with open(LOG_FILE, 'r') as f:
            log_text.insert(tk.END, f.read())
    except FileNotFoundError:
        log_text.insert(tk.END, "No log entries yet.")

    # Make the text read-only
    log_text.config(state=tk.DISABLED)

    # Add a scrollbar with a nice visual style
    scrollbar = ttk.Scrollbar(log_window, orient=tk.VERTICAL, command=log_text.yview)
    scrollbar.grid(row=0, column=1, sticky='ns')
    log_text.configure(yscrollcommand=scrollbar.set)

    # Scroll to the bottom by default to show latest entries
    log_text.see(tk.END)

    # Properly handle window closing
    log_window.mainloop()

# Function to open the Webhook Configuration GUI
def open_webhook_gui():
    """Create a GUI to edit webhook configurations."""
    def save_config():
        global CONFIG
        
        # Mic Appear Webhook Config
        CONFIG['mic_appear_webhook'] = {
            "url": appear_url_entry.get(),
            "method": appear_method_var.get(),
            "payload": {}  # Future enhancement: add payload configuration
        }
        
        # Mic Disappear Webhook Config
        CONFIG['mic_disappear_webhook'] = {
            "url": disappear_url_entry.get(),
            "method": disappear_method_var.get(),
            "payload": {}  # Future enhancement: add payload configuration
        }
        
        log_activity("Webhook configurations updated")
        messagebox.showinfo("Success", "Webhook configurations updated!")
        gui_window.quit()

    # Create the main window
    gui_window = tk.Tk()
    gui_window.title("Webhook Configuration")
    gui_window.geometry("500x300")
    
    # Set the window icon to match the system tray icon
    gui_window.iconbitmap(r"C:\Scripts\headset2.ico")


    # Mic Appear Webhook Section
    appear_frame = tk.LabelFrame(gui_window, text="Mic Appear Webhook")
    appear_frame.pack(pady=10, padx=10, fill='x')

    tk.Label(appear_frame, text="URL:").pack()
    appear_url_entry = tk.Entry(appear_frame, width=50)
    appear_url_entry.insert(0, CONFIG['mic_appear_webhook']['url'])
    appear_url_entry.pack()

    tk.Label(appear_frame, text="HTTP Method:").pack()
    appear_method_var = tk.StringVar(value=CONFIG['mic_appear_webhook']['method'])
    appear_method_dropdown = ttk.Combobox(appear_frame, textvariable=appear_method_var, values=['GET', 'POST'])
    appear_method_dropdown.pack()

    # Mic Disappear Webhook Section
    disappear_frame = tk.LabelFrame(gui_window, text="Mic Disappear Webhook")
    disappear_frame.pack(pady=10, padx=10, fill='x')

    tk.Label(disappear_frame, text="URL:").pack()
    disappear_url_entry = tk.Entry(disappear_frame, width=50)
    disappear_url_entry.insert(0, CONFIG['mic_disappear_webhook']['url'])
    disappear_url_entry.pack()

    tk.Label(disappear_frame, text="HTTP Method:").pack()
    disappear_method_var = tk.StringVar(value=CONFIG['mic_disappear_webhook']['method'])
    disappear_method_dropdown = ttk.Combobox(disappear_frame, textvariable=disappear_method_var, values=['GET', 'POST'])
    disappear_method_dropdown.pack()

    # Save Button
    save_button = tk.Button(gui_window, text="Save Configuration", command=save_config)
    save_button.pack(pady=10)

    # Properly handle closing the window (X button)
    gui_window.protocol("WM_DELETE_WINDOW", gui_window.quit)

    # Start the tkinter event loop
    gui_window.mainloop()

# Function to start the tray icon
def setup_tray_icon():
    """Create a tray icon with options to edit webhook URL and view log."""
    # Create a menu with options
    menu = Menu(
        MenuItem('Open Webhook Editor', lambda icon, item: threading.Thread(target=open_webhook_gui, daemon=True).start()),
        MenuItem('View Log', lambda icon, item: threading.Thread(target=view_log_file, daemon=True).start()),
        MenuItem('Quit', quit_action)
    )
    icon = Icon("test", create_image(), menu=menu)
    icon.run()

def mic_check_loop():
    """Main loop to check microphone status and send webhook."""
    previous_status = None  # Track the previous mic status to avoid duplicate webhooks
    first_check = True  # Flag to ignore the first status check

    log_activity("Microphone monitoring started")

    while not exit_event.is_set():
        mic_in_use = find_microphone_icon()
        
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
                    # Continue checking if the microphone icon is still present
                    if not find_microphone_icon():
                        # If microphone icon disappears during the 5-second wait, log and break
                        log_activity("Mic icon disappeared during confirmation")
                        break
                    time.sleep(0.5)  # Check every 0.5 seconds
                else:
                    # If the microphone icon remains visible for 5 seconds, send webhook
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

    log_activity("Microphone monitoring stopped")

if __name__ == "__main__":
    # Ensure log file exists
    open(LOG_FILE, 'a').close()

    # Run the system tray icon in a separate thread
    tray_thread = threading.Thread(target=setup_tray_icon, daemon=True)
    tray_thread.start()

    # Run the microphone checking loop
    mic_check_loop()

    # After the loop ends (i.e., exit_event is set), exit the program
    print("Exiting program...")
    sys.exit()
