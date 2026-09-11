/*
 * EE04 + 7.3" Spectra 6 -- 캘린더 + 사진 액자 통합
 *
 * 평소에는 딥슬립. 정해진 주기로 깨어나 GitHub에서 캘린더 이미지를 받아 표시합니다.
 *
 *   KEY1 (D1/GPIO2) 짧게 : 다음 사진
 *   KEY2 (D2/GPIO3) 짧게 : 캘린더 즉시 갱신
 *   KEY3 (D4/GPIO5) 2초  : 웹 관리 모드 (Wi-Fi 설정/사진 관리). 10분 뒤 자동 복귀
 *
 * Tools > Board           : XIAO_ESP32S3_PLUS  (없으면 XIAO_ESP32S3)
 * Tools > PSRAM           : OPI PSRAM          <-- 필수
 * Tools > Partition Scheme: 16M Flash (3MB APP/9.9MB FATFS)  <-- 사진 저장 공간
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <FS.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <FFat.h>
#include <Preferences.h>
#include <esp_sleep.h>
#include <driver/rtc_io.h>
#include "driver.h"
#include "TFT_eSPI.h"

// ============================================================ 사용자 설정
// 최초 접속용 기본값입니다. 웹 관리 모드에서 저장한 값이 있으면 그 값을 우선합니다.
// 비워 두어도 KEY3 관리 모드의 설정 AP를 통해 Wi-Fi를 등록할 수 있습니다.
static const char* DEFAULT_WIFI_SSID = "";
static const char* DEFAULT_WIFI_PASS = "";
static const char* SETUP_AP_SSID = "EE04-Setup";
static const char* SETUP_AP_PASS = "ee04setup";  // WPA2는 8자 이상이어야 합니다.

// GitHub Actions가 생성·커밋하는 800x480, 4bpp E6 프레임입니다.
static const char* CAL_URL =
    "https://raw.githubusercontent.com/YONGJINSONG/calendar/main/out/cal.bin";
// 이 저장소는 공개 저장소입니다. 비공개 저장소로 전환할 때만 fine-grained 토큰을 입력하세요.
static const char* GH_TOKEN = "";

static const uint64_t WAKE_HOURS   = 6;    // 캘린더 자동 갱신 주기
static const uint32_t WEB_MINUTES  = 10;   // 웹 관리 모드 유지 시간

// ----------------------------------------------------------- 핀
static const gpio_num_t KEY1 = GPIO_NUM_2;
static const gpio_num_t KEY2 = GPIO_NUM_3;
static const gpio_num_t KEY3 = GPIO_NUM_5;
static const uint64_t BTN_MASK = (1ULL << KEY1) | (1ULL << KEY2) | (1ULL << KEY3);

EPaper epaper;
WebServer server(80);
DNSServer dnsServer;

static_assert(EPD_WIDTH == 800 && EPD_HEIGHT == 480,
              "EE04 calendar frame requires the 800x480 Spectra 6 panel setting");

static uint8_t* frame = nullptr;
static size_t   frameSize = 0;
static size_t   received  = 0;
static String   uploadName;
static File     uploadFile;
static volatile bool pendingShow = false;
static volatile bool pendingRestart = false;
static bool fsReady = false;

// 딥슬립에도 살아남는 변수
RTC_DATA_ATTR static int photoIndex = 0;

// ============================================================ 유틸

static void loadWifiConfig(String& ssid, String& pass) {
  ssid = DEFAULT_WIFI_SSID;
  pass = DEFAULT_WIFI_PASS;
  Preferences prefs;
  if (prefs.begin("ee04wifi", true)) {
    ssid = prefs.getString("ssid", DEFAULT_WIFI_SSID);
    pass = prefs.getString("pass", DEFAULT_WIFI_PASS);
    prefs.end();
  }
}

static bool hasWifiConfig() {
  String ssid, pass;
  loadWifiConfig(ssid, pass);
  return ssid.length() > 0;
}

static bool wifiUp(uint32_t ms = 20000) {
  if (WiFi.status() == WL_CONNECTED) return true;

  String ssid, pass;
  loadWifiConfig(ssid, pass);

  if (ssid.length() == 0) {
    Serial.println("[wifi] 저장된 Wi-Fi가 없습니다");
    return false;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  Serial.printf("[wifi] '%s' 연결 시도\n", ssid.c_str());
  WiFi.begin(ssid.c_str(), pass.c_str());
  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < ms) delay(200);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] 연결됨 IP=%s RSSI=%d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return true;
  }
  Serial.printf("[wifi] 연결 실패 status=%d (2.4GHz/SSID/비밀번호 확인)\n",
                (int)WiFi.status());
  return false;
}

static void showFrame() {
  Serial.println("[epd] 갱신 시작");
  const uint32_t t = millis();
  epaper.pushImage(0, 0, EPD_WIDTH, EPD_HEIGHT, (uint16_t*)frame);
  epaper.update();
  epaper.sleep();
  Serial.printf("[epd] 완료 %lu ms\n", (unsigned long)(millis() - t));
}

// 사진 목록 --------------------------------------------------

static int photoCount() {
  int n = 0;
  if (!fsReady) return 0;
  File dir = FFat.open("/img");
  if (!dir) return 0;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile())
    if (!f.isDirectory()) n++;
  return n;
}

static String photoPath(const char* rawName) {
  String path = rawName;
  if (!path.startsWith("/")) path = String("/img/") + path;
  return path;
}

static String photoName(const char* rawName) {
  String name = rawName;
  const int slash = name.lastIndexOf('/');
  if (slash >= 0) name = name.substring(slash + 1);
  return name;
}

static String photoAt(int idx) {
  int n = 0;
  if (!fsReady) return "";
  File dir = FFat.open("/img");
  if (!dir) return "";
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    if (f.isDirectory()) continue;
    if (n == idx) return photoPath(f.name());
    n++;
  }
  return "";
}

// ============================================================ 동작

static bool fetchCalendar() {
  if (!wifiUp()) return false;

  WiFiClientSecure* tls = new WiFiClientSecure;
  // 인증서 검증을 생략합니다. 받는 것이 공개 이미지뿐이라 위험이 낮지만,
  // 엄격하게 하려면 GitHub 루트 CA를 setCACert()로 넣으세요.
  tls->setInsecure();

  HTTPClient http;
  http.setTimeout(25000);
  if (!http.begin(*tls, CAL_URL)) { delete tls; return false; }
  if (strlen(GH_TOKEN) > 0) http.addHeader("Authorization", String("Bearer ") + GH_TOKEN);

  const int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("[cal] HTTP %d\n", code);
    http.end(); delete tls; return false;
  }

  WiFiClient* s = http.getStreamPtr();
  size_t got = 0;
  uint32_t last = millis();
  while (got < frameSize && http.connected()) {
    const size_t avail = s->available();
    if (avail) {
      got += s->readBytes(frame + got, min(avail, frameSize - got));
      last = millis();
    } else if (millis() - last > 12000) break;
    else delay(5);
  }
  http.end(); delete tls;

  if (got != frameSize) {
    Serial.printf("[cal] 수신 부족 %u/%u\n", (unsigned)got, (unsigned)frameSize);
    return false;
  }
  Serial.println("[cal] 수신 완료");
  showFrame();
  return true;
}

static bool showPhoto(int step) {
  const int n = photoCount();
  if (n == 0) { Serial.println("[photo] 저장된 사진이 없습니다"); return false; }

  photoIndex = ((photoIndex + step) % n + n) % n;
  const String path = photoAt(photoIndex);
  File f = FFat.open(path, "r");
  if (!f || f.size() != frameSize) {
    Serial.printf("[photo] %s 읽기 실패\n", path.c_str());
    return false;
  }
  f.read(frame, frameSize);
  f.close();
  Serial.printf("[photo] %s (%d/%d)\n", path.c_str(), photoIndex + 1, n);
  showFrame();
  return true;
}

// ============================================================ 웹 UI

static const char PAGE[] PROGMEM = R"rawliteral(
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ePaper 관리</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:14px;background:#f4f4f5}
h2{margin:0 0 10px;font-size:17px}
.card{background:#fff;border-radius:10px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.1)}
input,textarea,button,select{font:inherit;width:100%;box-sizing:border-box;margin-top:8px}
textarea{height:90px;padding:8px;border:1px solid #ccc;border-radius:6px}
button{padding:11px;border:0;border-radius:6px;background:#1a73e8;color:#fff;font-weight:600}
button.sec{background:#666}button.danger{background:#c0392b}button:disabled{background:#aaa}
canvas{width:100%;border:1px solid #ddd;border-radius:6px;margin-top:8px;image-rendering:pixelated}
.row{display:flex;gap:8px}.row>*{flex:1}
#msg{margin-top:8px;font-size:14px;color:#555;min-height:20px}
ul{list-style:none;padding:0;margin:8px 0 0}
li{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #eee;font-size:14px}
li span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li button{width:auto;margin:0;padding:6px 10px;font-size:13px}
.bar{height:8px;background:#e5e5e5;border-radius:4px;overflow:hidden;margin-top:8px}
.bar>div{height:100%;background:#1a73e8}
small{color:#777}
</style></head><body>

<div class="card">
  <h2>Wi-Fi 설정</h2>
  <div id="wifiStatus"><small>연결 상태 확인 중...</small></div>
  <input id="ssid" maxlength="32" autocomplete="username" placeholder="Wi-Fi 이름(SSID)">
  <input id="pass" type="password" maxlength="63" autocomplete="new-password"
         placeholder="Wi-Fi 비밀번호 (공개망은 비움)">
  <button id="wifiSave">저장 후 재시작</button>
  <small>연결에 실패하면 KEY3 관리 모드에서 EE04-Setup에 접속하세요.</small>
</div>

<div class="card">
  <h2>저장 공간</h2>
  <div class="bar"><div id="usebar" style="width:0"></div></div>
  <div id="use"><small>불러오는 중...</small></div>
</div>

<div class="card">
  <h2>저장된 사진</h2>
  <ul id="list"><li><small>불러오는 중...</small></li></ul>
  <button class="danger" id="delAll" style="margin-top:10px">전체 삭제</button>
</div>

<div class="card">
  <h2>사진 추가</h2>
  <input type="file" id="file" accept="image/*">
  <div class="row">
    <select id="fit"><option value="cover">꽉 채우기</option><option value="contain">전체 보이기</option></select>
    <select id="rot"><option value="0">가로</option><option value="90">세로 90°</option><option value="270">세로 270°</option></select>
  </div>
</div>

<div class="card">
  <h2>글 추가</h2>
  <textarea id="text" placeholder="화면에 띄울 글"></textarea>
  <div class="row">
    <select id="size"><option value="48">보통</option><option value="72">크게</option><option value="34">작게</option></select>
    <select id="color"><option value="0,0,0">검정</option><option value="255,0,0">빨강</option><option value="0,0,255">파랑</option></select>
  </div>
  <button id="mkText">글로 만들기</button>
</div>

<div class="card">
  <h2>미리보기</h2>
  <canvas id="cv" width="800" height="480"></canvas>
  <input id="name" placeholder="파일 이름 (비우면 자동)">
  <div class="row" style="margin-top:8px">
    <button id="save" disabled>저장하기</button>
    <button class="sec" id="show" disabled>바로 표시</button>
  </div>
  <div id="msg"></div>
</div>

<script>
const W=800,H=480;
const cv=document.getElementById('cv'),ctx=cv.getContext('2d',{willReadFrequently:true});
const msg=document.getElementById('msg');
const saveBtn=document.getElementById('save'),showBtn=document.getElementById('show');
const PAL=[[255,255,255,0x0],[0,255,0,0x2],[255,0,0,0x6],[255,255,0,0xB],[0,0,255,0xD],[0,0,0,0xF]];
function blank(){ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);}
blank();
function nearest(r,g,b){let bi=0,bd=1e9;for(let i=0;i<PAL.length;i++){const p=PAL[i],dr=r-p[0],dg=g-p[1],db=b-p[2];const d=dr*dr*0.299+dg*dg*0.587+db*db*0.114;if(d<bd){bd=d;bi=i;}}return bi;}
function dither(){
  const img=ctx.getImageData(0,0,W,H),d=img.data;
  const buf=new Float32Array(W*H*3);
  for(let i=0,j=0;i<d.length;i+=4,j+=3){buf[j]=d[i];buf[j+1]=d[i+1];buf[j+2]=d[i+2];}
  const idx=new Uint8Array(W*H);
  const push=(x,y,er,eg,eb,f)=>{if(x<0||x>=W||y<0||y>=H)return;const k=(y*W+x)*3;buf[k]+=er*f;buf[k+1]+=eg*f;buf[k+2]+=eb*f;};
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
    const k=(y*W+x)*3,r=buf[k],g=buf[k+1],b=buf[k+2];
    const pi=nearest(r,g,b),p=PAL[pi];idx[y*W+x]=pi;
    const er=r-p[0],eg=g-p[1],eb=b-p[2];
    push(x+1,y,er,eg,eb,7/16);push(x-1,y+1,er,eg,eb,3/16);
    push(x,y+1,er,eg,eb,5/16);push(x+1,y+1,er,eg,eb,1/16);
  }
  for(let i=0,j=0;i<idx.length;i++,j+=4){const p=PAL[idx[i]];d[j]=p[0];d[j+1]=p[1];d[j+2]=p[2];d[j+3]=255;}
  ctx.putImageData(img,0,0);
  const out=new Uint8Array(W*H/2);
  for(let y=0;y<H;y++){const base=y*(W/2);
    for(let x=0;x<W;x+=2) out[base+(x>>1)]=(PAL[idx[y*W+x]][3]<<4)|PAL[idx[y*W+x+1]][3];}
  return out;
}
function ready(){window.payload=dither();msg.textContent='준비 완료';saveBtn.disabled=false;showBtn.disabled=false;}

document.getElementById('file').onchange=e=>{
  const f=e.target.files[0];if(!f)return;
  const img=new Image();
  img.onload=()=>{
    const rot=+document.getElementById('rot').value, fit=document.getElementById('fit').value;
    blank();ctx.save();
    if(rot){ctx.translate(W/2,H/2);ctx.rotate(rot*Math.PI/180);ctx.translate(-H/2,-W/2);}
    const cw=rot?H:W,chh=rot?W:H;
    const s=fit==='cover'?Math.max(cw/img.width,chh/img.height):Math.min(cw/img.width,chh/img.height);
    ctx.drawImage(img,(cw-img.width*s)/2,(chh-img.height*s)/2,img.width*s,img.height*s);
    ctx.restore();
    if(!document.getElementById('name').value) document.getElementById('name').value=f.name.replace(/\.[^.]+$/,'');
    msg.textContent='변환 중...';setTimeout(ready,30);
  };
  img.src=URL.createObjectURL(f);
};

document.getElementById('mkText').onclick=()=>{
  const t=document.getElementById('text').value||'',size=+document.getElementById('size').value;
  blank();
  ctx.fillStyle='rgb('+document.getElementById('color').value+')';
  ctx.font='700 '+size+'px system-ui,sans-serif';ctx.textBaseline='top';
  const lines=[];
  for(const raw of t.split('\n')){let cur='';
    for(const ch of raw){if(ctx.measureText(cur+ch).width>W-80){lines.push(cur);cur=ch;}else cur+=ch;}
    lines.push(cur);}
  const lh=size*1.35;let y=Math.max(30,(H-lines.length*lh)/2);
  for(const ln of lines){ctx.fillText(ln,40,y);y+=lh;}
  msg.textContent='변환 중...';setTimeout(ready,30);
};

async function send(url,extra){
  const fd=new FormData();
  fd.append('img',new Blob([window.payload]),'f.bin');
  const q=extra?('?'+extra):'';
  const r=await fetch(url+q,{method:'POST',body:fd});
  return r.ok;
}
saveBtn.onclick=async()=>{
  saveBtn.disabled=true;msg.textContent='저장 중...';
  const nm=encodeURIComponent(document.getElementById('name').value||'');
  msg.textContent=(await send('/save','name='+nm))?'저장 완료':'저장 실패';
  saveBtn.disabled=false;refresh();
};
showBtn.onclick=async()=>{
  showBtn.disabled=true;msg.textContent='전송 중...';
  msg.textContent=(await send('/show'))?'전송 완료. 갱신에 15~20초 걸립니다.':'전송 실패';
  showBtn.disabled=false;
};

async function refresh(){
  try{
    const j=await (await fetch('/api/list')).json();
    const pct=j.total?Math.round(j.used*100/j.total):0;
    document.getElementById('usebar').style.width=pct+'%';
    document.getElementById('use').innerHTML=
      '<small>'+(j.used/1024/1024).toFixed(2)+' MB / '+(j.total/1024/1024).toFixed(2)+
      ' MB · 사진 '+j.files.length+'장 · 추가 가능 약 '+Math.max(0,Math.floor((j.total-j.used)/192000))+'장</small>';
    const ul=document.getElementById('list');ul.innerHTML='';
    if(!j.files.length) ul.innerHTML='<li><small>저장된 사진이 없습니다</small></li>';
    for(const f of j.files){
      const li=document.createElement('li');
      const s=document.createElement('span');s.textContent=f;
      const b1=document.createElement('button');b1.className='sec';b1.textContent='표시';
      b1.onclick=async()=>{await fetch('/api/show?f='+encodeURIComponent(f));msg.textContent='표시 요청함';};
      const b2=document.createElement('button');b2.className='danger';b2.textContent='삭제';
      b2.onclick=async()=>{if(confirm(f+' 삭제할까요?')){await fetch('/api/del?f='+encodeURIComponent(f));refresh();}};
      li.append(s,b1,b2);ul.appendChild(li);
    }
  }catch(e){document.getElementById('use').innerHTML='<small>목록을 불러오지 못했습니다</small>';}
}
document.getElementById('delAll').onclick=async()=>{
  if(confirm('저장된 사진을 모두 삭제할까요?')){await fetch('/api/delall');refresh();}
};
async function wifiStatus(){
  try{document.getElementById('wifiStatus').textContent=await (await fetch('/api/wifi/status')).text();}
  catch(e){document.getElementById('wifiStatus').textContent='상태를 확인하지 못했습니다';}
}
document.getElementById('wifiSave').onclick=async()=>{
  const b=document.getElementById('wifiSave');
  const ssid=document.getElementById('ssid').value.trim();
  const pass=document.getElementById('pass').value;
  if(!ssid){msg.textContent='Wi-Fi 이름을 입력하세요';return;}
  b.disabled=true;msg.textContent='Wi-Fi 설정 저장 중...';
  try{
    const body=new URLSearchParams({ssid,pass});
    const r=await fetch('/api/wifi',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});
    msg.textContent=await r.text();
    if(!r.ok)b.disabled=false;
  }catch(e){msg.textContent='저장 요청에 실패했습니다';b.disabled=false;}
};
refresh();
wifiStatus();
</script></body></html>
)rawliteral";

// ---------------------------------------------------- 웹 핸들러

static void hRoot() { server.send_P(200, "text/html; charset=utf-8", PAGE); }

static void hCaptivePortal() {
  server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
  server.send(302, "text/plain", "");
}

static void hWifiStatus() {
  String status;
  if (WiFi.status() == WL_CONNECTED) {
    status = String("연결됨: ") + WiFi.SSID() + " · IP " +
             WiFi.localIP().toString() + " · 신호 " + String(WiFi.RSSI()) + " dBm";
  } else {
    status = String("인터넷 Wi-Fi 미연결 · 설정 AP: ") + SETUP_AP_SSID +
             " · " + WiFi.softAPIP().toString();
  }
  server.send(200, "text/plain; charset=utf-8", status);
}

static void hWifiSave() {
  String ssid = server.arg("ssid");
  const String pass = server.arg("pass");
  ssid.trim();

  if (ssid.length() == 0 || ssid.length() > 32) {
    server.send(400, "text/plain; charset=utf-8", "Wi-Fi 이름은 1~32자로 입력하세요.");
    return;
  }
  if (pass.length() > 0 && (pass.length() < 8 || pass.length() > 63)) {
    server.send(400, "text/plain; charset=utf-8", "비밀번호는 비워 두거나 8~63자로 입력하세요.");
    return;
  }

  // 설정 AP는 유지한 채 새 자격 증명으로 실제 연결을 검증합니다. 잘못된
  // 비밀번호를 저장해 장치가 다시 접근 불능이 되는 것을 방지합니다.
  WiFi.mode(WIFI_AP_STA);
  WiFi.disconnect(false, false);
  delay(100);
  Serial.printf("[wifi] 새 설정 '%s' 검증 중\n", ssid.c_str());
  WiFi.begin(ssid.c_str(), pass.c_str());
  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) delay(200);
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[wifi] 새 설정 검증 실패 status=%d\n", (int)WiFi.status());
    server.send(400, "text/plain; charset=utf-8",
                "연결 실패: 2.4GHz SSID와 비밀번호를 확인하세요. 기존 설정은 유지됩니다.");
    return;
  }

  Preferences prefs;
  if (!prefs.begin("ee04wifi", false)) {
    server.send(500, "text/plain; charset=utf-8", "설정 저장소를 열지 못했습니다.");
    return;
  }
  const size_t ssidWritten = prefs.putString("ssid", ssid);
  const size_t passWritten = prefs.putString("pass", pass);
  prefs.end();
  if (ssidWritten == 0 || (pass.length() > 0 && passWritten == 0)) {
    server.send(500, "text/plain; charset=utf-8", "Wi-Fi 설정 저장에 실패했습니다.");
    return;
  }

  Serial.printf("[wifi] 새 설정 연결 성공 IP=%s RSSI=%d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  server.send(200, "text/plain; charset=utf-8",
              String("연결 확인 및 저장 완료 · IP ") + WiFi.localIP().toString() +
              " · 장치를 재시작합니다.");
  pendingRestart = true;
}

static void hList() {
  String j = "{\"total\":" + String(fsReady ? FFat.totalBytes() : 0) +
             ",\"used\":" + String(fsReady ? FFat.usedBytes() : 0) + ",\"files\":[";
  File dir = fsReady ? FFat.open("/img") : File();
  bool first = true;
  if (dir) for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    if (f.isDirectory()) continue;
    if (!first) j += ",";
    j += "\"" + photoName(f.name()) + "\"";
    first = false;
  }
  j += "]}";
  server.send(200, "application/json", j);
}

static void hDel() {
  const String f = server.arg("f");
  const bool ok = fsReady && f.length() && FFat.remove("/img/" + f);
  server.send(ok ? 200 : 400, "text/plain", ok ? "OK" : "FAIL");
}

static void hDelAll() {
  File dir = fsReady ? FFat.open("/img") : File();
  if (dir) {
    String names[64]; int n = 0;
    for (File f = dir.openNextFile(); f && n < 64; f = dir.openNextFile())
      if (!f.isDirectory()) names[n++] = photoName(f.name());
    for (int i = 0; i < n; i++) FFat.remove("/img/" + names[i]);
  }
  photoIndex = 0;
  server.send(200, "text/plain", "OK");
}

static void hShowFile() {
  const String f = server.arg("f");
  File fp = fsReady ? FFat.open("/img/" + f, "r") : File();
  if (!fp || fp.size() != frameSize) { server.send(404, "text/plain", "NF"); return; }
  fp.read(frame, frameSize); fp.close();
  pendingShow = true;
  server.send(200, "text/plain", "OK");
}

// 업로드 스트리밍: /show 는 메모리로, /save 는 파일로 직접 씁니다.
static void hUploadData() {
  HTTPUpload& up = server.upload();
  const bool toFile = server.uri().startsWith("/save");

  if (up.status == UPLOAD_FILE_START) {
    received = 0;
    if (toFile) {
      if (!fsReady) return;
      if (!FFat.exists("/img")) FFat.mkdir("/img");
      String nm = server.arg("name");
      nm.replace("/", "_"); nm.replace("\\", "_"); nm.replace("\"", "_");
      if (nm.length() == 0) nm = "img" + String(millis());
      if (nm.length() > 24) nm = nm.substring(0, 24);
      uploadName = nm + ".bin";
      uploadFile = FFat.open("/img/" + uploadName, "w");
    }
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (toFile) {
      if (uploadFile) { uploadFile.write(up.buf, up.currentSize); received += up.currentSize; }
    } else if (received + up.currentSize <= frameSize) {
      memcpy(frame + received, up.buf, up.currentSize);
      received += up.currentSize;
    }
  } else if (up.status == UPLOAD_FILE_END) {
    if (toFile && uploadFile) uploadFile.close();
  }
}

static void hSaveDone() {
  if (received != frameSize) {
    if (fsReady) FFat.remove("/img/" + uploadName);
    server.send(400, "text/plain", "size");
    return;
  }
  Serial.printf("[fs] 저장 %s\n", uploadName.c_str());
  server.send(200, "text/plain", "OK");
}

static void hShowDone() {
  if (received != frameSize) { server.send(400, "text/plain", "size"); return; }
  pendingShow = true;
  server.send(200, "text/plain", "OK");
}

// ============================================================ 슬립

static void goSleep() {
  Serial.printf("[sleep] %llu시간 후 또는 버튼으로 기상\n", WAKE_HOURS);
  Serial.flush();
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);

  // 버튼을 놓을 때까지 기다립니다. 누른 채로 잠들면 즉시 다시 깨어납니다.
  while (digitalRead(KEY1) == LOW || digitalRead(KEY2) == LOW ||
         digitalRead(KEY3) == LOW) delay(20);
  delay(80);

  // pinMode의 내부 풀업은 딥슬립에서 꺼집니다. RTC 도메인 풀업을 따로 켜야
  // 핀이 뜨지 않고, 그래야 오작동 기상이 생기지 않습니다.
  const gpio_num_t keys[] = { KEY1, KEY2, KEY3 };
  for (gpio_num_t g : keys) {
    rtc_gpio_pullup_en(g);
    rtc_gpio_pulldown_dis(g);
  }

  esp_sleep_enable_timer_wakeup(WAKE_HOURS * 3600ULL * 1000000ULL);
  esp_sleep_enable_ext1_wakeup(BTN_MASK, ESP_EXT1_WAKEUP_ANY_LOW);
  esp_deep_sleep_start();
}

static void webMode() {
  const bool needsSetup = !hasWifiConfig();
  const bool connected = wifiUp(10000);
  // 관리 중에는 STA 연결 성공 여부와 무관하게 복구용 AP를 유지합니다.
  // 새 Wi-Fi 검증에 실패해도 192.168.4.1 관리 화면이 끊기지 않습니다.
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  const IPAddress apIp(192, 168, 4, 1);
  WiFi.softAPConfig(apIp, apIp, IPAddress(255, 255, 255, 0));
  if (!WiFi.softAP(SETUP_AP_SSID, SETUP_AP_PASS, 1, false, 4)) {
    Serial.println("[wifi] 설정 AP 시작 실패");
    goSleep();
  }
  Serial.print("[wifi] 설정 AP: "); Serial.print(SETUP_AP_SSID);
  Serial.print(" / "); Serial.print(SETUP_AP_PASS);
  Serial.print(" / http://"); Serial.println(WiFi.softAPIP());
  server.on("/", HTTP_GET, hRoot);
  server.on("/api/wifi/status", HTTP_GET, hWifiStatus);
  server.on("/api/wifi", HTTP_POST, hWifiSave);
  server.on("/api/list", HTTP_GET, hList);
  server.on("/api/del", HTTP_GET, hDel);
  server.on("/api/delall", HTTP_GET, hDelAll);
  server.on("/api/show", HTTP_GET, hShowFile);
  server.on("/save", HTTP_POST, hSaveDone, hUploadData);
  server.on("/show", HTTP_POST, hShowDone, hUploadData);
  server.onNotFound(hCaptivePortal);
  server.begin();
  dnsServer.start(53, "*", WiFi.softAPIP());

  if (connected) {
    Serial.print("[web] 공유기 경유: http://"); Serial.println(WiFi.localIP());
  }
  Serial.print("[web] 설정 AP 경유: http://"); Serial.println(WiFi.softAPIP());

  // 진입에 사용한 KEY3를 먼저 놓게 한 뒤부터 '다시 눌러 종료'를 감시합니다.
  while (digitalRead(KEY3) == LOW) delay(10);
  delay(80);

  const uint32_t until = millis() + WEB_MINUTES * 60000UL;
  // Wi-Fi가 아직 등록되지 않은 장치는 사용자가 저장할 때까지 AP를 닫지 않습니다.
  while (needsSetup || millis() < until) {
    dnsServer.processNextRequest();
    server.handleClient();
    if (pendingRestart) {
      delay(800);
      ESP.restart();
    }
    if (pendingShow) { pendingShow = false; showFrame(); }
    if (digitalRead(KEY3) == LOW) {         // KEY3를 다시 누르면 즉시 종료
      delay(50);
      if (digitalRead(KEY3) == LOW) break;
    }
    delay(2);
  }
  Serial.println("[web] 종료");
  dnsServer.stop();
  server.stop();
}

// ============================================================ 본체

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n=== ePaper frame ===");

  pinMode(KEY1, INPUT_PULLUP);
  pinMode(KEY2, INPUT_PULLUP);
  pinMode(KEY3, INPUT_PULLUP);

  const esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  Serial.printf("[boot] wakeup cause=%d\n", (int)cause);

  // 초기화보다 먼저 판정하여 플래시 직후/리셋 직후에도 KEY3 2초가 정확히
  // 동작하게 합니다. millis() 기준 2초까지 계속 눌려 있으면 진입합니다.
  bool key3Long = digitalRead(KEY3) == LOW;
  while (key3Long && millis() < 2000) {
    delay(10);
    key3Long = digitalRead(KEY3) == LOW;
  }

  epaper.begin();
  frameSize = (size_t)EPD_WIDTH * EPD_HEIGHT / 2;
  frame = (uint8_t*)ps_malloc(frameSize);
  if (!frame) { Serial.println("[err] PSRAM 실패 (OPI PSRAM 확인)"); goSleep(); }

  fsReady = FFat.begin(true);
  if (!fsReady) Serial.println("[err] FFat 마운트 실패 (FATFS 파티션 확인)");
  else Serial.printf("[fs] FFat %u / %u 바이트 사용\n",
                     (unsigned)FFat.usedBytes(), (unsigned)FFat.totalBytes());

  // Wi-Fi 미등록 첫 부팅과 어느 종류의 리셋에서도 KEY3 길게 누르기를
  // 설정 모드로 처리합니다. 설정을 완료하기 전에는 잠들지 않습니다.
  if (key3Long || !hasWifiConfig()) {
    if (!hasWifiConfig()) Serial.println("[wifi] 최초 설정 모드 자동 진입");
    webMode();
    goSleep();
  }

  if (cause == ESP_SLEEP_WAKEUP_EXT1) {
    const uint64_t pins = esp_sleep_get_ext1_wakeup_status();

    if (pins & (1ULL << KEY3)) {
      // 짧게 눌러 깨운 경우는 무시합니다. 길게 누르기는 위에서 처리했습니다.
      Serial.println("[key] KEY3 짧게 눌림 — 무시");

    } else if (pins & (1ULL << KEY1)) {
      showPhoto(+1);

    } else if (pins & (1ULL << KEY2)) {
      if (!fetchCalendar()) Serial.println("[cal] 실패");
    }
  } else {
    // 타이머 기상 또는 첫 부팅
    if (!fetchCalendar()) Serial.println("[cal] 실패 — 화면 유지");
  }

  goSleep();
}

void loop() {}
