import cv2
import time
import asyncio
import threading
import spidev
import RPi.GPIO as GPIO

from picamera2 import Picamera2
from bleak import BleakClient, BleakScanner


# BLE

HC05_ADDRESS = "C6:10:18:B0:6B:55"
FFE1_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

ble_client = None
ble_loop = None
ble_ready = False
ble_connecting = False
ble_stop_event = threading.Event()

rx_buffer = ""
last_stop_time = 0


# MAX7219

MAX7219_CS = 16

GPIO.setmode(GPIO.BCM)
GPIO.setup(MAX7219_CS, GPIO.OUT, initial=GPIO.HIGH)

spi = spidev.SpiDev()
spi.open(1, 0)
spi.max_speed_hz = 1000000
spi.mode = 0


def max7219_write(register, data):
    GPIO.output(MAX7219_CS, GPIO.LOW)
    spi.xfer2([register, data])
    GPIO.output(MAX7219_CS, GPIO.HIGH)


def max7219_init():
    max7219_write(0x09, 0x00)
    max7219_write(0x0A, 0x05)
    max7219_write(0x0B, 0x07)
    max7219_write(0x0C, 0x01)
    max7219_write(0x0F, 0x00)


def max7219_clear():
    for row in range(1, 9):
        max7219_write(row, 0x00)


def max7219_full():
    for row in range(1, 9):
        max7219_write(row, 0xFF)


def max7219_circle():
    circle = [
        0b00111100,
        0b01111110,
        0b11100111,
        0b11000011,
        0b11000011,
        0b11100111,
        0b01111110,
        0b00111100
    ]

    for row in range(8):
        max7219_write(row + 1, circle[row])


max7219_init()
max7219_clear()


# 정류장 및 버스 노선

graph = {
    "A": ["B", "C"],
    "B": ["A", "C", "D"],
    "C": ["A", "B", "D"],
    "D": ["B", "C"]
}

buses = {
    101: ["A", "B", "C"],
    202: ["A", "C", "D"],
    303: ["B", "D"]
}

bus_arrival_times = {
    101: 3,
    202: 5,
    303: 7
}

bus_remaining_times = dict(bus_arrival_times)

bus_time_lock = threading.Lock()
bus_timer_stop_event = threading.Event()


# 사용자 안내 상태

tracking_active = False
tracking_destination = None
tracking_bus_number = None

tracking_lock = threading.Lock()

current_station = "A"


# 시스템 상태

IDLE = 0
CONFIRMING = 1
ACTIVE = 2

state = IDLE

DETECTION_CONFIRM_TIME = 2.0
detection_start_time = None


# 버스 경로

def route_path(route, start, destination):
    if start not in route or destination not in route:
        return None

    start_index = route.index(start)
    destination_index = route.index(destination)

    if start_index <= destination_index:
        return route[start_index:destination_index + 1]

    return list(reversed(route[destination_index:start_index + 1]))


def find_bus(start, destination):
    best_bus = None
    best_path = None

    for bus_number, route in buses.items():
        path = route_path(route, start, destination)

        if path is None:
            continue

        if best_path is None or len(path) < len(best_path):
            best_bus = bus_number
            best_path = path

    return best_bus, best_path


# Raspberry Pi -> Arduino

def send_text(message):
    global ble_ready

    if not message.endswith("\n"):
        message += "\n"

    if not ble_ready or ble_client is None or ble_loop is None:
        print("BLE 연결 안 됨, 전송 보류 ->", repr(message))
        return False

    try:
        if not ble_client.is_connected:
            ble_ready = False
            print("BLE 연결 끊김, 전송 보류 ->", repr(message))
            return False

        future = asyncio.run_coroutine_threadsafe(
            ble_client.write_gatt_char(
                FFE1_UUID,
                message.encode("utf-8"),
                response=True
            ),
            ble_loop
        )

        future.result(timeout=5)

        print("Pi -> Arduino:", repr(message))
        return True

    except Exception as e:
        print("BLE 전송 오류:", repr(e))
        return False


