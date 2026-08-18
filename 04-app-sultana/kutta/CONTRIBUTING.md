# Contributing to kutta

Thanks for your interest. kutta is a 2D wind tunnel for intuition and fun: it
gets the shape of the flow right and the absolute numbers wrong, on purpose. Keep
that sentence in mind and most contribution questions answer themselves.

## Qualitative, and honest about it

kutta is not validated CFD and does not pretend to be. That is a feature; it is
what lets the whole thing run in real time inside one executable.

- Changes to the physics need to show their effect: describe what behavior
  changed (stagnation point, wake, separation angle) and ideally attach a short
  capture. "Looks more plausible" is a legitimate argument here; "matches
  XFOIL to 2%" is not a goal.
- Never add language to the code or docs implying the numbers are physical.
  The README's disclaimer is load-bearing.

## It stays one executable

Single binary, nothing to install alongside it, prebuilt releases for people
without Go. Anything that breaks that (external data files, a runtime
dependency, an installer) is out.

Builds are cgo-free: Ebitengine reaches the OS through purego, so
`CGO_ENABLED=0` cross-compiles from one machine. Keep it that way; a
contribution must not introduce cgo or anything that needs a C toolchain.

## Dependencies are the family stack

Ebitengine plus my own libraries: `minigui` (immediate-mode UI), `filo`
(embedded scripting), `glaze` (WebView). That list does not grow casually; if
the standard library or a few lines cover it, that wins. Improvements to a
widget or a binding belong upstream in `minigui`/`native`/`glaze`, not as local
copies here.

## Performance is a feature

It is a real-time simulation; frame rate is part of the user experience.

- Don't optimize speculatively; YAGNI applies to cleverness too. Any change on
  the hot path (the solver, the field rendering) should state its frame-time
  impact. Measure it; don't guess.
- If a change trades visual quality for speed or vice versa, say so and make it
  a conscious decision, not a side effect.

## Code style

`gofmt`, US English everywhere. No inline `if` init; assign, then `if`. No
`else` after a terminal branch; return early. Comments explain why, not what.

```sh
go fix ./...
gofmt -l .        # must print nothing
go vet ./...
golangci-lint run ./...
go test -timeout 30s -count 1 ./...
go build          # and run it; this project is judged on the screen
```

## Proposing a change

The default branch is `trunk`. Small fixes go straight to a PR. For new tools,
new visualizations, or solver changes, open an issue first with what you want to
see on screen; kutta is small and opinionated, and agreeing on the idea first
avoids wasted work.
