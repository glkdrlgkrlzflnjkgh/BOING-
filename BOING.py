#!/usr/bin/env python3
"""
Pong game using pygame-ce with:
- main menu, settings, particle effects, delta-timing, impact effects
- rebindable controls (persisted in config.pickle)
- optional controller (gamepad/joystick) support
- debug overlay (F3)
- settings persisted to per-user config path via pickle

Controller support (optional):
- If one or more joysticks are connected they are initialized automatically.
- Default runtime mapping:
  - Joystick 0 axis 1 -> Left paddle (vertical). Up is negative axis value -> moves up.
  - Joystick 1 axis 1 -> Right paddle (vertical), if present.
  - If only one joystick and it has >=4 axes, joystick 0 axis 3 -> Right paddle (vertical).
  - D-pad / hat (if available) also controls paddles (hat y -1/0/1).
  - Buttons (generic mapping):
      button 0 -> Reset scores
      button 1 -> Return to menu
      button 2 -> Toggle debug overlay
  - Keyboard bindings (rebindable) still work and are persisted; controller input is additive (if controller active it will override paddle movement).
- This is intentionally lightweight plug-and-play controller support; if you want controller bindings persisted like keyboard rebinds I can add that next.
"""
import random
import sys, os
import math
import pickle
import time
import pygame as pg
from pygame import mixer as mix


def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)


# Initialize mixer safely (may raise if no audio device; we'll guard play calls by settings)
try:
    mix.init()
    # Attempt to load sounds; if missing, catch below
    try:
        SCORE_SND = mix.Sound(resource_path("DATA/wallhit.ogg"))
    except Exception:
        SCORE_SND = None
    try:
        HIT_SND = mix.Sound(resource_path("DATA/hit.ogg"))
    except Exception:
        HIT_SND = None
    try:
        MENU_MUSIC = mix.Sound(resource_path("DATA/menu.ogg"))
    except Exception:
        MENU_MUSIC = None
except Exception:
    SCORE_SND = None
    HIT_SND = None
    MENU_MUSIC = None

# --- Configuration ---
WIDTH, HEIGHT = 900, 600
FPS = 144

# Win condition: set to an integer to enable; e.g. SCORE_TO_WIN = 3
SCORE_TO_WIN = 5

# Speeds are pixels per second (delta-time friendly)
PADDLE_WIDTH, PADDLE_HEIGHT = 12, 100
PADDLE_SPEED = 360.0  # px/s

BALL_SIZE = 14
BALL_SPEED_START = 300.0  # px/s
BALL_SPEED_INCREMENT = 0.8
MAX_BALL_SPEED = 1200.0  # px/s cap

FONT_SIZE = 48
MENU_FONT_SIZE = 64

# Colors
BG = (10, 10, 10)
WHITE = (240, 240, 240)
DARK = (30, 30, 30)
ACCENT = (100, 200, 255)
HIGHLIGHT = (200, 230, 255)
PARTICLE_COLORS = [(255, 220, 120), (255, 120, 180), (120, 200, 255), (200, 255, 150)]

# Default settings (modifiable in Settings menu)
DEFAULT_SETTINGS = {
    "ai_difficulty": "Normal",     # Easy, Normal, Hard
    "particle_quality": "Normal",  # Low, Normal, High
    "sound": True,                 # enables SFX/music playback
    # controls: store pygame key constants (ints)
    "controls": {
        "left_up": pg.K_w,
        "left_down": pg.K_s,
        "right_up": pg.K_UP,
        "right_down": pg.K_DOWN,
        "reset": pg.K_r,
        "menu": pg.K_m,
        "debug": pg.K_F3,
    },
}

# Global debug toggle (F3)
DEBUG = False

# Global list of initialized joysticks (populated at runtime)
JOYSTICKS = []  # list of pg.joystick.Joystick instances


# Where to persist settings:
def get_config_path():
    user_profile = os.getenv("USERPROFILE") or os.path.expanduser("~")
    config_dir = os.path.join(user_profile, "AppData", "pong", "config")
    config_path = os.path.join(config_dir, "config.pickle")
    return config_dir, config_path


def ensure_config_dir():
    config_dir, _ = get_config_path()
    try:
        os.makedirs(config_dir, exist_ok=True)
    except Exception:
        fallback = os.path.join(os.path.expanduser("~"), ".pong")
        os.makedirs(fallback, exist_ok=True)
        return fallback
    return config_dir


