package main

import (
	"math"

	"kutta/foil"
	"kutta/scene"
)

// sultanaScene is an optional wind-tunnel specimen. Every component is a
// closed LBM solid: the flow, pressure and wake respond to it, rather than to
// a decorative overlay.  Its planform follows aefcsefef.ork: 180 mm ellipsoid
// nose, 700 mm x 50.8 mm body tube, rear trapezoids and forward airfoil fins.
// A side view exposes the upper/lower pair of each radial fin set; the small
// axial offset keeps the four individual canards legible.
func sultanaScene() *scene.Scene {
	p := func(x, y float64) foil.Point { return foil.Point{X: x, Y: y} }

	// The .ork assembly is 0.880 m long.  At 250 lattice cells per metre it
	// fills the tunnel without approaching the inlet/outlet boundaries.
	rocket := openRocketBody(p)
	// 147/49 mm trapezoids: 89.60 mm sweep, 70 mm span, rooted at the body end.
	tailTop := trapezoidFin(p, 233.25, 270, 255.65, 267.90, 106.35, 17.50, true)
	tailBottom := trapezoidFin(p, 233.25, 270, 255.65, 267.90, 93.65, 17.50, false)

	objects := []*scene.Object{
		{Name: "cohete Sultana del Norte", Shape: rocket, Pivot: p(160, 100)},
		{Name: "estabilizador superior", Shape: tailTop, Pivot: p(251, 106)},
		{Name: "estabilizador inferior", Shape: tailBottom, Pivot: p(251, 94)},
	}
	// OpenRocket defines the forward set as four 93.1 mm root-chord, 72 mm
	// high, zero-tip-chord airfoil fins positioned 150 mm from the tube front.
	// Their correct side-view silhouette is triangular.  The normal NACA mode
	// remains intact for analysing individual NACA profiles interactively.
	objects = append(objects,
		&scene.Object{Name: "canard C1 · sección NACA", Shape: triangularCanard(p, 132.5, 155.78, 106.35, 18, true), Pivot: p(144, 106)},
		&scene.Object{Name: "canard C2 · sección NACA", Shape: triangularCanard(p, 136.0, 159.28, 106.35, 18, true), Pivot: p(148, 106)},
		&scene.Object{Name: "canard C3 · sección NACA", Shape: triangularCanard(p, 132.5, 155.78, 93.65, 18, false), Pivot: p(144, 94)},
		&scene.Object{Name: "canard C4 · sección NACA", Shape: triangularCanard(p, 136.0, 159.28, 93.65, 18, false), Pivot: p(148, 94)},
	)
	return &scene.Scene{Objects: objects}
}

// openRocketBody creates the continuous ellipsoid-nose/body outline from the
// OpenRocket dimensions.  It is a physical 2D profile, not a generic rocket.
func openRocketBody(p func(float64, float64) foil.Point) []foil.Point {
	const (
		noseX, noseBase, tailX = 50.0, 95.0, 270.0
		cy, radius             = 100.0, 6.35
		segments               = 20
	)
	shape := make([]foil.Point, 0, 2*segments+4)
	for i := 0; i <= segments; i++ {
		u := float64(i) / segments
		shape = append(shape, p(noseX+(noseBase-noseX)*u, cy+radius*math.Sqrt(1-(u-1)*(u-1))))
	}
	shape = append(shape, p(tailX, cy+radius), p(tailX, cy-radius))
	for i := segments; i >= 0; i-- {
		u := float64(i) / segments
		shape = append(shape, p(noseX+(noseBase-noseX)*u, cy-radius*math.Sqrt(1-(u-1)*(u-1))))
	}
	return shape
}

func trapezoidFin(p func(float64, float64) foil.Point, rootLead, rootTrail, tipLead, tipTrail, rootY, height float64, upper bool) []foil.Point {
	if upper {
		return []foil.Point{p(rootLead, rootY), p(rootTrail, rootY), p(tipTrail, rootY+height), p(tipLead, rootY+height)}
	}
	return []foil.Point{p(rootLead, rootY), p(tipLead, rootY-height), p(tipTrail, rootY-height), p(rootTrail, rootY)}
}

func triangularCanard(p func(float64, float64) foil.Point, rootLead, tipX, rootY, height float64, upper bool) []foil.Point {
	if upper {
		return []foil.Point{p(rootLead, rootY), p(tipX, rootY+height), p(tipX, rootY)}
	}
	return []foil.Point{p(rootLead, rootY), p(tipX, rootY), p(tipX, rootY-height)}
}
