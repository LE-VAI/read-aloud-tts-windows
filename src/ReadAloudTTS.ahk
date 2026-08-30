#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

SetWorkingDir A_ScriptDir

global AppDir := A_ScriptDir
global ConfigPath := AppDir . "\config.json"
global TempDir := AppDir . "\tmp"
global PidPath := TempDir . "\speak.pid"
global DaemonPidPath := TempDir . "\daemon.pid"
global DaemonReadyPath := TempDir . "\daemon_ready"
global RequestPath := TempDir . "\request.json"
global ResponsePath := TempDir . "\response.json"
global HighlightPath := TempDir . "\highlight_state.json"
global PyExe := AppDir . "\.venv\Scripts\python.exe"
global Q := Chr(34)
; Last daemon response content — WaitResponse() captures it (before
; deleting the response file) so callers can inspect status/message
; without racing the file away.
global LastResponse := ""

; Highlight overlay state
global HighlightGui := ""
global HighlightWords := []
global HighlightTotalMs := 0
global HighlightPlayStart := 0
global HighlightTimer := ""
global HighlightCurrentIdx := -1
global HighlightPaused := false
global HighlightFullText := ""
global TranscriptGui := ""
; Last cursor position — used by HighlightTick to require mouse MOVEMENT
; before hover-pause (prevents pause/resume flapping when the overlay
; rebuilds under a resting cursor).
global gLastMouseX := 0
global gLastMouseY := 0

DirCreate TempDir
; Brand the tray — without this the taskbar shows AutoHotkey's generic icon.
; Graceful fallback: missing file just keeps the default icon.
if FileExist(AppDir . "\app.ico") {
    TraySetIcon(AppDir . "\app.ico")
}
A_IconTip := "ReadAloudTTS — select text, press Home"
; Don't blindly delete the readiness marker — if a daemon from a previous
; session is still alive (holding the single-instance mutex), deleting the
; marker orphans it and every subsequent Home press silently fails. Ping
; first; only prune if no response. The daemon also self-heals its marker
; (speak_server.py), so this is belt-and-suspenders.
PruneStaleDaemon()
InitTray()

; Register hotkeys BEFORE StartDaemon so the keyboard hook is installed
; before the auto-execute section blocks on daemon warmup.
;
; Hotkeys — kept deliberately minimal: one key to read, one key to stop.
;   Home = read the current text selection
;   F6   = stop speech immediately
; Remap either by editing the bindings below. See README "Remapping hotkeys".
$*^RButton::ReadSelection()
$*^RButton Up::SuppressCtrlRightClick()
$*Home::ReadSelection()
$*F6::StopSpeech()

$*^!t::ShowTranscript()

; Speed control — on-the-fly rate adjustment.
;   Ctrl + * (Ctrl+Shift+8) = faster  (multiply = more speed)
;   Ctrl + /                = slower  (divide = less speed)
; Tray menu "Speed:" item cycles presets and resets to normal.
; Takes effect on the next chunk being synthesized, not the currently
; playing one. Persists to config.json so it survives restarts.
$*^+8::AdjustSpeed(0.9)
$*^/::AdjustSpeed(1.1)

; Click-to-rewind on the highlight overlay: register the WM_LBUTTONDOWN
; monitor ONCE here. Previously it was registered inside every
; ShowHighlightOverlay() build, stacking duplicate message handlers.
OnMessage(0x201, OverlayClickHandler)

StartDaemon()

; ---------------------------------------------------------------------------
; Daemon management
; ---------------------------------------------------------------------------

IsDaemonReady() {
    global DaemonReadyPath
    return FileExist(DaemonReadyPath) != ""
}

; Ping-verified readiness check. PruneStaleDaemon() runs once at AHK startup,
; but if the daemon crashes mid-session the marker is left behind and
; IsDaemonReady() returns a false positive — SpeakViaDaemon then writes to a
; dead queue and WaitResponse hangs for 120s. This confirms the daemon is
; actually alive before trusting the marker. Costs ~20ms when the daemon is
; healthy (its poll interval); only pays the full 2s when the daemon is dead.
EnsureDaemonAlive() {
    global DaemonReadyPath, RequestPath, ResponsePath
    if !FileExist(DaemonReadyPath)
        return false  ; No marker — not ready. StartDaemon will spawn fresh.
    try FileDelete ResponsePath
    FileAppend '{"action":"ping"}', RequestPath, "UTF-8-RAW"
    if WaitResponse(2)
        return true  ; Daemon responded — alive and ready.
    ; Stale marker from a crashed daemon. Clean up so StartDaemon respawns.
    try FileDelete DaemonReadyPath
    try FileDelete RequestPath
    try FileDelete ResponsePath
    return false
}

SendDaemonQuit(timeoutSec := 3) {
    global RequestPath, ResponsePath
    try FileDelete ResponsePath
    FileAppend '{"action":"quit"}', RequestPath, "UTF-8-RAW"
    return WaitResponse(timeoutSec)
}

