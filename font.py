import os
import random
from PIL import ImageFont


FONT_DIR = r"C:\Windows\Fonts"


PREFERRED_FONTS = ["Arial.ttf", "Verdana.ttf", "Tahoma.ttf"]

def get_font(size, preferred_fonts=PREFERRED_FONTS):

    for f in preferred_fonts:
        path = os.path.join(FONT_DIR, f)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                continue

    system_fonts = [f for f in os.listdir(FONT_DIR) if f.lower().endswith(".ttf")]
    random.shuffle(system_fonts)
    for f in system_fonts:
        path = os.path.join(FONT_DIR, f)
        try:
            return ImageFont.truetype(path, size)
        except IOError:
            continue

    return ImageFont.load_default()
