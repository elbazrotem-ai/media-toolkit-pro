import tqdm as tqdm_module
from PySide6.QtCore import Signal

class SignalTqdm(tqdm_module.tqdm):
    _signal = None

    def update(self, n=1):
        super().update(n)
        if self._signal and self.total and self.total > 0:
            self._signal.emit((self.n / self.total) * 100)
