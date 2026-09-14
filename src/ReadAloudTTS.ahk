#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; RichEdit 4.1 (msftedit.dll) hosts the reading overlay: per-word text
; COLORING instead of the classic Edit's blue selection block. MUST load
; the DLL before creating any RichEdit50W control (SDK: msftedit.dll is
; the only module that registers the class).
#DllLoad Msftedit.dll

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
global HighlightLastColored := -1
global HighlightPaused := false
global HighlightFullText := ""
global gSeekInFlight := false
global gLastSeekTick := 0
global gOnStartLogged := false
global gOverlayReducedMotion := false
global TranscriptGui := ""
global ReplayGui := ""
; --- Overlay stability state (research-driven hardening 2026-09-07) ---
; Gate ALL Edit writes on state change: EM_SETSEL every 30ms starves
; WM_PAINT (the freeze/flicker root cause). Track the last applied
; selection so a tick with an unchanged word touches nothing.
global gLastSelStart := -1
global gLastSelEnd := -1
; Last logged tick state — gates the tick DebugLog to transitions only.
global gLastTickState := ""
    ; Pause-indicator status text ("⏸ paused — Space resumes" / "Space pauses").
    ; Space is the ONLY pause path now — hover-pause was removed 2026-09-07
    ; (user: more frustrating than helpful).
    global OverlayStatusCtrl := ""
    ; Demo-child per-tick report gate (demo studio only).
    global gLastReportedIdx := -1
    global gLastReportedGui := -1
; DPI scale for overlay geometry (per-monitor: read at build time).
global gOverlayDpi := 96
; Session memory of the user's dragged panel position: rebuilds reuse
; the last dragged spot instead of snapping back to bottom-center every read.
; -1,-1 = never dragged (use default position).
global gOverlayDraggedX := -1
global gOverlayDraggedY := -1
; UI zoom scale for the overlay (user-requested drag-to-scale + Ctrl+wheel
; 2026-09-08). ONE multiplier composes with the DPI scale everywhere the panel
; is built: width/height/margins/font twips all derive from uiScale =
; dpiScale * gOverlayScale. Since 2026-09-10 zoom also grows the VISIBLE LINE
; COUNT (OverlayLineCount: 2 lines at 1.0 → 5 at ~1.9) — the old lockstep
; font+box scaling froze the panel at 2 lines at every zoom, so zooming up
; bought bigger text but never more reading context. Clamp 0.75..2.0: below
; 0.75 the status line would shrink below legibility; at 2.0 the 5-line
; panel is ~384px, inside a 1080p work area. Session-only like dragged-
; position (a restart resets to 1.0 — deliberate: this is a readability aid
; for the current sitting, not a persistent preference) and every rebuild
; re-applies it, so a mid-read zoom survives the panel rebuilds that follow
; seeks/restarts.
global gOverlayScale := 1.0
; Per-read "Esc dismissed the panel" flag. The tick's playing stream must
; NOT resurrect a panel the user just dismissed: without this, Esc
; destroys the Gui and the next 30ms tick (state=playing, gui="") hits
; HighlightOnPlaying's recovery rebuild — the panel returns in the same
; second and the Esc was cosmetic (caught live 2026-09-07). Cleared on
; every genuine (re)build request (HighlightOnStart new read) and at
; stop/teardown so the NEXT read always starts clean.
global gOverlayDismissed := false
; --- Bare-wheel scroll override (2026-09-12) ---
; While the user is reading back through previous lines, auto-re-anchoring is
; suspended so the view holds still. See OverlayWheelScrollOverride.
global gScrollOverrideActive := false
global gScrollOverrideUntil := 0
; Allows disabling follow-mode entirely (a user who wants full manual control
; of the panel). No UI toggle yet — a config field can drive this later.
global gOverlayAutoScrollEnabled := true
; Drag offsets for the manual overlay drag (OverlayDragHandler).
global gDragOffX := 0
global gDragOffY := 0
global gDragStartX := 0
global gDragStartY := 0
global gDragMoved := false
; Corner-grip drag-to-scale state: a press on the bottom-right grip zone
; scales the panel (dx+dy → gOverlayScale) instead of moving it. The grip
; shares the drag model: offsets captured at press, a 16ms timer tracks
; until release, a 4px dead zone separates click from drag.
global gDragScaling := false
global gDragScaleStart := 1.0
global gDragScaleStartX := 0
global gDragScaleStartY := 0
global gDragScaleMoved := false
; Corner grip glyph control (bottom-right, dim — a discoverability hint
; for the drag-to-scale surface, right next to the "Ctrl+wheel zooms" copy).
global OverlayGripCtrl := ""

; --- Demo child mode (self-serve demo studio 2026-09-10) -------------------
; src/demo_director.py launches THIS script with --demo-child inside an
; isolated sandbox dir, then speaks the REAL daemon file protocol
; (tmp\highlight_state.json packets + tmp\request.json/response.json) so
; the real panel build, seek-resume, pause, speed and zoom paths all run
; for deterministic demo capture. Fail-closed by construction: hotkeys
; are suspended (below), no daemon is spawned, and the child only ever
; touches files inside its own AppDir. Production launches never pass
; --demo-child, so live behavior is byte-for-byte unchanged.
global gDemoChild := false
global gDemoParentPid := 0
loop A_Args.Length {
    if (A_Args[A_Index] = "--demo-child") {
        gDemoChild := true
    } else if (A_Args[A_Index] = "--demo-parent" and A_Index < A_Args.Length) {
        gDemoParentPid := Integer(A_Args[A_Index + 1])
    }
}

DirCreate TempDir
; Brand the tray — without this the taskbar shows AutoHotkey's generic icon.
; Graceful fallback: missing file just keeps the default icon.
if FileExist(AppDir . "\app.ico") {
    TraySetIcon(AppDir . "\app.ico")
}
A_IconTip := "ReadAloudTTS — select text, press Home"

; Demo child: arm the demo runner BEFORE hotkeys register (the first
; hotkey definition ends the auto-execute section, so any gate placed
; below it never runs). Demo children skip the tray, daemon spawn and
; prune cycle entirely — the Python director is their only producer, so
; a demo child can never speak audio or touch the live daemon.
if gDemoChild {
    DemoChildRun()
}

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
; Esc dismisses a visible overlay panel (speech keeps playing — it's a
; "get this off my screen" gesture, not a stop; F6 stays the stop key).
; No overlay = plain Esc passes through untouched.
$*Esc::OverlayEscKey()

$*^!t::ShowTranscript()

; Space = play/pause for the reading overlay (design contract #14:
; keyboard control, never pointer-only — WCAG 1.4.13). The ONLY pause
; trigger now; toggles pause while the overlay is reading, resumes when
; paused. Gated on the overlay context (pointer inside the panel or the
; panel focused) so normal Space typing elsewhere is untouched.
$*Space::OverlaySpaceKey()

; Speed control — on-the-fly rate adjustment.
;   Ctrl + * (Ctrl+Shift+8) = faster  (multiply = more speed)
;   Ctrl + /                = slower  (divide = less speed)
;   Ctrl + 0                = reset to normal speed (1.0) — the keyboard
;      complement of the nudge pair; ResetSpeed routes through SendSpeed
;      so the panel flash + tray tip carry the DAEMON-CONFIRMED value
;      (the old dead-code version wrote a bare request and read config,
;      which can be stale against the runtime override).
; Tray menu "Speed:" item cycles presets and resets to normal.
; Takes effect on the next chunk being synthesized, not the currently
; playing one. Persists to config.json so it survives restarts.
$*^+8::AdjustSpeed(0.9)
$*^/::AdjustSpeed(1.1)
$*^0::ResetSpeed()

; Demo child fail-closed: suspend every hotkey above. The demo child
; must never steal a keystroke from the user while the demo runs
; (Home/F6/Space/Esc/speed nudges all pass straight through to the
; real apps — the director drives this process by file, not keyboard).
if gDemoChild {
    Suspend 1
}

; Click-to-rewind on the highlight overlay: register the WM_LBUTTONDOWN
; monitor ONCE here. Previously it was registered inside every
; ShowHighlightOverlay() build, stacking duplicate message handlers.
OnMessage(0x201, OverlayDragHandler)
; Click-to-rewind on the RichEdit surface. EM_CHARFROMPOS on RichEdit
; is 0x427 (WM_USER+39) — NOT the classic Edit's 0xD7 — and takes a
; pointer to a POINT struct, returning the flat char index (not the
; packed low-word of the classic Edit).
OnMessage(0x201, OverlayClickHandler)
; Double-click suppression: a rapid second click on the same word (the
; user investigating the vanish) made the RichEdit child run its
; native double-click WORD-SELECT, and with the child focused the old
; selection block became visible — the "blue select mode" report. Block
; the message at the window level: the RichEdit never sees it, so it
; never starts a native selection, and clicks keep flowing to the seek
; handler below. (Losing native dblclick behavior is nothing — this
; surface's only click verb is seek.)
OnMessage(0x203, OverlayDoubleClickSuppress)
; Context-menu suppression: the RichEdit's native right-click menu offers
; Select All — the one verb that turns this display-only surface into a
; blue block. Right-click / Shift+F10 / Menu key all funnel here.
OnMessage(0x7B, OverlayContextMenuSuppress)
; Ctrl+wheel zoom on the overlay (user feature 2026-09-08). Win10/11
; posts WM_MOUSEWHEEL directly to the window under the cursor (Raymond Chen
; 2016-04-20), so this fires with the pointer over the panel even while
; another app holds focus. The handler returns an integer for panel/
; RichEdit targets — suppressing further processing is what stops BOTH the
; RichEdit's native wheel scroll AND its built-in Ctrl+wheel zoom (RichEdit
; 4.1 zooms natively; unsuppressed we'd double-step). Gated on Ctrl: a
; bare wheel over the panel stays native so any scroll instinct still works.
OnMessage(0x020A, OverlayWheelDispatch)

; Demo children never spawn a daemon: the Python director IS their
; daemon (it writes the same files the real one writes, with zero audio).
if !gDemoChild {
    StartDaemon()
}

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
    A_TrayMenu.Add("Open Reading Overlay", (*) => OpenReadingOverlay())
    A_TrayMenu.Add("Open Config", (*) => OpenConfig())
    A_TrayMenu.Add("Open Logs", (*) => OpenLogs())
    A_TrayMenu.Add("Show Transcript`tCtrl+Alt+T", (*) => ShowTranscript())
    A_TrayMenu.Add("Restart Daemon", (*) => RestartDaemon())
    A_TrayMenu.Add("Exit", (*) => ExitApp())
    A_TrayMenu.Default := "Read Selection`tCtrl+Right-click"
}

