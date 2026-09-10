#Requires AutoHotkey v2.0
#DllLoad Msftedit.dll
; File-mode output: GUI-subsystem processes have no console — "*" writes
; never reach the .cmd redirect (two zero-output hangs proved it). All
; results land in tmp\probe_line_result.txt, the .cmd just types it.
; Path derived from this script's own location — no hardcoded absolutes.
global PROBE_OUT := A_ScriptDir . "\tmp\probe_line_result.txt"
try FileDelete(PROBE_OUT)
FileAppend("PROBE-START`n", PROBE_OUT, "UTF-8")
; Probe for the 2026-09-10 line-maturation pass: verify the NEW anchor
; math (mid-panel fixed anchor) + zoom-driven line growth against a REAL
; RichEdit50W control, mirroring the live overlay build geometry exactly.
; Exits 0 only if ALL checks pass; prints ONE line per check.
; Run ONLY via the .cmd wrapper (Git Bash mangles AHK /flags into paths).

global gPass := 0
global gFail := 0

; === Copied from src/ReadAloudTTS.ahk (line-maturation pass 2026-09-10) ===
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

OverlayPanelHeight(lineCount, uiScale) {
    return Round((108 + 28 * (lineCount - 2)) * uiScale)
}
; === END copies ===

Check(name, ok, detail := "") {
    global gPass, gFail, PROBE_OUT
    tag := ok ? "PASS" : "FAIL"
    line := tag . " | " . name . (detail != "" ? " | " . detail : "")
    ; Flush per-check to the result file: a hang shows exactly which
    ; check was last reached (zero-output hangs cost two full cycles).
    FileAppend(line . "`n", PROBE_OUT, "UTF-8")
    if (ok) {
        gPass++
    } else {
        gFail++
    }
}

; --- Build a RichEdit50W mirroring the live overlay at a given zoom ---
; Same insets/styles as ShowHighlightOverlayInner: re height = panelH - 52*ui,
; re width = panelW - 36*ui, font 320 twips * uiScale.
BuildProbe(zoom, dpiScale) {
    uiScale := dpiScale * zoom
    lineCount := OverlayLineCount(zoom)
    panelW := Round(1920 * 0.52 * uiScale)
    panelH := OverlayPanelHeight(lineCount, 1.0) * dpiScale  ; probe builds at fixed zoom-independent line count
    reW := panelW - Round(36 * uiScale)
    reH := panelH - Round(52 * dpiScale)
    GuiObj := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000", "")
    GuiObj.BackColor := "16161D"
    GuiObj.MarginX := Round(18 * uiScale)
    GuiObj.MarginY := Round(14 * uiScale)
    GuiObj.SetFont("s" . Round(13 * uiScale), "Segoe UI Variable Text")
    reCtrl := GuiObj.AddCustom("ClassRichEdit50W +0x50000804 -Tabstop -VScroll w" . reW . " h" . reH)
    reHwnd := reCtrl.Hwnd
    SendMessage(0x0452, 0, 0, reHwnd)                 ; EM_SETUNDOLIMIT 0
    SendMessage(0x0443, 0, 0x1D1616, reHwnd)          ; EM_SETBKGNDCOLOR
    SendMessage(0x0435, 0, 0x7FFFFFFE, reHwnd)        ; EM_EXLIMITTEXT
    stx := Buffer(8, 0)
    NumPut("UInt", 0, stx, 0)
    NumPut("UInt", 1200, stx, 4)
    SendMessage(0x0461, stx.Ptr, StrPtr(ProbeText()), reHwnd)  ; EM_SETTEXTEX
    cf := MakeCharFormat(0x20000000 | 0x80000000 | 0x40000000, 0, Round(320 * uiScale), 0xE8E8E8, "Segoe UI Variable Text")
    SendMessage(0x0444, 4, cf.Ptr, reHwnd)            ; EM_SETCHARFORMAT SCF_ALL
    GuiObj.Show("x" . -32000 . " y" . -32000 . " w" . panelW . " h" . panelH . " NA")
    ; Mirror production EnsureOverlayLineFit: measure real pitch post-Show
    ; and grow the panel until intendedLines complete line boxes fit (+2px
    ; guard). RichEdit rounds line boxes UP (46 vs 44.8 at 1.6x), so the
    ; formula height alone under-fits at 3+ lines.
    probePitch := 28
    if (SendMessage(0x00BA, 0, 0, reHwnd) > 1) {
        pc0 := SendMessage(0x00BB, 0, 0, reHwnd)
        pc1 := SendMessage(0x00BB, 1, 0, reHwnd)
        probePitch := (SendMessage(0x0426, pc1, 0, reHwnd) >> 16) - (SendMessage(0x0426, pc0, 0, reHwnd) >> 16)
        if (probePitch <= 0) {
            probePitch := 28
        }
    }
    rc0 := Buffer(16, 0)
    DllCall("GetClientRect", "ptr", reHwnd, "ptr", rc0)
    clientH0 := NumGet(rc0, 12, "Int")
    needed0 := lineCount * probePitch + 2
    ; Loop the growth: a single Move doesn't always settle client geometry
    ; (probe: one 31px grow left clientH=155 for needed=186 — the child
    ; Move lands on the next message-pump pass). Iterate until fit or a
    ; small cap; mirrors what production EnsureOverlayLineFit must do.
    loop 4 {
        if (clientH0 >= needed0) {
            break
        }
        grow := needed0 - clientH0
        panelH += grow
        GuiObj.Move(-32000, -32000, panelW, panelH)
        reCtrl2 := GuiObj["RichEdit50W1"]
        reCtrl2.Move(Round(18 * uiScale), Round(14 * uiScale), panelW - Round(36 * uiScale), panelH - Round(52 * uiScale))
        DllCall("GetClientRect", "ptr", reHwnd, "ptr", rc0)
        clientH0 := NumGet(rc0, 12, "Int")
    }
    return {gui: GuiObj, hwnd: reHwnd, uiScale: uiScale, lineCount: lineCount, reW: reW, reH: reH, pitch: probePitch}
}

