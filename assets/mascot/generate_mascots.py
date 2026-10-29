import zlib, struct, os

OUTDIR = '/workspace/assets/mascot'

def chunk(typ, data):
    c = typ + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

SKIN   = (110, 200, 120)
SKIN_D = (70, 150, 85)
BELLY  = (225, 215, 150)
EYE    = (255, 230, 90)
PUPIL  = (25, 25, 35)
HORN   = (245, 190, 90)
INK    = (35, 45, 40)
FIRE_O = (255, 140, 60)
FIRE_I = (255, 220, 90)
NONE   = None
W = H = 48

def draw(mood):
    grid = [[NONE]*W for _ in range(H)]
    def px(x, y, c):
        if 0 <= x < W and 0 <= y < H: grid[y][x] = c
    def rect(x0, y0, x1, y1, c):
        for y in range(y0, y1+1):
            for x in range(x0, x1+1): px(x, y, c)

    # heads
    rect(5, 8, 15, 18, SKIN); rect(17, 4, 28, 14, SKIN); rect(30, 8, 40, 18, SKIN)
    # snouts
    rect(6, 15, 12, 18, SKIN); rect(19, 11, 26, 14, SKIN); rect(32, 15, 38, 18, SKIN)
    # necks
    rect(8, 19, 12, 24, SKIN); rect(20, 15, 25, 24, SKIN); rect(33, 19, 37, 24, SKIN)
    # body
    rect(8, 24, 37, 34, SKIN); rect(13, 27, 32, 34, BELLY)
    # legs
    rect(11, 35, 16, 38, SKIN); rect(29, 35, 34, 38, SKIN)
    # tail
    rect(37, 28, 43, 31, SKIN); rect(42, 26, 45, 28, SKIN)
    # spikes
    for hx in (7, 10, 13): rect(hx, 6, hx+1, 7, SKIN_D)
    for hx in (19, 22, 25): rect(hx, 2, hx+1, 3, SKIN_D)
    for hx in (32, 35, 38): rect(hx, 6, hx+1, 7, SKIN_D)
    # horns
    rect(5, 5, 6, 7, HORN); rect(14, 5, 15, 7, HORN)
    rect(17, 1, 18, 3, HORN); rect(27, 1, 28, 3, HORN)
    rect(30, 5, 31, 7, HORN); rect(39, 5, 40, 7, HORN)
    # shading under body
    for x in range(9, 37): px(x, 34, SKIN_D)

    closed = mood in ('sleep', 'sad')
    angry = mood == 'angry'
    # eyes
    eye_boxes = [(7,10,9,12),(12,10,14,12),(19,6,21,8),(24,6,26,8),(32,10,34,12),(37,10,39,12)]
    for (x0,y0,x1,y1) in eye_boxes:
        if closed:
            for x in range(x0, x1+1): px(x, y1-1, INK)
        elif angry:
            rect(x0, y0+1, x1, y1, EYE)
            px((x0+x1)//2, y1-1, PUPIL)
            for i in range(x0, x1+1):  # brow slash
                px(i, y0 + (i-x0)//3, INK)
        else:
            rect(x0, y0, x1, y1, EYE); px((x0+x1)//2, (y0+y1)//2, PUPIL)

    # nostrils
    px(8,16,INK); px(11,16,INK); px(21,12,INK); px(24,12,INK); px(34,16,INK); px(37,16,INK)

    # mouths
    if mood == 'happy':
        for x in range(7,12): px(x, 14 + max(0,(x-7)-(x-9)), INK)
        for x in range(20,26): px(x, 10 + max(0,(x-20)-(x-22)), INK)
        for x in range(33,38): px(x, 14 + max(0,(x-33)-(x-35)), INK)
    elif mood == 'angry':
        for x in range(7,12): px(x,14, INK)
        for x in range(20,26):
            px(x, 10 if x%2 else 11, FIRE_O)  # gritted fire mouth
        for x in range(33,38): px(x,14, INK)
    elif mood == 'sad':
        for x in range(7,12): px(x, 14 - max(0,(x-7)-(x-9)), INK)
        for x in range(20,26): px(x, 10 - max(0,(x-20)-(x-22)), INK)
        for x in range(33,38): px(x, 14 - max(0,(x-33)-(x-35)), INK)
    else:
        for x in range(7,12): px(x,14, INK)
        for x in range(20,26): px(x,10, INK)
        for x in range(33,38): px(x,14, INK)

    if mood == 'cheer':
        # open center mouth with fire breath
        rect(20, 10, 25, 13, INK)
        rect(21, 11, 24, 12, FIRE_O)
        px(22, 12, FIRE_I); px(23, 12, FIRE_I)
        rect(27, 14, 30, 16, FIRE_O)  # breath going right-down
        rect(28, 15, 29, 15, FIRE_I)
        # raised arms
        rect(4, 22, 7, 24, SKIN); rect(38, 22, 41, 24, SKIN)
    if mood == 'sleep':
        rect(28, 2, 31, 4, NONE)  # clear
        for (x,y) in ((29,2),(30,2),(31,2),(30,3),(31,4)): px(x, y, EYE)  # zzz-ish dots
    if mood == 'sad':
        px(8, 13, EYE); px(8, 14, EYE)  # tear on left head
        px(38, 13, EYE); px(38, 14, EYE)

    # outline pass
    src = [row[:] for row in grid]; added=[]
    for y in range(H):
        for x in range(W):
            if src[y][x] is None:
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx,ny=x+dx,y+dy
                    if 0<=nx<W and 0<=ny<H and src[ny][nx] is not None:
                        added.append((x,y)); break
    for x,y in added: grid[y][x] = INK

    palette=[]; idx={}
    for row in grid:
        for c in row:
            if c is not None and c not in idx:
                idx[c]=len(palette); palette.append(c)
    plte=b'\x00\x00\x00\x00'+b''.join(bytes(c) for c in palette)
    raw=b''.join(b'\x00'+bytes(0 if c is None else idx[c]+1 for c in row) for row in grid)
    ihdr=struct.pack(">IIBBBBB",W,H,8,3,0,0,0)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'PLTE',plte)
            +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))

for mood, name in (('idle','dragon_idle'),('cheer','dragon_cheer'),
                   ('angry','dragon_angry'),('sad','dragon_sad'),('sleep','dragon_sleep')):
    png = draw(mood)
    with open(os.path.join(OUTDIR, name+'.png'),'wb') as f: f.write(png)
    print(name, len(png))
