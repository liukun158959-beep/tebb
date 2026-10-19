"""显示器工作区与 DPI。桌宠绘制已改到 Electron。"""
from __future__ import annotations

import ctypes
from ctypes import byref, c_void_p, sizeof
from ctypes.wintypes import DWORD, LONG, POINT
from .state import SCALES

user32 = ctypes.windll.user32

SPI_GETWORKAREA = 48
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = c_void_p(-4)
MONITOR_DEFAULTTONEAREST = 2


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", DWORD),
    ]


user32.MonitorFromWindow.argtypes = [c_void_p, DWORD]
user32.MonitorFromWindow.restype = c_void_p
user32.MonitorFromPoint.argtypes = [POINT, DWORD]
user32.MonitorFromPoint.restype = c_void_p
user32.GetMonitorInfoW.argtypes = [c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = ctypes.c_int


def enable_dpi_aware() -> None:
    if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        return
    awareness = ctypes.c_int()
    hr = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
    if hr == 0 and awareness.value >= 2:
        return
    raise RuntimeError(
        "无法切换到 Per-Monitor DPI 感知。请确认在 Windows 10 1703 及以上运行。"
    )


def _monitor_info(hwnd: int | None = None) -> MONITORINFO:
    if hwnd:
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            raise RuntimeError("MonitorFromWindow 失败，无法定位宠物所在显示器。")
    else:
        work = RECT()
        if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, byref(work), 0):
            raise RuntimeError("无法读取工作区（SPI_GETWORKAREA 失败）。")
        point = POINT((work.left + work.right) // 2, (work.top + work.bottom) // 2)
        monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            raise RuntimeError("MonitorFromPoint 失败，无法定位主显示器。")
    info = MONITORINFO()
    info.cbSize = sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, byref(info)):
        raise RuntimeError("GetMonitorInfo 失败，无法读取显示器矩形。")
    return info


def work_area(hwnd: int | None = None) -> tuple[int, int, int, int]:
    """当前显示器工作区（不含任务栏）。无 HWND 时用主屏。"""
    rect = _monitor_info(hwnd).rcWork
    return rect.left, rect.top, rect.right, rect.bottom


def screen_area(hwnd: int | None = None) -> tuple[int, int, int, int]:
    """当前显示器全屏矩形（含任务栏）。无 HWND 时用主屏。"""
    rect = _monitor_info(hwnd).rcMonitor
    return rect.left, rect.top, rect.right, rect.bottom
