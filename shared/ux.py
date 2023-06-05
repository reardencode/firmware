# (c) Copyright 2018 by Coinkite Inc. This file is covered by license found in COPYING-CC.
#
# ux.py - UX/UI related helper functions
#
from uasyncio import sleep_ms
from queues import QueueEmpty
import utime, gc, version
from utils import word_wrap
from charcodes import (KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_HOME, KEY_NFC, KEY_QR,
                        KEY_END, KEY_PAGE_UP, KEY_PAGE_DOWN, KEY_SELECT, KEY_CANCEL)
from exceptions import AbortInteraction

DEFAULT_IDLE_TIMEOUT = const(4*3600)      # (seconds) 4 hours

# See ux_mk or ux_q1 for some display functions now
if version.has_qwerty:
    from lcd_display import CHARS_W, CHARS_H
    CH_PER_W = CHARS_W
    STORY_H = CHARS_H
    from ux_q1 import PressRelease, ux_enter_number, ux_input_numbers, ux_input_text, ux_show_pin
else:
    # How many characters can we fit on each line? How many lines?
    # (using FontSmall)
    CH_PER_W = 17
    STORY_H = 5
    from ux_mk4 import PressRelease, ux_enter_number, ux_input_numbers, ux_input_text, ux_show_pin

class UserInteraction:
    def __init__(self):
        self.stack = []

    def top_of_stack(self):
        return self.stack[-1] if self.stack else None

    def reset(self, new_ux):
        self.stack.clear()
        gc.collect()
        self.push(new_ux)

    async def interact(self):
        # this is called inside a while(1) all the time
        # - execute top of stack item
        try:
            await self.stack[-1].interact()
        except AbortInteraction:
            pass

    def push(self, new_ux):
        self.stack.append(new_ux)

    def replace(self, new_ux):
        old = self.stack.pop()
        del old
        self.stack.append(new_ux)

    def pop(self):
        if len(self.stack) < 2:
            # top of stack, do nothing
            return True

        old = self.stack.pop()
        del old

# Singleton. User interacts with this "menu" stack.
the_ux = UserInteraction()

def ux_clear_keys(no_aborts=False):
    # flush any pending keypresses
    from glob import numpad

    try:
        while 1:
            ch = numpad.get_nowait()

            if not no_aborts and ch == numpad.ABORT_KEY:
                raise AbortInteraction()

    except QueueEmpty:
        return

async def ux_wait_keyup(expected=None):
    # Wait for single keypress in 'expected' set, return it
    # no visual feedback, no escape
    from glob import numpad

    armed = None
    while 1:
        ch = await numpad.get()

        if ch == numpad.ABORT_KEY:
            raise AbortInteraction()

        if len(ch) > 1:
            # multipress
            continue

        if expected and (ch not in expected):
            # unwanted
            continue

        if ch == '' and armed:
            return armed

        armed = ch

def ux_poll_key():
    # non-blocking check if any key is pressed
    # - responds to key down only
    from glob import numpad

    try:
        ch = numpad.get_nowait()

        if ch == numpad.ABORT_KEY:
            raise AbortInteraction()
    except QueueEmpty:
        return None

    return ch

