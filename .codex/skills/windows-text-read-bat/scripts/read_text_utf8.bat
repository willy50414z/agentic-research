@echo off
setlocal

if "%~1"=="" (
  echo Usage: read_text_utf8.bat ^<path^> [escaped^|raw^|lines]
  exit /b 2
)

set "TARGET=%~1"
set "MODE=%~2"

if "%MODE%"=="" set "MODE=escaped"

python -X utf8 -c "from pathlib import Path; import sys; p = Path(sys.argv[1]); mode = sys.argv[2]; text = p.read_text(encoding='utf-8'); esc = text.encode('unicode_escape').decode('ascii'); lines = ''.join(f'{i}: {line.encode(\"unicode_escape\").decode(\"ascii\")}\\n' for i, line in enumerate(text.splitlines(), 1)); sys.stdout.write(text if mode == 'raw' else lines if mode == 'lines' else esc)" "%TARGET%" "%MODE%"

exit /b %ERRORLEVEL%
