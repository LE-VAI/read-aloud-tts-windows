#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

SetWorkingDir A_ScriptDir

global AppDir := A_ScriptDir
global ConfigPath := AppDir . "\config.json"
global TempDir := AppDir . "\tmp"
global TextPath := TempDir . "\selection.txt"
global PidPath := TempDir . "\speak.pid"
global PyExe := AppDir . "\.venv\Scripts\python.exe"
global Q := Chr(34)

DirCreate TempDir
InitTray()

^RButton::ReadSelection()
^!Space::StopSpeech()

InitTray() {
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Read Selection`tCtrl+Right-click", ReadSelection)
    A_TrayMenu.Add("Stop`tCtrl+Alt+Space", StopSpeech)
    A_TrayMenu.Add()

    voiceMenu := Menu()
    voices := GetVoiceMap()
    current := GetCurrentVoice()
    for voiceId, label in voices {
        voiceMenu.Add(label, SetVoice.Bind(voiceId))
        if (voiceId = current) {
            voiceMenu.Check(label)
        }
    }
    A_TrayMenu.Add("Voice", voiceMenu)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Open Config", OpenConfig)
    A_TrayMenu.Add("Open Logs", OpenLogs)
    A_TrayMenu.Add("Exit", (*) => ExitApp())
    A_TrayMenu.Default := "Read Selection`tCtrl+Right-click"
}

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
        InitTray()
        TrayTip "Voice set to " . voiceId, "ReadAloudTTS"
    } else {
        TrayTip "Could not set voice. Open logs for details.", "ReadAloudTTS"
    }
}

ReadSelection(*) {
    global TextPath, PyExe, AppDir, PidPath, TempDir, Q

    if !FileExist(PyExe) {
        TrayTip "Python environment missing. Run install.ps1.", "ReadAloudTTS"
        return
    }

    savedClipboard := ClipboardAll()
    A_Clipboard := ""
    Send "^c"

    if !ClipWait(0.8) {
        A_Clipboard := savedClipboard
        TrayTip "No selected text found.", "ReadAloudTTS"
        return
    }

    text := A_Clipboard
    A_Clipboard := savedClipboard
    text := Trim(text)

    if (text = "") {
        TrayTip "No selected text found.", "ReadAloudTTS"
        return
    }

    StopSpeech()
    DirCreate TempDir
    try FileDelete TextPath
    FileAppend text, TextPath, "UTF-8"

    cmd := Q . PyExe . Q . " " . Q . AppDir . "\speak.py" . Q . " --input-file " . Q . TextPath . Q . " --delete-input-file"
    Run(cmd, AppDir, "Hide", &pid)
    try FileDelete PidPath
    FileAppend pid, PidPath, "UTF-8"
    TrayTip "Speaking selection...", "ReadAloudTTS"
}

StopSpeech(*) {
    global PidPath
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
