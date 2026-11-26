import math
import os
import time

import machine
import psram
from picovector import ANTIALIAS_FAST, HALIGN_CENTER, PicoVector, Polygon, Transform
from presto import Presto

psram.mkramfs()

try:
    with open("/ramfs/launch.txt", "r") as f:
        result = f.readline()
except OSError:
    result = ""

if result.endswith(".py"):
    os.remove("/ramfs/launch.txt")
    __import__(result[:-3])

# Setup for the Presto display
presto = Presto(ambient_light=False)

display = presto.display

WIDTH, HEIGHT = display.get_bounds()

icons = {
    "travel": "\ue6ca",
    "bomb": "\uf568",
    "lightbulb": "\ue0f0",
    "deployed-code": "\uf720",
    "photo-library": "\ue413",
    "joystick": "\uf5ee",
    "monitoring": "\uf190",
    "timer": "\ue425",
    "description": "\ue873",
    "schedule": "\ue8b5"
}

CENTER_Y = HEIGHT // 2
CENTER_X = WIDTH // 2

OFFSET_X = 0
OFFSET_Y = -30

RADIUS_X = 160
RADIUS_Y = 30

# Couple of colours for use later
BLACK = display.create_pen(0, 0, 0)
BACKGROUND = display.create_pen(255, 250, 240)

# We do a clear and update here to stop the screen showing whatever is in the buffer.
display.set_pen(BLACK)
display.clear()
presto.update()

# Pico Vector
vector = PicoVector(display)
vector.set_antialiasing(ANTIALIAS_FAST)

vector.set_font("Roboto-Medium-With-Material-Symbols.af", 20)
vector.set_font_align(HALIGN_CENTER)


t = Transform()

# Touch tracking and menu movement
touch_start_x = 0
touch_start_time = None
tap = False

move_angle = 0
move = 0
friction = 0.98

# Rounded corners
rounded_corners = Polygon()
rounded_corners.rectangle(0, 0, WIDTH, HEIGHT)
rounded_corners.rectangle(0, 0, WIDTH, HEIGHT, (10, 10, 10, 10))


# Story Map's tile: the Auto Story Map logo (a backbone bar over columns of cards),
# scaled from its 32 x 32 SVG to the 60 x 60 tile. Added to Pimoroni's launcher;
# the original is backed up as main.py.orig.
LOGO_FILE = "story_map.py"
LOGO_BG = display.create_pen(0x50, 0x46, 0xD5)
LOGO_BAR = display.create_pen(255, 255, 255)
LOGO_CARD = display.create_pen(0xC9, 0xC4, 0xF5)
LOGO_OUTLINE = display.create_pen(0x2E, 0x26, 0x9A)


def logo_polygons(w, h):
    k = w / 32

    def box(x, y, bw, bh, r):
        r = max(1, int(r * k))
        return Polygon().rectangle(x * k - w / 2, y * k - h / 2, bw * k, bh * k, (r, r, r, r))

    bar = box(5, 5, 22, 5, 1.5)
    cards = Polygon()
    for x, y in ((5, 12.5), (5, 18), (13, 12.5), (13, 18), (13, 23.5), (21, 12.5)):
        r = max(1, int(k))
        cards.rectangle(x * k - w / 2, y * k - h / 2, 6 * k, 4 * k, (r, r, r, r))
    return bar, cards


class Application:
    maximum_scale = 1.6
    minimum_scale = 0.6
    count = 0

    def __init__(self, w, h, file):
        self.index = Application.count
        Application.count += 1

        self.selected = False
        self.icon = "description"

        # Bit of filename formatting for scripts without a title in the header.
        self.name = " ".join([w[0].upper() + w[1:] for w in file[:-3].replace("_", " ").split()])
        self.description = ""

        with open(file) as f:
            header = [f.readline().strip() for _ in range(3)]

        for line in header:
            if line.startswith("# ICON "):
                icon = line[7:].strip()
                self.icon = icons[icon]

            if line.startswith("# NAME "):
                self.name = line[7:]

            if line.startswith("# DESC "):
                self.description = line[7:]

        self.w = w
        self.h = h
        self.file = file
        self.logo = logo_polygons(w, h) if file == LOGO_FILE else None

        # Background
        self.bg = Polygon().rectangle(0 - w / 2, 0 - h / 2, w, h, (10, 10, 10, 10))

        # Outline
        self.ol = Polygon().rectangle(0 - w / 2, 0 - h / 2, w, h, (10, 10, 10, 10), stroke=2)

        # Transform
        self.t = Transform()

        self.angle = 0
        self.scale = 0
        self.x = 0
        self.y = 0

        self.color_fg = None
        self.color_bg = None
        self.color_ol = None

    def __lt__(self, icon):
        return self.scale < icon.scale

    def touched(self, touch):
        x, y, w, h = self.bounds()
        return touch.x > x and touch.x < x + w and touch.y > y and touch.y < y + h

    def update(self, move_angle):
        angle_per_icon = 2 * math.pi / Application.count
        self.angle = angle_per_icon * self.index + move_angle

        self.angle %= 2 * math.pi

        scale_factor = (math.cos(self.angle) + 1.0) / 2
        self.scale = max(self.minimum_scale, scale_factor) * self.maximum_scale

        # The lower the lower bounds here, the less saturated
        s = min(0.6, scale_factor + 0.1)

        self.hue = (angle_per_icon * self.index) / (2 * math.pi)
        self.color_fg = display.create_pen_hsv(self.hue, s, 0.2)
        self.color_ol = display.create_pen_hsv(self.hue, s, 1.0)
        self.color_bg = display.create_pen_hsv(self.hue, s, 0.9)

        self.y = RADIUS_Y * math.cos(self.angle)
        self.x = RADIUS_X * math.sin(self.angle)

        # Quick and dirty way to "perspective correct" the circle
        self.x *= self.scale

        # Logically these things below happen in reverse order
        # but because these are matrix operations we need to apply them back to front
        # THIS IS WEIRD BUT MATHS GON' MATHS
        self.t.reset()
        # Translate to our final display offset
        self.t.translate(OFFSET_X, OFFSET_Y)
        # Translate back to screen space, moving origin 0, 0 to our X and Y
        self.t.translate(CENTER_X + self.x, CENTER_Y + self.y)
        # Scale the icon around origin 0, 0
        self.t.scale(self.scale, self.scale)

    def draw(self, selected=False):
        vector.set_font_size(20)
        vector.set_transform(self.t)
        if self.logo:
            display.set_pen(LOGO_BG)
            vector.draw(self.bg)
            display.set_pen(LOGO_BAR)
            vector.draw(self.logo[0])
            display.set_pen(LOGO_CARD)
            vector.draw(self.logo[1])
            self.t.translate(0, 2)
        else:
            display.set_pen(self.color_bg)
            vector.draw(self.bg)
            display.set_pen(self.color_fg)
            self.t.translate(0, 2)
            vector.text(self.icon, 0, 0)

        if selected:
            self.t.translate(0, -2)
            display.set_pen(BLACK)
            vector.set_font_size(10)
            vector.text(self.name, -self.w, 40, max_width=self.w * 2)
            display.set_pen(LOGO_OUTLINE if self.logo else self.color_ol)
            vector.draw(self.ol)
            vector.set_font_size(8)
            desc_length = vector.measure_text(self.description, 18)
            vector.text(
                self.description,
                -int(desc_length[2]) // 2,
                50,
                max_width=int(desc_length[2]) + 5,
            )

        # Useful for debugging
        # display.rectangle(*self.bounds())

    def bounds(self):
        w = self.w * self.scale
        h = self.h * self.scale
        x = -w // 2
        y = -h // 2

        return (
            int(x + self.x + CENTER_X + OFFSET_X),
            int(y + self.y + CENTER_Y + OFFSET_Y),
            int(w),
            int(h),
        )

    def launch(self):
        with open("/ramfs/launch.txt", "w") as f:
            f.write(self.file)

        # Clear the display buffer before launching the next app
        display.set_pen(BLACK)
        display.clear()
        presto.update()

        # Reset!
        machine.reset()


