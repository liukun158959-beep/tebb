"""Shared import guard for subprocess checks of non-GUI services."""
import importlib.abc
import sys

FORBIDDEN = ('desk_companion.app', 'desk_companion.local_api.host', 'desk_companion.layered',
             'desk_companion.pet_shell', 'desk_companion.tray', 'desk_companion.winforms_host',
             'desk_companion.bridge', 'desk_companion.board_workbench',
             'webview', 'win32api', 'win32gui', 'win32con', 'pystray', 'clr')


class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in FORBIDDEN):
            raise RuntimeError('GUI import forbidden: ' + fullname)


def assert_no_gui():
    assert not any(name == prefix or name.startswith(prefix + '.')
                   for name in sys.modules for prefix in FORBIDDEN)
