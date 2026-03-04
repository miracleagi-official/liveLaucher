"""
file : main.py
desc : GUI entry point of liveLaucher
author : gbox3d
date : 2026-03-04
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from dotenv import load_dotenv

__version__ = "1.0.1"

DEFAULT_HOST = "127.0.0.1"
STATUS_PENDING = "Pending"
STATUS_RUNNING = "Running"
STATUS_SUCCESS = "Success"
STATUS_FAILED = "Failed"
STATUS_STOPPED = "Stopped"
EVENT_POLL_INTERVAL_MS = 100
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
IMAGE_SUBSYSTEM_WINDOWS_CUI = 3


class ConfigError(ValueError):
    """Raised when config.json cannot be interpreted."""


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

CONFIG_FILE = BASE_DIR / "config.json"
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", 2))
MAX_RETRY = int(os.getenv("MAX_RETRY", 5))
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 2))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_non_negative_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


AUTO_START = env_bool("AUTO_START", False)
AUTO_CLOSE = env_bool("AUTO_CLOSE", False)
AUTO_CLOSE_DELAY = env_non_negative_int("AUTO_CLOSE_DELAY", 10)
AUTO_START_DELAY_MS = env_non_negative_int("AUTO_START_DELAY_MS", 800)


def is_admin() -> bool:
    """Return True when the current process has admin privileges."""
    try:
        import ctypes

        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def parse_port(value: object) -> int:
    """Normalize config port values."""
    if value in (None, ""):
        return 0

    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"유효하지 않은 PORT 값입니다: {value}") from exc

    if not 0 <= port <= 65535:
        raise ConfigError(f"PORT 범위가 잘못되었습니다: {port}")

    return port


def normalize_item(raw_item: dict) -> dict:
    """Return a UI-friendly config item with predictable keys."""
    if not isinstance(raw_item, dict):
        raise ConfigError("config.json 항목은 객체여야 합니다.")

    item = {
        "name": str(raw_item.get("name", "")).strip() or "Unnamed",
        "host": str(raw_item.get("host", DEFAULT_HOST)).strip() or DEFAULT_HOST,
        "PORT": parse_port(raw_item.get("PORT", 0)),
        "is_service": bool(raw_item.get("is_service", False)),
    }

    if item["is_service"]:
        item["service_name"] = str(raw_item.get("service_name", "")).strip()
    else:
        item["path"] = str(raw_item.get("path", "")).strip()
        item["executable"] = str(raw_item.get("executable", "")).strip()

    return item


def serialize_item(raw_item: dict) -> dict:
    """Persist only the fields needed by config.json."""
    item = normalize_item(raw_item)
    data = {"name": item["name"]}

    if item["is_service"]:
        data["is_service"] = True
        if item.get("service_name"):
            data["service_name"] = item["service_name"]
    else:
        data["path"] = item.get("path", "")
        data["executable"] = item.get("executable", "")

    if item["PORT"] > 0:
        data["PORT"] = item["PORT"]

    if item["host"] != DEFAULT_HOST:
        data["host"] = item["host"]

    return data


def load_config(config_path: Path) -> list[dict]:
    """Load config.json and normalize its structure."""
    if not config_path.exists():
        return []

    try:
        with config_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"설정 파일 파싱 오류: {exc}") from exc

    if not isinstance(data, list):
        raise ConfigError("config.json 최상위 구조는 배열이어야 합니다.")

    return [normalize_item(item) for item in data]


def save_config(config_path: Path, items: list[dict]) -> None:
    """Save config.json in a stable format."""
    payload = [serialize_item(item) for item in items]
    with config_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4, ensure_ascii=False)
        file.write("\n")


def check_port(host: str, port: int, timeout: float = CONNECTION_TIMEOUT) -> bool:
    """Return True if the given TCP port is reachable."""
    if port <= 0:
        return False

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except socket.error:
        return False


def decode_windows_output(payload: bytes) -> str:
    """Decode subprocess output from common Windows encodings."""
    if not payload:
        return ""

    for encoding in ("cp949", "utf-8", sys.getfilesystemencoding() or "utf-8"):
        try:
            return payload.decode(encoding).strip()
        except UnicodeDecodeError:
            continue

    return payload.decode("utf-8", errors="replace").strip()


def get_pe_subsystem(executable_path: Path) -> int | None:
    """Return the PE subsystem value for a Windows executable."""
    try:
        with executable_path.open("rb") as file:
            if file.read(2) != b"MZ":
                return None

            file.seek(0x3C)
            pe_offset = struct.unpack("<I", file.read(4))[0]
            file.seek(pe_offset)
            if file.read(4) != b"PE\x00\x00":
                return None

            optional_header_start = pe_offset + 4 + 20
            file.seek(optional_header_start)
            magic = struct.unpack("<H", file.read(2))[0]

            if magic not in (0x10B, 0x20B):
                return None

            file.seek(optional_header_start + 68)
            return struct.unpack("<H", file.read(2))[0]
    except (OSError, struct.error):
        return None


def launch_service(service_name: str) -> tuple[bool, str]:
    """Start a Windows service via `net start`."""
    if not service_name:
        return False, "service_name 값이 비어 있습니다."

    try:
        completed = subprocess.run(
            ["net", "start", service_name],
            capture_output=True,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, f"서비스 시작 중 예외 발생: {exc}"

    stdout = decode_windows_output(completed.stdout)
    stderr = decode_windows_output(completed.stderr)
    combined_message = " / ".join(
        part for part in (stdout, stderr) if part
    ) or f"net start 종료 코드: {completed.returncode}"

    lowered = combined_message.lower()
    already_started = "already started" in lowered or "이미 시작" in combined_message

    if completed.returncode == 0 or already_started:
        return True, combined_message

    return False, combined_message


def launch_executable(item: dict) -> tuple[bool, str]:
    """Start a standard executable or batch file."""
    path_text = item.get("path", "")
    executable = item.get("executable", "")

    if not path_text or not executable:
        return False, "path 또는 executable 값이 비어 있습니다."

    target = Path(path_text) / executable
    if not target.exists():
        return False, f"실행 파일을 찾을 수 없습니다: {target}"

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    kwargs = {"cwd": str(target.parent), "creationflags": creationflags}

    subsystem = get_pe_subsystem(target) if target.suffix.lower() == ".exe" else None
    if subsystem == IMAGE_SUBSYSTEM_WINDOWS_CUI:
        kwargs["creationflags"] = creationflags | getattr(
            subprocess, "CREATE_NEW_CONSOLE", 0
        )
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL

    try:
        if target.suffix.lower() in {".bat", ".cmd"}:
            process = subprocess.Popen(str(target), shell=True, **kwargs)
        else:
            process = subprocess.Popen([str(target)], shell=False, **kwargs)
    except Exception as exc:
        return False, f"프로그램 실행 실패: {exc}"

    time.sleep(0.5)
    return_code = process.poll()
    if return_code is not None:
        return False, f"프로그램이 즉시 종료되었습니다. (exit code: {return_code})"

    return True, f"프로그램 실행 완료 (PID: {process.pid})"


def launch_program(item: dict) -> tuple[bool, str]:
    """Dispatch to the appropriate launcher for the item type."""
    if item.get("is_service", False):
        return launch_service(item.get("service_name", ""))

    return launch_executable(item)


def wait_with_stop(seconds: float, stop_event: Event) -> bool:
    """Wait for the given duration and stop early if requested."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return True
        time.sleep(0.1)
    return stop_event.is_set()


