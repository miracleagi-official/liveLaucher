# Live Launcher

config.json에 정의된 서비스들을 순차적으로 실행하는 런처입니다.

## 동작 방식

1. `config.json` 배열의 순서대로 각 항목 처리
2. 지정된 포트로 접속 테스트 수행
3. 접속 실패 시 프로그램 실행 후 재시도
4. 모든 항목 처리 완료 후 결과 대화상자 표시
5. 확인 버튼 클릭 또는 10초 후 자동 종료

## config.json 형식

```json
[
    {
        "name": "서비스 이름",
        "path": "실행 파일 경로",
        "executable": "실행 파일명",
        "PORT": 포트번호,
        "host": "호스트 주소 (선택, 기본값: 127.0.0.1)"
    }
]
```

## 설정 옵션 (main.py 상단)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CONNECTION_TIMEOUT` | 2초 | 소켓 연결 타임아웃 |
| `MAX_RETRY` | 5회 | 프로그램 실행 후 최대 재시도 횟수 |
| `RETRY_INTERVAL` | 2초 | 재시도 간격 |
| `AUTO_CLOSE_DELAY` | 10초 | 완료 대화상자 자동 종료 시간 |

## 실행 방법

```bash
python main.py
```

## 요구사항

- Python 3.11+
- tkinter (Python 표준 라이브러리)