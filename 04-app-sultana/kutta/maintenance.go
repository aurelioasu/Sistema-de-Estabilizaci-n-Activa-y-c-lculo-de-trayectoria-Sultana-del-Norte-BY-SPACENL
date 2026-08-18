package main

import (
	"bufio"
	"fmt"
	"image/color"
	"io"
	"runtime"
	"runtime/debug"
	"strings"
	"time"

	"github.com/hajimehoshi/ebiten/v2"

	"kutta/viz"
)

const degradedParticles = 1800

func (g *Game) startStdinControl(input io.Reader) {
	go func() {
		scanner := bufio.NewScanner(input)
		for scanner.Scan() {
			switch strings.ToUpper(strings.TrimSpace(scanner.Text())) {
			case "MAINTENANCE":
				g.enqueue(g.performMemoryMaintenance)
			case "SAVE_SESSION":
				g.enqueue(func() { g.saveSession(true) })
			}
		}
	}()
}

func (g *Game) applyDegradedMode() {
	g.degraded = true
	g.glow = false
	g.showParticles = true
	g.smoke = viz.NewParticles(degradedParticles, gridW, gridH, 1)
	g.smokeVtx = nil
	g.smokeIdx = nil
	g.tps = 30
	ebiten.SetTPS(30)
}

func (g *Game) performMemoryMaintenance() {
	g.saveSession(true)
	g.applyDegradedMode()
	g.releaseGraphics()
	g.allocateGraphics()
	runtime.GC()
	debug.FreeOSMemory()
	g.saveSession(true)
	fmt.Println("KUTTA_MAINTENANCE_DONE")
}

func (g *Game) allocateGraphics() {
	g.fieldImg = ebiten.NewImage(gridW, gridH)
	g.trailImg = ebiten.NewImage(simW, simH)
	g.dotImg = ebiten.NewImage(2, 2)
	g.dotImg.Fill(color.White)
	g.fadeImg = ebiten.NewImage(1, 1)
	g.fadeImg.Fill(color.RGBA{0, 0, 0, 26})
	if len(g.pixbuf) != gridW*gridH*4 {
		g.pixbuf = make([]byte, gridW*gridH*4)
	}
	g.lastFieldPaint = time.Time{}
	g.lastTrailPaint = time.Time{}
}

func (g *Game) releaseGraphics() {
	for _, image := range []*ebiten.Image{g.fieldImg, g.trailImg, g.dotImg, g.fadeImg, g.labelBarImg} {
		if image != nil {
			image.Deallocate()
		}
	}
	g.fieldImg, g.trailImg, g.dotImg, g.fadeImg, g.labelBarImg = nil, nil, nil, nil, nil
	g.bloomFx.deallocate()
}
