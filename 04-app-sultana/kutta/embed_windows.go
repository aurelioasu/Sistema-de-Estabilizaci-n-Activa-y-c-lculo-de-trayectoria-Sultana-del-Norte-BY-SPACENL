//go:build windows

package main

import (
	"log"
	"unsafe"

	"github.com/hajimehoshi/ebiten/v2"
)

var (
	getWindowLongW  = user32.NewProc("GetWindowLongW")
	setWindowLongW  = user32.NewProc("SetWindowLongW")
	setWindowOwner  = user32.NewProc("SetWindowLongPtrW")
	setWindowPos    = user32.NewProc("SetWindowPos")
	showWindow      = user32.NewProc("ShowWindow")
	getWindowRect   = user32.NewProc("GetWindowRect")
	getAncestor     = user32.NewProc("GetAncestor")
	isWindowVisible = user32.NewProc("IsWindowVisible")
	moveWindow      = user32.NewProc("MoveWindow")
)

type winRect struct {
	left, top, right, bottom int32
}

// syncEmbeddedWindow docks Kutta over the Qt tab without cross-process
// reparenting. Ebitengine owns a GPU swapchain whose SetParent path can
// deadlock with Qt; an owned, borderless native window preserves the graphics
// context while behaving as part of the tab.
func syncEmbeddedWindow(parent uintptr, embedded bool, oldX, oldY, oldWidth, oldHeight int) (bool, int, int, int, int) {
	if parent == 0 {
		return embedded, oldX, oldY, oldWidth, oldHeight
	}
	window := uintptr(mainWindowHandle())
	if window == 0 {
		return false, oldX, oldY, oldWidth, oldHeight
	}
	if !embedded {
		ebiten.RunOnMainThread(func() {
			style, _, _ := getWindowLongW.Call(window, ^uintptr(15)) // GWL_STYLE (-16)
			style = uintptr(uint32(style) &^ 0x00CF0000)             // overlapped frame
			style |= 0x86000000                                      // popup + clipping
			setWindowLongW.Call(window, ^uintptr(15), style)
			owner, _, _ := getAncestor.Call(parent, 2)      // GA_ROOT
			setWindowOwner.Call(window, ^uintptr(7), owner) // GWLP_HWNDPARENT (-8)
			setWindowPos.Call(window, 0, 0, 0, 0, 0, 0x0027)
			embedded = true
			log.Printf("KUTTA_EMBEDDED hwnd=%d parent=%d", window, parent)
		})
	}

	visible, _, _ := isWindowVisible.Call(parent)
	if visible == 0 {
		if oldWidth >= 0 {
			ebiten.RunOnMainThread(func() { showWindow.Call(window, 0) })
		}
		return embedded, oldX, oldY, -1, -1
	}
	var rect winRect
	ok, _, _ := getWindowRect.Call(parent, uintptr(unsafe.Pointer(&rect)))
	if ok == 0 {
		return embedded, oldX, oldY, oldWidth, oldHeight
	}
	x := int(rect.left)
	y := int(rect.top)
	width := max(1, int(rect.right-rect.left))
	height := max(1, int(rect.bottom-rect.top))
	if x != oldX || y != oldY || width != oldWidth || height != oldHeight {
		ebiten.RunOnMainThread(func() {
			moveWindow.Call(window, uintptr(x), uintptr(y), uintptr(width), uintptr(height), 1)
			showWindow.Call(window, 5)
		})
	}
	return embedded, x, y, width, height
}
