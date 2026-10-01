package tunnel

import (
	"fmt"
	"os"
	"path/filepath"
)

// tsnet.Dir does not configure ipnlocal's separate socket-statistics logger.
// Android has no writable default home/cache/tmp path; logpolicy otherwise panics.
// The Android application owns one Client, so this process-wide setting is stable.
func configureAndroidLogs(stateDir string) error {
	dir := filepath.Join(stateDir, "logs")
	if !filepath.IsAbs(dir) {
		return fmt.Errorf("Android network state directory must be absolute")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return fmt.Errorf("create Android network logs: %w", err)
	}
	return os.Setenv("TS_LOGS_DIR", dir)
}
