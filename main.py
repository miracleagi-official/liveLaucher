"""
file : main.py
desc : entry point of liveLaucher
author : gbox3d
date : 2026-01-13
desc : main entry point of liveLaucher
plase do not edit this commented block!
"""

import json
import socket
import subprocess
import time
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 설정
# CONFIG_FILE = "config.json"

# 실행 파일(frozen)인지 스크립트인지 구분하여 기본 경로 설정
# 실행 파일(frozen)인지 스크립트인지 구분하여 기본 경로 설정
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent


env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

# 3. 설정값 읽기 (환경변수 -> 없으면 기본값)
# .env의 값은 문자열이므로 int() 변환이 필수입니다.
CONFIG_FILE = BASE_DIR / "config.json"
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", 2))
MAX_RETRY = int(os.getenv("MAX_RETRY", 5))
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 2))
AUTO_CLOSE_DELAY = int(os.getenv("AUTO_CLOSE_DELAY", 10))


def load_config(config_path: str) -> list:
    """config.json 파일을 로드합니다."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] 설정 파일을 찾을 수 없습니다: {config_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[ERROR] 설정 파일 파싱 오류: {e}")
        sys.exit(1)


def check_port(host: str, port: int, timeout: float = CONNECTION_TIMEOUT) -> bool:
    """지정된 포트에 접속 가능한지 테스트합니다."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            return result == 0
    except socket.error as e:
        print(f"  [SOCKET ERROR] {e}")
        return False


def launch_program(item: dict) -> subprocess.Popen | None:
    """프로그램을 실행합니다."""
    path = item.get("path", "")
    executable = item.get("executable", "")
    
    if not path or not executable:
        print(f"  [ERROR] path 또는 executable이 설정되지 않았습니다.")
        return None
    
    full_path = Path(path) / executable
    
    if not full_path.exists():
        print(f"  [ERROR] 실행 파일을 찾을 수 없습니다: {full_path}")
        return None
    
    try:
        # 작업 디렉토리를 프로그램 경로로 설정하고 실행
        process = subprocess.Popen(
            str(full_path),
            cwd=path,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print(f"  [LAUNCHED] PID: {process.pid}")
        return process
    except Exception as e:
        print(f"  [ERROR] 프로그램 실행 실패: {e}")
        return None


def process_item(item: dict) -> bool:
    """단일 항목을 처리합니다."""
    name = item.get("name", "Unknown")
    port = item.get("PORT", 0)
    host = item.get("host", "127.0.0.1")  # 기본값 localhost
    
    print(f"\n[{name}] 처리 중...")
    print(f"  포트: {port}, 호스트: {host}")
    
    # port 가 0 이면 바로 프로그램 실행 시도
    if port == 0:
        print(f"  포트가 0으로 설정되어 있습니다. 프로그램을 바로 실행합니다.")
        process = launch_program(item)
        return process is not None
    
    # 1. 먼저 포트 접속 테스트
    print(f"  접속 테스트 중...")
    if check_port(host, port):
        print(f"  [OK] 이미 실행 중입니다.")
        return True
    
    print(f"  [FAIL] 접속 실패 - 프로그램 실행 시도...")
    
    # 2. 프로그램 실행
    process = launch_program(item)
    if process is None:
        print(f"  [ERROR] 프로그램 실행에 실패했습니다.")
        return False
    
    # 3. 재시도 (프로그램이 시작될 때까지 대기)
    print(f"  프로그램 시작 대기 중...")
    for attempt in range(1, MAX_RETRY + 1):
        time.sleep(RETRY_INTERVAL)
        print(f"  재시도 {attempt}/{MAX_RETRY}...")
        
        if check_port(host, port):
            print(f"  [OK] 연결 성공!")
            return True
    
    print(f"  [FAIL] 최대 재시도 횟수 초과")
    return False


def countdown_exit(seconds: int):
    """카운트다운 후 종료합니다."""
    print(f"\n{seconds}초 후 자동 종료됩니다. (Enter 키를 누르면 즉시 종료)")
    
    # Windows에서 non-blocking input 처리
    if sys.platform == "win32":
        import msvcrt
        for remaining in range(seconds, 0, -1):
            print(f"\r{remaining}초...", end="", flush=True)
            # 1초 동안 100ms 간격으로 키 입력 체크
            for _ in range(10):
                if msvcrt.kbhit():
                    msvcrt.getch()  # 입력 버퍼 비우기
                    print("\r종료합니다.    ")
                    return
                time.sleep(0.1)
    else:
        # Linux/Mac
        import select
        for remaining in range(seconds, 0, -1):
            print(f"\r{remaining}초...", end="", flush=True)
            # select로 stdin 입력 대기 (1초 타임아웃)
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if ready:
                sys.stdin.readline()
                print("\r종료합니다.    ")
                return
    
    print("\r종료합니다.    ")


def main():
    print("=" * 50)
    print("Live Launcher 시작")
    print("=" * 50)
    
    # 설정 파일 로드
    config = load_config(CONFIG_FILE)
    
    if not config:
        print("[ERROR] 설정이 비어있습니다.")
        sys.exit(1)
    
    print(f"총 {len(config)}개 항목을 처리합니다.")
    
    # 각 항목 순서대로 처리
    success_count = 0
    failed_items = []
    
    for item in config:
        name = item.get("name", "Unknown")
        if process_item(item):
            success_count += 1
        else:
            failed_items.append(name)
    
    # 결과 출력
    print("\n" + "=" * 50)
    print(f"처리 완료: {success_count}/{len(config)} 성공")
    if failed_items:
        print(f"실패 항목: {', '.join(failed_items)}")
    else:
        print("모든 작업이 성공적으로 완료되었습니다!")
    print("=" * 50)
    
    # 카운트다운 후 종료
    countdown_exit(AUTO_CLOSE_DELAY)


if __name__ == "__main__":
    main()