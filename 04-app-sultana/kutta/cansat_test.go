package main

import (
	"strings"
	"testing"
)

func TestCansatSceneContainsRocketAndFourNACACanards(t *testing.T) {
	sc := cansatScene()
	if len(sc.Objects) != 7 {
		t.Fatalf("objects = %d, want rocket, two stabilizers and four canards", len(sc.Objects))
	}
	canards := 0
	for _, object := range sc.Objects {
		if strings.Contains(object.Name, "canard") {
			canards++
			if len(object.Shape) != 3 {
				t.Errorf("%s has %d points; expected the OpenRocket triangular planform", object.Name, len(object.Shape))
			}
		}
	}
	if canards != 4 {
		t.Fatalf("canards = %d, want 4", canards)
	}
	covered := 0
	for _, solid := range sc.Mask(0, gridW, gridH) {
		if solid {
			covered++
		}
	}
	if covered < 1000 || covered > gridW*gridH/4 {
		t.Errorf("CANSAT solid coverage = %d, outside compact tunnel range", covered)
	}
}

func TestNewGameKeepsInteractiveNACAProfilesAsDefault(t *testing.T) {
	g := NewGame()
	if g.scn != nil || g.nacaCode == "" {
		t.Fatalf("default scene must remain the interactive NACA profile, got scene %q", g.scenePath)
	}
}
