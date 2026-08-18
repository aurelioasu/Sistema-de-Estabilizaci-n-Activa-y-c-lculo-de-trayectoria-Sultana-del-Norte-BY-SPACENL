package lbm

import (
	"math"
	"testing"
)

// weightsSumToOne is the basic D2Q9 sanity check; a broken weight table
// silently corrupts every equilibrium.
func TestWeightsSumToOne(t *testing.T) {
	sum := 0.0
	for _, wi := range w {
		sum += wi
	}
	if math.Abs(sum-1) > 1e-12 {
		t.Fatalf("D2Q9 weights sum to %g, want 1", sum)
	}
}

// equilibriumRecoversMacros verifies that a field set entirely to equilibrium
// reports back the density and velocity it was built from.
func TestEquilibriumRecoversMacros(t *testing.T) {
	const rho, ux, uy = 1.0, 0.07, -0.03
	gotRho, gotMx, gotMy := 0.0, 0.0, 0.0
	for i := range 9 {
		fi := feq(i, rho, ux, uy)
		gotRho += fi
		gotMx += fi * exf[i]
		gotMy += fi * eyf[i]
	}
	if math.Abs(gotRho-rho) > 1e-12 {
		t.Errorf("density = %g, want %g", gotRho, rho)
	}
	if math.Abs(gotMx/gotRho-ux) > 1e-12 {
		t.Errorf("ux = %g, want %g", gotMx/gotRho, ux)
	}
	if math.Abs(gotMy/gotRho-uy) > 1e-12 {
		t.Errorf("uy = %g, want %g", gotMy/gotRho, uy)
	}
}

// uniformFlowStaysUniform checks that an empty channel keeps its free stream:
// no body, no walls, so nothing should disturb the inlet velocity.
func TestUniformFlowStaysUniform(t *testing.T) {
	const u0 = 0.08
	s := New(40, 20, 0.6, u0)
	for range 200 {
		s.Step()
	}
	c := 10*s.NX + 20
	if math.Abs(s.Ux[c]-u0) > 1e-3 {
		t.Errorf("ux drifted to %g, want ~%g", s.Ux[c], u0)
	}
	if math.Abs(s.Uy[c]) > 1e-3 {
		t.Errorf("uy = %g, want ~0", s.Uy[c])
	}
	if math.Abs(s.Rho[c]-1) > 1e-3 {
		t.Errorf("rho = %g, want ~1", s.Rho[c])
	}
}

// bluffBodyStaysFiniteAndDrags runs flow past a solid block and asserts the
// simulation stays stable, the body sees positive drag, and the wake slows down.
func TestBluffBodyStaysFiniteAndDrags(t *testing.T) {
	const u0 = 0.1
	s := New(120, 60, 0.6, u0)
	mask := make([]bool, s.NX*s.NY)
	for y := 25; y < 35; y++ {
		for x := 30; x < 40; x++ {
			mask[y*s.NX+x] = true
		}
	}
	s.SetSolid(mask)
	for range 400 {
		s.Step()
	}
	for c := range s.Rho {
		if math.IsNaN(s.Rho[c]) || math.IsInf(s.Rho[c], 0) {
			t.Fatalf("solver diverged at cell %d", c)
		}
	}
	if s.Fx <= 0 {
		t.Errorf("drag Fx = %g, want positive (with the stream)", s.Fx)
	}
	wake := 30*s.NX + 45
	if s.Ux[wake] >= u0 {
		t.Errorf("wake speed %g not below free stream %g", s.Ux[wake], u0)
	}
}

// inclinedPlateLiftsUp checks the force sign convention end to end: a thin plate
// with its leading (upstream) edge high and trailing edge low sits at a positive
// angle of attack, so it must feel positive lift (+y) and positive drag (+x).
func TestInclinedPlateLiftsUp(t *testing.T) {
	s := New(200, 100, 0.6, 0.1)
	mask := make([]bool, s.NX*s.NY)
	for x := 60; x < 140; x++ {
		yc := 58 - int(float64(x-60)*0.18) // left high, right low => nose up
		for dy := -2; dy <= 2; dy++ {
			mask[(yc+dy)*s.NX+x] = true
		}
	}
	s.SetSolid(mask)
	for range 4000 {
		s.Step()
	}
	if s.Fy <= 0 {
		t.Errorf("lift Fy = %g, want positive (nose-up plate)", s.Fy)
	}
	if s.Fx <= 0 {
		t.Errorf("drag Fx = %g, want positive", s.Fx)
	}
}

