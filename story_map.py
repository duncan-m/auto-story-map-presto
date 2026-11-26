# ICON description
# NAME Story Map
# DESC Next stories to build

# Next available stories from Auto Story Map, on a Pimoroni Presto.
#
# Fetches FEED_URL (served by feed.mjs on your computer) and shows the planned
# stories whose dependencies have all landed, in release order.
#
# List:    drag up and down to scroll, tap a story to open it, tap the header to refresh.
# Detail:  drag up and down to scroll, swipe left or right for the next or
#          previous story, tap the header to go back, and tap Build with Claude
#          to start a Claude Code session for the story on your computer. It
#          asks its questions in the Claude app before it plans or builds.

import time
import gc
import json
from presto import Presto
import secrets

try:
    import requests
except ImportError:
    import urequests as requests

FEED_URL = secrets.FEED_URL
REFRESH_SECONDS = getattr(secrets, "REFRESH_SECONDS", 300)
BUILD_TOKEN = getattr(secrets, "BUILD_TOKEN", None)
BUILD_URL = FEED_URL.rsplit("/", 1)[0] + "/build/"

presto = Presto(full_res=True)
display = presto.display
touch = presto.touch
WIDTH, HEIGHT = display.get_bounds()
display.set_font("bitmap8")

# Colours
BG = display.create_pen(16, 18, 24)
HEADER = display.create_pen(34, 38, 52)
CARD = display.create_pen(28, 31, 42)
CARD_DOWN = display.create_pen(44, 50, 68)
TEXT = display.create_pen(232, 234, 240)
MUTED = display.create_pen(140, 146, 160)
ACCENT = display.create_pen(110, 200, 140)
WARN = display.create_pen(240, 180, 80)
PILL = display.create_pen(60, 66, 88)
BAR = display.create_pen(80, 86, 108)
BUTTON = display.create_pen(70, 150, 100)
BUTTON_TEXT = display.create_pen(10, 20, 14)
BUTTON_OFF = display.create_pen(40, 44, 58)
BLUE = display.create_pen(90, 150, 240)
AMBER = display.create_pen(240, 180, 80)
MERGED_BG = display.create_pen(26, 78, 48)
MERGED_TEXT = display.create_pen(200, 255, 215)

MARGIN = 14
HEADER_H = 64
CARD_H = 70
GAP = 6
ROW = CARD_H + GAP
BODY_H = HEIGHT - HEADER_H
TEXT_W = WIDTH - 2 * MARGIN - 8
HEAD_H = 30
ACTIVE_REFRESH_SECONDS = 60   # while a build is running, check more often for its merge
NOTIFIED_PATH = "story_map_notified.txt"
CACHE_PATH = "story_map_cache.json"   # last feed, shown at once while Wi-Fi connects
BTN_H = 64
DETAIL_H = BODY_H - BTN_H

DRAG_START = 12   # pixels a touch must move before it counts as a drag
SWIPE_MIN = 70    # horizontal pixels for a swipe between stories

feed = None
error = None
view = "list"          # "list", "detail", "confirm" or "result"
result_title = ""
result_message = ""
result_ok = False
list_scroll = 0
detail_index = 0
detail_source = "ready"   # which list the open story belongs to: "ready" or "builds"
detail_scroll = 0
detail_lines = []      # (text, pen, scale, line height)
pressed_card = -1
last_fetch = -REFRESH_SECONDS * 1000
notices = []           # merged builds waiting to be announced
notice_return = "list"


def ascii(text):
    # bitmap8 only has ASCII glyphs.
    if not text:
        return ""
    for a, b in (("…", "..."), ("–", "-"), ("—", "-"), ("‘", "'"),
                 ("’", "'"), ("“", '"'), ("”", '"'), ("→", "->")):
        text = text.replace(a, b)
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in text)


