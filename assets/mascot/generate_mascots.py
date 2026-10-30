#!/usr/bin/env python3
"""Маскот Threelanguage — трёхголовый пиксель-арт дракон. v3: 128x128, КРАСНЫЙ.

Рендер: дизайн-сетка 64 -> устройство 128 (ровный блок 2x2). Палитра/геометрия
редактируются здесь; перегенерация: `python3 generate_mascots.py`.
"""
import zlib, struct, os

try:
    from PIL import Image
except ImportError:
    Image = None

# --- палитра: красный дракон (без оранжевого сдвига) --------------
SKIN   = (208, 40, 40)     # основной тон чешуи
SKIN_D = (150, 28, 32)     # тёмно-красные тени/шипы
BELLY  = (240, 190, 130)   # кремовое брюхо
EYE    = (255, 230, 90)    # жёлтые глаза
PUPIL  = (25, 25, 35)
HORN   = (225, 200, 175)   # костяные рога (бежевые, не рыжие)
INK    = (35, 18, 20)      # почти-чёрный контур
FIRE_O = (255, 120, 50)    # огонь (только во рту/дыхании)
FIRE_I = (255, 220, 90)
NONE   = None

D    = 64    # дизайн-сетка
S    = 128   # выходной размер (128x128), блок 2x2
W = H = S
OUTDIR = os.path.dirname(os.path.abspath(__file__)) or '.'

def chunk(typ, data):
    c = typ + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

def draw(mood):
    grid = [[NONE]*W for _ in range(H)]
    def px(x, y, c):
        if 0 <= x < W and 0 <= y < H: grid[y][x] = c
    def rect(x0, y0, x1, y1, c, overwrite=True):  # inclusive, дизайн-сетка, блок 2x2
        for y in range(y0*2, (y1+1)*2):
            for x in range(x0*2, (x1+1)*2):
                if overwrite or grid[y][x] is None: px(x, y, c)
    def outline_block(x, y, c):             # аутлайн: только в пустые ячейки
        rect(x, y, x, y, c, overwrite=False)
    def pxs(x, y, c): rect(x, y, x, y, c)
    def pxs_shadow(x, y):  # затемнить СУЩЕСТВУЮЩИЙ красный пиксель (кромка), не трогать лицо/пустоту
        if grid[y*2][x*2] in (SKIN, SKIN_D): rect(x, y, x, y, SKIN_D)
    def line(xs, y, c):
        for x in xs: pxs(x, y, c)

    # heads (левая/правая чуть ниже центральной)
    rect(4,  8, 15, 18, SKIN); rect(17, 3, 28, 14, SKIN); rect(30, 8, 41, 18, SKIN)
    # snouts
    rect(5, 15, 12, 18, SKIN); rect(19, 11, 26, 14, SKIN); rect(33, 15, 40, 18, SKIN)
    # necks
    rect(8, 19, 12, 24, SKIN); rect(20, 15, 25, 24, SKIN); rect(33, 19, 37, 24, SKIN)
    # body
    rect(8, 24, 37, 35, SKIN); rect(13, 27, 32, 34, BELLY)
    # legs
    rect(11, 37, 16, 41, SKIN); rect(29, 37, 34, 41, SKIN)
    # tail
    rect(37, 28, 44, 31, SKIN); rect(43, 26, 47, 28, SKIN); pxs(47, 25, SKIN_D)
    # spikes на головах
    for hx in (6, 9, 12):  rect(hx, 6, hx+1, 7, SKIN_D)
    for hx in (19, 22, 25):rect(hx, 1, hx+1, 2, SKIN_D)
    for hx in (33, 36, 39):rect(hx, 6, hx+1, 7, SKIN_D)
    # horns
    rect(4, 5, 5, 7, HORN);  rect(14, 5, 15, 7, HORN)
    rect(17, 0, 18, 2, HORN); rect(27, 0, 28, 2, HORN)
    rect(30, 5, 31, 7, HORN); rect(40, 5, 41, 7, HORN)

    closed = mood in ('sleep', 'sad')
    angry  = mood == 'angry'
    eye_boxes = [(6,10,8,12),(11,10,13,12),          # левая голова
                 (19,6,21,8),(24,6,26,8),            # центр
                 (33,10,35,12),(38,10,40,12)]        # правая
    for (x0,y0,x1,y1) in eye_boxes:
        if closed:
            rect(x0, y1, x1, y1, INK)               # сомкнутый глаз — линия
        elif angry:
            rect(x0, y0+1, x1, y1, EYE)
            cx=(x0+x1)//2
            pxs(cx, y1, PUPIL); pxs(cx+1, y1, PUPIL)
            dx=max(1,(x1-x0)//2)
            for k in range(x1-x0+1):                 # сердитая бровь — диагональ
                pxs(x0+k, y0 + k//dx, INK)
        else:
            rect(x0, y0, x1, y1, EYE)
            pxs((x0+x1)//2, (y0+y1)//2, PUPIL)

    # nostrils
    for (nx,ny) in ((7,16),(10,16),(21,12),(24,12),(35,16),(38,16)): pxs(nx, ny, INK)

    # mouths
    if mood == 'cheer':
        rect(20, 10, 25, 13, INK)                    # открытая пасть + огонь
        rect(21, 11, 24, 12, FIRE_O)
        pxs(22, 12, FIRE_I); pxs(23, 12, FIRE_I)
        rect(27, 14, 30, 16, FIRE_O); rect(28, 15, 29, 15, FIRE_I)
        line(range(6,12), 14, INK); line(range(34,40), 14, INK)   # ухмылки боковых
        rect(4, 22, 7, 24, SKIN); rect(38, 22, 41, 24, SKIN)      # поднятые лапки
    elif mood == 'angry':
        line(range(6,12), 14, INK)
        for x in range(20,26): pxs(x, 10 if x%2 else 11, FIRE_O)  # скрежет огнём
        line(range(34,40), 14, INK)
    elif mood == 'sad':
        for x in range(6,12):  pxs(x, 14 - max(0,(x-6)-(x-11)), INK)
        for x in range(20,26): pxs(x, 10 - max(0,(x-20)-(x-25)), INK)
        for x in range(34,40): pxs(x, 14 - max(0,(x-34)-(x-39)), INK)
        pxs(7, 13, EYE); pxs(7, 14, EYE)                          # слеза
        pxs(39, 13, EYE); pxs(39, 14, EYE)
    elif mood == 'sleep':
        line(range(6,12), 15, INK); line(range(20,26), 11, INK); line(range(34,40), 15, INK)
        for (x,y) in ((30,1),(31,1),(32,1),(31,2),(32,3)): pxs(x, y, EYE)  # zzz
    else:  # idle
        line(range(6,12), 14, INK); line(range(20,26), 10, INK); line(range(34,40), 14, INK)

    # shading: затемнить нижний ряд силуэта (кромка), не трогая пустоту и лицо
    for x in range(9, 37): pxs_shadow(x, 35)
    for x in range(5, 12): pxs_shadow(x, 19)
    for x in range(34, 41): pxs_shadow(x, 19)

    # outline pass: 1px по дизайн-сетке -> 2px блоки (crisp pixel-art)
    dgrid = [[NONE]*D for _ in range(D)]
    for y in range(D):
        for x in range(D):
            dgrid[y][x] = grid[y*2][x*2]
    added=[]
    for y in range(D):
        for x in range(D):
            if dgrid[y][x] is None:
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx,ny=x+dx,y+dy
                    if 0<=nx<D and 0<=ny<D and dgrid[ny][nx] is not None:
                        added.append((x,y)); break
    for x,y in added:
        outline_block(x, y, INK)

    palette=[]; idx={}
    for row in grid:
        for c in row:
            if c is not None and c not in idx:
                idx[c]=len(palette); palette.append(c)
    plte=b'\x00\x00\x00'+b''.join(bytes(c) for c in palette)
    raw=b''.join(b'\x00'+bytes(0 if c is None else idx[c]+1 for c in row) for row in grid)
    ihdr=struct.pack(">IIBBBBB",W,H,8,3,0,0,0)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'PLTE',plte)
         +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))
    return png, grid, palette, idx

