package scene

import (
	"math"
	"testing"

	"kutta/foil"
)

func sqKnots() []foil.Point {
	return []foil.Point{{X: 0, Y: 0}, {X: 10, Y: 0}, {X: 10, Y: 10}, {X: 0, Y: 10}}
}

func TestOutlinePolygonVsCurved(t *testing.T) {
	o := &Object{Shape: sqKnots()}
	got := len(o.Outline())
	if got != 4 {
		t.Errorf("polygon outline len = %d, want 4", got)
	}
	if o.HasHandles() {
		t.Error("object with no handles reports HasHandles")
	}
	o.AutoSmooth()
	if !o.HasHandles() {
		t.Fatal("AutoSmooth did not create handles")
	}
	// A fully curved 4-anchor loop flattens to 4 cubic edges of bezSeg each.
	got = len(o.Outline())
	if got != 4*bezSeg {
		t.Errorf("curved outline len = %d, want %d", got, 4*bezSeg)
	}
}

func TestFlattenPassesThroughAnchors(t *testing.T) {
	o := &Object{Shape: sqKnots()}
	o.AutoSmooth()
	out := o.Outline()
	// The start of each cubic edge (t=0) lands exactly on its anchor.
	for i, k := range o.Shape {
		p := out[i*bezSeg]
		if math.Abs(p.X-k.X) > 1e-9 || math.Abs(p.Y-k.Y) > 1e-9 {
			t.Errorf("edge %d start = (%g,%g), want anchor (%g,%g)", i, p.X, p.Y, k.X, k.Y)
		}
	}
}

func TestMixedCornerAndCurve(t *testing.T) {
	// Two corner anchors and two smooth ones: only the curved edges expand.
	o := &Object{
		Shape:  sqKnots(),
		Handle: []foil.Point{{}, {}, {X: 2, Y: 0}, {X: 0, Y: 2}},
	}
	// Edges: 0->1 corner (1 pt), 1->2 has h1 -> cubic, 2->3 cubic, 3->0 has h0 -> cubic.
	// corner edge contributes 1, each cubic contributes bezSeg.
	want := 1 + 3*bezSeg
	got := len(o.Outline())
	if got != want {
		t.Errorf("mixed outline len = %d, want %d", got, want)
	}
}

func TestOpenObjectNotRasterized(t *testing.T) {
	o := &Object{Shape: []foil.Point{{X: 2, Y: 2}, {X: 8, Y: 2}, {X: 8, Y: 8}, {X: 2, Y: 8}}}
	s := &Scene{Objects: []*Object{o}}
	if !anySolid(s.Mask(0, 10, 10)) {
		t.Fatal("closed object should rasterize a solid")
	}
	o.Gaps = []bool{false, true, false, false} // cut one edge
	if !o.Broken() {
		t.Fatal("a cut edge should make it broken")
	}
	if anySolid(s.Mask(0, 10, 10)) {
		t.Error("broken object should not rasterize")
	}
}

func anySolid(m []bool) bool {
	for _, b := range m {
		if b {
			return true
		}
	}
	return false
}

func TestOpenOutlineOpen(t *testing.T) {
	pts := []foil.Point{{X: 0, Y: 0}, {X: 10, Y: 0}, {X: 10, Y: 10}}
	// No handles: an open polyline is just the anchors (no closing edge).
	out := OpenOutline(pts, nil)
	if len(out) != 3 {
		t.Errorf("open polyline len = %d, want 3", len(out))
	}
}