def get_bus_remaining(bus_number):
    with bus_time_lock:
        return bus_remaining_times.get(bus_number, 0)


# 버스 타이머

def global_bus_timer():
    print()
    print("================================")
    print("전체 버스 타이머 시작")

    with bus_time_lock:
        for bus_number, remaining in bus_remaining_times.items():
            print(f"{bus_number}번 : {remaining}분")

    print("================================")

    while not bus_timer_stop_event.is_set():
        if bus_timer_stop_event.wait(60):
            break

        with bus_time_lock:
            for bus_number in bus_remaining_times:
                if bus_remaining_times[bus_number] > 0:
                    bus_remaining_times[bus_number] -= 1

            current_times = dict(bus_remaining_times)

        print()
        print("===== 현재 전체 버스 시간 =====")

        for bus_number, remaining in current_times.items():
            print(f"{bus_number}번 : {remaining}분")

        print("===============================")

        with tracking_lock:
            active = tracking_active
            tracked_bus = tracking_bus_number

        if active and tracked_bus is not None:
            remaining = current_times.get(tracked_bus, 0)

            print("현재 안내 버스 업데이트:", tracked_bus, remaining, "분")

            send_text(f"BUS:{tracked_bus},{remaining}")


bus_timer_thread = threading.Thread(
    target=global_bus_timer,
    daemon=True
)

bus_timer_thread.start()


# BLE 재연결 상태 복구

async def restore_arduino_state(client):
    with tracking_lock:
        active = tracking_active
        bus_number = tracking_bus_number
        destination = tracking_destination

    if active and bus_number is not None:
        remaining = get_bus_remaining(bus_number)
        message = f"BUS:{bus_number},{remaining}\n"

        try:
            await client.write_gatt_char(
                FFE1_UUID,
                message.encode("utf-8"),
                response=True
            )

            print()
            print("================================")
            print("BLE 재연결 상태 복구")
            print("목적지:", destination)
            print("버스:", bus_number)
            print("남은 시간:", remaining, "분")
            print("================================")

        except Exception as e:
            print("상태 복구 실패:", repr(e))

    elif state == ACTIVE:
        try:
            await client.write_gatt_char(
                FFE1_UUID,
                b"READY\n",
                response=True
            )

            print("BLE 재연결 -> READY 재전송")

        except Exception as e:
            print("READY 복구 실패:", repr(e))


def stop_tracking():
    global tracking_active
    global tracking_destination
    global tracking_bus_number

    with tracking_lock:
        tracking_active = False
        tracking_destination = None
        tracking_bus_number = None

    print("현재 사용자 버스 안내 종료")
    print("전체 버스 시간은 계속 흐릅니다.")


# 목적지 처리

process_lock = threading.Lock()


def process_destination(destination):
    global tracking_active
    global tracking_destination
    global tracking_bus_number

    destination = destination.strip().upper()

    print()
    print("================================")
    print("Arduino 목적지:", destination)
    print("현재 정류장:", current_station)
    print("================================")

    if state != ACTIVE:
        print("시스템이 아직 ACTIVE 상태가 아닙니다.")
        send_text("ERROR:NOT_ACTIVE")
        return

    if destination not in graph:
        send_text("ERROR:INVALID_DEST")
        return

    if destination == current_station:
        send_text("ERROR:SAME_DEST")
        return

    bus_number, path = find_bus(current_station, destination)

    if bus_number is None:
        print("해당 목적지로 가는 버스 없음")
        send_text("ERROR:NO_BUS")
        return

    remaining = get_bus_remaining(bus_number)

    with tracking_lock:
        tracking_active = True
        tracking_destination = destination
        tracking_bus_number = bus_number

    print()
    print("--------------------------------")
    print("현재 정류장 :", current_station)
    print("목적지      :", destination)
    print("경로        :", " -> ".join(path))
    print("버스 번호   :", bus_number)
    print("남은 시간   :", remaining, "분")
    print("--------------------------------")

    send_text(f"BUS:{bus_number},{remaining}")


