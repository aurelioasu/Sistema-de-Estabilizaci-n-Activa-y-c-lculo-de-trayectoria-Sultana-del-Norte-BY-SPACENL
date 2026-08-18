package foil

import (
	"math"
	"testing"
)

const seligDAT = `TEST SELIG
1.0 0.0
0.5 0.06
0.0 0.0
0.5 -0.06
`

const lednicerDAT = `TEST LEDNICER
3. 3.
0.0 0.0
0.5 0.06
1.0 0.0
0.0 0.0
0.5 -0.06
1.0 0.0
`

func TestParseSelig(t *testing.T) {
	pts, err := ParseDAT([]byte(seligDAT))
	if err != nil {
		t.Fatal(err)
	}
	if len(pts) != 4 {
		t.Fatalf("got %d points, want 4", len(pts))
	}
	if pts[0] != (Point{X: 1, Y: 0}) || pts[2] != (Point{X: 0, Y: 0}) {
		t.Errorf("unexpected loop order: %+v", pts)
	}
}

func TestParseLednicer(t *testing.T) {
	pts, err := ParseDAT([]byte(lednicerDAT))
	if err != nil {
		t.Fatal(err)
	}
	// reverse(upper) [TE->LE, 3 pts] + lower[1:] [2 pts] = 5, sharing the LE.
	if len(pts) != 5 {
		t.Fatalf("got %d points, want 5: %+v", len(pts), pts)
	}
	if pts[0] != (Point{X: 1, Y: 0}) {
		t.Errorf("loop should start at the trailing edge, got %+v", pts[0])
	}
	var maxY, minY float64
	for _, p := range pts {
		maxY = math.Max(maxY, p.Y)
		minY = math.Min(minY, p.Y)
	}
	if maxY <= 0 || minY >= 0 {
		t.Errorf("stitched loop should span both surfaces: maxY=%g minY=%g", maxY, minY)
	}
}

func TestParseDATErrors(t *testing.T) {
	cases := []string{
		"NAME\n1.0 0.0\n",           // too short
		"NAME\nfoo bar\nx y\nz w\n", // non-numeric
		"",                          // empty
	}
	for _, c := range cases {
		_, err := ParseDAT([]byte(c))
		if err == nil {
			t.Errorf("expected error for %q", c)
		}
	}
}

// TestParsedShapeRasterizes confirms a parsed outline is a usable polygon.
func TestParsedShapeRasterizes(t *testing.T) {
	pts, err := ParseDAT([]byte(seligDAT))
	if err != nil {
		t.Fatal(err)
	}
	placed := Place(pts, 80, 10, 50, 0, 0)
	mask := Rasterize(placed, 120, 100)
	count := 0
	for _, b := range mask {
		if b {
			count++
		}
	}
	if count == 0 {
		t.Error("parsed shape rasterized to nothing")
	}
}
