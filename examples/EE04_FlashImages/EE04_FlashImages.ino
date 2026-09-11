/*
 * EE04 + 7.3" Spectra 6 (GDEP073E01) -- microSD 없이 플래시에 박은 이미지 테스트
 *
 * KEY1 (GPIO2) : 다음 이미지
 * KEY2 (GPIO3) : 이전 이미지
 *
 * Arduino IDE 설정
 *   Tools > Board        : XIAO_ESP32S3_PLUS
 *   Tools > PSRAM        : OPI PSRAM          <-- 필수
 *   Tools > USB CDC On Boot : Enabled          <-- 시리얼 로그 보려면
 *   Tools > Partition Scheme : 앱 영역이 이미지 총량보다 큰 것으로
 *
 * 보드의 24/50PIN 점퍼는 50 PIN 쪽에 꽂혀 있어야 합니다.
 */

#include "../../driver.h"
#include "TFT_eSPI.h"

#include "../../pic1.h"
#include "../../pic2.h"
#include "../../pic3.h"

#ifndef EPAPER_ENABLE
#error "driver.h에서 BOARD_SCREEN_COMBO 509 와 USE_XIAO_EPAPER_DISPLAY_BOARD_EE04 를 정의해야 합니다"
#endif

EPaper epaper;

static constexpr size_t IMAGE_BYTES = EPD_WIDTH * EPD_HEIGHT / 2;

static_assert(EPD_WIDTH == 800 && EPD_HEIGHT == 480,
              "GDEP073E01은 800x480 패널 설정이 필요합니다");
static_assert(sizeof(pic1) == IMAGE_BYTES, "pic1 데이터 크기가 올바르지 않습니다");
static_assert(sizeof(pic2) == IMAGE_BYTES, "pic2 데이터 크기가 올바르지 않습니다");
static_assert(sizeof(pic3) == IMAGE_BYTES, "pic3 데이터 크기가 올바르지 않습니다");

static const uint8_t* const IMAGES[] = { pic1, pic2, pic3 };
static const int IMAGE_COUNT = sizeof(IMAGES) / sizeof(IMAGES[0]);

static const int KEY1 = 2;   // D1
static const int KEY2 = 3;   // D2
static const int EPD_BUSY_PIN = 4;  // EE04 D3

static int current = 0;

static void showImage(int n) {
  Serial.printf("[epd] 이미지 %d/%d 표시 시작\n", n + 1, IMAGE_COUNT);
  const uint32_t t0 = millis();

  // 4bpp E6 버퍼를 스프라이트에 밀어넣습니다.
  // 배열은 플래시에 메모리 매핑되어 있어 RAM으로 복사할 필요가 없습니다.
  epaper.pushImage(0, 0, EPD_WIDTH, EPD_HEIGHT, (uint16_t*)IMAGES[n]);
  epaper.update();

  Serial.printf("[epd] 갱신 완료 (%lu ms)\n", (unsigned long)(millis() - t0));
}

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println("=== EE04 flash image test ===");
  const size_t psramSize = ESP.getPsramSize();
  Serial.printf("[sys] PSRAM : %lu kB\n", (unsigned long)(psramSize / 1024));
  if (psramSize == 0) {
    Serial.println("[sys] !!! PSRAM이 0입니다. Tools > PSRAM > OPI PSRAM 을 켜세요 !!!");
    while (true) delay(1000);
  }
  if (!epaper.created()) {
    Serial.println("[sys] !!! 4bpp 화면 버퍼 할당에 실패했습니다 !!!");
    while (true) delay(1000);
  }
  Serial.printf("[sys] 패널  : %d x %d\n", EPD_WIDTH, EPD_HEIGHT);
  Serial.printf("[sys] 이미지: %d장, 장당 %u 바이트\n",
                IMAGE_COUNT, (unsigned int)IMAGE_BYTES);

  pinMode(KEY1, INPUT_PULLUP);
  pinMode(KEY2, INPUT_PULLUP);
  pinMode(EPD_BUSY_PIN, INPUT);
  Serial.printf("[sys] EPD BUSY before init: %d\n", digitalRead(EPD_BUSY_PIN));

  epaper.begin();
  showImage(current);
}

void loop() {
  bool changed = false;

  if (digitalRead(KEY1) == LOW) {
    current = (current + 1) % IMAGE_COUNT;
    changed = true;
  } else if (digitalRead(KEY2) == LOW) {
    current = (current - 1 + IMAGE_COUNT) % IMAGE_COUNT;
    changed = true;
  }

  if (changed) {
    // 버튼을 뗄 때까지 기다립니다. 갱신 중 재진입을 막습니다.
    while (digitalRead(KEY1) == LOW || digitalRead(KEY2) == LOW) delay(10);
    showImage(current);
  }

  delay(20);
}