GetSpeedLabel() {
    return SpeedValueLabel(GetCurrentSpeed()) . " (Ctrl+*/)"
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
    ; Send the target speed directly, with the same confirmed-feedback
    ; path as the hotkeys (panel flash + tray tip).
    global RequestPath, ResponsePath, LastResponse
    try FileDelete ResponsePath
    FileAppend '{"action":"set_speed","speed":' . target . '}', RequestPath, "UTF-8-RAW"
    confirmed := target
    if WaitResponse(3) {
        if RegExMatch(LastResponse, '"speed"\s*:\s*([\d.]+)', &m) {
            confirmed := Round(m[1], 2)
        }
        FlashOverlaySpeed(confirmed)
        TrayTip SpeedValueLabel(confirmed), "ReadAloudTTS Speed"
    } else {
        FlashOverlaySpeed(-1)
        TrayTip "Speed not set — daemon unreachable", "ReadAloudTTS Speed", 2
    }
    InitTray()
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

    ; NO WinGetProcessName("A") here — it throws "Target window not
    ; found" when Home fires during a foreground transition (e.g. the
    ; notepad spawn), and an unguarded throw in a hotkey body pops a
    ; MODAL ERROR DIALOG that kills every hotkey until dismissed
    ; (caught live 2026-09-07: the user's hotkeys went dead).

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
    ; Clean up any stale response/highlight/request files. Deleting the
    ; request file matters: if the daemon is mid-read of an old request,
    ; FileAppend would CONCATENATE onto it and the daemon would reject the
    ; merged JSON (silent no-read).
    try FileDelete RequestPath
    try FileDelete ResponsePath
    try FileDelete HighlightPath
    FileAppend req, RequestPath, "UTF-8-RAW"
    ; Start the highlight overlay timer only when enabled (opt-in).
    if ShowOverlayEnabled() {
        DebugLog "SpeakViaDaemon: overlay enabled, starting tick timer"
        StartHighlightTimer()
    } else {
        DebugLog "SpeakViaDaemon: overlay DISABLED in config"
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
    ; Control characters (0x00-0x1F, 0x7F) not covered above — vertical tab,
    ; form feed, and stray 0x01s ride along in PDF/web clipboard copies —
    ; are invalid JSON and make the daemon's json.loads reject the whole
    ; request ("Unknown action: error", silent no-audio). Replace each with
    ; a space so the read plays instead of failing. (The daemon's own
    ; sanitize_text would strip them anyway, but it only sees the text
    ; AFTER JSON parsing — this is the parse gate.)
    ;
    ; ONE RegExReplace, not a per-character loop. The previous version walked
    ; every character and rebuilt the whole string for each control char it
    ; found (`text := SubStr(...) . " " . SubStr(...)`), which is O(n x k) in
    ; string copies. Measured with QueryPerformanceCounter, 200 iterations on
    ; a 30k-char selection: 5.4 ms before, 0.3 ms now (about 18x). The larger
    ; reason to prefer this form is correctness-by-construction: it is a
    ; single C-level pass, so it cannot be wrong about which positions it
    ; examined. An earlier strided-probe rewrite of this function MISSED a
    ; control char whenever the length was an exact multiple of the stride —
    ; which would have shipped as a silent no-read (invalid JSON, the daemon
    ; rejects the request, no audio and no visible error). Verified by
    ; exhaustive control-code x offset grid: outputs/logs/rat-stability.
    ;
    ; Order is load-bearing and unchanged: the four StrReplace passes above
    ; have already converted raw newlines/tabs into two-character "\n"/"\t"
    ; sequences, so this class only ever matches genuine leftovers and never
    ; corrupts an escape that was just written.
    return RegExReplace(text, "[\x00-\x1F\x7F]", " ")
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
    global RequestPath, ResponsePath, LastResponse
    speed := Max(0.5, Min(2.0, Round(speed, 2)))
    ; Read current speed from config to avoid accumulating rounding drift.
    current := GetCurrentSpeed()
    newSpeed := Max(0.5, Min(2.0, Round(current * speed, 2)))
    if (newSpeed = current) {
        ; No change — confirm the CURRENT speed rather than going silent:
        ; "did it register?" should always have a visible answer.
        FlashOverlaySpeed(current, true)
        TrayTip SpeedValueLabel(current), "ReadAloudTTS Speed"
        return
    }
    try FileDelete ResponsePath
    req := '{"action":"set_speed","speed":' . newSpeed . '}'
    FileAppend req, RequestPath, "UTF-8-RAW"
    if WaitResponse(3) {
        ; The daemon is the source of truth — read the CONFIRMED speed
        ; from its response, not the value we guessed. ("message" holds
        ; the human label; "speed" is the clamped+rounded value applied.)
        confirmed := newSpeed
        if RegExMatch(LastResponse, '"speed"\s*:\s*([\d.]+)', &m) {
            confirmed := Round(m[1], 2)
        }
        FlashOverlaySpeed(confirmed)
        TrayTip SpeedValueLabel(confirmed), "ReadAloudTTS Speed"
    } else {
        ; Daemon didn't answer: say so — a silent hotkey is the exact
        ; "did that even register?" ambiguity this feedback exists to kill.
        FlashOverlaySpeed(-1)
        TrayTip "Speed not set — daemon unreachable", "ReadAloudTTS Speed", 2
    }
}

SpeedValueLabel(speed) {
    if (speed < 1.0) {
        return Round(1.0 / speed, 1) . "x faster"
    } else if (speed > 1.0) {
        return Round(speed, 1) . "x slower"
    }
    return "normal speed"
}

; Flash the CONFIRMED speed on the reading panel's status line for ~1.5s,
; then restore the normal hint line. The panel is where the user's
; eyes already are during a read, so speed changes confirm themselves
; in-place instead of requiring a glance at the tray.
;   speed >= 0.5  -> "1.1x faster" (amber, the accent = "it registered")
;   speed = -1    -> "speed not set" (the daemon-unreachable failure)
; A pause active when the flash ends restores the PAUSED line, not the
; hint line — SetOverlayStatus(paused) is the single source of truth.
FlashOverlaySpeed(speed, keepOld := false) {
    global HighlightGui, OverlayStatusCtrl, HighlightPaused, gOverlayScale
    if (HighlightGui = "" or OverlayStatusCtrl = "") {
        return
    }
    try {
        s := Round(8 * gOverlayScale)
        OverlayStatusCtrl.SetFont("s" . s . " cFFC400")
        if (speed = -1) {
            OverlayStatusCtrl.Text := "  speed not set — daemon unreachable"
        } else {
            OverlayStatusCtrl.Text := "  " . SpeedValueLabel(speed)
        }
        if (keepOld) {
            ; no-change path: shorter flash, the value didn't move
            SetTimer RestoreOverlayStatus, -900
        } else {
            SetTimer RestoreOverlayStatus, -1500
        }
    }
}

RestoreOverlayStatus() {
    global OverlayStatusCtrl, HighlightPaused
    if (OverlayStatusCtrl = "") {
        return
    }
    ; Reuse the canonical status setter so pause state stays coherent.
    SetOverlayStatus(HighlightPaused)
}

AdjustSpeed(factor) {
    ; factor < 1 = faster (e.g. 0.9 = 10% faster)
    ; factor > 1 = slower (e.g. 1.1 = 10% slower)
    SendSpeed(factor)
}

ResetSpeed() {
    ; Ctrl+0: back to normal speed. Implemented as the exact inverse
    ; nudge (1.0/current) so it rides the SAME confirmed-feedback path
    ; as Ctrl+* and Ctrl+/ — daemon-confirmed flash + tray tip, and the
    ; natural no-op when already at 1.0 (SendSpeed's no-change branch
    ; flashes "normal speed" instead of a silent hotkey). The old
    ; dead-code version wrote a bare request with no visible feedback
    ; and its "already normal" gate read stale config, not the daemon.
    current := GetCurrentSpeed()
    if (current > 0) {
        SendSpeed(1.0 / current)
    }
    InitTray()
}

global gDebugLogWrites := 0
DebugLog(msg) {
    global TempDir, gDebugLogWrites
    logPath := TempDir . "\working_debug.log"
    ; Over-time stability (research 2026-09-08): the log grew ~1.2MB/day in
    ; real use — unbounded FileAppend over a days-long tray session eats
    ; disk and slows tailing. Rotate at 2MB, keeping one .1 generation.
    ; Size check amortized: one stat per 500 writes, not per line.
    gDebugLogWrites += 1
    if (Mod(gDebugLogWrites, 500) = 0) {
        try {
            if FileExist(logPath) {
                sizeMB := FileGetSize(logPath, "K") / 1024.0
                if (sizeMB >= 2.0) {
                    old := logPath . ".1"
                    if FileExist(old) {
                        FileDelete old
                    }
                    FileMove logPath, old, 1
                }
            }
        }
    }
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
    HideReplayBar()   ; a manual stop dismisses the replay offer
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

OpenReadingOverlay(*) {
    ; The daemon's loopback karaoke viewer. Port comes from config.json
    ; (overlay_port, default 8792) so a custom port doesn't strand the
    ; menu item. If the daemon isn't up yet, StartDaemon brings it.
    global ConfigPath, AppDir
    port := 8792
    try {
        content := FileRead(ConfigPath, "UTF-8-RAW")
        if RegExMatch(content, '"overlay_port"\s*:\s*(\d+)', &m) {
            port := Integer(m[1])
        }
    }
    StartDaemon()
    Run "http://127.0.0.1:" . port . "/overlay"
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
    global HighlightGui, HighlightPaused, gSeekInFlight, gLastTickState, gOnStartLogged
    ; Critical 50: serialize this tick against hotkeys/OnMessage for up to
    ; 50ms — the 30ms timer and the WM_LBUTTONDOWN handler both mutate
    ; shared state and AHK preempts a timer thread by default (mid-tick
    ; interruption corrupted pause state; the research packet's root-cause
    ; #2). No Sleep/blocking calls inside this callback.
    Critical 50
    ; --- Hover-pause REMOVED (user directive 2026-09-07: more
    ; frustrating than helpful — a drifting cursor silently stopped reads).
    ; Pause is now Space-only (pointer inside the panel), fully deliberate
    ; and visible: the pause indicator on the panel plus the amber word
    ; staying put make the state obvious. The old movement-gated hover
    ; machine (enter margin, leave debounce, per-tick actuator) is gone.
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
    ; Log state TRANSITIONS only: a per-tick line wrote ~30/s while the
    ; panel sits idle after "done" (21K lines / 20min, live 2026-09-07).
    ; First tick after a fresh timer start logs once via the reset below.
    if (state != gLastTickState) {
        DebugLog "Tick state=" . state . " len=" . StrLen(raw)
        gLastTickState := state
        ; Leaving a "start" hold window re-arms the OnStart detail log
        ; (the daemon holds the start packet 0.4s = ~13 ticks; without
        ; this reset gate each seek flooded 13 identical detail lines).
        if (state != "start") {
            gOnStartLogged := false
        }
    }
    if (state = "start") {
        HighlightOnStart(raw)
    } else if (state = "playing") {
        HighlightOnPlaying(raw)
    } else if (state = "done" or state = "stop") {
        ; While hover-paused, the daemon's stop state is EXPECTED (pause
        ; stops playback) and may be followed by a done state from the
        ; playback worker exiting. Neither may tear the overlay down or
        ; kill this timer — that was the "hovering makes the box
        ; disappear" bug, and the dead timer then stranded
        ; HighlightPaused=true into the NEXT read, whose hover machine
        ; resumed from a stale word index (mid-paragraph restarts).
        ; Same for a seek-in-flight (click-to-rewind / hover-resume /
        ; Space): the stop + the old worker's late "done" land BEFORE the
        ; new speak's "start" — treating them as terminal tears the box
        ; down mid-seek and the in-place resume path never engages.
        if (!HighlightPaused and !gSeekInFlight) {
            HighlightOnStop()
        }
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
    global HighlightGui, HighlightCurrentIdx, HighlightFullText
    global gSeekInFlight, gOverlayDismissed, gOnStartLogged
    ; The new speak's start packet arrived — terminal-state suppression
    ; (gSeekInFlight) is no longer needed; from here the daemon's states
    ; are genuine again.
    gSeekInFlight := false
    ; A fresh read (or replay/seek) re-opens the panel by explicit user
    ; action: any prior Esc-dismissal no longer applies.
    gOverlayDismissed := false
    text := JsonGet(raw, "text")
    totalMs := JsonGet(raw, "total_ms")
    HighlightTotalMs := (totalMs != "") ? Round(totalMs) : 0
    ; Parse words: [["word",start_ms,end_ms],...]
    HighlightWords := ParseWordTimings(raw)
    HighlightPlayStart := A_TickCount
    ; Once per start episode: the daemon holds the "start" packet 0.4s
    ; (~13 ticks) and every seek re-holds it — an unconditional log here
    ; wrote 13 identical lines per seek during live use 2026-09-08.
    if (!gOnStartLogged) {
        gOnStartLogged := true
        DebugLog "OnStart: gui=" . (HighlightGui != "" ? "exists" : "new")
            . " words=" . HighlightWords.Length . " textLen=" . StrLen(text)
            . " sameText=" . (text = HighlightFullText)
    }
    if (HighlightGui != "" and text = HighlightFullText) {
        ; SEEK-RESUME, not a new read: the daemon now sends the FULL text
        ; with zero-timed prefix words on seeks (hover-resume /
        ; click-to-rewind / Space). Rebuilding here was the jumpiness:
        ; the box flickered, lost its dragged position, and reset
        ; selection state. Update words/timers in place — same panel,
        ; same position, highlight just continues from the new packet.
        HighlightCurrentIdx := -1
        return
    }
    ShowHighlightOverlay(text)
}

HighlightOnPlaying(raw) {
    global HighlightWords, HighlightPlayStart, HighlightTotalMs, HighlightGui
    global HighlightCurrentIdx, HighlightPaused, AppDir, gOverlayDismissed
    ; Skip updates while paused (Space-pause).
    if (HighlightPaused) {
        return
    }
    ; the user dismissed the panel mid-read with Esc — the voice keeps
    ; playing (that is the point of Esc), but the tick's missing-Gui
    ; recovery must NOT rebuild it. Without this gate the panel came back
    ; within 30ms of the Esc (caught live 2026-09-07).
    if (gOverlayDismissed) {
        return
    }
    ; On first "playing" state, initialize the overlay from the full payload.
    if (HighlightGui = "") {
        text := JsonGet(raw, "text")
        totalMs := JsonGet(raw, "total_ms")
        HighlightTotalMs := (totalMs != "") ? Round(totalMs) : 0
        HighlightWords := ParseWordTimings(raw)
        ; Fallback: if this bare "playing" packet raced past the daemon's
        ; "start" window (poller never saw text+words), recover the text
        ; from the once-per-speak sidecar the daemon writes for the web
        ; overlay. Without this the box appears EMPTY (no text at all).
        if (text = "") {
            ; Sidecar fallback when a bare "playing" packet raced past the
            ; held "start" window. Path needs the backslash (AppDir carries
            ; no trailing slash — the original AppDir . "tmp\..." resolved
            ; to "...\ReadAloudTTS tmp\..." and FileRead always threw into
            ; the swallow), and the sidecar is JSON — extract the text
            ; value, never display the raw {"text": ...} wrapper.
            try {
                sidecar := FileRead(AppDir . "\tmp\overlay_text.json", "UTF-8")
                sidecarText := JsonGet(sidecar, "text")
                if (sidecarText != "") {
                    text := sidecarText
                }
            }
        }
        ShowHighlightOverlay(text)
    }
    msStr := JsonGet(raw, "ms")
    if (msStr = "") {
        return
    }
    ; The daemon appends word timings per chunk during playback. When the
    ; payload carries a words array larger than what we parsed, refresh —
    ; this lets the overlay highlight ahead into not-yet-played chunks.
    ; NOTE: on seek-resumes the daemon sends the FULL word list (with a
    ; zero-timed prefix) from packet one, so this refresh also picks up
    ; the complete list immediately after a rewind.
    if RegExMatch(raw, '"words"\s*:\s*\[') {
        newWords := ParseWordTimings(raw)
        if (newWords.Length > HighlightWords.Length) {
            HighlightWords := newWords
        }
    }
    elapsed := Round(msStr)
    ; Find the word whose [start_ms, end_ms) contains elapsed.
    idx := FindWordIndex(HighlightWords, elapsed)
    if (idx >= 0 and idx != HighlightCurrentIdx) {
        HighlightCurrentIdx := idx
        SelectOverlayWord(idx)
    }
    DemoTickReport()
}

HighlightOnStop() {
    global HighlightPaused, HighlightCurrentIdx, HighlightFullText
    global gOverlayDismissed
    ; Read ended — the dismiss flag belongs to THIS read only. Clear it so
    ; the next read (or a click on the replay bar) builds its panel fresh.
    gOverlayDismissed := false
    DebugLog "OnStop: paused=" . HighlightPaused . " hadText=" . (HighlightFullText != "")
    StopHighlightTimer()
    ; Finished reads leave a small Replay bar instead of nothing: the last
    ; text stays one click away (replay-from-finished), no re-select needed.
    ; The bar is destroyed by the next ShowHighlightOverlay or ExitFunc.
    if (!HighlightPaused and HighlightFullText != "") {
        ShowReplayBar()
    }
    HideHighlightOverlay()
}

; --- Replay-from-finished bar ---------------------------------------------
; A tiny always-on-top strip (bottom-right): "↻ Replay". Click re-speaks
; the last read from word 0; F6 or a read elsewhere hides it. No daemon
; state is touched until clicked, so it can't interfere with a new read.

ShowReplayBar() {
    global ReplayGui
    HideReplayBar()
    ReplayGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000")
    ReplayGui.BackColor := "181A1E"
    ReplayGui.SetFont("s11 cFFC400", "Segoe UI")
    btn := ReplayGui.Add("Text", "w90 h30 Center BackgroundTrans", "↻ Replay")
    btn.OnEvent("Click", (*) => ReplayLastText())
    ReplayGui.Show("x0 y0 Hide NA")
    ; Position bottom-right after sizing.
    barW := 106, barH := 38
    ReplayGui.Move(A_ScreenWidth - barW - 24, A_ScreenHeight - barH - 24, barW, barH)
    ReplayGui.Show("NA")
    SetTranslucent(ReplayGui.Hwnd, 230)
    ; Auto-fade: the replay offer is a convenience, not a squatter — it
    ; disappears after 8s instead of sitting on the corner all day. A new
    ; read (ShowReplayBar on the next done) re-offers it; clicking works
    ; any time inside the window. A NAMED timer target is load-bearing:
    ; SetTimer replaces a pending named timer, but a fat-arrow creates a
    ; NEW timer object each call — two reads within 8s would stack fades
    ; and the older timer would kill the newer bar early.
    SetTimer ReplayBarFade, -8000
}

ReplayBarFade() {
    HideReplayBar()
    SetTimer ReplayBarFade, 0
}

HideReplayBar() {
    global ReplayGui
    if ReplayGui != "" {
        try ReplayGui.Destroy()
        ReplayGui := ""
    }
}

ReplayLastText() {
    global HighlightFullText, HighlightPaused, HighlightCurrentIdx
    if (HighlightFullText = "") {
        return
    }
    HideReplayBar()
    HighlightPaused := false
    HighlightCurrentIdx := 0
    SeekFromWord(0)
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
    global HighlightGui, HighlightFullText, gLastSelStart, gLastSelEnd, gOverlayDpi
    global HighlightPaused, HighlightCurrentIdx, HighlightLastColored, gOverlayReducedMotion
    DebugLog "ShowHighlightOverlay entry: textLen=" . StrLen(text)
    try {
        ShowHighlightOverlayInner(text)
    } catch as e {
        ; A throw mid-build (the NumPut("Str") / EM_GETLINE-with-NULL class
        ; of bug) used to abort the thread SILENTLY: daemon spoke, tick ran,
        ; no panel, nothing logged. Log every build failure loudly now, and
        ; DESTROY the half-built Gui — HighlightOnPlaying sees gui="" and
        ; retries, so a persistent failure would otherwise leak one Gui
        ; object per tick (693 leaked in the first live catch of this).
        DebugLog "ShowHighlightOverlay FAILED: " . e.Message . " @ " . e.What
        try HighlightGui.Destroy()
        HighlightGui := ""
    }
}

; OverlayLineCount(zoom): visible body lines the panel shows at a given
; zoom. Zoom used to scale font and box in lockstep, freezing the panel at
; 2 lines no matter the zoom — a bigger font bought no more reading context
; (user call 2026-09-10: "no matter how much you zoom"). Now the panel
; HEIGHT outgrows the font: 1.0 stays the validated 2-line baseline; the
; count steps up as the font grows (1.3 → 3 lines, 1.6 → 4, 1.9 → 5) so
; larger type arrives with MORE context, not less. Line-height stays
; ~1.5em and text never clips (WCAG 1.4.4's 200%-no-loss principle).
OverlayLineCount(zoom) {
    if (zoom < 1.15) {
        return 2
    }
    if (zoom < 1.45) {
        return 3
    }
    if (zoom < 1.75) {
        return 4
    }
    return 5
}

; OverlayPanelHeight(lineCount, uiScale): total panel px for a line count.
; Single source of truth used by the build path AND the zoom-resize path
; (two 108*uiScale derivations drifting apart was the bug class to avoid).
; 108 = 2-line baseline (14 top margin + 2x28 text + 14 gap + 18 status +
; 6 pad, at 96dpi); each extra body line adds its 28px pitch (scaled).
OverlayPanelHeight(lineCount, uiScale) {
    return Round((108 + 28 * (lineCount - 2)) * uiScale)
}

ShowHighlightOverlayInner(text) {
    global HighlightGui, HighlightFullText, gLastSelStart, gLastSelEnd, gOverlayDpi
    global HighlightPaused, HighlightCurrentIdx, HighlightLastColored, gOverlayReducedMotion
    global OverlayStatusCtrl, gOverlayDraggedX, gOverlayDraggedY
    HideReplayBar()   ; a new read supersedes the finished-read replay bar
    HideHighlightOverlay()
    HighlightFullText := text
    ; Reset per-read state so a rebuilt overlay starts clean (a stale
    ; gLastSel* would suppress the first highlight; a stale PAUSE flag +
    ; word index from the previous read made the first resume seek into
    ; the new text mid-paragraph — stale HighlightCurrentIdx against
    ; fresh words).
    HighlightPaused := false
    HighlightCurrentIdx := -1
    HighlightLastColored := -1
    gLastSelStart := -1
    gLastSelEnd := -1
    ; +E0x08000000 = WS_EX_NOACTIVATE: window doesn't steal focus when clicked.
    ; Do NOT use +E0x20 (WS_EX_TRANSPARENT) — it makes the window invisible
    ; to mouse events, which would break dragging and click-to-rewind.
    HighlightGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000")
    HighlightGui.BackColor := "181A1E"
    ; DPI-aware geometry: AHK v2 is not per-monitor DPI aware by default;
    ; scale the panel/font from the primary monitor's DPI at build time.
    try DllCall("SetProcessDpiAwarenessContext", "ptr", -4, "int")
    try {
        gOverlayDpi := DllCall("GetDpiForSystem", "uint")
    } catch as e {
        gOverlayDpi := 96
    }
    dpiScale := gOverlayDpi / 96.0
    ; uiScale = DPI * zoom: one composed multiplier drives ALL panel geometry
    ; so a zoom scales the panel as a unit (proportions preserved), not just
    ; the font (which would reflow the same-size box into fewer lines).
    uiScale := dpiScale * gOverlayScale
    gOverlayReducedMotion := !ReducedMotionEnabled()
    ; Typography (Apple HIG x Material 3 contract + ChatGPT teardown):
    ; 16px-class body type (weight 400, generous leading) — the old
    ; s12 panel forced a lean-in to read the highlight (user
    ; complaint). Segoe UI Variable is the Win11 system stack; falls
    ; back to Segoe UI on older builds. Font must be set via
    ; CHARFORMAT so the RichEdit honors it, but SetFont on the Gui first
    ; gives the fallback a baseline.
    fontName := "Segoe UI Variable Text"
    HighlightGui.SetFont("s" . Round(13 * uiScale), fontName)
    ; Near-opaque dark panel (research: composited text on translucent
    ; panels fails WCAG 4.5:1 worst-case; 243/255 ≈ 95% keeps a whisper
    ; of depth while guaranteeing contrast), ~52% of screen width,
    ; bottom-center. Taller than the old 80px strip: two lines of 16px
    ; text plus comfortable padding (Gemini/ChatGPT composer feel).
    screenWidth := A_ScreenWidth
    ; Width scales with zoom too (a bigger font in the same-width box reads
    ; as a reflow, not a zoom) but clamps just short of fullscreen so a
    ; 2x panel never runs off the right edge.
    panelWidth := Round(screenWidth * 0.52 * uiScale)
    maxW := screenWidth - Round(16 * uiScale)
    if (panelWidth > maxW) {
        panelWidth := maxW
    }
    panelHeight := OverlayPanelHeight(OverlayLineCount(gOverlayScale), uiScale)
    panelX := Round((screenWidth - panelWidth) / 2)
    panelY := A_ScreenHeight - panelHeight - Round(56 * dpiScale)
    ; Dragged-position memory: if the user moved the panel, rebuilds
    ; (every new read) reuse the LAST dragged spot instead of snapping
    ; back to bottom-center — position amnesia was the top drag complaint.
    ; Session-only (a global): restart resets to default. Keep the panel
    ; on-screen — a resolution change between drags could strand it.
    if (gOverlayDraggedX >= 0 and gOverlayDraggedY >= 0) {
        if (gOverlayDraggedX + panelWidth <= screenWidth and gOverlayDraggedY + panelHeight <= A_ScreenHeight and gOverlayDraggedX >= 0 and gOverlayDraggedY >= 0) {
            panelX := gOverlayDraggedX
            panelY := gOverlayDraggedY
        }
    }
    HighlightGui.MarginX := Round(18 * uiScale)
    HighlightGui.MarginY := Round(14 * uiScale)
    ; RichEdit50W styles (verified vs MS SDK richedit.h + AHK shipping
    ; examples): WS_CHILD|WS_VISIBLE (0x50000000) + ES_MULTILINE|ES_READONLY
    ; (0x804). Deliberately NO ES_NOHIDESEL: on this never-focused panel
    ; the selection stays INVISIBLE by default — the visible signal is
    ; the amber word color, not a selection block. ES_SAVESEL not needed
    ; (we never rely on selection persistence).
    reStyle := "ClassRichEdit50W +0x50000804 -Tabstop -VScroll w" . (panelWidth - Round(36 * uiScale)) . " h" . (panelHeight - Round(52 * uiScale))
    reCtrl := HighlightGui.AddCustom(reStyle)
    DebugLog "  AddCustom ok hwnd=" . reCtrl.Hwnd
    ; Status line under the text: pause state must be VISIBLE (the old
    ; hover-pause failed partly because pause was imperceptible). Default
    ; copy teaches the Space control; on pause it flips to amber
    ; "⏸ paused — Space resumes". Tiny, dim gray #9A9AA5 on the panel
    ; BackColor — never competes with the amber word.
    OverlayStatusCtrl := HighlightGui.Add("Text", "x" . Round(18 * uiScale) . " w" . (panelWidth - Round(36 * uiScale)) . " h" . Round(18 * uiScale) . " c9A9AA5 BackgroundTrans", "  Space pauses · click a word to jump · Ctrl+wheel zooms")
    HighlightGui.SetFont("s" . Round(8 * uiScale), "Segoe UI Variable Text")
    OverlayStatusCtrl.SetFont("s" . Round(8 * uiScale) . " c9A9AA5")
    ; Corner grip glyph (bottom-right): the drag-to-scale affordance. A
    ; triangle glyph (U+25E2) reads as "pull corner to grow" — the standard
    ; resize affordance — and stays dim so it never competes with the amber
    ; word. The grip ZONE (hit test in OverlayDragHandler) is the bottom-
    ; right ~20x20 area of the panel, not this control's exact pixels: the
    ; glyph is the visual hint, the zone is the functional surface.
    try {
        OverlayGripCtrl := HighlightGui.Add("Text", "x" . (panelWidth - Round(30 * uiScale)) . " y" . (panelHeight - Round(28 * uiScale)) . " w" . Round(16 * uiScale) . " h" . Round(16 * uiScale) . " c9AA0AB BackgroundTrans Right", "◢")
        OverlayGripCtrl.SetFont("s" . Round(9 * uiScale), "Segoe UI")
    } catch as e {
        DebugLog "  grip glyph failed: " . e.Message
        OverlayGripCtrl := ""
    }
    ; --- RichEdit init (research gotchas, in order) ---
    reHwnd := reCtrl.Hwnd
    ; EM_SETUNDOLIMIT 0: per-word recoloring creates undo records at 3+/s
    ; — pollute nothing on a display-only surface.
    SendMessage(0x0452, 0, 0, reHwnd)
    ; EM_SETBKGNDCOLOR: opaque dark core matching the glass tint
    ; rgb(24,26,30) = #181A1E -> BGR 0x1E1A18 (RichEdit backgrounds are
    ; always opaque; the layered alpha frost shows on the panel chrome).
    SendMessage(0x0443, 0, 0x1E1A18, reHwnd)
    ; EM_EXLIMITTEXT: RichEdit's default ~32K would truncate long reads.
    SendMessage(0x0435, 0, 0x7FFFFFFE, reHwnd)
    ; EM_SETTEXTEX (0x0461): SETTEXTEX{flags=ST_DEFAULT, codepage=1200
    ; (Unicode)}; returns 1 on success. Research: the documented RichEdit
    ; path with a real success return (unlike WM_SETTEXT).
    stx := Buffer(8, 0)
    NumPut("UInt", 0, stx, 0)
    NumPut("UInt", 1200, stx, 4)
    setTextRet := SendMessage(0x0461, stx.Ptr, StrPtr(text), reHwnd)
    DebugLog "  EM_SETTEXTEX ret=" . setTextRet
    ; Set font + size + base color document-wide: EM_SETCHARFORMAT
    ; SCF_ALL (0x4 — NOT 0x8, which is SCF_USEUIRULES) with CFM_FACE|
    ; CFM_SIZE|CFM_COLOR. yHeight is TWIPS (points x 20): 16pt-class
    ; readable body = 320 twips (scaled by DPI at build time).
    cf := MakeCharFormat(0x20000000 | 0x80000000 | 0x40000000, 0, Round(320 * uiScale), 0xF7F5F5, fontName)
    docFmtRet := SendMessage(0x0444, 4, cf.Ptr, reHwnd)
    DebugLog "  EM_SETCHARFORMAT(SCF_ALL) ret=" . docFmtRet
    ; Word-wrap is ON by default in RichEdit — no EM_FMTLINES call needed
    ; (and NEVER send 0x00C4: that is EM_GETLINE, whose lParam must be a
    ; buffer with capacity in the first word — lParam=0 makes the control
    ; write to NULL and AHK throws OSError, silently killing every live
    ; panel build; caught live 2026-09-07 after 693 failed rebuilds).
    HighlightGui.Show("x" . panelX . " y" . panelY . " w" . panelWidth . " h" . panelHeight . " NA")
    DebugLog "  Gui.Show ok x=" . panelX . " y=" . panelY . " w=" . panelWidth . " h=" . panelHeight . " hwnd=" . HighlightGui.Hwnd
    ; Line-fit self-correction: the height formula assumed pitch scales
    ; linearly with zoom; RichEdit rounds line boxes UP, so at 3+ lines
    ; the built panel can come up a few px short (probe: 46px pitch at
    ; 1.6x vs 44.8 formula → 4th line clipped). Measure real pitch and
    ; grow — must run AFTER Show (client geometry is real only then).
    try EnsureOverlayLineFit(reHwnd, OverlayLineCount(gOverlayScale))
    ; Windows 11 polish via DWM (research packet area 3). All wrapped in
    ; try — attribute 33/38 need Win11; failure falls back to square/dark.
    hwnd := HighlightGui.Hwnd
    ; DWMWA_WINDOW_CORNER_PREFERENCE (33) = DWMWCP_ROUND (2): the
    ; card/dialog radius — 12px-class per the design contract.
    try DllCall("dwmapi\DwmSetWindowAttribute", "ptr", hwnd, "uint", 33, "int*", 2, "uint", 4)
    ; DWMWA_USE_IMMERSIVE_DARK_MODE (20): dark chrome if a border ever shows.
    try DllCall("dwmapi\DwmSetWindowAttribute", "ptr", hwnd, "uint", 20, "int*", 1, "uint", 4)
    ; When the user has reduced motion on, force-disable DWM window
    ; transitions for this surface (contract #18).
    if (gOverlayReducedMotion) {
        try DllCall("dwmapi\DwmSetWindowAttribute", "ptr", hwnd, "uint", 3, "int*", 1, "uint", 4)
    }
    ; Opacity 230/255: frosted glass matching the web overlay's
    ; rgba(24,26,30,0.72) surface (243 was near-opaque). Contrast is
    ; carried by the opaque RichEdit core; on the chrome the dimmest
    ; token 9A9AA5 measures 5.02:1 worst-case (over white at 230).
    SetTranslucent(hwnd, 230)
    DebugLog "  ShowHighlightOverlay BUILD COMPLETE"
    ; Demo child: report the build the moment it lands. A bare "start"
    ; packet carries no ms field, so the tick returns before its report
    ; hook — without this the director never sees the rebuilt panel.
    if (gDemoChild) {
        DemoTickReport()
    }
}

; Build a zero-initialized CHARFORMAT2W (116 bytes, pack(4) — offsets
; verified against MS SDK richedit.h). Zero-init is mandatory: a garbage
; dwMask silently misformats (documented SO failure mode).
MakeCharFormat(mask, effects, yHeightTwips, bgrColor, faceName) {
    cf := Buffer(116, 0)
    NumPut("UInt", 116, cf, 0)          ; cbSize
    NumPut("UInt", mask, cf, 4)          ; dwMask
    NumPut("UInt", effects, cf, 8)       ; dwEffects (0 for explicit color!)
    NumPut("Int", yHeightTwips, cf, 12)  ; yHeight (twips)
    NumPut("UInt", bgrColor, cf, 20)    ; crTextColor (0x00BBGGRR)
    ; szFaceName[32] at byte 26. AHK v2 has NO string NumPut — NumPut("Str",...)
    ; throws "Invalid parameter(s)" at runtime (the /Validate harness does not
    ; catch it, and in the production script the try-less throw aborts the
    ; auto-execute thread, silently killing the overlay build). The correct
    ; idiom is StrPut against a raw pointer: StrPut(str, buf.Ptr + offset).
    StrPut(faceName, cf.Ptr + 26, "UTF-16")
    return cf
}

; Pause the current read. Space is the ONLY pause trigger now — the
; movement-gated hover machine was removed 2026-09-07 (user: more
; frustrating than helpful; a drifting cursor silently stopped reads).
OverlayHoverPause(*) {
    global HighlightPaused, HighlightGui
    if (HighlightGui = "" or HighlightPaused) {
        return
    }
    HighlightPaused := true
    SetOverlayStatus(true)
    ; Stop the daemon playback (resume re-sends text from the current word).
    StopSpeechDaemon()
}

; Pause-state status line on the panel. The pause is Space-only now, so
; the state must be readable at a glance: paused = amber "⏸ paused" with
; the resume hint; playing = the dim control hint. Reduced motion does
; not gate this (it's an instant text swap, no animation).
SetOverlayStatus(paused) {
    global OverlayStatusCtrl, HighlightGui, gOverlayScale
    if (HighlightGui = "" or OverlayStatusCtrl = "") {
        return
    }
    try {
        s := Round(8 * gOverlayScale)
        if (paused) {
            OverlayStatusCtrl.SetFont("s" . s . " cFFC400")
            OverlayStatusCtrl.Text := "  ⏸ paused — Space resumes"
        } else {
            OverlayStatusCtrl.SetFont("s" . s . " c9A9AA5")
            OverlayStatusCtrl.Text := "  Space pauses · click a word to jump · Ctrl+wheel zooms"
        }
    }
}

; Space-key play/pause (the ONLY pause path — hover-pause removed
; 2026-09-07, user directive). The overlay is WS_EX_NOACTIVATE, so the
; real focus stays wherever it was — meaning a blind "no overlay →
; passthrough / overlay → toggle" split is WRONG: with the overlay up
; during a long read, Space typed into ANY other window (chat, editor,
; browser) would toggle playback instead of typing a space. Gate the
; toggle on the overlay itself being the active context: either the
; pointer is inside the panel, or the focused window IS the panel
; (covers the tray/other activation paths). Every other context passes
; Space through untouched.
OverlaySpaceKey() {
    global HighlightGui, HighlightPaused
    if (HighlightGui != "" and (IsMouseOverOverlay() or WinActive("ahk_id " . HighlightGui.Hwnd))) {
        if (!HighlightPaused) {
            OverlayHoverPause()
        } else {
            OverlayMouseLeaveResume()
        }
        return
    }
    Send "{Space}"
}

; Esc on a visible panel = dismiss the panel, keep the voice. The tick
; keeps polling highlight_state, so dismissing here must also stop the
; terminal-state machinery from resurrecting the panel on the NEXT read's
; start — no: a new read rebuilds the panel by design (fresh text), and
; a mid-read Esc keeps the amber state coherent by simply hiding the
; window until the read ends (HideHighlightOverlay destroys it; the tick
; skips recolor for gui="" and the next read rebuilds clean).
OverlayEscKey() {
    global HighlightGui, HighlightPaused, gOverlayDismissed
    if (HighlightGui != "") {
        ; A paused read dismissed with Esc: clear the pause flag so the
        ; next Space (no panel visible) can't resume a ghost panel —
        ; OverlayMouseLeaveResume already guards this, but the flag must
        ; not survive into the next read's rebuild state either way.
        HighlightPaused := false
        gOverlayDismissed := true
        HideReplayBar()
        HideHighlightOverlay()
        DebugLog "Esc dismissed overlay"
        return
    }
    Send "{Esc}"
}

OverlayMouseLeaveResume(*) {
    global HighlightPaused, HighlightCurrentIdx, HighlightFullText, HighlightGui
    if (!HighlightPaused) {
        return
    }
    ; If the overlay was torn down while paused (genuine stop path), the
    ; pause state belongs to a DEAD panel — resuming would speak with no
    ; visible box. Only resume a pause that still has a live overlay to
    ; attach to; otherwise just clear the flag (next read starts fresh).
    if (HighlightGui = "") {
        HighlightPaused := false
        return
    }
    HighlightPaused := false
    SetOverlayStatus(false)
    ; Guard against a stale index: HighlightCurrentIdx was reset to -1 on
    ; the previous read's teardown, and ShowHighlightOverlay resets it for
    ; every new read. Only resume when we actually have a word position
    ; AND text to resume from — otherwise the seek would slice a fresh
    ; paragraph mid-sentence (the "starts mid paragraph" bug).
    if (HighlightCurrentIdx >= 0 and HighlightFullText != "") {
        SeekFromWord(HighlightCurrentIdx)
    }
}

OverlayClickHandler(wParam, lParam, msg, hwnd) {
    global HighlightGui, HighlightWords
    if (HighlightGui = "") {
        return
    }
    ; Click-to-rewind on the RichEdit surface. EM_CHARFROMPOS on RichEdit
    ; is 0x427 (WM_USER+39) — NOT the classic Edit's 0xD7 — and takes a
    ; pointer to a POINT struct, returning the flat char index (not the
    ; packed low-word of the classic Edit).
    ctrlHwnd := 0
    try {
        ctrl := HighlightGui["RichEdit50W1"]
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
    pt := Buffer(8, 0)
    NumPut("Int", px, pt, 0)
    NumPut("Int", py, pt, 4)
    charIdx := SendMessage(0x0427, 0, pt.Ptr, ctrlHwnd)
    ; RichEdit returns the flat char index (up to 0x7FFFFFFE — no low-word
    ; mask, unlike the classic Edit's packed return).
    ; Find which word this char belongs to.
    idx := FindNearestWordByChar(HighlightWords, charIdx)
    DebugLog "Click: px=" . px . " py=" . py . " charIdx=" . charIdx . " wordIdx=" . idx
    if (idx >= 0) {
        SeekFromWord(idx)
    }
    ; Defense-in-depth against selection residue: even with the swallow
    ; below keeping the native click handler from planting one, a selection
    ; that exists from any other path must not survive to be painted blue
    ; by a stray focus event. Collapse to a zero-width caret at the clicked
    ; char — same EM_EXSETSEL idiom as ColorWordRange's post-format
    ; collapse. Max(0,…) guards the -1 EM_CHARFROMPOS failure code; the
    ; control clamps positions beyond EOT itself.
    cr := Buffer(8, 0)
    NumPut("Int", Max(0, charIdx), cr, 0)
    NumPut("Int", Max(0, charIdx), cr, 4)
    SendMessage(0x0437, 0, cr.Ptr, ctrlHwnd)
    ; SWALLOW the click: returning an integer keeps WM_LBUTTONDOWN away
    ; from the RichEdit's own window proc. Its native handler is what
    ; planted an insertion-point selection and could SetFocus the child —
    ; and a focused RichEdit paints its selection block, the "blue select
    ; mode" seen while click-storming the vanishing panel (live
    ; 2026-09-07). The seek above is the click's only effect on this
    ; display-only surface. Early returns above stay blank so clicks on
    ; every other control process normally; panel-margin drag is bound to
    ; the PANEL hwnd, unaffected by this.
    return 1
}

; Second click of a system double-click on the RichEdit arrives as
; WM_LBUTTONDBLCLK (0x203) INSTEAD of a second WM_LBUTTONDOWN — the
; seek handler above never sees it. Swallow it too: the RichEdit's
; native double-click WORD-SELECT was the other half of the blue-select
; report, and a same-spot re-click inside the 200ms seek debounce
; carries no new intent anyway (SeekFromWord already drops repeats).
OverlayDoubleClickSuppress(wParam, lParam, msg, hwnd) {
    global HighlightGui
    if (HighlightGui = "") {
        return
    }
    ctrlHwnd := 0
    try {
        ctrl := HighlightGui["RichEdit50W1"]
        if IsObject(ctrl) {
            ctrlHwnd := ctrl.Hwnd
        }
    } catch {
        return
    }
    if (ctrlHwnd = 0 or hwnd != ctrlHwnd) {
        return
    }
    return 1   ; swallow — no native word-select on this surface
}

; Right-click (and Shift+F10 / the Menu key) land as WM_CONTEXTMENU
; (0x7B) on the RichEdit, whose native menu offers Select All — the one
; verb that turns the whole display surface into the blue block the
; user reported. This panel is display-only: it has no edit verbs,
; so the menu carries no value here. Swallow it entirely.
OverlayContextMenuSuppress(wParam, lParam, msg, hwnd) {
    global HighlightGui
    if (HighlightGui = "") {
        return
    }
    ctrlHwnd := 0
    try {
        ctrl := HighlightGui["RichEdit50W1"]
        if IsObject(ctrl) {
            ctrlHwnd := ctrl.Hwnd
        }
    } catch {
        return
    }
    if (ctrlHwnd = 0 or hwnd != ctrlHwnd) {
        return
    }
    return 1   ; swallow — no context menu, no Select All, on this surface
}

; ONE handler for WM_MOUSEWHEEL, dispatching on the Ctrl modifier.
; AHK v2 OnMessage keeps a single handler per message id, so registering the
; zoom handler and the scroll-override handler separately would silently
; replace one with the other. The dispatcher is the only correct shape.
OverlayWheelDispatch(wParam, lParam, msg, hwnd) {
    ; Ctrl held -> zoom (and swallow, so RichEdit's built-in Ctrl+wheel zoom
    ; does not double-apply).
    if ((wParam & 0xFFFF & 0x0008) != 0) {
        return OverlayWheelZoomHandler(wParam, lParam, msg, hwnd)
    }
    ; Bare wheel -> engage the scroll override, then LET THE MESSAGE THROUGH
    ; (returning nothing) so the control scrolls natively.
    OverlayWheelScrollOverride(wParam, lParam, hwnd)
    return
}

; Ctrl+wheel over the overlay = zoom (user feature 2026-09-08). Win10/11
; posts WM_MOUSEWHEEL directly to the window under the cursor (Raymond Chen
; 2016-04-20), so this fires while another app holds focus — but that also
; means the RichEdit child receives wheel messages natively, and RichEdit 4.1
; zooms on Ctrl+wheel BUILT-IN. The integer return at the bottom suppresses
; ALL further processing of the message (AHK v2 OnMessage semantics), which
; is what stops the native double-zoom. Bare wheel (no Ctrl) stays native.
OverlayWheelZoomHandler(wParam, lParam, msg, hwnd) {
    global HighlightGui, gOverlayScale, gDragScaling
    if (HighlightGui = "") {
        return
    }
    ; Don't fight an in-progress grip drag (its math anchors to the scale
    ; at press; a mid-drag wheel step would snap back on the next tick).
    if (gDragScaling) {
        return 1
    }
    ; Ctrl gate — MK_CONTROL = 0x0008 in wParam's low word.
    if ((wParam & 0xFFFF & 0x0008) = 0) {
        return
    }
    ; Accept the wheel anywhere over the panel: the OS posts to whichever
    ; child is under the cursor (panel, RichEdit, status line, grip glyph),
    ; so walk up the parent chain instead of enumerating children. The
    ; transcript window (a sibling Gui) fails the walk and stays native.
    walk := hwnd
    found := false
    loop 8 {
        if (walk = HighlightGui.Hwnd) {
            found := true
            break
        }
        parent := DllCall("GetParent", "ptr", walk, "ptr")
        if (parent = 0) {
            break
        }
        walk := parent
    }
    if (!found) {
        return
    }
    ; Wheel delta: SIGNED short in wParam's HIGH word (negative = toward
    ; user = zoom out). Mask-then-sign — a raw >>16 reads AHK's unsigned
    ; wParam and turns every zoom-out into a huge positive.
    d := (wParam >> 16) & 0xFFFF
    if (d >= 0x8000) {
        d -= 0x10000
    }
    ; One notch (±120) = ±10%; high-resolution deltas scale with |delta|,
    ; capped at 3 notches so a single flick can't jump the clamp range.
    steps := d / 120.0
    if (steps > 3) {
        steps := 3
    }
    if (steps < -3) {
        steps := -3
    }
    ApplyOverlayScale(gOverlayScale * (1.1 ** steps))
    return 1   ; integer return: suppress native scroll AND native Ctrl+wheel zoom
}

; --- bare-wheel scroll override (user feature 2026-09-12) -------------------
;
; THE PROBLEM. The overlay auto-re-anchors the spoken line every time the word
; advances. Previously a bare wheel did nothing useful on this surface (every
; classic scroll route is dead on a readonly, no-WS_VSCROLL RichEdit — see
; ScrollWordIntoView), and the OS posts WM_MOUSEWHEEL to the window under the
; cursor, so a wheel flick over the panel reached a control that could not
; scroll. Either way: NO way to glance back at a previous sentence, because
; the next word advance would immediately pull the view back.
;
; THE FIX (WCAG 2.2.2 Pause/Stop/Hide — the auto-scroll starts on its own,
; lasts >5s, and runs in parallel with other content, so a pause MECHANISM is
; required; the scroll IS that mechanism, and it must be discoverable and its
; state perceivable). Three states:
;
;   FOLLOWING  — normal: the spoken line is re-anchored as it advances.
;   USER_OWNED — the user wheeled; auto-re-anchoring is SUSPENDED so they can
;                read. The highlight keeps updating (that is data, not scroll).
;   RETURNING  — one instant jump back to the spoken line, then FOLLOWING.
;
; WHY NO ANIMATION: EM_SETSCROLLPOS snaps to whole lines by design ("the
; control checks the x and y coordinates and adjusts them... so that a
; complete line is displayed at the top"). A 200-300ms animated return would
; be 3-6 discrete line jumps in a 2-5 line window — visibly worse than one
; jump, and worse for vestibular sensitivity. Instant is both better-looking
; and the safer choice.
;
; RESUME CONDITION: the user gets HOLD_MS (4s) to read, OR the jump-back
; happens early if the spoken word's line scrolls back into view. No product
; publishes a resume timeout — the industry convention is distance-gated
; (MUI X chat: a 150px buffer; Twitch: resume on reaching bottom). A pure
; distance gate is ambiguous in a 2-line window, so this is a hybrid: time as
; the floor, distance as the earlier exit.
OverlayWheelScrollOverride(wParam, lParam, hwnd) {
    global HighlightGui, gScrollOverrideUntil, gScrollOverrideActive
    global gLastAutoTopLine, gOverlayAutoScrollEnabled

    ; Ctrl is the zoom gesture — leave it entirely to the zoom handler.
    if ((wParam & 0xFFFF & 0x0008) != 0) {
        return false
    }
    ; User can disable the whole follow behaviour.
    if (!gOverlayAutoScrollEnabled) {
        return false
    }
    ; Only for the overlay panel (same parent-chain walk as the zoom handler,
    ; because the OS posts to whichever child is under the cursor).
    walk := hwnd
    found := false
    loop 8 {
        if (HighlightGui != "" and walk = HighlightGui.Hwnd) {
            found := true
            break
        }
        parent := DllCall("GetParent", "ptr", walk, "ptr")
        if (parent = 0) {
            break
        }
        walk := parent
    }
    if (!found) {
        return false
    }

    gScrollOverrideActive := true
    gScrollOverrideUntil := A_TickCount + 4000
    ; Perceivable state, not a silent flag (WCAG 2.2.2). Shown in the status
    ; line so the panel does not change size and the reader is told why the
    ; text stopped following.
    OverlayScrollHint("↕ reading back — resumes in 4s")
    DebugLog "wheel: scroll override engaged for 4000ms"
    ; CRITICAL: return false, NOT 1. An integer return SUPPRESSES the message,
    ; so the wheel never reaches the control and the panel would appear frozen
    ; — the exact opposite of the feature. Letting it through means RichEdit
    ; does whatever scrolling it can natively.
    return false
}

; Called from the highlight tick when a new word is colored. Returns true if
; the caller should re-anchor the view, false while the user owns it.
OverlayMayAutoScroll() {
    global gScrollOverrideActive, gScrollOverrideUntil, gScrollOverrideHint
    if (!gScrollOverrideActive) {
        return true
    }
    ; The user's window expired — hand control back.
    if (A_TickCount >= gScrollOverrideUntil) {
        gScrollOverrideActive := false
        OverlayScrollHint("")
        DebugLog "wheel: scroll override expired, resuming follow"
        return true
    }
    return false
}

; Perceivable state cue (WCAG 2.2.2 requires the paused state be knowable, not
; a silent flag). Shown in the existing status line so no layout changes.
OverlayScrollHint(text) {
    global OverlayStatusCtrl, HighlightPaused, gOverlayScale
    if (HighlightPaused) {
        return   ; the pause indicator owns the status line while paused
    }
    if (!IsObject(OverlayStatusCtrl)) {
        return
    }
    try {
        if (text = "") {
            OverlayStatusCtrl.SetFont("s" . Round(10 * gOverlayScale) . " c9A9AA5")
            OverlayStatusCtrl.Text := "Space pauses"
        } else {
            OverlayStatusCtrl.SetFont("s" . Round(10 * gOverlayScale) . " cFFC400")
            OverlayStatusCtrl.Text := text
        }
    } catch {
        ; status line gone (panel rebuilding) — the hint is not critical
    }
}

OverlayDragHandler(wParam, lParam, msg, hwnd) {
    ; Manual drag for the NOACTIVATE overlay: left button on the PANEL
    ; (not the Edit) starts a Windows-standard drag. The Edit consumes
    ; its own clicks (click-to-rewind); the panel margins are the grab
    ; zone. Uses the mouse-move stream already driven by the tick.
    global HighlightGui, gDragOffX, gDragOffY, gDragStartX, gDragStartY, gDragMoved
    global gOverlayDpi, gOverlayScale, gDragScaling, gDragScaleStart
    global gDragScaleStartX, gDragScaleStartY, gDragScaleMoved, OverlayGripCtrl
    ; The grip GLYPH is its own child window — a press on it arrives with
    ; the glyph's hwnd, not the panel's. Accept both (the glyph sits
    ; entirely inside the grip zone, so the zone math is identical); the
    ; status line's far-right sliver that overlaps the zone stays a no-op
    ; (pressing text-status pixels doing nothing is the safe default).
    gripHwnd := 0
    if (IsObject(OverlayGripCtrl)) {
        try gripHwnd := OverlayGripCtrl.Hwnd
    }
    if (HighlightGui = "" or (hwnd != HighlightGui.Hwnd and hwnd != gripHwnd)) {
        return
    }
    ; Suppress the micro-drag glitch: a press on the panel edge that
    ; drifts 0-2px and releases should NOT reposition the panel
    ; (rounded-corner dead pixels + hand jitter made single clicks nudge
    ; the box). DragTrackOverlay applies a 4px dead zone before the
    ; first Move; the offset is captured here.
    ;
    ; Press point from lParam (client coords of the receiving hwnd), NOT
    ; MouseGetPos: the physical cursor can move between the click and this
    ; handler running, and lParam IS the click. For a press on the grip
    ; GLYPH (its own child hwnd), translate glyph-client -> panel-client
    ; via the two origins.
    WinGetPos &wx, &wy, &ww, &wh, "ahk_id " . HighlightGui.Hwnd
    px := lParam & 0xFFFF
    py := (lParam >> 16) & 0xFFFF
    if (gripHwnd != 0 and hwnd = gripHwnd) {
        WinGetPos &ggx, &ggy,,, "ahk_id " . gripHwnd
        px += ggx - wx
        py += ggy - wy
    }
    ; GRIP ZONE branch: a press in the bottom-right corner (the ◢ glyph
    ; is the visual hint) starts a SCALE drag, not a move drag. Zone =
    ; 24x24 at panel scale measured from the panel's bottom-right corner
    ; in panel-client coords. The same 4px dead zone + release-persist
    ; model as the move drag.
    dpiScale2 := gOverlayDpi / 96.0
    uiScale2 := dpiScale2 * gOverlayScale
    gz := Round(24 * uiScale2)
    if (px >= ww - gz and px <= ww and py >= wh - gz and py <= wh) {
        gDragScaling := true
        gDragScaleStart := gOverlayScale
        gDragScaleStartX := wx + px
        gDragScaleStartY := wy + py
        gDragScaleMoved := false
        DebugLog "ScaleDrag armed at " . px . "," . py . " (panel-client)"
        SetTimer DragScaleTrackOverlay, 16
        return 1   ; consume: not a move-drag press
    }
    gDragOffX := px
    gDragOffY := py
    gDragStartX := wx + px
    gDragStartY := wy + py
    gDragMoved := false
    ; Track until release without stealing focus: poll in a tight loop is
    ; bad (blocks the tick); instead install a temporary timer.
    SetTimer DragTrackOverlay, 16
}

DragTrackOverlay() {
    global HighlightGui, gDragOffX, gDragOffY, gDragMoved, gDragStartX, gDragStartY
    global gOverlayDraggedX, gOverlayDraggedY
    if (HighlightGui = "") {
        SetTimer DragTrackOverlay, 0
        gDragMoved := false
        return
    }
    if !GetKeyState("LButton", "P") {
        SetTimer DragTrackOverlay, 0
        ; Persist the dragged position for the next rebuild: a drag is an
        ; explicit placement — later reads must NOT snap back to default.
        ; Try-armored: WinGetPos on a dying window throws, and a throw in
        ; a timer callback pops the same modal error dialog that kills
        ; every hotkey (see ReadSelection note 2026-09-07).
        if (gDragMoved) {
            try {
                WinGetPos &px, &py,,, "ahk_id " . HighlightGui.Hwnd
                gOverlayDraggedX := px
                gOverlayDraggedY := py
                DebugLog "Drag saved pos=" . px . "," . py
            }
        }
        gDragMoved := false
        return
    }
    CoordMode "Mouse", "Screen"
    MouseGetPos &mx, &my
    ; 4px dead zone before the first Move: a press that hasn't actually
    ; travelled is a click, not a drag — hand jitter on the panel edge
    ; was nudging the box on single clicks (the micro-position glitch).
    if (!gDragMoved) {
        if (Abs(mx - gDragStartX) < 4 and Abs(my - gDragStartY) < 4) {
            return
        }
        gDragMoved := true
    }
    HighlightGui.Move(mx - gDragOffX, my - gDragOffY)
}

; Scale-drag tracker (grip corner): until left-button release, map pointer
; travel to a scale delta. dx+dy diagonal travel = grow (pulling the corner
; outward/down), reverse = shrink — both axes summed so the grip works at
; any grab angle. Sensitivity: 480px of diagonal travel = 2x panel (1px ≈
; 0.21% scale) — deliberate, not twitchy. Dead zone mirrors the move drag
; (a press that hasn't travelled is a click, not a scale). Keep the panel's
; TOP-LEFT anchored while scaling so the grip corner tracks the pointer —
; the natural corner-pull mental model.
DragScaleTrackOverlay() {
    global HighlightGui, gDragScaling, gDragScaleStart, gDragScaleMoved
    global gDragScaleStartX, gDragScaleStartY, gOverlayScale
    if (!gDragScaling) {
        SetTimer DragScaleTrackOverlay, 0
        return
    }
    if (HighlightGui = "") {
        gDragScaling := false
        SetTimer DragScaleTrackOverlay, 0
        return
    }
    if !GetKeyState("LButton", "P") {
        SetTimer DragScaleTrackOverlay, 0
        gDragScaling := false
        DebugLog "ScaleDrag end scale=" . Format("{:.2f}", gOverlayScale)
        return
    }
    CoordMode "Mouse", "Screen"
    MouseGetPos &mx, &my
    if (!gDragScaleMoved) {
        if (Abs(mx - gDragScaleStartX) < 4 and Abs(my - gDragScaleStartY) < 4) {
            return
        }
        gDragScaleMoved := true
    }
    travel := (mx - gDragScaleStartX) + (my - gDragScaleStartY)
    ApplyOverlayScale(gDragScaleStart * (1.0 + travel / 480.0))
}

; Live-apply a new overlay scale (wheel step or grip drag). This is the
; heavy path: a full EM_SETCHARFORMAT SCF_ALL re-wrap at the new font size
; plus Gui.Move — NOT EM_SETZOOM (the pure view transform works on RichEdit
; 4.1 but zooms only the text inside a fixed box; the panel would stay the
; same size, which reads as a reflow, not the "panel grows as a unit" zoom
; the user asked for). Steps, in order, all armored — a throw inside a
; 16ms timer callback kills every hotkey (the modal-dialog lesson):
;  1. clamp + no-op check
;  2. Gui.Move to the new panel box, top-left anchored, clamped on-screen
;  3. RichEdit child Move to the new inner box
;  4. SCF_ALL re-wrap at the new font twips (resets per-char formats to
;     base — the amber word repaints base gray, so it MUST be re-colored)
;  5. invalidate the change-gate (gLastSel*) so the next tick re-colors
;     instead of no-oping on "unchanged" — plus re-color + re-anchor NOW,
;     not at the next word: a mid-pause zoom would otherwise leave the
;     amber word gray until the next word boundary (could be minutes).
;  6. re-anchor the amber word into view (the re-wrap invalidated the old
;     scroll position: EM_SETSCROLLPOS semantics are "self-clamping", not
;     "survives reflow") + re-apply status font.
ApplyOverlayScale(newScale) {
    global HighlightGui, gOverlayScale, gOverlayDpi, OverlayStatusCtrl, OverlayGripCtrl
    global HighlightWords, HighlightCurrentIdx, HighlightLastColored
    global gLastSelStart, gLastSelEnd, gCfAmber, gCfBase, HighlightPaused
    if (HighlightGui = "") {
        return
    }
    if (newScale > 2.0) {
        newScale := 2.0
    }
    if (newScale < 0.75) {
        newScale := 0.75
    }
    if (Abs(newScale - gOverlayScale) < 0.005) {
        return
    }
    gOverlayScale := newScale
    uiScale := (gOverlayDpi / 96.0) * gOverlayScale
    screenWidth := A_ScreenWidth
    panelWidth := Round(screenWidth * 0.52 * uiScale)
    maxW := screenWidth - Round(16 * uiScale)
    if (panelWidth > maxW) {
        panelWidth := maxW
    }
    panelHeight := OverlayPanelHeight(OverlayLineCount(gOverlayScale), uiScale)
    try {
        ; Top-left anchored: the grip corner tracks the pointer; for wheel
        ; zoom keep the top-left too (the status line stays under the same
        ; reading spot). Clamp on-screen (a 2x panel near the right edge
        ; would otherwise run off).
        WinGetPos &px, &py,,, "ahk_id " . HighlightGui.Hwnd
        if (px + panelWidth > screenWidth) {
            px := screenWidth - panelWidth
        }
        if (px < 0) {
            px := 0
        }
        if (py + panelHeight > A_ScreenHeight) {
            py := A_ScreenHeight - panelHeight
        }
        if (py < 0) {
            py := 0
        }
        HighlightGui.Move(px, py, panelWidth, panelHeight)
        ; RichEdit child: same inset math as the build (x/y = Gui margins
        ; 18/14, w = panel minus 36, h = panel minus 52). x/y MUST be re-
        ; derived, not kept: a kept x drifts the left inset as the margin
        ; rescales, and a kept y lets the taller text box run over the
        ; status line below it.
        try {
            reCtrl := HighlightGui["RichEdit50W1"]
            reCtrl.Move(Round(18 * uiScale), Round(14 * uiScale), panelWidth - Round(36 * uiScale), panelHeight - Round(52 * uiScale))
        } catch {
        }
        ; Status line + grip glyph ride along — status y re-derived to match
        ; the build's auto-layout exactly: MarginY(14) + RichEdit h(H-52) +
        ; gap(14) = H-24 from the panel top (52 decomposes 14+14+18+6).
        if (IsObject(OverlayStatusCtrl)) {
            try OverlayStatusCtrl.Move(Round(18 * uiScale), panelHeight - Round(24 * uiScale), panelWidth - Round(36 * uiScale), Round(18 * uiScale))
        }
        if (IsObject(OverlayGripCtrl)) {
            try OverlayGripCtrl.Move(panelWidth - Round(30 * uiScale), panelHeight - Round(28 * uiScale))
        }
        ; SCF_ALL re-wrap at the new size. This resets every char's format
        ; to base — the live amber word repaints gray until re-colored
        ; below. CFM_FACE|CFM_SIZE|CFM_COLOR so face + size + color all
        ; re-bind (size is the whole point).
        reHwnd := 0
        try reHwnd := HighlightGui["RichEdit50W1"].Hwnd
        if (reHwnd != 0) {
            cf := MakeCharFormat(0x20000000 | 0x80000000 | 0x40000000, 0, Round(320 * uiScale), 0xF7F5F5, "Segoe UI Variable Text")
            SendMessage(0x0444, 4, cf.Ptr, reHwnd)
            ; Re-color the current amber word NOW (don't wait for the next
            ; word boundary — under pause that's minutes away) and re-anchor
            ; it into the band. Also reset the change-gate so the tick's
            ; SelectOverlayWord doesn't no-op on "same word": a no-op would
            ; skip both the recolor and the ScrollWordIntoView re-anchor.
            idx := HighlightCurrentIdx
            if (idx >= 0 and idx <= HighlightWords.Length - 1) {
                wordInfo := HighlightWords[idx + 1]
                if (wordInfo[4] >= 0 and wordInfo[5] >= 0) {
                    EnsureCharFormatPair("Segoe UI Variable Text")
                    ColorWordRange(reHwnd, wordInfo[4], wordInfo[5], gCfAmber)
                }
                gLastSelStart := -1
                gLastSelEnd := -1
                ScrollWordIntoView(reHwnd, wordInfo[4])
            } else {
                gLastSelStart := -1
                gLastSelEnd := -1
            }
            ; Line pitch depends on font size: ScrollWordIntoView measures
            ; it live every call, so the re-anchor above already used the
            ; post-reflow pitch. No cached pitch to fix.
            ; Zoom line-fit self-correction: reflow changes the real pitch
            ; — measure and grow if the intended line count no longer fits
            ; (same rounding guard as the build path).
            EnsureOverlayLineFit(reHwnd, OverlayLineCount(gOverlayScale))
        }
        SetOverlayStatus(HighlightPaused)
    } catch as e {
        DebugLog "ApplyOverlayScale failed: " . e.Message . " @ " . e.What
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

; Click-to-seek must resolve EVERY click on the text surface, not just
; clicks that happen to land on letter pixels: EM_CHARFROMPOS happily
; returns the index of a SPACE between words (a click at a word gap
; returned charIdx=30 with wordIdx=-1 in live testing — "click a word,
; nothing happens"). Snap outward from the clicked char to the nearest
; word, LEFT-preferred on ties: reading flows left-to-right, so a click
; in the gap after a word most naturally means that word. Scan window
; is 10 chars — covers inter-word spaces and sentence gaps; a click
; far off any word (panel padding) still no-ops via -1.
FindNearestWordByChar(words, charIdx) {
    exact := FindWordByChar(words, charIdx)
    if (exact >= 0) {
        return exact
    }
    offset := 1
    while (offset <= 10) {
        left := FindWordByChar(words, charIdx - offset)
        if (left >= 0) {
            return left
        }
        right := FindWordByChar(words, charIdx + offset)
        if (right >= 0) {
            return right
        }
        offset++
    }
    return -1
}

SeekFromWord(idx) {
    global HighlightFullText, RequestPath, ResponsePath, HighlightPath
    global HighlightCurrentIdx, gSeekInFlight, gLastSeekTick
    global HighlightPaused
    ; Click-storm debounce, BEFORE anything else: investigating the
    ; vanish, the user click-stormed — the old code ran one FULL
    ; seek request per click, each re-slicing the remainder and racing
    ; the next. A repeat inside 200ms of the last accepted click is a
    ; storm artifact, not new intent; drop it before touching state.
    now := A_TickCount
    if (now - gLastSeekTick < 200) {
        DebugLog "Seek: debounced (idx=" . idx . " " . (now - gLastSeekTick) . "ms since last)"
        return
    }
    gLastSeekTick := now
    ; ARM FIRST, act second. The old body armed gSeekInFlight only AFTER
    ; the daemon stop request and the file writes — but the daemon answers
    ; a stop in single-digit ms while the 30ms tick was free to read that
    ; {"state":"stop"} (plus the old playback worker's late "done") with
    ; the flag still false → HighlightOnStop tore the panel down mid-
    ; click. That ordering race WAS the "dialogue disappears when I
    ; click a word" bug (live 2026-09-07). Nothing below may yield the
    ; thread before this line: a preempting tick must see the flag set.
    gSeekInFlight := true
    ; Backstop for every "start never comes" path — dead daemon, lost
    ; request, synth error before chunk 0 — so terminal-state suppression
    ; cannot stay armed forever. A NAMED one-shot (SetTimer replaces the
    ; pending one, same load-bearing reason as ReplayBarFade); a healthy
    ; seek clears the flag long before this fires and the watchdog no-ops.
    SetTimer SeekWatchdog, -6000
    HighlightCurrentIdx := idx
    ; Click while paused = resume from the clicked word. Leaving the
    ; pause flag set made the tick ignore the seek's packets: amber word
    ; frozen, status line still saying "paused" while audio played.
    if (HighlightPaused) {
        HighlightPaused := false
        SetOverlayStatus(false)
    }
    ; NO stop request. The daemon's run_speak_async takeover already
    ; signals + cancels + joins the old playback worker the moment our
    ; speak request arrives; a separate stop only injected {"state":"stop"}
    ; mid-seek (the vanish trigger) and its WaitResponse(3) could block
    ; the click handler for up to 3 seconds. The daemon's generation
    ; counter (this same pass) keeps the superseded worker from writing a
    ; late {"state":"done"} on top of the new read.
    try FileDelete RequestPath
    try FileDelete ResponsePath
    try FileDelete HighlightPath
    jsonText := JsonEscape(HighlightFullText)
    req := '{"action":"speak","text":"' . jsonText . '","from_word":' . idx . '}'
    FileAppend req, RequestPath, "UTF-8-RAW"
    ; Terminal states stay suppressed until the new "start" packet lands
    ; (HighlightOnStart clears the flag). "playing" states during the
    ; takeover window are harmless: the panel keeps its position and the
    ; amber word jumps to the clicked word when "start" arrives.
    ; Restart the highlight timer (only when the overlay is enabled).
    if ShowOverlayEnabled() {
        StartHighlightTimer()
    }
}

; Safety net for every path where a seek's "start" packet never arrives
; (daemon died, request lost, synth failed before chunk 0): 6s after
; arming, if the flag is STILL set, release terminal-state suppression so
; a later genuine done/stop tears the panel down normally instead of
; the panel living forever in seek mode. A healthy seek clears the flag
; within ~2s (synth of chunk 0) and this no-ops.
SeekWatchdog() {
    global gSeekInFlight
    if (gSeekInFlight) {
        DebugLog "SeekWatchdog: start packet never arrived — releasing seek suppression"
        gSeekInFlight := false
    }
    SetTimer SeekWatchdog, 0
}

StopSpeechDaemon() {
    global RequestPath, ResponsePath
    if IsDaemonReady() {
        try FileDelete ResponsePath
        try FileDelete RequestPath
        FileAppend '{"action":"stop"}', RequestPath, "UTF-8-RAW"
        WaitResponse(3)
    }
}

; --- Per-word karaoke coloring (RichEdit) ----------------------------------
; The visible reading signal is a calm amber word — NOT a selection block.
; Flow per tick when the word changes: recolor PREVIOUS word back to the
; base color, color CURRENT word amber, EM_SCROLLCARET (0xB7) to follow.
; Research gotchas honored: alternate two DIFFERENT structs (resending
; byte-identical params TOGGLES the effect), EM_EXSETSEL wParam MUST be 0,
; dwEffects stays 0 (CFE_AUTOCOLOR would override the explicit color),
; never SCF_ALL per tick.
global gCfAmber := ""
global gCfBase := ""

EnsureCharFormatPair(faceName) {
    global gCfAmber, gCfBase, gOverlayDpi
    dpiScale := gOverlayDpi / 96.0
    if (gCfAmber = "") {
        ; Amber #FFC400 -> BGR 0x00C4FF (matches the web overlay's
        ; highlight_color and the tray accent). CFM_COLOR only —
        ; recoloring must not touch size/face metrics (no reflow).
        gCfAmber := MakeCharFormat(0x40000000, 0, Round(320 * dpiScale), 0x00C4FF, faceName)
        ; Base text #F5F5F7 -> BGR 0xF7F5F5 (the BGR triple must be
        ; fully spelled out: 0xF7F5 would be #F5F700 yellow).
        gCfBase := MakeCharFormat(0x40000000, 0, Round(320 * dpiScale), 0xF7F5F5, faceName)
    }
}

ColorWordRange(reHwnd, charStart, charEnd, cf) {
    ; EM_EXSETSEL (0x437): wParam MUST be 0, lParam = CHARRANGE.
    cr := Buffer(8, 0)
    NumPut("Int", charStart, cr, 0)
    NumPut("Int", charEnd, cr, 4)
    SendMessage(0x0437, 0, cr.Ptr, reHwnd)
    ; EM_SETCHARFORMAT (0x444) SCF_SELECTION (1). Returns nonzero on
    ; success — 0 means the format silently failed (validate!).
    ok := SendMessage(0x0444, 1, cf.Ptr, reHwnd)
    ; Collapse the selection the formatting just used: it otherwise sits
    ; on the word for the whole read, and ANY focus event on the child
    ; would paint it as a blue block (the residue under the blue-select
    ; bug). The caret stays at the range end, so EM_SCROLLCARET still
    ; tracks the same point it always did.
    NumPut("Int", charEnd, cr, 0)
    SendMessage(0x0437, 0, cr.Ptr, reHwnd)
    return ok
}

SelectOverlayWord(idx) {
    global HighlightGui, HighlightWords, gLastSelStart, gLastSelEnd
    global HighlightLastColored, gOverlayDpi
    if HighlightGui = "" {
        return
    }
    ; Calculate character offset of word idx in the full text.
    wordInfo := HighlightWords[idx + 1]  ; AHK arrays are 1-based
    if !IsObject(wordInfo) {
        return
    }
    charStart := wordInfo[4]
    charEnd := wordInfo[5]
    if (charStart < 0 or charEnd < 0) {
        return
    }
    ; CHANGE-GATED: recolor only when the word actually changes — a
    ; per-tick EM_SETCHARFORMAT storm starves WM_PAINT (the original
    ; flicker root cause), and the same params RESENT would TOGGLE the
    ; color off (documented RichEdit behavior).
    if (charStart = gLastSelStart and charEnd = gLastSelEnd) {
        return
    }
    gLastSelStart := charStart
    gLastSelEnd := charEnd
    EnsureCharFormatPair("Segoe UI Variable Text")
    try {
        reCtrl := HighlightGui["RichEdit50W1"]
        reHwnd := reCtrl.Hwnd
    } catch {
        return
    }
    ; Recolor the PREVIOUS word back to base first.
    if (HighlightLastColored >= 0 and HighlightLastColored <= HighlightWords.Length - 1) {
        prev := HighlightWords[HighlightLastColored + 1]
        if (prev[4] >= 0 and prev[5] >= 0) {
            ColorWordRange(reHwnd, prev[4], prev[5], gCfBase)
        }
    }
    ; Color the current word amber + page it into view. EM_SCROLLCARET
    ; (0xB7) is a NO-OP on this control: the panel's RichEdit is readonly
    ; AND windowless-scrollbar-style (no WS_VSCROLL), and probe5 proved
    ; every classic scroll route refuses it (SCROLLCARET ret=0, LINESCROLL
    ; and WM_VSCROLL leave FIRSTVISIBLE pinned at 0) — the "dialogue frozen
    ; after the first two sentences" bug: the amber word marched through
    ; the whole read while the visible 2-line window never scrolled.
    ; EM_SETSCROLLPOS (0x4DE — RichEdit-only, undocumented in MSDN) is the
    ; one route that works READONLY (probe5: ret=1, FV tracks, clamps).
    ; PAGE-FLIP semantics, not pixel-follow: compute the amber word's
    ; display line via EM_LINEFROMCHAR, page when it leaves the viewport
    ; so its line becomes the TOP visible line. Page-flip at reading
    ; pace avoids any jitter/oscillation from continuous micro-scrolls.
    ColorWordRange(reHwnd, charStart, charEnd, gCfAmber)
    ; The highlight always updates — it is data, not scroll. Only the VIEW
    ; re-anchoring yields to the user while they read back.
    if (OverlayMayAutoScroll()) {
        ScrollWordIntoView(reHwnd, charStart)
    }
    HighlightLastColored := idx
}

ScrollWordIntoView(reHwnd, charIdx) {
    ; Visible-band math from three EM_POSFROMCHAR probes (0x426):
    ; line pitch 28px at the 2-line baseline, and the return packs
    ; x=LOWORD/y=HIWORD — a signed read (pos >> 16) keeps negative y
    ; (word above the viewport) intact. Anchoring matured 2026-09-10:
    ; the original page-flip pinned the amber word's line as the TOP
    ; line, which on a 2-line panel teleported the next word from the
    ; bottom row to the top row every line advance (the just-read line
    ; vanished instantly — user: "jumps the next word to the first
    ; line"). Now the amber line anchors MID-PANEL (row ceil(N/2) of N
    ; visible lines): at the 2-line default that is still row 1
    ; (identical to the validated baseline), but at 3+ lines the active
    ; line sits mid-window with unread text visible BELOW it — each
    ; advance slides one row instead of page-flipping, and the just-read
    ; line stays on screen (teleprompter research: upcoming-text preview
    ; below the active line is the natural reading-aid model).
    pos := SendMessage(0x0426, charIdx, 0, reHwnd)
    y := pos >> 16
    ; Line pitch measured live (view-relative y is scroll-invariant):
    ; doc line 1 minus doc line 0. Single-line docs keep the probe default.
    pitch := 28
    if (SendMessage(0x00BA, 0, 0, reHwnd) > 1) {
        c0 := SendMessage(0x00BB, 0, 0, reHwnd)
        c1 := SendMessage(0x00BB, 1, 0, reHwnd)
        pitch := (SendMessage(0x0426, c1, 0, reHwnd) >> 16) - (SendMessage(0x0426, c0, 0, reHwnd) >> 16)
        if (pitch <= 0) {
            pitch := 28
        }
    }
    rc := Buffer(16, 0)
    DllCall("GetClientRect", "ptr", reHwnd, "ptr", rc)
    clientH := NumGet(rc, 12, "Int")
    ; Mid-panel anchor row: the amber line lands at row ceil(N/2)
    ; (1-based). Row 1 at N=2 — the 2-line default behaves exactly as
    ; shipped; rows 2/2/3 at N=3/4/5. visLines is measured LIVE from
    ; client/pitch (the formula's line count is the design intent; the
    ; control's actual pixel fit is the truth).
    visLines := Max(1, Round(clientH / pitch))
    anchorRow := (visLines + 1) // 2
    topLine := Max(0, SendMessage(0x00C9, charIdx, 0, reHwnd) - (anchorRow - 1))
    ; Fixed-anchor model: fire when the amber line drifts BELOW the
    ; anchor row (y >= anchorRow*pitch → client row anchorRow+1) or
    ; leaves the top (y<0, e.g. a backward seek). The amber word then
    ; re-anchors AT the anchor row — its client position never changes
    ; mid-read, the text slides one row underneath per line advance
    ; (teleprompter model; the old bottom-edge y+pitch>clientH trigger
    ; let the amber wander to the panel floor first, then teleport it
    ; back to the top — the "jump" the user called out). RichEdit
    ; snaps EM_SETSCROLLPOS targets to whole lines, so line-grain is the
    ; native grain — no sub-line smooth scroll exists to add.
    if (y < 0 or y >= anchorRow * pitch) {
        pt := Buffer(8, 0)
        NumPut("Int", 0, pt, 0)
        NumPut("Int", topLine * pitch, pt, 4)
        SendMessage(0x04DE, 0, pt.Ptr, reHwnd)
    }
    ; Words already inside the band: no scroll — mid-line words never
    ; micro-move. The 0.4s hold packet at read start also doesn't scroll:
    ; char 0 is always visible.
}

; EnsureOverlayLineFit: grow the panel until `intendedLines` COMPLETE line
; boxes fit the RichEdit client. The height formula assumes line pitch
; scales exactly linearly with zoom (28px * uiScale), but RichEdit rounds
; line boxes UP (probe 2026-09-10: 46px measured at 1.6x zoom vs 44.8
; formula) — the formula-built panel came up ~5px short at 4 lines and the
; bottom line clipped. Measure the REAL pitch post-build and grow by the
; shortfall; self-correcting against any font-metric, DPI, or rounding
; drift. Bottom edge stays on-screen: the top edge moves up (the clamp),
; so status line + grip keep their screen position.
EnsureOverlayLineFit(reHwnd, intendedLines) {
    global HighlightGui, OverlayStatusCtrl, OverlayGripCtrl, gOverlayDpi, gOverlayScale
    if (HighlightGui = "" or intendedLines < 1) {
        return
    }
    uiScale := (gOverlayDpi / 96.0) * gOverlayScale
    ; Pitch measured exactly as ScrollWordIntoView measures it (lines 0/1,
    ; view-relative y — scroll-invariant). Single-line docs keep 28.
    pitch := 28
    if (SendMessage(0x00BA, 0, 0, reHwnd) > 1) {
        c0 := SendMessage(0x00BB, 0, 0, reHwnd)
        c1 := SendMessage(0x00BB, 1, 0, reHwnd)
        pitch := (SendMessage(0x0426, c1, 0, reHwnd) >> 16) - (SendMessage(0x0426, c0, 0, reHwnd) >> 16)
        if (pitch <= 0) {
            pitch := 28
        }
    }
    rc := Buffer(16, 0)
    DllCall("GetClientRect", "ptr", reHwnd, "ptr", rc)
    clientH := NumGet(rc, 12, "Int")
    ; +2px: guard the last line box against client-edge off-by-ones.
    needed := intendedLines * pitch + 2
    if (clientH >= needed) {
        return
    }
    ; LOOP the growth: a single Move does not always settle client
    ; geometry (probe 2026-09-10: one 31px grow left clientH=155 for
    ; needed=186 — the child Move lands on a later message-pump pass).
    ; Iterate measure→grow up to 4 rounds until the intended lines fit.
    try {
        reCtrl := HighlightGui["RichEdit50W1"]
        loop 4 {
            if (clientH >= needed) {
                break
            }
            shortfall := needed - clientH
            WinGetPos &px, &py, &pw, &ph, "ahk_id " . HighlightGui.Hwnd
            newH := ph + shortfall
            if (py + newH > A_ScreenHeight) {
                py := A_ScreenHeight - newH
            }
            if (py < 0) {
                py := 0
            }
            HighlightGui.Move(px, py, pw, newH)
            ; Same inset math as build/zoom paths; the RichEdit grows by
            ; the full shortfall, status + grip re-derive from the height.
            reCtrl.Move(Round(18 * uiScale), Round(14 * uiScale), pw - Round(36 * uiScale), (ph - Round(52 * uiScale)) + shortfall)
            if (IsObject(OverlayStatusCtrl)) {
                try OverlayStatusCtrl.Move(Round(18 * uiScale), newH - Round(24 * uiScale), pw - Round(36 * uiScale), Round(18 * uiScale))
            }
            if (IsObject(OverlayGripCtrl)) {
                try OverlayGripCtrl.Move(pw - Round(30 * uiScale), newH - Round(28 * uiScale))
            }
            DllCall("GetClientRect", "ptr", reHwnd, "ptr", rc)
            clientH := NumGet(rc, 12, "Int")
            DebugLog "  EnsureOverlayLineFit: grew " . shortfall . "px (clientH=" . clientH . " needed=" . needed . " pitch=" . pitch . ")"
        }
    } catch as e {
        DebugLog "  EnsureOverlayLineFit failed: " . e.Message
    }
}

HideHighlightOverlay() {
    global HighlightGui, HighlightPaused, HighlightCurrentIdx, OverlayGripCtrl
    if HighlightGui != "" {
        try HighlightGui.Destroy()
        HighlightGui := ""
        ; Null the per-build control refs with the Gui they belong to —
        ; a stale Gui object reference survives Destroy (AHK v2 destroys
        ; the windows but the OBJECT lives while any variable holds it).
        OverlayGripCtrl := ""
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

; Reduced-motion detection (design contract #18): read the system
; "animate controls" preference once per overlay build. When disabled,
; every fade/animation becomes an instant snap — respect the user's OS
; setting instead of forcing motion on them.
ReducedMotionEnabled() {
    ; SPI_GETCLIENTAREAANIMATION = 0x1042. pvParam receives a BOOL.
    val := 0
    try {
        buf := Buffer(4, 0)
        if DllCall("SystemParametersInfo", "uint", 0x1042, "uint", 0, "ptr", buf, "uint", 0) {
            val := NumGet(buf, 0, "uint")
        }
    }
    ; Returns TRUE (1) when client-area animation is ENABLED.
    return (val != 0)
}

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
    TranscriptGui.BackColor := "181A1E"
    TranscriptGui.SetFont("s12", "Segoe UI")
    ; Scrollable read-only Edit control (+VScroll shows the scrollbar so
    ; long transcripts can be navigated; previously -VScroll hid it).
    TranscriptGui.MarginX := 12
    TranscriptGui.MarginY := 12
    editCtrl := TranscriptGui.Add("Edit", "w600 h400 +ReadOnly +VScroll +Wrap cF5F5F7 Background181A1E", text)
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
    ; String values: locate the opening quote, then WALK to the closing
    ; quote honoring backslash escapes — no regex capture for the body.
    ; The old '"((?:[^"\\]|\\.)*)"' pattern recursed PCRE once per
    ; character: a long read's highlight JSON (11K+ chars live 2026-09-09)
    ; blew the recursion limit and threw PCRE error -21 as a modal
    ; (user screenshot 07:00). A bounded InStr/SubStr walk is linear
    ; and cannot recurse. Escape semantics preserved: \" never closes the
    ; string, \\ is a literal backslash (so \\" is backslash + CLOSE).
    if !RegExMatch(json, pat, &mKey) {
        return ""
    }
    open := mKey.Pos + mKey.Len
    if (SubStr(json, open, 1) != '"') {
        return ""
    }
    i := open + 1
    len := StrLen(json)
    while (i <= len) {
        c := SubStr(json, i, 1)
        if (c = "\") {
            i += 2   ; skip the escaped pair — an escaped quote can't close
        } else if (c = '"') {
            break    ; real closing quote
        } else {
            i++
        }
    }
    if (i > len) {
        return ""   ; unterminated — treat as absent
    }
    return JsonUnescape(SubStr(json, open + 1, i - open - 1))
}

JsonUnescape(s) {
    ; Decode the JSON escapes the daemon emits (json.dumps output):
    ; \" \\ \/ \n \r \t \b \f and \uXXXX. Order matters — resolve
    ; backslash-pairs LAST so "\\" (an escaped backslash) is not mistaken
    ; for an escape prefix. Single scan, no double-unescaping.
    if !InStr(s, "\") {
        return s
    }
    out := ""
    i := 1
    len := StrLen(s)
    while (i <= len) {
        ch := SubStr(s, i, 1)
        if (ch != "\") {
            out .= ch
            i++
            continue
        }
        next := SubStr(s, i + 1, 1)
        switch next {
            case '"':  out .= '"',    i += 2
            case "\":  out .= "\",    i += 2
            case "/":  out .= "/",    i += 2
            case "n":  out .= "`n",   i += 2
            case "r":  out .= "`r",   i += 2
            case "t":  out .= "`t",   i += 2
            case "b":  out .= " ",    i += 2
            case "f":  out .= " ",    i += 2
            ; NOTE: no OTB brace after the case label — "case x: {" on one
            ; line is a v2 syntax error ("Unexpected {"); the block brace
            ; must open on its own line.
            case "u":
            {
                hex := SubStr(s, i + 2, 4)
                code := RegExMatch(hex, "^[0-9a-fA-F]{4}$") ? Integer("0x" . hex) : 0x20
                ; Surrogate pair: high half U+D800-DBFF followed by \uDC00-DFFF.
                ; The pair is 12 characters (\uXXXX\uXXXX) — consume all of
                ; them or trailing hex digits leak into the text.
                if (code >= 0xD800 and code <= 0xDBFF and SubStr(s, i + 6, 2) = "\u") {
                    lowHex := SubStr(s, i + 8, 4)
                    if RegExMatch(lowHex, "^[0-9a-fA-F]{4}$") {
                        low := Integer("0x" . lowHex)
                        if (low >= 0xDC00 and low <= 0xDFFF) {
                            code := 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                            out .= Chr(code)
                            i += 12
                            continue
                        }
                    }
                }
                out .= Chr(code)
                i += 6
            }
            default:  out .= next, i += 2  ; unknown escape: keep next char
        }
    }
    return out
}

ParseWordTimings(json) {
    ; Extract the "words" array and build per-word info with char offsets.
    ; Each entry: ["word", start_ms, end_ms]. We also compute char offsets
    ; by scanning the full text for each word sequentially.
    global HighlightWords
    result := []
    text := JsonGet(json, "text")
    ; Find each word tuple by WALKING the JSON array — the old regex
    ; '\["((?:[^"\\]|\\.)*)",...' recursed PCRE per character and threw
    ; -21 on long reads (same failure class as JsonGet's string pattern).
    ; Walk: find "[" + quote, walk the literal to its closing quote
    ; (escape-aware), then expect ,number ,number ] right after.
    pos := 1
    charSearchPos := 1
    len := StrLen(json)
    while (pos <= len) {
        openBracket := InStr(json, '["', false, pos)
        if (openBracket = 0) {
            break
        }
        litStart := openBracket + 2
        i := litStart
        while (i <= len) {
            c := SubStr(json, i, 1)
            if (c = "\") {
                i += 2
            } else if (c = '"') {
                break
            } else {
                i++
            }
        }
        if (i > len) {
            break   ; unterminated literal — stop scanning
        }
        ; After the closing quote: ,<num>,<num>] = a word tuple.
        if RegExMatch(json, ',\s*([\d.]+)\s*,\s*([\d.]+)\s*\]', &mNum, i + 1)
                and mNum.Pos = i + 1 {
            word := JsonUnescape(SubStr(json, litStart, i - litStart))
            startMs := Round(mNum[1])
            endMs := Round(mNum[2])
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
            pos := mNum.Pos + mNum.Len
        } else {
            ; Not a word tuple (e.g. a nested array of strings) — move
            ; past this opening bracket and keep scanning.
            pos := openBracket + 1
        }
    }
    return result
}

FindWordIndex(words, elapsedMs) {
    ; words is 1-based AHK array of [word, startMs, endMs, charStart, charEnd].
    ; Return 0-based index of the word whose [startMs, endMs) contains elapsedMs.
    ; BINARY SEARCH: the old linear scan was O(N) per 30ms tick; with
    ; full-context seek packets (whole-document word lists) that became
    ; a per-tick sweep of hundreds of words — wasted work in the very
    ; tick that must stay lean (WM_PAINT starvation root cause).
    n := words.Length
    if (n = 0) {
        return -1
    }
    ; Zero-timed seek prefix (daemon pads already-spoken words with
    ; [0,0]) — skip them so a resume highlights the CURRENT word, not
    ; the first prefix word that matches elapsed < endMs.
    lo := 1
    while (lo <= n and words[lo][2] = 0 and words[lo][3] = 0) {
        lo++
    }
    if (lo > n) {
        return n - 1
    }
    ; Binary search for the last word with startMs <= elapsedMs.
    hi := n
    while (lo < hi) {
        mid := (lo + hi + 1) // 2
        if (words[mid][2] <= elapsedMs) {
            lo := mid
        } else {
            hi := mid - 1
        }
    }
    return lo - 1
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
    HideReplayBar()
    HideHighlightOverlay()
    CloseTranscript()
    if !gDemoChild {
        StopDaemon()
    }
    ExitApp
}

; ---------------------------------------------------------------------------
; Demo child mode (src/demo_director.py)
; ---------------------------------------------------------------------------
; The demo studio launches THIS script with --demo-child in an isolated
; sandbox dir. The director then plays daemon: it writes the real wire
; files (tmp\highlight_state.json packets, tmp\request.json for set_speed
; seeks... wait — the director never writes request.json; it answers the
; panel's own request.json writes by dropping response.json) and the
; panel's genuine tick/pause/seek/speed/zoom code paths run untouched.
;
; Directives (tmp\demo_directives.json, polled 60ms):
;   {"action":"exit"}                        — clean shutdown
;   {"action":"exit_now"}                    — immediate ExitApp (kill path)
;   {"action":"scale","scale":1.6}           — set panel zoom
;   {"action":"pause"} / {"action":"resume"} — Space-equivalent pause
;   {"action":"flash_speed","speed":0.8}     — drive FlashOverlaySpeed
;   {"action":"speed_set","speed":0.8}       — full SendSpeed path
;                                              (request.json -> confirmed)
;   {"action":"panel_info"}                  — report panel.json snapshot
;
; The reporter (tmp\panel.json, written on every panel state change) is
; the director's eyes: panel rect, hwnd, RichEdit metrics, scroll state,
; current word, pause/scale state. Without it the director is blind.
; ---------------------------------------------------------------------------

DemoChildRun() {
    global AppDir, TempDir, gDemoParentPid, ShowOverlayEnabled
    dir := TempDir . "\demo"
    DirCreate dir
    ; Exit fast when the parent (Python director) dies mid-run — orphaned
    ; demo children would linger as invisible GUI processes. Poll every
    ; second; parent death is a hard stop, not a graceful one.
    if (gDemoParentPid > 0) {
        SetTimer DemoParentWatchdog, 2000
    }
    SetTimer DemoDirectivePoll, 30
    ; The demo child has no tray and no Home hotkey — the director IS the
    ; read trigger. The panel lane always runs with the overlay on: the
    ; 30ms highlight tick polls tmp\highlight_state.json for the mock
    ; daemon's packets, exactly like a live read.
    StartHighlightTimer()
    DemoChildReport("child_started")
}

DemoParentWatchdog() {
    global gDemoParentPid
    if !ProcessExist(gDemoParentPid) {
        ExitApp
    }
}

DemoDirectivePoll() {
    global TempDir
    static lastRaw := ""
    path := TempDir . "\demo_directives.json"
    raw := ""
    try raw := FileRead(path, "UTF-8-RAW")
    if (raw = "") {
        return
    }
    raw := Trim(raw)
    if (raw = lastRaw) {
        return
    }
    lastRaw := raw
    DemoHandleDirective(raw)
}

DemoHandleDirective(raw) {
    global TempDir, HighlightPaused, gOverlayScale
    ; One directive per file write; JSON body via the shared JsonGet.
    action := JsonGet(raw, "action")
    switch action {
        case "exit":
            ExitApp
        case "exit_now":
            ExitApp
        case "scale":
            s := JsonGet(raw, "scale")
            if (s != "") {
                ApplyOverlayScale(Float(s))
            }
            DemoChildReport("scale")
        case "pause":
            if (HighlightGui != "" and !HighlightPaused) {
                OverlayHoverPause()
            }
            DemoChildReport("pause")
        case "resume":
            if (HighlightPaused) {
                OverlayMouseLeaveResume()
            }
            DemoChildReport("resume")
        case "flash_speed":
            sp := JsonGet(raw, "speed")
            if (sp != "") {
                FlashOverlaySpeed(Float(sp))
            }
            DemoChildReport("flash_speed")
        case "speed_set":
            ; Full SendSpeed path: writes request.json like the real
            ; hotkey, waits for the director's response.json, reads the
            ; CONFIRMED speed from it — the genuine confirmed-feedback
            ; loop, exercised end to end on camera.
            sp := JsonGet(raw, "speed")
            if (sp != "") {
                SendSpeed(Float(sp))
            }
            DemoChildReport("speed_set")
        case "panel_info":
            DemoChildReport("panel_info")
        case "replay":
            ; The replay-bar click path: SeekFromWord restarts the
            ; highlight timer (HighlightOnStop stopped it on done) and
            ; arms gSeekInFlight — the genuine replay gesture without
            ; touching request.json or audio.
            SeekFromWord(0)
            DemoChildReport("replay")
        default:
            ; Unknown directive: ignore silently (the director may write
            ; a directive the child predates).
    }
}

; Demo child reporter: panel geometry + RichEdit metrics + playback state,
; as JSON. Written on every directive and on every tick word change —
; the director waits on these values between camera moves. Written
; atomically enough for a poller: single FileAppend to a fresh file
; (delete-then-append, the same idiom the request/response files use).
DemoChildReport(reason) {
    global HighlightGui, HighlightWords, HighlightCurrentIdx, HighlightPaused
    global gOverlayScale, gOverlayDpi, gLastSelStart, gLastSelEnd, TempDir
    path := TempDir . "\panel.json"
    try FileDelete path
    hwnd := (HighlightGui != "") ? HighlightGui.Hwnd : 0
    x := 0, y := 0, w := 0, h := 0
    if (hwnd) {
        WinGetPos &x, &y, &w, &h, "ahk_id " . hwnd
    }
    reH := 0
    reW := 0
    charIdx := -1
    visLineCount := 0
    firstVisLine := -1
    if (hwnd) {
        try {
            reCtrl := HighlightGui["RichEdit50W1"]
            ControlGetPos &cx, &cy, &cw, &ch, , "ahk_id " . reCtrl.Hwnd
            reW := cw
            reH := ch
            ; First visible line + total client lines via EM_GETFIRSTVISIBLELINE
            ; and EM_POSFROMCHAR on the line after the last — the director
            ; reads scroll state from this without touching the control.
            firstVisLine := SendMessage(0x00CE, 0, 0, reCtrl.Hwnd)  ; EM_GETFIRSTVISIBLELINE
            charIdx := HighlightWords.Length > 0 && HighlightCurrentIdx >= 0
                ? HighlightWords[HighlightCurrentIdx + 1][4] : -1
            ; visible line count = EM_GETLINECOUNT while the control wraps
            total := SendMessage(0x00BA, 0, 0, reCtrl.Hwnd)  ; EM_GETLINECOUNT
            visLineCount := total
        } catch {
        }
    }
    out := '{"reason":"' . reason . '"'
    out .= ',"gui":' . (hwnd ? 1 : 0)
    out .= ',"hwnd":' . hwnd
    out .= ',"x":' . x . ',"y":' . y . ',"w":' . w . ',"h":' . h
    out .= ',"re_w":' . reW . ',"re_h":' . reH
    out .= ',"first_vis_line":' . firstVisLine
    out .= ',"line_count":' . visLineCount
    out .= ',"words":' . HighlightWords.Length
    out .= ',"cur_idx":' . HighlightCurrentIdx
    out .= ',"char_idx":' . charIdx
    out .= ',"paused":' . (HighlightPaused ? "true" : "false")
    out .= ',"scale":' . Format("{:.2f}", gOverlayScale)
    out .= ',"dpi":' . gOverlayDpi
    out .= ',"sel_start":' . gLastSelStart . ',"sel_end":' . gLastSelEnd
    out .= '}'
    FileAppend out, path, "UTF-8-RAW"
}

; Demo tick hook: the 30ms highlight tick already owns the word-change
; moment — piggyback the reporter there so the director sees the amber
; word's real timing without a second timer. Also reports on GUI LIFECYCLE
; changes (built/destroyed/rebuilt): a rebuild after done produces the
; same cur_idx as before, and a change-gate on words alone would leave
; the director staring at a stale dead-hwnd report forever.
DemoTickReport() {
    global HighlightGui, HighlightCurrentIdx, gLastReportedIdx, gLastReportedGui
    guiAlive := (HighlightGui != "") ? 1 : 0
    if (HighlightCurrentIdx != gLastReportedIdx or guiAlive != gLastReportedGui) {
        gLastReportedIdx := HighlightCurrentIdx
        gLastReportedGui := guiAlive
        DemoChildReport("tick")
    }
}