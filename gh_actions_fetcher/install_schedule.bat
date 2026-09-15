@echo off
REM 注册宝可梦机场码自动抓取计划任务
REM 每周一 11:17 本地跑 auto_push.py,push 到 GitHub。错过会补跑。
REM 用法: 双击本 bat,或管理员 cmd 运行

setlocal
set TASKNAME=BaokemengAutoFetch
set PYEXE=C:\Users\10791\AppData\Local\Python\pythoncore-3.14-64\python.exe
set WORKDIR=C:\Users\10791\Desktop\你好
set SCRIPT=%WORKDIR%\gh_actions_fetcher\auto_push.py

REM 优先用 pythoncore 的 python,fallback 到 PATH 里的 python
if not exist "%PYEXE%" set PYEXE=python

REM 删旧任务(若存在)
schtasks /Delete /TN %TASKNAME% /F >nul 2>&1

REM 注册:每周一 11:17,错过补跑,失败重试3次
schtasks /Create /TN %TASKNAME% /TR "\"%PYEXE%\" \"%SCRIPT%\"" /SC WEEKLY /D MON /ST 11:17 /F /RL HIGHEST
if errorlevel 1 (
    echo.
    echo [!] 注册失败,可能需要管理员权限。请右键以管理员身份运行本 bat。
    pause
    exit /b 1
)

echo.
echo [+] 计划任务 %TASKNAME% 注册成功
echo     每周一 11:17 自动跑 %SCRIPT%
echo     日志: schtasks /Query /TN %TASKNAME% /V
echo.
echo 测试跑一次? (回车跳过)
pause
"%PYEXE%" "%SCRIPT%"
pause
endlocal
