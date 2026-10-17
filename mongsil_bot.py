"""몽실군단 자동 전투 봇 (LDPlayer + adb).

전투 화면: 늑대가 x 530(성문 앞에 닿기 4초 전)까지 오면 그때부터 6번 칸(천공 거신 시바견)을 누른다. 당근이 모자라면 게임이 무시한다.
로비 화면: '출전하기' 를 눌러 다음 전투를 시작한다.
결과 화면: 패배면 '다시 도전', 승리면 '다음 성으로' 를 누른다.
모르는 화면이 STUCK_SEC 초 넘게 이어지면 화면을 저장하고 멈춘다.
"""
import io
import os
import subprocess
import sys
import time
from datetime import datetime

from PIL import Image

ADB = os.environ.get("LDPLAYER_ADB", r"C:\LDPlayer\LDPlayer14\adb.exe")
DEVICE = os.environ.get("LDPLAYER_DEVICE", "emulator-5554")
LOOP_SEC = 1.0
STUCK_SEC = 15
BURST_TAPS = 5  # 화면을 한 번 찍을 때마다 누르는 횟수
# 결과 창의 오른쪽 버튼('다시 도전'/'다음 성으로') 자리. 이 안에 있는 카드를 여러 번 누르면
# 그사이 뜬 결과 창의 버튼을 누르고 새 판에서 바로 소환해 버리니, 그런 카드는 1번씩만 누른다.
RESULT_BUTTON_BOX = (655, 555, 995, 610)
FIRST_BURST_TAPS = 20  # 소환을 시작하는 순간 모아 둔 당근을 다 쓰도록 누르는 횟수
BURST_GAP_SEC = 0.1

# 화면 좌표는 1280x720 가로 기준
CARDS = [(1165, 590)]  # 6번 칸 천공 거신 시바견만 (특수 당근 1개. 보통 당근 100개가 자동으로 특수 당근 1개가 된다)
SORTIE_BUTTON = (353, 478)

# (좌표, 기대 색) 이 모두 맞으면 그 화면으로 본다
BATTLE_MARKS = [((30, 570), (33, 41, 64)), ((160, 690), (33, 41, 64))]
LOBBY_MARKS = [((130, 478), (19, 71, 122)), ((580, 478), (41, 111, 163))]
# 승리/패배 결과 창: 아래 두 버튼 자리가 같다. 패배 창에만 청록 부활 버튼이 있다.
RESULT_MARKS = [((700, 582), (56, 99, 143)), ((380, 582), (31, 61, 94))]
REVIVE_MARK = [((900, 450), (43, 112, 110))]
RESULT_RIGHT_BUTTON = (824, 582)  # 패배: 다시 도전, 승리: 다음 성으로
TOLERANCE = 18

# 적 체력 막대(빨강)가 이 x 보다 왼쪽에 오면 그 전투에서는 계속 소환한다.
# 우리 성문 바로 앞. 이보다 왼쪽(x 170 안쪽)은 성벽이라 늑대를 읽을 수 없다.
# 천공 거신은 특수 당근이 없으면 눌러도 아무 일이 없으니, 늑대를 기다리지 않고 전투 내내 누른다.
WAIT_FOR_WOLVES = False
TRIGGER_X = 530  # 성문 앞(x 230)보다 4초 일찍. 늑대는 1초에 75px 쯤 온다
ENEMY_BAR = (255, 84, 82)
LANE = (170, 290, 1100, 345)  # 땅 위 늑대만 본다. 하늘의 적(y 150 근처)과 양쪽 성 깃발은 뺀다


# 속도 버튼의 '×1.5' 중 '5' 자리 흰 글자 모양. 모양이 바뀌면(×1.0 등) 버튼을 누른다.
# 3판에 한 번, 늑대가 오기 전에 성장 창을 열어 '생산 속도' 강화를 눌러 본다. '기본 수량 +1' 은 사지 않는다(사용자 지시).
# 골드가 모자라면 게임이 사 주지 않으니 금액은 읽지 않는다.
GROWTH_OPEN_BUTTON = (1088, 38)  # 전투 화면의 '성장 · 유닛 구매'
GROWTH_MARKS = [((1160, 48), (43, 61, 94)), ((100, 114), (51, 107, 153)), ((640, 200), (19, 33, 60))]
PRODUCTION_UPGRADE_BUTTON = (636, 545)
GROWTH_CLOSE_BUTTON = (1186, 48)
UPGRADE_TAPS = 10  # 강화마다 누르는 횟수. 골드가 모자라면 게임이 무시한다
UPGRADE_EVERY_BATTLES = 3  # 성장 창은 3판에 한 번만 연다


def try_upgrade_production():
    tap(GROWTH_OPEN_BUTTON)
    time.sleep(1)
    if not matches(screenshot(), GROWTH_MARKS):
        return False
    for _ in range(UPGRADE_TAPS):
        tap(PRODUCTION_UPGRADE_BUTTON)
        time.sleep(0.25)
    tap(GROWTH_CLOSE_BUTTON)
    time.sleep(1)
    return True