// centerOfPressureLandsOnTheBody: Mz together with the force should place the
// center of pressure somewhere along the plate, not off in space.
func TestCenterOfPressureOnPlate(t *testing.T) {
	s := New(200, 100, 0.6, 0.1)
	mask := make([]bool, s.NX*s.NY)
	for x := 60; x < 140; x++ {
		yc := 58 - int(float64(x-60)*0.18)
		for dy := -2; dy <= 2; dy++ {
			mask[(yc+dy)*s.NX+x] = true
		}
	}
	s.SetSolid(mask)
	for range 4000 {
		s.Step()
	}
	// Intersect the force's line of action (Rx*Fy - Ry*Fx = Mz) with the chord.
	cx, cy := 1.0, -0.18
	norm := math.Hypot(cx, cy)
	cx, cy = cx/norm, cy/norm
	lex, ley := 60.0, 58.0
	denom := cx*s.Fy - cy*s.Fx
	if math.Abs(denom) < 1e-9 {
		t.Fatal("degenerate force, cannot locate center of pressure")
	}
	tt := (s.Mz - (lex*s.Fy - ley*s.Fx)) / denom
	copX := lex + tt*cx
	if copX < 55 || copX > 145 {
		t.Errorf("center of pressure x = %g, want on the plate [60,140]", copX)
	}
}

// updateSolidKeepsFlow: nudging the body must not wipe the developed flow.
func TestUpdateSolidKeepsFlow(t *testing.T) {
	s := New(120, 60, 0.6, 0.1)
	mask := make([]bool, s.NX*s.NY)
	for y := 25; y < 35; y++ {
		for x := 30; x < 40; x++ {
			mask[y*s.NX+x] = true
		}
	}
	s.SetSolid(mask)
	for range 300 {
		s.Step()
	}
	wake := 30*s.NX + 50
	before := s.Ux[wake]
	// Shift the body one cell; the wake velocity should barely move, proving the
	// field was preserved rather than reset to the uniform free stream.
	shifted := make([]bool, s.NX*s.NY)
	for y := 25; y < 35; y++ {
		for x := 31; x < 41; x++ {
			shifted[y*s.NX+x] = true
		}
	}
	s.UpdateSolid(shifted)
	s.Step()
	after := s.Ux[wake]
	if math.Abs(after-before) > 0.02 {
		t.Errorf("wake jumped %g -> %g; UpdateSolid reset the flow", before, after)
	}
}

func TestVelocityAtBilinear(t *testing.T) {
	s := New(4, 4, 0.6, 0.0)
	// Lay down a known horizontal gradient: ux = x.
	for y := 0; y < s.NY; y++ {
		for x := 0; x < s.NX; x++ {
			s.Ux[y*s.NX+x] = float64(x)
		}
	}
	ux, _ := s.VelocityAt(1.5, 2)
	if math.Abs(ux-1.5) > 1e-9 {
		t.Errorf("bilinear ux = %g, want 1.5", ux)
	}
	// Clamping outside the domain must not panic and must hold the edge value.
	ux, _ = s.VelocityAt(99, 99)
	if math.Abs(ux-3) > 1e-9 {
		t.Errorf("clamped ux = %g, want 3", ux)
	}
}

func TestVorticitySolidBodyRotation(t *testing.T) {
	s := New(5, 5, 0.6, 0.0)
	// Solid-body rotation u = (-y, x) about the center has constant curl 2.
	for y := 0; y < s.NY; y++ {
		for x := 0; x < s.NX; x++ {
			c := y*s.NX + x
			s.Ux[c] = -float64(y)
			s.Uy[c] = float64(x)
		}
	}
	got := s.Vorticity(2, 2)
	if math.Abs(got-2) > 1e-9 {
		t.Errorf("vorticity = %g, want 2", got)
	}
}
