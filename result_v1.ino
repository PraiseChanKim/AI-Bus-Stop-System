#include <Servo.h>
#include <SoftwareSerial.h>

// =====================================================
// Bluetooth
// BLE TX -> Arduino D10
// BLE RX <- Arduino D11
// =====================================================

SoftwareSerial BT(10, 11);
String rxBuffer = "";

// =====================================================
// Servo
// =====================================================

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;
Servo servo5;
Servo servo6;

// =====================================================
// Servo Pin
// =====================================================

const int SERVO1_PIN = 4;
const int SERVO2_PIN = 5;
const int SERVO3_PIN = 6;
const int SERVO4_PIN = 7;
const int SERVO5_PIN = 8;
const int SERVO6_PIN = 9;

// =====================================================
// Servo Angle
// 기존 값 그대로
// =====================================================

const int BASE_LEFT = 100;
const int UP_LEFT = 82;

const int BASE_RIGHT = 0;
const int UP_RIGHT = 18;

// =====================================================
// Button
//
// D2 = 정류장 이동
// D3 = 선택 / 버스 안내 종료
// =====================================================

const int MOVE_BUTTON = 2;
const int ACTION_BUTTON = 12;

bool lastMoveState = HIGH;
bool lastActionState = HIGH;

// =====================================================
// Mode
// =====================================================

const int MODE_STATION_SELECT = 0;
const int MODE_WAIT_BUS = 1;
const int MODE_BUS_DISPLAY = 2;

int systemMode = MODE_STATION_SELECT;

// =====================================================
// Station
//
// A = 1
// B = 2
// C = 3
// D = 4
// =====================================================

char selectedStation = 'A';

// =====================================================
// Bus Display
// =====================================================

String currentBusNumber = "";
String currentArrivalTime = "";

bool stopRequested = false;

// =====================================================
// Display Time
// =====================================================

const unsigned long DIGIT_SHOW_TIME = 1500;
const unsigned long DIGIT_GAP_TIME = 500;
const unsigned long SECTION_GAP_TIME = 1500;

// =====================================================
// Braille
// =====================================================

struct BrailleNumber
{
    bool dot1;
    bool dot2;
    bool dot3;
    bool dot4;
    bool dot5;
    bool dot6;
};

// =====================================================
// 숫자 점자
// 네 현재 배열 그대로
// =====================================================

BrailleNumber numbers[10] =
{
    // 0
    {false, true, true, false, true, false},

    // 1
    {false, false, false, false, false, true},

    // 2
    {false, false, false, false, true, true},

    // 3
    {false, false, true, false, false, true},

    // 4
    {false, true, true, false, false, true},

    // 5
    {false, true, false, false, false, true},

    // 6
    {false, false, true, false, true, true},

    // 7
    {false, true, true, false, true, true},

    // 8
    {false, true, false, false, true, true},

    // 9
    {false, false, true, false, true, false}
};

// =====================================================
// Clear
// =====================================================

void ClearBraille()
{
    servo1.write(BASE_LEFT);
    servo2.write(BASE_LEFT);
    servo3.write(BASE_LEFT);

    servo4.write(BASE_RIGHT);
    servo5.write(BASE_RIGHT);
    servo6.write(BASE_RIGHT);
}

// =====================================================
// 숫자 표시
// =====================================================

void ShowNumber(int num)
{
    if (num < 0 || num > 9) return;

    BrailleNumber b = numbers[num];

    servo1.write(b.dot1 ? UP_LEFT : BASE_LEFT);
    servo2.write(b.dot2 ? UP_LEFT : BASE_LEFT);
    servo3.write(b.dot3 ? UP_LEFT : BASE_LEFT);

    servo4.write(b.dot4 ? UP_RIGHT : BASE_RIGHT);
    servo5.write(b.dot5 ? UP_RIGHT : BASE_RIGHT);
    servo6.write(b.dot6 ? UP_RIGHT : BASE_RIGHT);
}

// =====================================================
// 정류장 -> 숫자
// =====================================================

int StationToNumber(char station)
{
    if (station == 'A') return 1;
    if (station == 'B') return 2;
    if (station == 'C') return 3;
    if (station == 'D') return 4;

    return 1;
}

