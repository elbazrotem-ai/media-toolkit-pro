import os
import shutil
import yt_dlp
import tqdm as tqdm_module
from faster_whisper import WhisperModel
import sqlite3

from PySide6.QtCore import QThread, Signal

from database import DB_PATH, INTERNAL_DIR, init_db
from utils import get_ffmpeg_path
from signal_tqdm import SignalTqdm

class ProcessingThread(QThread):
    log_signal      = Signal(str)
    progress_signal = Signal(float)
    finished_signal = Signal()

    def __init__(self, source, output_folder, is_local, action, quality, model_size):
        super().__init__()
        self.source        = source
        self.output_folder = output_folder
        self.is_local      = is_local
        self.action        = action
        self.quality       = quality
        self.model_size    = model_size
        self._stop_flag    = False

    def stop(self):
        self._stop_flag = True

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%', '').strip()
            try:
                self.progress_signal.emit(float(p))
            except Exception:
                pass

    def run(self):
        try:
            init_db()
            os.makedirs(self.output_folder, exist_ok=True)
            os.makedirs(INTERNAL_DIR,        exist_ok=True)

            files_to_process = []
            ffmpeg_bin       = get_ffmpeg_path()

            # ── 1. Download or use local ───────────────────────────────────
            if not self.is_local:
                self.log_signal.emit("📥 Starting YouTube download process...")
                ydl_opts = {
                    'format':        'bestaudio/best',
                    'outtmpl':       os.path.join(self.output_folder,
                                                  '%(title)s.%(ext)s'),
                    'progress_hooks': [self.progress_hook],
                    'ignoreerrors':  True,
                    'postprocessors': [{
                        'key':              'FFmpegExtractAudio',
                        'preferredcodec':   'mp3',
                        'preferredquality': self.quality,
                    }],
                }
                if ffmpeg_bin:
                    ydl_opts['ffmpeg_location'] = ffmpeg_bin

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self.source, download=True)
                    if 'entries' in info:           # playlist
                        for entry in info['entries']:
                            if entry:
                                f_path = (ydl.prepare_filename(entry)
                                          .rsplit('.', 1)[0] + '.mp3')
                                files_to_process.append(f_path)
                    else:                           # single video
                        f_path = (ydl.prepare_filename(info)
                                  .rsplit('.', 1)[0] + '.mp3')
                        files_to_process.append(f_path)
            else:
                self.log_signal.emit(
                    f"📂 Using local file: {os.path.basename(self.source)}")
                files_to_process.append(self.source)

            # ── 2. Load Whisper model (with download-progress patching) ────
            conn   = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            model  = None

            if self.action == 'text':
                self.log_signal.emit(
                    f"🧠 Loading Whisper model: {self.model_size} "
                    f"(downloading if not cached)...")
                self.progress_signal.emit(0)

                original_tqdm        = tqdm_module.tqdm
                SignalTqdm._signal   = self.progress_signal
                tqdm_module.tqdm     = SignalTqdm
                try:
                    model = WhisperModel(self.model_size,
                                        device="cpu",
                                        compute_type="int8")
                    self.log_signal.emit("✅ Model ready.")
                finally:
                    tqdm_module.tqdm   = original_tqdm
                    SignalTqdm._signal = None
                    self.progress_signal.emit(0)   # reset bar

            # ── 3. Process each file ───────────────────────────────────────
            for audio_file in files_to_process:
                if self._stop_flag:
                    self.log_signal.emit("⛔ Process stopped by user.")
                    break

                if not os.path.exists(audio_file):
                    self.log_signal.emit(
                        f"⚠️ File not found, skipping: {audio_file}")
                    continue

                # Copy to internal storage so history survives deletion
                internal_audio = os.path.join(INTERNAL_DIR,
                                              os.path.basename(audio_file))
                if os.path.abspath(audio_file) != os.path.abspath(internal_audio):
                    shutil.copy2(audio_file, internal_audio)
                    self.log_signal.emit(
                        f"💾 Saved internally: {os.path.basename(internal_audio)}")

                t_path = ""

                if self.action == 'text' and model:
                    self.log_signal.emit(
                        f"📝 Transcribing: {os.path.basename(audio_file)}")
                    segments, info = model.transcribe(audio_file, beam_size=5)

                    transcript_text = []
                    for segment in segments:
                        if self._stop_flag:
                            self.log_signal.emit(
                                "⛔ Transcription stopped by user.")
                            break
                        ts = (f"[{int(segment.start // 60):02d}:"
                              f"{int(segment.start % 60):02d}]")
                        transcript_text.append(f"{ts} {segment.text}")
                        self.progress_signal.emit(
                            (segment.end / info.duration) * 100)

                    if transcript_text:
                        t_path = os.path.join(
                            INTERNAL_DIR,
                            os.path.basename(audio_file).rsplit('.', 1)[0]
                            + "_transcript.txt"
                        )
                        with open(t_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(transcript_text))
                        self.log_signal.emit(
                            f"✅ Transcript saved: {os.path.basename(t_path)}")

                cursor.execute(
                    "INSERT INTO history "
                    "(filename, audio_path, transcript_path) "
                    "VALUES (?, ?, ?)",
                    (os.path.basename(audio_file), internal_audio, t_path)
                )
                self.log_signal.emit(
                    f"📋 Added to history: {os.path.basename(audio_file)}")

            conn.commit()
            conn.close()

            if self.action != 'text':
                self.log_signal.emit("✅ Audio extraction completed.")

        except Exception as e:
            self.log_signal.emit(f"❌ Error: {e}")
        finally:
            self.finished_signal.emit()
