package main

import "testing"

func TestStallStatus(t *testing.T) {
	cases := []struct {
		sep  float64
		want string
	}{
		{0.0, "adherido"},
		{sepOnset - 0.01, "adherido"},
		{sepOnset, "separandose"},
		{sepStall - 0.01, "separandose"},
		{sepStall, "PERDIDA"},
		{0.5, "PERDIDA"},
	}
	for _, c := range cases {
		got, _ := stallStatus(c.sep)
		// The label is a prefix plus a percentage; compare the leading word group.
		if len(got) < len(c.want) || got[:len(c.want)] != c.want {
			t.Errorf("stallStatus(%.3f) = %q, want prefix %q", c.sep, got, c.want)
		}
	}
}