def load_settings():
    _, config_path = get_config_path()
    try:
        if os.path.exists(config_path):
            with open(config_path, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                settings = DEFAULT_SETTINGS.copy()
                # merge controls dict properly
                controls = DEFAULT_SETTINGS["controls"].copy()
                if "controls" in data and isinstance(data["controls"], dict):
                    controls.update(data["controls"])
                settings.update(data)
                settings["controls"] = controls
                return settings
    except Exception as e:
        print(f"error loading settings: {e}")
        return DEFAULT_SETTINGS.copy()
    # return a deep copy so we don't mutate DEFAULT_SETTINGS
    s = DEFAULT_SETTINGS.copy()
    s["controls"] = DEFAULT_SETTINGS["controls"].copy()
    return s


def save_settings(settings):
    config_dir = ensure_config_dir()
    _, config_path = get_config_path()
    if not os.path.isdir(os.path.dirname(config_path)):
        config_path = os.path.join(config_dir, "config.pickle")
    try:
        with open(config_path, "wb") as f:
            pickle.dump(settings, f)
    except Exception as e:
        print(f"error writing settings: {e}")


# --- Joystick helpers ---
def init_joysticks():
    """
    Initialize available joysticks and populate JOYSTICKS list.
    Call after pg.init().
    """
    global JOYSTICKS
    JOYSTICKS = []
    try:
        pg.joystick.init()
        count = pg.joystick.get_count()
        for i in range(count):
            try:
                joy = pg.joystick.Joystick(i)
                joy.init()  # initialize once
                JOYSTICKS.append(joy)
                print(f"joystick initialized: {joy.get_name()}")
            except Exception as e:
                print(f"Initialization for a joystick FAILED! {e}")
    except Exception:
        JOYSTICKS = []


def joystick_info_summary():
    lines = []
    for j in JOYSTICKS:
        try:
            lines.append(f"#{j.get_id()} {j.get_name()} axes={j.get_numaxes()} buttons={j.get_numbuttons()} hats={j.get_numhats()}")
        except Exception:
            lines.append("<joystick?>")
    return lines


# --- Classes ---
class Paddle:
    def __init__(self, x, y):
        self.rect = pg.Rect(x, y, PADDLE_WIDTH, PADDLE_HEIGHT)
        self.y = float(self.rect.y)
        self.speed = 0.0  # px/s
        self.flash_timer = 0.0  # seconds

    def move(self, dt):
        self.y += self.speed * dt
        if self.y < 0:
            self.y = 0
        if self.y + self.rect.height > HEIGHT:
            self.y = HEIGHT - self.rect.height
        self.rect.y = int(self.y)
        if self.flash_timer > 0.0:
            self.flash_timer = max(0.0, self.flash_timer - dt)

    def draw(self, surf):
        if self.flash_timer > 0.0:
            frac = self.flash_timer / 0.12
            r = int(WHITE[0] * (1 - frac) + ACCENT[0] * frac)
            g = int(WHITE[1] * (1 - frac) + ACCENT[1] * frac)
            b = int(WHITE[2] * (1 - frac) + ACCENT[2] * frac)
            color = (r, g, b)
        else:
            color = WHITE
        pg.draw.rect(surf, color, self.rect, border_radius=6)


class Ball:
    def __init__(self):
        self.rect = pg.Rect(0, 0, BALL_SIZE, BALL_SIZE)
        self.x = float(WIDTH // 2)
        self.y = float(HEIGHT // 2)
        self.vx = 0.0
        self.vy = 0.0
        self.reset(direction=1)

    def reset(self, direction=None):
        self.x = float(WIDTH // 2)
        self.y = float(HEIGHT // 2)
        angle = random.uniform(-0.5, 0.5)
        speed = BALL_SPEED_START
        if direction is None:
            direction = random.choice([-1, 1])
        self.vx = direction * speed * (1 + abs(angle))
        self.vy = speed * angle * 2
        self.rect.center = (int(self.x), int(self.y))

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        half_h = self.rect.height / 2
        if self.y - half_h <= 0:
            self.y = half_h
            self.vy = -self.vy
        if self.y + half_h >= HEIGHT:
            self.y = HEIGHT - half_h
            self.vy = -self.vy
        self.rect.centerx = int(self.x)
        self.rect.centery = int(self.y)

    def draw(self, surf):
        pg.draw.ellipse(surf, ACCENT, self.rect)


class Particle:
    def __init__(self, pos, vel, life, size, color):
        self.x, self.y = float(pos[0]), float(pos[1])
        self.vx, self.vy = float(vel[0]), float(vel[1])
        self.life = float(life)  # seconds
        self.age = 0.0
        self.size = float(size)
        self.color = color

    def update(self, dt):
        self.age += dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 60.0 * dt
        self.vx *= (1.0 - 0.3 * dt)
        self.vy *= (1.0 - 0.1 * dt)

    def draw(self, surf):
        if self.age >= self.life:
            return
        alpha = max(0, int(255 * (1 - self.age / self.life)))
        s = pg.Surface((int(self.size * 2 + 2), int(self.size * 2 + 2)), pg.SRCALPHA)
        color = (*self.color, alpha)
        pg.draw.circle(s, color, (int(self.size) + 1, int(self.size) + 1), max(1, int(self.size)))
        surf.blit(s, (self.x - self.size - 1, self.y - self.size - 1))

    def is_dead(self):
        return self.age >= self.life


# --- Helper functions ---
def clamp(n, a, b):
    return max(a, min(b, n))


def key_name(k):
    if k is None:
        return "<unbound>"
    try:
        return pg.key.name(k)
    except Exception:
        return str(k)


def ai_move(paddle: Paddle, ball: Ball, settings):
    diff = settings.get("ai_difficulty", "Normal")
    if diff == "Easy":
        maxspeed = PADDLE_SPEED * 0.7
        deadzone = 14.0
    elif diff == "Hard":
        maxspeed = PADDLE_SPEED * 1.35
        deadzone = 4.0
    else:
        maxspeed = PADDLE_SPEED
        deadzone = 8.0

    if paddle.rect.centery < ball.rect.centery - deadzone:
        paddle.speed = maxspeed
    elif paddle.rect.centery > ball.rect.centery + deadzone:
        paddle.speed = -maxspeed
    else:
        paddle.speed = 0.0


def draw_center_line(surface):
    for y in range(0, HEIGHT, 30):
        pg.draw.rect(surface, DARK, (WIDTH // 2 - 2, y + 5, 4, 20))


def get_particle_count(settings, base):
    q = settings.get("particle_quality", "Normal")
    if q == "Low":
        return max(1, int(base * 0.5))
    if q == "High":
        return int(base * 1.8)
    return base


# --- UI helpers (popups, reset flow, credits) ---
def colored_confirm_popup(screen, clock, small_font, message, title="Confirm", title_color=(255, 200, 0), yes_text="Yes", no_text="No"):
    """
    Modal confirmation popup with a colored title. Returns True if user selects Yes, False otherwise.
    Navigation: Left/Right or A/D to select, Enter to confirm, Esc to cancel (No).
    Gamepad: button 0 = Yes, button 1 = No.
    """
    selected_yes = True
    w, h = 560, 160
    rect_x = (WIDTH - w) // 2
    rect_y = (HEIGHT - h) // 2

    while True:
        dt = clock.tick(FPS)
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                return False
            if ev.type == pg.KEYDOWN:
                if ev.key in (pg.K_LEFT, pg.K_a):
                    selected_yes = True
                elif ev.key in (pg.K_RIGHT, pg.K_d):
                    selected_yes = False
                elif ev.key in (pg.K_RETURN, pg.K_SPACE):
                    return selected_yes
                elif ev.key == pg.K_ESCAPE:
                    return False
            if ev.type == pg.JOYBUTTONDOWN:
                if ev.button == 0:
                    return True
                if ev.button == 1:
                    return False

        overlay = pg.Surface((w, h), pg.SRCALPHA)
        overlay.fill((12, 12, 12, 230))
        pg.draw.rect(overlay, title_color, (0, 0, w, 36))
        title_surf = small_font.render(title, True, DARK)
        overlay.blit(title_surf, (12, 6))

        # wrap message
        msg_lines = []
        words = message.split(" ")
        line = ""
        for word in words:
            if len(line) + len(word) + 1 > 60:
                msg_lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        if line:
            msg_lines.append(line)
        for i, ln in enumerate(msg_lines):
            msg_s = small_font.render(ln, True, WHITE)
            overlay.blit(msg_s, (16, 48 + i * 20))

        yes_col = ACCENT if selected_yes else WHITE
        no_col = ACCENT if not selected_yes else WHITE
        yes_surf = small_font.render(yes_text, True, yes_col)
        no_surf = small_font.render(no_text, True, no_col)
        overlay.blit(yes_surf, (w // 2 - 72 - yes_surf.get_width() // 2, h - 40))
        overlay.blit(no_surf, (w // 2 + 72 - no_surf.get_width() // 2, h - 40))

        dark = pg.Surface((WIDTH, HEIGHT), pg.SRCALPHA)
        dark.fill((0, 0, 0, 140))
        screen.blit(dark, (0, 0))
        screen.blit(overlay, (rect_x, rect_y))
        pg.display.flip()


def reset_settings_flow(screen, clock, small_font):
    """
    Two-layer reset confirmation: yellow then red. Returns True only if both confirmed.
    """
    yellow_title = "Warning"
    yellow_color = (220, 180, 40)
    yellow_msg = "This will reset all settings to their default values. Are you sure you want to continue?"
    ok1 = colored_confirm_popup(screen, clock, small_font, yellow_msg, title=yellow_title, title_color=yellow_color, yes_text="Continue", no_text="Cancel")
    if not ok1:
        return False

    red_title = "FINAL WARNING"
    red_color = (255, 0, 0)
    red_msg = "WE ARE ABOUT TO ERASE YOUR WHOLE SETTINGS FILE. ARE YOU SURE?"
    ok2 = colored_confirm_popup(screen, clock, small_font, red_msg, title=red_title, title_color=red_color, yes_text="Yes", no_text="No")
    return bool(ok2)


def credits_menu(screen, clock, title_font, menu_font, small_font):
    """
    Displays credits and attribution including music credit to Mindustry.
    Press any key or joystick button to return.
    """
    lines = [
        "CREDITS",
        "",
        "BOING!",
        "CREATED BY: XCALLUMNICX",
        "",
        "Music:",
        "- 'menu.ogg' used in this project is from Mindustry",
        "  (Anuke) — credit to Anuke and the Mindustry project.",
        "  Source / more info: https://anuke.itch.io/mindustry",
        "",
        "Libraries:",
        "- pygame-ce (pygame community edition)",
        "",
        "Special thanks:",
        "- Mindustry (for the menu music)",
        "",
        "Press any key or joystick button to return.",
    ]

    while True:
        dt = clock.tick(FPS)
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                return None
            if ev.type == pg.KEYDOWN or ev.type == pg.JOYBUTTONDOWN or ev.type == pg.MOUSEBUTTONDOWN:
                return None

        screen.fill(BG)
        # title
        title_surf = title_font.render("CREDITS", True, ACCENT)
        screen.blit(title_surf, (WIDTH // 2 - title_surf.get_width() // 2, 36))

        y = 120
        for i, ln in enumerate(lines[1:]):
            # use menu_font for main lines and small_font for smaller lines
            font_to_use = menu_font if i < 6 else small_font
            txt = font_to_use.render(ln, True, WHITE)
            screen.blit(txt, (WIDTH // 2 - txt.get_width() // 2, y))
            y += txt.get_height() + 6

        pg.display.flip()


# --- Menus and Rebinding UI ---
def main_menu(screen, clock, title_font, menu_font, small_font, settings):
    menu_items = ["1 Player", "2 Players", "Settings", "Quit"]
    selected = 0

    while True:
        dt_ms = clock.tick(FPS)
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_ESCAPE:
                    return None
                elif event.key in (pg.K_UP, pg.K_w):
                    selected = (selected - 1) % len(menu_items)
                elif event.key in (pg.K_DOWN, pg.K_s):
                    selected = (selected + 1) % len(menu_items)
                elif event.key in (pg.K_RETURN, pg.K_SPACE):
                    choice = menu_items[selected]
                    if choice == "1 Player":
                        return "1p"
                    elif choice == "2 Players":
                        return "2p"
                    elif choice == "Settings":
                        return "settings"
                    else:
                        return None

        screen.fill(BG)
        title_surf = title_font.render("BOING!", True, ACCENT)
        screen.blit(title_surf, (WIDTH // 2 - title_surf.get_width() // 2, 60))

        start_y = 200
        gap = 50
        for i, item in enumerate(menu_items):
            color = HIGHLIGHT if i == selected else WHITE
            surf = menu_font.render(item, True, color)
            screen.blit(surf, (WIDTH // 2 - surf.get_width() // 2, start_y + i * gap))

        help_surf = small_font.render("Use Up/Down and Enter to choose. Esc to quit.", True, DARK)
        screen.blit(help_surf, (WIDTH // 2 - help_surf.get_width() // 2, HEIGHT - 40))

        pg.display.flip()


def settings_menu(screen, clock, title_font, menu_font, small_font, settings):
    options = [
        ("AI Difficulty", ["Easy", "Normal", "Hard"]),
        ("Particles", ["Low", "Normal", "High"]),
        ("Controls", None),
        ("Sound", ["Off", "On"]),
        ("Credits", None),
        ("Reset Settings", None),
        ("Back", None),
    ]
    selected = 0

    while True:
        dt_ms = clock.tick(FPS)
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None
            elif event.type == pg.KEYDOWN:
                if event.key in (pg.K_ESCAPE, pg.K_RETURN) and selected == len(options) - 1:
                    save_settings(settings)
                    return settings
                elif event.key == pg.K_ESCAPE:
                    save_settings(settings)
                    return settings
                elif event.key in (pg.K_UP, pg.K_w):
                    selected = (selected - 1) % len(options)
                elif event.key in (pg.K_DOWN, pg.K_s):
                    selected = (selected + 1) % len(options)
                elif event.key in (pg.K_LEFT,):
                    label, vals = options[selected]
                    if vals:
                        cur = settings_get_label_value(settings, label)
                        i = vals.index(cur)
                        new = vals[(i - 1) % len(vals)]
                        settings_set_label_value(settings, label, new)
                        save_settings(settings)
                elif event.key in (pg.K_RIGHT,):
                    label, vals = options[selected]
                    if vals:
                        cur = settings_get_label_value(settings, label)
                        i = vals.index(cur)
                        new = vals[(i + 1) % len(vals)]
                        settings_set_label_value(settings, label, new)
                        save_settings(settings)
                elif event.key in (pg.K_SPACE, pg.K_RETURN):
                    label, vals = options[selected]
                    if label == "Back":
                        save_settings(settings)
                        return settings
                    if label == "Controls":
                        res = controls_rebind_menu(screen, clock, title_font, menu_font, small_font, settings)
                        if res is None:
                            return None
                        settings = res
                        continue
                    if label == "Credits":
                        credits_menu(screen, clock, title_font, menu_font, small_font)
                        continue
                    if label == "Reset Settings":
                        ok = reset_settings_flow(screen, clock, small_font)
                        if ok:
                            settings = DEFAULT_SETTINGS.copy()
                            settings["controls"] = DEFAULT_SETTINGS["controls"].copy()
                            save_settings(settings)
                        continue
                    if vals:
                        cur = settings_get_label_value(settings, label)
                        i = vals.index(cur)
                        new = vals[(i + 1) % len(vals)]
                        settings_set_label_value(settings, label, new)
                        save_settings(settings)

        screen.fill(BG)
        title_surf = title_font.render("SETTINGS", True, ACCENT)
        screen.blit(title_surf, (WIDTH // 2 - title_surf.get_width() // 2, 40))

        start_y = 170
        gap = 50
        for i, (label, vals) in enumerate(options):
            y = start_y + i * gap
            sel = (i == selected)
            color = HIGHLIGHT if sel else WHITE
            label_surf = menu_font.render(label, True, color)
            screen.blit(label_surf, (WIDTH // 2 - 260, y))
            if vals:
                cur = settings_get_label_value(settings, label)
                val_surf = menu_font.render(cur, True, WHITE if not sel else ACCENT)
                screen.blit(val_surf, (WIDTH // 2 + 40, y))

        hint = small_font.render("Use Left/Right to change values. Enter on Controls to rebind.", True, DARK)
        screen.blit(hint, (WIDTH // 2 - hint.get_width() // 2, HEIGHT - 40))

        pg.display.flip()


def settings_get_label_value(settings, label):
    if label == "AI Difficulty":
        return settings.get("ai_difficulty", "Normal")
    if label == "Particles":
        return settings.get("particle_quality", "Normal")
    if label == "Sound":
        return "On" if settings.get("sound", True) else "Off"
    return ""


def settings_set_label_value(settings, label, value):
    if label == "AI Difficulty":
        settings["ai_difficulty"] = value
    elif label == "Particles":
        settings["particle_quality"] = value
    elif label == "Sound":
        settings["sound"] = (value == "On")


# controls_rebind_menu remains unchanged except small "[UNBOUND]" label
def controls_rebind_menu(screen, clock, title_font, menu_font, small_font, settings):
    actions = [
        ("Left Up", "left_up"),
        ("Left Down", "left_down"),
        ("Right Up", "right_up"),
        ("Right Down", "right_down"),
        ("Reset", "reset"),
        ("Menu", "menu"),
        ("Debug", "debug"),
        ("Reset to Defaults", "__reset_defaults__"),
        ("Apply", "__apply__"),
        ("Back", "__back__"),
    ]
    selected = 0
    awaiting_key = False
    awaiting_action_key = None

    bindings = settings.get("controls", {}).copy()

    while True:
        dt_ms = clock.tick(FPS)
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None

            if awaiting_key:
                if event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        awaiting_key = False
                        awaiting_action_key = None
                    elif event.key in (pg.K_BACKSPACE, pg.K_DELETE):
                        bindings[awaiting_action_key] = None
                        save_settings(settings)
                        awaiting_key = False
                        awaiting_action_key = None
                    else:
                        bindings[awaiting_action_key] = event.key
                        save_settings(settings)
                        awaiting_key = False
                        awaiting_action_key = None
                continue

            if event.type == pg.KEYDOWN:
                if event.key in (pg.K_UP, pg.K_w):
                    selected = (selected - 1) % len(actions)
                elif event.key in (pg.K_DOWN, pg.K_s):
                    selected = (selected + 1) % len(actions)
                elif event.key in (pg.K_RETURN, pg.K_SPACE):
                    label, key = actions[selected]
                    if key == "__back__":
                        return settings
                    if key == "__reset_defaults__":
                        bindings = DEFAULT_SETTINGS["controls"].copy()
                    elif key == "__apply__":
                        all_unbound = True
                        for k, v in bindings.items():
                            if v:
                                all_unbound = False
                                break
                        if all_unbound:
                            msg = "WARNING: you have unbound all keys! are you sure you want to apply this change?"
                            confirm = colored_confirm_popup(screen, clock, small_font, msg, title="WARNING", title_color=(220, 180, 40), yes_text="Apply", no_text="Cancel")
                            if not confirm:
                                continue
                        settings["controls"] = bindings.copy()
                        save_settings(settings)
                        return settings
                    else:
                        awaiting_key = True
                        awaiting_action_key = key
                elif event.key == pg.K_ESCAPE:
                    return settings
                elif event.key in (pg.K_BACKSPACE, pg.K_DELETE):
                    label, key = actions[selected]
                    if key not in ("__back__", "__reset_defaults__", "__apply__"):
                        bindings[key] = None

        screen.fill(BG)
        title_surf = title_font.render("REMAP CONTROLS", True, ACCENT)
        screen.blit(title_surf, (WIDTH // 2 - title_surf.get_width() // 2, 36))

        start_y = 140
        gap = 40
        for i, (label, key) in enumerate(actions):
            y = start_y + i * gap
            sel = (i == selected)
            color = HIGHLIGHT if sel else WHITE
            label_surf = menu_font.render(label, True, color)
            screen.blit(label_surf, (120, y))

            if key in ("__back__", "__reset_defaults__", "__apply__"):
                val_text = ""
            else:
                bound = bindings.get(key)
                val_text = "[UNBOUND]" if not bound else key_name(bound)
            val_surf = menu_font.render(val_text, True, ACCENT if sel else WHITE)
            screen.blit(val_surf, (WIDTH - val_surf.get_width() - 120, y))

        hint = small_font.render("Enter to rebind | Backspace to clear | Apply to commit | Esc to cancel", True, DARK)
        screen.blit(hint, (WIDTH // 2 - hint.get_width() // 2, HEIGHT - 40))

        if awaiting_key and awaiting_action_key:
            prompt = small_font.render(f"Press a key to bind for '{awaiting_action_key}' (Esc to cancel)", True, ACCENT)
            screen.blit(prompt, (WIDTH // 2 - prompt.get_width() // 2, HEIGHT - 80))

        pg.display.flip()


# helper: show winner screen and wait a short time or until keypress
def show_winner_and_wait(screen, clock, font, left, right, ball, winner_text, wait_ms=1800):
    start = time.perf_counter()
    while True:
        screen.fill(BG)
        draw_center_line(screen)
        left.draw(screen)
        right.draw(screen)
        ball.draw(screen)
        win_surf = font.render(winner_text, True, ACCENT)
        screen.blit(win_surf, (WIDTH // 2 - win_surf.get_width() // 2, HEIGHT // 2 - win_surf.get_height() // 2))
        pg.display.flip()

        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                return True
            if ev.type == pg.KEYDOWN or ev.type == pg.JOYBUTTONDOWN or ev.type == pg.MOUSEBUTTONDOWN:
                return False
        elapsed = (time.perf_counter() - start) * 1000.0
        if elapsed >= wait_ms:
            return False
        clock.tick(60)


# --- Main game ---
def run_game(screen, clock, font, small_font, mode, settings):
    global DEBUG, JOYSTICKS

    left = Paddle(20, HEIGHT // 2 - PADDLE_HEIGHT // 2)
    right = Paddle(WIDTH - 20 - PADDLE_WIDTH, HEIGHT // 2 - PADDLE_HEIGHT // 2)
    ball = Ball()

    score_left = 0
    score_right = 0
    right_ai = True if mode == "1p" else False
    paused = False

    particles = []
    trail = []

    pq = settings.get("particle_quality", "Normal")
    if pq == "Low":
        TRAIL_LIFE_SEC = 0.10
    elif pq == "High":
        TRAIL_LIFE_SEC = 0.45
    else:
        TRAIL_LIFE_SEC = 0.25

    frame_times = []
    update_times = []
    draw_times = []
    MAX_SAMPLES = 200

    shake_timer = 0.0
    shake_magnitude = 0.0

    AXIS_DEADZONE = 0.20

    running = True
    if MENU_MUSIC and settings.get("sound", False):
        try:
            MENU_MUSIC.stop()
        except Exception:
            pass
    while running:
        dt = clock.tick(FPS) / 1000.0
        frame_start = time.perf_counter()

        if JOYSTICKS:
            try:
                j0 = JOYSTICKS[0]
                if j0.get_numaxes() > 1:
                    v = j0.get_axis(1)
                    left_axis_value = 0.0 if abs(v) < AXIS_DEADZONE else v
                    left.speed = left_axis_value * PADDLE_SPEED
                if j0.get_numhats() > 0:
                    hat_y = j0.get_hat(0)[1]
                    if hat_y != 0:
                        left.speed = -hat_y * PADDLE_SPEED
            except Exception:
                pass

            try:
                if len(JOYSTICKS) >= 2:
                    j1 = JOYSTICKS[1]
                    if j1.get_numaxes() > 1:
                        v = j1.get_axis(1)
                        right_axis_value = 0.0 if abs(v) < AXIS_DEADZONE else v
                        right.speed = right_axis_value * PADDLE_SPEED
                    if j1.get_numhats() > 0:
                        hat_y = j1.get_hat(0)[1]
                        if hat_y != 0:
                            right.speed = -hat_y * PADDLE_SPEED
                else:
                    j0 = JOYSTICKS[0]
                    if j0.get_numaxes() > 3:
                        v = j0.get_axis(3)
                        right_axis_value = 0.0 if abs(v) < AXIS_DEADZONE else v
                        right.speed = right_axis_value * PADDLE_SPEED
            except Exception:
                pass

        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None

            if event.type == pg.JOYBUTTONDOWN:
                b = event.button
                if b == 0:
                    score_left = 0
                    score_right = 0
                    ball.reset()
                    trail.clear()
                elif b == 1:
                    return "menu"
                elif b == 2:
                    DEBUG = not DEBUG
                continue

            if event.type == pg.JOYHATMOTION:
                hat_x, hat_y = event.value
                jid = getattr(event, "joy", None)
                if jid == 0:
                    left.speed = -hat_y * PADDLE_SPEED
                elif jid == 1:
                    right.speed = -hat_y * PADDLE_SPEED
                continue

            if event.type == pg.KEYDOWN:
                controls = settings.get("controls", {})
                if event.key == controls.get("left_up"):
                    left.speed = -PADDLE_SPEED
                elif event.key == controls.get("left_down"):
                    left.speed = PADDLE_SPEED
                elif event.key == controls.get("right_up") and not right_ai:
                    right.speed = -PADDLE_SPEED
                elif event.key == controls.get("right_down") and not right_ai:
                    right.speed = PADDLE_SPEED
                elif event.key == controls.get("reset"):
                    score_left = 0
                    score_right = 0
                    ball.reset()
                    trail.clear()
                elif event.key == controls.get("menu"):
                    return "menu"
                elif event.key == controls.get("debug"):
                    DEBUG = not DEBUG
                elif event.key == pg.K_ESCAPE:
                    return None
            elif event.type == pg.KEYUP:
                controls = settings.get("controls", {})
                if event.key == controls.get("left_up") and left.speed < 0:
                    left.speed = 0.0
                if event.key == controls.get("left_down") and left.speed > 0:
                    left.speed = 0.0
                if event.key == controls.get("right_up") and right.speed < 0:
                    right.speed = 0.0
                if event.key == controls.get("right_down") and right.speed > 0:
                    right.speed = 0.0

        t0 = time.perf_counter()

        if right_ai:
            ai_move(right, ball, settings)

        left.move(dt)
        right.move(dt)

        if not paused:
            ball.update(dt)

        trail.insert(0, (ball.x, ball.y, 0.0))
        new_trail = []
        for x, y, age in trail:
            age += dt
            if age < TRAIL_LIFE_SEC:
                new_trail.append((x, y, age))
        trail = new_trail[:int(TRAIL_LIFE_SEC / max(dt, 1e-6) + 1)]

        if ball.rect.colliderect(left.rect):
            if settings.get("sound", False) and HIT_SND:
                try:
                    HIT_SND.play()
                except Exception:
                    pass
            ball.x = left.rect.right + ball.rect.width / 2.0
            ball.vx = abs(ball.vx)
            offset = (ball.rect.centery - left.rect.centery) / (left.rect.height / 2)
            ball.vy += offset * 150.0
            speed = math.hypot(ball.vx, ball.vy)
            if speed < MAX_BALL_SPEED:
                scale = 1.0 + BALL_SPEED_INCREMENT / 10.0
                ball.vx *= scale
                ball.vy *= scale
            impact_strength = clamp(speed / BALL_SPEED_START, 0.8, 3.0)
            emit_particles(particles, (ball.rect.left, ball.rect.centery), direction=1, settings=settings, intensity=impact_strength)
            left.flash_timer = 0.12
            shake_timer = max(shake_timer, 0.12)
            shake_magnitude = max(shake_magnitude, min(18.0, 6.0 * impact_strength))

        if ball.rect.colliderect(right.rect):
            if settings.get("sound", False) and HIT_SND:
                try:
                    HIT_SND.play()
                except Exception:
                    pass
            ball.x = right.rect.left - ball.rect.width / 2.0
            ball.vx = -abs(ball.vx)
            offset = (ball.rect.centery - right.rect.centery) / (right.rect.height / 2)
            ball.vy += offset * 150.0
            speed = math.hypot(ball.vx, ball.vy)
            if speed < MAX_BALL_SPEED:
                scale = 1.0 + BALL_SPEED_INCREMENT / 10.0
                ball.vx *= scale
                ball.vy *= scale
            impact_strength = clamp(speed / BALL_SPEED_START, 0.8, 3.0)
            emit_particles(particles, (ball.rect.right, ball.rect.centery), direction=-1, settings=settings, intensity=impact_strength)
            right.flash_timer = 0.12
            shake_timer = max(shake_timer, 0.12)
            shake_magnitude = max(shake_magnitude, min(18.0, 6.0 * impact_strength))

        scorer = None
        if ball.rect.right < 0:
            score_right += 1
            scorer = 1
        elif ball.rect.left > WIDTH:
            score_left += 1
            scorer = -1

        if scorer is not None:
            if settings.get("sound", False) and SCORE_SND:
                try:
                    SCORE_SND.play()
                except Exception:
                    pass
            emit_score_burst(particles, (WIDTH // 2, HEIGHT // 2), settings=settings)
            serve_dir = -scorer
            ball.reset(direction=serve_dir)
            trail.clear()
            shake_timer = max(shake_timer, 0.22)
            shake_magnitude = max(shake_magnitude, 22.0)

            if SCORE_TO_WIN and SCORE_TO_WIN > 0:
                if score_left >= SCORE_TO_WIN or score_right >= SCORE_TO_WIN:
                    winner = "Left" if score_left >= SCORE_TO_WIN else "Right"
                    winner_text = f"{winner} wins!"
                    quit_requested = show_winner_and_wait(screen, clock, font, left, right, ball, winner_text, wait_ms=1800)
                    if quit_requested:
                        return None
                    score_left = 0
                    score_right = 0
                    ball.reset()
                    trail.clear()
                    paused = False

        t1 = time.perf_counter()
        update_duration = (t1 - t0) * 1000.0

        for p in particles:
            p.update(dt)
        particles = [p for p in particles if not p.is_dead()]

        if shake_timer > 0.0:
            shake_timer = max(0.0, shake_timer - dt)
            if shake_timer <= 0.0:
                shake_magnitude = 0.0

        t_draw_start = time.perf_counter()

        game_surf = pg.Surface((WIDTH, HEIGHT), pg.SRCALPHA)
        game_surf.fill(BG)
        draw_center_line(game_surf)

        for x, y, age in reversed(trail):
            frac = 1.0 - (age / max(1e-6, TRAIL_LIFE_SEC))
            size = 6 * (0.4 + 0.6 * frac)
            alpha = int(200 * frac)
            s = pg.Surface((int(size * 2 + 2), int(size * 2 + 2)), pg.SRCALPHA)
            pg.draw.circle(s, (ACCENT[0], ACCENT[1], ACCENT[2], alpha),
                           (int(size) + 1, int(size) + 1), int(size))
            game_surf.blit(s, (x - size - 1, y - size - 1))

        left.draw(game_surf)
        right.draw(game_surf)
        ball.draw(game_surf)

        for p in particles:
            p.draw(game_surf)

        left_surf = font.render(str(score_left), True, WHITE)
        right_surf = font.render(str(score_right), True, WHITE)
        game_surf.blit(left_surf, (WIDTH // 4 - left_surf.get_width() // 2, 20))
        game_surf.blit(right_surf, (WIDTH * 3 // 4 - right_surf.get_width() // 2, 20))

        if shake_timer > 0.0 and shake_magnitude > 0.0:
            mag = shake_magnitude * (shake_timer / 0.12 if shake_timer < 0.12 else 1.0)
            ox = int(random.uniform(-mag, mag))
            oy = int(random.uniform(-mag, mag))
        else:
            ox = 0
            oy = 0

        screen.fill(BG)
        screen.blit(game_surf, (ox, oy))

        mode_text = "1P (vs AI)" if mode == "1p" else "2P (Local)"
        help_surf = small_font.render(f"{mode_text}  |  Rebind Controls in Settings  |  R: reset  |  M: menu  |  F3: debug", True, DARK)
        screen.blit(help_surf, (20, HEIGHT - 28))

        t_draw_end = time.perf_counter()
        draw_duration = (t_draw_end - t_draw_start) * 1000.0

        frame_duration = (time.perf_counter() - frame_start) * 1000.0
        frame_times.append(frame_duration)
        update_times.append(update_duration)
        draw_times.append(draw_duration)
        if len(frame_times) > MAX_SAMPLES:
            frame_times.pop(0)
            update_times.pop(0)
            draw_times.pop(0)

        if DEBUG:
            dbg_w, dbg_h = 420, 180
            dbg_surf = pg.Surface((dbg_w, dbg_h), pg.SRCALPHA)
            dbg_surf.fill((8, 8, 8, 200))
            screen.blit(dbg_surf, (WIDTH - dbg_w - 12, 12))

            fps = clock.get_fps()
            avg_frame = sum(frame_times) / max(1, len(frame_times))
            avg_update = sum(update_times) / max(1, len(update_times))
            avg_draw = sum(draw_times) / max(1, len(draw_times))
            stats = [
                f"FPS: {fps:.1f}",
                f"Frame: {avg_frame:.2f} ms",
                f"Update: {avg_update:.2f} ms",
                f"Draw: {avg_draw:.2f} ms",
                f"Particles: {len(particles)}",
                f"TrailLen: {len(trail)}",
                f"Shake: {shake_magnitude:.1f}px / {shake_timer:.2f}s",
            ]
            for i, line in enumerate(stats):
                txt = small_font.render(line, True, ACCENT if i == 0 else WHITE)
                screen.blit(txt, (WIDTH - dbg_w + 8, 16 + i * 18))

            y0 = 16 + len(stats) * 18 + 6
            jinfo = joystick_info_summary()
            for i, jline in enumerate(jinfo):
                txt = small_font.render(jline, True, WHITE)
                screen.blit(txt, (WIDTH - dbg_w + 8, y0 + i * 16))

        pg.display.flip()

    return None


# --- Particle emission helpers ---
def emit_particles(particles_list, pos, direction, settings, intensity=1.0):
    base = 12
    count = int(get_particle_count(settings, base) * intensity)
    count = max(2, min(200, count))
    for _ in range(count):
        speed = random.uniform(80.0, 360.0) * (0.7 + random.random() * 0.8) * intensity
        angle = random.uniform(-0.9, 0.9) + (0 if direction == 1 else math.pi)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        life = random.uniform(0.18, 0.7)
        size = random.uniform(2.0, 6.0) * (0.6 + 0.8 * min(1.0, intensity))
        color = random.choice(PARTICLE_COLORS)
        particles_list.append(Particle(pos, (vx, vy), life, size, color))


def emit_score_burst(particles_list, pos, settings):
    base = 48
    count = get_particle_count(settings, base)
    for _ in range(count):
        speed = random.uniform(120.0, 420.0)
        angle = random.uniform(0, math.tau)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        life = random.uniform(0.5, 1.1)
        size = random.uniform(3.0, 6.0)
        color = random.choice(PARTICLE_COLORS)
        particles_list.append(Particle(pos, (vx, vy), life, size, color))


# --- Entry point ---
def main():
    pg.init()
    init_joysticks()

    pg.display.set_caption("BOING! V1.0")
    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()

    global title_font, font, small_font
    title_font = pg.font.SysFont(None, MENU_FONT_SIZE)
    font = pg.font.SysFont(None, FONT_SIZE)
    small_font = pg.font.SysFont(None, 24)

    settings = load_settings()
    if settings.get("sound", False) and MENU_MUSIC:
        try:
            MENU_MUSIC.play(loops=-1)
        except Exception:
            pass

    while True:
        if settings.get("sound", False) and MENU_MUSIC:
            try:
                MENU_MUSIC.stop()
                MENU_MUSIC.play(loops=-1)
            except Exception:
                pass

        mode = main_menu(screen, clock, title_font, font, small_font, settings)
        if mode is None:
            break
        if mode == "settings":
            new_settings = settings_menu(screen, clock, title_font, font, small_font, settings)
            if new_settings is None:
                break
            settings = new_settings
            continue
        result = run_game(screen, clock, font, small_font, mode, settings)
        if result is None:
            break

    save_settings(settings)
    print("quitting...")
    pg.quit()
    print("quitted pygame...")
    sys.exit()


if __name__ == "__main__":
    main()