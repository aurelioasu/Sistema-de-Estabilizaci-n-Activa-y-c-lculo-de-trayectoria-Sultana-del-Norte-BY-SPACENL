//go:build !windows

package main

func syncEmbeddedWindow(_ uintptr, embedded bool, x, y, width, height int) (bool, int, int, int, int) {
	return embedded, x, y, width, height
}