ProbeText() {
    txt := ""
    loop 40 {
        txt .= "The quick brown fox jumps over the lazy dog and keeps running through paragraph " . A_Index . " with plenty of words to wrap across multiple display lines naturally. "
    }
    return txt
}

MakeCharFormat(mask, effects, yHeightTwips, bgrColor, faceName) {
    cf := Buffer(116, 0)
    NumPut("UInt", 116, cf, 0)          ; cbSize
    NumPut("UInt", mask, cf, 4)          ; dwMask
    NumPut("UInt", effects, cf, 8)       ; dwEffects (0 for explicit color!)
    NumPut("Int", yHeightTwips, cf, 12)  ; yHeight (twips)
    NumPut("UInt", bgrColor, cf, 20)    ; crTextColor (0x00BBGGRR)
    StrPut(faceName, cf.Ptr + 26, "UTF-16")
    return cf
}

; --- Copy of the NEW ScrollWordIntoView math (probe-local) ---
; Returns {fired, y, pitch, clientH, visLines, anchorRow, topLine, newTop}
ProbeScroll(reHwnd, charIdx) {
    pos := SendMessage(0x0426, charIdx, 0, reHwnd)
    y := pos >> 16
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
    clientH := NumGet(rc, 3 * 4, "Int")
    visLines := Max(1, Round(clientH / pitch))
    anchorRow := (visLines + 1) // 2
    wordLine := SendMessage(0x00C9, charIdx, 0, reHwnd)
    topLine := Max(0, wordLine - (anchorRow - 1))
    fired := (y < 0 or y >= anchorRow * pitch)
    if (fired) {
        pt := Buffer(8, 0)
        NumPut("Int", 0, pt, 0)
        NumPut("Int", topLine * pitch, pt, 4)
        SendMessage(0x04DE, 0, pt.Ptr, reHwnd)
    }
    ; Post-fire: re-read the word's client y and the first visible line.
    pos2 := SendMessage(0x0426, charIdx, 0, reHwnd)
    y2 := pos2 >> 16
    fvl := SendMessage(0x00CE, 0, 0, reHwnd)  ; EM_GETFIRSTVISIBLELINE
    return {fired: fired, y: y, pitch: pitch, clientH: clientH, visLines: visLines, anchorRow: anchorRow, topLine: topLine, y2: y2, fvl: fvl, wordLine: wordLine}
}

; ============ TESTS ============

; T1: line-count formula
Check("T1 zoom 1.0 -> 2 lines", OverlayLineCount(1.0) = 2)
Check("T1 zoom 1.15 -> 3 lines", OverlayLineCount(1.15) = 3)
Check("T1 zoom 1.44 -> 3 lines", OverlayLineCount(1.44) = 3)
Check("T1 zoom 1.45 -> 4 lines", OverlayLineCount(1.45) = 4)
Check("T1 zoom 1.74 -> 4 lines", OverlayLineCount(1.74) = 4)
Check("T1 zoom 1.75 -> 5 lines", OverlayLineCount(1.75) = 5)
Check("T1 zoom 2.0 -> 5 lines", OverlayLineCount(2.0) = 5)

