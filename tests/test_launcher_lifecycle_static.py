"""Static checks for launcher browser/process lifecycle coupling."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "Launch-Sentinel-Simulation-Engine.ps1"


class LauncherLifecycleStaticTests(unittest.TestCase):
    def test_browser_window_close_stops_simulation_engine(self):
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
        self.assertIn("Browser window closed; shutting down Simulation Engine", script)

    def test_launcher_close_stops_dedicated_browser_and_server(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("[string]$BrowserProfileDir", script)
        self.assertIn("Get-ProfileProcesses", script)
        self.assertIn("CloseMainWindow", script)
        self.assertIn("Launcher process $ParentProcessId ended; closing browser and stopping server", script)
        self.assertIn("Start-LauncherWatchdog -ServerProcessId $ServerProcess.Id -BrowserProfileDir $BrowserProfileDir", script)


if __name__ == "__main__":
    unittest.main()
