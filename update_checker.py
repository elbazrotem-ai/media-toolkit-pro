import urllib.request
import json

from PySide6.QtCore import QThread, Signal

from config import APP_VERSION, GITHUB_REPO

class UpdateChecker(QThread):
    update_available = Signal(str)  # emits the new version string

    def run(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "MediaToolkitPro"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read())
                latest = data.get("tag_name", "").lstrip("v")
                if latest and latest != APP_VERSION:
                    self.update_available.emit(latest)
        except Exception:
            pass  # no internet or no releases — stay silent