async def ux_show_story(msg, title=None, escape=None, sensitive=False, strict_escape=False):
    # show a big long string, and wait for XY to continue
    # - returns character used to get out (X or Y)
    # - can accept other chars to 'escape' as well.
    # - accepts a stream or string
    from glob import dis

    lines = []
    if title:
        # kinda weak rendering but it works.
        # LATER: rarely used
        lines.append('\x01' + title)

    if hasattr(msg, 'readline'):
        # coming from in-memory file for larger messages
        msg.seek(0)
        for ln in msg:
            if ln[-1] == '\n': 
                ln = ln[:-1]

            if len(ln) > CH_PER_W:
                lines.extend(word_wrap(ln, CH_PER_W))
            else:
                # ok if empty string, just a blank line
                lines.append(ln)

        # no longer needed & rude to our caller, but let's save the memory
        msg.close()
        del msg
        gc.collect()
    else:
        # simple string being shown
        if version.has_qwerty:
            msg = msg.replace('\nX ', 'CANCEL ').replace(' X ', ' CANCEL ').replace('OK', 'SELECT')

        for ln in msg.split('\n'):
            if len(ln) > CH_PER_W:
                lines.extend(word_wrap(ln, CH_PER_W))
            else:
                # ok if empty string, just a blank line
                lines.append(ln)

    # trim blank lines at end, add our own marker
    while not lines[-1]:
        lines = lines[:-1]

    lines.append('EOT')

    top = 0
    ch = None
    pr = PressRelease()
    while 1:
        # redraw
        dis.draw_story(lines[top:top+STORY_H], top, len(lines), sensitive)

        # wait to do something
        ch = await pr.wait()
        if escape and (ch == escape or ch in escape):
            # allow another way out for some usages
            return ch
        elif ch == KEY_SELECT:
            if not strict_escape:
                return 'y'      # translate for Mk4 code
        elif ch == KEY_CANCEL:
            if not strict_escape:
                return 'x'      # translate for Mk4 code
        elif ch in 'xy':
            if not strict_escape:
                return ch
        elif ch == KEY_END:
            top = max(0, len(lines)-(STORY_H//2))
        elif ch == '0' or ch == KEY_HOME:
            top = 0
        elif ch == '7' or ch == KEY_PAGE_UP:
            top = max(0, top-STORY_H)
        elif ch == '9' or ch == KEY_PAGE_DOWN:
            top = min(len(lines)-2, top+STORY_H)
        elif ch == '5' or ch == KEY_UP:
            top = max(0, top-1)
        elif ch == '8' or ch == KEY_DOWN:
            top = min(len(lines)-2, top+1)
        elif not strict_escape:
            if ch in { KEY_NFC, KEY_QR }:
                return ch

        

async def idle_logout():
    import glob
    from glob import settings

    while not glob.hsm_active:
        await sleep_ms(5000)

        if not glob.numpad.last_event_time:
            continue

        now = utime.ticks_ms() 
        dt = utime.ticks_diff(now, glob.numpad.last_event_time)

        # they may have changed setting recently
        timeout = settings.get('idle_to', DEFAULT_IDLE_TIMEOUT)*1000        # ms

        if timeout and dt > timeout:
            # user has been idle for too long: do a logout
            print("Idle!")

            from actions import logout_now
            await logout_now()
            return              # not reached
            
async def ux_confirm(msg):
    # confirmation screen, with stock title and Y=of course.

    resp = await ux_show_story("Are you SURE ?!?\n\n" + msg)

    return resp == 'y'


async def ux_dramatic_pause(msg, seconds):
    from glob import dis, hsm_active

    if hsm_active:
        return

    # show a full-screen msg, with a dramatic pause + progress bar
    n = seconds * 8
    dis.fullscreen(msg)
    for i in range(n):
        dis.progress_bar_show(i/n)
        await sleep_ms(125)

    ux_clear_keys()

def show_fatal_error(msg):
    # show a multi-line error message, over some kinda "fatal" banner
    from glob import dis

    lines = msg.split('\n')[-6:]
    dis.show_yikes(lines)

async def ux_aborted():
    # use this when dangerous action is not performed due to confirmations
    await ux_dramatic_pause('Aborted.', 2)
    return None

def restore_menu():
    # redraw screen contents after distrupting it w/ non-ux things (usb upload)
    m = the_ux.top_of_stack()

    if hasattr(m, 'update_contents'):
        m.update_contents()

    if hasattr(m, 'show'):
        m.show()

def abort_and_goto(m):
    # cancel any menu drill-down and show them some UX
    from glob import numpad
    the_ux.reset(m)
    numpad.abort_ux()

def abort_and_push(m):
    # keep menu position, but interrupt it with a new UX
    from glob import numpad
    the_ux.push(m)
    numpad.abort_ux()

async def show_qr_codes(addrs, is_alnum, start_n):
    from qrs import QRDisplaySingle
    o = QRDisplaySingle(addrs, is_alnum, start_n, sidebar=None)
    await o.interact_bare()

async def show_qr_code(data, is_alnum):
    from qrs import QRDisplaySingle
    o = QRDisplaySingle([data], is_alnum)
    await o.interact_bare()

async def ux_enter_bip32_index(prompt, can_cancel=False, unlimited=False):
    if unlimited:
        max_value = (2 ** 31) - 1  # we handle hardened
    else:
        max_value = 9999

    return await ux_enter_number(prompt=prompt, max_value=max_value, can_cancel=can_cancel)


# EOF
