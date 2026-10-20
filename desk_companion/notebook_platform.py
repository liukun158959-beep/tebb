"""System launch requests for notebook outputs; no GUI imports."""
import os
import subprocess


class NotebookPlatform:
    def open(self, target):
        os.startfile(target)

    def reveal(self, path):
        subprocess.Popen(['explorer', f'/select,{path}'])
