"""Conservative crop adjustment for the visible Samsung Edge panel window."""

import re


EDGE_WINDOW = (
    'com.sec.android.app.launcher/'
    'com.samsung.app.honeyspace.edge.edgepanel.app.CocktailBarService'
)
WINDOW_BLOCK = re.compile(
    r'(?ms)^  Window #[^\n]*:\n.*?(?=^  Window #|^  mGlobalConfiguration|\Z)'
)
ATTRS = re.compile(
    r'mAttrs=\{\((-?\d+),(-?\d+)\)\((\d+)x(\d+)\)\s+'
    r'gr=([A-Z ]+?)(?=\s+\w+=|\})'
)


def exclude_visible_edge_panel(crop, window_dump, viewport_right=1080):
    """Return a crop and minimal evidence, failing on uncertain matching geometry."""
    matches = [block for block in WINDOW_BLOCK.findall(window_dump.replace('\r', ''))
               if EDGE_WINDOW in block.splitlines()[0] and 'isVisible=true' in block]
    if not matches:
        return list(crop), None
    if len(matches) != 1:
        raise ValueError('Visible Edge panel window geometry is not unique')

    attrs = ATTRS.search(matches[0])
    if attrs is None:
        raise ValueError('Visible Edge panel window geometry is unknown')
    x, y, width, height = map(int, attrs.group(1, 2, 3, 4))
    gravity = attrs.group(5).strip()
    if width <= 0 or height <= 0 or 'RIGHT' not in gravity.split():
        raise ValueError('Visible Edge panel window geometry is not right anchored')
    left, top, right, bottom = crop
    if not (left < right <= viewport_right and top < bottom):
        raise ValueError('App crop or viewport geometry is invalid')
    inset = viewport_right - right
    if inset > 8:
        raise ValueError('App crop is too far from right edge for overlay geometry')
    new_right = min(right, viewport_right - width - inset)
    if new_right - left < (right - left) * .75:
        raise ValueError('Overlay geometry would remove too much of broad app ROI')
    return [left, top, new_right, bottom], {
        'window': EDGE_WINDOW,
        'attrs': f'({x},{y})({width}x{height}) gr={gravity}',
        'width': width,
        'excluded_from_x': new_right,
    }


def exclude_visible_system_bubble(crop, window_dump, input_dump, display_width):
    """Exclude only a verified, edge anchored SystemUI Bubbles touch region."""
    blocks = [block for block in WINDOW_BLOCK.findall(window_dump.replace('\r', ''))
              if re.search(r'Window\{([0-9a-fA-F]+) u\d+ Bubbles!\}', block.splitlines()[0])
              and 'isVisible=true' in block]
    if not blocks:
        return list(crop), None
    if len(blocks) != 1:
        raise ValueError('Visible Bubbles window is not unique')
    block = blocks[0]
    header = block.splitlines()[0]
    token = re.search(r'Window\{([0-9a-fA-F]+) u\d+ Bubbles!\}', header).group(1)
    if not all(value in block for value in
               ('com.android.systemui', '(fillxfill)', 'APPLICATION_OVERLAY', 'fmt=TRANSLUCENT',
                'mHasSurface=true', 'isVisible=true')):
        raise ValueError('Visible Bubbles window is not a drawn SystemUI overlay')

    lines = [line.strip() for line in input_dump.replace('\r', '').splitlines()
             if re.match(r'\s*\d+: name=[^,]* Bubbles!,', line)
             and re.search(r'\bdisplayId=0(?:,|\b)', line)]
    if len(lines) != 1 or not re.search(r'\bname=' + re.escape(token) + r' Bubbles!,', lines[0]):
        raise ValueError('Visible Bubbles input window is not unique or does not match')
    line = lines[0]
    display = re.search(r'\bdisplayId=(-?\d+)', line)
    config = re.search(r'\binputConfig=([^,]+)', line)
    if (not display or int(display.group(1)) != 0 or not config
            or 'TRUSTED_OVERLAY' not in config.group(1).split(' | ')
            or 'NOT_VISIBLE' in config.group(1).split(' | ')
            or 'CLONE' in config.group(1).split(' | ')):
        raise ValueError('Visible Bubbles input is not a visible trusted display 0 overlay')
    region = re.search(r'\btouchableRegion=(.*?)(?:, ownerPid=|, ownerUid=|$)', line)
    bounds = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', region.group(1).strip()) if region else None
    frame = re.search(r'\bframe=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', line)
    if not bounds or not frame:
        raise ValueError('Visible Bubbles touchable region is not one rectangle')
    x1, y1, x2, y2 = map(int, bounds.groups())
    fx1, fy1, fx2, fy2 = map(int, frame.groups())
    left, top, right, bottom = crop
    if not (0 <= left < right <= display_width and top < bottom
            and fx1 == 0 and fy1 == 0 and fx2 == display_width and fy2 >= bottom
            and x1 < x2 and y1 < y2 and y1 < fy2 and y2 > fy1):
        raise ValueError('Visible Bubbles or app crop geometry is invalid')
    clipped_left, clipped_right = max(0, x1), min(display_width, x2)
    if clipped_left >= clipped_right:
        raise ValueError('Visible Bubbles region is outside display')
    if clipped_left <= 8:
        side = 'left'
        new_crop = [max(left, clipped_right + 2), top, right, bottom]
    elif clipped_right >= display_width - 8:
        side = 'right'
        new_crop = [left, top, min(right, clipped_left - 2), bottom]
    else:
        raise ValueError('Visible Bubbles region is not edge anchored')
    if new_crop[2] - new_crop[0] <= (right - left) * .75:
        raise ValueError('Visible Bubbles geometry would remove too much of broad app ROI')
    return new_crop, {
        'window': 'Bubbles!', 'owner': 'com.android.systemui', 'display_id': 0,
        'touchable_region': [x1, y1, x2, y2], 'side': side,
        'excluded_to_x': new_crop[0] if side == 'left' else new_crop[2],
    }