PruneStaleDaemon() {
    global DaemonReadyPath, RequestPath, ResponsePath
    if !FileExist(DaemonReadyPath) {
        return  ; No marker — nothing to prune. StartDaemon will spawn fresh.
    }
    ; Ping the daemon. If it responds, it's alive — keep the marker.
    try FileDelete ResponsePath
    FileAppend '{"action":"ping"}', RequestPath, "UTF-8-RAW"
    if WaitResponse(2) {
        return  ; Daemon is alive and responsive.
    }
    ; No response — stale marker from a crashed daemon. Remove it so
    ; StartDaemon spawns a fresh process instead of assuming ready.
    try FileDelete DaemonReadyPath
    try FileDelete RequestPath
    try FileDelete ResponsePath
}

StartDaemon() {
    global PyExe, AppDir, DaemonPidPath, DaemonReadyPath, Q
    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }
    if IsDaemonReady() {
        return  ; Already running and warmed up.
    }
    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --serve"
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete DaemonPidPath
    FileAppend pid, DaemonPidPath, "UTF-8-RAW"
    if WaitDaemonReady(10) {
        return  ; Daemon came up normally.
    }
    ; Marker didn't appear in 10s. The most likely cause: a previous
    ; daemon is still alive holding the single-instance mutex, so the new
    ; process exited immediately with "already running". Send a quit to
    ; the orphan, wait for it to release the mutex, then retry once.
    TrayTip "Recovering stuck TTS daemon...", "ReadAloudTTS"
    SendDaemonQuit(3)
    Sleep 800  ; Give the OS time to release the mutex after exit.
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete DaemonPidPath
    FileAppend pid, DaemonPidPath, "UTF-8-RAW"
    if WaitDaemonReady(10)
        TrayTip "TTS daemon recovered", "ReadAloudTTS"
    else
        TrayTip "TTS daemon failed to start. Run refresh-readaloud.cmd", "ReadAloudTTS"
}

WaitDaemonReady(timeoutSec := 10) {
    global DaemonReadyPath
    endTick := A_TickCount + (timeoutSec * 1000)
    while (A_TickCount < endTick) {
        if FileExist(DaemonReadyPath)
            return true
        Sleep 100
    }
    return false
}

