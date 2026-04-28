import os
import sys
import shutil
import urllib.request
import zipfile

def get_ffmpeg_path():
    """Return the directory containing ffmpeg, downloading it if necessary."""
    # Determine execution context (PyInstaller or script)
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    bin_dir = os.path.join(base_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    ffmpeg_exe = os.path.join(bin_dir, "ffmpeg.exe")
    
    if os.path.exists(ffmpeg_exe):
        return bin_dir
        
    if shutil.which("ffmpeg"):
        return None
        
    print("Downloading FFmpeg... This may take a minute.")
    # Direct link to a reliable, automated FFmpeg Windows build
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    zip_path = os.path.join(bin_dir, "ffmpeg.zip")
    
    urllib.request.urlretrieve(url, zip_path)
    
    # Extract only the necessary executables to save space
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file_info in zip_ref.infolist():
            if file_info.filename.endswith("ffmpeg.exe") or file_info.filename.endswith("ffprobe.exe"):
                file_info.filename = os.path.basename(file_info.filename)
                zip_ref.extract(file_info, bin_dir)
                
    os.remove(zip_path)
    return bin_dir

def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)