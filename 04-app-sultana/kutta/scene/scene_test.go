package scene

import (
	"math"
	"testing"

	"kutta/foil"
)

func approx(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func TestPoseAtNoKeys(t *testing.T) {
	o := &Object{}
	if o.PoseAt(3) != Identity() {
		t.Errorf("no keys should give identity, got %+v", o.PoseAt(3))
	}
}

func TestPoseAtClampsAndInterpolates(t *testing.T) {
	o := &Object{Keys: []Key{
		{T: 0, Pose: Pose{Rot: 0, Scale: 1}},
		{T: 2, Pose: Pose{Rot: 20, Scale: 1}},
		{T: 4, Pose: Pose{Rot: 0, Scale: 1}},
	}}
	got := o.PoseAt(-1)
	if got.Rot != 0 {
		t.Errorf("before first key should clamp: %+v", got)
	}
	got = o.PoseAt(9)
	if got.Rot != 0 {
		t.Errorf("after last key should clamp: %+v", got)
	}
	got = o.PoseAt(1)
	if !approx(got.Rot, 10) {
		t.Errorf("midpoint rot = %g, want 10", got.Rot)
	}
	got = o.PoseAt(3)
	if !approx(got.Rot, 10) {
		t.Errorf("rot at t=3 = %g, want 10", got.Rot)
	}
}

func TestApplyTranslate(t *testing.T) {
	pts := []foil.Point{{X: 0, Y: 0}, {X: 1, Y: 0}}
	out := Apply(pts, foil.Point{}, Pose{DX: 5, DY: -3, Scale: 1})
	if !approx(out[0].X, 5) || !approx(out[0].Y, -3) {
		t.Errorf("translate wrong: %+v", out[0])
	}
}

func TestApplyRotateAboutPivot(t *testing.T) {
	// A point one unit right of the pivot, rotated 90deg CCW, lands one unit up.
	pts := []foil.Point{{X: 1, Y: 0}}
	out := Apply(pts, foil.Point{X: 0, Y: 0}, Pose{Rot: 90, Scale: 1})
	if !approx(out[0].X, 0) || !approx(out[0].Y, 1) {
		t.Errorf("rotate 90 about origin: got %+v, want (0,1)", out[0])
	}
}

func TestApplyScaleAboutPivot(t *testing.T) {
	pts := []foil.Point{{X: 2, Y: 0}}
	out := Apply(pts, foil.Point{X: 1, Y: 0}, Pose{Scale: 2})
	if !approx(out[0].X, 3) { // pivot 1 + 2*(2-1) = 3
		t.Errorf("scale about pivot: got %+v, want X=3", out[0])
	}
}

func TestLoopTime(t *testing.T) {
	s := &Scene{Loop: 4}
	got := s.LoopTime(5)
	if !approx(got, 1) {
		t.Errorf("LoopTime(5) = %g, want 1", got)
	}
	got = s.LoopTime(8)
	if !approx(got, 0) {
		t.Errorf("LoopTime(8) = %g, want 0", got)
	}
	static := &Scene{Loop: 0}
	got = static.LoopTime(99)
	if got != 0 {
		t.Errorf("static LoopTime = %g, want 0", got)
	}
}

func square(cx, cy, h float64) []foil.Point {
	return []foil.Point{
		{X: cx - h, Y: cy - h}, {X: cx + h, Y: cy - h},
		{X: cx + h, Y: cy + h}, {X: cx - h, Y: cy + h},
	}
}

func TestMaskUnion(t *testing.T) {
	s := &Scene{Objects: []*Object{
		{Name: "a", Shape: square(5, 5, 2)},
		{Name: "b", Shape: square(15, 5, 2)},
	}}
	mask := s.Mask(0, 20, 10)
	count := 0
	for _, b := range mask {
		if b {
			count++
		}
	}
	if count == 0 {
		t.Fatal("union mask is empty")
	}
	if !mask[5*20+5] || !mask[5*20+15] {
		t.Error("both squares should be solid in the union")
	}
}

// TestMaskFollowsAnimation: a translating object must occupy different cells at
// different times.
func TestMaskFollowsAnimation(t *testing.T) {
	o := &Object{
		Name:  "mover",
		Shape: square(5, 5, 2),
		Keys: []Key{
			{T: 0, Pose: Pose{Scale: 1}},
			{T: 1, Pose: Pose{DX: 10, Scale: 1}},
		},
	}
	s := &Scene{Objects: []*Object{o}, Loop: 1}
	at0 := s.Mask(0, 30, 10)
	at1 := s.Mask(1, 30, 10)
	if !at0[5*30+5] {
		t.Error("at t=0 the object should sit near x=5")
	}
	if !at1[5*30+15] {
		t.Error("at t=1 the object should have moved to x=15")
	}
	if at1[5*30+5] {
		t.Error("at t=1 the object should have left x=5")
	}
}
