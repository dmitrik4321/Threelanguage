#!/usr/bin/env python3
"""Маскот Threelanguage — трёхголовый пиксель-арт дракон. v4: 256x256 RGBA, КРАСНЫЙ.

Рендер: дизайн-сетка 64 -> устройство 256 (ровный блок 4x4; картинка та же,
хранится вдвое крупнее). Прозрачность — через альфа-канал RGBA (фон = alpha 0).
Силуэт центрируется в холсте по bounding-box с симметричными полями.
Палитра/геометрия редактируются здесь; перегенерация: `python3 generate_mascots.py`.
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
B    = 4     # блок масштабирования: картинка та же, хранение вдвое крупнее (128->256)
S    = D*B   # выходной размер 256x256
W = H = S
OUTDIR = os.path.dirname(os.path.abspath(__file__)) or '.'

def chunk(typ, data):
    c = typ + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

def draw(mood):
    grid = [[NONE]*W for _ in range(H)]
    def px(x, y, c):
        if 0 <= x < W and 0 <= y < H: grid[y][x] = c
    def rect(x0, y0, x1, y1, c, overwrite=True):  # inclusive, дизайн-сетка, блок BxB
        for y in range(y0*B, (y1+1)*B):
            for x in range(x0*B, (x1+1)*B):
                if overwrite or grid[y][x] is None: px(x, y, c)
    def outline_block(x, y, c):             # аутлайн: только в пустые ячейки
        rect(x, y, x, y, c, overwrite=False)
    def pxs(x, y, c): rect(x, y, x, y, c)
    def pxs_shadow(x, y):  # затемнить СУЩЕСТВУЮЩИЙ красный пиксель (кромка), не трогать лицо/пустоту
        if grid[y*B][x*B] in (SKIN, SKIN_D): rect(x, y, x, y, SKIN_D)
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

    # outline pass: 1px по дизайн-сетке -> BxB блоки (crisp pixel-art)
    dgrid = [[NONE]*D for _ in range(D)]
    for y in range(D):
        for x in range(D):
            dgrid[y][x] = grid[y*B][x*B]
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

    return grid


def center_grid(grid):
    """Центрирование силуэта в холсте: bounding-box непустых пикселей -> симметричные поля."""
    H_ = len(grid); W_ = len(grid[0])
    ys = [y for y in range(H_) if any(c is not None for c in grid[y])]
    xs = [x for x in range(W_) if any(grid[y][x] is not None for y in range(H_))]
    if not ys or not xs:
        return grid
    y0, y1 = ys[0], ys[-1]; x0, x1 = xs[0], xs[-1]
    bh, bw = y1-y0+1, x1-x0+1
    oy = (H_ - bh)//2; ox = (W_ - bw)//2
    out = [[NONE]*W_ for _ in range(H_)]
    for j in range(bh):
        for i in range(bw):
            out[oy+j][ox+i] = grid[y0+j][x0+i]
    return out


def grid_to_rgba(grid):
    """RGBA-изображение: пустые пиксели имеют альфа=0 (настоящая прозрачность)."""
    pal = sorted({c for row in grid for c in row if c is not None})
    lut = {c: tuple(c) + (255,) for c in pal}
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    img.putdata([lut[v] if v is not None else (0, 0, 0, 0)
                 for row in grid for v in row])
    return img


def encode_fallback(grid):
    """PNG без PIL (indexed): пустые пиксели -> прозрачность через tRNS-чип."""
    palette=[]; idx={}
    for row in grid:
        for c in row:
            if c is not None and c not in idx:
                idx[c]=len(palette); palette.append(c)
    plte=b'\x00\x00\x00'+b''.join(bytes(c) for c in palette)
    trns=bytes([0]+[255]*len(palette))
    raw=b''.join(b'\x00'+bytes(0 if c is None else idx[c]+1 for c in row) for row in grid)
    ihdr=struct.pack(">IIBBBBB",W,H,8,3,0,0,0)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'PLTE',plte)
         +chunk(b'tRNS',trns)+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))
    return png, palette, idx


MOODS = (('idle','dragon_idle'),('cheer','dragon_cheer'),
         ('angry','dragon_angry'),('sad','dragon_sad'),('sleep','dragon_sleep'))

grids = {}
for mood, name in MOODS:
    grid = center_grid(draw(mood))
    grids[name] = grid
    # Официальный файл: RGBA 256x256 с альфа-каналом (прозрачный фон).
    if Image is not None:
        grid_to_rgba(grid).save(os.path.join(OUTDIR, name+'.png'))
    else:
        png, palette, idx = encode_fallback(grid)
        with open(os.path.join(OUTDIR, name+'.png'),'wb') as f: f.write(png)
    print(name, f"{W}x{H}", 'RGBA')

if Image is not None:
    PAD = 16   # плитки нативные 256px
    sheet = Image.new('RGB', (5*W+PAD*6, H+PAD*2), (24,24,32))
    for n,(mood,name) in enumerate(MOODS):
        tile = grid_to_rgba(grids[name])
        bg = Image.new('RGBA',(W,H),(40,40,52,255))
        checker = Image.new('RGBA',(16,16),(48,48,62,255))
        for yy in range(0,H,16):
            for xx in range(0,W,16):
                if (xx//16+yy//16)%2==0: bg.paste(checker,(xx,yy))
        bg.alpha_composite(tile)
        sheet.paste(bg.convert('RGB'), (PAD+n*(W+PAD), PAD))
    sheet.save(os.path.join(OUTDIR,'preview_sheet.png'))
    print('preview_sheet.png saved')