// =====================================================
// 현재 선택 정류장 표시
//
// 선택 모드에서는 숫자를 계속 유지한다.
// A = 1
// B = 2
// C = 3
// D = 4
// =====================================================

void ShowSelectedStation()
{
    int number = StationToNumber(selectedStation);

    ShowNumber(number);

    Serial.print("현재 선택 정류장 : ");
    Serial.print(selectedStation);
    Serial.print(" / 점자 : ");
    Serial.println(number);
}

// =====================================================
// 정류장 이동
// =====================================================

void MoveStation()
{
    if (selectedStation == 'A') selectedStation = 'B';
    else if (selectedStation == 'B') selectedStation = 'C';
    else if (selectedStation == 'C') selectedStation = 'D';
    else selectedStation = 'A';

    ShowSelectedStation();
}

// =====================================================
// 목적지 확정
// =====================================================

void ConfirmStation()
{
    BT.print("DEST:");
    BT.println(selectedStation);

    Serial.println();
    Serial.print("목적지 확정 : ");
    Serial.println(selectedStation);

    Serial.print("Arduino -> Pi : DEST:");
    Serial.println(selectedStation);

    ClearBraille();

    systemMode = MODE_WAIT_BUS;

    Serial.println("버스 정보 대기 중...");
}

// =====================================================
// Bluetooth 읽기
// =====================================================

void ProcessBluetooth()
{
    while (BT.available())
    {
        char c = BT.read();

        if (c == '\n')
        {
            rxBuffer.trim();

            if (rxBuffer.length() > 0)
            {
                ProcessMessage(rxBuffer);
            }

            rxBuffer = "";
        }
        else
        {
            rxBuffer += c;
        }
    }
}

// =====================================================
// ACTION 버튼 확인
//
// 선택 모드:
//     목적지 확정
//
// 버스 안내 모드:
//     현재 사이클 후 종료
// =====================================================

void CheckActionButton()
{
    bool actionState = digitalRead(ACTION_BUTTON);

    if (lastActionState == HIGH && actionState == LOW)
    {
        if (systemMode == MODE_STATION_SELECT)
        {
            ConfirmStation();
        }
        else if (systemMode == MODE_BUS_DISPLAY)
        {
            stopRequested = true;

            Serial.println();
            Serial.println("버스 안내 종료 요청");
            Serial.println("현재 사이클 완료 후 종료합니다.");
        }

        delay(150);
    }

    lastActionState = actionState;
}

// =====================================================
// 기다리면서 Bluetooth / 종료 버튼 확인
// =====================================================

void WaitSystem(unsigned long duration)
{
    unsigned long startTime = millis();

    while (millis() - startTime < duration)
    {
        ProcessBluetooth();
        CheckActionButton();

        delay(10);
    }
}

// =====================================================
// 숫자 하나 일정 시간 표시
// =====================================================

void ShowDigit(char digit)
{
    if (digit < '0' || digit > '9') return;

    int num = digit - '0';

    Serial.print("점자 표시 : ");
    Serial.println(num);

    ShowNumber(num);
    WaitSystem(DIGIT_SHOW_TIME);

    ClearBraille();
    WaitSystem(DIGIT_GAP_TIME);
}

// =====================================================
// 문자열 표시
// =====================================================

void ShowNumberString(String text)
{
    for (int i = 0; i < text.length(); i++)
    {
        ShowDigit(text[i]);
    }
}

// =====================================================
// BUS 한 사이클
//
// 예:
// 101 -> 3
// =====================================================

void ShowBusCycle()
{
    if (currentBusNumber.length() == 0) return;

    Serial.println();
    Serial.println("------------------------------");
    Serial.print("BUS : ");
    Serial.print(currentBusNumber);
    Serial.print(" / ");
    Serial.print(currentArrivalTime);
    Serial.println("분");

    ShowNumberString(currentBusNumber);

    ClearBraille();
    WaitSystem(SECTION_GAP_TIME);

    ShowNumberString(currentArrivalTime);

    ClearBraille();

    Serial.println("한 사이클 완료");

    if (stopRequested)
    {
        FinishBusDisplay();
    }
}

// =====================================================
// 버스 안내 종료
// =====================================================

