package main

import (
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSessionRoundTripPreservesSultanaAndControls(t *testing.T) {
	path := filepath.Join(t.TempDir(), "kutta-session.json")
	original := NewGame()
	original.sessionPath = path
	original.setScene(sultanaScene(), "Sultana 2D · cohete y canards NACA 66(1)-212")
	original.setAlpha(7.5)
	original.setSpeed(0.12)
	original.setControl(-8)
	original.mode = modeVorticity
	original.glow = false
	original.streamlines = true
	original.paused = true
	original.saveSession(true)

	restored := NewGame()
	restored.sessionPath = path
	if err := restored.restoreSession(); err != nil {
		t.Fatal(err)
	}
	if restored.scn == nil || len(restored.scn.Objects) != len(sultanaScene().Objects) {
		t.Fatal("Sultana scene was not restored")
	}
	if math.Abs(restored.alphaDeg-7.5) > 1e-9 || math.Abs(restored.u0-0.12) > 1e-9 {
		t.Fatalf("controls not restored: alpha=%g speed=%g", restored.alphaDeg, restored.u0)
	}
	if restored.mode != modeVorticity || !restored.streamlines || !restored.paused || restored.glow {
		t.Fatal("visual state was not restored")
	}
}

func TestInvalidSessionDoesNotReplaceCurrentState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bad-session.json")
	g := NewGame()
	g.sessionPath = path
	if err := os.WriteFile(path, []byte(`{"version":1,"speed":"nan"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := g.restoreSession(); err == nil {
		t.Fatal("invalid session should be rejected")
	}
	if g.scn != nil || g.nacaCode == "" {
		t.Fatal("invalid session replaced the default profile")
	}
}

func TestNACAProfileButtonLeavesSultanaAndCyclesProfiles(t *testing.T) {
	g := NewGame()
	g.setScene(sultanaScene(), "Sultana 2D")
	g.advanceNACAProfile()
	if g.scn != nil || g.nacaCode != profiles[0] {
		t.Fatalf("first NACA button action = scene %v, profile %q", g.scn != nil, g.nacaCode)
	}
	g.advanceNACAProfile()
	if g.nacaCode != profiles[1] {
		t.Fatalf("second NACA button action = %q, want %q", g.nacaCode, profiles[1])
	}
}

func TestLegacyDegradedFlagIsNotRestoredAsAUserPreference(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy-session.json")
	payload := []byte(`{"version":1,"naca":"2412","speed":0.1,"degraded":true}`)
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		t.Fatal(err)
	}
	g := NewGame()
	g.sessionPath = path
	if err := g.restoreSession(); err != nil {
		t.Fatal(err)
	}
	if g.degraded {
		t.Fatal("temporary degraded mode leaked into a normal restart")
	}
}

func TestMemoryMaintenanceKeepsSceneAndRebuildsGraphics(t *testing.T) {
	g := NewGame()
	g.setScene(sultanaScene(), "Sultana 2D")
	oldField, oldTrail := g.fieldImg, g.trailImg
	started := time.Now()
	g.performMemoryMaintenance()
	if elapsed := time.Since(started); elapsed >= 2*time.Second {
		t.Fatalf("maintenance took %s, want less than two seconds", elapsed)
	}
	if g.scn == nil || len(g.scn.Objects) != 7 {
		t.Fatal("maintenance discarded the active Sultana scene")
	}
	if !g.degraded || g.glow || len(g.smoke.X) != degradedParticles {
		t.Fatal("maintenance did not enter bounded visual mode")
	}
	if g.fieldImg == nil || g.trailImg == nil || g.fieldImg == oldField || g.trailImg == oldTrail {
		t.Fatal("graphics resources were not rebuilt")
	}
}
