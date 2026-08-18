// Command snapshot is a headless verification aid: it runs the solver past a
// NACA airfoil and writes PNGs of the speed and vorticity fields, plus a lift
// sweep, so the physics/geometry/colormap pipeline can be checked without a GPU.
// It is a development tool, not part of the app.
package main

import (
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"os"
	"path/filepath"

	"kutta/foil"
	"kutta/lbm"
	"kutta/viz"
)

const (
	nx, ny = 360, 200
	chord  = 0.40 * nx
	leadX  = 0.26 * nx
	u0     = 0.10
	vscale = 0.06
)

func main() {
	outDir := flag.String("out", ".", "directory to write the output PNGs into")
	flag.Parse()

	// Lift sweep: lift should rise with angle of attack before stalling.
	fmt.Println("AoA sweep (NACA 2412):")
	for _, a := range []float64{-4, 0, 4, 8, 12, 16} {
		s := run("2412", a, 4000)
		denom := 0.5 * u0 * u0 * chord
		fmt.Printf("  AoA %+5.0f deg  Cl ~ %+6.3f  Cd ~ %+6.3f\n", a, s.Fy/denom, s.Fx/denom)
	}

	s := run("2412", 8, 6000)
	for _, m := range []string{"speed", "vort", "press"} {
		p := filepath.Join(*outDir, "kutta_"+m+".png")
		writePNG(p, s, m)
		a, err := filepath.Abs(p)
		if err == nil {
			fmt.Printf("wrote %s\n", a)
		}
	}
}

func run(code string, alphaDeg float64, steps int) *lbm.Solver {
	s := lbm.New(nx, ny, 0.6, u0)
	outline, err := foil.NACA4(code, 80)
	if err != nil {
		panic(err)
	}
	placed := foil.Place(outline, chord, leadX, ny/2.0, alphaDeg*math.Pi/180, 0)
	s.SetSolid(foil.Rasterize(placed, nx, ny))
	for range steps {
		s.Step()
	}
	return s
}

func writePNG(path string, s *lbm.Solver, mode string) {
	img := image.NewRGBA(image.Rect(0, 0, nx, ny))
	for y := range ny {
		row := ny - 1 - y
		for x := range nx {
			var c color.RGBA
			switch {
			case s.Solid(x, y):
				c = color.RGBA{0x1a, 0x1d, 0x24, 0xff}
			case mode == "vort":
				c = viz.Vorticity(s.Vorticity(x, y), vscale)
			case mode == "press":
				cp := (s.Rho[y*nx+x] - 1) / (1.5 * u0 * u0)
				c = viz.Pressure(cp, 1.5)
			default:
				sp := math.Hypot(s.Ux[y*nx+x], s.Uy[y*nx+x])
				c = viz.Speed(sp / (u0 * 2))
			}
			img.Set(x, row, c)
		}
	}
	f, err := os.Create(path) // #nosec G304 -- output path is an explicit CLI arg
	if err != nil {
		panic(err)
	}
	err = png.Encode(f, img)
	if err != nil {
		panic(err)
	}
	err = f.Close()
	if err != nil {
		panic(err)
	}
}
