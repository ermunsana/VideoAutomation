import os
import random
from PIL import ImageFont

# Directory with system fonts
FONT_DIR = r"C:\Windows\Fonts"

# Optional preferred fonts
PREFERRED_FONTS = ["Arial.ttf", "Verdana.ttf", "Tahoma.ttf"]

def get_font(size, preferred_fonts=PREFERRED_FONTS):
    # Try preferred fonts first
    for f in preferred_fonts:
        path = os.path.join(FONT_DIR, f)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                continue

    # Fallback: pick a random font from system fonts
    system_fonts = [f for f in os.listdir(FONT_DIR) if f.lower().endswith(".ttf")]
    random.shuffle(system_fonts)
    for f in system_fonts:
        path = os.path.join(FONT_DIR, f)
        try:
            return ImageFont.truetype(path, size)
        except IOError:
            continue

    # Last fallback
    return ImageFont.load_default()