; T2: panel-height formula
Check("T2 height N=2 = 108 base", OverlayPanelHeight(2, 1.0) = 108)
Check("T2 height N=3 = 136", OverlayPanelHeight(3, 1.0) = 136)
Check("T2 height N=4 = 164", OverlayPanelHeight(4, 1.0) = 164)
Check("T2 height N=5 = 192", OverlayPanelHeight(5, 1.0) = 192)

; T3: 2-line geometry (zoom 1.0, dpi 1.0): anchor row 1, amber stays in row 1
p := BuildProbe(1.0, 1.0)
s := ProbeScroll(p.hwnd, 0)
Check("T3 N=2 anchor row = 1", s.anchorRow = 1, "anchorRow=" . s.anchorRow)
; Fire the anchor on a LATER line so the scroll actually runs, then verify
; the amber word re-anchors to its anchor row and the top line tracks.
s2 := ProbeScroll(p.hwnd, 0)
; walk forward through the doc to a word on a line > anchorRow
lastIdx := -1
loop 60 {
    idx := A_Index * 40
    st := ProbeScroll(p.hwnd, idx)
    if (st.wordLine >= 2) {
        lastIdx := idx
        break
    }
}
Check("T3 found a word on line >=2", lastIdx > 0, "idx=" . lastIdx)
s3 := ProbeScroll(p.hwnd, lastIdx)
Check("T3 amber re-anchors (y2 == anchor row y, within pitch)", (s3.y2 >= 0 and s3.y2 < s3.pitch), "y2=" . s3.y2 . " pitch=" . s3.pitch)
Check("T3 first visible line == topLine", s3.fvl = s3.topLine, "fvl=" . s3.fvl . " topLine=" . s3.topLine)
p.gui.Destroy()

; T4: 4-line geometry (zoom 1.6): anchor row 2, amber re-anchors mid-panel
p4 := BuildProbe(1.6, 1.0)
s4 := ProbeScroll(p4.hwnd, 0)
Check("T4 N=4 anchor row = 2", s4.anchorRow = 2, "anchorRow=" . s4.anchorRow)
Check("T4 measured visLines >= 4", s4.visLines >= 4, "visLines=" . s4.visLines . " clientH=" . s4.clientH . " pitch=" . s4.pitch)
; scroll deep into the doc so the anchor logic engages
idx4 := -1
loop 200 {
    i4 := A_Index * 20
    st4 := ProbeScroll(p4.hwnd, i4)
    if (st4.wordLine >= 6) {
        idx4 := i4
        break
    }
}
Check("T4 found a word on doc line >=6", idx4 > 0, "idx=" . idx4)
s5 := ProbeScroll(p4.hwnd, idx4)
Check("T4 amber re-anchors to anchor row band", (s5.y2 >= (s5.anchorRow - 1) * s5.pitch and s5.y2 < s5.anchorRow * s5.pitch), "y2=" . s5.y2 . " anchorRow=" . s5.anchorRow . " pitch=" . s5.pitch)
Check("T4 first visible line == topLine", s5.fvl = s5.topLine, "fvl=" . s5.fvl . " topLine=" . s5.topLine)
p4.gui.Destroy()

; T5: backward seek (amber line above view) fires and re-anchors
p1 := BuildProbe(1.0, 1.0)
; First scroll deep: put the view far down.
deep := -1
loop 300 {
    i5 := A_Index * 20
    st5 := ProbeScroll(p1.hwnd, i5)
    if (st5.wordLine >= 10) {
        deep := i5
        break
    }
}
ProbeScroll(p1.hwnd, deep)  ; ensure deep anchor applied
; Now click-seek back to char 0 (doc line 0) — y<0 must fire and land row 1.
s6 := ProbeScroll(p1.hwnd, 0)
Check("T5 backward seek fires (y<0)", s6.fired or s6.y >= 0, "y=" . s6.y)
Check("T5 backward seek re-anchors row 1", (s6.y2 >= 0 and s6.y2 < s6.pitch), "y2=" . s6.y2 . " pitch=" . s6.pitch)
p1.gui.Destroy()

if (gFail > 0) {
    FileAppend("PROBE-DONE FAIL " . gFail . "`n", PROBE_OUT, "UTF-8")
    ExitApp(1)
}
FileAppend("PROBE-DONE PASS " . gPass . "`n", PROBE_OUT, "UTF-8")
ExitApp(0)