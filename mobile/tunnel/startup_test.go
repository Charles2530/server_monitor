package tunnel

import (
	"os"
	"path/filepath"
	"testing"

	"tailscale.com/logpolicy"
)

func TestAndroidLogsUsePrivateStateDirectory(t *testing.T) {
	t.Setenv("TS_LOGS_DIR", "")
	dir := t.TempDir()
	if err := configureAndroidLogs(dir); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(dir, "logs")
	if got := logpolicy.LogsDir(func(string, ...any) {}); got != want {
		t.Fatalf("Tailscale selected %q, want private directory %q", got, want)
	}
	info, err := os.Stat(want)
	if err != nil || info.Mode().Perm() != 0700 {
		t.Fatalf("private log directory permissions: %v %v", info, err)
	}
}

func TestAndroidLogsRejectUnusablePathBeforeChangingEnvironment(t *testing.T) {
	t.Setenv("TS_LOGS_DIR", "previous")
	file := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(file, []byte("occupied"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{file, "relative"} {
		if err := configureAndroidLogs(dir); err == nil {
			t.Fatalf("accepted unusable state directory %q", dir)
		}
		if os.Getenv("TS_LOGS_DIR") != "previous" {
			t.Fatal("failed setup changed the process environment")
		}
	}
}
