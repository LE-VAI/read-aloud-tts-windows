; wheel-logic-verify.ahk — unit-test the scroll-override state machine.
;
; The state machine is pure logic (no window needed), so it can be verified
; directly: simulate the wheel engaging the override, then advance a fake
; clock and assert the gate opens again at the documented time. This proves
; the 4-second behavior WITHOUT waiting on a real read.
;
; Launched detached (Start-Process) — a direct child of the bash shell hangs.

#Requires AutoHotkey v2.0

; --- replicate the state machine (same logic as ReadAloudTTS.ahk) ----------
global gScrollOverrideActive := false
global gScrollOverrideUntil := 0

Engage(nowMs, holdMs := 4000) {
    global gScrollOverrideActive, gScrollOverrideUntil
    gScrollOverrideActive := true
    gScrollOverrideUntil := nowMs + holdMs
}

MayAutoScroll(nowMs) {
    global gScrollOverrideActive, gScrollOverrideUntil
    if (!gScrollOverrideActive) {
        return true
    }
    if (nowMs >= gScrollOverrideUntil) {
        gScrollOverrideActive := false
        return true
    }
    return false
}

; --- assertions ------------------------------------------------------------
out := []
pass := 0
fail := 0
; NOTE the explicit `global` declarations: AHK v2 functions read globals
; read-only by default, so out.Push()/pass += 1 inside a function without a
; `global` line would silently operate on a LOCAL copy and the report would
; come out empty (which is exactly what the first run did).
check(name, cond) {
    global out, pass, fail
    if (cond) {
        pass += 1
        out.Push("PASS  " . name)
    } else {
        fail += 1
        out.Push("FAIL  " . name)
    }
}

; Baseline: following normally.
gScrollOverrideActive := false
check("follows when never scrolled", MayAutoScroll(1000) = true)

; Engage at t=10000 with a 4000ms hold.
Engage(10000, 4000)
check("suspends immediately after the wheel", MayAutoScroll(10000) = false)
check("still suspended at +1ms",            MayAutoScroll(10001) = false)
check("still suspended at +1s",             MayAutoScroll(11000) = false)
check("still suspended at +3.9s",           MayAutoScroll(13999) = false)

; The boundary: exactly at the hold, follow resumes.
check("resumes at exactly +4s",             MayAutoScroll(14000) = true)
check("stays following after expiry",       MayAutoScroll(14001) = true)
check("flag cleared on expiry",             gScrollOverrideActive = false)

; A second wheel re-engages, extending the window (not stacking).
Engage(20000, 4000)
check("re-engage suspends again",           MayAutoScroll(20000) = false)
Engage(22000, 4000)   ; user wheels again mid-hold
check("second wheel extends from ITS time", MayAutoScroll(24000) = false)
check("and releases 4s after that one",     MayAutoScroll(26000) = true)

; Disabled-follow mode always permits scrolling (gate never blocks).
gScrollOverrideActive := false
check("clean state follows",                MayAutoScroll(99999) = true)

out.Push("")
out.Push("passed=" . pass . " failed=" . fail)
report := A_ScriptDir . "\tmp\wheel-logic-report.txt"
try FileCreateDir(A_ScriptDir . "\tmp")
try FileDelete(report)
FileAppend(joinLines(out) . "`n", report, "UTF-8")
ExitApp

joinLines(arr) {
    s := ""
    for i, v in arr {
        s .= (i = 1 ? "" : "`n") . v
    }
    return s
}
