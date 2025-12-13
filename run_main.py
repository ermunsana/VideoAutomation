# run_main.py
import subprocess
import sys
import os

# Function to run main.py
def run_main_py():
    print("Running main.py to generate the video...")
    try:
        # Ensure the virtual environment is activated
        venv_python = "D:/Github/Tiktoker/venv/Scripts/python.exe"  # Adjust to match your Python executable
        subprocess.run([venv_python, "main.py"], check=True)
        print("main.py executed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running main.py: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_main_py()
