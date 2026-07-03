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
global PyExe := AppDir . "\.venv\Scripts\python.exe"
global Q := Chr(34)

DirCreate TempDir
; Clean up any stale daemon readiness marker from a previous run.
try FileDelete DaemonReadyPath
InitTray()

; Register hotkeys BEFORE StartDaemon so the keyboard hook is installed
; before the auto-execute section blocks on daemon warmup.
^RButton::ReadSelection()
^RButton Up::SuppressCtrlRightClick()
^!Space::StopSpeech()

StartDaemon()

; ---------------------------------------------------------------------------
; Daemon management
; ---------------------------------------------------------------------------

IsDaemonReady() {
    global DaemonReadyPath
    return FileExist(DaemonReadyPath) != ""
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
    ; Launch the long-lived speak_server daemon (hidden window).
    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --serve"
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete DaemonPidPath
    FileAppend pid, DaemonPidPath, "UTF-8"
    ; Wait up to 10s for the daemon to load the model and signal ready.
    WaitDaemonReady(10)
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
    ; Send a quit request if the daemon is responsive.
    if IsDaemonReady() {
        req := '{"action":"quit"}'
        FileAppend req, RequestPath, "UTF-8"
        Sleep 500
    }
    ; Force-kill by PID if still alive.
    if FileExist(DaemonPidPath) {
        pidText := Trim(FileRead(DaemonPidPath, "UTF-8"))
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
    A_TrayMenu.Add("Stop`tCtrl+Alt+Space", (*) => StopSpeech())
    A_TrayMenu.Add()

    voiceMenu := Menu()
    voices := GetVoiceMap()
    current := GetCurrentVoice()
    for voiceId, label in voices {
        boundId := voiceId
        voiceMenu.Add(label, (*) => SetVoice(boundId))
        if (voiceId = current) {
            voiceMenu.Check(label)
        }
    }
    A_TrayMenu.Add("Voice", voiceMenu)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Open Config", (*) => OpenConfig())
    A_TrayMenu.Add("Open Logs", (*) => OpenLogs())
    A_TrayMenu.Add("Restart Daemon", (*) => RestartDaemon())
    A_TrayMenu.Add("Exit", (*) => ExitApp())
    A_TrayMenu.Default := "Read Selection`tCtrl+Right-click"
}

RestartDaemon(*) {
    StopDaemon()
    StartDaemon()
    if IsDaemonReady()
        TrayTip "TTS daemon restarted", "ReadAloudTTS"
    else
        TrayTip "TTS daemon failed to start", "ReadAloudTTS"
}

; ---------------------------------------------------------------------------
; Config helpers (unchanged from original)
; ---------------------------------------------------------------------------

GetVoiceMap() {
    global ConfigPath
    voices := Map()

    try {
        content := FileRead(ConfigPath, "UTF-8")
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

SetVoice(voiceId, *) {
    global Q, PyExe, AppDir
    StopSpeech()

    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }

    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --set-voice " . Q . voiceId . Q
    exitCode := RunWait(cmd, AppDir, "Hide")
    if (exitCode = 0) {
        ; Tell the daemon to reload the voice if it's running.
        if IsDaemonReady() {
            req := '{"action":"set_voice","voice":"' . voiceId . '"}'
            try FileDelete ResponsePath
            FileAppend req, RequestPath, "UTF-8"
            ; Wait briefly for the daemon to process it.
            WaitResponse(3)
        }
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

    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }

    savedClipboard := ClipboardAll()
    A_Clipboard := ""
    Sleep 40
    Send "^c"

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

    ; Try the daemon path first (fast — model pre-warmed).
    if IsDaemonReady() {
        SpeakViaDaemon(text)
    } else {
        StartDaemon()
        if IsDaemonReady() {
            SpeakViaDaemon(text)
        } else {
            SpeakColdStart(text)
        }
    }

    SetTimer DismissContextMenu, -150
}

SpeakViaDaemon(text) {
    global RequestPath, ResponsePath
    ; Escape the text for JSON.
    jsonText := JsonEscape(text)
    req := '{"action":"speak","text":"' . jsonText . '"}'
    ; Clean up any stale response file.
    try FileDelete ResponsePath
    FileAppend req, RequestPath, "UTF-8"
    ; Wait for the response (up to 120s for long text).
    WaitResponse(120)
}

SpeakColdStart(text) {
    global PyExe, AppDir, PidPath, TempDir, Q
    DirCreate TempDir
    inputPath := TempDir . "\selection-" . A_TickCount . "-" . Random(100000, 999999) . ".txt"
    FileAppend text, inputPath, "UTF-8-RAW"
    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --input-file " . Q . inputPath . Q . " --delete-input-file"
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete PidPath
    FileAppend pid, PidPath, "UTF-8"
}

WaitResponse(timeoutSec := 120) {
    global ResponsePath
    endTick := A_TickCount + (timeoutSec * 1000)
    while (A_TickCount < endTick) {
        if FileExist(ResponsePath) {
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

RestoreClipboard(savedClipboard) {
    try A_Clipboard := savedClipboard
}

DismissContextMenu() {
    Send "{Ctrl Up}{Esc}"
}

StopSpeech(*) {
    global PidPath, RequestPath, ResponsePath
    ; If the daemon is running, send it a stop request.
    if IsDaemonReady() {
        try FileDelete ResponsePath
        FileAppend '{"action":"stop"}', RequestPath, "UTF-8"
        WaitResponse(3)
    }
    ; Also kill any cold-start speak.py process.
    if FileExist(PidPath) {
        pidText := Trim(FileRead(PidPath, "UTF-8"))
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

OnExit(ExitFunc)

ExitFunc(*) {
    StopDaemon()
    ExitApp
}