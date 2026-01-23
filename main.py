"""
file : main.py
desc : entry point of liveLaucher
author : gbox3d
date : 2026-01-14
desc : Supports both standard executables and Windows Services
"""

import json
import socket
import subprocess
import time
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# --- 초기 설정 ---

# 실행 파일(frozen)인지 스크립트인지 구분하여 기본 경로 설정
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

# 환경 변수 및 설정 로드
CONFIG_FILE = BASE_DIR / "config.json"
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", 2))
MAX_RETRY = int(os.getenv("MAX_RETRY", 5))
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 2))
AUTO_CLOSE_DELAY = int(os.getenv("AUTO_CLOSE_DELAY", 10))


def load_config(config_path: Path) -> list:
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
        # 소켓 에러는 보통 연결 실패로 간주하고 로그만 남김
        # print(f"  [SOCKET INFO] {e}") 
        return False


def launch_service(service_name: str) -> bool:
    """Windows 서비스를 시작합니다 (net start 명령어 사용)."""
    print(f"  [SERVICE] 서비스 시작 시도: '{service_name}'")
    
    try:
        # net start 명령어 실행 (관리자 권한 필요)
        cmd = f'net start "{service_name}"'
        # shell=True로 실행하여 시스템 명령어를 호출
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding='cp949') 
        # 한글 윈도우 콘솔 출력을 위해 cp949 인코딩 사용 권장 (또는 utf-8)

        if result.returncode == 0:
            print(f"  [SUCCESS] 서비스 시작 명령 성공")
            return True
        elif result.returncode == 2 or "이미 시작" in result.stdout or "already started" in result.stdout:
            print(f"  [INFO] 서비스가 이미 실행 중입니다.")
            return True
        else:
            print(f"  [ERROR] 서비스 시작 실패: {result.stderr.strip()}")
            # net start의 경우 stdout에 에러 메시지가 나올 때도 있음
            if result.stdout: print(f"  [MSG] {result.stdout.strip()}")
            return False
            
    except Exception as e:
        print(f"  [ERROR] 서비스 제어 중 예외 발생: {e}")
        return False


def launch_executable(item: dict) -> subprocess.Popen | None:
    """일반 실행 파일을 실행합니다."""
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


def launch_program(item: dict):
    """항목 타입(서비스/실행파일)에 따라 적절한 실행 함수를 호출합니다."""
    if item.get("is_service", False):
        service_name = item.get("service_name", "")
        if not service_name:
            print("  [ERROR] 서비스 이름(service_name)이 누락되었습니다.")
            return None
        # 서비스 실행 성공 시 True 리턴
        return launch_service(service_name)
    else:
        # 일반 실행 파일 실행
        return launch_executable(item)


def process_item(item: dict) -> bool:
    """단일 항목을 처리합니다."""
    name = item.get("name", "Unknown")
    port = item.get("PORT", 0)
    host = item.get("host", "127.0.0.1")  # 기본값 localhost
    
    print(f"\n[{name}] 처리 중...")
    print(f"  포트: {port}, 호스트: {host}")
    
    # port가 0이면 연결 확인 없이 바로 실행 시도
    if port == 0:
        print(f"  포트가 0으로 설정되어 있습니다. 프로그램을 바로 실행합니다.")
        result = launch_program(item)
        # 서비스는 True/False, 프로세스는 Popen 객체 반환. 둘 다 Truthy면 성공
        return result is not None and result is not False
    
    # 1. 먼저 포트 접속 테스트
    print(f"  접속 테스트 중...")
    if check_port(host, port):
        print(f"  [OK] 이미 실행 중입니다.")
        return True
    
    print(f"  [FAIL] 접속 실패 - 프로그램/서비스 실행 시도...")
    
    # 2. 프로그램/서비스 실행
    launch_result = launch_program(item)
    
    # 실행 자체가 실패한 경우 (파일 없음, 권한 오류 등)
    if not launch_result:
        print(f"  [ERROR] 실행 명령 실패.")
        return False
    
    # 3. 재시도 (실행 후 포트가 열릴 때까지 대기)
    print(f"  서비스 활성화 대기 중...")
    for attempt in range(1, MAX_RETRY + 1):
        time.sleep(RETRY_INTERVAL)
        print(f"  확인 중 {attempt}/{MAX_RETRY}...")
        
        if check_port(host, port):
            print(f"  [OK] 연결 성공!")
            return True
    
    print(f"  [FAIL] 최대 재시도 횟수 초과 (실행은 되었으나 포트가 응답하지 않음)")
    return False


def countdown_exit(seconds: int):
    """카운트다운 후 종료합니다."""
    print(f"\n{seconds}초 후 자동 종료됩니다. (Enter 키를 누르면 즉시 종료)")
    
    # Windows에서 non-blocking input 처리
    if sys.platform == "win32":
        import msvcrt
        # 입력 버퍼 비우기 (이전 키 입력 잔재 제거)
        while msvcrt.kbhit():
            msvcrt.getch()
            
        for remaining in range(seconds, 0, -1):
            print(f"\r{remaining}초...   ", end="", flush=True)
            # 1초 동안 100ms 간격으로 키 입력 체크
            for _ in range(10):
                if msvcrt.kbhit():
                    msvcrt.getch()  # 키 입력 소비
                    print("\n즉시 종료합니다.")
                    return
                time.sleep(0.1)
    else:
        # Linux/Mac
        import select
        for remaining in range(seconds, 0, -1):
            print(f"\r{remaining}초...", end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if ready:
                sys.stdin.readline()
                print("\n즉시 종료합니다.")
                return
    
    print("\n종료합니다.")


def main():
    # 관리자 권한 확인 (윈도우 서비스 제어 시 필수)
    is_admin = False
    try:
        import ctypes
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        pass

    print("=" * 60)
    print("Live Launcher Service Manager")
    if not is_admin:
        print("[WARNING] 관리자 권한이 없습니다. 서비스 시작(net start)이 실패할 수 있습니다.")
    print("=" * 60)
    
    # 설정 파일 로드
    config = load_config(CONFIG_FILE)
    
    if not config:
        print("[ERROR] 설정이 비어있습니다.")
        sys.exit(1)
    
    print(f"총 {len(config)}개 항목을 처리합니다.")
    
    success_count = 0
    failed_items = []
    
    # 각 항목 처리
    for item in config:
        if process_item(item):
            success_count += 1
        else:
            failed_items.append(item.get("name", "Unknown"))
    
    # 결과 출력
    print("\n" + "=" * 60)
    print(f"처리 완료: {success_count}/{len(config)} 성공")
    if failed_items:
        print(f"실패 항목: {', '.join(failed_items)}")
    else:
        print("모든 작업이 성공적으로 완료되었습니다!")
    print("=" * 60)
    
    countdown_exit(AUTO_CLOSE_DELAY)


if __name__ == "__main__":
    main()