void FinishBusDisplay()
{
    BT.println("STOP");

    Serial.println();
    Serial.println("==============================");
    Serial.println("버스 안내 종료");
    Serial.println("Arduino -> Pi : STOP");
    Serial.println("==============================");

    currentBusNumber = "";
    currentArrivalTime = "";

    stopRequested = false;

    // 다시 A부터 선택
    selectedStation = 'A';

    systemMode = MODE_STATION_SELECT;

    // A = 1 표시
    ShowSelectedStation();
}

// =====================================================
// Pi 메시지 처리
// =====================================================

void ProcessMessage(String message)
{
    message.trim();

    Serial.print("Pi -> Arduino : ");
    Serial.println(message);

    // -------------------------------------------------
    // READY
    // -------------------------------------------------

    if (message == "READY")
    {
        // 현재 버스 표시 중이 아니면 선택 모드
        if (systemMode != MODE_BUS_DISPLAY)
        {
            systemMode = MODE_STATION_SELECT;

            Serial.println("목적지 선택 가능");

            ShowSelectedStation();
        }

        return;
    }

    // -------------------------------------------------
    // BUS
    //
    // BUS:101,3
    // BUS:101,2
    // BUS:101,1
    // BUS:101,0
    // -------------------------------------------------

    if (message.startsWith("BUS:"))
    {
        String data = message.substring(4);

        int commaIndex = data.indexOf(',');

        if (commaIndex == -1)
        {
            Serial.println("BUS 데이터 오류");
            return;
        }

        currentBusNumber = data.substring(0, commaIndex);
        currentArrivalTime = data.substring(commaIndex + 1);

        currentBusNumber.trim();
        currentArrivalTime.trim();

        systemMode = MODE_BUS_DISPLAY;

        Serial.print("최신 버스 : ");
        Serial.println(currentBusNumber);

        Serial.print("최신 시간 : ");
        Serial.print(currentArrivalTime);
        Serial.println("분");

        return;
    }

    // -------------------------------------------------
    // ERROR
    // -------------------------------------------------

    if (message.startsWith("ERROR:"))
    {
        Serial.print("Pi 오류 : ");
        Serial.println(message);

        systemMode = MODE_STATION_SELECT;

        ShowSelectedStation();

        return;
    }
}

// =====================================================
// 정류장 이동 버튼
// =====================================================

void CheckMoveButton()
{
    if (systemMode != MODE_STATION_SELECT) return;

    bool moveState = digitalRead(MOVE_BUTTON);

    if (lastMoveState == HIGH && moveState == LOW)
    {
        MoveStation();
        delay(150);
    }

    lastMoveState = moveState;
}

// =====================================================
// Setup
// =====================================================

void setup()
{
    Serial.begin(9600);
    BT.begin(38400);

    servo1.attach(SERVO1_PIN);
    servo2.attach(SERVO2_PIN);
    servo3.attach(SERVO3_PIN);
    servo4.attach(SERVO4_PIN);
    servo5.attach(SERVO5_PIN);
    servo6.attach(SERVO6_PIN);

    pinMode(MOVE_BUTTON, INPUT_PULLUP);
    pinMode(ACTION_BUTTON, INPUT_PULLUP);

    ClearBraille();

    selectedStation = 'A';
    systemMode = MODE_STATION_SELECT;

    delay(1000);

    Serial.println();
    Serial.println("================================");
    Serial.println(" BRAILLE BUS SYSTEM");
    Serial.println("================================");
    Serial.println("D2 : 정류장 이동");
    Serial.println("D3 : 선택 / 안내 종료");
    Serial.println("A=1, B=2, C=3, D=4");
    Serial.println("================================");

    // 시작 시 A = 1
    ShowSelectedStation();
}

// =====================================================
// Loop
// =====================================================

void loop()
{
    ProcessBluetooth();
    CheckActionButton();

    if (systemMode == MODE_STATION_SELECT)
    {
        CheckMoveButton();

        // 선택된 정류장 숫자를 계속 유지
        ShowNumber(StationToNumber(selectedStation));
        return;
    }

    if (systemMode == MODE_WAIT_BUS)
    {
        ClearBraille();
        return;
    }

    if (systemMode == MODE_BUS_DISPLAY)
    {
        ShowBusCycle();
        return;
    }
}
