#!/usr/bin/env python3
"""
render.py -- 구글 캘린더 + 날씨를 7.3" Spectra 6 패널용 이미지로 렌더링합니다.

GitHub Actions에서 하루 한 번 실행되어 out/cal.bin 과 out/cal.png 를 만듭니다.

환경변수:
    ICAL_URL   구글 캘린더 비공개 iCal 주소 (없으면 샘플 일정으로 렌더링)
    LAT, LON   날씨 좌표 (기본: 서울 37.52493718404563, 126.93209650151725)
    PLACE      화면에 표시할 지역 이름 (기본: 서울 여의도)
    TZ_NAME    시간대 (기본: Asia/Seoul)
"""

import datetime as dt
import os
import sys
import zoneinfo

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
TZ = zoneinfo.ZoneInfo(os.environ.get("TZ_NAME", "Asia/Seoul"))
ICAL_URL = os.environ.get("ICAL_URL", "").strip()
LAT = float(os.environ.get("LAT", "37.52493718404563"))
LON = float(os.environ.get("LON", "126.93209650151725"))
PLACE = os.environ.get("PLACE", "서울 여의도")
DAYS_AHEAD = 14
MAX_EVENTS = 6

E6 = [
    (255, 255, 255, 0x0),
    (0,   255, 0,   0x2),
    (255, 0,   0,   0x6),
    (255, 255, 0,   0xB),
    (0,   0,   255, 0xD),
    (0,   0,   0,   0xF),
]

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
RED, BLUE, YELLOW, GREEN = (255, 0, 0), (0, 0, 255), (255, 255, 0), (0, 255, 0)
TODAY_HIGHLIGHT = (240, 240, 255)

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

FONT_CANDIDATES = [
    os.environ.get("FONT_PATH", ""),
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    r"C:\Windows\Fonts\malgunbd.ttf",
]


def font(size):
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    print("[warn] 한글 폰트 없음", file=sys.stderr)
    return ImageFont.load_default()


# ------------------------------------------------------------------ 날씨

# WMO weather code -> (한글 설명, 아이콘 키)
WMO = {
    0: ("맑음", "sun"), 1: ("대체로 맑음", "sun"), 2: ("구름 조금", "partly"),
    3: ("흐림", "cloud"), 45: ("안개", "cloud"), 48: ("안개", "cloud"),
    51: ("이슬비", "rain"), 53: ("이슬비", "rain"), 55: ("이슬비", "rain"),
    61: ("비", "rain"), 63: ("비", "rain"), 65: ("강한 비", "rain"),
    66: ("어는 비", "rain"), 67: ("어는 비", "rain"),
    71: ("눈", "snow"), 73: ("눈", "snow"), 75: ("많은 눈", "snow"), 77: ("싸락눈", "snow"),
    80: ("소나기", "rain"), 81: ("소나기", "rain"), 82: ("강한 소나기", "rain"),
    85: ("소나기눈", "snow"), 86: ("소나기눈", "snow"),
    95: ("뇌우", "storm"), 96: ("뇌우", "storm"), 99: ("뇌우", "storm"),
}


def fetch_weather():
    """Open-Meteo에서 현재 기온과 3일 예보를 가져옵니다. 실패하면 None."""
    try:
        import requests
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}"
            "&current=temperature_2m,weather_code"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max"
            f"&timezone={TZ.key}&forecast_days=4"
        )
        j = requests.get(url, timeout=25).json()
        cur, daily = j["current"], j["daily"]
        days = []
        for i in range(min(4, len(daily["time"]))):
            days.append({
                "date": dt.date.fromisoformat(daily["time"][i]),
                "code": daily["weather_code"][i],
                "hi": round(daily["temperature_2m_max"][i]),
                "lo": round(daily["temperature_2m_min"][i]),
                "pop": daily["precipitation_probability_max"][i] or 0,
            })
        return {"temp": round(cur["temperature_2m"]), "code": cur["weather_code"], "days": days}
    except Exception as e:
        print("[err] 날씨 조회 실패:", e, file=sys.stderr)
        return None


