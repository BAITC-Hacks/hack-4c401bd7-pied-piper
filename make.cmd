@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"
if not defined DATA set "DATA=data"
if not defined OUTPUTS set "OUTPUTS=outputs"
if not defined PORT set "PORT=3000"
if "%~1"=="" goto help
if /i "%~1"=="help" goto help
if /i "%~1"=="setup" goto setup
if /i "%~1"=="pipeline" goto pipeline
if /i "%~1"=="validate" goto validate
if /i "%~1"=="ui" goto ui
if /i "%~1"=="stop" goto stop
if /i "%~1"=="run" goto run
if /i "%~1"=="test" goto test
if /i "%~1"=="demo" goto demo
if /i "%~1"=="docker-build" goto docker-build
if /i "%~1"=="docker-up" goto docker-up
if /i "%~1"=="docker-down" goto docker-down
if /i "%~1"=="docker-recompute" goto docker-recompute
if /i "%~1"=="docker-test" goto docker-test
echo Unknown command: "%~1". Use make.cmd help.
exit /b 1

:help
echo make.cmd docker-build Build runtime image once or after code updates
echo make.cmd docker-up    Start built containers without rebuilding
echo make.cmd docker-down  Stop containers and keep results
echo make.cmd docker-recompute Recalculate results using the built image
echo make.cmd docker-test  Build separate test image and run tests
echo make.cmd stop      Stop this project UI on the selected port
echo make.cmd setup     Install environment and dependencies using uv
echo make.cmd run       Calculate real data, validate, then start UI
echo make.cmd ui        Open existing real results without recalculation
echo make.cmd pipeline  Calculate real data
echo make.cmd validate  Validate real results
echo make.cmd test      Run tests
echo make.cmd demo      Generate, validate and open synthetic demo
echo Default UI address: http://localhost:%PORT%
exit /b 0

:setup
if exist "%PYTHON%" goto dependencies
uv venv --python 3.12 .venv
if errorlevel 1 exit /b 1
:dependencies
uv pip sync --python "%PYTHON%" requirements.lock
exit /b %errorlevel%

:docker-build
docker compose build ui
exit /b %errorlevel%

:docker-up
docker compose up -d --no-build --pull never
exit /b %errorlevel%

:docker-down
docker compose down
exit /b %errorlevel%

:docker-recompute
docker compose run --rm --no-deps pipeline
exit /b %errorlevel%

:docker-test
docker compose build tests
if errorlevel 1 exit /b 1
docker compose run --rm tests
exit /b %errorlevel%

:pipeline
"%PYTHON%" pipeline.py --data "%DATA%" --out "%OUTPUTS%"
exit /b %errorlevel%

:validate
"%PYTHON%" validate.py --data "%DATA%" --out "%OUTPUTS%"
exit /b %errorlevel%

:ui
"%PYTHON%" -m streamlit run app.py --server.address 127.0.0.1 --server.port %PORT% -- --outputs "%OUTPUTS%"
exit /b %errorlevel%

:stop
"%PYTHON%" scripts/stop_ui.py --port %PORT%
exit /b %errorlevel%

:run
call "%~f0" pipeline
if errorlevel 1 exit /b 1
call "%~f0" validate
if errorlevel 1 exit /b 1
call "%~f0" ui
exit /b %errorlevel%

:test
"%PYTHON%" -m pytest -q
exit /b %errorlevel%

:demo
"%PYTHON%" tests/fixtures/ui/make_fixture.py --out demo_outputs --data demo_data
if errorlevel 1 exit /b 1
"%PYTHON%" validate.py --data demo_data --out demo_outputs --expected-nodes 24
if errorlevel 1 exit /b 1
set "OUTPUTS=demo_outputs"
call "%~f0" ui
exit /b %errorlevel%
