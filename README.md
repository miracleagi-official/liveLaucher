# Live Launcher 1.0.1

`config.json`에 정의된 항목을 순차적으로 실행하는 Tkinter GUI 런처입니다.

## 주요 기능

- `Run All`로 등록된 항목을 위에서 아래 순서대로 실행
- 각 항목 실행 후 지정 포트 응답 확인
- 실패 시 즉시 중단하고 경고 대화상자 표시
- `Add`, `Edit`, `Delete`로 구성 관리
- `Move Up`, `Move Down`으로 실행 순서 변경
- 변경 사항 자동 저장 (`config.json`)
- 상태 색상 표시 (`Pending`, `Running`, `Success`, `Failed`, `Stopped`)

## 현재 기본 구성

현재 `config.json` 기준 기본 실행 순서는 다음과 같습니다.

1. `audio_output_device` -> `audioMi_server_le.exe` -> `26070`
2. `live Speaker 화자분리기` -> `livespeaker.exe` -> `26075`
3. `live_text_app` -> `LiveTextApp.exe` -> 포트 체크 없음
4. `liveText_Inspector` -> `liveText_Inspector.exe` -> `26073`

`Run All`을 누르면 위 순서대로 실행되며, 각 항목은 다음 항목으로 넘어가기 전에 지정 포트 응답 여부를 확인합니다.

## 실행 방법

```bash
python main.py
```

PyInstaller 빌드 시 `LiveLauncher.spec`는 GUI 모드(`console=False`)로 설정되어 있습니다.

## config.json 형식

### 실행 파일

```json
[
    {
        "name": "audio_output_device",
        "path": "C:\\tools\\audioMi",
        "executable": "audioMi_server_le.exe",
        "PORT": 26070
    }
]
```

### Windows Service

```json
[
    {
        "name": "database_service",
        "is_service": true,
        "service_name": "MyDatabaseService",
        "PORT": 5432
    }
]
```

## 필드 설명

| 키 | 설명 |
|----|------|
| `name` | UI에 표시할 이름 |
| `path` | 실행 파일이 있는 폴더 |
| `executable` | 실행 파일명 |
| `is_service` | `true`이면 Windows Service로 처리 |
| `service_name` | `net start`에 넘길 서비스 이름 |
| `PORT` | 실행 후 응답 확인할 포트. 생략 또는 `0`이면 확인 없이 실행 |
| `host` | 포트 확인 대상 호스트. 기본값은 `127.0.0.1` |

## 환경 변수

`.env`에서 다음 값을 조정할 수 있습니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTO_START` | `false` | 런처 시작 후 `Run All` 자동 실행 여부 |
| `AUTO_CLOSE` | `false` | 모든 항목이 성공했을 때 런처 자동 종료 여부 |
| `AUTO_CLOSE_DELAY` | `10` | 자동 종료까지 대기 시간(초) |
| `AUTO_START_DELAY_MS` | `800` | 앱 시작 후 자동 실행까지 대기 시간(ms) |
| `CONNECTION_TIMEOUT` | `2` | 소켓 연결 타임아웃(초) |
| `MAX_RETRY` | `5` | 포트 확인 최대 재시도 횟수 |
| `RETRY_INTERVAL` | `2` | 재시도 간격(초) |

## 작업 기록

### 2026-03-04 기준 반영 내용

- 기존 CLI 중심 흐름을 Tkinter GUI 런처로 정리했습니다.
- `config.json`을 읽어 실행 목록을 표 형태로 보여주도록 구성했습니다.
- `Run All` 실행 시 항목을 위에서 아래 순서대로 순차 실행하도록 구현했습니다.
- 각 항목 실행 후 지정된 `host:PORT`에 대해 연결 가능 여부를 재시도 방식으로 확인하도록 넣었습니다.
- 포트가 비어 있거나 `0`이면 헬스 체크 없이 다음 단계로 진행하도록 처리했습니다.
- `Add`, `Edit`, `Delete`, `Move Up`, `Move Down` 기능을 넣어 GUI에서 실행 구성을 직접 관리할 수 있게 했습니다.
- 편집 다이얼로그에서 `executable` 타입과 `service` 타입을 나눠 입력받도록 구성했습니다.
- `config.json` 로드 시 정규화, 저장 시 직렬화 과정을 분리해 포맷이 안정적으로 유지되게 했습니다.
- 잘못된 `PORT`, 잘못된 JSON 구조 등 설정 오류를 `ConfigError`로 정리했습니다.
- 실행 상태를 `Pending`, `Running`, `Success`, `Failed`, `Stopped`로 관리하고 색상으로 표시하도록 넣었습니다.
- 진행 상태 바, 현재 작업 텍스트, 로그 출력 창을 통해 런처 동작을 UI에서 추적할 수 있게 했습니다.
- 실행 작업은 백그라운드 스레드에서 처리하고, UI 갱신은 큐 기반으로 메인 스레드에 전달하도록 구성했습니다.
- Windows Service 항목은 `net start` 기반으로 시작하도록 넣었습니다.
- 일반 실행 파일은 `subprocess.Popen`으로 실행하며, `.exe`의 PE subsystem을 확인해 콘솔 프로그램과 GUI 프로그램을 다르게 띄우도록 처리했습니다.
- 관리자 권한이 아닐 때 Service 시작 실패 가능성을 상단 경고 배너로 안내하도록 추가했습니다.
- `.env`에서 `CONNECTION_TIMEOUT`, `MAX_RETRY`, `RETRY_INTERVAL`을 읽도록 연결했습니다.
- `AUTO_START`, `AUTO_CLOSE`, `AUTO_CLOSE_DELAY` 기반 자동 실행/자동 종료 옵션을 추가했습니다.
- PyInstaller 빌드를 고려해 스크립트 실행과 frozen 실행에서 모두 `BASE_DIR`를 올바르게 계산하도록 정리했습니다.

### 확인된 동작 기준

- 실행 파일 경로가 없으면 즉시 실패로 표시됩니다.
- 포트 응답이 끝까지 오지 않으면 해당 단계에서 중단되고 경고 대화상자를 표시합니다.
- 이미 시작된 Windows Service는 성공으로 간주하도록 처리했습니다.
- 콘솔 프로그램은 새 콘솔로 실행하고, GUI 프로그램은 표준 출력 연결 없이 실행하도록 분기합니다.

### 후속으로 기록해둘 만한 항목

- `liveSpeaker`처럼 자체 TCP 서버를 여는 프로세스는 중복 실행 시 포트 충돌 가능성이 있으므로, 런처 차원에서 중복 실행 방지 또는 기존 프로세스 점검 로직을 추가할 여지가 있습니다.
- 현재 README는 사용법 중심 문서이므로, 이후 배포 절차나 장애 대응 절차가 정리되면 별도 섹션으로 추가하는 것이 좋습니다.