# '골드 ×2' 버튼. 켜져 있으면 위 줄에 '골드 ×2', 아래 줄에 남은 시간이 뜬다.
# 꺼져 있으면 가운데 한 줄 '골드 ×2 보상' 이라 위 줄(y 22 → 34)의 흰 글자가 적다.
GOLD_BUTTON = (520, 38)
GOLD_TOP_ROW = (495, 22, 590, 34)
GOLD_ON_MIN_WHITE = 70  # 켜짐 134, 꺼짐 25 쯤


def gold_boost_off(im):
    x0, y0, x1, y1 = GOLD_TOP_ROW
    white = sum(min(im.getpixel((x, y))) > 170 for x in range(x0, x1) for y in range(y0, y1))
    return white < GOLD_ON_MIN_WHITE


SPEED_BUTTON = (933, 38)
# 속도 버튼을 누르면 뜨는 '15분 동안 게임 속도 ×1.5' 무료 보상 창
SPEED_REWARD_MARKS = [
    ((700, 500), (48, 110, 130)), ((950, 540), (48, 110, 130)),
    ((300, 500), (38, 51, 74)), ((640, 580), (17, 27, 47)),
]
SPEED_REWARD_BUTTON = (776, 522)  # 무료 보상 받기
SPEED_DIGIT_BOX = (958, 24, 972, 35)
SPEED_DIGIT_REF = [
    ".....#####....",
    ".....#........",
    "....#.........",
    "....##........",
    ".....#####....",
    ".........#....",
    ".........#....",
    "..............",
    ".........##...",
    "....##..###...",
    ".##.#####.....",
]
SPEED_DIFF_LIMIT = 12
SPEED_RETRY_SEC = 30


def speed_digit_changed(im):
    x0, y0, x1, y1 = SPEED_DIGIT_BOX
    diff = 0
    for row, y in zip(SPEED_DIGIT_REF, range(y0, y1)):
        for ch, x in zip(row, range(x0, x1)):
            white = min(im.getpixel((x, y))) > 170
            diff += white != (ch == "#")
    return diff > SPEED_DIFF_LIMIT


# 성 체력 막대. 한쪽 막대가 바닥났다가 다시 꽉 차면 새 판으로 본다.
# 카드 연타가 결과 창의 '다시 도전' 버튼을 눌러 결과 창을 못 보고 넘어가는 일이 있어서 필요하다.
OUR_BAR = ((30, 300), 103, (94, 194, 255))
ENEMY_BAR_TOP = ((978, 1250), 103, (255, 97, 97))


def bar_fill(im, bar):
    (x0, x1), y, color = bar
    xs = range(x0, x1, 5)
    hit = sum(all(abs(a - b) <= TOLERANCE for a, b in zip(im.getpixel((x, y)), color)) for x in xs)
    return hit / len(xs)


OUR_UNIT_BAR = (125, 242, 112)  # 우리 유닛 머리 위 초록 체력 막대
UNIT_AREA = (130, 140, 1150, 345)  # 땅과 하늘 모두


def our_units_present(im):
    x0, y0, x1, y1 = UNIT_AREA
    for x in range(x0, x1, 3):
        for y in range(y0, y1, 2):
            if all(abs(a - b) <= 25 for a, b in zip(im.getpixel((x, y)), OUR_UNIT_BAR)):
                return True
    return False


def leftmost_enemy_x(im):
    x0, y0, x1, y1 = LANE
    for x in range(x0, x1, 3):
        for y in range(y0, y1, 2):
            if im.getpixel((x, y)) == ENEMY_BAR:
                return x
    return None

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


def adb(*args, capture=False):
    cmd = [ADB, "-s", DEVICE, *args]
    for attempt in range(3):  # adb 는 가끔 한 번씩 실패하니 몇 번 더 해 본다
        try:
            if capture:
                return subprocess.run(cmd, capture_output=True, check=True).stdout
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(1)


def screenshot():
    png = adb("exec-out", "screencap", "-p", capture=True)
    return Image.open(io.BytesIO(png)).convert("RGB")


def tap(xy):
    adb("shell", "input", "tap", str(xy[0]), str(xy[1]))


def matches(im, marks):
    for (x, y), want in marks:
        got = im.getpixel((x, y))
        if any(abs(g - w) > TOLERANCE for g, w in zip(got, want)):
            return False
    return True