MOODS = (('idle','dragon_idle'),('cheer','dragon_cheer'),
         ('angry','dragon_angry'),('sad','dragon_sad'),('sleep','dragon_sleep'))

grids = {}
for mood, name in MOODS:
    png, grid, palette, idx = draw(mood)
    grids[name] = (grid, palette, idx)
    with open(os.path.join(OUTDIR, name+'.png'),'wb') as f: f.write(png)
    print(name, f"{S}x{S}", len(png), 'bytes')

if Image is not None:
    Z, PAD = 2, 16   # плитка 256px
    sheet = Image.new('RGB', (5*(S*Z)+PAD*6, S*Z+PAD*2), (24,24,32))
    for n,(mood,name) in enumerate(MOODS):
        grid, palette, idx = grids[name]
        pal_rgba = [(0,0,0,0)] + [tuple(c)+(255,) for c in palette]
        tile = Image.new('RGBA',(S,S))
        tile.putdata([(0,0,0,0) if v is None else pal_rgba[idx[v]+1] for row in grid for v in row])
        tile = tile.resize((S*Z,S*Z), Image.NEAREST)
        bg = Image.new('RGBA',(S*Z,S*Z),(40,40,52,255))
        checker = Image.new('RGBA',(16,16),(48,48,62,255))
        for yy in range(0,S*Z,16):
            for xx in range(0,S*Z,16):
                if (xx//16+yy//16)%2==0: bg.paste(checker,(xx,yy))
        bg.alpha_composite(tile)
        sheet.paste(bg.convert('RGB'), (PAD+n*(S*Z+PAD), PAD))
    sheet.save(os.path.join(OUTDIR,'preview_sheet.png'))
    print('preview_sheet.png saved')
