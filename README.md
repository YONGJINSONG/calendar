# EE04 ePaper Calendar Frame

`EE04_Frame.ino`는 Seeed XIAO ESP32S3 Plus와 EE04 + 7.3인치 Spectra 6 패널용 캘린더·사진 액자 구현입니다. Arduino 진입 파일은 폴더명과 같은 `EE04_FlashImages.ino`이며, 빌드할 때 `EE04_Frame.ino`가 자동으로 함께 컴파일됩니다. 평소에는 딥슬립 상태이며, 6시간마다 GitHub에서 캘린더 프레임을 받아 표시합니다.

기존 플래시 이미지 테스트 스케치는 `examples/EE04_FlashImages/EE04_FlashImages.ino`에 분리되어 있어 통합 스케치와 중복 컴파일되지 않습니다.

## Arduino 설정

- Board: `XIAO_ESP32S3_PLUS` (없으면 `XIAO_ESP32S3`)
- PSRAM: `OPI PSRAM`
- Partition Scheme: `16M Flash (3MB APP/9.9MB FATFS)`
- USB CDC On Boot: `Enabled` (시리얼 진단용)
- 라이브러리: Seeed_GFX (`TFT_eSPI.h`), WiFi, FFat

`driver.h`는 800×480 Spectra 6 + EE04 설정의 기준 파일입니다. Wi-Fi 정보는 아래 웹 관리 모드에서 저장하므로 스케치에 비밀번호를 넣지 않아도 됩니다.

## GitHub Actions 설정

1. GitHub 저장소의 **Settings → Secrets and variables → Actions**에서 `ICAL_URL` Secret을 만듭니다. 값에는 Google Calendar의 비공개 iCal 주소를 넣습니다.
2. 필요하면 Actions Variables에 `LAT`, `LON`, `PLACE`를 설정합니다. 기본값은 서울(37.5256, 126.9317)입니다.
3. **Actions → Render ePaper calendar → Run workflow**를 실행합니다.
4. 성공하면 Actions가 `out/cal.bin`(ESP32 수신용)과 `out/cal.png`(미리보기)를 `main`에 커밋합니다.

워크플로는 매일 KST 03:00에도 실행됩니다. GitHub의 스케줄 실행은 지연될 수 있습니다.

## 동작

- KEY1 (D1/GPIO2): 다음 저장 사진 표시
- KEY2 (D2/GPIO3): GitHub 캘린더 즉시 갱신
- KEY3 (D4/GPIO5)를 2초 이상: 사진 업로드·삭제 웹 관리 모드

## Wi-Fi 등록 및 변경

1. 최초 플래시 후에는 `EE04-Setup` 설정 AP가 자동으로 열립니다. 이후 다시 설정하려면 KEY3(D4/GPIO5)를 2초 이상 누릅니다. 딥슬립뿐 아니라 전원/리셋 직후에도 동작합니다.
2. 기존 Wi-Fi에 연결되면 시리얼 모니터에 표시된 `http://장치IP`로 접속합니다.
3. 기존 Wi-Fi에 연결되지 않으면 휴대폰에서 `EE04-Setup` Wi-Fi를 선택합니다. 비밀번호는 `ee04setup`입니다.
4. 휴대폰의 로그인/설정 화면이 자동으로 열립니다. 열리지 않으면 브라우저에서 `http://192.168.4.1`을 열고 새 SSID와 비밀번호를 저장합니다. ESP32-S3는 2.4GHz Wi-Fi만 지원합니다.
5. 펌웨어가 새 설정으로 실제 연결을 시험합니다. 성공한 경우에만 저장하고 재시작하며, 실패하면 설정 AP를 유지합니다.
6. 연결 성공 시 관리 화면과 시리얼 로그에 장치 IP와 신호 세기(RSSI)가 표시됩니다.

비밀번호를 비워 저장하면 암호가 없는 공개 Wi-Fi에 연결합니다. 저장된 비밀번호는 웹 화면이나 로그에 다시 표시하지 않습니다.

캘린더 수신이나 Wi-Fi 연결에 실패하면 전자종이의 마지막 화면은 유지됩니다.

## 플래시 후 빠른 확인

1. 보드의 24/50PIN 점퍼가 `50 PIN` 쪽인지 확인합니다.
2. Arduino 시리얼 모니터를 115200 baud로 열고 리셋합니다.
3. `[fs] FFat`, `[wifi] 연결됨 IP=...`, `[cal] 수신 완료`, `[epd] 완료` 순서의 로그를 확인합니다.
4. Wi-Fi 미등록 시 휴대폰에서 `EE04-Setup`이 보이는지 확인합니다. AP 비밀번호는 `ee04setup`입니다.

`LittleFS`용 파티션을 선택하면 사진 저장소가 마운트되지 않습니다. 반드시 위의 FATFS 파티션을 사용하세요.
Wi-Fi가 등록되기 전에는 설정 AP가 시간 제한 없이 유지되며, 등록 후 관리 모드는 10분간 유지됩니다.