def main():
    battles = 0
    wins = 0
    last_speed_tap = 0.0
    last_gold_tap = 0.0
    unknown_since = None
    card_i = 0
    in_battle = False
    armed = False
    upgraded = False
    ended_by = None  # 'our' 또는 'enemy': 그 성 막대가 바닥난 것을 봤다
    print("봇 시작. 멈추려면 Ctrl+C")
    while True:
        im = screenshot()
        now = time.time()
        if matches(im, BATTLE_MARKS):
            unknown_since = None
            if speed_digit_changed(im) and now - last_speed_tap > SPEED_RETRY_SEC:
                last_speed_tap = now
                print(f"[{datetime.now():%H:%M:%S}] 속도 배율이 바뀜, 속도 버튼 누름")
                tap(SPEED_BUTTON)
                time.sleep(1)
                continue
            our, enemy = bar_fill(im, OUR_BAR), bar_fill(im, ENEMY_BAR_TOP)
            if our < 0.25:
                ended_by = "our"
            elif enemy < 0.25:
                ended_by = "enemy"
            elif ended_by and (our if ended_by == "our" else enemy) > 0.9:
                ended_by = None
                in_battle = False  # 결과 창을 못 보고 새 판이 시작됐다
            if (WAIT_FOR_WOLVES and armed and our > 0.9 and enemy > 0.9 and leftmost_enemy_x(im) is None
                    and not our_units_present(im)):
                # 늑대도 우리 유닛도 없고 양쪽 성이 꽉 찼다 = 판이 막 시작된 모습. 다음 늑대를 기다린다.
                # (판 중간에 늑대만 잠깐 없는 때는 싸우던 우리 유닛이 남아 있으니 계속 뽑는다)
                armed = False
                print(f"[{datetime.now():%H:%M:%S}] 늑대 없음, 소환 멈춤")
            if gold_boost_off(im) and now - last_gold_tap > SPEED_RETRY_SEC:
                last_gold_tap = now
                print(f"[{datetime.now():%H:%M:%S}] 골드 ×2 꺼짐, 골드 버튼 누름")
                tap(GOLD_BUTTON)
                time.sleep(1)
                continue
            if not in_battle:
                battles += 1
                in_battle = True
                armed = False
                upgraded = (battles - 1) % UPGRADE_EVERY_BATTLES != 0
                print(f"[{datetime.now():%H:%M:%S}] 전투 {battles} 시작")
            if not armed and not upgraded:
                upgraded = True
                ok = try_upgrade_production()
                print(f"[{datetime.now():%H:%M:%S}] 생산 속도 강화 시도 ({'창 열림' if ok else '창 안 열림'})")
                continue
            if not armed and not WAIT_FOR_WOLVES:
                armed = True
            if not armed:
                ex = leftmost_enemy_x(im)
                if ex is None or ex > TRIGGER_X:
                    time.sleep(LOOP_SEC / 3)
                    continue
                armed = True
                print(f"[{datetime.now():%H:%M:%S}] 늑대가 x={ex} 도착, 소환 시작")
                for k in range(FIRST_BURST_TAPS):
                    tap(CARDS[k % len(CARDS)])
                    time.sleep(BURST_GAP_SEC)
            # 소환을 시작했으면 당근이 남는 대로 최대한 빨리 누른다
            # 카드마다: 결과 창 버튼과 겹치면 1번, 아니면 BURST_TAPS 번
            bx0, by0, bx1, by1 = RESULT_BUTTON_BOX
            for card in CARDS:
                on_button = bx0 <= card[0] <= bx1 and by0 <= card[1] <= by1
                for _ in range(1 if on_button else BURST_TAPS):
                    tap(card)
                    time.sleep(BURST_GAP_SEC)
            continue
        # 안내 문구가 잠깐 가린 화면은 전투 끝이 아니다. 결과 창/로비에서만 끝으로 본다.
        if matches(im, SPEED_REWARD_MARKS):
            unknown_since = None
            print(f"[{datetime.now():%H:%M:%S}] 무료 보상 창(속도/골드), 무료 보상 받기")
            tap(SPEED_REWARD_BUTTON)
            time.sleep(2)
            continue
        if matches(im, RESULT_MARKS):
            unknown_since = None
            in_battle = False
            ended_by = None
            if matches(im, REVIVE_MARK):
                print(f"[{datetime.now():%H:%M:%S}] 패배, 다시 도전")
            else:
                wins += 1
                print(f"[{datetime.now():%H:%M:%S}] 승리 {wins}, 다음 성으로")
            tap(RESULT_RIGHT_BUTTON)
            time.sleep(3)
            continue
        if matches(im, LOBBY_MARKS):
            unknown_since = None
            in_battle = False
            ended_by = None
            tap(SORTIE_BUTTON)
            time.sleep(3)
            continue
        if unknown_since is None:
            unknown_since = now
        elif now - unknown_since > STUCK_SEC:
            os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, f"stuck_{datetime.now():%Y%m%d_%H%M%S}.png")
            im.save(path)
            msg = f"몽실군단 봇 멈춤: 모르는 화면 {STUCK_SEC}초. 전투 {battles}회 진행."
            print(msg, path)
            sys.exit(1)
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("멈춤")
    except Exception as e:
        msg = f"몽실군단 봇 오류로 멈춤: {type(e).__name__}"
        print(msg)
        raise
