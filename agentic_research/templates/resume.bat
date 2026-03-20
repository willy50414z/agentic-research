@echo off
REM resume.bat — post clarification answers to start the research loop (Windows)

SET FRAMEWORK_API_URL=%FRAMEWORK_API_URL%
IF "%FRAMEWORK_API_URL%"=="" SET FRAMEWORK_API_URL=http://localhost:7001

echo [resume.bat] Posting clarification answers...

agentic-research resume --spec-clarified "%CD%\spec.clarified.md" --spec "%CD%\spec.md" --api "%FRAMEWORK_API_URL%"