StopDaemon() {
    global DaemonPidPath, DaemonReadyPath, RequestPath, ResponsePath
    ; Always send a quit request — the daemon may be alive even if the
    ; marker is missing (though self-heal makes that unlikely now). The
    ; quit IPC works regardless of process elevation, unlike taskkill.
    SendDaemonQuit(2)
    Sleep 500
    ; Force-kill by PID if still alive (fallback if quit wasn't processed).
    if FileExist(DaemonPidPath) {
        pidText := Trim(FileRead(DaemonPidPath, "UTF-8-RAW"))
        if RegExMatch(pidText, "^\d+$") {
            try ProcessClose Integer(pidText)
        }
        try FileDelete DaemonPidPath
    }
    try FileDelete DaemonReadyPath
    try FileDelete RequestPath
    try FileDelete ResponsePath
}

; ---------------------------------------------------------------------------
; Tray menu
; ---------------------------------------------------------------------------

InitTray() {
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Read Selection`tCtrl+Right-click", (*) => ReadSelection())
    A_TrayMenu.Add("Stop`tF6", (*) => StopSpeech())
    A_TrayMenu.Add("Speed: " . GetSpeedLabel(), (*) => CycleSpeed())
    A_TrayMenu.Add("Word highlight box: " . (ShowOverlayEnabled() ? "On" : "Off"), ToggleOverlayFromTray)
    A_TrayMenu.Add()

    voiceMenu := Menu()
    voices := GetVoiceMap()
    current := GetCurrentVoice()
    installedCount := 0
    for voiceId, label in voices {
        ; Only offer voices whose model files exist — a menu entry whose
        ; download was never run is a guaranteed-broken click that poisons
        ; current_voice (the "switch to Amy" failure: files absent, every
        ; later read then fails).
        if !VoiceFilesInstalled(voiceId) {
            continue
        }
        installedCount++
        boundId := voiceId
        voiceMenu.Add(label, (*) => SetVoice(boundId))
        if (voiceId = current) {
            voiceMenu.Check(label)
        }
    }
    if (installedCount = 0) {
        voiceMenu.Add("No voices installed — run download_voices.ps1", (*) => {})
        voiceMenu.Disable("No voices installed — run download_voices.ps1")
    }
    A_TrayMenu.Add("Voice", voiceMenu)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Open Config", (*) => OpenConfig())
    A_TrayMenu.Add("Open Logs", (*) => OpenLogs())
    A_TrayMenu.Add("Show Transcript`tCtrl+Alt+T", (*) => ShowTranscript())
    A_TrayMenu.Add("Restart Daemon", (*) => RestartDaemon())
    A_TrayMenu.Add("Exit", (*) => ExitApp())
    A_TrayMenu.Default := "Read Selection`tCtrl+Right-click"
}

GetSpeedLabel() {
    speed := GetCurrentSpeed()
    if (speed < 1.0) {
        mult := 1.0 / speed
        return Round(mult, 1) . "x faster (Ctrl+*/)"
    } else if (speed > 1.0) {
        return Round(speed, 1) . "x slower (Ctrl+*/)"
    } else {
        return "Normal (Ctrl+*/)"
    }
}

ToggleOverlayFromTray(*) {
    nowOn := ToggleOverlayEnabled()
    InitTray()
    TrayTip (nowOn ? "Word highlight box enabled" : "Word highlight box disabled"), "ReadAloudTTS"
}

CycleSpeed(*) {
    ; Cycle through common presets: normal → 1.2x slower → 1.5x slower → 0.8x faster → normal
    current := GetCurrentSpeed()
    presets := [1.0, 1.2, 1.5, 0.8]
    nextIdx := 0
    for i, p in presets {
        if (Abs(current - p) < 0.05) {
            nextIdx := i < presets.Length ? i + 1 : 1
            break
        }
    }
    target := presets[nextIdx > 0 ? nextIdx : 1]
    ; Send the target speed directly.
    global RequestPath, ResponsePath
    try FileDelete ResponsePath
    FileAppend '{"action":"set_speed","speed":' . target . '}', RequestPath, "UTF-8-RAW"
    WaitResponse(3)
    InitTray()
    TrayTip GetSpeedLabel(), "ReadAloudTTS Speed"
}

RestartDaemon(*) {
    StopDaemon()
    StartDaemon()
    if IsDaemonReady()
        TrayTip "TTS daemon restarted", "ReadAloudTTS"
    else
        TrayTip "TTS daemon failed to start. Try refresh-readaloud.cmd", "ReadAloudTTS"
}

; ---------------------------------------------------------------------------
; Config helpers (unchanged from original)
; ---------------------------------------------------------------------------

GetVoiceMap() {
    global ConfigPath
    voices := Map()

    try {
        content := FileRead(ConfigPath, "UTF-8-RAW")
        pos := 1
        pattern := '"([^"]+)"\s*:\s*\{[^{}]*"label"\s*:\s*"([^"]+)"'
        while RegExMatch(content, pattern, &match, pos) {
            voiceId := match[1]
            label := match[2]
            if (InStr(voiceId, "en_") = 1) {
                voices[voiceId] := label
            }
            pos := match.Pos + match.Len
        }
    }

    if (voices.Count = 0) {
        voices["en_US-lessac-medium"] := "Lessac - warm"
        voices["en_US-amy-medium"] := "Amy - clear"
        voices["en_US-hfc_female-medium"] := "HFC Female - soft"
    }
    return voices
}

GetCurrentVoice() {
    global ConfigPath
    try {
        content := FileRead(ConfigPath, "UTF-8")
        if RegExMatch(content, '"current_voice"\s*:\s*"([^"]+)"', &match) {
            return match[1]
        }
    }
    return "en_US-lessac-medium"
}

VoiceFilesInstalled(voiceId) {
    ; Check the model + config files for a voice exist, mirroring
    ; voice_files_exist() in speak.py. The voice entry's file paths are
    ; parsed from config.json; missing file entries count as absent.
    global AppDir, ConfigPath
    try {
        content := FileRead(ConfigPath, "UTF-8")
        pat := '"' . voiceId . '"\s*:\s*\{[^{}]*?"model"\s*:\s*"([^"]+)"[^{}]*?"config"\s*:\s*"([^"]+)"'
        if RegExMatch(content, pat, &m) {
            return FileExist(AppDir . "\" . m[2]) and FileExist(AppDir . "\" . m[3])
        }
    }
    return false
}

SetVoice(voiceId, *) {
    global Q, PyExe, AppDir, ResponsePath, RequestPath
    StopSpeech()

    if IsDaemonReady() {
        ; Daemon-direct switch: the daemon validates the voice, loads the
        ; model, and persists config.json itself — no synchronous Python
        ; RunWait blocking the AHK thread. The old RunWait path froze the
        ; app for the duration of a Python interpreter cold start AND let
        ; the CLI write config before the daemon learned about it.
        req := '{"action":"set_voice","voice":"' . voiceId . '"}'
        try FileDelete ResponsePath
        FileAppend req, RequestPath, "UTF-8-RAW"
        ok := WaitResponse(20)
        resp := LastResponse
        InitTray()
        if (ok and InStr(resp, '"ok"')) {
            TrayTip "Voice set to " . voiceId, "ReadAloudTTS"
        } else {
            TrayTip "Could not switch voice: " . (resp != "" ? resp : "daemon not responding"), "ReadAloudTTS"
        }
        return
    }

    ; Daemon not running — fall back to the CLI path (fresh-boot voice pick).
    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }
    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --set-voice " . Q . voiceId . Q
    exitCode := RunWait(cmd, AppDir, "Hide")
    if (exitCode = 0) {
        InitTray()
        TrayTip "Voice set to " . voiceId, "ReadAloudTTS"
    } else {
        TrayTip "Could not set voice. Open logs for details.", "ReadAloudTTS"
    }
}

; ---------------------------------------------------------------------------
; Read / Stop / misc
; ---------------------------------------------------------------------------

SuppressCtrlRightClick(*) {
    return
}

ReadSelection(*) {
    global PyExe, AppDir, PidPath, TempDir, Q, RequestPath, ResponsePath

    ; Hotkey-fired diagnostic — confirm the hook actually triggers in
    ; Electron apps (ZCode) where Home is normally eaten by the editor.
    DebugLog("ReadSelection() fired — host=" . WinGetProcessName("A"))

    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }

    savedClipboard := ClipboardAll()
    A_Clipboard := ""
    Sleep 40
    ; Use Ctrl+Insert instead of Ctrl+C to copy the selection. Ctrl+C is
    ; intercepted by AI coding tools (ZCode, etc.) as "stop generation"
    ; when text is selected during streaming/thinking output. Ctrl+Insert
    ; is the Windows legacy copy shortcut — Chrome, Electron, and most
    ; text editors respect it, but AI tools don't bind it to "stop."
    Send "{Ctrl down}{Insert}{Ctrl up}"

    if !ClipWait(1.2) {
        RestoreClipboard(savedClipboard)
        TrayTip "No selected text found.", "ReadAloudTTS"
        return
    }

    text := A_Clipboard
    RestoreClipboard(savedClipboard)
    text := Trim(text)

    if (text = "") {
        TrayTip "No selected text found.", "ReadAloudTTS"
        return
    }

    StopSpeech()
    TrayTip "Reading selected text...", "ReadAloudTTS"

    ; Try the daemon path first (fast — model pre-warmed). Use the
    ; ping-verified check: a stale marker from a crashed daemon would
    ; otherwise send the request to a dead queue and hang for 120s.
    if EnsureDaemonAlive() {
        SpeakViaDaemon(text)
    } else {
        StartDaemon()
        if EnsureDaemonAlive() {
            SpeakViaDaemon(text)
        } else {
            SpeakColdStart(text)
        }
    }

    SetTimer DismissContextMenu, -150
}