def process_destination_safe(destination):
    with process_lock:
        process_destination(destination)


# Arduino -> Raspberry Pi

def ble_notification_handler(sender, data):
    global rx_buffer
    global last_stop_time

    try:
        rx_buffer += data.decode("utf-8", errors="ignore")

        while "\n" in rx_buffer:
            line, rx_buffer = rx_buffer.split("\n", 1)
            line = line.strip()

            if not line:
                continue

            print("Arduino -> Pi:", repr(line))

            if line.startswith("DEST:"):
                destination = line[5:].strip().upper()

                threading.Thread(
                    target=process_destination_safe,
                    args=(destination,),
                    daemon=True
                ).start()

            elif line == "STOP":
                now = time.monotonic()

                if now - last_stop_time < 1.0:
                    print("중복 STOP 무시")
                    continue

                last_stop_time = now

                print("Arduino -> STOP")
                stop_tracking()
                print("버스 안내 종료 -> 새 목적지 선택 가능")

    except Exception as e:
        print("BLE 수신 오류:", repr(e))


# BLE 연결

async def ble_connect_loop():
    global ble_client
    global ble_loop
    global ble_ready
    global ble_connecting
    global rx_buffer

    ble_loop = asyncio.get_running_loop()

    while not ble_stop_event.is_set():
        client = None

        try:
            ble_connecting = True

            print()
            print("================================")
            print("HC-05 검색 중...")
            print("================================")

            device = await BleakScanner.find_device_by_address(
                HC05_ADDRESS,
                timeout=10.0
            )

            if device is None:
                print("HC-05를 찾지 못했습니다.")
                await asyncio.sleep(3)
                continue

            print("장치 발견:", device.name, device.address)
            print("BLE 연결 시도...")

            client = BleakClient(device)
            await client.connect()

            if not client.is_connected:
                raise RuntimeError("BLE 연결 실패")

            ble_client = client
            ble_ready = True
            ble_connecting = False
            rx_buffer = ""

            print("BLE 연결 성공!")
            print("연결 상태:", client.is_connected)

            await client.start_notify(
                FFE1_UUID,
                ble_notification_handler
            )

            print("FFE1 notification 활성화")

            await restore_arduino_state(client)

            while not ble_stop_event.is_set() and client.is_connected:
                await asyncio.sleep(1)

            print()
            print("HC-05 연결 끊김")
            print("버스 시간과 사용자 상태는 계속 유지합니다.")

        except Exception as e:
            print("BLE 오류:", repr(e))

        finally:
            ble_ready = False
            ble_connecting = False

            if client is not None:
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass

            ble_client = None

        if not ble_stop_event.is_set():
            print("3초 후 자동 재연결...")
            await asyncio.sleep(3)


def start_ble_thread():
    global ble_loop

    loop = asyncio.new_event_loop()
    ble_loop = loop
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(ble_connect_loop())

    except Exception as e:
        print("BLE 스레드 오류:", repr(e))

    finally:
        try:
            loop.close()
        except Exception:
            pass


ble_thread = threading.Thread(
    target=start_ble_thread,
    daemon=True
)

ble_thread.start()


# YOLO

YOLO_MODEL_PATH = "/home/pi/gdsb_python/best.onnx"

INPUT_SIZE = 640
CANE_CONF_THRESHOLD = 0.35
NMS_THRESHOLD = 0.45

print()
print("YOLO ONNX 모델 로딩...")

net = cv2.dnn.readNetFromONNX(YOLO_MODEL_PATH)

print("YOLO ONNX 모델 로딩 완료")