def process_item(
    item: dict, stop_event: Event, reporter: callable
) -> tuple[bool, str, bool]:
    """Process a single launcher item and return (success, message, stopped)."""
    name = item.get("name", "Unnamed")
    host = item.get("host", DEFAULT_HOST)
    port = parse_port(item.get("PORT", 0))

    reporter(f"[{name}] 항목 처리 시작")

    if stop_event.is_set():
        return False, "사용자 요청으로 중지되었습니다.", True

    reporter(f"[{name}] 실행 명령 전송")
    success, message = launch_program(item)
    reporter(f"[{name}] {message}")

    if not success:
        return False, message, False

    if port <= 0:
        reporter(f"[{name}] PORT 미설정: ping 체크 없이 다음 항목으로 진행")
        return True, "PORT 미설정으로 ping 체크 생략", False

    reporter(f"[{name}] 포트 확인 시작: {host}:{port}")
    for attempt in range(1, MAX_RETRY + 1):
        if wait_with_stop(RETRY_INTERVAL, stop_event):
            return False, "사용자 요청으로 중지되었습니다.", True

        reporter(f"[{name}] 포트 재확인 {attempt}/{MAX_RETRY}")
        if check_port(host, port):
            return True, f"포트 응답 확인 완료 ({host}:{port})", False

    return False, f"포트 응답이 없습니다. ({host}:{port})", False


