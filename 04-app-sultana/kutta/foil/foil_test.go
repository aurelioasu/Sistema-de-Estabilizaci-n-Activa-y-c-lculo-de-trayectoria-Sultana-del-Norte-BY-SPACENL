package foil

import (
	"math"
	"testing"
)

func TestNACA4Validation(t *testing.T) {
	cases := []string{"123", "12345", "12a4", ""}
	for _, code := range cases {
		_, err := NACA4(code, 40)
		if err == nil {
			t.Errorf("NACA4(%q) accepted an invalid code", code)
		}
	}
	_, err := NACA4("0012", 1)
	if err == nil {
		t.Error("NACA4 accepted n<2")
	}
}

// symmetricSectionIsSymmetric: a NACA 0012 has no camber, so the outline must be
// mirror-symmetric about the chord line (y=0).
func TestSymmetricSectionIsSymmetric(t *testing.T) {
	out, err := NACA4("0012", 60)
	if err != nil {
		t.Fatal(err)
	}
	maxY, minY := 0.0, 0.0
	for _, pt := range out {
		maxY = math.Max(maxY, pt.Y)
		minY = math.Min(minY, pt.Y)
	}
	if math.Abs(maxY+minY) > 1e-9 {
		t.Errorf("0012 not symmetric: maxY=%g minY=%g", maxY, minY)
	}
}

// thicknessMatchesCode: the last two digits are percent thickness, so the peak
// total thickness of a NACA 0015 should be close to 0.15 chord.
func TestThicknessMatchesCode(t *testing.T) {
	out, err := NACA4("0015", 200)
	if err != nil {
		t.Fatal(err)
	}
	maxY := 0.0
	for _, pt := range out {
		maxY = math.Max(maxY, pt.Y)
	}
	total := 2 * maxY
	if math.Abs(total-0.15) > 0.005 {
		t.Errorf("0015 max thickness = %g, want ~0.15", total)
	}
}

func TestNACA5(t *testing.T) {
	// 23012: 12% thick, cambered upward (design Cl 0.3), max camber near 15% chord.
	out, err := NACA5("23012", 120)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 2*121-1 {
		t.Errorf("point count = %d, want %d", len(out), 2*121-1)
	}
	maxY, minY := 0.0, 0.0
	for _, pt := range out {
		maxY = math.Max(maxY, pt.Y)
		minY = math.Min(minY, pt.Y)
	}
	if 2*math.Max(maxY, -minY) > 0.16 || 2*math.Max(maxY, -minY) < 0.10 {
		t.Errorf("23012 thickness envelope off: maxY=%g minY=%g", maxY, minY)
	}
	if maxY <= -minY { // cambered upward
		t.Errorf("23012 not cambered upward: maxY=%g minY=%g", maxY, minY)
	}
	// Dispatcher and validation.
	_, err = NACA("23012", 40)
	if err != nil {
		t.Errorf("NACA dispatch on 5-digit: %v", err)
	}
	_, err = NACA5("23112", 40)
	if err == nil {
		t.Error("reflex (Q=1) should be rejected")
	}
	_, err = NACA("240", 40)
	if err == nil {
		t.Error("3-digit code should be rejected")
	}
}

func TestLibraryProfiles(t *testing.T) {
	for _, code := range []string{"661-212", "66(1)-212", "747A315"} {
		out, ok := Library(code)
		if !ok {
			t.Errorf("Library(%q) was not found", code)
			continue
		}
		if len(out) < 40 {
			t.Errorf("Library(%q) returned only %d points", code, len(out))
		}
		if math.Abs(out[0].X-1) > 1e-6 || math.Abs(out[len(out)-1].X-1) > 1e-6 {
			t.Errorf("Library(%q) does not close at the trailing edge", code)
		}
	}
	if _, ok := Library("does-not-exist"); ok {
		t.Error("unknown library profile was accepted")
	}
}

// camberLifTStheMeanLine: a cambered NACA 2412 must bulge above the chord more
// than it dips below it, unlike a symmetric section.
func TestCamberRaisesMeanLine(t *testing.T) {
	out, err := NACA4("2412", 100)
	if err != nil {
		t.Fatal(err)
	}
	maxY, minY := 0.0, 0.0
	for _, pt := range out {
		maxY = math.Max(maxY, pt.Y)
		minY = math.Min(minY, pt.Y)
	}
	if maxY <= -minY {
		t.Errorf("2412 not cambered upward: maxY=%g minY=%g", maxY, minY)
	}
}

func TestPlaceLeadingEdgeAndPitch(t *testing.T) {
	// A single chord segment from LE(0,0) to TE(1,0).
	seg := []Point{{X: 0, Y: 0}, {X: 1, Y: 0}}
	placed := Place(seg, 100, 50, 30, 0, 0) // pivot at the leading edge
	if math.Abs(placed[0].X-50) > 1e-9 || math.Abs(placed[0].Y-30) > 1e-9 {
		t.Errorf("LE not pinned to origin: got %+v", placed[0])
	}
	if math.Abs(placed[1].X-150) > 1e-9 || math.Abs(placed[1].Y-30) > 1e-9 {
		t.Errorf("TE at zero AoA wrong: got %+v", placed[1])
	}
	// Positive AoA is nose up: with y up, the trailing edge must drop below the
	// leading edge.
	pitched := Place(seg, 100, 50, 30, 0.2, 0)
	if pitched[1].Y >= pitched[0].Y {
		t.Errorf("positive AoA did not lower the trailing edge: %+v", pitched)
	}
}

func TestRasterizeSquare(t *testing.T) {
	square := []Point{{X: 2, Y: 2}, {X: 6, Y: 2}, {X: 6, Y: 6}, {X: 2, Y: 6}}
	mask := Rasterize(square, 10, 10)
	count := 0
	for _, b := range mask {
		if b {
			count++
		}
	}
	// Cell centers at 2.5..5.5 fall inside [2,6] on each axis: a 4x4 block.
	if count != 16 {
		t.Errorf("rasterized square covered %d cells, want 16", count)
	}
	if !mask[3*10+3] {
		t.Error("interior cell (3,3) should be solid")
	}
	if mask[0] {
		t.Error("corner cell (0,0) should be empty")
	}
}

func TestRasterizeProducesSolidFoil(t *testing.T) {
	out, err := NACA4("2412", 80)
	if err != nil {
		t.Fatal(err)
	}
	const nx, ny = 200, 100
	placed := Place(out, 120, 40, 50, 0.1, 0)
	mask := Rasterize(placed, nx, ny)
	count := 0
	for _, b := range mask {
		if b {
			count++
		}
	}
	if count == 0 {
		t.Fatal("airfoil rasterized to an empty mask")
	}
	// A thin section should fill far less than its bounding box; a wildly large
	// count would mean the polygon winding leaked.
	if count > nx*ny/4 {
		t.Errorf("airfoil mask suspiciously large: %d cells", count)
	}
}
