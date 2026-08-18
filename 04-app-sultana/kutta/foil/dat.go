package foil

import (
	"bufio"
	"bytes"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// ParseDAT reads an airfoil coordinate file (the de-facto airfoiltools formats)
// into a single closed outline in chord-normalized coordinates (x in [0,1], y
// up), traversed continuously so it can be rasterized directly.
//
// Both common layouts are accepted:
//   - Selig: a name line, then one continuous loop of "x y" pairs from the
//     trailing edge over the top to the leading edge and back along the bottom.
//   - Lednicer: a name line, a "n_upper n_lower" count line, then the upper
//     surface (leading to trailing edge) and the lower surface (leading to
//     trailing edge). They are stitched into one loop here.
func ParseDAT(data []byte) ([]Point, error) {
	data = bytes.TrimPrefix(data, []byte{0xEF, 0xBB, 0xBF}) // strip UTF-8 BOM
	sc := bufio.NewScanner(bytes.NewReader(data))

	var lines []string
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line != "" {
			lines = append(lines, line)
		}
	}
	err := sc.Err()
	if err != nil {
		return nil, fmt.Errorf("foil: reading .dat: %w", err)
	}
	if len(lines) < 4 {
		return nil, fmt.Errorf("foil: .dat too short")
	}

	// Line 0 is the airfoil name. The next line decides the format: a pair of
	// counts (both well above 1) means Lednicer; an actual coordinate means Selig.
	a, b, ok := parsePair(lines[1])
	if ok && a > 1.5 && b > 1.5 {
		return parseLednicer(lines[1:])
	}
	return parseSelig(lines[1:])
}

func parseSelig(lines []string) ([]Point, error) {
	pts := make([]Point, 0, len(lines))
	for _, line := range lines {
		x, y, ok := parsePair(line)
		if !ok {
			return nil, fmt.Errorf("foil: bad coordinate %q", line)
		}
		pts = append(pts, Point{X: x, Y: y})
	}
	if len(pts) < 3 {
		return nil, fmt.Errorf("foil: not enough points")
	}
	return pts, nil
}

func parseLednicer(lines []string) ([]Point, error) {
	nu, nl, ok := parsePair(lines[0])
	if !ok {
		return nil, fmt.Errorf("foil: bad Lednicer count line %q", lines[0])
	}
	nUpper, nLower := int(math.Round(nu)), int(math.Round(nl))
	coords := lines[1:]
	if nUpper < 2 || nLower < 2 || len(coords) < nUpper+nLower {
		return nil, fmt.Errorf("foil: Lednicer counts (%d,%d) exceed the data", nUpper, nLower)
	}

	upper := make([]Point, nUpper)
	for i := range nUpper {
		x, y, okc := parsePair(coords[i])
		if !okc {
			return nil, fmt.Errorf("foil: bad upper coordinate %q", coords[i])
		}
		upper[i] = Point{X: x, Y: y}
	}
	lower := make([]Point, nLower)
	for i := range nLower {
		x, y, okc := parsePair(coords[nUpper+i])
		if !okc {
			return nil, fmt.Errorf("foil: bad lower coordinate %q", coords[nUpper+i])
		}
		lower[i] = Point{X: x, Y: y}
	}

	// Stitch into one loop: upper trailing->leading, then lower leading->trailing.
	// Both surfaces share the leading-edge point, so drop the duplicate.
	out := make([]Point, 0, nUpper+nLower-1)
	for i := nUpper - 1; i >= 0; i-- {
		out = append(out, upper[i])
	}
	out = append(out, lower[1:]...)
	return out, nil
}

// parsePair reads the first two whitespace-separated floats from a line.
func parsePair(line string) (float64, float64, bool) {
	f := strings.Fields(line)
	if len(f) < 2 {
		return 0, 0, false
	}
	a, err1 := strconv.ParseFloat(strings.TrimSuffix(f[0], "."), 64)
	b, err2 := strconv.ParseFloat(strings.TrimSuffix(f[1], "."), 64)
	if err1 != nil || err2 != nil {
		return 0, 0, false
	}
	return a, b, true
}