SpeakViaDaemon(text) {
    global RequestPath, ResponsePath, HighlightPath, LastResponse
    ; Escape the text for JSON.
    jsonText := JsonEscape(text)
    req := '{"action":"speak","text":"' . jsonText . '"}'
    ; Clean up any stale response and highlight files.
    try FileDelete ResponsePath
    try FileDelete HighlightPath
    FileAppend req, RequestPath, "UTF-8-RAW"
    ; Start the highlight overlay timer only when enabled (opt-in).
    if ShowOverlayEnabled() {
        StartHighlightTimer()
    }
    ; Wait for the response (up to 120s for long text). The daemon replies
    ; "Speak started" immediately after spawning the playback worker; an
    ; error status here means nothing will play — surface it instead of
    ; failing silently (the 2026-08-30 outage was 20 minutes of silent
    ; error responses with zero user-visible feedback).
    if WaitResponse(120) {
        if !InStr(LastResponse, '"ok"') {
            msg := LastResponse
            if RegExMatch(msg, '"message"\s*:\s*"([^"]*)"', &m) {
                msg := m[1]
            }
            TrayTip "ReadAloudTTS: " . msg, "Read failed"
        }
    }
}

SpeakColdStart(text) {
    global PyExe, AppDir, PidPath, TempDir, Q
    DirCreate TempDir
    inputPath := TempDir . "\selection-" . A_TickCount . "-" . Random(100000, 999999) . ".txt"
    FileAppend text, inputPath, "UTF-8-RAW"
    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --input-file " . Q . inputPath . Q . " --delete-input-file"
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete PidPath
    FileAppend pid, PidPath, "UTF-8-RAW"
}

WaitResponse(timeoutSec := 120) {
    global ResponsePath, LastResponse
    LastResponse := ""
    endTick := A_TickCount + (timeoutSec * 1000)
    while (A_TickCount < endTick) {
        if FileExist(ResponsePath) {
            try LastResponse := FileRead(ResponsePath, "UTF-8")
            try FileDelete ResponsePath
            return true
        }
        Sleep 50
    }
    return false
}

