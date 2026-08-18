package main

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"time"

	"kutta/sceneio"
)

type sessionState struct {
	Version     int     `json:"version"`
	SceneData   string  `json:"scene_data,omitempty"`
	ScenePath   string  `json:"scene_path,omitempty"`
	NACA        string  `json:"naca,omitempty"`
	AlphaDeg    float64 `json:"alpha_deg"`
	Speed       float64 `json:"speed"`
	ControlDeg  float64 `json:"control_deg"`
	Mode        int     `json:"mode"`
	Glow        bool    `json:"glow"`
	Particles   bool    `json:"particles"`
	Streamlines bool    `json:"streamlines"`
	Label       bool    `json:"label"`
	Paused      bool    `json:"paused"`
	AnimTime    float64 `json:"anim_time"`
	AnimPlaying bool    `json:"anim_playing"`
	Degraded    bool    `json:"degraded,omitempty"` // accepted only for backward compatibility
}

func (g *Game) currentSession() (sessionState, error) {
	state := sessionState{
		Version: 1, NACA: g.nacaCode, AlphaDeg: g.alphaDeg, Speed: g.u0,
		ControlDeg: g.controlDeg, Mode: int(g.mode), Glow: g.glow,
		Particles: g.showParticles, Streamlines: g.streamlines, Label: g.showLabel,
		Paused: g.paused, AnimTime: g.animTime, AnimPlaying: g.animPlaying,
	}
	if g.scn != nil {
		text, err := sceneio.Save(g.scn)
		if err != nil {
			return state, err
		}
		state.SceneData = text
		state.ScenePath = g.scenePath
	}
	return state, nil
}

func (g *Game) saveSession(force bool) {
	if g.sessionPath == "" {
		return
	}
	now := time.Now()
	if !force && !g.sessionLastSaved.IsZero() && now.Sub(g.sessionLastSaved) < time.Second {
		return
	}
	state, err := g.currentSession()
	if err != nil {
		g.perf.eventf("session_save_error error=%q", err)
		return
	}
	payload, err := json.Marshal(state)
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(g.sessionPath), 0o700); err != nil {
		return
	}
	if err := os.WriteFile(g.sessionPath, payload, 0o600); err != nil {
		g.perf.eventf("session_save_error error=%q", err)
		return
	}
	g.sessionLastSaved = now
}

func (g *Game) restoreSession() error {
	if g.sessionPath == "" {
		return nil
	}
	payload, err := os.ReadFile(g.sessionPath) // #nosec G304 -- explicit host path
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	var state sessionState
	if err := json.Unmarshal(payload, &state); err != nil {
		return err
	}
	if state.Version != 1 || !finiteSessionState(state) {
		return fmt.Errorf("estado de sesión no válido")
	}
	if state.SceneData != "" {
		sc, err := sceneio.Load(state.SceneData)
		if err != nil {
			return err
		}
		g.setScene(sc, state.ScenePath)
	} else if state.NACA != "" {
		g.selectFoil(state.NACA)
	}
	g.setSpeed(state.Speed)
	g.setAlpha(state.AlphaDeg)
	g.setControl(state.ControlDeg)
	if state.Mode >= int(modeSpeed) && state.Mode < int(modeCount) {
		g.mode = fieldMode(state.Mode)
	}
	g.glow = state.Glow
	g.showParticles = state.Particles
	g.streamlines = state.Streamlines
	g.showLabel = state.Label
	g.paused = state.Paused
	g.animTime = state.AnimTime
	g.animPlaying = state.AnimPlaying
	// Degraded mode is a temporary runtime safeguard, not a user preference.
	// Old checkpoints may contain degraded=true, but a normal/manual restart
	// must return to the full-density, full-speed renderer. Automatic recovery
	// reapplies the mode explicitly through the -degraded command-line flag.
	g.degraded = false
	return nil
}

func finiteSessionState(s sessionState) bool {
	for _, value := range []float64{s.AlphaDeg, s.Speed, s.ControlDeg, s.AnimTime} {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return false
		}
	}
	return s.Speed >= spdMin && s.Speed <= spdMax
}