def _cloud(d, cx, cy, r, lw=2):
    """세 개의 원 + 아래쪽 바닥으로 구름 모양을 만듭니다."""
    lobes = [
        (cx - r * 0.62, cy + r * 0.10, r * 0.46),
        (cx + r * 0.05, cy - r * 0.28, r * 0.60),
        (cx + r * 0.68, cy + r * 0.14, r * 0.42),
    ]
    for x, y, rr in lobes:
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=WHITE, outline=BLACK, width=lw)
    # 원들 사이의 윤곽선을 흰색으로 덮어 하나의 덩어리처럼 보이게 합니다.
    d.rectangle([cx - r * 1.05, cy + r * 0.10, cx + r * 1.05, cy + r * 0.50], fill=WHITE)
    d.line([cx - r * 1.06, cy + r * 0.52, cx + r * 1.08, cy + r * 0.52], fill=BLACK, width=lw)
    d.arc([cx - r * 1.12, cy - r * 0.36, cx - r * 0.94, cy + r * 0.58], 90, 270, fill=BLACK, width=lw)
    d.arc([cx + r * 0.96, cy - r * 0.30, cx + r * 1.14, cy + r * 0.58], 270, 90, fill=BLACK, width=lw)


def draw_icon(d, kind, cx, cy, r):
    """6색만으로 그리는 단순 날씨 아이콘. (cx, cy)는 아이콘의 중심입니다."""
    if kind == "sun":
        d.ellipse([cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62],
                  fill=YELLOW, outline=BLACK, width=2)
        for i in range(8):
            import math
            a = i * math.pi / 4
            d.line([cx + math.cos(a) * r * 0.78, cy + math.sin(a) * r * 0.78,
                    cx + math.cos(a) * r * 1.02, cy + math.sin(a) * r * 1.02],
                   fill=YELLOW, width=3)

    elif kind == "partly":
        d.ellipse([cx - r * 0.05, cy - r * 0.95, cx + r * 0.85, cy - r * 0.05],
                  fill=YELLOW, outline=BLACK, width=2)
        _cloud(d, cx - r * 0.10, cy + r * 0.20, r * 0.82)

    elif kind == "cloud":
        _cloud(d, cx, cy, r * 0.92)

    elif kind in ("rain", "storm"):
        _cloud(d, cx, cy - r * 0.28, r * 0.82)
        if kind == "storm":
            d.polygon([(cx + r * 0.10, cy + r * 0.30), (cx - r * 0.28, cy + r * 0.98),
                       (cx + r * 0.02, cy + r * 0.92), (cx - r * 0.10, cy + r * 1.35),
                       (cx + r * 0.36, cy + r * 0.62), (cx + r * 0.06, cy + r * 0.66)],
                      fill=RED, outline=BLACK)
        else:
            for dx in (-r * 0.48, 0, r * 0.48):
                d.line([cx + dx + r * 0.10, cy + r * 0.44,
                        cx + dx - r * 0.10, cy + r * 1.02], fill=BLUE, width=3)

    elif kind == "snow":
        _cloud(d, cx, cy - r * 0.28, r * 0.82)
        for dx in (-r * 0.48, 0, r * 0.48):
            d.line([cx + dx - r * 0.20, cy + r * 0.74, cx + dx + r * 0.20, cy + r * 0.74],
                   fill=BLUE, width=3)
            d.line([cx + dx, cy + r * 0.54, cx + dx, cy + r * 0.94], fill=BLUE, width=3)


# ------------------------------------------------------------------ 일정

def fetch_events():
    if not ICAL_URL:
        return sample_events()
    import requests
    import icalendar
    import recurring_ical_events

    r = requests.get(ICAL_URL, timeout=30)
    r.raise_for_status()
    cal = icalendar.Calendar.from_ical(r.text)

    today = dt.datetime.now(TZ).date()
    occ = recurring_ical_events.of(cal).between(today, today + dt.timedelta(days=DAYS_AHEAD))

    out = []
    for ev in occ:
        s = ev["DTSTART"].dt
        allday = not isinstance(s, dt.datetime)
        if not allday:
            s = s.astimezone(TZ) if s.tzinfo else s.replace(tzinfo=TZ)
        out.append((s, allday, str(ev.get("SUMMARY", "(제목 없음)"))))

    out.sort(key=lambda e: (e[0].date() if isinstance(e[0], dt.datetime) else e[0], 0 if e[1] else 1))
    return out[:MAX_EVENTS]