# Story Map first, so it is at the front when the Presto starts; the rest alphabetically.
apps = [file for file in os.listdir() if file.endswith(".py") and file not in ("main.py", "secrets.py")]
apps.sort(key=lambda file: (file != LOGO_FILE, file))
icons = [Application(60, 60, file) for file in apps]

# Take a local reference to touch for a tiny performance boost
touch = presto.touch

while True:
    # We don't want any of the icon transforms to apply to our background
    vector.set_transform(t)

    # Clear screen to our background colour
    display.set_pen(BACKGROUND)
    display.clear()

    # Draw rounded corners in black
    display.set_pen(BLACK)
    vector.draw(rounded_corners)

    touch.poll()

    if touch.state and touch_start_time is None:
        touch_start_time = time.ticks_ms()
        touch_start_x = touch.x
        last_touch_x = touch.x
        tap = True

    elif touch.state:
        # Get the duration of the touch in milliseconds
        touch_ms = time.ticks_ms() - touch_start_time

        # Get the x distance between the touch start and current touch
        touch_dist = touch_start_x - touch.x

        # Calculate the touch speed, speed = distance / time
        touch_speed = touch_dist / touch_ms

        # Any movement should cancel our tap action
        if abs(touch_dist) > 4:
            tap = False

        # If a touch is under this minimal distance it counts as a "stop spinning, darn it"
        if abs(touch_dist) > 10:
            # Follow finger as it moves
            move = -math.radians(last_touch_x - touch.x) * 0.12
            last_touch_x = touch.x

            # Normal friction after touch ends ( the closer this is to 1 the longer it will take to slow down )
            friction = 0.8

        else:
            # Pick the one you like best
            # move = 0      # Stop abruptly
            friction = 0.7  # Apply a braking friction
    else:
        touch_start_time = None

    move_angle += move         # Apply the movement distance, this is in degrees and needs finagled to follow your finger
    move_angle %= 2 * math.pi  # Wrap at 360 degrees (in radians)
    move *= friction           # Apply friction, this will slowly decrease "move" when there's no touch, to slow the spin down

    # Pre-calculate the scales and angles for sorting.
    for icon in icons:
        icon.update(move_angle)

    # We have implemented the __lt__ magic method on Icons so we can just sort them
    # by visual size- the biggest icon is at the front!
    sorted_icons = sorted(icons)

    # Draw all but the front-most icon
    for icon in sorted_icons[:-1]:
        icon.draw()

    # Draw the front-most selected icons, True == selected
    front_most_icon = sorted_icons[-1]
    front_most_icon.draw(True)

    if tap and not touch.state:
        tap = False
        if front_most_icon.touched(touch):
            front_most_icon.launch()

        # Handle touches on all the inactive icons
        for icon in sorted_icons[:-1]:
            if icon.touched(touch):
                a = icon.angle
                friction = 0.5  # The lower this value, the faster the transition
                if a - math.pi > 0:  # Take the shortest route
                    a = 2 * math.pi - a
                    move = a * (1.0 - friction)
                else:
                    move = -a * (1.0 - friction)

    # Cycle the hue of the backlight LEDs to match the icon colours
    hue = 1.0 - (move_angle % (2 * math.pi)) / (2 * math.pi)
    for i in range(7):
        presto.set_led_hsv(i, hue, 1.0, 0.5)

    presto.update()
