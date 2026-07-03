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

; Highlight overlay state
global HighlightGui := ""
global HighlightWords := []
global HighlightTotalMs := 0
global HighlightPlayStart := 0
global HighlightTimer := ""
global HighlightCurrentIdx := -1
global HighlightPaused := false
global HighlightFullText := ""

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
    ; Clean up any stale response and highlight files.
    try FileDelete ResponsePath
    try FileDelete HighlightPath
    FileAppend req, RequestPath, "UTF-8"
    ; Start the highlight overlay timer (polls highlight_state.json).
    StartHighlightTimer()
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
    ; Stop the highlight overlay first.
    StopHighlightTimer()
    HideHighlightOverlay()
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
    global HighlightPath, HighlightGui, HighlightPaused
    ; Check mouse-leave resume (hover-pause: resume when mouse leaves overlay).
    if (HighlightGui != "" and HighlightPaused) {
        if !IsMouseOverOverlay() {
            OverlayMouseLeaveResume()
        }
    }
    if !FileExist(HighlightPath) {
        return
    }
    try {
        raw := FileRead(HighlightPath, "UTF-8")
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

ShowHighlightOverlay(text) {
    global HighlightGui, HighlightFullText
    HideHighlightOverlay()
    HighlightFullText := text
    HighlightGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x20")
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
    editCtrl := HighlightGui.Add("Edit", "w" . (panelWidth - 32) . " h" . (panelHeight - 24) . " -VScroll -E0x200 cWhite Background1a1a2e", text)
    ; Hover-pause: when mouse enters the overlay, pause speech.
    ; Mouse leaves: resume from current word.
    HighlightGui.OnEvent("MouseMove", OverlayHoverPause)
    ; Click on the Edit control: rewind to the clicked word.
    OnMessage(0x201, OverlayClickHandler)  ; WM_LBUTTONDOWN
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
    global HighlightPaused, HighlightGui, HighlightCurrentIdx
    if (HighlightGui = "" or !HighlightPaused) {
        return
    }
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
    FileAppend req, RequestPath, "UTF-8"
    ; Restart the highlight timer.
    StartHighlightTimer()
}

StopSpeechDaemon() {
    global RequestPath, ResponsePath
    if IsDaemonReady() {
        try FileDelete ResponsePath
        FileAppend '{"action":"stop"}', RequestPath, "UTF-8"
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
    HighlightPaused := false
    HighlightCurrentIdx := -1
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
    StopDaemon()
    ExitApp
}