def fetch_events_span(start, end):
    """[start, end) 구간과 겹치는 일정을 기간 정보까지 담아 반환합니다.

    반환: [{'s': date, 'e': date(포함), 'allday': bool, 'tm': datetime|None, 'title': str}, ...]
    """
    if not ICAL_URL:
        return []
    import requests
    import icalendar
    import recurring_ical_events

    r = requests.get(ICAL_URL, timeout=30)
    r.raise_for_status()
    cal = icalendar.Calendar.from_ical(r.text)
    occ = recurring_ical_events.of(cal).between(start, end)

    out = []
    for ev in occ:
        s = ev["DTSTART"].dt
        allday = not isinstance(s, dt.datetime)

        e = ev.get("DTEND")
        e = e.dt if e is not None else s

        if allday:
            sd = s
            # 종일 일정의 DTEND는 끝나는 다음 날이라 하루를 뺍니다.
            ed = (e - dt.timedelta(days=1)) if isinstance(e, dt.date) and e > s else s
            tm = None
        else:
            s = s.astimezone(TZ) if s.tzinfo else s.replace(tzinfo=TZ)
            if isinstance(e, dt.datetime):
                e = e.astimezone(TZ) if e.tzinfo else e.replace(tzinfo=TZ)
            else:
                e = s
            sd, ed, tm = s.date(), e.date(), s

        out.append({"s": sd, "e": max(sd, ed), "allday": allday, "tm": tm,
                    "title": str(ev.get("SUMMARY", "(제목 없음)"))})

    out.sort(key=lambda x: (x["s"], not x["allday"], -( (x["e"] - x["s"]).days ),
                            x["tm"] or dt.datetime.min.replace(tzinfo=TZ)))
    return out


def fetch_events_range(start, end):
    """[start, end) 구간의 일정을 날짜별 딕셔너리로 반환합니다."""
    if not ICAL_URL:
        return {}
    import requests
    import icalendar
    import recurring_ical_events

    r = requests.get(ICAL_URL, timeout=30)
    r.raise_for_status()
    cal = icalendar.Calendar.from_ical(r.text)
    occ = recurring_ical_events.of(cal).between(start, end)

    by_date = {}
    for ev in occ:
        s = ev["DTSTART"].dt
        allday = not isinstance(s, dt.datetime)
        if not allday:
            s = s.astimezone(TZ) if s.tzinfo else s.replace(tzinfo=TZ)
        key = s.date() if isinstance(s, dt.datetime) else s
        by_date.setdefault(key, []).append(
            (None if allday else s, str(ev.get("SUMMARY", "(제목 없음)")))
        )
    for k in by_date:
        by_date[k].sort(key=lambda t: (t[0] is not None, t[0] or dt.datetime.min.replace(tzinfo=TZ)))
    return by_date


def sample_events():
    now = dt.datetime.now(TZ)
    d = now.date()
    return [
        (d, True, "샘플 종일 일정"),
        (now.replace(hour=14, minute=0, second=0, microsecond=0), False, "치과 진료 예약"),
        (d + dt.timedelta(days=1), True, "재활용 배출일"),
        (now.replace(hour=10, minute=30) + dt.timedelta(days=2), False, "팀 회의"),
    ]


# ------------------------------------------------------------------ 레이아웃

def truncate(d, text, f, max_w):
    if d.textlength(text, font=f) <= max_w:
        return text
    while text and d.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text + "…"


