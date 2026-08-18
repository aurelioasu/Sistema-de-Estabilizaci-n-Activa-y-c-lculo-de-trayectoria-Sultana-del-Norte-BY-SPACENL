package sceneio

import (
	"math"
	"testing"

	"kutta/foil"
)

const sample = `
(scene
  (object "wing"
    (naca "2412" 130 90 72))
  (object "flap"
    (shape (point 200 69) (point 240 69) (point 240 75) (point 200 75))
    (pivot 200 72)
    (keys
      (key 0 (pose 0 0 0 1))
      (key 2 (pose 0 0 -25 1))
      (key 4 (pose 0 0 0 1))))
  (loop 4))
`

func TestLoadSample(t *testing.T) {
	s, err := Load(sample)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(s.Objects) != 2 {
		t.Fatalf("got %d objects, want 2", len(s.Objects))
	}
	if s.Loop != 4 {
		t.Errorf("loop = %g, want 4", s.Loop)
	}

	wing := s.Objects[0]
	if wing.Name != "wing" || len(wing.Shape) < 10 {
		t.Errorf("wing not decoded: name=%q points=%d", wing.Name, len(wing.Shape))
	}

	flap := s.Objects[1]
	if flap.Name != "flap" || len(flap.Shape) != 4 {
		t.Fatalf("flap not decoded: name=%q points=%d", flap.Name, len(flap.Shape))
	}
	if flap.Pivot.X != 200 || flap.Pivot.Y != 72 {
		t.Errorf("flap pivot = %+v, want (200,72)", flap.Pivot)
	}
	if len(flap.Keys) != 3 {
		t.Fatalf("flap keys = %d, want 3", len(flap.Keys))
	}
	// The negative rotation literal must parse and reach the pose.
	got := flap.Keys[1].Pose.Rot
	if math.Abs(got+25) > 1e-9 {
		t.Errorf("middle key rot = %g, want -25 (check negative literal parsing)", got)
	}

	// The flap should actually deflect: its lowest point at mid-loop sits below
	// where it sits at rest.
	if !(lowestY(flap.PolygonAt(2)) < lowestY(flap.PolygonAt(0))) {
		t.Errorf("flap did not deflect down: rest=%g deflected=%g",
			lowestY(flap.PolygonAt(0)), lowestY(flap.PolygonAt(2)))
	}
}

func lowestY(pts []foil.Point) float64 {
	min := math.Inf(1)
	for _, p := range pts {
		if p.Y < min {
			min = p.Y
		}
	}
	return min
}

func TestLoadErrors(t *testing.T) {
	cases := []string{
		`(scene)`,                               // no objects
		`(object "x" (naca "2412" 10 0 0))`,     // no scene wrapper
		`(scene (object "x" (pivot 0 0)))`,      // object without a shape
		`(scene (object "x" (naca "9" 1 0 0)))`, // naca code too short
	}
	for _, c := range cases {
		_, err := Load(c)
		if err == nil {
			t.Errorf("expected error for %q", c)
		}
	}
}
