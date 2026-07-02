"""Static checks for launcher browser/process lifecycle coupling."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "Launch-Sentinel-Archive.ps1"


class LauncherLifecycleStaticTests(unittest.TestCase):
    def test_browser_window_close_stops_sentinel_archive(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("$BrowserProcessIds = @()", script)
        self.assertIn("$BrowserWindowProcessIds = @()", script)
        self.assertIn("$BrowserStartedAt = $null", script)
        self.assertIn("function Get-BrowserProfileProcesses", script)
        self.assertIn("function Wait-BrowserWindowProcesses", script)
        self.assertIn("function Test-BrowserWindowClosed", script)
        self.assertIn("Wait-BrowserProfileProcesses -Seconds 10", script)
        self.assertIn("Wait-BrowserWindowProcesses -Seconds 10", script)
        self.assertIn("if (Test-BrowserWindowClosed)", script)
        self.assertIn("Browser window closed; shutting down Sentinel Archive", script)

    def test_launcher_close_stops_dedicated_browser_and_server(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("[string]$BrowserProfileDir", script)
        self.assertIn("Get-ProfileProcesses", script)
        self.assertIn("CloseMainWindow", script)
        self.assertIn("Launcher process $ParentProcessId ended; closing browser and stopping server", script)
        self.assertIn("Start-LauncherWatchdog -ServerProcessId $ServerProcess.Id -BrowserProfileDir $BrowserProfileDir", script)

    def test_launcher_does_not_silently_fall_back_to_regular_browser_tab(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("[switch]$AllowDefaultBrowserFallback", script)
        self.assertIn("throw \"A dedicated Edge or Chrome app window could not be opened.", script)
        self.assertIn("if ($AllowDefaultBrowserFallback)", script)
        self.assertIn("--app=$Url", script)
        self.assertIn("--user-data-dir=$script:BrowserProfileDir", script)


if __name__ == "__main__":
    unittest.main()