def render(events, wx):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_date = font(52)
    f_wd = font(26)
    f_temp = font(46)
    f_mid = font(22)
    f_day = font(22)
    f_ev = font(23)
    f_sm = font(17)

    now = dt.datetime.now(TZ)
    today = now.date()

    # ---- 헤더 --------------------------------------------------------
    d.rectangle([0, 0, W, 84], fill=BLUE)
    d.text((26, 12), now.strftime("%m/%d"), font=f_date, fill=WHITE)
    wd = WEEKDAY_KO[today.weekday()]
    wd_col = RED if today.weekday() == 6 else (YELLOW if today.weekday() == 5 else WHITE)
    d.text((186, 30), f"{wd}요일", font=f_wd, fill=wd_col)
    d.text((W - 26, 32), now.strftime("%Y년 %m월"), font=f_wd, fill=WHITE, anchor="ra")

    # ---- 날씨 --------------------------------------------------------
    top = 96
    if wx:
        desc, icon = WMO.get(wx["code"], ("—", "cloud"))
        draw_icon(d, icon, 62, top + 38, 30)
        d.text((116, top + 8), f"{wx['temp']}°", font=f_temp, fill=BLACK)
        d.text((118, top + 62), f"{PLACE} · {desc}", font=f_sm, fill=BLACK)

        x = 300
        for day in wx["days"][1:4]:
            _, ic = WMO.get(day["code"], ("—", "cloud"))
            lab = WEEKDAY_KO[day["date"].weekday()]
            lab_col = RED if day["date"].weekday() == 6 else BLACK
            d.text((x + 55, top - 4), lab, font=f_mid, fill=lab_col, anchor="ma")
            draw_icon(d, ic, x + 55, top + 46, 20)
            d.text((x + 55, top + 70), f"{day['hi']}° / {day['lo']}°",
                   font=f_sm, fill=BLACK, anchor="ma")
            if day["pop"] >= 40:
                d.text((x + 55, top + 90), f"{day['pop']}%", font=f_sm, fill=BLUE, anchor="ma")
            x += 165
    else:
        d.text((26, top + 30), "날씨 정보를 가져오지 못했습니다", font=f_mid, fill=BLACK)

    d.line([26, 210, W - 26, 210], fill=BLACK, width=2)

    # ---- 일정 --------------------------------------------------------
    y = 226
    if not events:
        d.text((W // 2, 300), "예정된 일정이 없습니다", font=f_mid, fill=BLACK, anchor="mm")

    prev = None
    for start, allday, title in events:
        if y > H - 74:
            break
        sd = start.date() if isinstance(start, dt.datetime) else start
        if sd != prev:
            if prev is not None:
                y += 6
                d.line([26, y, W - 26, y], fill=BLACK, width=1)
                y += 10
            delta = (sd - today).days
            if delta == 0:
                lab, col = "오늘", RED
            elif delta == 1:
                lab, col = "내일", BLACK
            else:
                lab, col = f"{sd.month}/{sd.day} ({WEEKDAY_KO[sd.weekday()]})", BLACK
            d.text((26, y + 2), lab, font=f_day, fill=col)
            prev = sd

        d.text((150, y + 2), "종일" if allday else start.strftime("%H:%M"), font=f_ev, fill=BLACK)
        d.text((240, y + 2), truncate(d, title, f_ev, W - 26 - 240), font=f_ev, fill=BLACK)
        y += 36

    # ---- 푸터 --------------------------------------------------------
    d.line([26, H - 38, W - 26, H - 38], fill=BLACK, width=1)
    d.text((26, H - 30), "갱신 " + now.strftime("%m-%d %H:%M"), font=f_sm, fill=BLACK)
    return img


# ------------------------------------------------- 월간 격자 레이아웃

def month_matrix(year, month):
    """해당 달을 월요일 시작 6주 격자로 만듭니다. 앞뒤 달 날짜도 채웁니다."""
    first = dt.date(year, month, 1)
    start = first - dt.timedelta(days=first.weekday())      # 그 주 월요일
    return [[start + dt.timedelta(days=r * 7 + c) for c in range(7)] for r in range(6)]


def render_month(by_date, wx):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_title = font(30)
    f_temp = font(28)
    f_wd = font(18)
    f_num = font(20)
    f_ev = font(13)
    f_sm = font(15)

    now = dt.datetime.now(TZ)
    today = now.date()

    # ---- 헤더 (흰 바탕, 갱신 부담이 적습니다) --------------------------
    d.text((24, 12), now.strftime("%Y년 %-m월"), font=f_title, fill=BLACK)

    if wx:
        desc, icon = WMO.get(wx["code"], ("—", "cloud"))
        draw_icon(d, icon, 486, 30, 22)
        d.text((520, 8), f"{wx['temp']}°", font=f_temp, fill=BLACK)
        d.text((520, 40), desc, font=f_sm, fill=BLACK)
        x = 610
        for day in wx["days"][1:3]:
            _, ic = WMO.get(day["code"], ("—", "cloud"))
            lab = WEEKDAY_KO[day["date"].weekday()]
            d.text((x + 42, 4), lab, font=f_sm, fill=BLACK, anchor="ma")
            draw_icon(d, ic, x + 42, 34, 15)
            d.text((x + 42, 50), f"{day['hi']}°/{day['lo']}°", font=f_sm, fill=BLACK, anchor="ma")
            x += 92
    d.line([24, 70, W - 24, 70], fill=BLACK, width=2)

    # ---- 요일 머리글 --------------------------------------------------
    left, right = 20, W - 20
    cw = (right - left) / 7
    for c in range(7):
        col = RED if c == 6 else (BLUE if c == 5 else BLACK)
        d.text((left + cw * (c + 0.5), 78), WEEKDAY_KO[c], font=f_wd, fill=col, anchor="ma")

    # ---- 격자 ---------------------------------------------------------
    top, bottom = 104, 452
    rows = 6
    ch = (bottom - top) / rows
    grid = month_matrix(today.year, today.month)

    for r in range(rows + 1):
        y = top + ch * r
        d.line([left, y, right, y], fill=BLACK, width=1)
    for c in range(8):
        x = left + cw * c
        d.line([x, top, x, bottom], fill=BLACK, width=1)

    for r, week in enumerate(grid):
        for c, day in enumerate(week):
            x0, y0 = left + cw * c, top + ch * r
            other = day.month != today.month

            if day == today:
                d.rectangle([x0 + 1, y0 + 1, x0 + cw - 1, y0 + ch - 1], fill=TODAY_HIGHLIGHT)

            if other:
                num_col = BLACK           # 다른 달은 작게, 색 강조 없이
                nf = f_ev
            else:
                num_col = RED if c == 6 else (BLUE if c == 5 else BLACK)
                nf = f_num
            d.text((x0 + 6, y0 + 3), str(day.day), font=nf, fill=num_col)

            if other:
                continue

            items = by_date.get(day, [])
            if not items:
                continue

            # 셀 높이가 58px뿐이라 일정 줄은 최대 2줄입니다.
            # 3건 이상이면 1건만 보여주고 나머지는 건수로 접습니다.
            if len(items) <= 2:
                lines = [((tm.strftime("%H:%M ") if tm else "") + title, BLACK)
                         for tm, title in items]
            else:
                tm, title = items[0]
                lines = [((tm.strftime("%H:%M ") if tm else "") + title, BLACK),
                         (f"외 {len(items) - 1}건", RED)]

            ey = y0 + 26
            for label, col in lines:
                while label and d.textlength(label, font=f_ev) > cw - 12:
                    label = label[:-1]
                d.text((x0 + 6, ey), label, font=f_ev, fill=col)
                ey += 16

    # ---- 푸터 ---------------------------------------------------------
    todays = by_date.get(today, [])
    if todays:
        tm, title = todays[0]
        line = "오늘  " + (tm.strftime("%H:%M ") if tm else "") + title
        if len(todays) > 1:
            line += f"  외 {len(todays) - 1}건"
    else:
        line = "오늘 일정 없음"
    d.text((24, H - 24), line, font=f_sm, fill=BLACK)
    d.text((W - 24, H - 24), now.strftime("갱신 %m-%d %H:%M"), font=f_sm, fill=BLACK, anchor="ra")
    return img


# ------------------------------------------- 2주 / 3주 격자 레이아웃

def render_weeks(by_date, wx, weeks=2):
    """이번 주 월요일부터 N주치를 격자로 그립니다. 행이 적을수록 칸이 커집니다."""
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    big = weeks <= 2
    f_title = font(28)
    f_temp = font(28)
    f_wd = font(18)
    f_num = font(22 if big else 20)
    f_ev = font(16 if big else 14)
    f_sm = font(15)

    now = dt.datetime.now(TZ)
    today = now.date()
    start = today - dt.timedelta(days=today.weekday())
    last = start + dt.timedelta(days=weeks * 7 - 1)

    # ---- 헤더 ---------------------------------------------------------
    title = f"{start.month}월 {start.day}일 – {last.month}월 {last.day}일"
    d.text((24, 14), title, font=f_title, fill=BLACK)

    if wx:
        desc, icon = WMO.get(wx["code"], ("—", "cloud"))
        draw_icon(d, icon, 486, 30, 22)
        d.text((520, 8), f"{wx['temp']}°", font=f_temp, fill=BLACK)
        d.text((520, 40), desc, font=f_sm, fill=BLACK)
        x = 610
        for day in wx["days"][1:3]:
            _, ic = WMO.get(day["code"], ("—", "cloud"))
            d.text((x + 42, 4), WEEKDAY_KO[day["date"].weekday()], font=f_sm, fill=BLACK, anchor="ma")
            draw_icon(d, ic, x + 42, 34, 15)
            d.text((x + 42, 50), f"{day['hi']}°/{day['lo']}°", font=f_sm, fill=BLACK, anchor="ma")
            x += 92
    d.line([24, 70, W - 24, 70], fill=BLACK, width=2)

    # ---- 요일 머리글 --------------------------------------------------
    left, right = 20, W - 20
    cw = (right - left) / 7
    for c in range(7):
        col = RED if c == 6 else (BLUE if c == 5 else BLACK)
        d.text((left + cw * (c + 0.5), 78), WEEKDAY_KO[c], font=f_wd, fill=col, anchor="ma")

    # ---- 격자 ---------------------------------------------------------
    top, bottom = 104, 452
    ch = (bottom - top) / weeks
    lh = 20 if big else 17
    head = 30 if big else 26
    capacity = max(1, int((ch - head - 4) / lh))     # 칸에 들어가는 일정 줄 수

    for r in range(weeks + 1):
        y = top + ch * r
        d.line([left, y, right, y], fill=BLACK, width=1)
    for c in range(8):
        x = left + cw * c
        d.line([x, top, x, bottom], fill=BLACK, width=1)

    for r in range(weeks):
        for c in range(7):
            day = start + dt.timedelta(days=r * 7 + c)
            x0, y0 = left + cw * c, top + ch * r

            if day == today:
                d.rectangle([x0 + 1, y0 + 1, x0 + cw - 1, y0 + ch - 1], fill=TODAY_HIGHLIGHT)

            num_col = RED if c == 6 else (BLUE if c == 5 else BLACK)
            label = f"{day.month}/{day.day}" if day.day == 1 else str(day.day)
            d.text((x0 + 7, y0 + 4), label, font=f_num, fill=num_col)

            items = by_date.get(day, [])
            if not items:
                continue

            if len(items) <= capacity:
                lines = [((tm.strftime("%H:%M ") if tm else "") + t, BLACK) for tm, t in items]
            else:
                lines = [((tm.strftime("%H:%M ") if tm else "") + t, BLACK)
                         for tm, t in items[:capacity - 1]]
                lines.append((f"외 {len(items) - capacity + 1}건", RED))

            ey = y0 + head
            for text, col in lines:
                s = text
                while s and d.textlength(s, font=f_ev) > cw - 12:
                    s = s[:-1]
                d.text((x0 + 7, ey), s, font=f_ev, fill=col)
                ey += lh

    # ---- 푸터 ---------------------------------------------------------
    todays = by_date.get(today, [])
    if todays:
        tm, t = todays[0]
        line = "오늘  " + (tm.strftime("%H:%M ") if tm else "") + t
        if len(todays) > 1:
            line += f"  외 {len(todays) - 1}건"
    else:
        line = "오늘 일정 없음"
    d.text((24, H - 24), line, font=f_sm, fill=BLACK)
    d.text((W - 24, H - 24), now.strftime("갱신 %m-%d %H:%M"), font=f_sm, fill=BLACK, anchor="ra")
    return img


# -------------------------------------- 막대형 주간 격자 (첨부 스타일)

# E6 팔레트에 없는 색(회색 등)을 채우면 디더링되어 노이즈가 됩니다.
# 면을 채울 때는 반드시 팔레트의 6색만 씁니다.


def _assign_lanes(spans):
    """겹치지 않는 막대끼리 같은 줄(lane)에 모읍니다."""
    lanes = []
    for sp in spans:
        for i, lane in enumerate(lanes):
            if all(sp["c1"] < o["c0"] or sp["c0"] > o["c1"] for o in lane):
                lane.append(sp)
                sp["lane"] = i
                break
        else:
            sp["lane"] = len(lanes)
            lanes.append([sp])
    return len(lanes)


def render_bars(events, wx, weeks=3):
    """이번 주 월요일부터 N주. 종일·다일 일정은 가로 막대, 시간 일정은 점 목록."""
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_title = font(26)
    f_temp = font(26)
    f_wd = font(16)
    f_num = font(19)
    f_bar = font(14)
    f_ev = font(14)
    f_sm = font(14)

    now = dt.datetime.now(TZ)
    today = now.date()
    start = today - dt.timedelta(days=today.weekday())
    last = start + dt.timedelta(days=weeks * 7 - 1)

    # ---- 헤더 ---------------------------------------------------------
    d.text((22, 8), f"{start.month}월 {start.day}일 – {last.month}월 {last.day}일",
           font=f_title, fill=BLACK)
    if wx:
        desc, icon = WMO.get(wx["code"], ("—", "cloud"))
        draw_icon(d, icon, 494, 24, 18)
        d.text((524, 3), f"{wx['temp']}°", font=f_temp, fill=BLACK)
        d.text((524, 30), desc, font=f_sm, fill=BLACK)
        x = 618
        for day in wx["days"][1:3]:
            _, ic = WMO.get(day["code"], ("—", "cloud"))
            d.text((x + 40, 0), WEEKDAY_KO[day["date"].weekday()], font=f_sm, fill=BLACK, anchor="ma")
            draw_icon(d, ic, x + 40, 26, 13)
            d.text((x + 40, 38), f"{day['hi']}°/{day['lo']}°", font=f_sm, fill=BLACK, anchor="ma")
            x += 88

    left, right = 16, W - 16
    cw = (right - left) / 7

    # ---- 요일 머리글 (연한 띠) -----------------------------------------
    hy0, hy1 = 58, 84
    for c in range(7):
        col = RED if c == 6 else (BLUE if c == 5 else BLACK)
        d.text((left + cw * (c + 0.5), hy0 + 5), WEEKDAY_KO[c], font=f_wd, fill=col, anchor="ma")
    d.line([left, hy0 - 2, right, hy0 - 2], fill=BLACK, width=1)

    # ---- 격자 ---------------------------------------------------------
    top, bottom = hy1, 458
    ch = (bottom - top) / weeks
    num_h = 34        # 날짜 번호 아래 여백
    bar_h = 20
    bar_gap = 2
    line_h = 19       # 일정 줄 간격

    for c in range(8):
        x = left + cw * c
        d.line([x, top, x, bottom], fill=BLACK, width=1)
    for r in range(weeks + 1):
        y = top + ch * r
        d.line([left, y, right, y], fill=BLACK, width=2 if r in (0, weeks) else 1)

    for r in range(weeks):
        wk0 = start + dt.timedelta(days=r * 7)
        wk1 = wk0 + dt.timedelta(days=6)
        y0 = top + ch * r

        # 오늘 칸 표시
        if wk0 <= today <= wk1:
            c = (today - wk0).days
            d.rectangle([left + cw * c + 1, y0 + 1, left + cw * (c + 1) - 1, y0 + ch - 1],
                        fill=TODAY_HIGHLIGHT)

        # 날짜 번호 (오른쪽 정렬)
        for c in range(7):
            day = wk0 + dt.timedelta(days=c)
            col = RED if c == 6 else (BLUE if c == 5 else BLACK)
            lab = f"{day.month}/{day.day}" if day.day == 1 else str(day.day)
            d.text((left + cw * c + 8, y0 + 4), lab, font=f_num, fill=col)

        # --- 이 주와 겹치는 종일/다일 일정을 막대로 ---------------------
        spans = []
        for ev in events:
            if ev["allday"] and ev["e"] >= wk0 and ev["s"] <= wk1:
                spans.append({"c0": max(0, (ev["s"] - wk0).days),
                              "c1": min(6, (ev["e"] - wk0).days),
                              "title": ev["title"],
                              "multi": ev["e"] > ev["s"]})
        spans.sort(key=lambda s: (s["c0"], -(s["c1"] - s["c0"])))
        nlanes = _assign_lanes(spans)

        max_lanes = 2
        for sp in spans:
            if sp["lane"] >= max_lanes:
                continue
            bx0 = left + cw * sp["c0"] + 3
            bx1 = left + cw * (sp["c1"] + 1) - 3
            by0 = y0 + num_h + sp["lane"] * (bar_h + bar_gap)
            if sp["multi"]:
                d.rounded_rectangle([bx0, by0, bx1, by0 + bar_h], radius=3, fill=BLUE)
                txt = WHITE
            else:
                d.rounded_rectangle([bx0, by0, bx1, by0 + bar_h], radius=3,
                                    fill=WHITE, outline=BLACK, width=1)
                txt = BLACK
            s = sp["title"]
            while s and d.textlength(s, font=f_bar) > (bx1 - bx0) - 10:
                s = s[:-1]
            d.text((bx0 + 5, by0 + 2), s, font=f_bar, fill=txt)

        used = min(nlanes, max_lanes)
        base = y0 + num_h + used * (bar_h + bar_gap) + (3 if used else 0)
        cap = max(1, int((ch - (base - y0) - 4) / line_h))

        # --- 시간 일정은 점 목록 ---------------------------------------
        for c in range(7):
            day = wk0 + dt.timedelta(days=c)
            timed = [e for e in events if not e["allday"] and e["s"] == day]
            if not timed:
                continue
            x0 = left + cw * c
            ey = base
            if len(timed) <= cap:
                show, tail = timed, 0
            elif cap >= 2:
                show, tail = timed[:cap - 1], len(timed) - (cap - 1)
            else:
                # 줄이 하나뿐이면 첫 일정을 보여주고 남은 건수를 같은 줄에 붙인다
                show, tail = timed[:1], -(len(timed) - 1)

            for i, ev in enumerate(show):
                d.ellipse([x0 + 7, ey + 7, x0 + 12, ey + 12], fill=BLACK)
                label = ev["title"]
                if tail < 0 and i == len(show) - 1:
                    label += f" +{-tail}"
                while label and d.textlength(label, font=f_ev) > cw - 24:
                    label = label[:-1]
                d.text((x0 + 17, ey + 2), label, font=f_ev, fill=BLACK)
                ey += line_h
            if tail > 0:
                d.text((x0 + 17, ey + 2), f"외 {tail}건", font=f_ev, fill=RED)

    # ---- 푸터 ---------------------------------------------------------
    todays = [e for e in events if e["s"] <= today <= e["e"]]
    if todays:
        e0 = todays[0]
        line = "오늘  " + (e0["tm"].strftime("%H:%M ") if e0["tm"] else "") + e0["title"]
        if len(todays) > 1:
            line += f"  외 {len(todays) - 1}건"
    else:
        line = "오늘 일정 없음"
    d.text((22, H - 19), line, font=f_sm, fill=BLACK)
    d.text((W - 22, H - 19), now.strftime("갱신 %m-%d %H:%M"), font=f_sm, fill=BLACK, anchor="ra")
    return img


# ------------------------------------------------------------------ 변환

def to_e6_4bpp(img):
    pal = Image.new("P", (1, 1))
    flat = []
    for r, g, b, _ in E6:
        flat += [r, g, b]
    flat += [0] * (768 - len(flat))
    pal.putpalette(flat)

    q = img.quantize(palette=pal, dither=Image.FLOYDSTEINBERG)
    lut = bytes(E6[i][3] if i < len(E6) else 0x0 for i in range(256))
    codes = q.tobytes().translate(lut)

    out = bytearray(W * H // 2)
    for yy in range(H):
        row = codes[yy * W:(yy + 1) * W]
        base = yy * (W // 2)
        for xx in range(0, W, 2):
            out[base + (xx >> 1)] = (row[xx] << 4) | row[xx + 1]
    return bytes(out), q.convert("RGB")


def main():
    layout = os.environ.get("LAYOUT", "list").strip().lower()
    wx = fetch_weather()

    if layout in ("bars", "bars2", "bars4"):
        weeks = {"bars": 3, "bars2": 2, "bars4": 4}[layout]
        today = dt.datetime.now(TZ).date()
        start = today - dt.timedelta(days=today.weekday())
        try:
            evs = fetch_events_span(start, start + dt.timedelta(days=weeks * 7))
        except Exception as e:
            print("[err] 일정 조회 실패:", e, file=sys.stderr)
            evs = []
        img = render_bars(evs, wx, weeks)
        n = len(evs)
    elif layout in ("week2", "week3"):
        weeks = 2 if layout == "week2" else 3
        today = dt.datetime.now(TZ).date()
        start = today - dt.timedelta(days=today.weekday())
        try:
            by_date = fetch_events_range(start, start + dt.timedelta(days=weeks * 7))
        except Exception as e:
            print("[err] 일정 조회 실패:", e, file=sys.stderr)
            by_date = {}
        img = render_weeks(by_date, wx, weeks)
        n = sum(len(v) for v in by_date.values())
    elif layout == "month":
        today = dt.datetime.now(TZ).date()
        grid = month_matrix(today.year, today.month)
        try:
            by_date = fetch_events_range(grid[0][0], grid[-1][-1] + dt.timedelta(days=1))
        except Exception as e:
            print("[err] 일정 조회 실패:", e, file=sys.stderr)
            by_date = {}
        img = render_month(by_date, wx)
        n = sum(len(v) for v in by_date.values())
    else:
        try:
            events = fetch_events()
        except Exception as e:
            print("[err] 일정 조회 실패:", e, file=sys.stderr)
            events = []
        img = render(events, wx)
        n = len(events)
    data, preview = to_e6_4bpp(img)

    os.makedirs("out", exist_ok=True)
    with open("out/cal.bin", "wb") as f:
        f.write(data)
    preview.save("out/cal.png")
    print(f"[ok] {layout} 레이아웃, 일정 {n}건, 날씨 {'있음' if wx else '없음'}, {len(data)} 바이트")


if __name__ == "__main__":
    main()
