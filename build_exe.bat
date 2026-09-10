@echo off
REM Build a standalone CHistViewer.exe (no Python needed on the target PC).
REM Output: dist\CHistViewer.exe  (single file, ~14-16 MB)
REM
REM Requires PyInstaller in the build environment:  pip install pyinstaller
REM Pillow is bundled if it's installed here (enables inline image previews).
REM
REM numpy/scipy/etc. are excluded: Pillow only pulls them in for an optional
REM ndarray bridge this app never uses, and they roughly double the exe.

cd /d "%~dp0"

python -m PyInstaller ^
  --noconfirm --clean ^
  --onefile ^
  --windowed ^
  --name CHistViewer ^
  --collect-submodules PIL ^
  --hidden-import PIL.ImageTk ^
  --hidden-import PIL._tkinter_finder ^
  --exclude-module numpy ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --exclude-module pandas ^
  --exclude-module IPython ^
  --exclude-module pytest ^
  gui_app.py

echo.
echo Done. The exe is at:  dist\CHistViewer.exe
