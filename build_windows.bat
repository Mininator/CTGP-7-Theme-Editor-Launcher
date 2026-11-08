@echo off
cd /d "%~dp0"

echo === Installation des dependances ===
python -m pip install --upgrade pyinstaller PyQt5

echo.
echo === Compilation de "CTGP-7 Launcher" ===
pyinstaller --noconfirm --onefile --windowed --name "CTGP-7 Launcher" --icon "assets\icon.ico" ^
  --add-data "sprites.zip;." ^
  --add-data "sound.zip;." ^
  --add-data "assets;assets" ^
  --add-data "cwavtool\windows-x86_64;cwavtool\windows-x86_64" ^
  --add-data "cwavtool\windows-i686;cwavtool\windows-i686" ^
  --add-data "cwavtool\LICENSE.txt;cwavtool" ^
  ctgp7_launcher_editor.py

echo.
if exist "dist\CTGP-7 Launcher.exe" (
    echo Termine : dist\CTGP-7 Launcher.exe
) else (
    echo ERREUR : la compilation a echoue, voir les messages ci-dessus.
)
pause
