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

/**
 * Starts `runner.py <args>` detached (it outlives this server), its output appended to
 * `log`. onEnd(ok) runs once when it exits (ok: code 0), unless this server stops first.
 */
function spawnRunner(
  args: string[],
  log: string,
  onEnd?: (ok: boolean, error?: Error) => void,
) {
  const logFd = fs.openSync(log, "a");
  let ended = false;
  const end = (ok: boolean, error?: Error) => {
    if (ended) return;
    ended = true;
    onEnd?.(ok, error);
  };
  try {
    const child = spawn(
      runnerInterpreter(),
      [path.join(BACKEND_DIR, "runner.py"), ...args],
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
    child.on("error", (error) => end(false, error));
    child.on("exit", (code) => end(code === 0));
    child.unref();
  } finally {
    fs.closeSync(logFd);
  }
}

const missingPython = `Python not found at ${RUNNER_PYTHON}. Create the backend venv or set RUNNER_PYTHON in .env`;

/** Starts runner.py for a job in the background and returns immediately. */
export async function startRunner(id: string, extraArgs: string[] = []) {
  const dir = jobDir(id);
  if (!fs.existsSync(RUNNER_PYTHON)) {
    await updateJob(id, {
      status: "failed",
      message: "Failed",
      error: missingPython,
    });
    return;
  }
  spawnRunner(
    ["--job-dir", dir, ...extraArgs],
    path.join(dir, "runner.log"),
    (_ok, error) => {
      if (!error) return;
      void updateJob(id, {
        status: "failed",
        message: "Failed",
        error: `Could not start the runner: ${error.message}`,
      });
    },
  );
}

/**
 * Cancels a running job (runner.py --cancel: stops its runner, cancels its Modal calls,
 * cleans the volume and writes job.json). Resolves when that is done, with whether it
 * worked; the job's runner.log says why not.
 */
export function cancelRunner(id: string): Promise<boolean> {
  if (!fs.existsSync(RUNNER_PYTHON)) return Promise.resolve(false);
  const dir = jobDir(id);
  return new Promise((resolve) =>
    spawnRunner(
      ["--job-dir", dir, "--cancel"],
      path.join(dir, "runner.log"),
      (ok) => resolve(ok),
    ),
  );
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
  spawnRunner(
    ["--data-dir", DATA_DIR, ...args],
    path.join(STATS_DIR, logName),
    (ok) => onEnd?.(ok),
  );
  return true;
}
