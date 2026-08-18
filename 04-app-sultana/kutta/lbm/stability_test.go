package lbm

import (
	"math"
	"testing"
)

// broadsideMask is the regression geometry for issue #1: a vertical plate that
// blocks ~70% of the channel, the worst case a user can reach by turning the
// foil to +-90 degrees at the top inlet speed.
func broadsideMask(nx, ny int) []bool {
	mask := make([]bool, nx*ny)
	x0 := nx / 3
	h := int(0.72 * float64(ny))
	y0 := (ny - h) / 2
	for y := y0; y < y0+h; y++ {
		for x := x0; x < x0+8; x++ {
			mask[y*nx+x] = true
		}
	}
	return mask
}

// TestBroadsideAtTopSpeedStaysFinite reproduces issue #1 headlessly: an
// impulsive start at u0=0.15 with a broadside plate used to dissolve into NaNs
// within ~900 steps. The collide clamp must keep the run finite.
func TestBroadsideAtTopSpeedStaysFinite(t *testing.T) {
	s := New(360, 200, 0.6, 0.15)
	s.SetSolid(broadsideMask(360, 200))
	for i := 1; i <= 2400; i++ {
		s.Step()
		if i%100 == 0 && !s.Finite() {
			t.Fatalf("solver went non-finite at step %d", i)
		}
	}
	if !s.Finite() {
		t.Fatal("solver went non-finite by the end of the run")
	}
}

// TestClampInertInNormalEnvelope guards the other side of the clamp: at a
// slender-body operating point the flow must never come near the clamp bounds,
// so the toy's normal physics is untouched by the stability net.
func TestClampInertInNormalEnvelope(t *testing.T) {
	s := New(360, 200, 0.6, 0.10)
	// A thin horizontal plate: mild blockage, attached flow.
	mask := make([]bool, 360*200)
	for x := 120; x < 240; x++ {
		for y := 98; y < 102; y++ {
			mask[y*360+x] = true
		}
	}
	s.SetSolid(mask)
	peak := 0.0
	for i := 1; i <= 1200; i++ {
		s.Step()
		if i%100 != 0 {
			continue
		}
		for c := range s.Ux {
			sp := math.Hypot(s.Ux[c], s.Uy[c])
			if sp > peak {
				peak = sp
			}
		}
	}
	if peak >= uMax {
		t.Fatalf("clamp engaged in the normal envelope: peak |u| = %.3f >= %.2f", peak, uMax)
	}
}

func TestFiniteDetectsNaN(t *testing.T) {
	s := New(64, 32, 0.6, 0.10)
	if !s.Finite() {
		t.Fatal("fresh solver reports non-finite")
	}
	s.Rho[100] = math.NaN()
	if s.Finite() {
		t.Fatal("Finite() = true with NaN density")
	}
	s.Rho[100] = 1
	s.Uy[7] = math.Inf(1)
	if s.Finite() {
		t.Fatal("Finite() = true with Inf velocity")
	}
}