class ItemDialog(tk.Toplevel):
    """Modal editor for launcher items."""

    def __init__(self, parent: tk.Misc, item: dict | None = None) -> None:
        super().__init__(parent)
        self.result: dict | None = None
        self.title("항목 편집")
        self.resizable(False, False)
        self.transient(parent)

        seed = normalize_item(item or {})
        item_type = "service" if seed.get("is_service") else "executable"

        self.name_var = tk.StringVar(value=seed.get("name", ""))
        self.type_var = tk.StringVar(value=item_type)
        self.path_var = tk.StringVar(value=seed.get("path", ""))
        self.executable_var = tk.StringVar(value=seed.get("executable", ""))
        self.service_name_var = tk.StringVar(value=seed.get("service_name", ""))
        self.host_var = tk.StringVar(value=seed.get("host", DEFAULT_HOST))
        self.port_var = tk.StringVar(value=str(seed.get("PORT", 0) or ""))

        self._build_ui()
        self._sync_type_fields()

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.bind("<Return>", lambda event: self._save())
        self.bind("<Escape>", lambda event: self._cancel())

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.grid(sticky="nsew")

        ttk.Label(container, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.name_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=4
        )

        ttk.Label(container, text="Type").grid(row=1, column=0, sticky="w", pady=4)
        type_box = ttk.Combobox(
            container,
            textvariable=self.type_var,
            values=("executable", "service"),
            state="readonly",
            width=18,
        )
        type_box.grid(row=1, column=1, sticky="w", pady=4)
        type_box.bind("<<ComboboxSelected>>", lambda event: self._sync_type_fields())

        ttk.Label(container, text="Path").grid(row=2, column=0, sticky="w", pady=4)
        self.path_entry = ttk.Entry(container, textvariable=self.path_var, width=42)
        self.path_entry.grid(row=2, column=1, sticky="ew", pady=4)
        self.browse_button = ttk.Button(container, text="Browse...", command=self._browse)
        self.browse_button.grid(row=2, column=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(container, text="Executable").grid(
            row=3, column=0, sticky="w", pady=4
        )
        self.executable_entry = ttk.Entry(
            container, textvariable=self.executable_var, width=42
        )
        self.executable_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(container, text="Service Name").grid(
            row=4, column=0, sticky="w", pady=4
        )
        self.service_entry = ttk.Entry(
            container, textvariable=self.service_name_var, width=42
        )
        self.service_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(container, text="Host").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.host_var, width=42).grid(
            row=5, column=1, columnspan=2, sticky="ew", pady=4
        )

        ttk.Label(container, text="Port").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.port_var, width=42).grid(
            row=6, column=1, columnspan=2, sticky="ew", pady=4
        )

        ttk.Label(
            container,
            text="Port가 비어 있거나 0이면 연결 확인 없이 실행됩니다.",
            foreground="#6b7280",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 12))

        button_row = ttk.Frame(container)
        button_row.grid(row=8, column=0, columnspan=3, sticky="e")
        ttk.Button(button_row, text="Save", command=self._save).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side="left")

        container.columnconfigure(1, weight=1)

    def _sync_type_fields(self) -> None:
        is_service = self.type_var.get() == "service"
        exec_state = "disabled" if is_service else "normal"
        service_state = "normal" if is_service else "disabled"

        self.path_entry.configure(state=exec_state)
        self.executable_entry.configure(state=exec_state)
        self.browse_button.configure(state=exec_state)
        self.service_entry.configure(state=service_state)

    def _browse(self) -> None:
        file_path = filedialog.askopenfilename(
            parent=self,
            title="실행 파일 선택",
            initialdir=self.path_var.get() or str(BASE_DIR),
            filetypes=[
                ("Applications", "*.exe *.bat *.cmd *.com"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return

        selected = Path(file_path)
        self.path_var.set(str(selected.parent))
        self.executable_var.set(selected.name)
        if self.name_var.get().strip() in ("", "Unnamed"):
            self.name_var.set(selected.stem)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        host = self.host_var.get().strip() or DEFAULT_HOST
        port_text = self.port_var.get().strip()

        if not name:
            messagebox.showerror("입력 오류", "Name 값은 비워둘 수 없습니다.", parent=self)
            return

        try:
            port = parse_port(port_text or 0)
        except ConfigError as exc:
            messagebox.showerror("입력 오류", str(exc), parent=self)
            return

        if self.type_var.get() == "service":
            service_name = self.service_name_var.get().strip()
            if not service_name:
                messagebox.showerror(
                    "입력 오류", "Service Name 값은 비워둘 수 없습니다.", parent=self
                )
                return

            self.result = {
                "name": name,
                "is_service": True,
                "service_name": service_name,
                "host": host,
                "PORT": port,
            }
        else:
            path_text = self.path_var.get().strip()
            executable = self.executable_var.get().strip()
            if not path_text or not executable:
                messagebox.showerror(
                    "입력 오류",
                    "Executable 항목은 Path와 Executable 값을 모두 입력해야 합니다.",
                    parent=self,
                )
                return

            self.result = {
                "name": name,
                "path": path_text,
                "executable": executable,
                "host": host,
                "PORT": port,
            }

        self.result = normalize_item(self.result)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def show(self) -> dict | None:
        self.wait_window()
        return self.result


class LiveLauncherApp:
    """Tkinter application for managing and launching services sequentially."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Live Launcher {__version__}")
        self.root.geometry("1100x720")
        self.root.minsize(960, 640)
        self.root.configure(bg="#efe8db")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.event_queue: Queue = Queue()
        self.stop_event = Event()
        self.worker_thread: Thread | None = None
        self.is_running = False
        self.admin_mode = is_admin()
        self.auto_close_after_id: str | None = None
        self.auto_close_remaining = 0

        self.items: list[dict] = []
        self.statuses: list[str] = []

        self.activity_var = tk.StringVar(value="대기 중")
        self.summary_var = tk.StringVar(value="설정 로드 중")
        self.progress_var = tk.DoubleVar(value=0)
        self.auto_start_var = tk.BooleanVar(value=AUTO_START)
        self.auto_close_var = tk.BooleanVar(value=AUTO_CLOSE)
        self.auto_close_delay_var = tk.StringVar(value=str(AUTO_CLOSE_DELAY))
        self.progress_bar: ttk.Progressbar | None = None

        self._configure_style()
        self._build_ui()
        self._load_items()
        self._refresh_tree()
        self._refresh_controls()
        self.root.after(EVENT_POLL_INTERVAL_MS, self._drain_queue)
        self.root.after(AUTO_START_DELAY_MS, self._auto_run_if_enabled)

    def _configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("Accent.TButton", padding=(12, 7), font=("Segoe UI Semibold", 10))
        style.configure("Danger.TButton", padding=(12, 7), font=("Segoe UI Semibold", 10))
        style.map(
            "Accent.TButton",
            background=[("active", "#19535f"), ("!disabled", "#245f6b")],
            foreground=[("!disabled", "white")],
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#8f2d2d"), ("!disabled", "#a33636")],
            foreground=[("!disabled", "white")],
        )

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg="#efe8db", padx=18, pady=16)
        container.pack(fill="both", expand=True)

        header = tk.Frame(container, bg="#efe8db")
        header.pack(fill="x")

        tk.Label(
            header,
            text="Live Launcher",
            font=("Bahnschrift SemiBold", 24),
            bg="#efe8db",
            fg="#20323c",
        ).pack(anchor="w")
        tk.Label(
            header,
            text=f"Version {__version__}  |  순차 실행 GUI 런처",
            font=("Segoe UI", 10),
            bg="#efe8db",
            fg="#586670",
        ).pack(anchor="w", pady=(2, 0))

        if not self.admin_mode:
            tk.Label(
                container,
                text="관리자 권한이 없습니다. Windows Service 시작 시 실패할 수 있습니다.",
                bg="#f4c95d",
                fg="#4a2f00",
                padx=12,
                pady=8,
            ).pack(fill="x", pady=(14, 0))

        control_card = tk.Frame(
            container,
            bg="#fffaf1",
            bd=1,
            relief="solid",
            padx=14,
            pady=12,
        )
        control_card.pack(fill="x", pady=(16, 14))

        self.run_button = ttk.Button(
            control_card, text="Run All", style="Accent.TButton", command=self.run_all
        )
        self.run_button.pack(side="left")

        self.stop_button = ttk.Button(
            control_card, text="Stop", style="Danger.TButton", command=self.stop_run
        )
        self.stop_button.pack(side="left", padx=(8, 16))

        self.add_button = ttk.Button(control_card, text="Add", command=self.add_item)
        self.add_button.pack(side="left")

        self.edit_button = ttk.Button(control_card, text="Edit", command=self.edit_item)
        self.edit_button.pack(side="left", padx=(8, 0))

        self.delete_button = ttk.Button(
            control_card, text="Delete", command=self.delete_item
        )
        self.delete_button.pack(side="left", padx=(8, 0))

        self.up_button = ttk.Button(control_card, text="Move Up", command=self.move_up)
        self.up_button.pack(side="left", padx=(18, 0))

        self.down_button = ttk.Button(
            control_card, text="Move Down", command=self.move_down
        )
        self.down_button.pack(side="left", padx=(8, 0))

        option_frame = tk.Frame(control_card, bg="#fffaf1")
        option_frame.pack(side="right")

        self.auto_start_check = ttk.Checkbutton(
            option_frame,
            text="Auto Run",
            variable=self.auto_start_var,
        )
        self.auto_start_check.pack(side="left", padx=(0, 10))

        self.auto_close_check = ttk.Checkbutton(
            option_frame,
            text="Auto Close",
            variable=self.auto_close_var,
        )
        self.auto_close_check.pack(side="left", padx=(0, 8))

        ttk.Label(
            option_frame,
            text="Delay(s)",
            background="#fffaf1",
            foreground="#455661",
        ).pack(side="left", padx=(0, 6))
        self.auto_close_delay_entry = ttk.Entry(
            option_frame,
            textvariable=self.auto_close_delay_var,
            width=6,
        )
        self.auto_close_delay_entry.pack(side="left")

        list_card = tk.Frame(
            container,
            bg="#fffaf1",
            bd=1,
            relief="solid",
            padx=12,
            pady=12,
        )
        list_card.pack(fill="both", expand=True)

        tk.Label(
            list_card,
            text="Configured Processes",
            font=("Bahnschrift SemiBold", 14),
            bg="#fffaf1",
            fg="#20323c",
        ).pack(anchor="w", pady=(0, 10))

        tree_frame = tk.Frame(list_card, bg="#fffaf1")
        tree_frame.pack(fill="both", expand=True)

        columns = ("name", "kind", "target", "host", "port", "status")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        self.tree.heading("name", text="Name")
        self.tree.heading("kind", text="Type")
        self.tree.heading("target", text="Target")
        self.tree.heading("host", text="Host")
        self.tree.heading("port", text="Port")
        self.tree.heading("status", text="Status")
        self.tree.column("name", width=190, anchor="w")
        self.tree.column("kind", width=120, anchor="center")
        self.tree.column("target", width=320, anchor="w")
        self.tree.column("host", width=130, anchor="center")
        self.tree.column("port", width=90, anchor="center")
        self.tree.column("status", width=110, anchor="center")
        self.tree.tag_configure("pending", background="#f7f3ec")
        self.tree.tag_configure("running", background="#f8e2b2")
        self.tree.tag_configure("success", background="#dbeed3")
        self.tree.tag_configure("failed", background="#f4d0d0")
        self.tree.tag_configure("stopped", background="#d8e4ef")
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._refresh_controls())

        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")

        log_card = tk.Frame(
            container,
            bg="#20323c",
            bd=1,
            relief="solid",
            padx=12,
            pady=12,
        )
        log_card.pack(fill="both", expand=False, pady=(14, 0))

        tk.Label(
            log_card,
            text="Activity Log",
            font=("Bahnschrift SemiBold", 14),
            bg="#20323c",
            fg="#edf2f4",
        ).pack(anchor="w", pady=(0, 10))

        self.log_text = ScrolledText(
            log_card,
            height=10,
            wrap="word",
            font=("Consolas", 10),
            bg="#17252d",
            fg="#edf2f4",
            insertbackground="#edf2f4",
            relief="flat",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        footer = tk.Frame(container, bg="#efe8db", pady=12)
        footer.pack(fill="x")

        progress_row = tk.Frame(footer, bg="#efe8db")
        progress_row.pack(fill="x")

        self.progress_bar = ttk.Progressbar(
            progress_row,
            variable=self.progress_var,
            maximum=1,
            mode="determinate",
        )
        self.progress_bar.pack(side="left", fill="x", expand=True)

        tk.Label(
            progress_row,
            textvariable=self.summary_var,
            font=("Segoe UI", 10),
            bg="#efe8db",
            fg="#455661",
            padx=12,
        ).pack(side="right")

        tk.Label(
            footer,
            textvariable=self.activity_var,
            font=("Segoe UI", 10),
            bg="#efe8db",
            fg="#20323c",
        ).pack(anchor="w", pady=(8, 0))

    def _load_items(self) -> None:
        try:
            self.items = load_config(CONFIG_FILE)
            self.statuses = [STATUS_PENDING] * len(self.items)
        except ConfigError as exc:
            self.items = []
            self.statuses = []
            messagebox.showerror("설정 파일 오류", str(exc), parent=self.root)

        if self.items:
            self.summary_var.set(f"{len(self.items)}개 항목 로드됨")
            self._append_log("config.json 로드 완료")
        else:
            self.summary_var.set("구성 항목이 없습니다.")
            self._append_log("config.json 이 없거나 비어 있습니다.")

        self._update_progress_bounds()

    def _save_items(self) -> bool:
        try:
            save_config(CONFIG_FILE, self.items)
        except (OSError, ConfigError) as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self.root)
            return False

        self.summary_var.set(f"{len(self.items)}개 항목 저장됨")
        return True

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _status_tag(self, status: str) -> str:
        return {
            STATUS_PENDING: "pending",
            STATUS_RUNNING: "running",
            STATUS_SUCCESS: "success",
            STATUS_FAILED: "failed",
            STATUS_STOPPED: "stopped",
        }.get(status, "pending")

    def _item_type_label(self, item: dict) -> str:
        return "Service" if item.get("is_service", False) else "Executable"

    def _item_target_label(self, item: dict) -> str:
        if item.get("is_service", False):
            return item.get("service_name", "")
        return item.get("executable", "")

    def _selected_index(self) -> int | None:
        selected = self.tree.selection()
        if not selected:
            return None

        try:
            return int(selected[0])
        except (TypeError, ValueError):
            return None

    def _refresh_tree(self, select_index: int | None = None) -> None:
        if select_index is None:
            select_index = self._selected_index()

        self.tree.delete(*self.tree.get_children())

        for index, item in enumerate(self.items):
            status = self.statuses[index] if index < len(self.statuses) else STATUS_PENDING
            port_text = str(item.get("PORT", 0)) if item.get("PORT", 0) else "-"
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item.get("name", ""),
                    self._item_type_label(item),
                    self._item_target_label(item),
                    item.get("host", DEFAULT_HOST),
                    port_text,
                    status,
                ),
                tags=(self._status_tag(status),),
            )

        if select_index is not None and 0 <= select_index < len(self.items):
            self.tree.selection_set(str(select_index))
            self.tree.focus(str(select_index))

        self._update_progress_bounds()

    def _set_button_enabled(self, button: ttk.Button, enabled: bool) -> None:
        if enabled:
            button.state(["!disabled"])
        else:
            button.state(["disabled"])

    def _refresh_controls(self) -> None:
        selected_index = self._selected_index()
        has_selection = selected_index is not None
        has_items = bool(self.items)

        self._set_button_enabled(self.run_button, has_items and not self.is_running)
        self._set_button_enabled(self.stop_button, self.is_running)
        self._set_button_enabled(self.add_button, not self.is_running)
        self._set_button_enabled(self.edit_button, has_selection and not self.is_running)
        self._set_button_enabled(self.delete_button, has_selection and not self.is_running)
        self._set_button_enabled(
            self.up_button,
            has_selection and not self.is_running and selected_index > 0,
        )
        self._set_button_enabled(
            self.down_button,
            has_selection and not self.is_running and selected_index < len(self.items) - 1,
        )
        self._set_button_enabled(self.auto_start_check, not self.is_running)
        self._set_button_enabled(self.auto_close_check, not self.is_running)
        self._set_button_enabled(self.auto_close_delay_entry, not self.is_running)

    def _update_progress_bounds(self) -> None:
        if self.progress_bar is not None:
            self.progress_bar.configure(maximum=max(1, len(self.items)))

        if not self.is_running:
            self.progress_var.set(0)

    def _validate_items_for_run(self) -> tuple[bool, str]:
        if not self.items:
            return False, "실행할 항목이 없습니다."

        for index, item in enumerate(self.items, start=1):
            if item.get("is_service", False):
                if not item.get("service_name", "").strip():
                    return False, f"{index}번째 항목의 Service Name 값이 비어 있습니다."
            else:
                path_text = item.get("path", "").strip()
                executable = item.get("executable", "").strip()
                if not path_text or not executable:
                    return False, f"{index}번째 항목의 Path 또는 Executable 값이 비어 있습니다."
        return True, ""

    def add_item(self) -> None:
        dialog = ItemDialog(self.root, {"host": DEFAULT_HOST, "PORT": 0})
        item = dialog.show()
        if not item:
            return

        self.items.append(item)
        self.statuses.append(STATUS_PENDING)
        if self._save_items():
            self._refresh_tree(select_index=len(self.items) - 1)
            self._refresh_controls()
            self.activity_var.set(f"항목 추가: {item['name']}")
            self._append_log(f"항목 추가: {item['name']}")

    def edit_item(self) -> None:
        index = self._selected_index()
        if index is None:
            return

        dialog = ItemDialog(self.root, self.items[index])
        item = dialog.show()
        if not item:
            return

        self.items[index] = item
        self.statuses[index] = STATUS_PENDING
        if self._save_items():
            self._refresh_tree(select_index=index)
            self._refresh_controls()
            self.activity_var.set(f"항목 수정: {item['name']}")
            self._append_log(f"항목 수정: {item['name']}")

    def delete_item(self) -> None:
        index = self._selected_index()
        if index is None:
            return

        item_name = self.items[index].get("name", "Unnamed")
        confirmed = messagebox.askyesno(
            "항목 삭제",
            f"'{item_name}' 항목을 삭제하시겠습니까?",
            parent=self.root,
        )
        if not confirmed:
            return

        self.items.pop(index)
        self.statuses.pop(index)
        if self._save_items():
            next_index = min(index, len(self.items) - 1) if self.items else None
            self._refresh_tree(select_index=next_index)
            self._refresh_controls()
            self.activity_var.set(f"항목 삭제: {item_name}")
            self._append_log(f"항목 삭제: {item_name}")

    def move_up(self) -> None:
        index = self._selected_index()
        if index is None or index <= 0:
            return

        self.items[index - 1], self.items[index] = self.items[index], self.items[index - 1]
        self.statuses[index - 1], self.statuses[index] = (
            self.statuses[index],
            self.statuses[index - 1],
        )
        if self._save_items():
            self._refresh_tree(select_index=index - 1)
            self._refresh_controls()
            self._append_log("항목 순서 변경: 위로 이동")

    def move_down(self) -> None:
        index = self._selected_index()
        if index is None or index >= len(self.items) - 1:
            return

        self.items[index + 1], self.items[index] = self.items[index], self.items[index + 1]
        self.statuses[index + 1], self.statuses[index] = (
            self.statuses[index],
            self.statuses[index + 1],
        )
        if self._save_items():
            self._refresh_tree(select_index=index + 1)
            self._refresh_controls()
            self._append_log("항목 순서 변경: 아래로 이동")

    def run_all(self) -> None:
        valid, message = self._validate_items_for_run()
        if not valid:
            messagebox.showwarning("실행 불가", message, parent=self.root)
            return

        self._cancel_auto_close()
        self.is_running = True
        self.stop_event.clear()
        self.statuses = [STATUS_PENDING] * len(self.items)
        self.progress_var.set(0)
        self._refresh_tree()
        self._refresh_controls()
        self.summary_var.set(f"0 / {len(self.items)} 완료")
        self.activity_var.set("순차 실행 시작")
        self._append_log("Run All 시작")

        snapshot = [dict(item) for item in self.items]
        self.worker_thread = Thread(
            target=self._worker_run,
            args=(snapshot,),
            daemon=True,
        )
        self.worker_thread.start()

    def stop_run(self) -> None:
        if not self.is_running:
            return

        self._cancel_auto_close()
        self.stop_event.set()
        self.activity_var.set("중지 요청을 전달했습니다.")
        self._append_log("사용자 중지 요청")

    def _auto_run_if_enabled(self) -> None:
        if not self.auto_start_var.get():
            return
        if self.is_running or not self.items:
            return
        self._append_log("AUTO_START 활성화: Run All 자동 시작")
        self.run_all()

    def _auto_close_delay_seconds(self) -> int:
        try:
            return max(0, int(self.auto_close_delay_var.get().strip() or "0"))
        except ValueError:
            return 0

    def _cancel_auto_close(self) -> None:
        if self.auto_close_after_id is not None:
            try:
                self.root.after_cancel(self.auto_close_after_id)
            except Exception:
                pass
            self.auto_close_after_id = None
        self.auto_close_remaining = 0

    def _schedule_auto_close(self) -> None:
        self._cancel_auto_close()
        if not self.auto_close_var.get():
            return

        delay_seconds = self._auto_close_delay_seconds()
        if delay_seconds <= 0:
            self._append_log("AUTO_CLOSE 활성화: 지연 없이 런처를 종료합니다.")
            self.root.after(0, self._on_close)
            return

        self.auto_close_remaining = delay_seconds
        self._append_log(
            f"AUTO_CLOSE 활성화: 모든 항목 성공 후 {delay_seconds}초 뒤 런처를 종료합니다."
        )
        self._tick_auto_close()

    def _tick_auto_close(self) -> None:
        if self.auto_close_remaining <= 0:
            self.auto_close_after_id = None
            self._on_close()
            return

        self.activity_var.set(
            f"모든 항목 정상 동작 확인됨. {self.auto_close_remaining}초 후 런처 자동 종료"
        )
        self.auto_close_remaining -= 1
        self.auto_close_after_id = self.root.after(1000, self._tick_auto_close)

    def _worker_run(self, items: list[dict]) -> None:
        total = len(items)
        success_count = 0
        completed_count = 0
        failed_items: list[str] = []
        stopped = False

        for index, item in enumerate(items):
            name = item.get("name", f"Item {index + 1}")
            if self.stop_event.is_set():
                stopped = True
                break

            self.event_queue.put(
                {
                    "type": "item_status",
                    "index": index,
                    "status": STATUS_RUNNING,
                }
            )
            self.event_queue.put({"type": "activity", "message": f"{name} 실행 중"})

            success, message, was_stopped = process_item(
                item,
                self.stop_event,
                lambda text: self.event_queue.put({"type": "log", "message": text}),
            )

            if was_stopped:
                stopped = True
                self.event_queue.put(
                    {
                        "type": "item_status",
                        "index": index,
                        "status": STATUS_STOPPED,
                    }
                )
                self.event_queue.put({"type": "log", "message": f"[{name}] 실행 중지"})
                break

            completed_count += 1
            self.event_queue.put(
                {"type": "progress", "value": completed_count, "total": total}
            )

            if success:
                success_count += 1
                self.event_queue.put(
                    {
                        "type": "item_status",
                        "index": index,
                        "status": STATUS_SUCCESS,
                    }
                )
                self.event_queue.put({"type": "log", "message": f"[{name}] 성공: {message}"})
                continue

            failed_items.append(name)
            self.event_queue.put(
                {
                    "type": "item_status",
                    "index": index,
                    "status": STATUS_FAILED,
                }
            )
            self.event_queue.put(
                {
                    "type": "warning",
                    "title": "실행 실패",
                    "message": f"{name} 실행에 실패했습니다.\n\n{message}",
                }
            )
            self.event_queue.put({"type": "log", "message": f"[{name}] 실패: {message}"})
            break

        if stopped:
            summary = f"{success_count} / {total} 완료 후 중지"
            activity = "사용자 요청으로 실행이 중지되었습니다."
            dialog = ("info", "실행 중지", "사용자 요청으로 순차 실행이 중지되었습니다.")
            auto_close = False
        elif failed_items:
            summary = f"{success_count} / {total} 성공"
            activity = f"실패 항목: {', '.join(failed_items)}"
            dialog = None
            auto_close = False
        else:
            summary = f"{success_count} / {total} 성공"
            activity = "모든 항목 실행이 완료되었습니다."
            dialog = ("info", "실행 완료", "모든 항목이 성공적으로 처리되었습니다.")
            auto_close = True

        self.event_queue.put(
            {
                "type": "finished",
                "summary": summary,
                "activity": activity,
                "dialog": dialog,
                "auto_close": auto_close,
            }
        )

    def _drain_queue(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except Empty:
            pass
        finally:
            self.root.after(EVENT_POLL_INTERVAL_MS, self._drain_queue)

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type")

        if event_type == "log":
            self._append_log(event["message"])
            return

        if event_type == "item_status":
            index = event["index"]
            status = event["status"]
            if 0 <= index < len(self.statuses):
                self.statuses[index] = status
                self._refresh_tree(select_index=index)
            return

        if event_type == "progress":
            value = event.get("value", 0)
            total = event.get("total", len(self.items))
            self.progress_var.set(value)
            self.summary_var.set(f"{value} / {total} 완료")
            return

        if event_type == "activity":
            self.activity_var.set(event["message"])
            return

        if event_type == "warning":
            messagebox.showwarning(
                event["title"],
                event["message"],
                parent=self.root,
            )
            return

        if event_type == "finished":
            self.is_running = False
            self.stop_event.clear()
            self.summary_var.set(event["summary"])
            self.activity_var.set(event["activity"])
            self._refresh_controls()

            auto_close = bool(event.get("auto_close"))
            dialog = event.get("dialog")
            if dialog and not auto_close:
                kind, title, message = dialog
                if kind == "info":
                    messagebox.showinfo(title, message, parent=self.root)

            if auto_close:
                self._append_log("AUTO_CLOSE 활성화 상태이므로 완료 팝업 없이 자동 종료를 진행합니다.")
                self._schedule_auto_close()
            return

    def _on_close(self) -> None:
        self._cancel_auto_close()
        if self.is_running:
            confirmed = messagebox.askyesno(
                "종료 확인",
                "실행 중입니다. 종료하면 현재 순차 실행이 중지됩니다. 계속하시겠습니까?",
                parent=self.root,
            )
            if not confirmed:
                return
            self.stop_event.set()

        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    LiveLauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
