import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { env } from "~/env";
import { updateJob } from "./job-store";
import { BACKEND_DIR, DATA_DIR, jobDir, STATS_DIR } from "./paths";

const RUNNER_PYTHON =
  env.RUNNER_PYTHON ??
  (process.platform === "win32"
    ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND_DIR, ".venv", "bin", "python"));

/**
 * On Windows a detached console program always gets its own console window
 * (Node ignores windowsHide for detached children), which pops up over the app.
 * pythonw.exe from the same environment runs the same Python without a console;
 * runner.py hides the windows of anything it starts in turn.
 */
function runnerInterpreter() {
  if (process.platform !== "win32") return RUNNER_PYTHON;
  const pythonw = RUNNER_PYTHON.replace(/python\.exe$/i, "pythonw.exe");
  return pythonw !== RUNNER_PYTHON && fs.existsSync(pythonw)
    ? pythonw
    : RUNNER_PYTHON;
}

/** Starts runner.py for a job in the background and returns immediately. */
export async function startRunner(id: string, extraArgs: string[] = []) {
  const dir = jobDir(id);
  const runnerScript = path.join(BACKEND_DIR, "runner.py");

  if (!fs.existsSync(RUNNER_PYTHON)) {
    await updateJob(id, {
      status: "failed",
      message: "Failed",
      error: `Python not found at ${RUNNER_PYTHON}. Create the backend venv or set RUNNER_PYTHON in .env`,
    });
    return;
  }

  const logFd = fs.openSync(path.join(dir, "runner.log"), "a");
  const child = spawn(
    runnerInterpreter(),
    [runnerScript, "--job-dir", dir, ...extraArgs],
    {
      cwd: BACKEND_DIR,
      detached: true,
      windowsHide: true,
      stdio: ["ignore", logFd, logFd],
      env: {
        ...process.env,
        DATA_DIR,
        PYTHONUTF8: "1",
        PYTHONUNBUFFERED: "1",
      },
    },
  );

  child.on("error", (error) => {
    void updateJob(id, {
      status: "failed",
      message: "Failed",
      error: `Could not start the runner: ${error.message}`,
    });
  });

  child.unref();
  fs.closeSync(logFd);
}

/**
 * Runs `runner.py <args>` in the background for a task that has no job (for example
 * reading the Modal bill), logging to <DATA_DIR>/stats/<logName>. Returns false when
 * the backend Python environment is missing.
 */
export function startRunnerTask(
  args: string[],
  logName: string,
  /** Called once when the task ends (ok: it exited with code 0). Not called if this server stops first. */
  onEnd?: (ok: boolean) => void,
) {
  if (!fs.existsSync(RUNNER_PYTHON)) return false;
  fs.mkdirSync(STATS_DIR, { recursive: true });
  const logFd = fs.openSync(path.join(STATS_DIR, logName), "a");
  const child = spawn(
    runnerInterpreter(),
    [path.join(BACKEND_DIR, "runner.py"), "--data-dir", DATA_DIR, ...args],
    {
      cwd: BACKEND_DIR,
      detached: true,
      windowsHide: true,
      stdio: ["ignore", logFd, logFd],
      env: { ...process.env, DATA_DIR, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
    },
  );
  let ended = false;
  const end = (ok: boolean) => {
    if (ended) return;
    ended = true;
    onEnd?.(ok);
  };
  child.on("error", () => end(false));
  child.on("exit", (code) => end(code === 0));
  child.unref();
  fs.closeSync(logFd);
  return true;
}
