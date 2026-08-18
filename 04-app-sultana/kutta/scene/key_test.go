package scene

import "testing"

func TestSetKeyInsertsSorted(t *testing.T) {
	o := &Object{}
	o.SetKey(2, Pose{DX: 2, Scale: 1})
	o.SetKey(0, Pose{DX: 0, Scale: 1})
	o.SetKey(1, Pose{DX: 1, Scale: 1})
	if len(o.Keys) != 3 {
		t.Fatalf("len = %d, want 3", len(o.Keys))
	}
	for i := 1; i < len(o.Keys); i++ {
		if o.Keys[i].T < o.Keys[i-1].T {
			t.Errorf("keys not sorted: %v", o.Keys)
		}
	}
}

func TestSetKeyReplaces(t *testing.T) {
	o := &Object{}
	o.SetKey(1, Pose{Rot: 10, Scale: 1})
	o.SetKey(1, Pose{Rot: -30, Scale: 1})
	if len(o.Keys) != 1 {
		t.Fatalf("len = %d, want 1 (replace, not insert)", len(o.Keys))
	}
	if o.Keys[0].Pose.Rot != -30 {
		t.Errorf("Rot = %g, want -30", o.Keys[0].Pose.Rot)
	}
}

func TestDeleteKey(t *testing.T) {
	o := &Object{}
	o.SetKey(0, Identity())
	o.SetKey(1, Identity())
	if !o.DeleteKey(1) {
		t.Error("DeleteKey(1) = false, want true")
	}
	if o.DeleteKey(5) {
		t.Error("DeleteKey(5) = true, want false (no such key)")
	}
	if len(o.Keys) != 1 || o.Keys[0].T != 0 {
		t.Errorf("after delete: %v, want one key at t=0", o.Keys)
	}
}