def detect_cane(frame):
    height, width = frame.shape[:2]

    blob = cv2.dnn.blobFromImage(
        frame,
        scalefactor=1 / 255.0,
        size=(INPUT_SIZE, INPUT_SIZE),
        swapRB=False,
        crop=False
    )

    net.setInput(blob)
    output = net.forward()[0].transpose()

    boxes = []
    confidences = []

    for detection in output:
        x = float(detection[0])
        y = float(detection[1])
        w = float(detection[2])
        h = float(detection[3])
        confidence = float(detection[4])

        if confidence < CANE_CONF_THRESHOLD:
            continue

        x *= width / INPUT_SIZE
        y *= height / INPUT_SIZE
        w *= width / INPUT_SIZE
        h *= height / INPUT_SIZE

        left = int(x - w / 2)
        top = int(y - h / 2)

        boxes.append([
            left,
            top,
            int(w),
            int(h)
        ])

        confidences.append(confidence)

    if not boxes:
        return []

    indexes = cv2.dnn.NMSBoxes(
        boxes,
        confidences,
        CANE_CONF_THRESHOLD,
        NMS_THRESHOLD
    )

    results = []

    if len(indexes) > 0:
        for i in indexes:
            i = int(i)

            x, y, w, h = boxes[i]

            results.append(
                (x, y, w, h, confidences[i])
            )

    return results


# Camera

picam2 = Picamera2()

camera_config = picam2.create_video_configuration(
    main={
        "size": (1280, 720),
        "format": "RGB888"
    }
)

picam2.configure(camera_config)
picam2.start()

time.sleep(2)


# 시스템 실행

def run_system():
    global state
    global detection_start_time

    print()
    print("================================")
    print("카메라 자동 인식 시작")
    print("================================")

    while True:
        frame = picam2.capture_array()
        frame = cv2.flip(frame, 1)

        cane_boxes = detect_cane(frame)
        cane_detected = len(cane_boxes) > 0

        current_time = time.monotonic()

        if state == IDLE:
            if cane_detected:
                state = CONFIRMING
                detection_start_time = current_time

                print("지팡이 검출 -> CONFIRMING")

        elif state == CONFIRMING:
            if cane_detected:
                elapsed = current_time - detection_start_time

                if elapsed >= DETECTION_CONFIRM_TIME:
                    state = ACTIVE

                    max7219_circle()

                    print()
                    print("================================")
                    print("지팡이 2초 연속 검출")
                    print("SYSTEM -> ACTIVE")
                    print("================================")

                    with tracking_lock:
                        active = tracking_active

                    if not active:
                        send_text("READY")

            else:
                state = IDLE
                detection_start_time = None

                print("지팡이 검출 실패 -> IDLE")

        elif state == ACTIVE:
            max7219_circle()

        time.sleep(0.01)


# 종료

def cleanup():
    global ble_client

    print()
    print("프로그램 종료")

    bus_timer_stop_event.set()
    ble_stop_event.set()

    if ble_client is not None and ble_loop is not None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                ble_client.disconnect(),
                ble_loop
            )

            future.result(timeout=3)

        except Exception:
            pass

    if ble_thread.is_alive():
        ble_thread.join(timeout=3)

    if bus_timer_thread.is_alive():
        bus_timer_thread.join(timeout=3)

    try:
        max7219_clear()
    except Exception:
        pass

    try:
        spi.close()
    except Exception:
        pass

    try:
        GPIO.cleanup()
    except Exception:
        pass

    try:
        picam2.stop()
    except Exception:
        pass

    print("프로그램 종료 완료")


# MAIN

if __name__ == "__main__":
    print()
    print("================================")
    print(" Bus Stop AI System")
    print("================================")
    print("YOLO :", YOLO_MODEL_PATH)
    print("BLE  :", HC05_ADDRESS)
    print("현재 정류장 :", current_station)
    print("101번 :", bus_arrival_times[101], "분")
    print("202번 :", bus_arrival_times[202], "분")
    print("303번 :", bus_arrival_times[303], "분")
    print("웹 서버 : 사용 안 함")
    print("================================")

    try:
        max7219_full()
        time.sleep(2)
        max7219_clear()

        run_system()

    except KeyboardInterrupt:
        print("Ctrl+C 감지")

    finally:
        cleanup()