JsonEscape(text) {
    text := StrReplace(text, "\", "\\")
    text := StrReplace(text, '"', '\"')
    text := StrReplace(text, "`n", "\n")
    text := StrReplace(text, "`r", "\r")
    text := StrReplace(text, "`t", "\t")
    return text
}

; ---------------------------------------------------------------------------
; Speed control (on-the-fly length_scale adjustment)
; ---------------------------------------------------------------------------
; Sends a "set_speed" action to the daemon. The daemon updates the runtime
; length_scale override (takes effect on the next chunk) and persists to
; config.json. We read the current speed from config.json to compute the
; new value — the daemon is the source of truth, so we reload from disk
; each time (the daemon writes atomically).

GetCurrentSpeed() {
    global ConfigPath
    try {
        content := FileRead(ConfigPath, "UTF-8-RAW")
        if RegExMatch(content, '"length_scale"\s*:\s*([\d.]+)', &m) {
            return Round(m[1], 2)
        }
    }
    return 1.0
}

SendSpeed(speed) {
    global RequestPath, ResponsePath
    speed := Max(0.5, Min(2.0, Round(speed, 2)))
    ; Read current speed from config to avoid accumulating rounding drift.
    current := GetCurrentSpeed()
    ; If the daemon set a runtime override, it also wrote it to config,
    ; so reading config gives us the live value.
    newSpeed := Max(0.5, Min(2.0, Round(current * speed, 2)))
    if (newSpeed = current)
        return  ; No change needed.
    try FileDelete ResponsePath
    req := '{"action":"set_speed","speed":' . newSpeed . '}'
    FileAppend req, RequestPath, "UTF-8-RAW"
    if WaitResponse(3) {
        ; Read the daemon's response for a human-friendly label.
        try {
            resp := FileRead(ResponsePath, "UTF-8-RAW")
        } catch {
            resp := ""
        }
    }
    ; Show a tray tip with the new speed.
    if (newSpeed < 1.0) {
        mult := 1.0 / newSpeed
        label := Round(mult, 1) . "x faster"
    } else if (newSpeed > 1.0) {
        label := Round(newSpeed, 1) . "x slower"
    } else {
        label := "normal speed"
    }
    TrayTip label, "ReadAloudTTS Speed"
}

AdjustSpeed(factor) {
    ; factor < 1 = faster (e.g. 0.9 = 10% faster)
    ; factor > 1 = slower (e.g. 1.1 = 10% slower)
    SendSpeed(factor)
}

ResetSpeed() {
    global RequestPath, ResponsePath
    current := GetCurrentSpeed()
    if (current = 1.0) {
        TrayTip "Already normal speed", "ReadAloudTTS Speed"
        return
    }
    try FileDelete ResponsePath
    FileAppend '{"action":"set_speed","speed":1.0}', RequestPath, "UTF-8-RAW"
    WaitResponse(3)
    TrayTip "Normal speed", "ReadAloudTTS Speed"
}

DebugLog(msg) {
    global TempDir
    logPath := TempDir . "\working_debug.log"
    ts := FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss")
    try FileAppend "[" . ts . "] " . msg . "`n", logPath, "UTF-8-RAW"
}

RestoreClipboard(savedClipboard) {
    try A_Clipboard := savedClipboard
}

DismissContextMenu() {
    Send "{Ctrl Up}{Esc}"
}

StopSpeech(*) {
    global PidPath, RequestPath, ResponsePath
    ; Stop the highlight overlay first.
    StopHighlightTimer()
    HideHighlightOverlay()
    ; If the daemon is running, send it a stop request.
    if IsDaemonReady() {
        try FileDelete ResponsePath
        FileAppend '{"action":"stop"}', RequestPath, "UTF-8-RAW"
        WaitResponse(3)
    }
    ; Also kill any cold-start speak.py process.
    if FileExist(PidPath) {
        pidText := Trim(FileRead(PidPath, "UTF-8-RAW"))
        if RegExMatch(pidText, "^\d+$") {
            try ProcessClose Integer(pidText)
        }
        try FileDelete PidPath
    }
}

OpenConfig(*) {
    global ConfigPath, Q
    Run "notepad.exe " . Q . ConfigPath . Q
}

OpenLogs(*) {
    global AppDir, Q
    Run "explorer.exe " . Q . AppDir . "\logs" . Q
}

; ---------------------------------------------------------------------------
; Word highlighting overlay
; ---------------------------------------------------------------------------
;
; A minimal always-on-top borderless window that shows the text being read
; with the current word selected (highlighted). The daemon writes
; highlight_state.json with per-word timings; this timer polls it and
; moves the Edit selection to match the spoken word.
;
; The overlay is intentionally small (bottom-center, ~40% screen width) so
; it doesn't obscure the source text. Click it to dismiss; Ctrl+Alt+Space
; to stop speech and hide it.

StartHighlightTimer() {
    global HighlightTimer
    if HighlightTimer != "" {
        SetTimer HighlightTimer, 0
    }
    HighlightTimer := HighlightTick
    SetTimer HighlightTimer, 30
}

StopHighlightTimer() {
    global HighlightTimer
    if HighlightTimer != "" {
        SetTimer HighlightTimer, 0
        HighlightTimer := ""
    }
}

HighlightTick() {
    global HighlightGui, HighlightPaused, gLastMouseX, gLastMouseY
    ; Hover-pause/resume via this 30ms poll — Gui has no MouseMove event in
    ; AHK v2. Resume works even when the overlay was torn down mid-pause
    ; (stop-state handler destroys the GUI; position/paused survive now).
    if (HighlightPaused) {
        if !IsMouseOverOverlay() {
            OverlayMouseLeaveResume()
        }
    } else if (HighlightGui != "") {
        if IsMouseOverOverlay() {
            ; Only pause on mouse MOVEMENT into the overlay. If the overlay
            ; rebuilds (resume) directly under a resting cursor, pausing
            ; immediately would flap pause/resume forever.
            MouseGetPos &mx, &my
            if (mx != gLastMouseX or my != gLastMouseY) {
                OverlayHoverPause()
            }
        }
    }
    MouseGetPos &gLastMouseX, &gLastMouseY
    if !FileExist(HighlightPath) {
        return
    }
    try {
        raw := FileRead(HighlightPath, "UTF-8-RAW")
    } catch {
        return
    }
    raw := Trim(raw)
    if (raw = "") {
        return
    }
    ; Parse the single-line JSON state.
    state := JsonGet(raw, "state")
    if (state = "start") {
        HighlightOnStart(raw)
    } else if (state = "playing") {
        HighlightOnPlaying(raw)
    } else if (state = "done" or state = "stop") {
        HighlightOnStop()
    }
}

IsMouseOverOverlay() {
    global HighlightGui
    if (HighlightGui = "") {
        return false
    }
    MouseGetPos &mouseX, &mouseY, &winHwnd
    return (winHwnd = HighlightGui.Hwnd)
}

HighlightOnStart(raw) {
    global HighlightWords, HighlightTotalMs, HighlightPlayStart
    text := JsonGet(raw, "text")
    totalMs := JsonGet(raw, "total_ms")
    HighlightTotalMs := (totalMs != "") ? Round(totalMs) : 0
    ; Parse words: [["word",start_ms,end_ms],...]
    HighlightWords := ParseWordTimings(raw)
    HighlightPlayStart := A_TickCount
    ShowHighlightOverlay(text)
}

HighlightOnPlaying(raw) {
    global HighlightWords, HighlightPlayStart, HighlightTotalMs, HighlightGui
    global HighlightCurrentIdx, HighlightPaused
    ; Skip updates while paused (hover-pause).
    if (HighlightPaused) {
        return
    }
    ; On first "playing" state, initialize the overlay from the full payload.
    if (HighlightGui = "") {
        text := JsonGet(raw, "text")
        totalMs := JsonGet(raw, "total_ms")
        HighlightTotalMs := (totalMs != "") ? Round(totalMs) : 0
        HighlightWords := ParseWordTimings(raw)
        ShowHighlightOverlay(text)
    }
    msStr := JsonGet(raw, "ms")
    if (msStr = "") {
        return
    }
    ; The daemon appends word timings per chunk during playback. When the
    ; payload carries a words array larger than what we parsed, refresh —
    ; this lets the overlay highlight ahead into not-yet-played chunks.
    if RegExMatch(raw, '"words"\s*:\s*\[') {
        newWords := ParseWordTimings(raw)
        if (newWords.Length > HighlightWords.Length) {
            HighlightWords := newWords
        }
    }
    elapsed := Round(msStr)
    ; Find the word whose [start_ms, end_ms) contains elapsed.
    idx := FindWordIndex(HighlightWords, elapsed)
    if (idx >= 0) {
        HighlightCurrentIdx := idx
        SelectOverlayWord(idx)
    }
}

HighlightOnStop() {
    StopHighlightTimer()
    HideHighlightOverlay()
}

; --- Overlay GUI ---

ShowOverlayEnabled() {
    global ConfigPath
    try {
        content := FileRead(ConfigPath, "UTF-8-RAW")
        if RegExMatch(content, '"highlight_overlay"\s*:\s*(true|false)', &m) {
            return (m[1] = "true")
        }
    }
    return false  ; Opt-in by default — the box annoyed the user.
}

ToggleOverlayEnabled() {
    global ConfigPath
    newVal := ShowOverlayEnabled() ? "false" : "true"
    try {
        content := FileRead(ConfigPath, "UTF-8-RAW")
        if RegExMatch(content, '"highlight_overlay"\s*:') {
            content := RegExReplace(content, '"highlight_overlay"\s*:\s*(true|false)', '"highlight_overlay": ' . newVal)
        } else {
            content := RegExReplace(content, '\{', '{' . Q . 'highlight_overlay' . Q . ': ' . newVal . ', ', , 1)
        }
        FileDelete ConfigPath
        FileAppend content, ConfigPath, "UTF-8-RAW"
    }
    return ShowOverlayEnabled()
}

ShowHighlightOverlay(text) {
    global HighlightGui, HighlightFullText
    HideHighlightOverlay()
    HighlightFullText := text
    ; +E0x08000000 = WS_EX_NOACTIVATE: window doesn't steal focus when clicked.
    ; Do NOT use +E0x20 (WS_EX_TRANSPARENT) — it makes the window invisible
    ; to mouse events, which would break hover-pause and click-to-rewind.
    HighlightGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000")
    HighlightGui.BackColor := "1a1a2e"
    HighlightGui.SetFont("s12", "Segoe UI")
    ; Translucent dark panel, ~50% of screen width, bottom-center.
    screenWidth := A_ScreenWidth
    panelWidth := Round(screenWidth * 0.5)
    panelHeight := 80
    panelX := Round((screenWidth - panelWidth) / 2)
    panelY := A_ScreenHeight - panelHeight - 60
    HighlightGui.MarginX := 16
    HighlightGui.MarginY := 12
    ; -E0x200 removes WS_EX_TRANSPARENT from the Edit control too.
    ; +0x100 = ES_NOHIDESEL: keep the selection VISIBLE when the control
    ; doesn't have focus. The window is WS_EX_NOACTIVATE and never takes
    ; focus, so without this style EM_SETSEL ran every 30ms invisibly.
    editCtrl := HighlightGui.Add("Edit", "w" . (panelWidth - 32) . " h" . (panelHeight - 24) . " -VScroll +0x100 cWhite Background1a1a2e", text)
    ; NOTE: hover-pause is implemented by polling IsMouseOverOverlay() in
    ; HighlightTick — Gui.OnEvent("MouseMove", ...) is INVALID in AHK v2
    ; (valid events are Close/Escape/Size/ContextMenu/DropFiles) and threw
    ; "Parameter #1 of Gui.Prototype.OnEvent is invalid" on every read.
    ; Click-to-rewind uses OnMessage(WM_LBUTTONDOWN), registered ONCE in the
    ; auto-execute section — registering per-overlay-build stacked handlers.
    ; Make the window translucent (220/255 opacity).
    HighlightGui.Show("x" . panelX . " y" . panelY . " w" . panelWidth . " h" . panelHeight . " NA")
    SetTranslucent(HighlightGui.Hwnd, 220)
}

OverlayHoverPause(*) {
    global HighlightPaused, HighlightGui
    if (HighlightGui = "" or HighlightPaused) {
        return
    }
    HighlightPaused := true
    ; Stop the daemon playback (it will remember nothing — resume re-sends text from current word).
    StopSpeechDaemon()
}

OverlayMouseLeaveResume(*) {
    global HighlightPaused, HighlightCurrentIdx
    if (!HighlightPaused) {
        return
    }
    ; NOTE: no HighlightGui check here — hover-pause may have torn the
    ; overlay down (stop-state handler); resume must fire regardless.
    HighlightPaused := false
    ; Resume from the current word index.
    if (HighlightCurrentIdx >= 0) {
        SeekFromWord(HighlightCurrentIdx)
    }
}

OverlayClickHandler(wParam, lParam, msg, hwnd) {
    global HighlightGui, HighlightWords
    if (HighlightGui = "") {
        return
    }
    ; Get the character position under the cursor via EM_CHARFROMPOS = 0xD7.
    ctrlHwnd := 0
    try {
        ctrl := HighlightGui["Edit1"]
        if IsObject(ctrl) {
            ctrlHwnd := ctrl.Hwnd
        }
    } catch {
        return
    }
    if (ctrlHwnd = 0 or hwnd != ctrlHwnd) {
        return
    }
    ; lParam has the client coordinates (low word = x, high word = y).
    px := lParam & 0xFFFF
    py := (lParam >> 16) & 0xFFFF
    ; EM_CHARFROMPOS returns char index in low word, line in high word.
    charIdx := SendMessage(0xD7, 0, (py << 16) | px, ctrlHwnd)
    charIdx := charIdx & 0xFFFF
    ; Find which word this char belongs to.
    idx := FindWordByChar(HighlightWords, charIdx)
    if (idx >= 0) {
        SeekFromWord(idx)
    }
}

FindWordByChar(words, charIdx) {
    ; words is 1-based AHK array of [word, startMs, endMs, charStart, charEnd].
    i := 1
    while (i <= words.Length) {
        w := words[i]
        if (charIdx >= w[4] and charIdx <= w[5]) {
            return i - 1  ; 0-based index
        }
        i++
    }
    return -1
}

SeekFromWord(idx) {
    global HighlightFullText, RequestPath, ResponsePath, HighlightPath
    global HighlightCurrentIdx
    HighlightCurrentIdx := idx
    ; Stop current playback.
    StopSpeechDaemon()
    ; Clear state and send a seek request.
    try FileDelete ResponsePath
    try FileDelete HighlightPath
    jsonText := JsonEscape(HighlightFullText)
    req := '{"action":"speak","text":"' . jsonText . '","from_word":' . idx . '}'
    FileAppend req, RequestPath, "UTF-8-RAW"
    ; Restart the highlight timer (only when the overlay is enabled).
    if ShowOverlayEnabled() {
        StartHighlightTimer()
    }
}

StopSpeechDaemon() {
    global RequestPath, ResponsePath
    if IsDaemonReady() {
        try FileDelete ResponsePath
        FileAppend '{"action":"stop"}', RequestPath, "UTF-8-RAW"
        WaitResponse(3)
    }
}

SelectOverlayWord(idx) {
    global HighlightGui, HighlightWords
    if HighlightGui = "" {
        return
    }
    ; Calculate character offset of word idx in the full text.
    ; We use the stored word start/end offsets computed at parse time.
    wordInfo := HighlightWords[idx + 1]  ; AHK arrays are 1-based
    if !IsObject(wordInfo) {
        return
    }
    charStart := wordInfo[4]
    charEnd := wordInfo[5]
    if (charStart < 0 or charEnd < 0) {
        return
    }
    ; Select the current word (highlight). EM_SETSEL = 0xB1.
    ; Find the Edit control.
    try {
        ctrl := HighlightGui["Edit1"]
        if IsObject(ctrl) {
            SendMessage(0xB1, charStart, charEnd, ctrl)
        }
    } catch {
        ; Fallback: use window handle.
        SendMessage 0xB1, charStart, charEnd, "Edit1", "ahk_id " . HighlightGui.Hwnd
    }
}

HideHighlightOverlay() {
    global HighlightGui, HighlightPaused, HighlightCurrentIdx
    if HighlightGui != "" {
        try HighlightGui.Destroy()
        HighlightGui := ""
    }
    ; Preserve pause/position when a hover-pause teardown lands here —
    ; wiping them made mouse-leave resume impossible (the resume branch
    ; requires HighlightPaused=true and idx>=0), killing speech until the
    ; next Home press. Only reset for genuine final stops.
    if (!HighlightPaused) {
        HighlightCurrentIdx := -1
    }
}

; ---------------------------------------------------------------------------
; Transcript overlay
; ---------------------------------------------------------------------------
;
; A separate, larger, scrollable window that shows the full text being read.
; Opens via Ctrl+Alt+T or the tray menu. Stays open until closed so the user
; can review what was read. If the highlight overlay is active, the transcript
; syncs to show the same text.

ShowTranscript(*) {
    global TranscriptGui, HighlightFullText
    ; Destroy any existing transcript window.
    if TranscriptGui != "" {
        try TranscriptGui.Destroy()
        TranscriptGui := ""
    }
    ; Use the last-read text if available; otherwise prompt for clipboard.
    text := HighlightFullText
    if (text = "") {
        savedClipboard := ClipboardAll()
        A_Clipboard := ""
        Sleep 40
        Send "{Ctrl down}{Insert}{Ctrl up}"
        if ClipWait(1.0) {
            text := A_Clipboard
        }
        try A_Clipboard := savedClipboard
        text := Trim(text)
    }
    if (text = "") {
        TrayTip "No text to show. Select text and read first, or copy text to clipboard.", "ReadAloudTTS"
        return
    }

    TranscriptGui := Gui("+AlwaysOnTop +Resize +MinSize300x200", "ReadAloudTTS Transcript")
    TranscriptGui.BackColor := "1a1a2e"
    TranscriptGui.SetFont("s12", "Segoe UI")
    ; Scrollable read-only Edit control (+VScroll shows the scrollbar so
    ; long transcripts can be navigated; previously -VScroll hid it).
    TranscriptGui.MarginX := 12
    TranscriptGui.MarginY := 12
    editCtrl := TranscriptGui.Add("Edit", "w600 h400 +ReadOnly +VScroll +Wrap cWhite Background1a1a2e", text)
    ; Close button.
    TranscriptGui.Add("Button", "default w120 x260 h32", "Close").OnEvent("Click", (*) => CloseTranscript())
    TranscriptGui.OnEvent("Close", (*) => CloseTranscript())
    TranscriptGui.OnEvent("Size", TranscriptResize)
    TranscriptGui.Show("AutoSize")
}

TranscriptResize(*) {
    global TranscriptGui
    if TranscriptGui = "" {
        return
    }
    ; Resize the Edit control to fill the window.
    try {
        ctrl := TranscriptGui["Edit1"]
        if IsObject(ctrl) {
            ctrl.Move(, , TranscriptGui.ClientWidth - 24, TranscriptGui.ClientHeight - 60)
        }
    }
}

CloseTranscript(*) {
    global TranscriptGui
    if TranscriptGui != "" {
        try TranscriptGui.Destroy()
        TranscriptGui := ""
    }
}

; --- JSON helpers (minimal regex parsing, no external lib) ---

JsonGet(json, key) {
    pat := '"\s*' . key . '\s*"\s*:\s*'
    if RegExMatch(json, pat . '(-?\d+\.?\d*)', &m) {
        return m[1]
    }
    if RegExMatch(json, pat . '"([^"]*)"', &m) {
        return m[1]
    }
    return ""
}

ParseWordTimings(json) {
    ; Extract the "words" array and build per-word info with char offsets.
    ; Each entry: ["word", start_ms, end_ms]. We also compute char offsets
    ; by scanning the full text for each word sequentially.
    global HighlightWords
    result := []
    text := JsonGet(json, "text")
    pos := 1
    ; Find each word tuple via regex.
    pat := '\["([^"]+)",\s*([\d.]+),\s*([\d.]+)\]'
    searchFrom := 1
    charSearchPos := 1
    while RegExMatch(json, pat, &m, searchFrom) {
        word := m[1]
        startMs := Round(m[2])
        endMs := Round(m[3])
        ; Find this word's char offset in the full text (sequential scan).
        foundPos := InStr(text, word, false, charSearchPos)
        if (foundPos > 0) {
            charStart := foundPos - 1  ; 0-based for EM_SETSEL
            charEnd := foundPos + StrLen(word) - 1
            result.Push([word, startMs, endMs, charStart, charEnd])
            charSearchPos := foundPos + StrLen(word)
        } else {
            ; Fallback: append with unknown offset.
            result.Push([word, startMs, endMs, -1, -1])
        }
        searchFrom := m.Pos + m.Len
    }
    return result
}

FindWordIndex(words, elapsedMs) {
    ; words is 1-based AHK array of [word, startMs, endMs, charStart, charEnd].
    ; Return 0-based index of the word whose [startMs, endMs) contains elapsedMs.
    if (words.Length = 0) {
        return -1
    }
    i := 1
    while (i <= words.Length) {
        w := words[i]
        if (elapsedMs >= w[2] and elapsedMs < w[3]) {
            return i - 1
        }
        i++
    }
    ; If past the last word, return last index.
    return words.Length - 1
}

SetTranslucent(hwnd, opacity) {
    ; opacity: 0-255. 255 = fully opaque.
    if (hwnd) {
        exStyle := DllCall("GetWindowLong", "Ptr", hwnd, "Int", -20, "Ptr")
        DllCall("SetWindowLong", "Ptr", hwnd, "Int", -20, "Ptr", exStyle | 0x80000)
        DllCall("SetLayeredWindowAttributes", "Ptr", hwnd, "UInt", 0, "UChar", opacity, "UInt", 0x02)
    }
}

OnExit(ExitFunc)

ExitFunc(*) {
    StopHighlightTimer()
    HideHighlightOverlay()
    CloseTranscript()
    StopDaemon()
    ExitApp
}