def wrap(text, width, scale, max_lines=0):
    lines, line = [], ""
    for word in ascii(text).split():
        trial = word if not line else line + " " + word
        if not line or display.measure_text(trial, scale) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if not lines:
        return [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and display.measure_text(last + "...", scale) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


def leds(r, g, b):
    try:
        for i in range(7):
            presto.set_led_rgb(i, r, g, b)
    except Exception:
        pass


def stories():
    return feed["ready"] if feed else []


def builds():
    return (feed.get("builds") or []) if feed else []


def detail_items():
    return builds() if detail_source == "builds" else stories()


def build_status(item):
    return (item.get("build") or {}).get("status")


def load_cache():
    global feed
    try:
        with open(CACHE_PATH) as f:
            feed = json.loads(f.read())
    except Exception:
        feed = None


def save_cache(text):
    try:
        with open(CACHE_PATH, "w") as f:
            f.write(text)
    except OSError:
        pass


# ---------- Data ----------

def fetch():
    global feed, error, last_fetch, view
    last_fetch = time.ticks_ms()
    items = detail_items()
    current = items[detail_index]["id"] if view != "list" and detail_index < len(items) else None
    try:
        response = requests.get(FEED_URL)
        text = response.text
        response.close()
        data = json.loads(text)
        if "error" in data:
            raise ValueError(data["error"])
        feed = data
        save_cache(text)
        error = None
        leds(0, 40, 10) if feed["counts"]["ready"] else leds(0, 0, 30)
    except Exception as e:
        error = "Can't reach feed: " + str(e)
        leds(50, 25, 0)
    gc.collect()

    # Keep the open story open if it is still in the list.
    if current:
        ids = [s["id"] for s in detail_items()]
        if current in ids:
            kept = view
            open_detail(ids.index(current), keep_scroll=True)
            view = kept
        else:
            view = "list"
    check_merges()
    clamp_list()


# ---------- Merge alerts ----------

def load_notified():
    try:
        with open(NOTIFIED_PATH) as f:
            return set(f.read().split())
    except OSError:
        return set()


notified = load_notified()


def check_merges():
    global view, notice_return
    if not feed:
        return
    fresh = [b for b in builds() if build_status(b) == "merged" and b["id"] not in notified]
    if not fresh:
        return
    for b in fresh:
        notified.add(b["id"])
        notices.append(b)
    try:
        with open(NOTIFIED_PATH, "w") as f:
            f.write("\n".join(sorted(notified)))
    except OSError:
        pass
    if view != "notice":
        notice_return = view if view in ("list", "detail") else "list"
        view = "notice"
        draw()
    chime()


def chime():
    try:
        from presto import Buzzer
        buzzer = Buzzer(43)
    except Exception:
        buzzer = None
    for i, (tone, ms) in enumerate(((784, 110), (988, 110), (1319, 260))):
        leds(0, 120, 40) if i % 2 == 0 else leds(0, 20, 5)
        if buzzer:
            buzzer.set_tone(tone)
        time.sleep_ms(ms)
    if buzzer:
        buzzer.set_tone(-1)
    leds(0, 80, 25)


def draw_notice():
    b = notices[0]
    display.set_pen(MERGED_BG)
    display.clear()
    display.set_pen(MERGED_TEXT)
    label = "MERGED"
    display.text(label, (WIDTH - display.measure_text(label, 6)) // 2, 70, WIDTH, 6)
    w = display.measure_text(b["id"], 4)
    display.set_pen(TEXT)
    display.text(b["id"], (WIDTH - w) // 2, 150, WIDTH, 4)
    y = 205
    for line in wrap(b["title"], WIDTH - 2 * MARGIN, 2, 4):
        display.text(line, (WIDTH - display.measure_text(line, 2)) // 2, y, WIDTH, 2)
        y += 24
    build = b.get("build") or {}
    if build.get("pr"):
        pr = "PR #" + str(build["pr"])
        display.set_pen(MERGED_TEXT)
        display.text(pr, (WIDTH - display.measure_text(pr, 2)) // 2, y + 14, WIDTH, 2)
        y += 24
    cost = cost_label(build)
    if cost:
        tokens = tokens_label(build)
        line = cost + ("  -  " + tokens if tokens else "")
        display.set_pen(TEXT)
        display.text(line, (WIDTH - display.measure_text(line, 3)) // 2, y + 22, WIDTH, 3)
    more = "Tap to dismiss" + ("  ({} more)".format(len(notices) - 1) if len(notices) > 1 else "")
    display.set_pen(MERGED_TEXT)
    display.text(more, (WIDTH - display.measure_text(more, 2)) // 2, HEIGHT - 50, WIDTH, 2)


# ---------- List view ----------
# The list is a run of rows: a "Your builds" group when there are any, then the
# stories ready to build. Each row is (kind, data, height).

def list_rows():
    rows = []
    if builds():
        rows.append(("head", "YOUR BUILDS", HEAD_H))
        for i in range(len(builds())):
            rows.append(("build", i, ROW))
        rows.append(("head", "READY TO BUILD", HEAD_H))
    for i, s in enumerate(stories()):
        if not build_of(s):   # already shown under Your builds
            rows.append(("story", i, ROW))
    return rows


def list_content_height():
    return GAP + sum(r[2] for r in list_rows()) + 70


def clamp_list():
    global list_scroll
    list_scroll = max(0, min(list_scroll, list_content_height() - BODY_H))


def draw_card(r, story, y):
    display.set_pen(CARD_DOWN if r == pressed_card else CARD)
    display.rectangle(MARGIN - 4, y, WIDTH - 2 * (MARGIN - 4), CARD_H)
    display.set_pen(ACCENT)
    display.text(story["id"], MARGIN + 4, y + 8, WIDTH, 2)

    x = WIDTH - MARGIN
    est = story.get("estimate")
    if est:
        x = pill(str(est) + " pts", x, y + 5, PILL, TEXT)
    build = build_of(story)
    if build:
        label, bg, fg = build_look(build)
        pill(label, x - 6, y + 5, bg, fg)

    display.set_pen(TEXT)
    for n, line in enumerate(wrap(story["title"], TEXT_W, 2, 2)):
        display.text(line, MARGIN + 4, y + 30 + n * 19, WIDTH, 2)


def build_look(build):
    # (label, pill colour, pill text colour) for a build's state.
    status = build.get("status")
    if status == "merged":
        return "Merged", ACCENT, BUTTON_TEXT
    if status == "pr":
        return "PR #" + str(build["pr"]), AMBER, BUTTON_TEXT
    if status == "closed":
        return "Closed", PILL, MUTED
    return "Building", BLUE, BUTTON_TEXT


def cost_label(b):
    cost = b.get("costUsd")
    return "${:.2f}".format(cost) if cost is not None else None


def tokens_label(b):
    n = b.get("tokens")
    if n is None:
        return None
    if n >= 1000000:
        return "{:.1f}M tokens".format(n / 1000000)
    if n >= 1000:
        return "{:.0f}k tokens".format(n / 1000)
    return str(n) + " tokens"


def draw_build_card(r, b, y):
    build = b.get("build") or {}
    merged = build.get("status") == "merged"
    label, stripe, fg = build_look(build)
    display.set_pen(CARD_DOWN if r == pressed_card else (MERGED_BG if merged else CARD))
    display.rectangle(MARGIN - 4, y, WIDTH - 2 * (MARGIN - 4), CARD_H)
    display.set_pen(stripe)
    display.rectangle(MARGIN - 4, y, 5, CARD_H)
    display.set_pen(MERGED_TEXT if merged else ACCENT)
    display.text(b["id"], MARGIN + 8, y + 8, WIDTH, 2)
    x = pill(label, WIDTH - MARGIN, y + 5, stripe, fg)
    cost = cost_label(build)
    if merged and cost:
        pill(cost, x - 6, y + 5, PILL, MERGED_TEXT)
    display.set_pen(MERGED_TEXT if merged else TEXT)
    for n, line in enumerate(wrap(b["title"], TEXT_W - 4, 2, 2)):
        display.text(line, MARGIN + 8, y + 30 + n * 19, WIDTH, 2)


def draw_list():
    rows = list_rows()
    y = HEADER_H + GAP - list_scroll
    for r, (kind, data, h) in enumerate(rows):
        if y > HEIGHT:
            break
        if y + h >= HEADER_H:
            if kind == "head":
                display.set_pen(MUTED)
                display.text(data, MARGIN, y + 8, WIDTH, 2)
            elif kind == "build":
                draw_build_card(r, builds()[data], y)
            else:
                draw_card(r, stories()[data], y)
        y += h

    # Below the last card: what else is going on.
    y = HEADER_H + GAP - list_scroll + sum(r[2] for r in rows) + 8
    display.set_pen(MUTED)
    if not stories():
        display.text("No planned story has all its", MARGIN, y + 10, WIDTH, 2)
        display.text("dependencies landed yet.", MARGIN, y + 32, WIDTH, 2)
        y += 60
    display.text(str(feed["counts"]["waiting"]) + " more waiting on other stories", MARGIN, y, WIDTH, 2)
    display.text("Updated " + feed["updated"][-5:] + "  -  tap header to refresh", MARGIN, y + 22, WIDTH, 2)

    draw_scrollbar(list_scroll, list_content_height())

    # Header last, so cards scroll underneath it.
    draw_header_bar()
    display.set_pen(ACCENT)
    display.text("NEXT UP", MARGIN, 12, WIDTH, 3)
    count = str(feed["counts"]["ready"]) + " ready"
    display.set_pen(TEXT)
    display.text(count, WIDTH - MARGIN - display.measure_text(count, 2), 16, WIDTH, 2)
    display.set_pen(WARN if error else MUTED)
    line2 = "Offline - last updated " + feed["updated"][-5:] if error else (feed.get("nextRelease") or "")
    display.text(wrap(line2, WIDTH - 2 * MARGIN, 2, 1)[0], MARGIN, 42, WIDTH, 2)


def pill(label, right, y, bg, fg):
    w = display.measure_text(label, 2) + 12
    display.set_pen(bg)
    display.rectangle(right - w, y, w, 22)
    display.set_pen(fg)
    display.text(label, right - w + 6, y + 3, WIDTH, 2)
    return right - w


def build_of(story):
    build = story.get("build")
    return build if build and build.get("status") in ("started", "pr", "merged", "closed") else None


def card_at(y):
    # The row index of the card under y, or -1 for a heading, a gap or nothing.
    top = HEADER_H + GAP - list_scroll
    for r, (kind, data, h) in enumerate(list_rows()):
        if kind != "head" and top <= y < top + CARD_H:
            return r
        top += h
    return -1


# ---------- Detail view ----------

def open_detail(i, keep_scroll=False, source=None):
    global view, detail_index, detail_scroll, detail_lines, detail_source
    if source:
        detail_source = source
    view = "detail"
    detail_index = i
    if not keep_scroll:
        detail_scroll = 0
    s = detail_items()[i]
    lines = []

    def add(text, pen, scale=2, max_lines=0, height=None):
        for line in wrap(text, TEXT_W, scale, max_lines):
            lines.append((line, pen, scale, height or (8 * scale + 6)))

    def gap(h=12):
        lines.append(("", MUTED, 2, h))

    def section(title):
        gap(14)
        add(title.upper(), ACCENT)

    facts = []
    if s.get("estimate"):
        facts.append(str(s["estimate"]) + " pts")
    if s.get("activity"):
        facts.append(s["activity"].replace("-", " "))
    add(s["title"], TEXT, 3)
    gap(4)
    add("  -  ".join(facts), MUTED)
    add(s.get("release") or "", MUTED)

    build = s.get("build") or {}
    if build.get("status") == "merged":
        section("Cost")
        cost = cost_label(build)
        if cost:
            tokens = tokens_label(build)
            add(cost + ("  -  " + tokens if tokens else ""), TEXT, 3)
            add("Estimated, recorded in PR #" + str(build["pr"]), MUTED)
        else:
            add("Not recorded yet in PR #" + str(build["pr"]), MUTED)

    if s.get("summary"):
        section("Summary")
        add(s["summary"], TEXT)

    if s.get("dependsOn"):
        section("Builds on")
        for d in s["dependsOn"]:
            add(d["id"] + " (" + d["status"] + ")", MUTED)
            add(d["title"], TEXT)

    if s.get("unblocks"):
        section("Unblocks")
        for d in s["unblocks"]:
            add(d["id"], MUTED)
            add(d["title"], TEXT)

    if s.get("jobs"):
        section("Jobs")
        for job in s["jobs"]:
            add(job.replace("-", " "), TEXT)

    gap(20)
    add("Swipe left or right for other " + ("builds" if detail_source == "builds" else "stories"), MUTED)
    gap(20)
    detail_lines = lines
    clamp_detail()


def detail_content_height():
    return 12 + sum(l[3] for l in detail_lines)


def clamp_detail():
    global detail_scroll
    detail_scroll = max(0, min(detail_scroll, detail_content_height() - DETAIL_H))


def draw_detail():
    s = detail_items()[detail_index]
    y = HEADER_H + 12 - detail_scroll
    for text, pen, scale, height in detail_lines:
        if y > HEIGHT:
            break
        if y + height > HEADER_H and text:
            display.set_pen(pen)
            display.text(text, MARGIN + 4, y, WIDTH, scale)
        y += height

    draw_scrollbar(detail_scroll, detail_content_height(), DETAIL_H)
    draw_build_button(s)

    draw_header_bar()
    display.set_pen(MUTED)
    display.text("< Back", MARGIN, 24, WIDTH, 2)
    display.set_pen(ACCENT)
    w = display.measure_text(s["id"], 3)
    display.text(s["id"], (WIDTH - w) // 2, 20, WIDTH, 3)
    pos = "{}/{}".format(detail_index + 1, len(detail_items()))
    display.set_pen(MUTED)
    display.text(pos, WIDTH - MARGIN - display.measure_text(pos, 2), 24, WIDTH, 2)


# ---------- Build ----------

def can_build(story):
    return BUILD_TOKEN and not build_of(story)


def button(label, x, y, w, h, bg, fg):
    display.set_pen(bg)
    display.rectangle(x, y, w, h)
    display.set_pen(fg)
    label = wrap(label, w - 16, 2, 1)[0]
    display.text(label, x + (w - display.measure_text(label, 2)) // 2, y + (h - 16) // 2, WIDTH, 2)


def draw_build_button(story):
    y = HEIGHT - BTN_H
    display.set_pen(BG)
    display.rectangle(0, y, WIDTH, BTN_H)
    build = build_of(story)
    if build and build["status"] == "merged":
        cost = cost_label(build)
        label = "Merged in PR #{}".format(build["pr"]) + ("  -  " + cost if cost else "")
        bg, fg = MERGED_BG, MERGED_TEXT
    elif build and build["status"] == "closed":
        label, bg, fg = "PR #{} was closed".format(build["pr"]), BUTTON_OFF, MUTED
    elif build and build["status"] == "pr":
        state = "draft" if build.get("draft") else build.get("prState", "open")
        label, bg, fg = "PR #{} ({})".format(build["pr"], state), BUTTON_OFF, MUTED
    elif build:
        label, bg, fg = "Session running - see Claude app", BUTTON_OFF, MUTED
    elif not BUILD_TOKEN:
        label, bg, fg = "Add BUILD_TOKEN to secrets.py", BUTTON_OFF, MUTED
    else:
        label, bg, fg = "Build with Claude", BUTTON, BUTTON_TEXT
    button(label, MARGIN - 4, y + 8, WIDTH - 2 * (MARGIN - 4), BTN_H - 16, bg, fg)


def draw_confirm():
    s = detail_items()[detail_index]
    draw_header_bar()
    display.set_pen(ACCENT)
    display.text("Start a session?", MARGIN, 20, WIDTH, 3)
    y = HEADER_H + 20
    display.set_pen(ACCENT)
    display.text(s["id"], MARGIN, y, WIDTH, 3)
    y += 34
    display.set_pen(TEXT)
    for line in wrap(s["title"], WIDTH - 2 * MARGIN, 2, 3):
        display.text(line, MARGIN, y, WIDTH, 2)
        y += 22
    y += 14
    display.set_pen(MUTED)
    note = ("Opens Claude Code on your computer in plan mode. It asks its questions "
            "in the Claude app, then waits for you to approve a plan before it builds.")
    for line in wrap(note, WIDTH - 2 * MARGIN, 2, 6):
        display.text(line, MARGIN, y, WIDTH, 2)
        y += 22
    half = (WIDTH - 2 * MARGIN - 10) // 2
    by = HEIGHT - BTN_H + 8
    button("Cancel", MARGIN, by, half, BTN_H - 16, BUTTON_OFF, TEXT)
    button("Start", MARGIN + half + 10, by, half, BTN_H - 16, BUTTON, BUTTON_TEXT)


def draw_result():
    draw_header_bar()
    display.set_pen(ACCENT if result_ok else WARN)
    display.text(result_title, MARGIN, 20, WIDTH, 3)
    display.set_pen(TEXT)
    y = HEADER_H + 30
    for line in wrap(result_message, WIDTH - 2 * MARGIN, 2, 10):
        display.text(line, MARGIN, y, WIDTH, 2)
        y += 22
    display.set_pen(MUTED)
    display.text("Tap to go back", MARGIN, HEIGHT - 40, WIDTH, 2)


def start_build():
    global view, result_title, result_message, result_ok
    s = detail_items()[detail_index]
    draw_header_bar()
    display.set_pen(ACCENT)
    display.text("Starting...", MARGIN, 20, WIDTH, 3)
    presto.update()
    try:
        response = requests.post(BUILD_URL + s["id"], headers={"X-Build-Token": BUILD_TOKEN}, data="")
        data = response.json()
        response.close()
        result_ok = bool(data.get("ok"))
        result_title = "Session started" if result_ok else "Not started"
        if result_ok:
            result_message = s["id"] + " is open in Terminal on your computer, on branch " + data.get("branch", "") + ". " + data.get("message", "")
        else:
            result_message = data.get("error", "Unknown error")
    except Exception as e:
        result_ok = False
        result_title = "Not started"
        result_message = "Can't reach your computer: " + str(e)
    view = "result"
    gc.collect()
    if result_ok:
        fetch()   # picks up the Building badge


# ---------- Shared drawing ----------

def draw_header_bar():
    display.set_pen(HEADER)
    display.rectangle(0, 0, WIDTH, HEADER_H)


def draw_scrollbar(scroll, content, visible=BODY_H):
    if content <= visible:
        return
    bar_h = max(30, visible * visible // content)
    bar_y = HEADER_H + (visible - bar_h) * scroll // (content - visible)
    display.set_pen(BAR)
    display.rectangle(WIDTH - 5, bar_y, 3, bar_h)


def draw_message(title, message, pen):
    draw_header_bar()
    display.set_pen(ACCENT)
    display.text(title, MARGIN, 20, WIDTH, 3)
    display.set_pen(pen)
    for n, line in enumerate(wrap(message, WIDTH - 2 * MARGIN, 2, 8)):
        display.text(line, MARGIN, HEADER_H + 30 + n * 22, WIDTH, 2)


def draw():
    display.set_pen(BG)
    display.clear()
    if view == "notice" and notices:
        draw_notice()
    elif feed is None:
        draw_message("NEXT UP", error or "Loading...", WARN if error else MUTED)
    elif view == "detail" and detail_index < len(detail_items()):
        draw_detail()
    elif view == "confirm" and detail_index < len(detail_items()):
        draw_confirm()
    elif view == "result":
        draw_result()
    else:
        draw_list()
    presto.update()


def draw_refreshing():
    draw_header_bar()
    display.set_pen(ACCENT)
    display.text("Refreshing...", MARGIN, 20, WIDTH, 3)
    presto.update()


# ---------- Gestures ----------

def on_tap(x, y):
    global view
    if view == "notice":
        notices.pop(0)
        if not notices:
            view = notice_return
            leds(0, 40, 10) if feed and feed["counts"]["ready"] else leds(0, 0, 30)
        return
    if view == "result":
        view = "detail"
        return
    if view == "confirm":
        if y >= HEIGHT - BTN_H:
            if x >= WIDTH // 2:
                start_build()
            else:
                view = "detail"
        return
    if view == "detail":
        if y < HEADER_H:
            view = "list"
        elif y >= HEIGHT - BTN_H and can_build(detail_items()[detail_index]):
            view = "confirm"
        return
    if y < HEADER_H:
        draw_refreshing()
        fetch()
        return
    r = card_at(y)
    if r < 0:
        return
    kind, data, _ = list_rows()[r]
    open_detail(data, source="builds" if kind == "build" else "ready")


def on_drag(dy):
    global list_scroll, detail_scroll
    if view in ("confirm", "result", "notice"):
        return
    if view == "detail":
        detail_scroll -= dy
        clamp_detail()
    else:
        list_scroll -= dy
        clamp_list()


def on_swipe(dx):
    if view != "detail" or not detail_items():
        return
    step = 1 if dx < 0 else -1   # swipe left for the next story
    open_detail((detail_index + step) % len(detail_items()))


load_cache()
draw()
try:
    presto.connect()
except Exception as e:
    error = "Wi-Fi failed: " + str(e)
    leds(50, 0, 0)
fetch()
draw()

down = False
moved = False
axis = None
start_x = start_y = last_x = last_y = 0

while True:
    touch.poll()
    dirty = False

    if touch.state:
        x, y = touch.x, touch.y
        if not down:
            down, moved, axis = True, False, None
            start_x, start_y, last_x, last_y = x, y, x, y
            if view == "list" and feed and y >= HEADER_H:
                pressed_card = card_at(y)
                dirty = pressed_card >= 0
        else:
            if not moved and (abs(x - start_x) > DRAG_START or abs(y - start_y) > DRAG_START):
                moved = True
                axis = "v" if abs(y - start_y) >= abs(x - start_x) else "h"
                if pressed_card >= 0:
                    pressed_card = -1
                    dirty = True
            if moved and axis == "v" and y != last_y and feed:
                on_drag(y - last_y)
                dirty = True
            last_x, last_y = x, y
    elif down:
        down = False
        pressed_card = -1
        if not moved:
            on_tap(start_x, start_y)
        elif axis == "h" and abs(last_x - start_x) > SWIPE_MIN:
            on_swipe(last_x - start_x)
        dirty = True

    running = any(build_status(b) in ("started", "pr") for b in builds())
    interval = ACTIVE_REFRESH_SECONDS if running else REFRESH_SECONDS
    if not down and time.ticks_diff(time.ticks_ms(), last_fetch) > interval * 1000:
        fetch()
        dirty = True

    if dirty:
        draw()
    time.sleep_ms(10)
